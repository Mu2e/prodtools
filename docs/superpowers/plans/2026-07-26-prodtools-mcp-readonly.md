# prodtools MCP read-only server — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only MCP stdio server exposing prodtools campaign status and dataset discovery/provenance as six typed tools returning structured JSON.

**Architecture:** A FastMCP stdio server that imports `utils/*` in-process and composes existing functions. `server.py` holds FastMCP wiring only; every tool is a plain Python function taking plain arguments and returning a plain dict, so all of it is testable in the existing `test/test_unit.py` suite with no MCP machinery. A decorator layer (`adapters.py`) converts exceptions to a JSON error envelope, traps `SystemExit`, and redirects stray `print()` off the JSON-RPC channel.

**Tech Stack:** Python ≥3.10, `mcp>=1.2.0` (FastMCP), sqlite3, unittest (run via pytest).

**Spec:** `docs/superpowers/specs/2026-07-26-prodtools-mcp-design.md`

## Global Constraints

- Python `>=3.10`; dependency `mcp>=1.2.0`.
- **No writes of any kind.** No submission, no SAM definition creation, no ledger mutation. Every tool is safe to run as the calling user; none needs mu2epro.
- **Never call** `samweb_wrapper.create_definition` or `samweb_wrapper.delete_definition`.
- **Never invoke** `htgettoken`, `kinit`, or any credential refresh. Auth failures return `auth_expired` and stop.
- **stdout is the JSON-RPC channel.** No `print()` may reach stdout from any tool path.
- **"Unknown" must never render as "zero":** an unknown queue block carries `state: "unknown"` and **omits the count keys entirely**.
- **Fail loudly; never substitute empty.** A catalog error returns `catalog_unavailable`, never an empty result list.
- Error `kind` comes from exactly this closed set: `env_missing`, `auth_expired`, `catalog_unavailable`, `not_found`, `invalid_argument`, `internal`.
- Tests live in `test/test_unit.py` (unittest classes), run with `python -m pytest test/test_unit.py -v`. Suite is at **540 tests** before this plan.
- Commit footers on every commit:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
  ```
- **Do NOT `git push`** — bash subshells cannot reach the user's ssh-agent.

## File Structure

| File | Responsibility |
|---|---|
| `mcp/src/prodtools_mcp/adapters.py` | `ToolError`, `error()`, `@safe_tool` — error envelope, `SystemExit` trap, stdout guard |
| `mcp/src/prodtools_mcp/ledger_ro.py` | Read-only sqlite access to the submission ledger; no DDL |
| `mcp/src/prodtools_mcp/tools/status.py` | `campaign_status`, `list_campaigns` |
| `mcp/src/prodtools_mcp/tools/discovery.py` | `find_datasets`, `dataset_details` |
| `mcp/src/prodtools_mcp/tools/lineage.py` | Depth-bounded graph walk + `trace_provenance` |
| `mcp/src/prodtools_mcp/server.py` | FastMCP wiring, `get_server_info`, `main()` |
| `mcp/pyproject.toml` | Package metadata and dependencies |
| `mcp/scripts/start_mcp.sh` | Source Mu2e env, compose PYTHONPATH, exec server; `--check` mode |
| `mcp/scripts/install.sh` | Build venv, run the two-part check |
| `mcp/scripts/smoke_test_stdio.py` | Spawn server over stdio, list tools, call `get_server_info` |
| `test/test_unit.py` | All unit tests (modified — append new classes) |

---

### Task 1: adapters.py — error envelope, SystemExit trap, stdout guard

**Files:**
- Create: `mcp/src/prodtools_mcp/__init__.py`
- Create: `mcp/src/prodtools_mcp/adapters.py`
- Modify: `test/test_unit.py` (add sys.path entry near line 27; append test class at end)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ERROR_KINDS: tuple[str, ...]`
  - `class ToolError(Exception)` with `.kind: str`, `.message: str`, `.remedy: str`; constructor `ToolError(kind, message, remedy='')`
  - `error(kind: str, message: str, remedy: str = '') -> dict`
  - `safe_tool(fn) -> callable` — decorator preserving `__name__`/`__doc__`

**Why this exists:** `SystemExit` derives from `BaseException`, so `except Exception` does not catch it — uncaught it kills the server mid-session. And stdout is the JSON-RPC channel: `utils/famtree.py:71` prints `"No files found for dataset: …"` to stdout on the not-found path, which sits directly on `trace_provenance`'s route and would corrupt the protocol stream.

- [ ] **Step 1: Add the package directory and sys.path entry**

Create `mcp/src/prodtools_mcp/__init__.py` containing exactly:

```python
"""Read-only MCP server exposing prodtools status and discovery."""
```

In `test/test_unit.py`, immediately after the existing line:

```python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

add:

```python
# The MCP server package lives outside utils/; add its src root so the
# server's tools are testable in this suite without MCP machinery.
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'mcp', 'src'))
```

- [ ] **Step 2: Write the failing tests**

Append to the end of `test/test_unit.py`, before the `if __name__ == '__main__':` block:

```python
# ---------------------------------------------------------------------------
# MCP adapters
# ---------------------------------------------------------------------------

class TestMcpAdapters(unittest.TestCase):
    def test_error_shape(self):
        from prodtools_mcp.adapters import error
        e = error('not_found', 'no such dataset', 'check the name')
        self.assertEqual(e, {'error': {'kind': 'not_found',
                                       'message': 'no such dataset',
                                       'remedy': 'check the name'}})

    def test_error_rejects_unknown_kind(self):
        from prodtools_mcp.adapters import error
        with self.assertRaises(ValueError):
            error('banana', 'nope')

    def test_safe_tool_passes_success_through(self):
        from prodtools_mcp.adapters import safe_tool

        @safe_tool
        def ok():
            return {'value': 1}
        self.assertEqual(ok(), {'value': 1})

    def test_safe_tool_converts_toolerror(self):
        from prodtools_mcp.adapters import safe_tool, ToolError

        @safe_tool
        def boom():
            raise ToolError('catalog_unavailable', 'SAM down', 'retry later')
        self.assertEqual(boom()['error']['kind'], 'catalog_unavailable')
        self.assertEqual(boom()['error']['remedy'], 'retry later')

    def test_safe_tool_traps_systemexit(self):
        """SystemExit derives from BaseException; an uncaught one would
        terminate the server rather than fail one call."""
        from prodtools_mcp.adapters import safe_tool

        @safe_tool
        def exits():
            sys.exit('MU2E_MAX_QUEUED is not an integer')
        result = exits()
        self.assertEqual(result['error']['kind'], 'internal')
        self.assertIn('MU2E_MAX_QUEUED', result['error']['message'])

    def test_safe_tool_converts_unexpected_exception(self):
        from prodtools_mcp.adapters import safe_tool

        @safe_tool
        def raises():
            raise RuntimeError('kaboom')
        result = raises()
        self.assertEqual(result['error']['kind'], 'internal')
        self.assertIn('kaboom', result['error']['message'])

    def test_safe_tool_keeps_stdout_clean(self):
        """stdout IS the JSON-RPC channel. A print() inside a util must
        not reach it (utils/famtree.py:71 does exactly this)."""
        from prodtools_mcp.adapters import safe_tool

        @safe_tool
        def chatty():
            print("No files found for dataset: dts.mu2e.X.Y.art")
            return {'ok': True}

        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, 'stdout', out), patch.object(sys, 'stderr', err):
            result = chatty()
        self.assertEqual(result, {'ok': True})
        self.assertEqual(out.getvalue(), '')
        self.assertIn('No files found', err.getvalue())

    def test_safe_tool_preserves_name(self):
        from prodtools_mcp.adapters import safe_tool

        @safe_tool
        def my_tool():
            """Docstring survives."""
            return {}
        self.assertEqual(my_tool.__name__, 'my_tool')
        self.assertEqual(my_tool.__doc__, 'Docstring survives.')
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest test/test_unit.py -v -k TestMcpAdapters`
Expected: FAIL — `ModuleNotFoundError: No module named 'prodtools_mcp'`

- [ ] **Step 4: Implement adapters.py**

Create `mcp/src/prodtools_mcp/adapters.py`:

```python
"""Boundary layer between MCP tool calls and prodtools internals.

Three jobs, all load-bearing for a stdio server:

1. Trap SystemExit. It derives from BaseException, so `except Exception`
   misses it; uncaught it terminates the server rather than failing one
   call. Reachable examples on these tools' paths: submissions.resolve_cap
   (utils/submissions.py:59) and _acquire_lock (utils/submissions.py:648).
2. Guard stdout. In a stdio server stdout IS the JSON-RPC channel, and
   utils/famtree.py:71 prints to it on the not-found path, directly on
   trace_provenance's route. Stray output is rerouted to stderr.
3. Build the error envelope every tool returns on failure.
"""
import contextlib
import functools
import sys

ERROR_KINDS = (
    'env_missing',
    'auth_expired',
    'catalog_unavailable',
    'not_found',
    'invalid_argument',
    'internal',
)


class ToolError(Exception):
    """A failure a tool can describe precisely. Carries a closed-set kind
    so callers can branch without parsing prose."""

    def __init__(self, kind, message, remedy=''):
        if kind not in ERROR_KINDS:
            raise ValueError(f"unknown error kind {kind!r}; "
                             f"expected one of {ERROR_KINDS}")
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.remedy = remedy


def error(kind, message, remedy=''):
    """Build the error envelope. Validates `kind` against the closed set
    so a typo becomes a loud failure here rather than a silently
    unhandleable payload at the caller."""
    if kind not in ERROR_KINDS:
        raise ValueError(f"unknown error kind {kind!r}; "
                         f"expected one of {ERROR_KINDS}")
    return {'error': {'kind': kind, 'message': message, 'remedy': remedy}}


