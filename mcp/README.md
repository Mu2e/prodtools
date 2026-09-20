# prodtools MCP servers

Ask an AI assistant about Mu2e production state in plain language —
"how is MDC2025au doing?", "what datasets came out of it?" — instead of
remembering which CLI to run.

## Quick start

### 1. Connect

**Someone already runs one for the collaboration.** Nothing to install:

    claude mcp add --transport http prodtools http://<host>:8008/mcp

Other MCP clients take the same thing as config:

```json
{
  "mcpServers": {
    "prodtools": { "type": "http", "url": "http://<host>:8008/mcp" }
  }
}
```

**Or run your own**, on a Fermilab node (mu2egpvm, with CVMFS):

```bash
cd /exp/mu2e/app/users/$USER
git clone https://github.com/Mu2e/prodtools
cd prodtools
bash mcp/scripts/install.sh            # once, ~1 minute
bash mcp/scripts/start_mcp.sh --check  # should print OK three times
```

Every path below assumes that location,
`/exp/mu2e/app/users/<user>/prodtools`, with `<user>` your login. Any
other place works as long as you write the full path: not `~`, and not a
relative one, because the client starts the script from its own
directory. Inside a shell `$USER` does the substitution for you; in a
JSON file or a tool call you type the login out.

Your client then starts it on demand — `.mcp.json` in the clone already
does this for Claude Code; for another client:

```json
{
  "mcpServers": {
    "prodtools": { "command": "/exp/mu2e/app/users/<user>/prodtools/mcp/scripts/start_mcp.sh" }
  }
}
```

Your own server runs as you, so it reads what your credentials can read.
A shared one runs as its host account.

### 2. Ask it things

| Question | Tool it uses |
| --- | --- |
| "How is MDC2025au doing?" | `campaign_status(campaign="MDC2025au")` |
| "What is running right now?" | `list_campaigns(state="active")` |
| "What datasets does MDC2025au have?" | `find_datasets(campaign="MDC2025au")` |
| "How many files and events in this dataset?" | `dataset_details(dataset="dig.mu2e....art")` |
| "Where are its files on /pnfs?" | `dataset_files(dataset=..., location="tape")` |
| "Does SAM know this file?" | `locate_file(name="cnf.mu2e....0.tar")` |
| "What was this file made from?" | `trace_provenance(name=..., direction="up")` |

Two things to know when you read the answers:

- **Production is the default.** For a campaign you ran yourself, add
  "for user <login>" so the tool passes `user="<login>"` — it switches
  both the ledger and the grid queue to that account. Without it you get
  production's, and an empty result looks exactly like "no campaigns".
- **`state: "unknown"` is not zero.** It means the query failed. The
  campaign may well still be running, so never start a recovery on one.

### 3. Nothing it can break

Every tool here is read-only: no job submission, no SAM definition
create or delete, no ledger change. Submitting is a separate server
(`prodtools-write`) that is not reachable over HTTP at all.

### 4. Submit your own jobs

This needs your own install (the "run your own" route above): the same
`install.sh` sets up a second server, `prodtools-write`, and the same
`.mcp.json` registers it. With `run_as="self"` everything lands under
your own name — your scratch area, files called `*.<user>.*` in
SAM, your own ledger at
`/exp/mu2e/data/users/<user>/prodtools/submissions.db`. It cannot
touch production.

Before the first one, check that

- `getToken` works in your shell;
- `/exp/mu2e/data/users/<user>/` exists and you can write to it
  (the `prodtools/` folder inside is created for you, its parent is not);
- you are a registered Mu2e SAM user — declaring a file fails otherwise.

**A first job that needs no input data and no Musing** is a three-job
G4beamline smoke test, under ten minutes end to end:

```bash
cd /exp/mu2e/app/users/$USER
git clone https://github.com/Mu2e/G4BeamlineScripts
cp prodtools/data/g4bl/g4bl.json my_g4bl.json
# edit my_g4bl.json:
#   "g4bl_dir" -> /exp/mu2e/app/users/<user>/G4BeamlineScripts
#   "dsconf"   -> a name you have never used, e.g. MyTest001
```

