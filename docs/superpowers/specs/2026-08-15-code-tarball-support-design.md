# Custom code tarball support (`--code`) — design

**Status:** approved 2026-08-15, not yet implemented.

## Goal

Let a prodtools job run against an Offline build that is not on
/cvmfs — a local Muse build, tarred with `muse tarball` — both locally
via `bin/runlocal` and on the grid via the direct submission backend.

This is the prodtools equivalent of `mu2eprodsys --code` in mu2egrid.

## Why this is not already possible

`simjob_setup` is a free-form path. Nothing in `json2jobdef` or
`utils/jobdef.py` requires it to be under /cvmfs; the string is copied
verbatim into jobpars `setup`, and `runmu2e.build_mu2e_cmd` does
`source {setup} && mu2e -c {fcl}` (`utils/runmu2e.py:288`).

So **local runs against a custom build already work today**: point
`simjob_setup` at a sourceable script for your build and `bin/runlocal`
uses it. The only friction is that a Muse work directory contains no
`setup.sh` — Muse generates one only inside `muse tarball` output
(`museTarball.sh:217-228`), and it is four lines:

```bash
CODE_DIR=$(dirname $(readlink -f $BASH_SOURCE))
[ -f $CODE_DIR/setup_pre.sh ] && source $CODE_DIR/setup_pre.sh
muse setup $CODE_DIR -q <opts>
```

**The grid is where it genuinely fails**: `/exp` is not mounted on
worker nodes, so a path-based `simjob_setup` that works locally cannot
work there. The build must travel with the job.

`utils/jobdef.py` already parses a `--code TARBALL` flag
(`jobdef.py:775`), but it is dead surface: `_build_jobpars_json` is
always called with `code=""` (`jobdef.py:682`), and `setup` is taken
from `config['simjob_setup']`, which is `None` in that path
(`jobdef.py:220-221`). A cnf built with `--code` today carries
`{"code": "", "setup": null}` and cannot run. `config['code']` is used
only to print an equivalent `mu2ejobdef` command string
(`jobdef.py:643-644`). `jobquery.codesize()` returns a hardcoded 0 and
`jobquery.extract_code()` extracts any tar member ending in `.tar`,
which is wrong under every delivery model considered here.

## Upstream precedent

mu2egrid ships **two** different answers, split by tool.

`mu2ejobdef --code` (mu2ejobtools v2_03_00) **embeds** the tarball
inside the cnf as member `code.tar`, sets jobpars
`setup: "Code/setup.sh"` (`mu2ejobdef:451`, `mu2ejobdef:781`), and
requires the tarball to be bzip2-compressed and to contain
`Code/setup.sh` (`mu2ejobdef:808-828`). The worker then runs
`mu2ejobquery --extract-code $jobdef` and
`source $(mu2ejobquery --setup $jobdef)`
(`mu2egrid/bin/impl/mu2ejobsub.sh:155-156`).

`mu2eprodsys --code` uses a **sidecar**: the tarball is shipped by
jobsub, not embedded.

```perl
# mu2eprodsys:336-338
$ENV{'MU2EGRID_USERSETUP'} = 'Code/setup.sh'; # relative path per the tar file convention
$ENV{'MU2EGRID_CODE'} = mu2egrid::find_file($opt{'code'});

# mu2eprodsys:474-475
push @args, ('--tar_file_name', 'dropbox://' . $ENV{MU2EGRID_CODE}) if $ENV{MU2EGRID_CODE};

# impl/mu2eprodsys.sh:275-277  (worker)
if [ -n "$MU2EGRID_CODE" ]; then
    MU2EGRID_USERSETUP="${INPUT_TAR_DIR_LOCAL}/${MU2EGRID_USERSETUP}";
```

`muse tarball` emits `Code.tar.bz2` with everything under `Code/`,
including `Code/setup.sh` — exactly the layout both tools expect.

## Decision: sidecar delivery (the mu2eprodsys model)

We take the sidecar transport, verbatim, and reject embedding.

Embedding would make the cnf self-contained, which is attractive. It is
not affordable here. The user's build tree is 3.6 GB, so
`Code.tar.bz2` is roughly 1 GB. A prodtools cnf is a SAM-registered
artifact pushed to /pnfs under a name that can never be reused, and it
is shipped to every job via `-f dropbox://`. A ~1 GB cnf multiplies
all three costs. The one thing embedding buys — interoperability with
`mu2ejobsub` and `mu2ejobquery --extract-code` — is worth nothing,
because that backend was retired from prodtools.

