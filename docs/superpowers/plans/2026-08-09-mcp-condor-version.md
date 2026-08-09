# MCP HTCondor Client Version Drift — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the `campaign_status` queue block — `unknown` for every campaign because the venv pins `htcondor==23.0.*` against a `25.0.12` pool — and make the next version drift announce itself instead of requiring a diagnosis session.

**Architecture:** Port `condor.py` to the v2 bindings (`htcondor2`) that the 25.x wheel actually ships; derive the pin at install time from the node's own `/usr/bin/condor_version` instead of a literal that can go stale; gate `start_mcp.sh --check` on client/node version agreement via a unit-testable Python helper; and thread a `reason` string out of the query so a failed queue block names its true cause instead of a fixed string blaming the schedds.

**Tech Stack:** Python 3.9 (unit suite) and 3.10 (MCP venv), `unittest`, HTCondor Python bindings v2, bash.

**Spec:** `docs/superpowers/specs/2026-08-09-mcp-condor-version-design.md`

## Global Constraints

- The unit suite is `python3 -u test/test_unit.py`, run from the repo root. Currently **964 tests, OK (skipped=1)**. It must stay green at every commit.
- **The suite MUST keep running on plain python3.9 with no htcondor wheel importable.** This is what the lazy, in-function `import` statements in `condor.py` exist for. Never hoist an htcondor import to module level.
- **`clusters is None` remains the ONLY signal any caller may branch on** to decide the queue result is untrustworthy. The new `reason` string is diagnostic text for humans and must never become control flow.
- An `unknown` queue block **omits** `running`/`idle`/`held` entirely. There must be no zero to misread as "drained".
- The read-only server performs **no writes**. Nothing in this plan adds any.
- Every subprocess spawned from server code passes `stdin=subprocess.DEVNULL`. The MCP server's stdin is the JSON-RPC channel; a child that inherits it can consume protocol bytes.
- `/usr/bin/condor_version` is always referenced by **absolute path**, never via `PATH` — `muse setup ops` rewrites `PATH`.
- No fallbacks for missing required data: if a version cannot be read, say so and fail; never substitute a plausible default.
- Do **not** `git push`. The user pushes from their own shell.
- Commit message footers:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
  ```

## Environment note for every task

Unit tests run with the plain system python3.9 from the repo root and need no setup:

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools
python3 -u test/test_unit.py 2>&1 | tail -5
```

Anything that must import the real `htcondor2` wheel (Tasks 2 and 6 only) needs the MCP venv environment:

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools
MCP_ROOT=$PWD/mcp; source mcp/scripts/_mcp_env.sh 2>/dev/null
"$PYTHON_BIN" -c "import htcondor2; print(htcondor2.version())"
```

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `mcp/src/prodtools_mcp/condor.py` | v2 bindings; version reporting; `(clusters, reason)` query contract | 1, 3, 4 |
| `mcp/src/prodtools_mcp/tools/status.py` | Thread `reason` from the query seam into the queue block | 4 |
| `mcp/src/prodtools_mcp/server.py` | `get_server_info` condor block; INSTRUCTIONS text | 5 |
| `mcp/pyproject.toml` | Declares the htcondor floor; no literal pin | 2 |
| `mcp/scripts/install.sh` | Derives and installs the wheel series from the node | 2 |
| `mcp/scripts/start_mcp.sh` | `--check` gate on version agreement | 2 |
| `test/test_unit.py` | All unit tests | 1, 3, 4, 5 |
| `wiki/pages/prodtools-mcp-server.md` | Operational description of the queue path | 5 |

---

### Task 1: Version reporting helper in condor.py

Pure, injectable version comparison. No wiring yet — later tasks consume it.

**Files:**
- Modify: `mcp/src/prodtools_mcp/condor.py` (add to the module; do not touch existing functions)
- Test: `test/test_unit.py` (add to `class TestMcpCondor`, which starts at the line matching `class TestMcpCondor`)

**Interfaces:**
- Produces:
  - `condor.CONDOR_VERSION_BIN` — `str`, `'/usr/bin/condor_version'`
  - `condor.parse_version(text) -> str | None` — `'25.0.12'` from a `$CondorVersion: ... $` banner
  - `condor.series(version) -> str | None` — `'25.0'` from `'25.0.12'`
  - `condor.version_report(client_fn=..., node_fn=...) -> dict` with keys `client`, `node`, `series_match`, `reason`

- [ ] **Step 1: Write the failing tests**

Add these methods inside `class TestMcpCondor` in `test/test_unit.py`:

```python
    def test_parse_version_reads_the_condor_banner(self):
        from prodtools_mcp import condor
        banner = ('$CondorVersion: 25.0.12 2026-07-07 BuildID: 930047 '
                  'PackageID: 25.0.12-1 $')
        self.assertEqual(condor.parse_version(banner), '25.0.12')
        self.assertEqual(condor.series(condor.parse_version(banner)), '25.0')

    def test_parse_version_returns_none_rather_than_guessing(self):
        """No fallbacks: an unparseable banner is unknown, not a
        plausible default. A wrong-but-plausible version is exactly how
        the stale pin went unnoticed."""
        from prodtools_mcp import condor
        self.assertIsNone(condor.parse_version('not a banner'))
        self.assertIsNone(condor.parse_version(''))
        self.assertIsNone(condor.parse_version(None))
        self.assertIsNone(condor.series(None))

    def test_version_report_matching_series_has_no_reason(self):
        from prodtools_mcp import condor
        report = condor.version_report(
            client_fn=lambda: '$CondorVersion: 25.0.9 2026-01-01 $',
            node_fn=lambda: '$CondorVersion: 25.0.12 2026-07-07 $')
        self.assertEqual(report['client'], '25.0.9')
        self.assertEqual(report['node'], '25.0.12')
        self.assertIs(report['series_match'], True)
        self.assertIsNone(report['reason'])

    def test_version_report_names_both_versions_on_mismatch(self):
        """The reason must carry BOTH numbers: this exact mismatch
        (client 23.0.28, node 25.0.12) surfaced as an authentication
        failure at the collector and cost a full diagnosis session."""
        from prodtools_mcp import condor
        report = condor.version_report(
            client_fn=lambda: '$CondorVersion: 23.0.28 2025-08-21 $',
            node_fn=lambda: '$CondorVersion: 25.0.12 2026-07-07 $')
        self.assertIs(report['series_match'], False)
        self.assertIn('23.0.28', report['reason'])
        self.assertIn('25.0.12', report['reason'])

    def test_version_report_unknown_side_is_none_not_a_match(self):
        """series_match must be None, never True, when a side is
        unreadable — claiming agreement we cannot verify is the failure
        this whole change exists to prevent."""
        from prodtools_mcp import condor

        def boom():
            raise RuntimeError('condor_version not found')

        report = condor.version_report(
            client_fn=lambda: '$CondorVersion: 25.0.12 2026-07-07 $',
            node_fn=boom)
        self.assertEqual(report['client'], '25.0.12')
        self.assertIsNone(report['node'])
        self.assertIsNone(report['series_match'])
        self.assertIn('condor_version not found', report['reason'])

    def test_version_report_client_import_failure_is_reported(self):
        from prodtools_mcp import condor

        def boom():
            raise ModuleNotFoundError("No module named 'htcondor2'")

        report = condor.version_report(
            client_fn=boom,
            node_fn=lambda: '$CondorVersion: 25.0.12 2026-07-07 $')
        self.assertIsNone(report['client'])
        self.assertIsNone(report['series_match'])
        self.assertIn('htcondor2', report['reason'])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -u test/test_unit.py 2>&1 | tail -20`
Expected: FAIL — `AttributeError: module 'prodtools_mcp.condor' has no attribute 'parse_version'`

- [ ] **Step 3: Implement**

In `mcp/src/prodtools_mcp/condor.py`, add `import re` and `import subprocess` beside the existing `import concurrent.futures`, then append this block after the `hold_reasons` function:

```python
CONDOR_VERSION_BIN = '/usr/bin/condor_version'

