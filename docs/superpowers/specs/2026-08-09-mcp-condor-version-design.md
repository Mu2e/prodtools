# prodtools MCP queue block — HTCondor client version drift

**Status:** designed 2026-08-09; root cause confirmed live against the
production pool. Not yet implemented.
**Follows:** `2026-07-26-prodtools-mcp-design.md`, which introduced the
in-process HTCondor query and its `htcondor==23.0.*` pin.

**Goal:** restore the `campaign_status` queue block, which has been
returning `state: "unknown"` for every campaign, and make the next
occurrence of this failure name itself instead of requiring a diagnosis
session.

**Scope:** `mcp/src/prodtools_mcp/condor.py`, the queue-block branch of
`mcp/src/prodtools_mcp/tools/status.py`, `get_server_info` in
`server.py`, the venv build (`mcp/pyproject.toml`,
`mcp/scripts/install.sh`, `mcp/scripts/start_mcp.sh`), their tests, and
the docs that assert the stale pin. No tool gains or loses a parameter.
The read-only server stays read-only.

---

## 1. The problem

`campaign_status` reports `queue: {state: "unknown", reason: "HTCondor
queue query failed, timed out, or could not reach every schedd"}` for
every campaign, while `jobsub_q` on the same node returns the jobs
without complaint.

The root cause is not the schedds. **The venv pins
`htcondor==23.0.*`; the pool and the node's own client RPM are
`25.0.12`.** Condor 23's SCITOKENS handshake is rejected by the upgraded
collector, so `_locate_jobsub_schedds()` raises before any schedd is
contacted, `query_owner_jobs()` returns `None`, and the queue block
serializes `unknown`.

Under `_condor_TOOL_DEBUG=D_SECURITY:2` the 23.0.28 client shows:

```
SSL Auth: Server has rejected our token!
AUTHENTICATE: method 4096 (SCITOKENS) failed.
AUTHENTICATE: no available authentication methods succeeded!
SECMAN: required authentication with collector at <131.225.152.24:9618>
        failed, so aborting command QUERY_SCHEDD_ADS.
```

Evidence that isolates the version as the cause:

| test | result |
|---|---|
| `23.0.28` wheel → collector | `HTCondorIOError: Failed communication with collector` |
| `23.0.28` wheel, **freshly minted** bearer token | still fails |
| `25.0.12` wheel, venv python3.10, **expired** token | 8 schedds, 6 jobsub, 1446 ads for `mu2epro` on jobsub04 |
| system RPM bindings (`htcondor2`, 25.0.12, py3.9) | 8 schedds |

The middle row is the one that matters. An expired bearer token at the
WLCG discovery path (`/run/user/$UID/bt_u$UID`) is a real and separate
condition — `jobsub_q` sidesteps it by keeping its own token at
`/tmp/bt_token_mu2e_<role>_$UID` and refreshing it — but refreshing the
discovery token does **not** fix the 23.0.28 client, and the 25.0.12
client succeeds with the discovery token expired. Token freshness is not
the variable; client version is.

### 1.1 Why it was expensive to find

Two independent defects made a one-line cause read as a distributed
systems problem:

- **`query_owner_jobs()` discards the exception.** Every failure path
  funnels into a bare `return None`. The `HTCondorIOError`, the
  authentication method, the collector address — all thrown away.
- **`queue_block()` then prints a fixed string that names schedds.**
  The failure happened at the collector, during `QUERY_SCHEDD_ADS`,
  before a single schedd was contacted. The reason text sent the
  investigation to the wrong layer.

The pin itself was not wrong when written — `htcondor==23.0.28` did
match the pool, verified 2026-07-26. It went stale, and **nothing in the
system was watching for that**, even though the answer sits in
`condor_version` on the same machine.

## 2. Approach

Four changes. The port is the smallest of them; the rest exist so this
class of failure is caught by the build or announces itself at runtime.

The alternative of dropping the wheel and shelling out to the system
py3.9 RPM bindings was considered and rejected: it guarantees the client
tracks the pool, but pays a subprocess and a JSON boundary on every
status call forever, and still would not survive an API break — only a
version drift.

### 2.1 Port to the v2 bindings

The 25.x wheel ships `htcondor2`, `classad2` and `classad3`. There is no
v1 `htcondor` module in it, so this is an API port, not a version-number
edit. Three call sites in `condor.py`:

```python
import htcondor2 as htcondor              # was: import htcondor
coll.locateAll(htcondor.DaemonType.Schedd)  # was: DaemonTypes.Schedd
htcondor.Schedd(schedd_ad).query(constraint, projection=_PROJECTION)
```

`Schedd.query(constraint, projection=...)` is unchanged between v1 and
v2, verified live. The `Collector()` no-argument form still auto-resolves
`COLLECTOR_HOST` from `/etc/condor/config.d`.

Both imports stay **inside** their functions. That laziness is
load-bearing: it is what lets the unit suite exercise this module's
query logic on plain python3.9 where no wheel is installed, and it must
not be hoisted to module level as part of this port.

### 2.2 Derive the pin from the machine

`condor_version` and the `condor` RPM both report the node's client
version, with no network call and no token. The node's client RPM is
upgraded by the same admins, in lockstep with the pool, and is the
binding `jobsub_lite` itself uses — so it is the best locally available
proxy for what the pool will accept.

