# prodtools MCP write path — identity-aware submission

**Status:** approved design, not yet implemented
**Supersedes:** the "Deferred: submission" section of
`2026-07-26-prodtools-mcp-design.md:492-529`

**Goal:** let a submission run under the caller's own identity or under
`mu2epro`, and expose the full push → enqueue → submit chain over MCP as
typed tools, without weakening the confirmation gate that protects
production.

**Scope:** two phases in one spec. Phase 1 hardens `utils/` so the CLI is
identity-correct and retry-safe. Phase 2 adds a separate write-capable
MCP server on top. The phases ship in order; Phase 1 has standalone
value and Phase 2 is unsound without it.

---

## Why now

Production writes are reachable only through `ksu mu2epro` in a Bash
tool call. That has two costs: every push needs a permission prompt on a
*shell string* rather than on a declared action, and a user who wants to
submit a test campaign under their own name has no supported path.

A typed MCP tool is a strictly narrower capability than a Bash allowlist
entry — `Bash(ksu mu2epro:*)` authorizes anything at all as the
production account, while `push_cnf(...)` can only do the one declared
thing. The gate is preserved rather than traded away, because hook
matchers can match MCP tool names.

## Findings that shaped this design

**The submission stack is already identity-parameterized.** Everything
derives from whoever runs the process:

| axis | as a user | as `mu2epro` | source |
|---|---|---|---|
| jobsub role | default (Analysis) | `Production` | `jobsub_argv.role_for_user:44` |
| outstage wftop | `/pnfs/mu2e/scratch/users` | `/pnfs/mu2e/persistent/users` | `jobsub_argv.default_wftop:49` |
| dataset owner | `$USER` | `mu2e` | `job_common.default_owner:218` |
| prodtools tarball | `/tmp/prodtools-$USER.tar` | `/tmp/prodtools-mu2epro.tar` | `submit.py:45` |

So "mine today, `mu2epro` tomorrow" is not a feature to build. The only
difference between the two is whether the command is wrapped in `ksu`.

**The ledger is the one axis that is not identity-derived.**

```python
DEFAULT_DB = os.environ.get('MU2E_SUBMISSION_DB',
    '/exp/mu2e/data/users/mu2epro/prodtools/submissions.db')  # submission_ledger.py:29
```

That file is `-rw-r--r--`: world-readable, `mu2epro`-writable only. A
self-submission therefore submits its grid jobs and *then* fails the
ledger write. For self-submission that partial failure is not a rare
race, it is the guaranteed outcome.

**The ledger row is written after submission.** `_record_in_ledger`
(`submit.py:137`) documents itself as recording "a successful direct
submission" and never raises, "the submission already happened". A
client that dies between `jobsub_submit` and the ledger write leaves a
live cluster with no row; the retry re-submits the same window as
duplicate physics. Today this is mitigated only by a human rule — never
wrap `submissions run` in `timeout` — which exists precisely because the
code is not idempotent.

**Authorization is bounded and not ours to route around.** Becoming
`mu2epro` is gated by `~mu2epro/.k5login` (24 principals). A write tool's
`mu2epro` path therefore serves exactly that set; its `self` path serves
everyone.

---

## Phase 1 — identity-correct, retry-safe `utils/`

### 1.1 Two names instead of one overloaded default

```python
PRODUCTION_DB = '/exp/mu2e/data/users/mu2epro/prodtools/submissions.db'

def ledger_for(user=None):
    return f'/exp/mu2e/data/users/{user or getpass.getuser()}/prodtools/submissions.db'
```

`DEFAULT_DB` keeps meaning production. This is deliberate: there is **one
production ledger everyone reads and N personal ledgers each person
writes**, so a single default cannot serve both.

- **Readers** (`ledger_ro.py:73-106`, the read-only MCP,
  `listNewDatasets.py:40`, `submissions status`) resolve to
  `PRODUCTION_DB` — unchanged, no regression.
- **Writers** (`submit_map`, `submissions run`) default to
  `ledger_for()`.
- `MU2E_SUBMISSION_DB` continues to override both.

For `mu2epro` the two paths coincide, so the production cron and every
existing path behave identically. This is a pure generalization with no
migration.

### 1.2 `submissions status --mine`

Default remains production. `--mine` selects `ledger_for()`. Personal
campaigns are never merged into the production listing by default.

### 1.3 Ledger directory creation

`ledger_for()`'s parent directory is created on first write. If it
cannot be created, fail loudly. **No fallback to `PRODUCTION_DB`** — a
silent fallback would write personal campaigns into the production
ledger, the worst available outcome.

### 1.4 Two-phase ledger write

Reserve the row **before** `jobsub_submit`, carrying the window and a
`submitting` state; fill in `cluster_id` and `jobsub_id` after it
returns.

- The overlap guard (`_slice_overlaps_ledger`, `submissions.py:291`)
  consults reserved rows, so a retry after a timeout hits an in-flight
  window and refuses instead of duplicating.
- A reserved row with no cluster is a visible `needs_reconciliation`
  state rather than an invisible orphan.
- This retires the "never `timeout` a submission" rule by making the
  code idempotent instead of relying on operator discipline.

### 1.5 `check_inputs` on the direct path

`check_inputs` currently runs only in `_enqueue_entries`
(`submit.py:241`), reached only via `--enqueue` (`submit.py:601`). A
windowed direct submit never calls it and can launch against unverified
inputs — the bulk-death failure the gate exists to prevent. Run it on
the direct path too.

