# Code-tarball entries in `submit_once`, and `run_local`

Date: 2026-09-25
Status: design approved in conversation 2026-09-25; this written spec
awaits review.
Origin: autoresearch's generic studies (Phase C) drive prodtools as an
MCP kit. Every autoresearch stage entry sets `code` and none sets
`simjob_setup`, and a study runs its stages on the grid or locally. The
MCP write server can do neither today.

## Context

`submit_once` (2026-09-19 spec) submits one entry to outstage and leaves
a receipt, and the read-only server's `run_status` reads that receipt.
Code-tarball entries (2026-08-15 spec, implemented) work everywhere
below the MCP layer:

- `json2jobdef` takes exactly one of `simjob_setup` and `code`
  (`json2jobdef.py:412`);
- `jobdef` writes `setup: "Code/setup.sh"` and `code_ref`;
- `submit_entry` gates on `check_code_tarball` and ships the tarball
  with `--tar_file_name dropbox://` (`jobsub_argv.py:294`);
- `runlocal --code` unpacks it once for its jobs.

Two things are missing:

1. **The MCP write server refuses code entries.** `_select_push_params`
   (`mcp/src/prodtools_mcp_write/tools.py:47`) raises when an entry that
   is not g4bl has no `simjob_setup`. The helper exists because
   `json2jobdef` must run inside a Musing: `run_cli`'s setup chain
   sources the entry's `simjob_setup`, and without `mu2e` on PATH
   `json2jobdef` exits (`json2jobdef.py:1156`). A code entry has no
   Musing path to source. Its environment is inside the tarball.
2. **An MCP caller cannot run an entry locally.** `runlocal` exists, but
   it takes a cnf rather than an entry, runs in the foreground, and
   leaves nothing `run_status` can read.

Checked in the code before writing this:

- **A cvmfs Musing's `setup.sh` and a `muse tarball` `Code/setup.sh` are
  the same script.** `Musings/SimJob/MDC2025ax/setup.sh` reads:

  ```bash
  CODE_DIR=$(dirname $(readlink -f $BASH_SOURCE))
  [ -f $CODE_DIR/setup_pre.sh ] && source $CODE_DIR/setup_pre.sh
  muse setup $CODE_DIR -q $MUSE_SETUP_USE_OPTS
  RC=$?
  [ -f $CODE_DIR/setup_post.sh ] && source $CODE_DIR/setup_post.sh
  return $RC
  ```

  A `muse tarball` `Code/setup.sh` differs only in spelling out its
  qualifiers (`-q p101 e29 prof`). So sourcing an unpacked
  `Code/setup.sh` in `run_cli`'s chain is what the chain already does
  for a Musing. `_SELF_TEMPLATE` already unsets `MUSE_WORK_DIR` first.
  **The runner needs no change.**
- **The grid side needs nothing.** `build_jobdesc` carries `code` into
  the entry, `submit_entry` gates on it and adds `--tar_file_name`, and
  `runmu2e` resolves the setup against `$INPUT_TAR_DIR_LOCAL`.
- **A `dir:` inloc entry already goes through `submit_once` unchanged.**
  The autoresearch spike showed this on 2026-09-25, with a dry run of a
  real entry.
- **`runlocal` writes its summary atomically** (`write_summary`: a temp
  file, then a rename), so a missing summary means the driver died.
- **Tarball sizes differ widely.** autoresearch's per-config tarballs
  are 15 MB, or 17 MB unpacked. A full `muse tarball` can be about 1 GB,
  or 3.6 GB unpacked.

Decisions:

- **A code entry's build environment is the tarball's own
  `Code/setup.sh`,** the same script the worker sources. Nothing is
  passed alongside it, such as a FHiCL path: what the tarball's
  `setup_post.sh` sets is what the build sees, as it is on the worker.
- **Tarballs are unpacked into a per-user cache keyed by content.**
  Repeat builds and local runs of one tarball share one unpack.
- **`push_cnf` keeps refusing code entries.** A production cnf built
  against a code tarball needs that tarball on a durable path that
  mu2epro can read (2026-08-15 spec, failure mode 7), which is a
  separate decision.
- **`run_local` is detached.** It returns at once, and `run_status`
  reports on the run.
- **One receipt scheme covers both executors.** A local run and a grid
  run of the same desc+dsconf share a run name, and a name is used once,
  because the output file names derive from it.

