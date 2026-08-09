# MCP Read Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** let `campaign_status` and `list_campaigns` report on the caller's
own submission ledger and grid queue via a `mine: bool = False` parameter,
instead of only `mu2epro`'s.

**Architecture:** one private helper, `_resolve_identity(mine)`, returns
the `(db_path, owner)` pair for a call, and both the ledger read and the
HTCondor query take their account from that single result. `mine=False`
returns `(None, condor.OWNER)` — a `None` ledger path so the existing
`ledger_ro.DEFAULT_DB` fall-through (and its `MU2E_SUBMISSION_DB`
override) survives untouched.

**Tech Stack:** Python 3.9-compatible stdlib, `unittest`, FastMCP
(`mcp/src/prodtools_mcp/server.py`). Suite: `python3 -u test/test_unit.py`.

## Global Constraints

- The read-only server performs **NO writes**. Never call
  `submission_ledger.ensure_ledger_dir`, never `os.makedirs`, never issue
  DDL. A missing ledger is a `catalog_unavailable` finding.
- `mine=False` must be byte-identical to today's behaviour, including
  honoring the `MU2E_SUBMISSION_DB` environment variable.
- The ledger account and the HTCondor account must come from ONE
  resolution. Never read `os.environ['USER']` on one side only.
- Python 3.9 compatible: no `match`, no `X | Y` type unions, no
  `dict | dict`. The unit suite runs on the system python3.
- Suite is green at 942 tests, `OK (skipped=1)`, before this plan starts.
  It must be green at every commit.