Then ask the assistant to push that entry and submit it as yourself. It
makes two calls:

    push_cnf(json="/exp/mu2e/app/users/<user>/my_g4bl.json", desc="G4blSmoke",
             dsconf="MyTest001", slice_size=3, run_as="self")
    run_submissions(run_as="self", campaign_id=<the id push_cnf returned>)

`push_cnf` builds the job package, registers it in SAM and creates the
campaign; `run_submissions` sends the jobs to the grid. Watch them with
the read-only server:

    campaign_status(campaign_id=<id>, mine=true)

The queue block goes idle, then running, then empty. **Nothing advances
by itself** — there is no cron. Once the queue is empty, tick once more,
this time with no campaign id:

    run_submissions(run_as="self")

That pass checks every job's output against SAM, closes the rows that
are complete and resubmits the ones that are not. It has to be the bare
form: a campaign whose jobs were all submitted is already `complete`,
and a tick scoped to it is refused because it would do nothing. The
files are then in the dataset `nts.<user>.G4blSmoke.MyTest001.root`;
`dataset_files(dataset=..., location="scratch")` lists them.

**A first art job** goes the same way, with nothing to edit.
`data/examples/ceendpoint.json` makes a small conversion-electron sample
(`CeEndpoint` primaries, resampled from the stopped-muon sample) with the
newest MDC2025 SimJob release — three jobs of 500 generated events,
ten to fifteen minutes on the grid, of which a bit over half pass the primary
filter (848 of 1500 in the reference run):

    push_cnf(json="/exp/mu2e/app/users/<user>/prodtools/data/examples/ceendpoint.json",
             desc="CeEndpoint", dsconf="MDC2025ax", slice_size=3,
             run_as="self")
    run_submissions(run_as="self", campaign_id=<id>)
    campaign_status(campaign_id=<id>, mine=true)     # until the queue is empty
    run_submissions(run_as="self")                   # verify and recover

The result is `dts.<user>.CeEndpoint.MDC2025ax.art` on scratch.
The entry leaves `owner` out, so it defaults to whoever runs it. For a
second attempt change `dsconf` but keep its `MDC2025ax` prefix: it names
the release, and `simjob_setup` has to agree with it. To move to a newer
release, take the last line of
`ls /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/ | grep '^MDC2025'`
and change both. For more statistics raise `njobs` rather than
`events`: jobs run in parallel, and a long job is a job more likely to
be lost.

A production-sized request is a different thing: the entry
`CeEndpoint` / `MDC2025ax` in `data/mdc2025/primary_muon.json` (2000 jobs
of 5000 events, outputs to persistent disk, owned by `mu2e`). That one
is submitted by the production team with `run_as="mu2epro"`; ask them
rather than running it yourself.

**Try something without touching SAM.** For a test or a study whose
output nobody else should find, send the outputs to *outstage*: a
directory on scratch, nothing declared. Copy an entry, change its
`outloc` to `{"*.art": "outstage"}`, and submit it once:

    submit_once(json="/exp/mu2e/app/users/<user>/my_ceendpoint.json",
                desc="CeEndpoint", dsconf="MDC2025ax_try1", run_as="self")
    run_status(name="cnf.<user>.CeEndpoint.MDC2025ax_try1.0", user="<user>")

`submit_once` builds the job package locally, sends every job in one go
and returns a receipt: the run's `name`, its `jobid`, and `outstage`.
`run_status` says `running`, then `done` — or `short`, with the failed
job numbers and their exit codes — and lists each finished job's files
as `<outstage>/<cluster>/<job>/<file>`. A job can only exit 0 after its
copy landed, so exit codes are the whole answer; `unknown` means the
grid could not be asked, never that something failed.

What you give up, all of it on purpose: no recovery of failed jobs (make
a new run under a new `dsconf`), nothing findable through SAM, no
campaign and no ticks, at most 10000 jobs, never as production. The
files are on scratch and the grid forgets exit codes after about two
weeks, so copy what you want to keep. This is a way to look at
something, not a lighter way to make a dataset.

Five things that bite:

- **`json` must be an absolute path.**
- **A `desc` + `dsconf` pair is used once.** It names the job package in
  SAM, and a SAM name is never reused — not even after a failed attempt.
  Pick a new `dsconf`.
- **The jobs run the CVMFS release, not your clone.** Whatever
  `readlink /cvmfs/mu2e.opensciencegrid.org/bin/prodtools/current` names
  is the worker code, and it has to be **v3.3.4 or newer**: v3.3.3 and
  older fail on the grid under Python 3.12 (at `import samweb_client`
  up to v3.3.2, and at `import gfal2` for any job with input files up to
  v3.3.3). To run worker code you changed yourself, add
  `prodtools_dir="/exp/mu2e/app/users/<user>/prodtools"` to `push_cnf`:
  it ships that checkout's `bin/` and `utils/` with the jobs. Self only.
- **A failed campaign keeps coming back.** The verify-and-recover pass
  covers every open row in your ledger, not only the campaign you name,
  so jobs that failed yesterday are resubmitted next to today's new
  campaign — with the entry they were created with, which fails the
  same way. Close a campaign you have given up on, from a shell with
  the prodtools environment:
  `submissions --mine cancel <id> --close-rows --note "why"`.
- **Never interrupt `run_submissions`**, and never wrap the CLI it runs
  (`submissions run`) in `timeout`. A kill between "the grid accepted
  the jobs" and "the ledger wrote them down" leaves jobs nothing tracks,
  and the next tick submits the same work again.

Art jobs go the same way with a different entry file; `EXAMPLES.md` at
the repo root has the entry formats. `run_as="mu2epro"` is production:
it needs `ksu` rights, `confirm=true` and a confirmation prompt, and is
described under `prodtools-write` below.

---

Two servers live under `mcp/`, registered in `.mcp.json` at the repo
root and enabled in `.claude/settings.json`.

## `prodtools` (read-only)

Exposes campaign status and dataset discovery as typed tools:
`campaign_status`, `list_campaigns`, `find_datasets`, `dataset_details`,
`locate_file`, `dataset_files`, `trace_provenance`, `run_status`,
`get_server_info`.
It performs **NO writes** — it cannot submit jobs, create or delete SAM
definitions, or modify the submission ledger. That guarantee is why its
tools can be called without deliberation; do not weaken it.

Setup: `bash mcp/scripts/install.sh`.
Health check: `bash mcp/scripts/start_mcp.sh --check`.

### Serve it to other people

    bash mcp/scripts/start_mcp.sh --transport streamable-http \
        --host 0.0.0.0 --port 8008 [--allowed-host <fqdn>:8008]

`--host` defaults to `127.0.0.1`, so nothing reaches the network until
you say so, and `--allowed-host` (repeatable) turns on the SDK's
DNS-rebinding check — left out, that check is off, as on the other
central Mu2e servers. `mcp/deploy/prodtools-mcp.service` is a
`systemd --user` unit for a permanent instance; read its header first,
because a shared server queries SAM and HTCondor as ITS OWN account. The
HTCondor pool wants a bearer token, which lasts about three hours, and
how the hosting account gets and renews one is left open there. A
missing or expired token shows up as `state: "unknown"`, never as zero.

Only the read-only server is servable this way. `prodtools-write` stays
stdio: it submits as mu2epro behind `ksu`, `confirm=true` and a
PreToolUse hook, none of which survives being reached over a port.

## `prodtools-write`

Exposes submission: `push_cnf`, `run_submissions`, `submit_once`, and
publishing: `push_file`.

A production campaign takes two calls: `push_cnf(..., slice_size=N)`
builds the cnf, registers it in SAM and creates the campaign, returning
a `campaign_id` for `run_submissions`. That call mirrors `json2jobdef
--prod --enqueue`, which is now the only way json2jobdef runs under
`--prod`.

`push_cnf` identifies the campaign it created by desc+dsconf against a
snapshot of the ledger taken before the CLI ran. If nothing new appears
it RAISES rather than returning a pre-existing campaign — handing back
the wrong id would point `run_submissions` at an unrelated production
campaign.