**1.4 and 1.5 fix defects that bite `/mu2epro-submit` users today.**
That is the point of putting safety in the CLI rather than in the MCP
wrapper: a guard in the wrapper leaves every other caller exposed and
will drift.

---

## Phase 2 — the `prodtools-write` MCP server

### 2.1 Layout

`mcp/src/prodtools_mcp_write/`, sharing the existing venv and
`install.sh`, with its own `scripts/start_write_mcp.sh` and its own
`.mcp.json` entry. Separate process and separate tool namespace
(`mcp__prodtools-write__*`); shared packaging.

Rationale for a separate server over adding tools to the read-only one:
the existing server's "performs NO writes" claim is documented in
`CLAUDE.md` and in its own server instructions, and it is why those tools
are called without deliberation. Adding writes turns that into
"read-only except these three," a caveat that erodes. A separate
namespace also means one hook matcher covers every write tool that will
ever exist, instead of an enumeration a future tool can silently escape.

### 2.2 Tools

| tool | arguments | returns |
|---|---|---|
| `push_cnf` | `json, desc, dsconf, jobdefs_map, run_as, confirm, simjob_version=None` | `{tarball, datasets, map_path, entry_index}` |
| `enqueue_campaign` | `map_path, entry, slice_size, run_as, confirm` | `{campaign_id, njobs, tarball}` |
| `run_submissions` | `campaign_id, run_as, confirm` | campaign summary + attention keys |

**`run_as` is required and has no default** (`"self"` | `"mu2epro"`), so
production can never arrive from an omitted argument.

**Every argument that could fan out is required, none defaults to "all".**
`entry` is required, retiring the `entry=None` fan-out over every entry
in a map (`submit.py:583`). `campaign_id` is likewise required: ticking
every active campaign is the cron's job, not an interactive call, and a
tool whose default action is "everything" is the hazard this rule exists
to remove.

`jobdefs_map` is always explicit. Note that
`production_manager/direct_maps/` is `mu2epro`-owned, so a
`run_as="self"` push must name a map path the caller can write; the tool
does not invent one.

### 2.3 The gate

`run_as="mu2epro"` passes two independent gates:

1. **In-tool**: the call is refused unless `confirm=true`.
2. **Hook**: a `PreToolUse` matcher on `mcp__prodtools-write__.*`
   prompts, mirroring `.claude/hooks/mu2epro-guard.sh`.

Both, because the hook depends on settings being loaded — a known
failure mode is a guard that needs a `/hooks` reload to arm. A gate that
can be silently absent is not a gate for an irreversible action. The
in-tool refusal lives in the type signature and cannot be configured
away.

`run_as="self"` requires neither confirm nor prompt: it writes only the
caller's scratch outstage, datasets, and ledger.

Note that the existing `mu2epro-guard.sh` is registered with
`matcher: "Bash"` and greps the command string. It cannot fire for a
subprocess spawned inside an MCP server, so **the hook registration must
be extended as part of this work** or the gate disappears by
construction rather than by decision.

### 2.4 Execution

- `run_as="self"` calls the CLI in-process through the existing
  `adapters.py` (error envelope, `SystemExit` trap, stdout guard — built
  for exactly this).
- `run_as="mu2epro"` shells the ksu block verbatim from
  `.claude/commands/mu2epro-submit.md:121-133`: `mktemp` **inside** ksu
  (a caller-owned workdir makes `condor_vault_storer` fail), `cd
  "$WORKDIR"`, the `USER`/`LOGNAME`/`HOME`/`XDG_RUNTIME_DIR` exports,
  and `setupmu2e-art.sh` + `muse setup ops` sourced (or `jobsub_submit`
  is not on PATH). Each of these is a known failure, not a style choice.

**Results are read back from the ledger, never scraped from stdout.**
`submit_map` already records `cluster_id` and `jobsub_id`
(`submit.py:137-166`); parsing human output through ksu would reintroduce
exactly the parsing this project exists to eliminate.

### 2.5 Failure handling

- **Credentials are never remediated.** A missing `mu2epro` token
  returns a structured error and stops. No refresh, ever.
- **`needs_reconciliation`**: a reserved row with no cluster is reported
  with its row id and never auto-retried.
- **ksu auth failure** returns verbatim, no retry.
- **Client timeout** is now recoverable rather than duplicating, because
  of 1.4.

---

## Testing

**Unit** — ledger resolution (`PRODUCTION_DB` vs `ledger_for`, reader vs
writer defaults, `MU2E_SUBMISSION_DB` override); directory creation
failing loudly with no production fallback; the two-phase write; overlap
refusal against a reserved row; and both refusal paths (`run_as`
missing, `mu2epro` without `confirm`).

**Acceptance** — a real `run_as="self"` submission of a tiny campaign,
end to end: push, enqueue, submit, recover. Cheap, unprivileged, real
grid, and it proves the entire identity path including the personal
ledger.

**The `mu2epro` path is dry-run only in automated tests.** Its first
real use is a deliberate manual one.

---

## Out of scope

- **A request/approval queue** letting non-privileged users initiate
  *production* work. That is a governance system, not plumbing: it needs
  an authorization model, an approval path, and a rejection story. If it
  is ever wanted it gets its own spec.
- **Retiring `/mu2epro-submit`.** The skills remain the supported path
  and the fallback while the MCP path earns trust.
- **The MCP output-count defect** (`tools/status.py:161-168` feeding the
  bare glob `*.art` to SAM, degrading every glob-output campaign to
  `state: "unknown"`). Real, unrelated, tracked separately.