def safe_tool(fn):
    """Wrap a tool function: stdout guarded, exceptions enveloped.

    SystemExit is caught explicitly and BEFORE the general handler —
    `except Exception` would not match it.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            with contextlib.redirect_stdout(sys.stderr):
                return fn(*args, **kwargs)
        except ToolError as exc:
            return error(exc.kind, exc.message, exc.remedy)
        except SystemExit as exc:
            return error(
                'internal',
                f'{fn.__name__} exited: {exc}',
                'This is a prodtools bug — a util called sys.exit() on a '
                'server code path. Report the tool name and arguments.')
        except Exception as exc:
            return error(
                'internal',
                f'{fn.__name__} failed: {type(exc).__name__}: {exc}',
                'Unexpected failure; check the server stderr log.')
    return wrapper
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest test/test_unit.py -v -k TestMcpAdapters`
Expected: PASS — 8 tests

Then the whole suite: `python -m pytest test/test_unit.py -q`
Expected: 548 passed (540 + 8)

- [ ] **Step 6: Commit**

```bash
git add mcp/src/prodtools_mcp/__init__.py mcp/src/prodtools_mcp/adapters.py test/test_unit.py
git commit -m "$(cat <<'EOF'
feat(mcp): adapters — error envelope, SystemExit trap, stdout guard

stdout is the JSON-RPC channel in a stdio server, and famtree.py:71
prints to it on the not-found path (directly on trace_provenance's
route), so every tool call is wrapped in redirect_stdout(sys.stderr).

SystemExit is caught explicitly: it derives from BaseException, so
`except Exception` misses it and an uncaught one would kill the server
rather than fail one call.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

### Task 2: ledger_ro.py — read-only ledger access

**Files:**
- Create: `mcp/src/prodtools_mcp/ledger_ro.py`
- Modify: `test/test_unit.py` (append test class)

**Interfaces:**
- Consumes: `prodtools_mcp.adapters.ToolError`
- Produces:
  - `DEFAULT_DB: str` (re-exported from `utils.submission_ledger`)
  - `campaigns(db_path: str | None = None, state: str | None = None) -> list[dict]` — each dict has keys `id, created_utc, state, map_path, tarball, entry (dict), cursor, slice_size, closed_utc, note`. `db_path=None` falls back to `DEFAULT_DB`.
  - `rows(db_path: str | None = None) -> list[dict]` — each dict has keys `id, created_utc, state, attempt, parent_id, map_path, tarball, entry (dict), indices (list), jobsub_id, cluster_id, closed_utc, note`

**Why this exists:** `utils/submission_ledger.py:73-84` `_connect` executes DDL on **every** connect (`_SCHEMA`, `_CAMPAIGN_SCHEMA`, and a `CREATE UNIQUE INDEX IF NOT EXISTS`). Reads work today as a non-mu2epro user only because every object already exists; creating a missing one raises `OperationalError: attempt to write a readonly database`. Any future schema addition shipped before mu2epro's writer runs it would break every `campaign_status` call.

- [ ] **Step 1: Write the failing tests**

Append to `test/test_unit.py`:

```python
# ---------------------------------------------------------------------------
# MCP read-only ledger
# ---------------------------------------------------------------------------

class TestMcpLedgerRo(unittest.TestCase):
    def _make_db(self, tmpdir):
        """Build a real ledger via the writer, so the read path is tested
        against the actual schema rather than a hand-rolled copy."""
        from utils import submission_ledger
        db = os.path.join(tmpdir, 'ledger.db')
        entry = {'njobs': 4000, 'outputs': [
            {'dataset': 'dig.mu2e.FlatGamma.MDC2025au_best_v1_3.art',
             'location': 'tape'}]}
        cid = submission_ledger.create_campaign(
            db, tarball='cnf.mu2e.FlatGamma.MDC2025au_best_v1_3.0.tar',
            entry=entry, slice_size=500, map_path='/tmp/map_au.json')
        submission_ledger.record_submission(
            db, tarball='cnf.mu2e.FlatGamma.MDC2025au_best_v1_3.0.tar',
            entry=entry, indices=[0, 1, 2], jobsub_id='29308498.0@sched',
            cluster_id='29308498', map_path='/tmp/map_au.json')
        return db, cid

    def test_campaigns_returns_parsed_entry(self):
        from prodtools_mcp import ledger_ro
        with tempfile.TemporaryDirectory() as td:
            db, cid = self._make_db(td)
            camps = ledger_ro.campaigns(db)
        self.assertEqual(len(camps), 1)
        self.assertEqual(camps[0]['id'], cid)
        self.assertEqual(camps[0]['slice_size'], 500)
        self.assertIsInstance(camps[0]['entry'], dict)
        self.assertEqual(camps[0]['entry']['njobs'], 4000)

    def test_campaigns_filters_by_state(self):
        from prodtools_mcp import ledger_ro
        with tempfile.TemporaryDirectory() as td:
            db, _ = self._make_db(td)
            self.assertEqual(len(ledger_ro.campaigns(db, state='active')), 1)
            self.assertEqual(len(ledger_ro.campaigns(db, state='complete')), 0)

    def test_rows_returns_parsed_indices_and_cluster(self):
        from prodtools_mcp import ledger_ro
        with tempfile.TemporaryDirectory() as td:
            db, _ = self._make_db(td)
            rows = ledger_ro.rows(db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['indices'], [0, 1, 2])
        self.assertEqual(rows[0]['cluster_id'], '29308498')

    def test_issues_no_ddl(self):
        """The writer's _connect runs CREATE statements on every connect;
        the read path must not, or a read-only DB raises OperationalError."""
        from prodtools_mcp import ledger_ro
        seen = []
        real_connect = sqlite3.connect

        def spy_connect(*a, **kw):
            con = real_connect(*a, **kw)
            real_execute = con.execute

            def exec_spy(sql, *rest):
                seen.append(sql)
                return real_execute(sql, *rest)
            con.execute = exec_spy
            return con

        with tempfile.TemporaryDirectory() as td:
            db, _ = self._make_db(td)
            with patch.object(sqlite3, 'connect', spy_connect):
                ledger_ro.campaigns(db)
        self.assertTrue(seen, "expected at least one statement")
        for sql in seen:
            self.assertNotIn('CREATE', sql.upper())

    def test_missing_db_is_catalog_unavailable(self):
        from prodtools_mcp import ledger_ro
        from prodtools_mcp.adapters import ToolError
        with self.assertRaises(ToolError) as ctx:
            ledger_ro.campaigns('/nonexistent/path/ledger.db')
        self.assertEqual(ctx.exception.kind, 'catalog_unavailable')

    def test_operational_error_becomes_catalog_unavailable(self):
        """A DB missing an expected object must surface as a typed error,
        not an OperationalError traceback."""
        from prodtools_mcp import ledger_ro
        from prodtools_mcp.adapters import ToolError
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, 'empty.db')
            sqlite3.connect(db).close()      # exists, but has no tables
            with self.assertRaises(ToolError) as ctx:
                ledger_ro.campaigns(db)
        self.assertEqual(ctx.exception.kind, 'catalog_unavailable')
```

Add `import sqlite3` to the imports at the top of `test/test_unit.py` (alphabetically after `import os`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test/test_unit.py -v -k TestMcpLedgerRo`
Expected: FAIL — `ModuleNotFoundError: No module named 'prodtools_mcp.ledger_ro'`

- [ ] **Step 3: Implement ledger_ro.py**

Create `mcp/src/prodtools_mcp/ledger_ro.py`:

```python
"""Read-only access to the submission ledger.

utils.submission_ledger._connect issues DDL on every connect
(utils/submission_ledger.py:73-84): _SCHEMA, _CAMPAIGN_SCHEMA, and a
CREATE UNIQUE INDEX. Reading works today as a non-mu2epro user only
because every object already exists — creating a missing one raises
`OperationalError: attempt to write a readonly database`. A future
schema addition shipped before mu2epro's writer has run it would
otherwise break every campaign_status call.