- Do NOT `git push`. The user pushes from their own shell.
- Commit footers on every commit:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
  ```

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `mcp/src/prodtools_mcp/tools/status.py` | campaign/queue composition; now owns "which identity is this call about" | `_resolve_identity`, `mine` on both tools, owner through `queue_block` and `_default_clusters_fn`, local-variable rename |
| `mcp/src/prodtools_mcp/server.py` | FastMCP registration + `get_server_info` | `mine` on two tool wrappers; advertise it |
| `mcp/README.md` | operator documentation | document `mine`, and `--db` for other accounts |
| `CLAUDE.md` | agent-facing MCP guidance | one paragraph on `mine` |
| `test/test_unit.py` | the suite | new `TestMcpReadIdentity`; three existing `clusters_fn` lambdas updated |

`_resolve_identity` lives in `status.py`, not `ledger_ro.py`: it returns a
condor owner as well as a ledger path, and `status.py` is the only module
where both axes meet.

---

### Task 1: Identity resolution and `campaign_status`

**Files:**
- Modify: `mcp/src/prodtools_mcp/tools/status.py:14` (`queue_block`),
  `:176-182` (`_default_clusters_fn`), `:228-299` (`campaign_status`)
- Test: `test/test_unit.py` (new class + three existing lambdas)

**Interfaces:**
- Produces: `status._resolve_identity(mine) -> (db_path_or_None, owner_str)`;
  `status.queue_block(cluster_ids, clusters, owner=condor.OWNER) -> dict`
  whose result always carries an `'owner'` key;
  `status._default_clusters_fn(owner=None) -> dict_or_None`;
  `status.campaign_status(..., mine=False)`.
- Consumes: `utils.submission_ledger.ledger_for()`, `condor.OWNER`,
  `condor.query_owner_jobs(owner)`.

- [ ] **Step 1: Write the failing tests**

Append to `test/test_unit.py`, immediately after the `TestMcpCampaignStatus`
class ends (before the next `class ` line):

```python
class TestMcpReadIdentity(unittest.TestCase):
    """`mine` selects whose ledger AND whose queue — from one resolution.

    The bug this closes: a run_as="self" campaign could be written but
    not watched, and the failure was silent. An empty answer from the
    production ledger is indistinguishable from "no campaigns", and a
    queue counted against the wrong account reads as "nothing running".
    That is the read-side twin of 171517f, where live_clusters()
    defaulted to mu2epro, a self tick did not find its own cluster in
    production's queue, and absent-from-snapshot read as 'drained'.
    """

    def setUp(self):
        from prodtools_mcp import condor
        from prodtools_mcp.tools import status
        self.status = status
        self.condor = condor

    def test_default_returns_no_ledger_path_so_the_env_override_lives(self):
        # NOT the resolved production path. ledger_ro.DEFAULT_DB is
        # os.environ.get('MU2E_SUBMISSION_DB', PRODUCTION_DB); returning
        # a concrete path here would reach the same file in the common
        # case while silently destroying the override.
        db, owner = self.status._resolve_identity(False)
        self.assertIsNone(db)
        self.assertEqual(owner, self.condor.OWNER)

    def test_mine_resolves_the_ledger_to_the_calling_account(self):
        with patch('getpass.getuser', return_value='alice'):
            db, owner = self.status._resolve_identity(True)
        self.assertEqual(db,
                         '/exp/mu2e/data/users/alice/prodtools/submissions.db')
        self.assertEqual(owner, 'alice')

    def test_ledger_and_queue_cannot_name_different_accounts(self):
        # The whole point of one resolution. If a later edit reads
        # os.environ['USER'] on one side and getpass on the other, these
        # two diverge and this test says so.
        with patch('getpass.getuser', return_value='bob'):
            db, owner = self.status._resolve_identity(True)
        self.assertIn('/users/%s/' % owner, db)

    def test_resolution_creates_nothing_on_disk(self):
        # A read-only server has no first run. resolve_db() in the CLI
        # mkdirs a derived path; this must not.
        with patch('getpass.getuser', return_value='nobody_qqq'):
            db, _ = self.status._resolve_identity(True)
        self.assertFalse(os.path.exists(os.path.dirname(db)))
        self.assertFalse(os.path.exists(db))

    def test_queue_block_names_the_account_it_counted(self):
        block = self.status.queue_block(['1'], {}, 'alice')
        self.assertEqual(block['owner'], 'alice')

    def test_queue_block_names_the_account_even_when_unknown(self):
        # A fail-closed 'unknown' from the WRONG account is the most
        # misleading answer this server can give; it must still say whose.
        block = self.status.queue_block(['1'], None, 'alice')
        self.assertEqual(block['state'], 'unknown')
        self.assertEqual(block['owner'], 'alice')

    def test_default_clusters_fn_passes_the_owner_to_condor(self):
        with patch.object(self.condor, 'query_owner_jobs') as q:
            self.status._default_clusters_fn('alice')
        self.assertEqual(q.call_args.args[0], 'alice')

    def test_default_clusters_fn_without_an_owner_asks_for_production(self):
        with patch.object(self.condor, 'query_owner_jobs') as q:
            self.status._default_clusters_fn()
        self.assertEqual(q.call_args.args[0], self.condor.OWNER)

    def test_campaign_status_threads_the_owner_into_the_queue_seam(self):
        # Asserted through the seam, not by reading the constant: a test
        # double that ignores identity would prove nothing about threading.
        seen = {}

        def fake_clusters(owner):
            seen['owner'] = owner
            return {}

        with tempfile.TemporaryDirectory() as td:
            db = TestMcpCampaignStatus()._make_db(td)
            result = self.status.campaign_status(
                campaign='MDC2025au', db_path=db, include_outputs=False,
                clusters_fn=fake_clusters)
        self.assertEqual(seen['owner'], self.condor.OWNER)
        self.assertEqual(result['campaigns'][0]['queue']['owner'],
                         self.condor.OWNER)

    def test_mine_true_reads_the_callers_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            db = TestMcpCampaignStatus()._make_db(td)
            with patch.object(self.status, '_resolve_identity',
                              return_value=(db, 'alice')):
                result = self.status.campaign_status(
                    mine=True, campaign='MDC2025au', include_outputs=False,
                    clusters_fn=lambda owner: {})
        self.assertEqual(result['db_path'], db)
        self.assertEqual(result['campaigns'][0]['queue']['owner'], 'alice')

    def test_an_explicit_db_path_still_wins(self):
        # db_path is the injection seam the existing tests use; `mine`
        # must not take it away from them.
        with tempfile.TemporaryDirectory() as td:
            db = TestMcpCampaignStatus()._make_db(td)
            result = self.status.campaign_status(db_path=db)
        self.assertEqual(result['db_path'], db)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -u test/test_unit.py TestMcpReadIdentity 2>&1 | tail -20`

Expected: FAIL — `AttributeError: module 'prodtools_mcp.tools.status' has
no attribute '_resolve_identity'`, plus `TypeError` for the three-argument
`queue_block` and the one-argument `_default_clusters_fn`.

- [ ] **Step 3: Add the resolver**

In `mcp/src/prodtools_mcp/tools/status.py`, add to the imports at the top
(after the existing `from prodtools_mcp.adapters import ToolError`):

```python
import getpass