_VERSION_RE = re.compile(r'\$CondorVersion:\s*(\d+\.\d+\.\d+)')


def parse_version(text):
    """'25.0.12' out of a `$CondorVersion: ... $` banner, or None.

    None rather than a guess: an unreadable version must stay unknown.
    A plausible default is what let a stale pin sit undetected."""
    if not text:
        return None
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None


def series(version):
    """'25.0' out of '25.0.12' — the major.minor the wheel pin uses.
    None in, None out."""
    if not version:
        return None
    return '.'.join(version.split('.')[:2])


def _client_version_banner():
    """The bindings' own version banner. Imported lazily like every
    other htcondor use in this module, so the unit suite still runs
    where no wheel is installed."""
    import htcondor2
    return htcondor2.version()


def _node_version_banner():
    """condor_version(1) from its ABSOLUTE path.

    Absolute because `muse setup ops` rewrites PATH, and the version
    that matters is the node's own client RPM — the one jobsub_lite
    uses and the best local proxy for what the pool will accept.

    stdin=DEVNULL is mandatory, not tidiness: this can run inside an
    MCP server whose stdin IS the JSON-RPC channel, and a child that
    inherits it can consume protocol bytes."""
    completed = subprocess.run(
        [CONDOR_VERSION_BIN], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=10, check=True)
    return completed.stdout.decode('utf-8', 'replace')


def _read_version(fn, label):
    """(version or None, error text or None) for one side."""
    try:
        banner = fn()
    except Exception as exc:
        return None, f'{label}: {type(exc).__name__}: {exc}'
    version = parse_version(banner)
    if version is None:
        return None, f'{label}: unparseable version banner {banner!r}'
    return version, None