- `mcp/pyproject.toml` declares a floor, `htcondor>=23`, and states in a
  comment that the exact version is chosen at install time by
  `install.sh`, which is the authority. The literal pin is removed: a
  literal is what went stale.
- `mcp/scripts/install.sh` reads the version from **`/usr/bin/condor_version`**
  by absolute path — not via `PATH`, which `muse setup ops` rewrites —
  extracts the `major.minor` series, and runs
  `env -u PYTHONPATH ./.venv/bin/pip install "htcondor==${SERIES}.*"`
  **before** `pip install -e .`. Two details are load-bearing:
  ordering, because with a satisfying version already present the
  editable install cannot resolve the floor to something newer; and
  `env -u PYTHONPATH`, for the reason the existing install.sh comment
  gives — pip that can see the ops PYTHONPATH marks deps satisfied from
  the spack env and leaves a venv that is not self-contained, which is
  exactly what part 1 of `--check` fails on.

If `/usr/bin/condor_version` is missing or its output unparseable,
`install.sh` **fails loudly**. There is deliberately no hardcoded
fallback: a wrong-but-plausible default is precisely how this failed
silently the first time.

### 2.3 Gate the health check on agreement

`mcp/scripts/start_mcp.sh --check` gains a part that compares the
installed wheel's `major.minor` against `condor_version` and exits
non-zero on mismatch, naming both versions. This is the command already
run after any environment change, and it catches both a stale venv and a
drifted `pyproject.toml`.

### 2.4 Make a live failure name itself

`query_owner_jobs()` returns `(clusters, reason)`:

- `clusters` — the dict on success, `None` on any untrusted result.
- `reason` — `None` on success; on failure a short string naming what
  actually happened, including the exception type and text, and the
  client and local-condor versions when they disagree.

**The fail-closed contract is unchanged and remains load-bearing.**
`clusters is None` stays the only signal any caller may branch on, and
`queue_block()` still omits the count keys entirely on `unknown` so
there is no zero to misread. The `reason` is diagnostic text for a human
reader and must never become control flow — the docstring says so
explicitly, because the next reader will be tempted to test it.

`queue_block(cluster_ids, clusters, owner, reason=None)` substitutes the
supplied reason for today's fixed string, falling back to the current
wording only when a caller passes none.

`get_server_info()` reports a `condor` block: the client version, the
local `condor_version`, and whether the two series match. The local
version comes from a subprocess with `stdin=subprocess.DEVNULL` — the
trap that deadlocked the write server's `pushOutput` — and a short
timeout, degrading to `None` with a reason rather than raising.

### 2.5 Corrections swept up

- `condor.py` says "only ~5 are the jobsub_lite schedds". There are 6.
- `condor.py`, `pyproject.toml`, `server.py`'s `INSTRUCTIONS`, and
  `wiki/pages/prodtools-mcp-server.md` all assert the 23.0.x pin matches
  the pool. All four are now false and are corrected to describe the
  derived pin and the v2 bindings.

## 3. Testing

The unit suite runs on plain python3.9 with no htcondor wheel present.
That constraint is preserved by the lazy imports and must be re-verified,
not assumed.

- **Existing:** the fake at `test/test_unit.py:7977` patches
  `sys.modules['htcondor']` → becomes `htcondor2`, and its fake
  `DaemonTypes` → `DaemonType`.
- **Reason propagation, discovery failure:** a `schedds_fn` that raises
  yields `(None, reason)` with the exception text in the reason.
- **Reason propagation, per-schedd failure:** one failing `query_fn`
  yields `(None, reason)` — the whole result, not a partial one.
- **Reason propagation, timeout:** yields `(None, reason)` naming the
  timeout.
- **Success:** yields `(clusters, None)`.
- **`queue_block` carries the reason** into the `unknown` block, and
  that block still has **no** `running`/`idle`/`held` keys.
- **`queue_block` ignores the reason on success** — a supplied reason
  must not appear in a `known` block.

## 4. Acceptance

1. `bash mcp/scripts/install.sh` completes, having installed the series
   matching `condor_version` (today: `25.0.*`).
2. `bash mcp/scripts/start_mcp.sh --check` passes, including the new
   version-agreement part.
3. Full suite green: `python3 -u test/test_unit.py`.
4. **Live:** `campaign_status` for campaigns 54, 55 and 56 returns
   `queue.state == "known"` with counts, not `unknown`. At design time
   jobsub04 alone held 1446 `mu2epro` ads, so a zero total would itself
   be a failure.
5. **Negative, by hand:** with the venv forced to `htcondor==23.0.*`,
   `--check` fails naming both versions, and a `campaign_status` call
   returns `unknown` with a reason naming the authentication failure —
   not the schedds.

## 5. Known limit

The derived pin follows the node's client RPM, which is a proxy for the
pool, not proof of it. It would have caught this incident on the day it
happened. If the node ever lags the pool, §2.2 pins to the wrong thing
and §2.3–2.4 are what surface it — as a named error rather than a silent
`unknown`. That residual is accepted; closing it would require querying
the collector for its version, which is the very call that fails when
the versions disagree.
