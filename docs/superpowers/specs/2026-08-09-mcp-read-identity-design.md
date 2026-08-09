# prodtools MCP read path — identity-parameterized status

**Status:** designed 2026-08-09, not implemented.
**Follows:** `2026-08-08-mcp-write-path-design.md`, whose acceptance run
exposed this gap.

**Goal:** let `campaign_status` and `list_campaigns` report on any
account's submission ledger and grid queue, not only `mu2epro`'s.

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

One new parameter, `owner`, on two tools:

```python
campaign_status(campaign=None, campaign_id=None, include_queue=True,
                include_outputs=True, owner=None)
list_campaigns(state=None, owner=None)
```

`find_datasets`, `dataset_details` and `trace_provenance` are unchanged.
They are already identity-neutral: the owner is a field in the SAM name
pattern, so `mcs.oksuzian.*` already works and no parameter would add
anything.

### 2.1 The default is `None`, not `'mu2epro'`

This is load-bearing. Today `db_path=None` falls through to
`ledger_ro.DEFAULT_DB`, which is
`os.environ.get('MU2E_SUBMISSION_DB', PRODUCTION_DB)`. Defaulting `owner`
to the literal `'mu2epro'` and resolving it through `ledger_for('mu2epro')`
would reach the same file in the common case while silently destroying
the env override — a config knob that works until the day someone relies
on it.

So the resolution is:

| `owner` | ledger | condor owner |
|---|---|---|
| `None` (default) | `ledger_ro.DEFAULT_DB` — unchanged, env honored | `condor.OWNER` |
| `'mu2epro'` | `ledger_for('mu2epro')` == `PRODUCTION_DB` | `'mu2epro'` |
| `'<user>'` | `ledger_for('<user>')` | `'<user>'` |

Every existing call is byte-identical, because every existing call omits
the parameter.

### 2.2 Both axes move together

`owner` sets the ledger path *and* the condor query owner.
`_default_clusters_fn()` currently takes no arguments and calls
`condor.query_owner_jobs()`, which defaults to the module constant; it
becomes owner-aware, and `campaign_status` threads the value through.

Splitting these — letting a caller read one account's ledger against
another's queue — would manufacture the exact wrong-account-zero the
write side already had to fix. There is no use case for the mismatch, so
the parameter does not offer it.

`query_owner_jobs` needs no other change: it constrains on
`Owner=="{owner}"` and the jobsub schedds carry every owner's jobs. Both
acceptance clusters landed on jobsub schedds (`jobsub01`, `jobsub05`), so
`_is_jobsub_schedd`'s filter holds for non-production owners.

## 3. Validation

`owner` is interpolated directly into a filesystem path by `ledger_for()`,
and its value comes from the model. It is therefore validated before use:

```python
_OWNER_RE = re.compile(r'^[a-z_][a-z0-9_-]{0,31}$')
```

A non-matching value raises `ToolError('invalid_argument', ...)` naming
the expected shape. This is the one cost of parameterizing by account
name rather than by a `mine: bool`, and it is not optional: without it,
`owner='../../mu2epro'` is a path traversal, read-only or not.

Validation is shape-only. Whether the account exists, and whether it has
a ledger, are answered by the existing `catalog_unavailable` path — a
shape check that also verified existence would be two failure modes
wearing one error code.

## 4. What must not happen

**No `ensure_ledger_dir`.** `utils/submissions.resolve_db` mkdirs a
derived ledger path, because for a writer a missing directory is a first
run. A read-only server has no first run: a missing ledger is a finding,
and creating anything from a read tool would falsify the server's central
claim. The resolution here calls `ledger_for()` and nothing else.

## 5. Honest payloads

`campaign_status` already returns the resolved `db_path`. Two additions:

- `list_campaigns` returns `db_path` too. Its silence today is harmless
  only because there is one possible answer.
- The queue block gains `owner`, beside the existing `state` and counts.

A status payload that does not name whose ledger and whose queue produced
it cannot be checked by its reader. Every wrong-account bug in this
subsystem has been a number that was correct for an account nobody asked
about, and the fix each time has been to make the payload say which.

## 6. Error wording

`ledger_ro._connect` tests `os.path.exists(db_path)` and, when false,
raises `catalog_unavailable` with `submission ledger not found: <path>`.
For a same-user read that is accurate. For a cross-user read it is a
guess: `os.path.exists` returns False both for an absent file and for one
inside a directory the caller cannot traverse.

Cross-user reads work today — both ledgers are `-rw-r--r--` under
world-executable directories — but the message must stop asserting a
cause it has not established. It becomes "not found, or not readable by
you", with the hint naming both.

## 7. Testing

Unit, in `test/test_unit.py`:

- The resolution table of §2.1, including that `owner=None` still honors
  `MU2E_SUBMISSION_DB`. This is the regression that a literal default
  would cause and that no other test would catch.
- `owner` validation: accepts plausible usernames, rejects `../x`, `/abs`,
  empty, and a 33-character name.
- The condor owner is threaded from the same parameter — asserted through
  an injected `clusters_fn`, not by reading the constant.
- No directory is created for an owner whose ledger does not exist.
- `list_campaigns` returns `db_path`; the queue block carries `owner`.

Live, against the acceptance fixture that already exists:

- `campaign_status(owner='oksuzian')` returns campaigns 1 and 2
  (`MCPTest001`, `MCPTest002`) from the ledger at
  `/exp/mu2e/data/users/oksuzian/prodtools/submissions.db`.
- `campaign_status()` with no `owner` still returns the production
  campaigns and is unchanged from today.

## 8. Documentation

- `mcp/README.md` and the CLAUDE.md MCP section: what `owner` does, and
  that omitting it means production.
- `get_server_info` advertises that status tools accept `owner`, so a
  client can discover the capability without reading the source.

`EXAMPLES.md` is not touched here: it documents the CLI, and the CLI's
`--mine` / `--db` already cover this ground.

## 9. Non-goals

- **No write capability.** Unchanged.
- **No cross-user aggregation.** One call reads one account. A "show me
  everything" view would have to merge ledgers whose campaign ids collide,
  and nobody has asked for it.
- **No `owner` on the discovery or lineage tools.** They are already
  identity-neutral (§2).

## 10. Risks

- **Permissions are conventional, not enforced.** Cross-user reads work
  because personal ledgers happen to be mode 0644 under traversable
  directories. A user with a private home directory yields
  `catalog_unavailable`, which §6 makes legible rather than misleading.
  Nothing here grants access it does not already have.
- **The parameter can be forgotten.** A model that omits `owner` gets
  production. That is the safe direction — an under-reported personal
  campaign, never a personal ledger mistaken for production — and §5's
  `db_path` in the payload makes the omission visible after the fact.