## 1. The code cache: `utils/code_cache.py` (new, stdlib only)

```python
def cache_root(user=None):
    """/exp/mu2e/data/users/<user>/prodtools/code, next to runs/."""

def unpacked(tarball, root=None):
    """The directory holding Code/ for this tarball's content,
    unpacking it on first use. Raises ValueError or OSError; never
    prints."""
```

- `tarball` must be an absolute path to a readable file. Anything else
  raises `ValueError`.
- **The key is the sha256 of the bytes, not the file name.** On a hit,
  when `<root>/<sha256>/Code/setup.sh` exists, it returns
  `<root>/<sha256>`.
- **On a miss, the tarball is extracted into
  `<root>/<sha256>.part.<pid>`** with `tarfile` mode `r:bz2`.
  - With no `Code/setup.sh` in it, the part directory is removed and
    `ValueError` says the tarball "has no Code/setup.sh — build it with
    `muse tarball`".
  - Otherwise the part directory is renamed to `<root>/<sha256>`. The
    rename is the commit: a killed unpack leaves a `.part.<pid>`
    directory, never half a tree under the real name.
  - If a concurrent unpack of the same content already renamed its
    directory into place, this one removes its own part directory and
    returns the winner's.
- **It never writes to stdout.** It runs inside the stdio MCP server,
  whose stdout carries the protocol. `runlocal.unpack_code` prints
  progress and calls `sys.exit`, so the server cannot call it.
- **Nothing evicts cache entries.** Deleting an entry's directory frees
  it. That is safe while no local run is using the entry, and
  `run_status` shows which runs are running. At autoresearch's 17 MB per
  config this does not matter; with 3.6 GB trees it will, so
  `mcp/README.md` says where the cache is and how to clear it.

## 2. P1: `submit_once` accepts code entries

`_select_push_params(json_path, desc, dsconf, allow_code=False)` still
returns `(setup_script, tarball_desc)`:

| Entry | `allow_code=False` (`push_cnf`) | `allow_code=True` (`submit_once`, `run_local`) |
|---|---|---|
| `simjob_setup` set | unchanged | unchanged |
| g4bl | unchanged (`None`) | unchanged (`None`) |
| `code` set | `ValueError` | `code_cache.unpacked(entry['code']) + '/Code/setup.sh'` |

The `push_cnf` refusal says that code-tarball entries go through
`submit_once` or `run_local` only, and why: a production cnf needs its
code tarball on a durable path that mu2epro can read. `code_cache`
errors are re-raised as `ValueError` naming the entry's desc and dsconf.

`submit_once` calls the helper with `allow_code=True` and passes the
result to `run_cli` as `simjob_setup`, as it does today for a Musing.
Nothing downstream changes:

- `json2jobdef` runs in the tarball's environment, so the `mu2e` check
  passes and the tarball's FHiCL path is set;
- the cnf records `setup: "Code/setup.sh"` and `code_ref`;
- the grid job receives the tarball.

Failures come back as tool errors that name the cause, before anything
is submitted:

- the tarball path is missing, relative or unreadable;
- the tarball is not bzip2;
- the tarball has no `Code/setup.sh`;
- the tarball's `Code/setup.sh` fails when sourced. `run_cli`'s chain
  reports this as its existing "Musing setup failed".

## 3. P2: `run_local`

### 3a. `json2jobdef --once --local [--parallel N]`

Flag rules:

- `--local` requires `--once`.
- `--local` refuses `--prodtools-dir`: worker code travels only with
  grid jobs, so the flag is refused rather than ignored.
- `--parallel` requires `--local`. It defaults to 4, runlocal's
  `DEFAULT_PARALLEL`, and must be at least 1. It is named after
  runlocal's `-j/--parallel`.

A local run goes through the same `submit_once` function and every one
of its refusals: personal runs only, every `outloc` on outstage, and
`njobs` at most 10000. One entry shape then serves both executors, and
the entry stays the record of what was run. g4bl entries are also
refused, because runlocal runs art jobs only.

The receipt is reserved as `building` and the cnf built in the run
directory, exactly as for a grid run. Then:

1. **Mark the receipt as starting.** Set `state: "starting"`,
   `executor: "local"`, and the entry.
2. **Find the code root.** A code entry gets
   `code_cache.unpacked(config['code'])`, which is a cache hit because
   the MCP tool unpacked it before calling.