This module opens the DB with sqlite's mode=ro URI and issues no DDL.
"""
import json
import os
import sqlite3
from urllib.request import pathname2url

from prodtools_mcp.adapters import ToolError

# Re-exported so callers need not import the writer module for a path.
from utils.submission_ledger import DEFAULT_DB  # noqa: F401


def _connect(db_path):
    """Open the ledger read-only. No DDL, ever."""
    if not os.path.exists(db_path):
        raise ToolError(
            'catalog_unavailable',
            f'submission ledger not found: {db_path}',
            'Check MU2E_SUBMISSION_DB, or that the direct-submission '
            'subsystem has been run at least once.')
    uri = f'file:{pathname2url(db_path)}?mode=ro'
    try:
        con = sqlite3.connect(uri, uri=True, timeout=30)
    except sqlite3.Error as exc:
        raise ToolError('catalog_unavailable',
                        f'cannot open ledger {db_path}: {exc}',
                        'Check filesystem access to the ledger.') from exc
    con.row_factory = sqlite3.Row
    return con


def _query(db_path, sql, params=()):
    con = _connect(db_path)
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    except sqlite3.OperationalError as exc:
        raise ToolError(
            'catalog_unavailable',
            f'ledger query failed on {db_path}: {exc}',
            'The ledger schema may be older than this server expects, or '
            'a writer crash left a hot journal a reader cannot roll back.'
        ) from exc
    finally:
        con.close()


def campaigns(db_path=None, state=None):
    """Campaign rows, newest last. `entry` is parsed from entry_json."""
    db_path = db_path or DEFAULT_DB
    sql = 'SELECT * FROM campaigns'
    params = ()
    if state is not None:
        sql += ' WHERE state = ?'
        params = (state,)
    sql += ' ORDER BY id'
    out = []
    for row in _query(db_path, sql, params):
        row['entry'] = json.loads(row.pop('entry_json'))
        out.append(row)
    return out


def rows(db_path=None):
    """Submission rows, newest last. `entry` and `indices` are parsed."""
    db_path = db_path or DEFAULT_DB
    out = []
    for row in _query(db_path, 'SELECT * FROM submissions ORDER BY id'):
        row['entry'] = json.loads(row.pop('entry_json'))
        row['indices'] = json.loads(row.pop('indices_json'))
        out.append(row)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test/test_unit.py -v -k TestMcpLedgerRo`
Expected: PASS — 6 tests

Whole suite: `python -m pytest test/test_unit.py -q`
Expected: 554 passed

- [ ] **Step 5: Commit**

```bash
git add mcp/src/prodtools_mcp/ledger_ro.py test/test_unit.py
git commit -m "$(cat <<'EOF'
feat(mcp): read-only ledger access with no DDL

submission_ledger._connect issues CREATE statements on every connect
(:73-84). Reads work today as a non-mu2epro user only because every
object already exists; a future schema addition shipped before the
writer runs it would break every campaign_status call with
"attempt to write a readonly database".

Opens with sqlite's mode=ro URI, issues no DDL, and converts
OperationalError to a typed catalog_unavailable rather than a traceback.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

### Task 3: tools/status.py — campaign_status and list_campaigns

**Files:**
- Create: `mcp/src/prodtools_mcp/tools/__init__.py`
- Create: `mcp/src/prodtools_mcp/tools/status.py`
- Modify: `test/test_unit.py` (append test class)

**Interfaces:**
- Consumes: `prodtools_mcp.ledger_ro.campaigns/rows`, `prodtools_mcp.adapters.ToolError`
- Produces:
  - `queue_block(cluster_ids: list, clusters: dict | None) -> dict`
  - `campaign_status(campaign=None, campaign_id=None, include_queue=True, include_outputs=True, db_path=None, clusters_fn=None, count_fn=None) -> dict`
  - `list_campaigns(state=None, db_path=None) -> dict`

**Background the implementer needs.** `utils/submissions.py:124` `live_clusters(user='mu2epro', runner=subprocess.run)` returns `{cluster_id_str: [state_letters]}` or **`None`** when the query cannot be trusted. State letters: `R` running, `I` idle, `H` held, `C`/`X` terminal. `cluster_queue_state(cluster_id, clusters)` returns `'drained' | 'held' | 'running' | 'error'`.

`None` means *unknown*, and it is critical it never becomes zero: proc-form `jobsub_q` was verified on 2026-07-22 to report 0 total while 1976 jobs of one cluster ran. A `{"running": 0}` from a failed query would read as "campaign drained" and could trigger a recovery pass against live jobs.

Cluster IDs live on **submission rows**, not campaigns — there is no foreign key, so rows correlate to a campaign by `tarball`.

- [ ] **Step 1: Write the failing tests**

Append to `test/test_unit.py`:

```python
# ---------------------------------------------------------------------------
# MCP status tools
# ---------------------------------------------------------------------------

class TestMcpQueueBlock(unittest.TestCase):
    def test_unknown_omits_counts_entirely(self):
        """A failed jobsub_q must NOT serialize as running:0 — that reads
        as 'drained' and could trigger recovery against live jobs."""
        from prodtools_mcp.tools.status import queue_block
        block = queue_block(['29308498'], None)
        self.assertEqual(block['state'], 'unknown')
        self.assertIn('reason', block)
        for key in ('running', 'idle', 'held'):
            self.assertNotIn(key, block)

    def test_counts_by_state_letter(self):
        from prodtools_mcp.tools.status import queue_block
        block = queue_block(
            ['29308498'], {'29308498': ['R', 'R', 'I', 'H', 'C', 'X']})
        self.assertEqual(block['state'], 'known')
        self.assertEqual(block['running'], 2)
        self.assertEqual(block['idle'], 1)
        self.assertEqual(block['held'], 1)

    def test_absent_cluster_is_zero_not_unknown(self):
        """A genuinely drained cluster is a real zero, distinct from
        an unknown snapshot."""
        from prodtools_mcp.tools.status import queue_block
        block = queue_block(['29308498'], {'99999999': ['R']})
        self.assertEqual(block['state'], 'known')
        self.assertEqual(block['running'], 0)
        self.assertEqual(block['clusters'], [])


class TestMcpCampaignStatus(unittest.TestCase):
    def _make_db(self, tmpdir):
        from utils import submission_ledger
        db = os.path.join(tmpdir, 'ledger.db')
        entry = {'njobs': 4000, 'outputs': [
            {'dataset': 'dig.mu2e.FlatGamma.MDC2025au_best_v1_3.art',
             'location': 'tape'}]}
        submission_ledger.create_campaign(
            db, tarball='cnf.mu2e.FlatGamma.MDC2025au_best_v1_3.0.tar',
            entry=entry, slice_size=500, map_path='/tmp/map_au.json')
        submission_ledger.record_submission(
            db, tarball='cnf.mu2e.FlatGamma.MDC2025au_best_v1_3.0.tar',
            entry=entry, indices=[0, 1], jobsub_id='29308498.0@sched',
            cluster_id='29308498', map_path='/tmp/map_au.json')
        return db

    def test_ledger_only_when_no_campaign_named(self):
        """The bare call must not touch the network — otherwise a 23-row
        ledger fans out to one SAM count per output dataset."""
        from prodtools_mcp.tools import status

        def boom(*a, **kw):
            raise AssertionError("network call in ledger-only mode")

        with tempfile.TemporaryDirectory() as td:
            db = self._make_db(td)
            result = status.campaign_status(
                db_path=db, clusters_fn=boom, count_fn=boom)
        self.assertEqual(len(result['campaigns']), 1)
        camp = result['campaigns'][0]
        self.assertNotIn('queue', camp)
        self.assertNotIn('outputs', camp)
        self.assertEqual(camp['njobs'], 4000)
        self.assertEqual(camp['slice_size'], 500)

    def test_no_integer_entry_field(self):
        """The ledger stores the whole entry dict as entry_json; an index
        into the map is not recoverable, so we must not invent one."""
        from prodtools_mcp.tools import status
        with tempfile.TemporaryDirectory() as td:
            db = self._make_db(td)
            camp = status.campaign_status(db_path=db)['campaigns'][0]
        self.assertNotIn('entry', camp)

    def test_named_campaign_includes_queue_and_outputs(self):
        from prodtools_mcp.tools import status
        with tempfile.TemporaryDirectory() as td:
            db = self._make_db(td)
            result = status.campaign_status(
                campaign='MDC2025au', db_path=db,
                clusters_fn=lambda: {'29308498': ['R', 'R']},
                count_fn=lambda ds: 412)
        camp = result['campaigns'][0]
        self.assertEqual(camp['queue']['running'], 2)
        self.assertEqual(camp['outputs']['datasets'][0]['produced'], 412)
        self.assertEqual(camp['outputs']['datasets'][0]['expected'], 4000)

    def test_output_count_failure_is_unknown_not_zero(self):
        from prodtools_mcp.tools import status

        def boom(ds):
            raise RuntimeError('SAM down')

        with tempfile.TemporaryDirectory() as td:
            db = self._make_db(td)
            result = status.campaign_status(
                campaign='MDC2025au', db_path=db,
                clusters_fn=lambda: None, count_fn=boom)
        camp = result['campaigns'][0]
        self.assertEqual(camp['outputs']['state'], 'unknown')
        self.assertNotIn('datasets', camp['outputs'])

    def test_unknown_campaign_is_not_found(self):
        from prodtools_mcp.tools import status
        from prodtools_mcp.adapters import ToolError
        with tempfile.TemporaryDirectory() as td:
            db = self._make_db(td)
            with self.assertRaises(ToolError) as ctx:
                status.campaign_status(campaign='MDC9999zz', db_path=db)
        self.assertEqual(ctx.exception.kind, 'not_found')

    def test_shared_tarball_adds_conflation_note(self):
        """Rows correlate to a campaign by tarball only — no FK — so a
        reused tarball must be flagged, not silently merged."""
        from utils import submission_ledger
        from prodtools_mcp.tools import status
        with tempfile.TemporaryDirectory() as td:
            db = self._make_db(td)
            camps = submission_ledger.all_campaigns(db)
            submission_ledger.set_campaign_state(db, camps[0]['id'],
                                                 'complete')
            submission_ledger.create_campaign(
                db, tarball='cnf.mu2e.FlatGamma.MDC2025au_best_v1_3.0.tar',
                entry={'njobs': 4000, 'outputs': []}, slice_size=500)
            result = status.campaign_status(db_path=db)
        self.assertTrue(any('note' in c for c in result['campaigns']))


class TestMcpListCampaigns(unittest.TestCase):
    def test_filters_by_state(self):
        from utils import submission_ledger
        from prodtools_mcp.tools import status
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, 'ledger.db')
            submission_ledger.create_campaign(
                db, tarball='cnf.a.0.tar', entry={'njobs': 10},
                slice_size=5)
            active = status.list_campaigns(state='active', db_path=db)
            done = status.list_campaigns(state='complete', db_path=db)
        self.assertEqual(active['count'], 1)
        self.assertEqual(done['count'], 0)

    def test_rejects_bad_state(self):
        from prodtools_mcp.tools import status
        from prodtools_mcp.adapters import ToolError
        with self.assertRaises(ToolError) as ctx:
            status.list_campaigns(state='banana', db_path='/x')
        self.assertEqual(ctx.exception.kind, 'invalid_argument')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test/test_unit.py -v -k "TestMcpQueueBlock or TestMcpCampaignStatus or TestMcpListCampaigns"`
Expected: FAIL — `ModuleNotFoundError: No module named 'prodtools_mcp.tools'`

- [ ] **Step 3: Implement the tools package and status.py**

Create `mcp/src/prodtools_mcp/tools/__init__.py`:

```python
"""Tool implementations. Plain functions, no MCP machinery."""
```

Create `mcp/src/prodtools_mcp/tools/status.py`:

```python
"""Campaign status tools.