`mu2ejobdef` cnfs are per-job-list throwaways; ours are not. That
difference in cnf economics, not a difference in taste, is why we take
mu2eprodsys's answer rather than mu2ejobdef's.

Sidecar cost was verified rather than assumed. `jobsub_submit --help`
on this pool:

> `TAR_FILE will be copied with RCDS/cvmfs (or /pnfs), transferred to
> the job and unpacked there. The unpacked contents of TAR_FILE will be
> available inside the directory $INPUT_TAR_DIR_LOCAL.`
>
> `--use-cvmfs-dropbox   use cvmfs for dropbox (default is cvmfs)`

RCDS is the default: the tarball is published once, deduplicated by
content, and mounted on the worker. There is no per-job copy.
mu2eprodsys's own documentation still warns that the worker needs disk
for "a copy of the original tarball plus its extracted content"
(`mu2eprodsys:79-85`); that is stale pre-RCDS wording.

## Contract

The reference splits in two, because the cnf must stay small and the
worker must still learn what to source.

**In the cnf (`jobpars.json`):** `setup` holds the relative string
`"Code/setup.sh"`. Absolute means /cvmfs; relative means code mode.
That distinction is the entire mechanism — no separate flag encodes it.
The upstream `code` key stays `""`, which is truthful: nothing is
embedded.

**In the entry** (JSON build config, projected into the campaign's
`entry_json` snapshot): a new key `code`, the absolute path to
`Code.tar.bz2`. The path lives here and not in the cnf because a
tarball can move, and because `build_jobsub_argv` already receives the
entry. Snapshotting means later slices and recoveries reuse the same
tarball without re-deriving it.

**Exactly one of `simjob_setup` or `code`** per entry — the same rule
`mu2ejobdef` enforces.

### Divergence from mu2eprodsys, and why

mu2eprodsys carries the setup path in the `MU2EGRID_USERSETUP`
environment variable. It can, because its payload is an fcl list: there
is no jobpars to disagree with. prodtools' `runmu2e` reads `setup` from
the cnf (`_extract_simjob_setup` → `jp.setup()`), and `json2jobdef`
requires the field non-empty. Copying the env-var approach would leave
the cnf asserting one `simjob_setup` while an environment variable
silently overrode it at runtime — two sources of truth for what code
ran, with the durable one holding the wrong answer. Writing
`"Code/setup.sh"` into the cnf costs nothing, reuses upstream's own
string, and keeps `jobquery --recipe`, `fcldump` and `runlocal` honest.

The transport itself — `--tar_file_name dropbox://`, the
`Code/setup.sh` convention, `$INPUT_TAR_DIR_LOCAL` prefixing — is
copied unchanged, so a `muse tarball` output works in both tools.

### The resolver

One function, shared by the worker and the local runner:

```python
def resolve_setup(jp_setup, code_root=None):
    """Absolute path to the script to source.

    An absolute jp_setup passes through unchanged and code_root is
    ignored. A relative jp_setup means code mode and REQUIRES
    code_root; raise if it is missing. Never fall back to /cvmfs.
    """
```

The grid passes `code_root=os.environ.get('INPUT_TAR_DIR_LOCAL')`; the
local runner passes the directory it unpacked into. Everything else in
this design is wiring to this function.

### Data flow

```
muse tarball  ->  Code.tar.bz2                      (Code/setup.sh inside)
json2jobdef   ->  cnf.*.tar   {setup: "Code/setup.sh", code_ref: {...}}
              ->  entry       {code: "/abs/path/Code.tar.bz2"}
jobsub_argv   ->  --tar_file_name dropbox://<Code.tar.bz2>
                  (alongside the three existing -f dropbox://)
runjob.sh     ->  runmu2e reads $INPUT_TAR_DIR_LOCAL
runmu2e       ->  source $INPUT_TAR_DIR_LOCAL/Code/setup.sh && mu2e -c mu2e.fcl
```

Local is identical minus jobsub: `runlocal --code <path>` unpacks
**once** into `<workdir>/code/` and every child resolves against that
one root. Not per job — 3.6 GB times four parallel jobs is not viable.

