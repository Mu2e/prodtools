# prodtools MCP read path — identity-parameterized status

**Status:** implemented; live-verified against the acceptance fixture 2026-08-09.
**Follows:** `2026-08-08-mcp-write-path-design.md`, whose acceptance run
exposed this gap.

**Goal:** let `campaign_status` and `list_campaigns` report on the
caller's own submission ledger and grid queue, not only `mu2epro`'s.

**Scope:** two tools in `mcp/src/prodtools_mcp/`. No write capability is
added, and the read-only server stays read-only — that claim is why its
tools are called without deliberation, and nothing here weakens it.

---

## 1. The problem

The write path is identity-parameterized end to end: `run_as` selects the
account, `ledger_for(user)` selects the ledger, and after the acceptance
run's fixes `queue_owner()` selects the queue. The read path is not. It
is hardwired to production on two axes:

- `ledger_ro.DEFAULT_DB` — the production ledger (or `MU2E_SUBMISSION_DB`).
- `condor.OWNER = 'mu2epro'` — a module constant closed over by
  `_default_clusters_fn`.

So a campaign submitted with `run_as="self"` can be written but not
watched. `campaign_status` finds no such campaign and `list_campaigns`
omits it.

**The failure is silent, and that is the point.** An empty answer from
the wrong ledger is indistinguishable from "no campaigns". A
`queue: {state: "known", running: 0}` computed against the wrong account
is indistinguishable from "nothing is running". This is the same shape as
the drain-check bug the acceptance run found on the write side
(`171517f`): `live_clusters()` defaulted to `mu2epro`, a self tick did not
find its own cluster in production's queue, absent-from-snapshot reads as
`drained`, and a running row was recovered mid-flight. The read side has
not caused an incident only because nobody has yet trusted it for a
non-production campaign.

## 2. Surface

One new parameter, `mine`, on two tools:

```python
campaign_status(campaign=None, campaign_id=None, include_queue=True,
                include_outputs=True, mine=False)
list_campaigns(state=None, mine=False)
```

The name is deliberately the CLI's: `submissions --mine status` already
means exactly this. One concept, one word, in both places.

`find_datasets`, `dataset_details` and `trace_provenance` are unchanged.
They are already identity-neutral: the owner is a field in the SAM name
pattern, so `mcs.oksuzian.*` already works and no parameter would add
anything.

### 2.1 Resolution

| `mine` | ledger | condor owner |
|---|---|---|
| `False` (default) | `ledger_ro.DEFAULT_DB` — unchanged, `MU2E_SUBMISSION_DB` honored | `condor.OWNER` |
| `True` | `submission_ledger.ledger_for()` | `getpass.getuser()` |

Every existing call is byte-identical, because every existing call omits
the parameter. The default is `False` rather than "whoever is running":
production is the answer to almost every status question, and a default
that changed with the caller would make two people reading the same
campaign name get different answers with no indication why.

**Both sides of the `True` row must resolve the same account.**
`ledger_for()` called with no argument uses `getpass.getuser()`
internally, so passing no argument there and calling `getpass.getuser()`
for the condor owner gives one rule with one outcome. Do not substitute
`os.environ['USER']` on one side only: that is how the ledger and the
queue come to disagree about whose campaign is being reported.

### 2.2 Both axes move together

`mine` sets the ledger path *and* the condor query owner.
`_default_clusters_fn()` currently takes no arguments and calls
`condor.query_owner_jobs()`, which defaults to the module constant; it
becomes owner-aware, and `campaign_status` threads the value through.

Splitting these — reading one account's ledger against another's queue —
would manufacture the exact wrong-account zero the write side already had
to fix. There is no use case for the mismatch, so the parameter does not
offer it.

`query_owner_jobs` needs no other change: it constrains on
`Owner=="{owner}"` and the jobsub schedds carry every owner's jobs. Both
acceptance clusters landed on jobsub schedds (`jobsub01`, `jobsub05`), so
`_is_jobsub_schedd`'s filter holds for non-production owners.

## 3. Why a boolean, not an account name

An earlier draft took `owner: str`, which would also have answered "how
is my colleague's campaign doing" inside a conversation. It was rejected
for two reasons.

**The capability already exists.** `submissions --db <path> status` reads
any ledger the caller has permission to read, today, with no new code —
verified 2026-08-09 by reading mu2epro's ledger from an unprivileged
account. A colleague's campaign is a shell command away. The MCP
parameter would have moved that capability, not created it.

**A boolean has no validation surface.** An account name supplied by the
model is interpolated straight into a filesystem path by `ledger_for()`,
so `owner='../../elsewhere'` has to be refused, and a shape check plus its
tests has to be written and then maintained. `mine` is derived from
`getpass.getuser()`; there is no caller-supplied string, so the question
does not arise. Everything in this spec that fixes the actual bug (§2.2,
§4, §5) is identical either way — the account-name version bought one
convenience and charged one obligation.

Widening later is contained: `mine: bool` → `owner: str` touches the same
two functions, and by then there would be evidence about whether anyone
wants cross-user reads in a conversation rather than in a shell.