Composed from the read-only ledger plus, optionally, a jobsub_q snapshot
and SAM output counts. The bare call is ledger-only: a 23-row ledger
fanned out to one SAM count per output dataset would exceed the client's
timeout.
"""
from prodtools_mcp import ledger_ro
from prodtools_mcp.adapters import ToolError

# jobsub table state letters. C and X are terminal.
_TERMINAL = ('C', 'X')

CAMPAIGN_STATES = ('active', 'complete', 'paused', 'cancelled')


def queue_block(cluster_ids, clusters):
    """Queue counts for a campaign's clusters from a live_clusters()
    snapshot.

    A None snapshot means the query could not be trusted. It returns
    state='unknown' and OMITS the count keys entirely — there must be no
    zero to misread. Proc-form jobsub_q was verified on 2026-07-22
    reporting 0 total while 1976 jobs of one cluster ran; a {"running": 0}
    from a failed query reads as 'drained' and could trigger a recovery
    pass against live jobs.
    """
    if clusters is None:
        return {'state': 'unknown',
                'reason': 'jobsub_q query failed or was unparseable'}
    running = idle = held = 0
    seen = []
    for cid in cluster_ids:
        states = clusters.get(str(cid))
        if not states:
            continue
        seen.append(str(cid))
        for st in states:
            if st in _TERMINAL:
                continue
            if st == 'H':
                held += 1
            elif st == 'I':
                idle += 1
            else:
                running += 1
    return {'state': 'known', 'running': running, 'idle': idle,
            'held': held, 'clusters': seen}


def _outputs_block(entry, njobs, count_fn):
    """Produced-vs-expected per output dataset.

    `expected` is njobs: one output file per job per stream. Derived this
    way deliberately, to avoid a /pnfs cnf read on every status call.
    """
    from utils.poms_entry import outputs_of
    try:
        outputs = outputs_of(entry)
    except ValueError as exc:
        return {'state': 'unknown', 'reason': str(exc)}
    datasets = []
    for out in outputs:
        dataset = out.get('dataset')
        if not dataset:
            continue
        try:
            produced = count_fn(dataset)
        except Exception as exc:
            return {'state': 'unknown',
                    'reason': f'SAM count failed for {dataset}: {exc}'}
        datasets.append({'dataset': dataset, 'expected': njobs,
                         'produced': produced})
    return {'state': 'known', 'datasets': datasets}


def _default_clusters_fn():
    from utils.submissions import live_clusters
    return live_clusters()


def _default_count_fn(dataset):
    from utils.samweb_wrapper import dataset_file_count
    return dataset_file_count(dataset)


def _matches(camp, campaign, campaign_id):
    if campaign_id is not None:
        return camp['id'] == campaign_id
    if campaign is not None:
        return campaign in (camp['tarball'] or '')
    return True


def campaign_status(campaign=None, campaign_id=None, include_queue=True,
                    include_outputs=True, db_path=None,
                    clusters_fn=None, count_fn=None):
    """Status of one campaign, or a ledger-only summary of all of them.

    With neither `campaign` nor `campaign_id`, this is ledger-only: local
    sqlite, no network, and the queue/outputs blocks are omitted.
    """
    from utils.poms_entry import njobs_of

    all_camps = ledger_ro.campaigns(db_path)
    selected = [c for c in all_camps if _matches(c, campaign, campaign_id)]
    if not selected:
        raise ToolError(
            'not_found',
            f'no campaign matching '
            f'{campaign_id if campaign_id is not None else campaign!r}',
            'Call list_campaigns() to see what exists.')

    named = campaign is not None or campaign_id is not None
    want_queue = named and include_queue
    want_outputs = named and include_outputs

    all_rows = ledger_ro.rows(db_path) if want_queue else []
    clusters = None
    if want_queue:
        clusters = (clusters_fn or _default_clusters_fn)()

    tarball_counts = {}
    for camp in all_camps:
        tarball_counts[camp['tarball']] = \
            tarball_counts.get(camp['tarball'], 0) + 1

    out = []
    for camp in selected:
        njobs = njobs_of(camp['entry'])
        rec = {
            'id': camp['id'],
            'state': camp['state'],
            'tarball': camp['tarball'],
            'map_path': camp['map_path'],
            'slice_size': camp['slice_size'],
            'cursor': camp['cursor'],
            'njobs': njobs,
            'created_utc': camp['created_utc'],
        }
        if tarball_counts.get(camp['tarball'], 0) > 1:
            rec['note'] = ('more than one campaign shares this tarball; '
                           'submission rows correlate by tarball only '
                           '(no foreign key) and may be conflated')
        if want_queue:
            mine = [r for r in all_rows if r['tarball'] == camp['tarball']]
            rec['rows'] = {
                'open': sum(1 for r in mine if r['state'] == 'active'),
                'closed': sum(1 for r in mine if r['state'] != 'active'),
            }
            cluster_ids = [r['cluster_id'] for r in mine if r['cluster_id']]
            rec['queue'] = queue_block(cluster_ids, clusters)
        if want_outputs:
            rec['outputs'] = _outputs_block(
                camp['entry'], njobs, count_fn or _default_count_fn)
        out.append(rec)

    return {'db_path': db_path or ledger_ro.DEFAULT_DB, 'campaigns': out}


def list_campaigns(state=None, db_path=None):
    """Ledger-only campaign listing. No network."""
    if state is not None and state not in CAMPAIGN_STATES:
        raise ToolError(
            'invalid_argument',
            f'unknown state {state!r}',
            f'Expected one of {CAMPAIGN_STATES}.')
    camps = ledger_ro.campaigns(db_path, state=state)
    from utils.poms_entry import njobs_of
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
    return {'count': len(listing), 'campaigns': listing}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test/test_unit.py -v -k "TestMcpQueueBlock or TestMcpCampaignStatus or TestMcpListCampaigns"`
Expected: PASS — 11 tests

Whole suite: `python -m pytest test/test_unit.py -q`
Expected: 565 passed

- [ ] **Step 5: Commit**

```bash
git add mcp/src/prodtools_mcp/tools/__init__.py mcp/src/prodtools_mcp/tools/status.py test/test_unit.py
git commit -m "$(cat <<'EOF'
feat(mcp): campaign_status and list_campaigns

A failed jobsub_q returns state='unknown' with the count keys ABSENT.
Proc-form jobsub_q was verified 2026-07-22 reporting 0 total while 1976
jobs ran; {"running": 0} from a failed query reads as 'drained' and
could trigger recovery against live jobs.

The bare call is ledger-only — no network — since a 23-row ledger would
otherwise fan out to one SAM count per output dataset per call.

No integer `entry` field: the ledger stores the whole entry dict as
entry_json, so a map index is not recoverable and is not invented.
Rows correlate to a campaign by tarball (no FK), so a reused tarball
carries an explicit conflation note.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

### Task 4: tools/discovery.py — find_datasets and dataset_details

**Files:**
- Create: `mcp/src/prodtools_mcp/tools/discovery.py`
- Modify: `test/test_unit.py` (append test class)

**Interfaces:**
- Consumes: `prodtools_mcp.adapters.ToolError`
- Produces:
  - `find_datasets(campaign=None, tier=None, desc=None, pattern=None, latest_only=False, require_files=False, fetch_fn=None, count_fn=None) -> dict`
  - `dataset_details(dataset, summary_fn=None, created_fn=None) -> dict`

**Background the implementer needs.** `utils/latestDatasets.py:47` `fetch_definitions(defname_pattern, user)` shells `samweb list-definitions`. That is a **definition listing, not an existence check**: zero-file definitions appear, and `-LH`/`-CH` variants do not. `latest_per_description(names)` (`:51`) picks the newest dsconf per description.

`utils/samweb_wrapper.py:405` `dataset_summary(dataset)` returns file/event/size counts but **no creation time**; that needs `definition_creation_date` (`:386`), which returns `None` for exactly the metadata-only datasets above. So `created_utc` is nullable and comes from a second call.

- [ ] **Step 1: Write the failing tests**

Append to `test/test_unit.py`:

```python
# ---------------------------------------------------------------------------
# MCP discovery tools
# ---------------------------------------------------------------------------

class TestMcpFindDatasets(unittest.TestCase):
    NAMES = [
        'dig.mu2e.FlatGamma.MDC2025au_best_v1_3.art',
        'dig.mu2e.FlatGamma.MDC2025ar_best_v1_1.art',
        'dts.mu2e.CeMLeadingLog.MDC2025au.art',
    ]

    def test_parses_name_fields(self):
        from prodtools_mcp.tools import discovery
        res = discovery.find_datasets(pattern='*', fetch_fn=lambda p, u: self.NAMES)
        first = [d for d in res['datasets']
                 if d['name'].startswith('dig.mu2e.FlatGamma.MDC2025au')][0]
        self.assertEqual(first['tier'], 'dig')
        self.assertEqual(first['owner'], 'mu2e')
        self.assertEqual(first['desc'], 'FlatGamma')
        self.assertEqual(first['dsconf'], 'MDC2025au_best_v1_3')
        self.assertEqual(first['file_format'], 'art')

    def test_filters_by_campaign_and_tier(self):
        from prodtools_mcp.tools import discovery
        res = discovery.find_datasets(campaign='MDC2025au', tier='dig',
                                      fetch_fn=lambda p, u: self.NAMES)
        self.assertEqual(res['count'], 1)
        self.assertEqual(res['datasets'][0]['dsconf'], 'MDC2025au_best_v1_3')

    def test_always_reports_basis(self):
        """A definition listing must never be mistaken for existence."""
        from prodtools_mcp.tools import discovery
        res = discovery.find_datasets(pattern='*', fetch_fn=lambda p, u: self.NAMES)
        self.assertIn('basis', res)
        self.assertIn('list-definitions', res['basis'])

    def test_require_files_drops_empty_definitions(self):
        from prodtools_mcp.tools import discovery
        counts = {n: (0 if 'ar_best' in n else 5) for n in self.NAMES}
        res = discovery.find_datasets(pattern='*', require_files=True,
                                      fetch_fn=lambda p, u: self.NAMES,
                                      count_fn=lambda ds: counts[ds])
        self.assertTrue(all('ar_best' not in d['name'] for d in res['datasets']))
        self.assertEqual(res['count'], 2)

    def test_catalog_failure_is_not_empty_list(self):
        from prodtools_mcp.tools import discovery
        from prodtools_mcp.adapters import ToolError

        def boom(pattern, user):
            raise RuntimeError('SAM unreachable')

        with self.assertRaises(ToolError) as ctx:
            discovery.find_datasets(pattern='*', fetch_fn=boom)
        self.assertEqual(ctx.exception.kind, 'catalog_unavailable')


class TestMcpDatasetDetails(unittest.TestCase):
    SUMMARY = {'file_count': 800, 'total_event_count': 4000000,
               'total_file_size': 4294967296}

    def test_composes_summary_and_creation_date(self):
        from prodtools_mcp.tools import discovery
        import datetime as _dt
        res = discovery.dataset_details(
            'dig.mu2e.FlatGamma.MDC2025au_best_v1_3.art',
            summary_fn=lambda ds: self.SUMMARY,
            created_fn=lambda ds: _dt.datetime(2026, 7, 25, 2, 11,
                                               tzinfo=_dt.timezone.utc))
        self.assertTrue(res['exists'])
        self.assertEqual(res['file_count'], 800)
        self.assertEqual(res['event_count'], 4000000)
        self.assertEqual(res['total_size_bytes'], 4294967296)
        self.assertEqual(res['created_utc'], '2026-07-25T02:11:00+00:00')

    def test_created_utc_is_nullable(self):
        """definition_creation_date returns None for metadata-only
        -LH/-CH datasets; that is data, not an error."""
        from prodtools_mcp.tools import discovery
        res = discovery.dataset_details(
            'dig.mu2e.X.Y-LH.art',
            summary_fn=lambda ds: self.SUMMARY,
            created_fn=lambda ds: None)
        self.assertIsNone(res['created_utc'])
        self.assertTrue(res['exists'])

    def test_zero_files_means_not_exists(self):
        from prodtools_mcp.tools import discovery
        res = discovery.dataset_details(
            'dig.mu2e.Nope.Z.art',
            summary_fn=lambda ds: {'file_count': 0, 'total_event_count': 0,
                                   'total_file_size': 0},
            created_fn=lambda ds: None)
        self.assertFalse(res['exists'])

    def test_summary_failure_is_catalog_unavailable(self):
        from prodtools_mcp.tools import discovery
        from prodtools_mcp.adapters import ToolError

        def boom(ds):
            raise RuntimeError('SAM down')

        with self.assertRaises(ToolError) as ctx:
            discovery.dataset_details('x.y.z.w.art', summary_fn=boom)
        self.assertEqual(ctx.exception.kind, 'catalog_unavailable')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test/test_unit.py -v -k "TestMcpFindDatasets or TestMcpDatasetDetails"`
Expected: FAIL — `ModuleNotFoundError: No module named 'prodtools_mcp.tools.discovery'`

- [ ] **Step 3: Implement discovery.py**

Create `mcp/src/prodtools_mcp/tools/discovery.py`:

```python
"""Dataset discovery tools.