## Components

### `utils/jobdef.py`

Make the existing `--code` flag real.

- `_build_jobpars_json` sets `setup` to `'Code/setup.sh'` when
  `config['code']` is present, and to `config['simjob_setup']`
  otherwise. `code` remains `""`.
- The call site at `jobdef.py:682` stops passing `code=""`
  unconditionally.
- Validate the tarball at build time, mirroring `mu2ejobdef:808-828`:
  readable, bzip2-compressed, contains `Code/setup.sh`. Stop scanning
  at the first match — bzip2 is not seekable, and `museTarball.sh`
  writes that member early in the archive.

### `utils/json2jobdef.py`

- `validate_required_fields` (`json2jobdef.py:337`) stops requiring
  `simjob_setup` outright; it requires exactly one of `simjob_setup`
  and `code`.
- `build_jobdesc` (`json2jobdef.py:448`) passes `code` through to the
  entry, using the same pass-through shape as `input_pattern` and
  `prestage`.
- The equivalent-command print (`json2jobdef.py:399`) emits `--code`
  in code mode instead of hardcoding `--setup`.

### `utils/jobdesc.py`

- `validate_entry_value` learns `code`: must be a string and an
  absolute path. Deliberately **no filename-suffix rule** — the archive
  being bzip2 is checked by content in `jobdef`, the way
  `mu2ejobdef:813-816` does it, so a differently named tarball is not
  rejected for its name. **No existence check here either** — the
  ledger snapshot outlives the file, so existence is a submit-time
  gate, not a grammar rule.
- New `code_of(entry)` accessor beside `inloc_of` and `tarball_of`.
- `code` joins the `set-entry` settable whitelist, so a live campaign
  can be repointed at a rebuilt tarball without a new cnf.

### `utils/jobsub_argv.py`

`build_jobsub_argv` gains a `code_tarball=None` keyword. When set:

```python
argv.extend(["--tar_file_name", f"dropbox://{code_tarball}"])
```

The three existing `-f dropbox://` arguments are untouched. They are a
different flag serving a different mechanism, and mu2eprodsys uses both
together today.

### `utils/submit.py`

The call site that builds the argv reads `code_of(entry)` and passes it
through. It also calls the new pre-flight gate (below).

### `utils/runmu2e.py`

- Add `resolve_setup`.
- `_extract_simjob_setup` (`runmu2e.py:64`) currently returns
  `jp.setup()` raw; it returns the resolved path instead, passing
  `code_root=os.environ.get('INPUT_TAR_DIR_LOCAL')`. A relative setup
  with no root raises — no fallback.

### `bin/runjob.sh`

No logic change: jobsub exports `INPUT_TAR_DIR_LOCAL` itself. Add one
`echo` of it beside the existing diagnostics, because an empty value is
the first thing to look for in a failed log.

### `utils/runlocal.py`

- New `--code <path>` flag on the driver, naming the tarball.
- The driver unpacks it once into `<workdir>/code/` before children
  launch, skipped if already present so reruns stay cheap. Needs
  roughly 4 GB there.
- Children never see `--code`; they take `--code-root <dir>`, the
  already-unpacked directory. One unpack, N children. `child_argv`
  must emit `--code-root`, or the reproduce-this-job command the
  driver prints will not run.
- Children resolve through the same `resolve_setup`, passing that
  directory as `code_root`.

### `utils/jobquery.py`

- `codesize()` returns 0, now truthfully rather than as a placeholder.
- Remove `--extract-code`. Its current implementation extracts any
  member ending in `.tar`, which is wrong under sidecar delivery.
- `recipe()` gains the code line so a cnf still reconstructs its own
  build configuration.

### Unchanged

`jobfcl`, `fcldump` and `famtree` read the cnf for fcl content and
names, never for `setup`.

### Documentation

Add the section to `docs/EXAMPLES_schema.md` and regenerate
`EXAMPLES.md` with a full `/refresh-examples` run. No CLAUDE.md change.

## Provenance binding

`jobdef` computes the tarball's `sha256` and size at build time and
writes them into jobpars as a new key:

```json
"code_ref": {"sha256": "...", "size": 1073741824, "source_path": "/abs/Code.tar.bz2"}
```