3. **Start runlocal.** `Popen` the command
   `[sys.executable, <repo>/utils/runlocal.py, --jobdef <cnf>, --inloc
   <inloc_of(entry)>, --first 0, --num <njobs>, -j N, --workdir
   <run dir>, --json <run dir>/summary.json]`, plus `--code-root <root>`
   for a code entry. It uses `cwd=<run dir>`, `start_new_session=True`,
   `stdin=DEVNULL`, and stdout and stderr both to `<run dir>/runlocal.log`.
   - **Its own session.** The run outlives `json2jobdef`, `run_cli`'s
     shell and the MCP call. `kill <pid>` stops the whole run (see the
     runlocal changes below).
   - **No inherited pipes.** `run_cli` captures stdout and stderr and
     waits for EOF. A child still holding either pipe would block the
     tool for the whole run.
   - **The environment is inherited:** ops plus the entry's setup, a
     Musing or a tarball. Each job sources that setup again with
     `MUSE_WORK_DIR` removed (`runlocal.child_env`), as runlocal always
     does.
4. **Mark the receipt as running.** Record `state: "running"`, `host`
   (`socket.getfqdn()`), `pid`, `started_utc`, `summary`, `log`, `njobs`
   and `parallel`.
5. **Report.** Print the `RECEIPT <path>` line and exit 0.

If `Popen` fails, the receipt becomes `failed`. If the process is killed
between steps 3 and 4, the receipt stays `starting`. The name is then
refused again (`RunExists`), and `reserve`'s message, which today speaks
only of `submitting` and `jobsub_q`, gains the local case: look for a
`runlocal` process whose command line names this run directory.

**runlocal changes.**

- `--code-root` becomes a documented flag: an already-unpacked code
  root (the directory holding `Code/`), as an alternative to `--code`.
  Today the flag is suppressed and used only by child jobs. Giving both
  `--code` and `--code-root` is refused. The driver makes the path
  absolute, because its jobs run in their own directories, and refuses
  one with no `Code/setup.sh`.
- **SIGTERM to the driver ends every running job.** Each job runs in its
  own session so that a timeout can kill its whole process group
  (`kill_job`). A signal to the driver's group therefore never reaches
  the jobs: killing runlocal today leaves its mu2e jobs orphaned and
  still writing. (Found while writing the plan, 2026-09-25; the first
  draft of this spec said `kill -- -<pid>` stops the run, which is
  wrong.) The driver keeps the set of running jobs under a lock that is
  held across each `Popen` and its registration. On SIGTERM it takes the
  lock, ends each job's group with `kill_job`, and exits 143 without
  writing a summary: a missing summary is how a reader tells a stopped
  run from a finished one.

### 3b. The MCP write tool

`run_local(json, desc, dsconf, run_as, parallel=4)`:

- `run_as="self"` only, refused otherwise as in `submit_once`;
- `_select_push_params(..., allow_code=True)`;
- `run_cli` with `bin/json2jobdef --json J --desc D --dsconf C --once
  --local --parallel N`;
- parses the `RECEIPT` line and returns the receipt without its entry,
  as `submit_once` does.

It returns once the cnf is built, plus the first unpack of a new
tarball.

### 3c. `run_status` gains a local branch

A receipt with `executor: "local"` takes the new branch. A receipt with
no `executor` is a grid run, so existing receipts are unchanged.

- `building`, `starting` and `failed` are returned as they are, as the
  grid branch does today.
- `running` is resolved as follows:
  1. **If `summary.json` exists,** its jobs become the same `jobs` and
     `outputs` blocks the grid branch returns:
     - `expected` is njobs, and `ok`, `failed` and `exit_codes` are as
       in the grid branch. A timed-out job has rc 124.
     - `outputs` maps each index with rc 0 to its paths.
     - Both lists are capped at `INDEX_CAP`.
     - The state is `done` when every index is ok and `short`
       otherwise. An index missing from the summary is `unknown`, and so
       is the state.
  2. **If there is no summary and the receipt's host is this host,**
     the state is `running` when `/proc/<pid>/cmdline` names this run's
     summary path. Otherwise the state is `failed`, with the note
     "runlocal (pid P) is gone and wrote no summary; see <log>". The
     check reads the command line rather than calling `kill(pid, 0)`
     because pids are reused, and a kill check needs the same user.
  3. **If there is no summary and the host is another,** the state is
     `unknown`, with a note naming that host.