find_datasets reports a samweb DEFINITION listing, which is not the same
as existence: zero-file definitions appear and -LH/-CH variants do not.
Every response carries that caveat in `basis` so a caller cannot mistake
one for the other.
"""
from prodtools_mcp.adapters import ToolError

_BASIS = ('samweb list-definitions: a definition listing, not an '
          'existence check — zero-file definitions appear and -LH/-CH '
          'variants do not. Pass require_files=True to filter to '
          'definitions with at least one file.')


def _default_fetch_fn(pattern, user):
    from utils.latestDatasets import fetch_definitions
    return fetch_definitions(pattern, user)


def _default_count_fn(dataset):
    from utils.samweb_wrapper import dataset_file_count
    return dataset_file_count(dataset)


def _parse(name):
    """Split tier.owner.desc.dsconf.format. Returns None if it does not
    have the five prodtools fields."""
    parts = name.split('.')
    if len(parts) != 5:
        return None
    return {'name': name, 'tier': parts[0], 'owner': parts[1],
            'desc': parts[2], 'dsconf': parts[3], 'file_format': parts[4]}


def find_datasets(campaign=None, tier=None, desc=None, pattern=None,
                  latest_only=False, require_files=False, user=None,
                  fetch_fn=None, count_fn=None):
    """Datasets matching the given filters, from the SAM definition list."""
    fetch = fetch_fn or _default_fetch_fn
    query = pattern or '*'
    try:
        names = fetch(query, user)
    except Exception as exc:
        raise ToolError(
            'catalog_unavailable',
            f'samweb list-definitions failed: {exc}',
            'Check SAM availability and that muse setup ops has run.'
        ) from exc

    if latest_only:
        from utils.latestDatasets import latest_per_description
        names = latest_per_description(names)

    records = []
    for name in names:
        rec = _parse(name)
        if rec is None:
            continue
        if campaign and campaign not in rec['dsconf']:
            continue
        if tier and rec['tier'] != tier:
            continue
        if desc and rec['desc'] != desc:
            continue
        records.append(rec)

    if require_files:
        count = count_fn or _default_count_fn
        kept = []
        for rec in records:
            try:
                if count(rec['name']) > 0:
                    kept.append(rec)
            except Exception as exc:
                raise ToolError(
                    'catalog_unavailable',
                    f'file count failed for {rec["name"]}: {exc}',
                    'Check SAM availability.') from exc
        records = kept

    records.sort(key=lambda r: r['name'])
    return {'count': len(records), 'truncated': False,
            'basis': _BASIS, 'datasets': records}


def _default_summary_fn(dataset):
    from utils.samweb_wrapper import dataset_summary
    return dataset_summary(dataset)


def _default_created_fn(dataset):
    from utils.samweb_wrapper import definition_creation_date
    return definition_creation_date(dataset)


def dataset_details(dataset, summary_fn=None, created_fn=None):
    """File/event/size counts for one dataset, plus its creation date.

    `exists` is decided by file count, not by definition listing.
    `created_utc` is nullable: definition_creation_date returns None for
    exactly the metadata-only -LH/-CH datasets, and dataset_summary
    carries no creation time of its own.
    """
    try:
        summary = (summary_fn or _default_summary_fn)(dataset) or {}
    except Exception as exc:
        raise ToolError(
            'catalog_unavailable',
            f'dataset summary failed for {dataset}: {exc}',
            'Check SAM availability and that muse setup ops has run.'
        ) from exc

    try:
        created = (created_fn or _default_created_fn)(dataset)
    except Exception:
        created = None      # creation date is decoration, not the answer

    file_count = summary.get('file_count', 0) or 0
    return {
        'dataset': dataset,
        'exists': file_count > 0,
        'file_count': file_count,
        'event_count': summary.get('total_event_count', 0) or 0,
        'total_size_bytes': summary.get('total_file_size', 0) or 0,
        'created_utc': created.isoformat() if created is not None else None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test/test_unit.py -v -k "TestMcpFindDatasets or TestMcpDatasetDetails"`
Expected: PASS — 9 tests

Whole suite: `python -m pytest test/test_unit.py -q`
Expected: 574 passed

- [ ] **Step 5: Commit**

```bash
git add mcp/src/prodtools_mcp/tools/discovery.py test/test_unit.py
git commit -m "$(cat <<'EOF'
feat(mcp): find_datasets and dataset_details

find_datasets always reports its `basis`: samweb list-definitions is a
definition listing, not an existence check — zero-file definitions
appear and -LH/-CH variants do not. require_files=True filters to
definitions with at least one file.

dataset_details decides `exists` by file count, and created_utc is
nullable via definition_creation_date (dataset_summary carries no
creation time, and the date is None for exactly the metadata-only
-LH/-CH datasets).

Catalog failures raise catalog_unavailable rather than returning an
empty list — an empty list is a finding, and manufacturing one from an
error is how a campaign gets declared complete that is not.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

### Task 5: tools/lineage.py — trace_provenance

**Files:**
- Create: `mcp/src/prodtools_mcp/tools/lineage.py`
- Modify: `test/test_unit.py` (append test class)

**Interfaces:**
- Consumes: `prodtools_mcp.adapters.ToolError`
- Produces:
  - `walk(root: str, direction: str, depth: int, edge_fn) -> tuple[list[str], list[dict], bool]` returning `(nodes, edges, truncated)`
  - `trace_provenance(name, direction='up', depth=3, parents_fn=None, children_fn=None) -> dict`

**Background the implementer needs.** This is **new traversal code, not a wrapper.** `utils/famtree.py:118` `topology_for_dataset` has no depth limit, no truncation signal, and walks parents only — its recursion is a closure that cannot be parameterized. Nothing in `famtree` walks children; `utils/samweb_wrapper.py:417` `children_of_file(filename)` is per-file.

`famtree.get_parents` is decorated `@functools.lru_cache(maxsize=None)` (`famtree.py:46`). Lineage is immutable so cached values stay correct, but an unbounded cache in a long-lived server grows without limit — this module uses its own bounded cache.

`famtree.get_first_file_from_dataset` **prints to stdout** on the not-found path (`famtree.py:71`). Task 1's `safe_tool` redirects it, and Step 1 below tests that end-to-end.

- [ ] **Step 1: Write the failing tests**

Append to `test/test_unit.py`:

```python
# ---------------------------------------------------------------------------
# MCP lineage
# ---------------------------------------------------------------------------

class TestMcpLineage(unittest.TestCase):
    #  a -> b -> d
    #    -> c
    GRAPH = {'a': ['b', 'c'], 'b': ['d'], 'c': [], 'd': []}

    def test_walks_to_depth(self):
        from prodtools_mcp.tools.lineage import walk
        nodes, edges, truncated = walk(
            'a', 'up', 3, lambda n: self.GRAPH.get(n, []))
        self.assertEqual(set(nodes), {'a', 'b', 'c', 'd'})
        self.assertIn({'child': 'a', 'parent': 'b'}, edges)
        self.assertIn({'child': 'b', 'parent': 'd'}, edges)
        self.assertFalse(truncated)

    def test_depth_limit_sets_truncated(self):
        from prodtools_mcp.tools.lineage import walk
        nodes, edges, truncated = walk(
            'a', 'up', 1, lambda n: self.GRAPH.get(n, []))
        self.assertEqual(set(nodes), {'a', 'b', 'c'})
        self.assertTrue(truncated)

    def test_direction_down_reverses_edge_sense(self):
        from prodtools_mcp.tools.lineage import walk
        _, edges, _ = walk('a', 'down', 1, lambda n: self.GRAPH.get(n, []))
        self.assertIn({'child': 'b', 'parent': 'a'}, edges)

    def test_cycle_terminates(self):
        from prodtools_mcp.tools.lineage import walk
        cyclic = {'a': ['b'], 'b': ['a']}
        nodes, _, _ = walk('a', 'up', 10, lambda n: cyclic.get(n, []))
        self.assertEqual(set(nodes), {'a', 'b'})

    def test_rejects_bad_direction(self):
        from prodtools_mcp.tools import lineage
        from prodtools_mcp.adapters import ToolError
        with self.assertRaises(ToolError) as ctx:
            lineage.trace_provenance('x', direction='sideways')
        self.assertEqual(ctx.exception.kind, 'invalid_argument')

    def test_rejects_bad_depth(self):
        from prodtools_mcp.tools import lineage
        from prodtools_mcp.adapters import ToolError
        with self.assertRaises(ToolError) as ctx:
            lineage.trace_provenance('x', depth=0)
        self.assertEqual(ctx.exception.kind, 'invalid_argument')

    def test_trace_provenance_shape(self):
        from prodtools_mcp.tools import lineage
        res = lineage.trace_provenance(
            'a', direction='up', depth=2,
            parents_fn=lambda n: self.GRAPH.get(n, []))
        self.assertEqual(res['root'], 'a')
        self.assertEqual(res['direction'], 'up')
        self.assertEqual(res['depth'], 2)
        self.assertIn('nodes', res)
        self.assertIn('edges', res)
        self.assertNotIn('mermaid', res)

    def test_stdout_stays_clean_through_safe_tool(self):
        """famtree.get_first_file_from_dataset prints to stdout on the
        not-found path (famtree.py:71), directly on this route."""
        from prodtools_mcp.adapters import safe_tool
        from prodtools_mcp.tools import lineage

        def chatty_parents(node):
            print(f"No files found for dataset: {node}")
            return []

        wrapped = safe_tool(lineage.trace_provenance)
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, 'stdout', out), patch.object(sys, 'stderr', err):
            res = wrapped('a', parents_fn=chatty_parents)
        self.assertEqual(out.getvalue(), '')
        self.assertIn('No files found', err.getvalue())
        self.assertEqual(res['root'], 'a')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test/test_unit.py -v -k TestMcpLineage`
Expected: FAIL — `ModuleNotFoundError: No module named 'prodtools_mcp.tools.lineage'`

- [ ] **Step 3: Implement lineage.py**

Create `mcp/src/prodtools_mcp/tools/lineage.py`:

```python
"""Depth-bounded provenance traversal.