A new `check_code_tarball(entry, cnf_path)` in
`utils/check_inputs.py` reads `code_ref` out of the cnf at `cnf_path`,
rehashes the code tarball named by `code_of(entry)`, and refuses on
mismatch. The parameter is named `cnf_path`, not `tarball_path`,
because two different tarballs are in play on this path and the
existing `check_inputs(tarball_path, ...)` already means the cnf. It is called from the same fail-closed gate as
`check_inputs` (`submit.py:464`, exit 2). It lives beside
`check_inputs` rather than inside it so that `check_inputs` keeps its
single meaning: input-data residency.

Cost is roughly 3 seconds per submit on a 1 GB tarball — negligible
beside an RCDS publish.

This is the one place the design is stronger than mu2eprodsys, which
binds nothing: here you cannot silently ship different code than the
cnf was built against. It is cleanly separable — the feature works
without it — so it is the first thing to cut if it proves costly.

## Failure modes

1. **Tarball moved or deleted** between enqueue and a later slice: the
   gate refuses and the campaign pauses. This is correct — the bytes
   the cnf names are gone.
2. **Tarball rebuilt in place** with different content: hash mismatch,
   refused. The fix is a new cnf, or `set-entry code` if the change was
   intended.
3. **RCDS publication is a cvmfs propagation, not instant.** jobsub's
   own `rcds` check blocks until published. Do **not** pass
   `--skip-check rcds`; that is how jobs land before their code does.
   The first submit of a new tarball pays minutes; later slices reusing
   it are free.
4. **`INPUT_TAR_DIR_LOCAL` unset** on a worker whose cnf says
   `Code/setup.sh`: `resolve_setup` raises with a named error before
   `mu2e` starts, rather than failing as a bare `source: No such file`.
5. **`muse setup <dir>` against a read-only cvmfs mount.** mu2eprodsys
   does exactly this in production today, so it is expected to work —
   but that is inference, not measurement, until the grid smoke
   confirms it. It is the single most important thing that smoke
   proves.
6. **Local unpack** needs roughly 4 GB in the workdir. A tar failure is
   fatal, not warned past.
7. **The code tarball is not in SAM.** Delete it and the campaign
   becomes unreproducible even though the cnf survives. For `--prod`
   the tarball must live on a durable, mu2epro-readable path. This is
   documented as a requirement, not automated.

## Testing

### Unit

In `test/test_unit.py`, run as
`env -i PATH=/usr/bin:/bin HOME=$HOME /usr/bin/python3 -u test/test_unit.py`.
Fixtures build a few-kilobyte real bzip2 tarball containing
`Code/setup.sh` in-test: no /cvmfs, no network.

- `resolve_setup`, four cases: absolute passes through; relative plus
  root joins; relative with no root raises; absolute with a root
  ignores the root.
- Code-mode jobpars shape: `setup == "Code/setup.sh"`, `code == ""`,
  `code_ref` present and well formed.
- `validate_required_fields` accepts exactly one of `simjob_setup` and
  `code`, and rejects both zero and two.
- `code` grammar in `validate_entry_value`: absolute path required,
  relative path rejected, non-string rejected, and a tarball whose
  name does not end in `.tar.bz2` is **accepted** (content, not name,
  decides).
- `build_jobsub_argv` gains `--tar_file_name dropbox://<path>` **and
  still carries all three `-f dropbox://` arguments**.
- `code_of(entry)`.
- `check_code_tarball` refuses on hash mismatch and passes on match.
- `jobdef` rejects a tarball that is not bzip2, and one with no
  `Code/setup.sh`.

### Live gates, in order

**Local smoke.** `muse tarball` off the working build, then
`bin/runlocal --code <tarball> --nevts 10 --indices 0` against an
existing cnf. Proves the tarball is complete and self-sufficient.

**Grid smoke.** One `run_as="self"` entry, about five jobs. This is the
load-bearing test: it proves that `--tar_file_name` coexists with the
three `-f dropbox://`, that `INPUT_TAR_DIR_LOCAL` is populated, that
`muse setup` works against the read-only RCDS mount, and that outputs
land. No real work uses this feature before that smoke passes.

## Out of scope

- Automatically pushing the code tarball anywhere (SAM, dCache).
- More than one code tarball per campaign.
- `--use-pnfs-dropbox`.
- Any mu2epro production run beyond documenting the durable-path
  requirement in failure mode 7.