from utils import submission_ledger
```

Then add this function immediately above `def queue_block(`:

```python
def _resolve_identity(mine):
    """(ledger path, condor owner) for one call.

    ONE resolution feeds BOTH axes. `ledger_for()` with no argument uses
    getpass.getuser() internally, so asking it for the path and asking
    getpass for the queue owner cannot disagree. Reaching for
    os.environ['USER'] on one side only is exactly how the ledger and the
    queue come to report different accounts — the failure 171517f fixed
    on the write side.

    The two halves are deliberately asymmetric when mine is False. The
    ledger returns None so `ledger_ro.DEFAULT_DB` still applies, and that
    constant is os.environ.get('MU2E_SUBMISSION_DB', PRODUCTION_DB): a
    resolved path here would silently destroy the override. The condor
    owner has no such override, so it is returned concrete and the
    payload can always name it.

    Creates nothing: `ensure_ledger_dir` is the CLI's, not this server's.
    """
    if not mine:
        return None, condor.OWNER
    return submission_ledger.ledger_for(), getpass.getuser()
```

- [ ] **Step 4: Give `queue_block` and `_default_clusters_fn` an owner**

Change the `queue_block` signature (line 14) from
`def queue_block(cluster_ids, clusters):` to:

```python
def queue_block(cluster_ids, clusters, owner=condor.OWNER):
```

Add this paragraph to the end of its docstring, before the closing `"""`:

```
    `owner` is carried into the result so a reader can tell whose queue
    was counted. A count that is correct for an account nobody asked
    about is the recurring bug in this subsystem; naming it in the
    payload is what makes it checkable.
```

Add `'owner': owner` to BOTH returned dicts — the `clusters is None`
early return and the final `block` literal:

```python
    if clusters is None:
        return {'state': 'unknown',
                'owner': owner,
                'reason': 'HTCondor queue query failed, timed out, or '
                          'could not reach every schedd'}
```

```python
    block = {'state': 'known', 'owner': owner, 'running': running,
            'idle': idle, 'held': held, 'clusters': seen}
```

Replace `_default_clusters_fn` (lines 176-182) with:

```python
def _default_clusters_fn(owner=None):
    """condor.query_owner_jobs(), the MCP server's own path — direct
    ClassAd queries, independent of utils.submissions.live_clusters()
    (which backs the live production cron and stays untouched). Already
    bounded and already fail-closed to None on any timeout or
    unreachable schedd; nothing to add here.

    `owner` is threaded rather than left to condor.OWNER so the queue is
    always read for the SAME account as the ledger (see
    _resolve_identity)."""
    return condor.query_owner_jobs(owner or condor.OWNER)
```

- [ ] **Step 5: Thread it through `campaign_status`**

Change the signature (line 228) to add `mine=False` after
`include_outputs=True`:

```python
def campaign_status(campaign=None, campaign_id=None, include_queue=True,
                    include_outputs=True, mine=False, db_path=None,
                    clusters_fn=None, count_fn=None, job_pars_fn=None):
```

Immediately after `from utils.map_entry import njobs_of` inside the body,
insert:

```python
    resolved_db, owner = _resolve_identity(mine)
    # An explicit db_path is the injection seam the tests use and wins
    # over the derived one; `mine` only supplies a default.
    db_path = db_path or resolved_db
```

Change the clusters call from `clusters = (clusters_fn or _default_clusters_fn)()` to:

```python
        clusters = (clusters_fn or _default_clusters_fn)(owner)
```

**Rename the colliding local.** Inside the `for camp in selected:` loop
there is a local named `mine` holding this campaign's rows, which now
shadows the parameter. Rename it to `camp_rows` at all four occurrences:

```python
        # Rows back both blocks: the queue reads their cluster ids, and a
        # draining campaign's output DATASETS are only discoverable from
        # the input filenames they dispatched.
        camp_rows = ([r for r in all_rows if r['tarball'] == camp['tarball']]
                     if (want_queue or want_outputs) else [])
        if want_queue:
            rec['rows'] = _row_counts(camp_rows)
            cluster_ids = [r['cluster_id'] for r in camp_rows
                           if r['cluster_id']]
            rec['queue'] = queue_block(cluster_ids, clusters, owner)
        if want_outputs:
            rec['outputs'] = _outputs_block(
                camp['entry'], njobs, camp['cursor'],
                count_fn or _default_count_fn, rows=camp_rows,
                tarball=camp['tarball'], job_pars_fn=job_pars_fn)
```

- [ ] **Step 6: Update the three existing zero-argument `clusters_fn` fakes**

`clusters_fn` now receives the owner. Three existing injections are
zero-argument lambdas and will raise `TypeError`. Edit each:

- `test/test_unit.py:8154`: `clusters_fn=lambda: {'29308498': [running_job, running_job]},`
  becomes `clusters_fn=lambda owner: {'29308498': [running_job, running_job]},`
- `test/test_unit.py:8291`: `clusters_fn=lambda: None)`
  becomes `clusters_fn=lambda owner: None)`
- `test/test_unit.py:8333`: `clusters_fn=lambda: None, count_fn=boom)`
  becomes `clusters_fn=lambda owner: None, count_fn=boom)`

The `boom(*a, **kw)` fake at `:8129` already accepts arguments — leave it.
`status._default_clusters_fn()` at `:7942` is called with no arguments and
still works, since `owner` defaults to `None` — leave it.

- [ ] **Step 7: Run the full suite**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`

Expected: `OK (skipped=1)` with 953 tests (942 + 11 new).

- [ ] **Step 8: Commit**

```bash
git add mcp/src/prodtools_mcp/tools/status.py test/test_unit.py
git commit -F - <<'EOF'
feat(mcp): campaign_status reads the caller's ledger and queue with mine=True

A run_as="self" campaign could be written but not watched: the read
server was hardwired to mu2epro on two axes (ledger_ro.DEFAULT_DB and
condor.OWNER), and both failures are silent — an empty ledger reads as
"no campaigns", a wrong-account queue reads as "nothing is running".
Read-side twin of 171517f.

_resolve_identity(mine) feeds BOTH axes from one call, so the ledger and
the queue cannot name different accounts. mine=False returns a None
ledger path on purpose: ledger_ro.DEFAULT_DB honors MU2E_SUBMISSION_DB
and a resolved path would silently destroy that override.

queue_block now carries the owner it counted. The local `mine` holding a
campaign's rows is renamed camp_rows — it would otherwise shadow the new
parameter inside the loop.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
```

---

### Task 2: `list_campaigns`

**Files:**
- Modify: `mcp/src/prodtools_mcp/tools/status.py:302-321`
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `status._resolve_identity(mine)` from Task 1.
- Produces: `status.list_campaigns(state=None, mine=False, db_path=None)`
  returning `{'count': int, 'db_path': str, 'campaigns': [...]}`.

- [ ] **Step 1: Write the failing tests**

Append these methods to the `TestMcpReadIdentity` class from Task 1:

```python
    def test_list_campaigns_names_the_ledger_it_read(self):
        # Its silence was harmless only while there was one possible
        # answer. With `mine` there are two.
        with tempfile.TemporaryDirectory() as td:
            db = TestMcpCampaignStatus()._make_db(td)
            result = self.status.list_campaigns(db_path=db)
        self.assertEqual(result['db_path'], db)
        self.assertEqual(result['count'], 1)

    def test_list_campaigns_mine_reads_the_callers_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            db = TestMcpCampaignStatus()._make_db(td)
            with patch.object(self.status, '_resolve_identity',
                              return_value=(db, 'alice')):
                result = self.status.list_campaigns(mine=True)
        self.assertEqual(result['db_path'], db)
        self.assertEqual(result['count'], 1)

    def test_list_campaigns_default_reports_the_production_ledger(self):
        from prodtools_mcp import ledger_ro
        with patch.object(self.status.ledger_ro, 'campaigns',
                          return_value=[]) as camps:
            result = self.status.list_campaigns()
        self.assertIsNone(camps.call_args.args[0])
        self.assertEqual(result['db_path'], ledger_ro.DEFAULT_DB)

    def test_list_campaigns_still_rejects_an_unknown_state(self):
        from prodtools_mcp.adapters import ToolError
        with self.assertRaises(ToolError) as ctx:
            self.status.list_campaigns(state='banana')
        self.assertEqual(ctx.exception.kind, 'invalid_argument')
```

`ToolError` is imported inside the method, not at module scope: that is
how every other test in this file reaches it (`grep -n 'import ToolError'`
shows ten local imports and no module-level one), and the suite must keep
running on interpreters where the `mcp` package is absent.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -u test/test_unit.py TestMcpReadIdentity 2>&1 | tail -20`

Expected: FAIL — `KeyError: 'db_path'` on the listing result, and
`TypeError: list_campaigns() got an unexpected keyword argument 'mine'`.

- [ ] **Step 3: Implement**

Replace `list_campaigns` (lines 302-321) with:

```python
def list_campaigns(state=None, mine=False, db_path=None):
    """Ledger-only campaign listing. No network.

    `db_path` is echoed back for the same reason campaign_status echoes
    it: with `mine` there is more than one possible ledger, and a listing
    that does not say which one it read cannot be checked by its reader.
    """
    if state is not None and state not in CAMPAIGN_STATES:
        raise ToolError(
            'invalid_argument',
            f'unknown state {state!r}',
            f'Expected one of {CAMPAIGN_STATES}.')
    resolved_db, _ = _resolve_identity(mine)
    db_path = db_path or resolved_db
    camps = ledger_ro.campaigns(db_path, state=state)
    from utils.map_entry import njobs_of
    listing = [{
        'id': c['id'],
        'state': c['state'],
        'tarball': c['tarball'],
        'map_path': c['map_path'],
        'cursor': c['cursor'],
        'njobs': njobs_of(c['entry']),
        'slice_size': c['slice_size'],
        'created_utc': c['created_utc'],
    } for c in camps]
    return {'count': len(listing),
            'db_path': db_path or ledger_ro.DEFAULT_DB,
            'campaigns': listing}
```

The state check stays FIRST: an invalid state must be rejected before any
ledger is opened, so a bad argument cannot come back as
`catalog_unavailable`.

- [ ] **Step 4: Run the full suite**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`

Expected: `OK (skipped=1)` with 957 tests.

- [ ] **Step 5: Commit**

```bash
git add mcp/src/prodtools_mcp/tools/status.py test/test_unit.py
git commit -F - <<'EOF'
feat(mcp): list_campaigns takes mine and names the ledger it read

Same resolution as campaign_status. The payload gains db_path: its
silence was harmless only while one ledger was reachable.

The unknown-state check stays ahead of the ledger open, so a bad
argument cannot surface as catalog_unavailable.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
```

---

### Task 3: MCP surface and documentation

**Files:**
- Modify: `mcp/src/prodtools_mcp/server.py:120-134` (tool wrappers),
  `:88-101` (`get_server_info`)
- Modify: `mcp/README.md`
- Modify: `CLAUDE.md:37-52`
- Test: `test/test_unit.py`

**Interfaces:**
- Consumes: `status.campaign_status(..., mine=False)` and
  `status.list_campaigns(state=None, mine=False)` from Tasks 1-2.
- Produces: the registered FastMCP tools accept `mine: bool = False`;
  `get_server_info()['identity']` describes it.

- [ ] **Step 1: Write the failing test**

Append to the `TestMcpReadIdentity` class:

```python
    def test_server_info_advertises_the_identity_parameter(self):
        # A client must be able to discover `mine` without reading the
        # source, and must be told where OTHER accounts are read from --
        # `submissions --db <path> status`, not this server.
        from prodtools_mcp import server
        info = server.get_server_info()
        self.assertIn('mine', info['identity']['parameter'])
        self.assertIn('--db', info['identity']['other_accounts'])

    def test_registered_wrappers_pass_mine_through(self):
        # The wrapper is hand-written argument-by-argument, so a new
        # parameter on the tool function is NOT automatically exposed.
        import inspect
        from prodtools_mcp import server
        src = inspect.getsource(server.create_mcp_server)
        self.assertIn('mine: bool = False', src)
        self.assertIn('mine=mine', src)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -u test/test_unit.py TestMcpReadIdentity 2>&1 | tail -20`

Expected: FAIL — `KeyError: 'identity'` from `get_server_info`, and the
`assertIn('mine: bool = False', src)` assertion failing.

- [ ] **Step 3: Expose `mine` on both tool wrappers**

In `mcp/src/prodtools_mcp/server.py`, replace the two wrappers
(lines 120-134) with:

```python
    @mcp.tool(description='Status of one campaign, or a cheap ledger-only '
                          'summary of all of them when called with no '
                          'argument. Pass mine=true to read YOUR ledger '
                          'and queue instead of production\'s.')
    def campaign_status(campaign: Optional[str] = None,
                        campaign_id: Optional[int] = None,
                        include_queue: bool = True,
                        include_outputs: bool = True,
                        mine: bool = False) -> dict:
        return TOOL_FUNCTIONS['campaign_status'](
            campaign=campaign, campaign_id=campaign_id,
            include_queue=include_queue, include_outputs=include_outputs,
            mine=mine)

    @mcp.tool(description='List submission campaigns, optionally filtered '
                          'by state (active/complete/paused/cancelled). '
                          'Pass mine=true for your own ledger.')
    def list_campaigns(state: Optional[str] = None,
                       mine: bool = False) -> dict:
        return TOOL_FUNCTIONS['list_campaigns'](state=state, mine=mine)
```

- [ ] **Step 4: Advertise it in `get_server_info`**

In the dict returned by `get_server_info` (lines 90-100), add an
`identity` key after `ledger_db`:

```python
        'identity': {
            'parameter': 'mine (campaign_status, list_campaigns)',
            'default': 'production — the ledger in ledger_db and '
                       'mu2epro\'s grid queue',
            'mine_true': "your own ledger at "
                         "/exp/mu2e/data/users/$USER/prodtools/"
                         "submissions.db, and your own grid queue",
            'other_accounts': 'not available through MCP — use '
                              '`submissions --db <path> status`',
        },
```

- [ ] **Step 5: Document it in `mcp/README.md`**

The existing section at `mcp/README.md:55` is titled
`## \`submissions status\` and \`--mine\``. Append these paragraphs to the
end of that section:

```markdown
The MCP status tools take the same idea as a parameter: `campaign_status`
and `list_campaigns` accept `mine` (default `false`). With `mine=true`
they read `/exp/mu2e/data/users/$USER/prodtools/submissions.db` and count
YOUR grid queue; with it omitted they read production's ledger and
mu2epro's queue, exactly as before.

Both axes move together by construction — a call cannot read one
account's ledger against another's queue. Every reply names what it read:
`db_path` at the top level, and `owner` inside each `queue` block.

Another person's ledger is NOT reachable through MCP. Use the CLI, which
already does this:

    bash bin/submissions --db /exp/mu2e/data/users/<them>/prodtools/submissions.db status

Personal ledgers are world-readable, so this works without privilege.
```

- [ ] **Step 6: Document it in `CLAUDE.md`**

In `CLAUDE.md`, insert this paragraph immediately after the
`A queue or outputs block with state: "unknown"` paragraph (which ends
`Do not start a recovery pass on an unknown.`), before the `Setup:` line:

```markdown
`campaign_status` and `list_campaigns` read PRODUCTION by default. For a
campaign you submitted yourself (`run_as="self"`), pass `mine=true` — it
switches both the ledger and the grid queue to your account. Omitting it
against a personal campaign returns an empty result that looks exactly
like "no campaigns". Every reply names the ledger (`db_path`) and the
queue account (`queue.owner`); check them when a count surprises you.
Another user's ledger is not reachable here — use
`submissions --db <path> status`.
```

- [ ] **Step 7: Run the full suite**

Run: `python3 -u test/test_unit.py 2>&1 | tail -5`

Expected: `OK (skipped=1)` with 959 tests.

- [ ] **Step 8: Verify the server still imports and registers**

Run:

```bash
bash mcp/scripts/start_mcp.sh --check 2>&1 | tail -20
```

Expected: the health check passes and reports the five tools plus
`get_server_info`. If the venv is missing, run `bash mcp/scripts/install.sh`
first. A failure here means the FastMCP decorator rejected the new
signature — fix before committing.

- [ ] **Step 9: Commit**

```bash
git add mcp/src/prodtools_mcp/server.py mcp/README.md CLAUDE.md test/test_unit.py
git commit -F - <<'EOF'
feat(mcp): expose mine on the registered tools and document it

The FastMCP wrappers are written argument-by-argument, so a new tool-
function parameter is not automatically reachable from a client; both
wrappers now take mine and pass it through, and a test reads the
registration source so a future parameter cannot be silently stranded.

get_server_info advertises the parameter and, just as importantly, says
where OTHER accounts are read from: `submissions --db <path> status`,
not this server.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
```

---

### Task 4: Live verification against the acceptance fixture

**Files:** none modified. This task produces evidence, not code.

**Interfaces:**
- Consumes: everything from Tasks 1-3.

The fixture already exists: the MCP write-path acceptance run left two
campaigns in `/exp/mu2e/data/users/oksuzian/prodtools/submissions.db`
(`MCPTest001`, `MCPTest002`), and production's ledger is a separate file.
This task proves the two are now distinguishable through the tool.

- [ ] **Step 1: Confirm the fixture is still present**

Run:

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('file:/exp/mu2e/data/users/oksuzian/prodtools/submissions.db?mode=ro', uri=True)
print([r for r in c.execute('SELECT id, state, tarball FROM campaigns ORDER BY id')])
"
```

Expected: two rows, tarballs `cnf.oksuzian.MCPAcceptance.MCPTest001.0.tar`
and `...MCPTest002.0.tar`. If the file is gone, STOP and report — do not
fabricate a fixture; the point of this task is that the real one works.

- [ ] **Step 2: Read the personal ledger through the tool**

Run:

```bash
cd mcp && ./.venv/bin/python -c "
import json
from prodtools_mcp.tools import status
r = status.list_campaigns(mine=True)
print(json.dumps({'db_path': r['db_path'], 'count': r['count'],
                  'tarballs': [c['tarball'] for c in r['campaigns']]}, indent=1))
"
```

Expected: `db_path` ends
`/exp/mu2e/data/users/oksuzian/prodtools/submissions.db`, `count` is 2,
and both `MCPTest001` and `MCPTest002` appear.

- [ ] **Step 3: Confirm the default still reads production**

Run:

```bash
cd mcp && ./.venv/bin/python -c "
import json
from prodtools_mcp.tools import status
r = status.list_campaigns()
print(r['db_path'], r['count'])
"
```

Expected: `db_path` is
`/exp/mu2e/data/users/mu2epro/prodtools/submissions.db` and `count` is the
production campaign count (53 or more) — emphatically NOT 2.

- [ ] **Step 4: Confirm the queue follows the ledger**

Run:

```bash
cd mcp && ./.venv/bin/python -c "
import json
from prodtools_mcp.tools import status
r = status.campaign_status(campaign_id=1, mine=True, include_outputs=False)
c = r['campaigns'][0]
print(json.dumps({'db_path': r['db_path'], 'tarball': c['tarball'],
                  'queue': c['queue']}, indent=1))
"
```

Expected: `queue.owner` is `oksuzian`, NOT `mu2epro`. The counts
themselves may be zero (those clusters drained on 2026-08-09) or the
block may be `state: "unknown"` if the schedds are unreachable — either
is acceptable. **`owner` is the assertion.** A block naming `mu2epro`
here means the two axes have come apart and Task 1 is wrong.

- [ ] **Step 5: Record the result**

Append a short "Verified" note to the spec at
`docs/superpowers/specs/2026-08-09-mcp-read-identity-design.md`, stating
the date, the observed `db_path` values for both modes, and the observed
`queue.owner`. Then:

```bash
git add docs/superpowers/specs/2026-08-09-mcp-read-identity-design.md
git commit -F - <<'EOF'
docs(spec): record the live verification of the MCP read identity

mine=true reads the personal ledger (2 acceptance campaigns) and reports
queue.owner=oksuzian; the default still reads production. Verified
against the fixture the write-path acceptance run left behind, not a
synthetic one.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
```

---

## Notes for the implementer

- **`getpass.getuser()` reads the environment first** (`LOGNAME`, `USER`,
  `LNAME`, `USERNAME`) before falling back to the password database. That
  is fine here — the read server always runs as the invoking user and
  never shells through `ksu`. It is also why patching `getpass.getuser`
  in the tests reaches `ledger_for()` too: `utils/submission_ledger.py`
  calls the same module attribute.
- **Do not import `utils.submissions`** into the read-only server. It
  drags the whole submit and recovery stack in behind a one-line lookup.
  `utils.submission_ledger` is fine — `ledger_ro` already imports it.
- **`db_path` remains a test seam.** Every existing `campaign_status`
  test passes `db_path=` explicitly, and `mine` must not disturb that:
  an explicit path wins, `mine` only supplies a default.