New code, not a wrapper: famtree.topology_for_dataset (famtree.py:118)
has no depth limit, no truncation signal, and walks parents only — its
recursion is a closure that cannot be parameterized. Nothing in famtree
walks children; samweb_wrapper.children_of_file (:417) is per-file.

famtree.get_parents is lru_cache(maxsize=None) (famtree.py:46). Lineage
is immutable so its cached values stay correct, but an unbounded cache
in a long-lived server grows without limit, so this module keeps its own
bounded one.
"""
import functools

from prodtools_mcp.adapters import ToolError

DIRECTIONS = ('up', 'down')
MAX_DEPTH = 10
_CACHE_SIZE = 4096


def walk(root, direction, depth, edge_fn):
    """Breadth-first walk to `depth` levels.

    Returns (nodes, edges, truncated). `truncated` is True when the depth
    limit cut the walk short — the caller must be able to tell a complete
    answer from a clipped one.
    """
    seen = {root}
    order = [root]
    frontier = [root]
    edges = []
    for _ in range(depth):
        nxt = []
        for node in frontier:
            for other in edge_fn(node):
                if direction == 'up':
                    edge = {'child': node, 'parent': other}
                else:
                    edge = {'child': other, 'parent': node}
                if edge not in edges:
                    edges.append(edge)
                if other not in seen:
                    seen.add(other)
                    order.append(other)
                    nxt.append(other)
        frontier = nxt
        if not frontier:
            break
    return order, edges, bool(frontier)


def _default_parents_fn():
    from utils.famtree import get_parents

    @functools.lru_cache(maxsize=_CACHE_SIZE)
    def parents(name):
        return tuple(get_parents(name))
    return parents


def _default_children_fn():
    from utils.samweb_wrapper import children_of_file

    @functools.lru_cache(maxsize=_CACHE_SIZE)
    def children(name):
        return tuple(children_of_file(name))
    return children


def trace_provenance(name, direction='up', depth=3,
                     parents_fn=None, children_fn=None):
    """Lineage of a file as nodes and edges.

    Returns data, not presentation — no mermaid string; the caller can
    render one from the edges.
    """
    if direction not in DIRECTIONS:
        raise ToolError('invalid_argument',
                        f'unknown direction {direction!r}',
                        f'Expected one of {DIRECTIONS}.')
    if not isinstance(depth, int) or depth < 1 or depth > MAX_DEPTH:
        raise ToolError('invalid_argument',
                        f'depth must be an integer in 1..{MAX_DEPTH}, '
                        f'got {depth!r}',
                        f'Use a depth between 1 and {MAX_DEPTH}.')

    if direction == 'up':
        edge_fn = parents_fn or _default_parents_fn()
    else:
        edge_fn = children_fn or _default_children_fn()

    try:
        nodes, edges, truncated = walk(name, direction, depth, edge_fn)
    except Exception as exc:
        raise ToolError(
            'catalog_unavailable',
            f'lineage lookup failed for {name}: {exc}',
            'Check SAM availability and that muse setup ops has run.'
        ) from exc

    return {'root': name, 'direction': direction, 'depth': depth,
            'truncated': truncated, 'nodes': nodes, 'edges': edges}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test/test_unit.py -v -k TestMcpLineage`
Expected: PASS — 8 tests

Whole suite: `python -m pytest test/test_unit.py -q`
Expected: 582 passed

- [ ] **Step 5: Commit**

```bash
git add mcp/src/prodtools_mcp/tools/lineage.py test/test_unit.py
git commit -m "$(cat <<'EOF'
feat(mcp): trace_provenance with a depth-bounded walk

New traversal, not a wrapper: topology_for_dataset (famtree.py:118) has
no depth limit, no truncation signal, and walks parents only — its
recursion is a closure that cannot be parameterized.

Returns nodes and edges as data, not the mermaid string, and sets
`truncated` when the depth limit clipped the walk so a caller can tell a
complete answer from a partial one. Uses its own bounded cache rather
than famtree's lru_cache(maxsize=None), which would grow without limit
in a long-lived server.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

### Task 6: server.py, packaging, and the two-part check

**Files:**
- Create: `mcp/src/prodtools_mcp/server.py`
- Create: `mcp/pyproject.toml`
- Create: `mcp/scripts/start_mcp.sh`
- Create: `mcp/scripts/install.sh`
- Create: `mcp/scripts/smoke_test_stdio.py`
- Modify: `test/test_unit.py` (append test class)

**Interfaces:**
- Consumes: every tool function from Tasks 3–5, and `prodtools_mcp.adapters.safe_tool`
- Produces: `create_mcp_server() -> FastMCP`, `get_server_info() -> dict`, `main() -> None`

**Background the implementer needs.** `start_mcp.sh` must add **three** things to `PYTHONPATH`: the venv site-packages, the ops path, and **the prodtools repo root** (because `prodtools_mcp` imports `utils.*`). The metacat script it is modelled on has no repo-root line to copy.

The two-part check exists because the metacat venv is **not self-contained**: `.venv/bin/python -c "import mcp"` fails with `ModuleNotFoundError: No module named 'idna'` and works only because the ops `PYTHONPATH` is layered underneath. Part 1 runs *without* the ops path so that class of breakage fails at install rather than at first use.

- [ ] **Step 1: Write the failing tests**

Append to `test/test_unit.py`:

```python
# ---------------------------------------------------------------------------
# MCP server wiring
# ---------------------------------------------------------------------------

class TestMcpServerInfo(unittest.TestCase):
    def test_declares_read_only(self):
        from prodtools_mcp.server import get_server_info
        info = get_server_info()
        self.assertFalse(info['writes'])
        self.assertIn('read-only', info['description'].lower())

    def test_lists_every_tool(self):
        from prodtools_mcp.server import get_server_info, TOOL_NAMES
        info = get_server_info()
        self.assertEqual(sorted(info['tools']), sorted(TOOL_NAMES))
        self.assertEqual(len(TOOL_NAMES), 6)


class TestMcpToolRegistration(unittest.TestCase):
    def test_every_tool_is_wrapped_in_safe_tool(self):
        """An unwrapped tool could kill the server via SystemExit or
        corrupt the JSON-RPC stream via print()."""
        from prodtools_mcp import server
        for name, fn in server.TOOL_FUNCTIONS.items():
            self.assertTrue(getattr(fn, '__wrapped__', None) is not None,
                            f'{name} is not wrapped in safe_tool')

    def test_tool_names_covers_functions_plus_server_info(self):
        from prodtools_mcp import server
        self.assertEqual(
            sorted(server.TOOL_NAMES),
            sorted(list(server.TOOL_FUNCTIONS) + ['get_server_info']))

    def test_no_tool_can_reach_definition_writers(self):
        """create_definition/delete_definition must never be referenced
        from the server package."""
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent / 'mcp' / 'src'
        offenders = []
        for path in root.rglob('*.py'):
            text = path.read_text()
            for bad in ('create_definition', 'delete_definition'):
                if bad in text:
                    offenders.append(f'{path}: {bad}')
        self.assertEqual(offenders, [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest test/test_unit.py -v -k "TestMcpServerInfo or TestMcpToolRegistration"`
Expected: FAIL — `ModuleNotFoundError: No module named 'prodtools_mcp.server'`

- [ ] **Step 3: Implement server.py**

Create `mcp/src/prodtools_mcp/server.py`:

```python
"""FastMCP wiring for the read-only prodtools server.