def version_report(client_fn=_client_version_banner,
                   node_fn=_node_version_banner):
    """{'client', 'node', 'series_match', 'reason'} comparing the
    installed bindings against this node's condor client.

    `series_match` is True/False only when BOTH versions are known;
    when either side is unreadable it is None — never True. Claiming an
    agreement that was not verified is the exact failure this reporting
    exists to prevent.

    `reason` is None when both are known and the series agree; otherwise
    human-readable text naming what is wrong. It is diagnostic output,
    never a control-flow signal."""
    client, client_err = _read_version(client_fn, 'client bindings')
    node, node_err = _read_version(node_fn, CONDOR_VERSION_BIN)

    if client is None or node is None:
        errs = [e for e in (client_err, node_err) if e]
        return {'client': client, 'node': node, 'series_match': None,
                'reason': 'cannot compare HTCondor versions — '
                          + '; '.join(errs)}

    if series(client) == series(node):
        return {'client': client, 'node': node, 'series_match': True,
                'reason': None}

    return {
        'client': client, 'node': node, 'series_match': False,
        'reason': (f'HTCondor client bindings {client} do not match this '
                   f'node\'s condor {node}; the pool rejects the older '
                   f'client\'s authentication. Rerun mcp/scripts/install.sh '
                   f'to reinstall the matching wheel series.'),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: PASS, 970 tests, OK (skipped=1)

- [ ] **Step 5: Commit**

```bash
git add mcp/src/prodtools_mcp/condor.py test/test_unit.py
git commit -m "feat(mcp): report HTCondor client vs node version agreement

A stale wheel pin surfaced as an authentication failure at the
collector, which reads as a schedd problem. version_report() names
both versions so the next drift diagnoses itself; series_match is
None, never True, when either side is unreadable.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 2: Derived pin and the health-check gate

Make the venv track the node instead of a literal, and fail the health check when it does not.

**Files:**
- Modify: `mcp/pyproject.toml` (the `dependencies` list and its comment)
- Modify: `mcp/scripts/install.sh` (before the existing `pip install -e .` line)
- Modify: `mcp/scripts/start_mcp.sh` (inside the `--check` branch)

**Interfaces:**
- Consumes: `condor.version_report()`, `condor.parse_version()`, `condor.series()` from Task 1.
- Produces: a venv whose `htcondor` wheel series matches `/usr/bin/condor_version`; `start_mcp.sh --check` exits non-zero on mismatch.

- [ ] **Step 1: Replace the literal pin with a floor**

In `mcp/pyproject.toml`, replace the `htcondor` dependency and its comment block with:

```toml
  # Floor only. The EXACT version is chosen at install time by
  # mcp/scripts/install.sh, which reads /usr/bin/condor_version and
  # installs the matching major.minor series. A literal pin lived here
  # once and went stale against a pool upgrade (23.0.* vs a 25.0.12
  # pool); the client was then rejected by the collector and every
  # queue block read "unknown". install.sh is the authority; this floor
  # exists so the dependency is still declared. The system RPM htcondor
  # is py3.9-only, which is why this lives in the venv at all.
  "htcondor>=23"
```

- [ ] **Step 2: Derive and install the matching series**

In `mcp/scripts/install.sh`, insert this immediately **before** the existing `env -u PYTHONPATH ./.venv/bin/pip install -e . 1>&2` line:

```bash
# Pin the bindings to THIS NODE's condor client series. Absolute path:
# `muse setup ops` rewrites PATH. No fallback on failure — a
# wrong-but-plausible default is how the previous literal pin went
# stale unnoticed.
CONDOR_VERSION_BIN=/usr/bin/condor_version
if [[ ! -x "$CONDOR_VERSION_BIN" ]]; then
  echo "FATAL: $CONDOR_VERSION_BIN not found; cannot determine which" 1>&2
  echo "       htcondor wheel series to install." 1>&2
  exit 1
fi
CONDOR_FULL="$("$CONDOR_VERSION_BIN" | sed -n 's/.*\$CondorVersion: \([0-9.]*\).*/\1/p' | head -1)"
if [[ -z "$CONDOR_FULL" ]]; then
  echo "FATAL: could not parse a version from $CONDOR_VERSION_BIN" 1>&2
  exit 1
fi
CONDOR_SERIES="$(echo "$CONDOR_FULL" | cut -d. -f1,2)"
echo "node condor $CONDOR_FULL -> installing htcondor==${CONDOR_SERIES}.*" 1>&2
# BEFORE `pip install -e .`: with a satisfying version already present,
# the editable install cannot resolve the >=23 floor to something newer.
env -u PYTHONPATH ./.venv/bin/pip install "htcondor==${CONDOR_SERIES}.*" 1>&2
```

- [ ] **Step 3: Gate the health check**

In `mcp/scripts/start_mcp.sh`, inside the `--check` branch, insert this **between** the "part 2" python heredoc and the `exit 0` line:

```bash
  echo "== part 3: HTCondor client matches this node ==" 1>&2
  "$PYTHON_BIN" - <<'PY'
from prodtools_mcp import condor
report = condor.version_report()
if report['series_match'] is not True:
    raise SystemExit(
        f"FATAL: {report['reason']}\n"
        f"  client={report['client']} node={report['node']}")
print(f"OK: htcondor client {report['client']} matches node "
      f"condor {report['node']}")
PY
```

- [ ] **Step 4: Rebuild the venv and verify**

Run:
```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools
bash mcp/scripts/install.sh 2>&1 | tail -25
```
Expected: `node condor 25.0.12 -> installing htcondor==25.0.*`, then all three parts of both server checks pass, part 3 printing `OK: htcondor client 25.0.12 matches node condor 25.0.12`.

If part 3 reports `client=None` with a `ModuleNotFoundError` for `htcondor2`, the wheel did not install — a real failure, fix it before continuing. Note the queue path itself is still broken at this point: `condor.py` still imports the v1 module. Task 3 fixes that; do not chase it here.

- [ ] **Step 5: Confirm the venv now holds the v2 bindings**

Run:
```bash
MCP_ROOT=$PWD/mcp; source mcp/scripts/_mcp_env.sh 2>/dev/null
"$PYTHON_BIN" -c "import htcondor2; print(htcondor2.version())"
```
Expected: `$CondorVersion: 25.0.12 ...`

- [ ] **Step 6: Run the unit suite**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: PASS, 970 tests, OK (skipped=1) — unchanged; no test code moved in this task.

- [ ] **Step 7: Commit**

```bash
git add mcp/pyproject.toml mcp/scripts/install.sh mcp/scripts/start_mcp.sh
git commit -m "fix(mcp)!: derive the htcondor pin from the node, gate --check on it

The literal htcondor==23.0.* pin went stale against a 25.0.12 pool and
every queue block read 'unknown'. install.sh now reads
/usr/bin/condor_version and installs the matching series before the
editable install; pyproject keeps only a floor. start_mcp.sh --check
gains a third part that fails loudly when the two disagree.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 3: Port condor.py to the v2 bindings

The 25.x wheel ships `htcondor2`; there is no v1 `htcondor` module in it.

**Files:**
- Modify: `mcp/src/prodtools_mcp/condor.py` — module docstring, `_locate_jobsub_schedds`, `_query_schedd`, `_is_jobsub_schedd` docstring
- Test: `test/test_unit.py` — `test_query_schedd_filters_server_side_with_projection`, `test_only_jobsub_schedds_are_kept`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `_locate_jobsub_schedds()` and `_query_schedd()` calling the v2 API. Signatures and return shapes are unchanged — `_query_schedd(schedd_ad, owner)` still returns a list of plain dicts with keys `ClusterId`, `JobStatus`, `HoldReasonCode`, `HoldReason`.

- [ ] **Step 1: Update the existing tests to the v2 module name**

In `test/test_unit.py`, in `test_query_schedd_filters_server_side_with_projection`, change the patch target from `htcondor` to `htcondor2`:

```python
        fake_htcondor = types.SimpleNamespace(Schedd=FakeSchedd)
        with patch.dict(sys.modules, {'htcondor2': fake_htcondor}):
            result = condor._query_schedd('sched-a.fnal.gov', 'mu2epro')
```

In `test_only_jobsub_schedds_are_kept`, correct the docstring's schedd count — the pool advertises 8 daemons of which **6** are jobsub schedds (verified live 2026-08-09):

```python
    def test_only_jobsub_schedds_are_kept(self):
        """The pool advertises 8 daemons; only the ~6 whose Name starts
        with 'jobsub' are the schedds that carry mu2epro's jobs."""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -u test/test_unit.py 2>&1 | tail -20`
Expected: FAIL in `test_query_schedd_filters_server_side_with_projection` with `ModuleNotFoundError: No module named 'htcondor'` — the code still imports v1 while the test now injects v2.

- [ ] **Step 3: Port the two call sites**

In `mcp/src/prodtools_mcp/condor.py`, change `_locate_jobsub_schedds`:

```python
def _locate_jobsub_schedds():
    """Schedd location ClassAds for the pool's jobsub_lite schedds
    (see _is_jobsub_schedd)."""
    import htcondor2 as htcondor
    coll = htcondor.Collector()
    ads = coll.locateAll(htcondor.DaemonType.Schedd)
    return [ad for ad in ads if _is_jobsub_schedd(ad)]
```

and `_query_schedd`'s import line only (the body below it is unchanged):

```python
    import htcondor2 as htcondor
```

Also correct the count in `_is_jobsub_schedd`'s docstring — `only ~5 are the jobsub_lite schedds` becomes `only ~6 are the jobsub_lite schedds`, and its verification date becomes `2026-08-09`.

- [ ] **Step 4: Rewrite the module docstring's version paragraph**

Replace the paragraph in `mcp/src/prodtools_mcp/condor.py` beginning `Why this is possible now:` with:

```
Why this is possible now: the system RPM htcondor is py3.9-only, but
PyPI ships a cp310 manylinux wheel. The wheel version is NOT pinned to
a literal here or in pyproject.toml — mcp/scripts/install.sh reads
/usr/bin/condor_version and installs the matching major.minor series,
and start_mcp.sh --check fails when the two disagree. A literal pin
lived here once (23.0.*), went stale against a 25.0.12 pool upgrade,
and the collector rejected the old client's SCITOKENS authentication —
which surfaced as "could not reach every schedd" and cost a full
diagnosis session.

Condor 25 ships the v2 bindings: the module is `htcondor2` and the
enum is DaemonType, not DaemonTypes. htcondor2 is imported lazily,
inside the three functions that talk to the real pool
(_locate_jobsub_schedds, _query_schedd, _client_version_banner) —
never at module level. That keeps this module importable, and its
query logic fully testable via injected fakes, on interpreters that
never see the real htcondor package (e.g. the plain-python3.9 unit-test
run).
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: PASS, 970 tests, OK (skipped=1)

- [ ] **Step 6: Verify against the real pool**

Run:
```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools
MCP_ROOT=$PWD/mcp; source mcp/scripts/_mcp_env.sh 2>/dev/null
"$PYTHON_BIN" -c "
from prodtools_mcp import condor
schedds = condor._locate_jobsub_schedds()
print('jobsub schedds:', len(schedds))
jobs = condor._query_schedd(schedds[0], 'mu2epro')
print(schedds[0].get('Name'), 'ads:', len(jobs))
"
```
Expected: a non-zero schedd count (6 at the time of writing) and a non-zero ad count on a schedd carrying mu2epro work. If every schedd returns 0 ads, check `jobsub_q --group mu2e --user mu2epro` before assuming a code fault — mu2epro may genuinely have drained.

- [ ] **Step 7: Commit**

```bash
git add mcp/src/prodtools_mcp/condor.py test/test_unit.py
git commit -m "fix(mcp): port the queue query to the htcondor v2 bindings

The 25.x wheel ships htcondor2 and DaemonType; there is no v1 htcondor
module in it. Imports stay inside their functions so the unit suite
still runs on plain python3.9 with no wheel installed.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 4: Carry the real failure cause into the queue block

**Files:**
- Modify: `mcp/src/prodtools_mcp/condor.py` — `query_owner_jobs`
- Modify: `mcp/src/prodtools_mcp/tools/status.py` — `queue_block`, `_default_clusters_fn`, and the `campaign_status` call site
- Test: `test/test_unit.py` — `class TestMcpCondor`, `class TestMcpCampaignStatus`, and the identity tests around `_default_clusters_fn`

**Interfaces:**
- Consumes: `condor.version_report()` from Task 1.
- Produces:
  - `condor.query_owner_jobs(owner=OWNER, timeout=QUERY_TIMEOUT_S, schedds_fn=..., query_fn=...) -> (dict | None, str | None)`
  - `status._default_clusters_fn(owner=None) -> (dict | None, str | None)` — this is the injection seam; **any `clusters_fn` test double must now return a 2-tuple**
  - `status.queue_block(cluster_ids, clusters, owner=condor.OWNER, reason=None) -> dict`

- [ ] **Step 1: Write the failing tests**

Add to `class TestMcpCondor` in `test/test_unit.py`:

```python
    def test_query_owner_jobs_returns_a_reason_for_discovery_failure(self):
        """The bare `return None` threw away the only evidence of what
        went wrong. A collector authentication failure must not be
        reported as a schedd problem."""
        from prodtools_mcp import condor

        def boom():
            raise RuntimeError('Failed communication with collector')

        clusters, reason = condor.query_owner_jobs(schedds_fn=boom,
                                                   query_fn=None)
        self.assertIsNone(clusters)
        self.assertIn('Failed communication with collector', reason)

    def test_query_owner_jobs_reason_for_a_failing_schedd(self):
        from prodtools_mcp import condor

        def schedds():
            return ['sched-a', 'sched-b']

        def query(sd, owner):
            if sd == 'sched-a':
                raise RuntimeError('timed out talking to sched-a')
            return [{'ClusterId': 1, 'JobStatus': 2,
                     'HoldReasonCode': None, 'HoldReason': None}]

        clusters, reason = condor.query_owner_jobs(schedds_fn=schedds,
                                                   query_fn=query)
        self.assertIsNone(clusters)
        self.assertIn('timed out talking to sched-a', reason)

    def test_query_owner_jobs_reason_for_a_timeout(self):
        import time
        from prodtools_mcp import condor

        def schedds():
            return ['sched-a']

        def hang(sd, owner):
            time.sleep(5)
            return []

        clusters, reason = condor.query_owner_jobs(
            timeout=0.2, schedds_fn=schedds, query_fn=hang)
        self.assertIsNone(clusters)
        self.assertIn('timed out', reason.lower())

    def test_query_owner_jobs_success_has_no_reason(self):
        from prodtools_mcp import condor

        def schedds():
            return ['sched-a']

        def query(sd, owner):
            return [{'ClusterId': 1, 'JobStatus': 2,
                     'HoldReasonCode': None, 'HoldReason': None}]

        clusters, reason = condor.query_owner_jobs(schedds_fn=schedds,
                                                   query_fn=query)
        self.assertEqual(len(clusters['1']), 1)
        self.assertIsNone(reason)

    def test_no_schedds_found_is_untrusted_with_a_reason(self):
        from prodtools_mcp import condor
        clusters, reason = condor.query_owner_jobs(
            schedds_fn=lambda: [], query_fn=None)
        self.assertIsNone(clusters)
        self.assertIn('no jobsub schedds', reason)
```

Add to `class TestMcpCampaignStatus` in `test/test_unit.py`:

```python
    def test_queue_block_carries_the_supplied_reason(self):
        from prodtools_mcp.tools.status import queue_block
        block = queue_block(['1'], None, 'mu2epro',
                            reason='collector rejected our token')
        self.assertEqual(block['state'], 'unknown')
        self.assertEqual(block['reason'], 'collector rejected our token')

    def test_queue_block_unknown_still_omits_every_count(self):
        """An unknown block must have no zero to misread as drained."""
        from prodtools_mcp.tools.status import queue_block
        block = queue_block(['1'], None, 'mu2epro', reason='anything')
        for key in ('running', 'idle', 'held', 'clusters'):
            self.assertNotIn(key, block)

    def test_queue_block_ignores_a_reason_on_success(self):
        """A reason belongs only to an untrusted result; a known block
        that carried one would invite branching on it."""
        from prodtools_mcp.tools.status import queue_block
        block = queue_block(['1'], {'1': [{'JobStatus': 2}]}, 'mu2epro',
                            reason='stale text')
        self.assertEqual(block['state'], 'known')
        self.assertNotIn('reason', block)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -u test/test_unit.py 2>&1 | tail -20`
Expected: FAIL — `TypeError: cannot unpack non-sequence NoneType` and `TypeError: queue_block() got an unexpected keyword argument 'reason'`

- [ ] **Step 3: Change `query_owner_jobs` to return `(clusters, reason)`**

In `mcp/src/prodtools_mcp/condor.py`, replace the body of `query_owner_jobs` from the `try:` after the docstring through the final `return clusters`:

```python
    try:
        schedds = schedds_fn()
    except Exception as exc:
        return None, (f'schedd discovery failed: '
                      f'{type(exc).__name__}: {exc}')
    if not schedds:
        return None, 'the collector advertised no jobsub schedds'

    clusters = {}
    errors = []
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=len(schedds))
    try:
        futures = {executor.submit(query_fn, sd, owner): sd for sd in schedds}
        try:
            for fut in concurrent.futures.as_completed(futures,
                                                        timeout=timeout):
                try:
                    for ad in fut.result():
                        cid = str(ad.get('ClusterId'))
                        clusters.setdefault(cid, []).append(ad)
                except Exception as exc:
                    errors.append(f'{futures[fut]}: '
                                  f'{type(exc).__name__}: {exc}')
        except concurrent.futures.TimeoutError:
            errors.append(f'the query timed out after {timeout}s')
    finally:
        # wait=False: don't block server shutdown-of-this-call on a
        # straggler thread stuck inside a slow/hung schedd query — the
        # timeout above already decided the result is untrustworthy.
        executor.shutdown(wait=False)

    if errors:
        return None, '; '.join(errors)
    return clusters, None
```

Then update the docstring's opening line and add the reason contract. Replace the first paragraph and the "Trust rules" preamble with:

```
    """((clusters, reason)) for every idle/running/held job `owner` has
    across the pool's jobsub* schedds.

    `clusters` is {cluster_id: [{'JobStatus', 'HoldReasonCode',
    'HoldReason'}, ...]} on success, or None when the result cannot be
    trusted. `reason` is None on success, else human-readable text
    naming what actually failed.

    `clusters is None` is the ONLY signal a caller may branch on. The
    reason exists because the previous bare `return None` threw away
    every clue — a client/pool version mismatch surfaced as an
    authentication failure at the collector, and the fixed text the
    caller printed blamed the schedds instead. It is diagnostic output
    for a human reader and must never become control flow.

    Trust rules, matching the convention queue_block already
    understands (None -> 'unknown', never a zero):
```

- [ ] **Step 4: Thread the reason through status.py**

In `mcp/src/prodtools_mcp/tools/status.py`, change `queue_block`'s signature and its unknown branch:

```python
def queue_block(cluster_ids, clusters, owner=condor.OWNER, reason=None):
```

```python
    if clusters is None:
        return {'state': 'unknown',
                'owner': owner,
                'reason': reason or (
                    'HTCondor queue query failed, timed out, or could '
                    'not reach every schedd')}
```

Add to `queue_block`'s docstring, after the paragraph about the None snapshot:

```
    `reason` is that query's own account of what went wrong, passed
    through unchanged. The fixed fallback text below is only for callers
    that supply none — it once blamed the schedds for a failure that
    happened at the collector, before any schedd was contacted, and sent
    an investigation to the wrong layer.
```

Replace `_default_clusters_fn` with:

```python
def _default_clusters_fn(owner=None):
    """condor.query_owner_jobs(), the MCP server's own path — direct
    ClassAd queries, independent of utils.submissions.live_clusters()
    (which backs the live production cron and stays untouched). Already
    bounded and already fail-closed to None on any timeout or
    unreachable schedd.

    Returns (clusters, reason). On failure the reason is enriched here,
    once per request, with a client/node version mismatch when there is
    one: that mismatch is the cause that looks least like itself — it
    surfaces as an authentication failure at the collector — and this is
    the single place a per-request subprocess for it is affordable.

    `owner` is threaded rather than left to condor.OWNER so the queue is
    always read for the SAME account as the ledger (see
    _resolve_identity)."""
    clusters, reason = condor.query_owner_jobs(owner or condor.OWNER)
    if reason is not None:
        report = condor.version_report()
        if report['series_match'] is False:
            reason = f'{reason} [{report["reason"]}]'
    return clusters, reason
```

In `campaign_status`, replace the `clusters = None` / `if want_queue:` block:

```python
    clusters = None
    queue_reason = None
    if want_queue:
        clusters, queue_reason = (clusters_fn or _default_clusters_fn)(owner)
```

and the `rec['queue']` line:

```python
            rec['queue'] = queue_block(cluster_ids, clusters, owner,
                                       reason=queue_reason)
```

- [ ] **Step 5: Update every `clusters_fn` test double to the 2-tuple contract**

Find them all:

```bash
grep -n "clusters_fn\|query_owner_jobs" test/test_unit.py
```

Each double must now return `(clusters, reason)`. The required edits:

- `test_default_clusters_fn_delegates_to_condor_module` — `return_value={'1': [_job(2)]}` becomes `return_value=({'1': [_job(2)]}, None)`, and the assertion becomes `self.assertEqual(result, ({'1': [_job(2)]}, None))`.
- `test_named_campaign_includes_queue_and_outputs` — `clusters_fn=lambda owner: {'29308498': [running_job, running_job]}` becomes `clusters_fn=lambda owner: ({'29308498': [running_job, running_job]}, None)`.
- The two `clusters_fn=lambda owner: None` sites become `clusters_fn=lambda owner: (None, None)`.
- `test_default_clusters_fn_passes_the_owner_to_condor` and `test_default_clusters_fn_without_an_owner_asks_for_production` — the bare `patch.object(self.condor, 'query_owner_jobs')` returns a `MagicMock`, which `_default_clusters_fn` will try to unpack. Give each an explicit return value: `patch.object(self.condor, 'query_owner_jobs', return_value=(None, None))`.
- Both `fake_clusters(owner)` helpers that `return {}` become `return {}, None`.
- `test_ledger_only_when_no_campaign_named` passes `clusters_fn=boom`, which asserts it is never called — leave it unchanged.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: PASS, 978 tests, OK (skipped=1)

- [ ] **Step 7: Commit**

```bash
git add mcp/src/prodtools_mcp/condor.py mcp/src/prodtools_mcp/tools/status.py test/test_unit.py
git commit -m "fix(mcp): carry the real cause into an unknown queue block

query_owner_jobs returned a bare None, discarding the exception, and
queue_block then printed a fixed string blaming the schedds — for a
failure that happened at the collector before any schedd was
contacted. It now returns (clusters, reason); _default_clusters_fn
appends a client/node version mismatch once per request.

The fail-closed contract is unchanged: clusters is None remains the
only signal callers may branch on, and an unknown block still omits
every count key.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 5: Report versions in get_server_info, and correct the docs

**Files:**
- Modify: `mcp/src/prodtools_mcp/server.py` — `get_server_info`, and the `INSTRUCTIONS` bullet describing the queue block
- Modify: `wiki/pages/prodtools-mcp-server.md` — the queue-block bullet asserting the `23.0.*` pin
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `condor.version_report()` from Task 1.
- Produces: `get_server_info()['condor']` — a dict with keys `client`, `node`, `series_match`, `reason`.

- [ ] **Step 1: Write the failing test**

Add to `class TestMcpCondor` in `test/test_unit.py`:

```python
    def test_get_server_info_reports_the_condor_versions(self):
        """A reader must be able to see the client/node agreement in one
        cheap call, without having to provoke a failure first."""
        from prodtools_mcp import condor, server
        fake = {'client': '25.0.12', 'node': '25.0.12',
                'series_match': True, 'reason': None}
        with patch.object(condor, 'version_report', return_value=fake):
            info = server.get_server_info()
        self.assertEqual(info['condor'], fake)

    def test_get_server_info_survives_an_unreadable_version(self):
        """get_server_info must not raise just because condor_version is
        missing — it is the tool a reader calls when things are broken."""
        from prodtools_mcp import condor, server
        with patch.object(condor, 'version_report',
                          side_effect=RuntimeError('no such file')):
            info = server.get_server_info()
        self.assertIsNone(info['condor']['series_match'])
        self.assertIn('no such file', info['condor']['reason'])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -u test/test_unit.py 2>&1 | tail -20`
Expected: FAIL with `KeyError: 'condor'`

- [ ] **Step 3: Implement**

In `mcp/src/prodtools_mcp/server.py`, add `from prodtools_mcp import condor` to the imports, then add this helper immediately above `get_server_info`:

```python
def _condor_block():
    """Client/node HTCondor versions for get_server_info.

    Never raises: this is the tool a reader reaches for when something
    is already broken, and a version probe that takes the whole call
    down with it would be worse than useless."""
    try:
        return condor.version_report()
    except Exception as exc:
        return {'client': None, 'node': None, 'series_match': None,
                'reason': f'version probe failed: '
                          f'{type(exc).__name__}: {exc}'}
```

and add one key to the dict `get_server_info` returns, immediately after `'tools': TOOL_NAMES,`:

```python
        'condor': _condor_block(),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: PASS, 980 tests, OK (skipped=1)

- [ ] **Step 5: Correct the INSTRUCTIONS bullet**

In `mcp/src/prodtools_mcp/server.py`, in the `INSTRUCTIONS` string, replace `via the htcondor Python bindings — no jobsub_q table parsing` with:

```
via the htcondor2 Python bindings — no jobsub_q table parsing
```

and append this sentence to that same bullet, after the `hold_reasons` explanation:

```
  When the queue block is "unknown" its `reason` names what actually
  failed; `get_server_info` reports the client and node HTCondor
  versions, and a `series_match: false` there is the cause to fix
  first.
```

- [ ] **Step 6: Correct the wiki page**

In `wiki/pages/prodtools-mcp-server.md`, in the bullet beginning `**The queue block comes from live HTCondor ClassAd queries**`, replace the parenthetical

```
(a PyPI cp310
  wheel pinned to `htcondor==23.0.*` in `mcp/pyproject.toml`, matching
  the pool's running version; the system RPM htcondor is py3.9-only,
  which is why this is a venv dep)
```

with

```
(the v2 bindings from a PyPI
  cp310 wheel whose series is derived at install time from
  `/usr/bin/condor_version` — `mcp/pyproject.toml` carries only a
  floor, and `start_mcp.sh --check` fails when client and node
  disagree; the system RPM htcondor is py3.9-only, which is why this
  is a venv dep)
```

Then append this paragraph to the end of that bullet:

```
  A literal `htcondor==23.0.*` pin lived here until 2026-08-09 and went
  stale against a 25.0.12 pool upgrade. The old client's SCITOKENS
  authentication was rejected by the collector, so schedd discovery
  raised before any schedd was contacted and every queue block read
  `unknown` — while `jobsub_q` on the same node worked, because it uses
  the node's own 25.x bindings. Token expiry was NOT the cause: the
  23.0.28 client fails with a freshly minted bearer token, and the
  25.0.12 client succeeds with an expired one.
```

- [ ] **Step 7: Run the unit suite and commit**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`
Expected: PASS, 980 tests, OK (skipped=1)

```bash
git add mcp/src/prodtools_mcp/server.py wiki/pages/prodtools-mcp-server.md test/test_unit.py
git commit -m "feat(mcp): report condor client/node versions in get_server_info

One cheap call now shows whether the bindings match the node, without
having to provoke a failure first. The probe never raises: this is the
tool a reader reaches for when something is already broken.

Corrects the INSTRUCTIONS and wiki claims that the 23.0.* pin matched
the pool — it did when written, and stopped.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF"
```

---

### Task 6: Acceptance

No new code. Prove the thing works end to end, and prove the py3.9 constraint still holds.

**Files:** none modified unless a check fails.

**Interfaces:**
- Consumes: everything from Tasks 1–5.

- [ ] **Step 1: Verify the suite still runs with no htcondor wheel importable**

The plain system python3.9 has no `htcondor` or `htcondor2` module — that is exactly the constraint. Confirm it, then confirm the suite is green under it:

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools
python3 -c "import htcondor2" 2>&1 | tail -1
python3 -u test/test_unit.py 2>&1 | tail -5
```
Expected: `ModuleNotFoundError: No module named 'htcondor2'` from the first command, and `OK (skipped=1)` with 980 tests from the second. **If the first command succeeds, the constraint is not being tested** — report that rather than declaring the step passed.

- [ ] **Step 2: Verify the health check**

```bash
bash mcp/scripts/start_mcp.sh --check 2>&1 | tail -10
```
Expected: all three parts pass, part 3 printing `OK: htcondor client 25.0.12 matches node condor 25.0.12`.

- [ ] **Step 3: Live queue block for the three running campaigns**

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools
MCP_ROOT=$PWD/mcp; source mcp/scripts/_mcp_env.sh 2>/dev/null
"$PYTHON_BIN" -c "
import json
from prodtools_mcp.tools import status
for cid in (54, 55, 56):
    camp = status.campaign_status(campaign_id=cid,
                                  include_outputs=False)['campaigns'][0]
    print(cid, json.dumps(camp['queue']))
"
```
Expected: each `queue` has `state: "known"` with `running`/`idle`/`held` counts and no `reason`. A `state: "unknown"` here is a failure — read its `reason`, which now names the real cause.

Sanity-check the numbers against the pool directly; they should be the same order of magnitude:
```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1
muse setup ops > /dev/null 2>&1
jobsub_q --group mu2e --user mu2epro 2>/dev/null | grep -c "@"
```

- [ ] **Step 4: Verify get_server_info through the same path**

```bash
"$PYTHON_BIN" -c "
import json
from prodtools_mcp.server import get_server_info
print(json.dumps(get_server_info()['condor'], indent=2))
"
```
Expected: `series_match: true`, both versions populated, `reason: null`.

- [ ] **Step 5: Report, and tell the user to reconnect**

The running MCP server holds the code loaded at connect time. Report to the user that a `/mcp` reconnect of the `prodtools` server is required before `campaign_status` returns the fixed queue block **through the transport** — the checks above exercise the code directly, not the server the client is talking to.

Report: the three campaigns' live queue counts, the suite total, and the `--check` result.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §2.1 port to v2 bindings, lazy imports preserved | Task 3 |
| §2.2 derived pin, absolute `condor_version`, `env -u PYTHONPATH`, ordering, fail loudly | Task 2 |
| §2.3 `--check` version gate | Task 2 (implementation), Task 1 (the tested helper it calls) |
| §2.4 `(clusters, reason)`, `queue_block(reason=)`, `get_server_info` condor block, `stdin=DEVNULL` | Tasks 1, 4, 5 |
| §2.5 `~5` → 6 schedds; pyproject/`INSTRUCTIONS`/wiki corrections | Tasks 2, 3, 5 |
| §3 all seven listed test cases | Tasks 1, 4 |
| §4 acceptance items 1–4 | Tasks 2, 6 |
| §4 acceptance item 5 (negative: forced 23.0.* downgrade) | **Deliberately not a task** — see below |

**On the spec's acceptance item 5:** the spec called for a by-hand negative test that downgrades the venv to `23.0.*` and confirms `--check` fails. That is covered better and more cheaply by `test_version_report_names_both_versions_on_mismatch` (Task 1), which asserts the exact 23.0.28-vs-25.0.12 comparison with injected versions, plus `test_get_server_info_survives_an_unreadable_version` (Task 5). Physically downgrading a working venv to prove a pure comparison function risks leaving the environment broken for no additional evidence. The `--check` shell glue that consumes the helper remains unverified for its failure path; that is the accepted residual, and it is three lines of `if report['series_match'] is not True`.

**Placeholder scan:** none — every code step carries the literal text to write, and every run step names the command and its expected output.

**Type consistency:** `query_owner_jobs` returns `(dict | None, str | None)` in Tasks 4 and 6; `_default_clusters_fn` returns the same 2-tuple and is the documented `clusters_fn` seam contract; `queue_block(cluster_ids, clusters, owner=condor.OWNER, reason=None)` is used identically in Task 4's tests and its call site; `version_report()` returns the same four keys in Tasks 1, 4 and 5.

**Test-count arithmetic:** 964 baseline → 970 after Task 1 (+6) → 970 after Tasks 2 and 3 (edits only, no new tests) → 978 after Task 4 (+8) → 980 after Task 5 (+2).