## 4. What must not happen

**No `ensure_ledger_dir`.** `utils/submissions.resolve_db` mkdirs a
derived ledger path, because for a writer a missing directory is a first
run. A read-only server has no first run: a missing ledger is a finding,
and creating anything from a read tool would falsify the server's central
claim. The resolution here calls `ledger_for()` and nothing else.

## 5. Honest payloads

`campaign_status` already returns the resolved `db_path`. Two additions:

- `list_campaigns` returns `db_path` too. Its silence today is harmless
  only because there is one possible answer; with `mine` there are two.
- The queue block gains `owner`, beside the existing `state` and counts.

A status payload that does not name whose ledger and whose queue produced
it cannot be checked by its reader. Every wrong-account bug in this
subsystem has been a number that was correct for an account nobody asked
about, and the fix each time has been to make the payload say which.

## 6. Error wording

`ledger_ro._connect` raises `catalog_unavailable` with `submission ledger
not found: <path>` when `os.path.exists` is false. With `mine=True` that
is accurate — you can always traverse your own directory, so a false
result means the ledger genuinely is not there, and the existing hint
("the direct-submission subsystem has been run at least once") is the
right advice.

No change is required here. It is noted so a later reader does not
mistake the omission for an oversight: the wording only becomes a guess
if cross-user reads are ever added (§3), and it should be revisited then.

## 7. Testing

Unit, in `test/test_unit.py`:

- The resolution table of §2.1, including that `mine=False` still honors
  `MU2E_SUBMISSION_DB`. That is the regression a hardcoded production
  default would cause and that no other test would catch.
- Ledger account and condor account come from the same resolution — one
  test that patches the account and asserts both move, so a future edit
  cannot change one and leave the other.
- The condor owner is threaded from the parameter, asserted through an
  injected `clusters_fn` rather than by reading the module constant.
- No directory is created for a caller whose ledger does not exist.
- `list_campaigns` returns `db_path`; the queue block carries `owner`.

Live, against the acceptance fixture that already exists:

- `campaign_status(mine=True)` returns campaigns 1 and 2 (`MCPTest001`,
  `MCPTest002`) from `/exp/mu2e/data/users/oksuzian/prodtools/submissions.db`.
- `campaign_status()` with no argument still returns the production
  campaigns, unchanged from today.

## 8. Documentation

- `mcp/README.md` and the CLAUDE.md MCP section: what `mine` does, that
  omitting it means production, and that another account's ledger is
  read with `submissions --db <path> status` rather than through MCP.
- `get_server_info` advertises that the status tools accept `mine`, so a
  client can discover the capability without reading the source.

`EXAMPLES.md` is not touched here: it documents the CLI, and `--mine` and
`--db` are already covered there.

## 9. Non-goals

- **No write capability.** Unchanged.
- **No cross-user reads through MCP.** `submissions --db <path> status`
  covers it today (§3).
- **No cross-ledger aggregation.** One call reads one ledger. Merging
  would have to reconcile campaign ids that collide across ledgers, and
  nobody has asked for it.
- **No `mine` on the discovery or lineage tools.** Already
  identity-neutral (§2).

## 10. Risks

- **The parameter can be forgotten.** A model that omits `mine` gets
  production. That is the safe direction — an under-reported personal
  campaign, never a personal ledger mistaken for production — and §5's
  `db_path` in the payload makes the omission visible after the fact.
- **`mine` is ambiguous under an identity switch.** The read server runs
  as the invoking user and never shells through `ksu`, so "mine" has one
  meaning today. If a future change ever runs it under another account,
  §2.1's single-resolution rule is what keeps the two axes agreeing about
  which account that is.

## 11. Verified (2026-08-09)

Live verification against the fixture the write-path acceptance run left
behind (`MCPTest001`, `MCPTest002` in the personal ledger), run with
`cd mcp && ./.venv/bin/python`. Reproducing this requires
`PYTHONPATH=<repo root>` in the environment first — without it,
`from prodtools_mcp.tools import status` fails with
`ModuleNotFoundError: No module named 'utils'`, since `ledger_ro.py`
imports the top-level `utils` package from the repo root, not from
`mcp/`.

- `list_campaigns(mine=True)` — `db_path` =
  `/exp/mu2e/data/users/oksuzian/prodtools/submissions.db`, `count` = 2,
  both `MCPTest001` and `MCPTest002` present.
- `list_campaigns()` (default) — `db_path` =
  `/exp/mu2e/data/users/mu2epro/prodtools/submissions.db`, `count` = 53.
- `campaign_status(campaign_id=1, mine=True)` — `queue.owner` =
  `oksuzian` (not `mu2epro`). `queue.state` came back `"unknown"`
  (HTCondor schedd query failed/unreachable at verification time), which
  the task brief treats as an acceptable outcome — `owner` is the
  assertion, not the counts.

The two axes (ledger and queue) agree on identity. Full command
transcripts are in
`.superpowers/sdd/2026-08-09-mcp-read-identity/task-4-report.md`.