This module holds NO logic. Every tool is a plain function in tools/,
wrapped in adapters.safe_tool and registered here. That keeps the tools
testable without MCP machinery or a stdio transport.
"""
import logging
import os
import sys

from prodtools_mcp.adapters import safe_tool
from prodtools_mcp.tools import discovery, lineage, status

LOGGER = logging.getLogger('prodtools_mcp')

INSTRUCTIONS = """
Read-only MCP server for Mu2e prodtools production state.

This server performs NO writes: it cannot submit jobs, create or delete
SAM definitions, or modify the submission ledger. Submission remains the
/mu2epro-submit path.

WHAT IT ANSWERS:
- "How is <campaign> doing?"  -> campaign_status(campaign="MDC2025au")
- "What is running at all?"   -> list_campaigns(state="active")
- "What datasets exist?"      -> find_datasets(campaign="MDC2025au")
- "How big is this dataset?"  -> dataset_details(dataset="dig.mu2e...art")
- "Where did this come from?" -> trace_provenance(name="...", direction="up")

READING THE RESULTS:
- campaign_status called with NO argument is ledger-only and cheap. Name
  a campaign to include queue and output counts, which hit the network.
- A queue or outputs block with state="unknown" has NO count keys. Do
  NOT read a missing count as zero: the query failed, and the campaign
  may well be running. Never start a recovery pass on an "unknown".
- find_datasets reports a samweb DEFINITION listing (see its `basis`
  field): zero-file definitions appear and -LH/-CH variants do not. Pass
  require_files=True when you need existence.
- Errors arrive as {"error": {"kind", "message", "remedy"}}. Never retry
  an auth_expired — tell the user to renew in their own shell.
"""

# Wrapped once, here, so registration and tests see the same objects.
TOOL_FUNCTIONS = {
    'campaign_status': safe_tool(status.campaign_status),
    'list_campaigns': safe_tool(status.list_campaigns),
    'find_datasets': safe_tool(discovery.find_datasets),
    'dataset_details': safe_tool(discovery.dataset_details),
    'trace_provenance': safe_tool(lineage.trace_provenance),
}

TOOL_NAMES = sorted(list(TOOL_FUNCTIONS) + ['get_server_info'])


def get_server_info():
    """Capabilities and safe-usage guidance for this server."""
    return {
        'name': 'prodtools',
        'description': 'Read-only access to Mu2e prodtools campaign '
                       'status and dataset discovery.',
        'writes': False,
        'tools': TOOL_NAMES,
        'ledger_db': os.environ.get(
            'MU2E_SUBMISSION_DB',
            '/exp/mu2e/data/users/mu2epro/prodtools/submissions.db'),
        'guidance': INSTRUCTIONS.strip(),
    }


def _configure_logging():
    logging.basicConfig(
        level=os.environ.get('PRODTOOLS_MCP_LOG_LEVEL', 'INFO'),
        stream=sys.stderr,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )


def create_mcp_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP('prodtools', instructions=INSTRUCTIONS)

    @mcp.tool(description='Status of one campaign, or a cheap ledger-only '
                          'summary of all of them when called with no '
                          'argument.')
    def campaign_status(campaign: str = None, campaign_id: int = None,
                        include_queue: bool = True,
                        include_outputs: bool = True) -> dict:
        return TOOL_FUNCTIONS['campaign_status'](
            campaign=campaign, campaign_id=campaign_id,
            include_queue=include_queue, include_outputs=include_outputs)

    @mcp.tool(description='List submission campaigns, optionally filtered '
                          'by state (active/complete/paused/cancelled).')
    def list_campaigns(state: str = None) -> dict:
        return TOOL_FUNCTIONS['list_campaigns'](state=state)

    @mcp.tool(description='Find datasets by campaign, tier, description, '
                          'or glob pattern. Reports a definition listing; '
                          'pass require_files=True for existence.')
    def find_datasets(campaign: str = None, tier: str = None,
                      desc: str = None, pattern: str = None,
                      latest_only: bool = False,
                      require_files: bool = False) -> dict:
        return TOOL_FUNCTIONS['find_datasets'](
            campaign=campaign, tier=tier, desc=desc, pattern=pattern,
            latest_only=latest_only, require_files=require_files)

    @mcp.tool(description='File count, event count, size, and creation '
                          'date for one dataset.')
    def dataset_details(dataset: str) -> dict:
        return TOOL_FUNCTIONS['dataset_details'](dataset=dataset)

    @mcp.tool(description='Trace a file\'s lineage as nodes and edges, '
                          'up (parents) or down (children).')
    def trace_provenance(name: str, direction: str = 'up',
                         depth: int = 3) -> dict:
        return TOOL_FUNCTIONS['trace_provenance'](
            name=name, direction=direction, depth=depth)

    # Registered under the module function's name via name=, because a
    # nested `def get_server_info` would shadow the module-level one and
    # recurse. The advertised name must match TOOL_NAMES.
    @mcp.tool(name='get_server_info',
              description='Server capabilities and safe-usage guidance.')
    def _server_info_tool() -> dict:
        return get_server_info()

    return mcp


def main():
    _configure_logging()
    create_mcp_server().run()


if __name__ == '__main__':
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest test/test_unit.py -v -k "TestMcpServerInfo or TestMcpToolRegistration"`
Expected: PASS — 5 tests

Whole suite: `python -m pytest test/test_unit.py -q`
Expected: 587 passed

- [ ] **Step 5: Write pyproject.toml**

Create `mcp/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "prodtools-mcp"
version = "0.1.0"
description = "Read-only MCP stdio server for Mu2e prodtools status and discovery"
requires-python = ">=3.10"
dependencies = [
  "mcp>=1.2.0"
]

[project.scripts]
prodtools-mcp = "prodtools_mcp.server:main"

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 6: Write start_mcp.sh**

Create `mcp/scripts/start_mcp.sh` (then `chmod +x`):

```bash
#!/usr/bin/env bash
# Start the read-only prodtools MCP stdio server.
#
# All setup output goes to stderr: stdout is the JSON-RPC channel and a
# single stray line on it corrupts the protocol stream.
set -euo pipefail

MCP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$MCP_ROOT/.." && pwd)"

# CVMFS setup scripts are not set -e clean; guard around them.
set +u
if [[ $- == *e* ]]; then _restore_e=1; set +e; else _restore_e=0; fi
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh 1>&2
_rc=$?
if [[ ${_restore_e} -eq 1 ]]; then set -e; fi
if [[ ${_rc} -ne 0 ]]; then exit ${_rc}; fi
muse setup ops 1>&2
set -u

MU2E_OPS_PYTHONPATH="${PYTHONPATH:-}"

if [[ -n "${MCP_PYTHON:-}" ]]; then
  PYTHON_BIN="$MCP_PYTHON"
elif [[ -x "$MCP_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$MCP_ROOT/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

VENV_SITE="$("$PYTHON_BIN" - <<'PY'
import site
paths = [p for p in site.getsitepackages() if 'site-packages' in p]
print(paths[0] if paths else '')
PY
)"

# Order matters: venv first, then the repo root (prodtools_mcp imports
# utils.*), then the ops env. metacat's script has no repo-root entry to
# copy — this server needs one.
PP="$REPO_ROOT"
[[ -n "$VENV_SITE" ]] && PP="$VENV_SITE:$PP"
[[ -n "$MU2E_OPS_PYTHONPATH" ]] && PP="$PP:$MU2E_OPS_PYTHONPATH"
export PYTHONPATH="$PP"

if [[ "${1:-}" == "--check" ]]; then
  echo "== part 1: MCP deps WITHOUT the ops path ==" 1>&2
  env -u PYTHONPATH PYTHONPATH="${VENV_SITE:-}:$REPO_ROOT" \
    "$PYTHON_BIN" - <<'PY'
import importlib
importlib.import_module("mcp.server.fastmcp")
print("OK: mcp imports without the ops PYTHONPATH (self-contained)")
PY
  echo "== part 2: full environment ==" 1>&2
  "$PYTHON_BIN" - <<'PY'
import importlib, sys
importlib.import_module("mcp.server.fastmcp")
importlib.import_module("samweb_client")
from prodtools_mcp.server import get_server_info
info = get_server_info()
print("OK: interpreter", sys.executable)
print("OK: tools", ", ".join(info["tools"]))
PY
  exit 0
fi

exec "$PYTHON_BIN" -m prodtools_mcp.server
```

- [ ] **Step 7: Write install.sh**

Create `mcp/scripts/install.sh` (then `chmod +x`):

```bash
#!/usr/bin/env bash
# Build the venv and verify it. Run once after checkout.
set -euo pipefail

MCP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$MCP_ROOT"

set +u
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh 1>&2 || true
muse setup ops 1>&2
set -u

# Record which spack env this venv's interpreter binds to. An ops-env
# retirement changes the failure mode from an import error to a failed
# exec, so the binding is worth having written down.
echo "binding to: $(command -v python3)" | tee "$MCP_ROOT/.venv-binding"

python3 -m venv .venv
# --upgrade so the venv carries its OWN transitive deps. metacat's venv
# does not, and survives only because the ops PYTHONPATH supplies idna.
./.venv/bin/pip install --upgrade pip 1>&2
./.venv/bin/pip install -e . 1>&2

echo "== verifying =="
exec "$MCP_ROOT/scripts/start_mcp.sh" --check
```

- [ ] **Step 8: Write the stdio smoke test**

Create `mcp/scripts/smoke_test_stdio.py`:

```python
#!/usr/bin/env python3
"""Spawn the server over stdio, list its tools, call server_info.