`push_file(path, location, parents, run_as, confirm=False)` publishes one
already-built file to SAM with its parents through the same `pushOutput`
call a grid job makes (`bin/push_file`). The basename is the SAM name: a
six-field Mu2e file name owned by the identity (`mu2e` for mu2epro).
`location` is tape/disk/scratch. A name already in SAM is refused.

`push_cnf(..., prodtools_dir=...)` forwards `--prodtools-dir` so a
checkout can be run before its release lands on cvmfs; it is refused
for `run_as="mu2epro"` — stricter than json2jobdef's own rule, since
this write surface is the wrong place to accept a production prodtools
override at all.

Every tool takes a required `run_as`:

- `run_as="self"` needs no privilege and writes only your own scratch,
  datasets and ledger (`/exp/mu2e/data/users/$USER/prodtools/`). No
  confirmation and no prompt.
- `run_as="mu2epro"` registers artifacts in production SAM and submits
  production grid jobs. It is refused in-tool unless `confirm=true`
  (`runner.require_confirmed`), and a `PreToolUse` hook
  (`.claude/hooks/mcp-write-guard.sh`, matcher
  `mcp__prodtools-write__.*`) additionally prompts for confirmation.
  Both gates are independent and deliberate: the hook covers the whole
  tool namespace so a future write tool cannot silently escape it, and
  the in-tool refusal survives a hook left un-armed by a settings
  reload.

`confirm=true` is a **model-facing** gate — the model supplies it to
itself — so the hook is the only *human*-in-the-loop checkpoint on a
`run_as="mu2epro"` call. It is written to fail CLOSED: only a
positively parsed `run_as=="self"` passes silently; `run_as=="mu2epro"`,
a missing `run_as`, malformed hook input, an unrecognised value, or a
failing/missing `jq` binary all produce a prompt.

**A settings-hooks edit is not live in an already-running session.**
Registering a new `PreToolUse` matcher in `.claude/settings.json` (as
this one is) requires a `/hooks` reload — a session started before the
edit will call `prodtools-write` tools with the hook un-armed even
though `CLAUDE.md` documents the gate as present. Run `/hooks` (or
start a fresh session) after any change here before relying on the
prompt.

Health check: `bash mcp/scripts/start_write_mcp.sh --check`.

Both launchers share environment setup via `mcp/scripts/_mcp_env.sh`.

## `submissions status` and `--mine`

The `submissions status` verb (see `utils/submissions.py`) reads the
**production ledger by default** — the same ledger the direct-submission
cron uses — *only when the `MU2E_SUBMISSION_DB` env var is unset*; if
it is set, that path wins over the production default (see
`resolve_db`/`build_parser` in `utils/submissions.py`). Pass `--mine` to
read your own ledger
(`/exp/mu2e/data/users/$USER/prodtools/submissions.db`) instead, e.g.
after a `run_as="self"` campaign run through `prodtools-write`. Plain
`submissions status` will not show a self-run campaign; `submissions
status --mine` will.

The MCP status tools take the same idea as two parameters.
`campaign_status` and `list_campaigns` accept `user` and `mine`, and with
neither they read production's ledger and mu2epro's queue, exactly as
before.

`user="<login>"` reads `/exp/mu2e/data/users/<login>/prodtools/
submissions.db` and counts that account's grid queue. Personal ledgers
are world-readable, so this needs no privilege and works under either
transport.

`mine=true` is the same thing derived from the process account. It is
meaningful only over stdio, where the process IS you. A server started
with `--transport streamable-http` refuses it with an
`invalid_argument` naming `user` instead, because there the process
account is the host: a bare `mine` would hand every reader the host's
ledger, and an empty answer from the wrong ledger is indistinguishable
from "no campaigns".

Both axes move together by construction — a call cannot read one
account's ledger against another's queue. Every reply names what it read:
`db_path` at the top level, and `owner` inside each `queue` block.

A ledger somewhere other than `/exp/mu2e/data/users/<login>/prodtools/`
is still not reachable through MCP; `user` is validated as a UNIX login,
not a path. Use the CLI for those:

    bash bin/submissions --db /exp/mu2e/data/users/<them>/prodtools/submissions.db status