- **It stays read-only.** The receipt is never rewritten, and the state
  is recomputed on every call.
- **Test seams:** `alive_fn(pid, summary_path)` and `host_fn()`.

## 4. Docs

- `mcp/README.md`: the "try something without touching SAM" walkthrough
  gains `run_local`, and a short note covers code-tarball entries: the
  cache location, and when deleting a cache entry is safe.
- `docs/EXAMPLES_schema.md`: the `--once` section gains `--local`, and
  `EXAMPLES.md` is regenerated from it.

## Testing

**Unit tests** go in `test/test_unit.py`, run as
`env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -m unittest test.test_unit`.
Running the file directly (`python3 test/test_unit.py`) silently skips
the 15 classes defined below its `unittest.main()` call. On 2026-09-25
that was 87 tests, among them every `submit_once` and `run_status` test.
PR 1 moves the call to the end of the file.
The fixtures build small real bzip2 tarballs in the test.

- `code_cache`:
  - a miss unpacks and a hit does not;
  - the same bytes under two names share one directory;
  - a tarball without `Code/setup.sh` raises and leaves nothing under
    the key;
  - a lost rename race returns the winner's directory;
  - a relative path is refused;
  - nothing is written to stdout.
- `_select_push_params`:
  - a code entry is refused without `allow_code`, and the message names
    push_cnf;
  - with `allow_code` it returns `<cache>/<sha256>/Code/setup.sh`;
  - `simjob_setup` and g4bl entries are unchanged.
- The `submit_once` tool: a code entry reaches `run_cli` with the cached
  setup script.
- `json2jobdef --once --local`:
  - the flag refusals: `--local` without `--once`, `--parallel` without
    `--local`, `--local` with `--prodtools-dir`, `--parallel 0`, and a
    g4bl entry;
  - the receipt goes `building` → `starting` → `running`, and records
    host and pid;
  - a failed `Popen` leaves the receipt `failed`;
  - the runlocal argv carries `--code-root` for code entries only;
  - the `Popen` arguments: `start_new_session`, `stdin=DEVNULL`, and
    stdout to the log file, never the parent's stdout.
- `runlocal`:
  - `--code-root` works at driver level, with no unpack, and a relative
    one is made absolute;
  - `--code` together with `--code-root` is refused, and so is a code
    root with no `Code/setup.sh`;
  - SIGTERM to the driver ends a real job running in its own session,
    and exits 143.
- The `run_local` tool: mu2epro is refused, and the argv and the
  `RECEIPT` parsing are covered.
- `run_status` in the local branch:
  - `done`;
  - `short` with exit codes, including a timeout;
  - `running`;
  - `failed` when the process is gone and there is no summary;
  - `unknown` from another host;
  - `unknown` when indices are missing from the summary;
  - `starting` returned as it is.
- Grid receipts: the existing `run_status` tests pass untouched.

**Live tests** run as self on mu2esrv01. The first is a 1-job, 10-event
copy of autoresearch's gridphaseA01 mubeam entry (a 15 MB code tarball,
outloc outstage):

1. `run_local` returns, and `run_status` reads `running` and then
   `done`. The `.art` file is at the path it names.
2. A second, longer run is started and stopped with `kill <pid>`.
   `run_status` reads `failed`, and no `mu2e` process from that run is
   left.
3. The same entry goes through `submit_once`'s environment and
   `json2jobdef` without `--once`, so the cnf is built and nothing is
   submitted. That proves the P1 build path. A 1-job grid submission
   would prove the rest, and needs your go-ahead.

## Delivery

Two PRs to Mu2e/prodtools from oksuzian/prodtools, each squash-merged:

- **PR 1 (P1):** `code_cache`, `_select_push_params`, `submit_once`,
  their tests, and the README note.
- **PR 2 (P2), after PR 1:** `--local`, runlocal's `--code-root`, the
  `run_local` tool, the `run_status` local branch, their tests, and the
  docs.

## Out of scope

- A cancel tool for local runs. `kill <pid>` is documented instead.
- Cache eviction.
- Code entries in `push_cnf` or in campaigns.
- An event-count override on `run_local`. The entry is the record of
  what ran, so a smaller run needs a smaller copy of the entry.
- Local runs on another host or through a batch system.
- Local g4bl runs.