Exercises the real transport, which the unit tests deliberately do not.
Run:  python3 mcp/scripts/smoke_test_stdio.py
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
START = os.path.join(HERE, 'start_mcp.sh')


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command='bash', args=[START])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print('tools:', ', '.join(names))
            assert 'campaign_status' in names, names
            assert 'get_server_info' in names, names
            result = await session.call_tool('get_server_info', {})
            print('get_server_info ok:', not result.isError)
            assert not result.isError, result
    print('SMOKE OK')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f'SMOKE FAILED: {exc}', file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 9: Verify the scripts run**

```bash
chmod +x mcp/scripts/start_mcp.sh mcp/scripts/install.sh
bash mcp/scripts/install.sh
```
Expected: part 1 prints `OK: mcp imports without the ops PYTHONPATH (self-contained)`, part 2 prints the interpreter and six tool names.

If part 1 fails with a `ModuleNotFoundError`, the venv is not self-contained — `pip install` the named package explicitly into `.venv` and re-run. Do **not** "fix" it by adding the ops path to part 1; that is the exact failure this check exists to catch.

```bash
python3 mcp/scripts/smoke_test_stdio.py
```
Expected: `SMOKE OK`

- [ ] **Step 10: Commit**

```bash
git add mcp/pyproject.toml mcp/scripts mcp/src/prodtools_mcp/server.py test/test_unit.py
git commit -m "$(cat <<'EOF'
feat(mcp): FastMCP server wiring, packaging, two-part check

server.py holds no logic — every tool is a plain function in tools/,
wrapped once in safe_tool and registered here, so the tools stay
testable without MCP machinery.

install.sh runs a two-part check. Part 1 imports the MCP deps WITHOUT
the ops PYTHONPATH, proving self-containment: metacat's venv fails
exactly this (ModuleNotFoundError: idna) and survives only because the
ops path is layered underneath. Part 2 verifies the full environment.

start_mcp.sh adds the repo root to PYTHONPATH — prodtools_mcp imports
utils.*, and the metacat script it is modelled on has no such line. All
setup output goes to stderr, since stdout is the JSON-RPC channel.

A test asserts no module under mcp/src references create_definition or
delete_definition.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

### Task 7: Registration and documentation

**Files:**
- Modify: `.mcp.json`
- Modify: `.claude/settings.json`
- Modify: `CLAUDE.md`
- Create: `wiki/pages/prodtools-mcp-server.md`
- Modify: `wiki/index.md`

**Interfaces:**
- Consumes: `mcp/scripts/start_mcp.sh` from Task 6.
- Produces: nothing code-level.

- [ ] **Step 1: Register the server**

Replace the contents of `.mcp.json` with:

```json
{
  "mcpServers": {
    "metacat-readonly": {
      "command": "/exp/mu2e/app/users/oksuzian/muse_050125/aitools/mcp/metacat/scripts/start_mcp.sh"
    },
    "prodtools": {
      "command": "/exp/mu2e/app/users/oksuzian/muse_050125/prodtools/mcp/scripts/start_mcp.sh"
    }
  }
}
```

In `.claude/settings.json`, change the `enabledMcpjsonServers` line to:

```json
  "enabledMcpjsonServers": ["metacat-readonly", "prodtools"],
```

- [ ] **Step 2: Verify the server is reachable**

```bash
bash mcp/scripts/start_mcp.sh --check
```
Expected: both parts OK. (The Claude Code client picks up `.mcp.json` on next start; no further action needed here.)

- [ ] **Step 3: Add the CLAUDE.md pointer**

In `CLAUDE.md`, immediately after the `## Prodtools usage` section, insert:

```markdown
## MCP server

A read-only MCP server at `mcp/` exposes campaign status and dataset
discovery as typed tools (`campaign_status`, `list_campaigns`,
`find_datasets`, `dataset_details`, `trace_provenance`,
`get_server_info`). Prefer it over shelling the CLI for status questions —
it returns structured JSON and costs less context.

It performs **no writes**. Submission remains `/mu2epro-submit`.

A queue or outputs block with `state: "unknown"` has **no count keys**.
Never read a missing count as zero: the query failed and the campaign
may still be running. Do not start a recovery pass on an `unknown`.

Setup: `bash mcp/scripts/install.sh`. Health check:
`bash mcp/scripts/start_mcp.sh --check`.
```

- [ ] **Step 4: Write the wiki page**

Create `wiki/pages/prodtools-mcp-server.md`:

```markdown
---
title: prodtools MCP server (read-only)
tags: [reference, mcp, tooling, commissioned]
sources: [2026-07-26-prodtools-mcp-design]
updated: 2026-07-26
---

# prodtools MCP server (read-only)

Stdio MCP server exposing prodtools campaign status and dataset
discovery to any MCP client. Spec:
`docs/superpowers/specs/2026-07-26-prodtools-mcp-design.md`.

## What it is

Six tools: `campaign_status`, `list_campaigns`, `find_datasets`,
`dataset_details`, `trace_provenance`, `get_server_info`. It imports
`utils/*` in-process and composes existing functions; there is no LLM
in it and it makes no external API calls.

It performs **no writes** — no submission, no SAM definition create or
delete, no ledger mutation. Every tool is safe as the calling user;
none needs mu2epro.

## Reading the output

- `campaign_status()` with no argument is ledger-only and cheap. Naming
  a campaign adds queue and output counts, which hit the network.
- **`state: "unknown"` is not zero.** An unknown queue block omits its
  count keys entirely. Proc-form `jobsub_q` was verified on 2026-07-22
  reporting 0 total while 1976 jobs of one cluster ran, so a
  `{"running": 0}` from a failed query would read as "drained" and could
  trigger a recovery pass against live jobs.
- `find_datasets` reports a **definition listing**, not existence:
  zero-file definitions appear and `-LH`/`-CH` variants do not. Its
  `basis` field says so on every response. Pass `require_files=True`
  when you need existence.

## Operating it

```bash
bash mcp/scripts/install.sh            # once
bash mcp/scripts/start_mcp.sh --check  # health
python3 mcp/scripts/smoke_test_stdio.py
```

`--check` is two-part on purpose. Part 1 imports the MCP dependencies
**without** the ops `PYTHONPATH`. The neighbouring metacat server fails
exactly this — `import mcp` raises `ModuleNotFoundError: No module
named 'idna'` — and works only because its start script layers the ops
path underneath, leaving it one ops-env bump from breaking. Part 2
verifies the full environment.

The venv's interpreter still binds to the ops spack view (system
`/usr/bin/python3` is 3.9, too old for `mcp`). `install.sh` records the
binding in `mcp/.venv-binding`; an ops-env retirement will present as a
failed exec rather than an import error.

## Design notes worth keeping

- **stdout is the JSON-RPC channel.** `utils/famtree.py:71` prints to
  stdout on the not-found path, directly on `trace_provenance`'s route,
  so `adapters.safe_tool` redirects stdout to stderr around every call.
- **`SystemExit` is trapped explicitly** — it derives from
  `BaseException`, so `except Exception` misses it and an uncaught one
  would kill the server rather than fail one call.
- **The ledger is opened `mode=ro` with no DDL.**
  `submission_ledger._connect` issues `CREATE` statements on every
  connect; a future schema addition shipped before mu2epro's writer runs
  it would otherwise break every status call.

## Not included

Job submission. It was designed alongside these tools and pulled out
after review found the direct path never calls `check_inputs`
(`utils/submit.py:241` runs only under `--enqueue`) and has no
idempotency guard under client timeouts. Until a follow-on spec lands,
`/mu2epro-submit` is the submission path.
```

- [ ] **Step 5: Link it from the wiki index**

In `wiki/index.md`, add to the reference pages list:

```markdown
- [prodtools MCP server (read-only)](pages/prodtools-mcp-server.md) — six status/discovery tools; `unknown` ≠ zero; two-part venv check.
```

- [ ] **Step 6: Run the full suite one last time**

Run: `python -m pytest test/test_unit.py -q`
Expected: 587 passed

- [ ] **Step 7: Commit**

```bash
git add .mcp.json .claude/settings.json CLAUDE.md wiki/pages/prodtools-mcp-server.md wiki/index.md
git commit -m "$(cat <<'EOF'
docs(mcp): register the server and document it

Adds the prodtools entry to .mcp.json and enabledMcpjsonServers, a
CLAUDE.md pointer, and a wiki page.

All three repeat the one rule a caller can do damage by getting wrong:
a queue or outputs block with state="unknown" has NO count keys, and a
missing count must never be read as zero.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0188sbNRs537Esmwr4EPeNaF
EOF
)"
```

---

## Verification checklist

After Task 7, confirm all of the following:

- [ ] `python -m pytest test/test_unit.py -q` → 586 passed
- [ ] `bash mcp/scripts/start_mcp.sh --check` → both parts OK
- [ ] `python3 mcp/scripts/smoke_test_stdio.py` → `SMOKE OK`
- [ ] `grep -rn "create_definition\|delete_definition" mcp/src/` → no matches
- [ ] `grep -rn "htgettoken\|kinit\|voms-proxy" mcp/src/ mcp/scripts/` → no matches
- [ ] `grep -rn "ksu\|mu2epro -e" mcp/src/` → no matches (this phase writes nothing)
- [ ] Branch is ahead but **not pushed** — report to the user for their own `git push`
