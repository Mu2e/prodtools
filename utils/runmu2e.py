#!/usr/bin/env python3
import argparse
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Allow running this file directly: make package root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.job_common import Mu2eName, log_storage_location
from utils.jobdesc import OUTSTAGE_LOCATION
from utils.jobfcl import Mu2eJobFCL
from utils.jobquery import Mu2eJobPars
from utils.prod_utils import (
    run,
    fail,
    _fetch_file_local,
    resolve_entry_index,
    write_fcl,
    write_direct_input_fcl,
    push_output,
)
from utils.samweb_wrapper import locate_file_strict, locate_files_strict


# ============================================================
# Runner implementation: jobdesc validation, per-mode prep
# (direct-input / normal), mu2e command build, data/log pushes.
# runmu2e is the only consumer. Template and g4bl runner modes
# were deleted with the POMS backend (tag pre-poms-removal).
# ============================================================

def _job_index_from_fname(fname):
    """Parse (job_index, sequencer) from a Mu2e fname's sequencer field.
    Returns (0, sequencer) for all-zero sequencers (parent-tarball convention).
    Raises ValueError on a fname that isn't a 6-field Mu2e file/tarball."""
    n = Mu2eName.parse(Path(fname).name)
    sequencer = n.sequencer
    if sequencer is None:
        raise ValueError(f"Invalid Mu2e fname: {fname}; no sequencer field")
    stripped = sequencer.lstrip('0')
    return (int(stripped) if stripped else 0), sequencer

def _require_fields(entry, required_fields, mode_name):
    """Fail loudly (sys.exit 1) if any required field is missing from entry.
    Used by validate_jobdesc per-mode validation."""
    for field in required_fields:
        if field not in entry:
            fail(f"Error: {mode_name} requires '{field}' field")

def resolve_setup(jp_setup, code_root=None):
    """The absolute path of the script to source for this job.

    ABSOLUTE `jp_setup` is a /cvmfs Musing: returned unchanged, `code_root`
    ignored. RELATIVE means the cnf was built with `--code` and its Offline
    build ships as a separate tarball; join it onto `code_root`.

    Raises rather than falling back: silently sourcing a /cvmfs release
    for a code-mode job would run the WRONG Offline and report success.
    """
    if not jp_setup:
        raise ValueError(
            "cnf jobpars carries no 'setup' — the tarball is malformed")
    if os.path.isabs(jp_setup):
        return jp_setup
    if not code_root:
        raise ValueError(
            f"cnf setup {jp_setup!r} is relative, so this cnf was built "
            f"with --code and its Offline build travels as a separate "
            f"tarball, but no code root was given. On the grid that "
            f"means $INPUT_TAR_DIR_LOCAL is unset — was --tar_file_name "
            f"passed to jobsub_submit? Locally, pass --code to "
            f"bin/runlocal.")
    return os.path.join(code_root, jp_setup)

def _code_root_from(args):
    """Where an unpacked code tarball lives, for this caller.

    The grid worker learns it from $INPUT_TAR_DIR_LOCAL (set by jobsub
    when --tar_file_name was passed); the local runner passes --code-root
    on `args`. Checked in that order so a grid-like local run still wins.
    """
    return (getattr(args, 'code_root', None)
            or os.environ.get('INPUT_TAR_DIR_LOCAL'))

def _extract_simjob_setup(tarball, jp=None, code_root=None):
    """Read the setup-script path from a cnf.*.tar's jobpars.json (via
    Mu2eJobPars — pass a pre-built instance to avoid re-parsing the
    tarball), resolve it through `resolve_setup`. Failures propagate: the
    traceback (TarError/OSError from the tarball read, ValueError from
    resolve_setup) already names the tarball.
    """
    jp = jp if jp is not None else Mu2eJobPars(tarball)
    setup = resolve_setup(jp.setup(), code_root=code_root)
    print(f"Job setup script: {setup}")
    return setup

def replace_file_extensions(input_str, first_field, last_field):
    """Replace the tier and extension fields of a Mu2e dot-name."""
    return str(Mu2eName.parse(input_str).as_tier(first_field).with_extension(last_field))

def validate_jobdesc(jobdesc):
    """Validate the job description and pick the dispatch mode.

    Returns 'direct_input' or False (normal mode); exits via fail() on
    invalid input.
    """
    if not jobdesc:
        fail("Error: No job description found in ops")

    # firstjob (cnf-index window) only makes sense with a fixed njobs —
    # otherwise it's silently ignored and the entry re-runs cnf indices
    # [0, N), duplicating physics.
    if 'firstjob' in jobdesc and 'njobs' not in jobdesc:
        fail("Error: jobdesc has 'firstjob' but no 'njobs' — "
             "index windows require a fixed job count")

    # Direct-input mode: tarball present, no njobs.
    if 'tarball' in jobdesc and 'njobs' not in jobdesc:
        _require_fields(jobdesc, ['tarball', 'inloc', 'outputs'],
                        'Direct-input mode')
        return 'direct_input'

    if 'njobs' not in jobdesc:
        fail("Error: Normal mode requires 'njobs' in the jobdesc")
    _require_fields(jobdesc, ['tarball', 'inloc', 'outputs'],
                    'Normal mode')
    return False

def process_direct_input(jobdesc, fname, args):
    """Process a job in direct-input mode.

    fname is an actual art file (e.g. assigned by Data Dispatcher); output
    filenames are derived from its desc and sequencer fields. Returns
    (fcl, simjob_setup, fname, outputs).
    """

    jobdesc_entry = jobdesc
    tarball = jobdesc_entry['tarball']

    fname_base = Path(fname).name
    try:
        n = Mu2eName.parse(fname_base)
    except ValueError as exc:
        fail(f"Error: Invalid filename format: {fname_base}: {exc}")
    if not n.is_file:
        fail(f"Error: Invalid filename format: {fname_base}. "
             f"Expected tier.owner.desc.dsconf.sequencer.ext")
    print(f"Direct-input mode: fname={fname}, desc={n.description}, seq={n.sequencer}")

    _fetch_file_local(tarball)
    job_fcl = Mu2eJobFCL(tarball)
    fcl = write_direct_input_fcl(job_fcl, fname)

    # setup() lives on Mu2eJobBase, so job_fcl serves here too — avoids a
    # second gunzip+parse of jobpars.json.
    simjob_setup = _extract_simjob_setup(tarball, jp=job_fcl,
                                         code_root=_code_root_from(args))

    outputs = jobdesc_entry['outputs']
    return fcl, simjob_setup, fname, outputs

def proto_for_inloc(inloc):
    """'file' only for dir: paths a worker can POSIX-read. A dir: under
    /pnfs is dCache, never mounted on a worker, so it streams via xrootd
    like any other dCache location (file_resolver renders the xroot URL
    for dir:+root)."""
    if inloc.startswith('dir:') and not inloc[4:].startswith('/pnfs/'):
        return 'file'
    return 'root'

def _stage_inputs_locally(all_files, tarball, job_index_num):
    """Copy every input into ./indir and return an FCL that reads from
    there. One batch SAM locate (a mixing job has ~90 inputs); a file
    with no usable location is an error, not a retry."""
    fcl = write_fcl(tarball, f"dir:{os.getcwd()}/indir", 'file', job_index_num)
    print("Starting to copy input files locally")
    located = locate_files_strict(all_files)
    for file in all_files:
        locations = located.get(file)
        if not locations or 'location_type' not in locations[0]:
            raise RuntimeError(f"Could not detect location for file: {file}")
        file_inloc = locations[0]['location_type']
        print(f"Detected location of {file}: {file_inloc}")
        print(f"Copying {file} from {file_inloc}")
        _fetch_file_local(file, src_location=file_inloc)
    run(f"mkdir indir; mv *.art indir/", shell=True)
    return fcl


def process_jobdef(jobdesc, fname, args):
    """Process a job in normal mode.

    fname is the index filename; args needs `copy_input` (overridden by
    the resolved entry's 'copy_input' key when present). Returns
    (fcl, simjob_setup, infiles, outputs).
    """

    try:
        job_index, _ = _job_index_from_fname(fname)
    except ValueError as e:
        fail(f"Error: Invalid Mu2e fname: {fname}: {e}")

    # Global job index -> entry's cnf-local index
    jobdesc_entry, job_index_num = resolve_entry_index(jobdesc, job_index)

    if jobdesc_entry is None:
        fail(f"Error: Job index {job_index} out of range. "
             f"Total jobs available: {jobdesc.get('njobs', 0)}")

    print(f"Global job index: {job_index}, "
          f"Local job index within definition: {job_index_num}")

    inloc = jobdesc_entry['inloc']
    tarball = jobdesc_entry['tarball']

    _fetch_file_local(tarball)

    # tbs.chunk_mode = {source, lines, local_filename}: materialize this
    # job's slice of the cvmfs source into local_filename before mu2e
    # runs. Every job's FCL references local_filename (via fcl_overrides
    # at jobdef-creation time), so mu2e reads whatever's written there.
    jp = Mu2eJobPars(tarball)
    chunk_mode = jp.json_data.get('tbs', {}).get('chunk_mode')
    if chunk_mode:
        src = chunk_mode['source']
        lines_per_chunk = int(chunk_mode['lines'])
        local_name = chunk_mode['local_filename']
        start = job_index_num * lines_per_chunk + 1
        end = start + lines_per_chunk - 1
        print(f"chunk_mode: extracting lines {start}-{end} of {src} -> {local_name}")
        # Quote paths from jobpars — cvmfs today, but future configs might
        # contain whitespace or shell metacharacters.
        sed_range = f"{start},{end}p"
        cmd = f"sed -n {shlex.quote(sed_range)} {shlex.quote(src)} > {shlex.quote(local_name)}"
        run(cmd, shell=True)

    inputs = jp.job_inputs(job_index_num)
    all_files = []
    for file_list in inputs.values():
        all_files.extend(file_list)
    infiles = " ".join(all_files)

    # copy_input: entry's key wins when present (per-entry opt-in, e.g.
    # fat-runtime-tail descs where a mid-job xroot drop wastes the most
    # CPU), else the CLI --copy-input flag. Streaming is the default —
    # the POMS launch template never passed --copy-input.
    copy_input = jobdesc_entry.get('copy_input', bool(args.copy_input))
    if not isinstance(copy_input, bool):
        fail(f"Error: copy_input must be true or false, got {copy_input!r}")

    # Stash files are on CVMFS and resilient files use xrootd — no local copy needed
    if copy_input and infiles.strip() and inloc not in ("none", "stash", "resilient"):
        print(f"Copying input files locally from {inloc}: {infiles}")
        fcl = _stage_inputs_locally(all_files, tarball, job_index_num)
        print(f"FCL: {fcl}")
    else:
        proto = proto_for_inloc(inloc)
        print(f"Using streaming inputs from {inloc} (protocol: {proto})")
        fcl = write_fcl(tarball, inloc, proto, job_index_num)
        print(f"FCL: {fcl}")

    simjob_setup = _extract_simjob_setup(tarball, jp=jp,
                                         code_root=_code_root_from(args))

    outputs = jobdesc_entry['outputs']
    return fcl, simjob_setup, infiles, outputs, inloc

def build_mu2e_cmd(fcl, simjob_setup, args):
    """Build the `subprocess.run(..., shell=False)`-ready arg list for
    running mu2e against an FCL.

    The inner bash script joins setup-source and mu2e with `&&` so mu2e
    is skipped if the source fails; shell=False here just closes the
    quoting hazard around the `fcl` / `simjob_setup` paths.
    """
    inner = f"source {simjob_setup} && mu2e -c {fcl}"
    if args.nevts > 0:
        inner += f" -n {int(args.nevts)}"
    if args.mu2e_options.strip():
        inner += f" {args.mu2e_options.strip()}"
    return ['bash', '-c', inner]

def _outstage_dir():
    """`$MU2EGRID_WFOUTSTAGE/$CLUSTER/$PROCESS` — the mu2egrid outstage
    layout `mu2eClusterCheckAndMove` already knows how to walk.

    jobsub_argv exports MU2EGRID_WFOUTSTAGE on every submission with
    `--need-storage-modify`, so the worker token covers this write.
    Missing means the caller isn't a real worker; raise rather than
    default, or the output lands where nobody looks.
    """
    base = os.environ.get('MU2EGRID_WFOUTSTAGE')
    if not base:
        raise RuntimeError(
            "MU2EGRID_WFOUTSTAGE is not set — outstage outputs have "
            "nowhere to go. It is exported by the submission argv; a "
            "job seeing it unset was not launched by prodtools.")
    cluster = os.environ.get('CLUSTER', '0')
    process = os.environ.get('PROCESS', '0')
    return f"{base}/{cluster}/{process}"


def _copy_to_outstage(filenames):
    """Copy files to the job's outstage directory. No SAM declare.

    Uses ifdh rather than `mdh copy-file`: mdh resolves a destination
    from a file's dataset name and location class — exactly the layout
    an outstage file is opting out of. ifdh takes a literal destination
    directory instead.
    """
    if not filenames:
        return 0
    dest = _outstage_dir()
    print(f"Copying {len(filenames)} file(s) to outstage: {dest}")
    rc = run(f"ifdh mkdir_p {dest}", shell=True, retries=3, retry_delay=60)
    if rc:
        return rc
    return run(f"ifdh cp -D {' '.join(filenames)} {dest}",
               shell=True, retries=3, retry_delay=60)


def push_data(outputs, infiles, simjob_setup=None, track_parents=True):
    """Declare/push data files using wildcard patterns from JSON outputs.

    Outputs are partitioned by location. Anything bound for `outstage`
    is copied to $MU2EGRID_WFOUTSTAGE and never reaches pushOutput,
    which has no copy-without-declare mode (`dosam = True` is
    unconditional at pushOutput.py:268).

    `track_parents=False` writes 'none' in output.txt's third column and
    skips parents_list.txt, for jobs whose inputs aren't SAM-registered
    (e.g. cvmfs via `inloc: dir:<path>`) — printJson --parents exits 25
    on non-SAM parents, cascading into KeyError('checksum') inside
    pushOutput otherwise.
    """

    parents_field = "parents_list.txt" if track_parents else "none"

    # A job's own inputs are never outputs: in direct-input mode the
    # fetched input art file sits in cwd, so a broad glob (e.g. '*.art')
    # would declare it — and pushOutput, finding the original already at
    # its dataset path, tries to DELETE production data as a stale orphan
    # (smoke cluster 29444911; only the token scope blocked it).
    # Whether anything is DECLARED comes from config, not glob results:
    # an entry with a declared output still writes parents_list.txt and
    # calls pushOutput even on an empty match. Only outstage-only skips both.
    declares = any(o['location'] != OUTSTAGE_LOCATION for o in outputs)

    parent_names = {Path(p).name for p in infiles.split()} if infiles else set()
    output_specs = []
    outstage_files = []
    for output in outputs:
        dataset_pattern = output['dataset']
        location = output['location']
        matching_files = [f for f in glob.glob(dataset_pattern)
                          if Path(f).name not in parent_names]
        print(f"Pattern '{dataset_pattern}' matched {len(matching_files)} files: {matching_files}")
        for filename in matching_files:
            if location == OUTSTAGE_LOCATION:
                outstage_files.append(filename)
            else:
                output_specs.append((location, filename, parents_field))

    # parents_list.txt only exists to give pushOutput the SAM parents for
    # a declare; an outstage-only job has no use for it.
    if track_parents and declares:
        Path("parents_list.txt").write_text(infiles.replace(" ", "\n") + "\n")

    rc = _copy_to_outstage(outstage_files)
    if declares:
        rc = push_output(output_specs, "output.txt",
                         simjob_setup=simjob_setup) or rc
    return rc

def push_logs(fcl=None, simjob_setup=None, log_file=None, location="disk"):
    """Declare/push the log file.

    Pass `fcl` (log filename derived via replace_file_extensions) or
    `log_file` directly; at least one is required, and `log_file` wins if
    both are given (for runners with no FCL, e.g. g4bl).

    `location` is a pushOutput destination class — "disk" (default,
    persistent), "scratch" (for user runs lacking `storage.modify` on
    `/mu2e/persistent/.../log/<owner>/`), or "tape". "outstage" is not a
    pushOutput class: the log is copied to $MU2EGRID_WFOUTSTAGE and left
    undeclared, matching a job whose data went to outstage (a declared
    log would name undeclared parents) — log_storage_location() routes
    it here.
    """

    if log_file is not None:
        logfile = log_file
    elif fcl is not None:
        logfile = replace_file_extensions(fcl, "log", "log")
    else:
        print("Warning: push_logs called with neither fcl nor log_file; nothing to push")
        return 0

    # Only meaningful when derived from fcl (JOBSUB_LOG_FILE is the
    # canonical source); for explicit log_file the runner already
    # streamed to it.
    jsb_tmp = os.getenv("JSB_TMP")
    if jsb_tmp and log_file is None:
        src = os.path.join(jsb_tmp, "JOBSUB_LOG_FILE")
        print(f"Copying jobsub log from {src} to {logfile}")
        try:
            shutil.copy(src, logfile)
        except FileNotFoundError:
            print(f"Warning: Jobsub log not found at {src}")

    if Path(logfile).exists():
        if location == OUTSTAGE_LOCATION:
            return _copy_to_outstage([logfile])
        # Name parents_list.txt only if it's actually on disk: pushOutput
        # reports `ERROR - parents file ... not found` then exits 0, so a
        # missing file makes the log push a SILENT no-op. push_data writes
        # it, and it's routinely absent — mu2e failed (push_data skipped,
        # exactly when the log is the only evidence left) or
        # track_parents=False (inloc `dir:`, non-SAM inputs). G4bl passes
        # log_file and never has SAM parents.
        parents = ("parents_list.txt"
                   if log_file is None and Path("parents_list.txt").is_file()
                   else "none")
        output_specs = [(location, logfile, parents)]
        return push_output(output_specs, "log_output.txt", simjob_setup=simjob_setup)
    else:
        print(f"Warning: Log file {logfile} not found, skipping log push")
        return 0


# ============================================================
# Direct mode (jobsub_submit) — Phase 2 v1, and the only worker mode
#
# Detected by MU2EGRID_JOBDEF in the environment (set by the
# jobsub_submit argv); runmu2e refuses to run without it. The submitter
# ships the cnf tarball + an "ops JSON" via dropbox, both landing under
# $CONDOR_DIR_INPUT. Per-job index resolves via ops['jobs'][PROCESS].
# ============================================================


def _is_direct_mode():
    """Direct mode is signalled by env vars set by our jobsub_submit argv."""
    return 'MU2EGRID_JOBDEF' in os.environ


def _direct_input_dir():
    """jobsub_submit lands -f dropbox:// files under $CONDOR_DIR_INPUT.
    Fall back to cwd for local testing."""
    return os.environ.get('CONDOR_DIR_INPUT', '.')


def _load_direct_ops():
    """Load the ops JSON shipped via dropbox. Contains: jobs (PROCESS→index
    array), inspec (dataset → [protocol, location]), jobdesc (the
    submission-map entry, consumed via process_jobdef)."""
    ops_basename = os.environ['MU2EGRID_OPSJSON']
    ops_path = os.path.join(_direct_input_dir(), ops_basename)
    with open(ops_path) as f:
        return json.load(f)


def _resolve_direct_index(ops):
    """PROCESS → real job index via ops['jobs'][PROCESS] (replaces mu2ejobmap)."""
    process = int(os.environ.get('PROCESS', '0'))
    jobs = ops.get('jobs', [])
    if process < 0 or process >= len(jobs):
        raise RuntimeError(
            f"PROCESS={process} out of range for jobset of length {len(jobs)}"
        )
    return jobs[process]


def _synthesize_direct_fname(index):
    """Build a fake fname string that _job_index_from_fname() reverses to
    `index`. process_jobdef and friends only consume the parts[4] sequencer."""
    return f"x.x.x.x.{index:08d}.x"


def _emit_manifest(log_path, manifest_files):
    """Append the SHA256 manifest block to the log file, in a format
    `mu2eClusterCheckAndMove` can parse. Port of `addManifest` from
    mu2egrid::impl/mu2ejobsub.sh:44-56.

    Format (the parser is regex-strict):

        mu2egrid diskUse = <kbytes>
        #================================================================
        # mu2egrid manifest
        # <ls -al line, each prefixed with '# '>
        ...
        #----------------------------------------------------------------
        # algorithm: sha256sum
        <hex>  <file>
        ...
        # mu2egrid manifest selfcheck: <hex>  -

    The selfcheck reads the manifest from stdin (`sha256sum < log`), which
    is why the trailing `  -` is part of the contract.
    """
    log = Path(log_path)

    # Raw `du -ks` output (kbytes \t path) for byte-exact parity with the
    # bash addManifest reference; regex parsers match
    # `^mu2egrid diskUse = (\d+)` regardless of trailing content.
    du = subprocess.run(['du', '-ks'], capture_output=True, text=True, check=False)
    du_out = du.stdout.rstrip('\n') if du.stdout else '0'

    # C locale so ls's positional fields are stable
    ls = subprocess.run(['ls', '-al'], capture_output=True, text=True,
                        env={**os.environ, 'LC_ALL': 'C'}, check=False)

    with log.open('a') as f:
        f.write(f"mu2egrid diskUse = {du_out}\n")
        f.write("#" + "=" * 64 + "\n")
        f.write("# mu2egrid manifest\n")
        for line in ls.stdout.splitlines():
            f.write(f"# {line}\n")
        f.write("#" + "-" * 64 + "\n")
        f.write("# algorithm: sha256sum\n")
        for fname in manifest_files:
            if not Path(fname).exists():
                continue
            h = hashlib.sha256()
            with open(fname, 'rb') as g:
                for chunk in iter(lambda: g.read(1 << 20), b''):
                    h.update(chunk)
            f.write(f"{h.hexdigest()}  {fname}\n")

    # Selfcheck: sha256sum of everything written so far, in `sha256sum <
    # log` format ("  -" trailer, no filename).
    sc = hashlib.sha256()
    with log.open('rb') as g:
        for chunk in iter(lambda: g.read(1 << 20), b''):
            sc.update(chunk)
    with log.open('a') as f:
        f.write(f"# mu2egrid manifest selfcheck: {sc.hexdigest()}  -\n")


def _is_terminal_push_error(output):
    """True when pushOutput failed for a reason no retry can ever clear.

    Known case: the target already exists on tape from an earlier push
    that copied the file but never finished declaring it. pushOutput's
    `recover` path then tries to gfal-rm the orphan to rewrite it, and
    tape is write-once — the delete 403s every time, so retrying just
    re-runs mu2e for hours and dies at the same step.

    Deliberately narrow: a bare 403 is NOT enough — a 403 on the *write*
    means a missing storage.modify scope, a different diagnosis.
    Unknown/unavailable output stays retryable, since misclassifying a
    transient failure as terminal would strand recoverable work.
    """
    if not output:
        return False
    text = str(output)
    return 'rm failed' in text and ('HTTP 403' in text
                                    or 'Permission refused' in text)


def _push_with_retry(push_fn, *args, retries=3, base_delay=30, **kwargs):
    """Direct-mode wrapper for push_data / push_logs. Retries on
    CalledProcessError with exponential backoff, then raises so condor
    sees a job failure (CB2: don't silently leave files unregistered).

    Terminal failures (see _is_terminal_push_error) skip retries and
    raise on the first attempt — retrying only burns grid time and
    delays the human who has to intervene."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            push_fn(*args, **kwargs)
            return
        except subprocess.CalledProcessError as e:
            last_exc = e
            if _is_terminal_push_error(getattr(e, 'output', None)):
                print(f"[direct] {push_fn.__name__} failed for a "
                      f"NON-RETRYABLE reason (rc={e.returncode}): an "
                      f"undeclared file already occupies the tape path and "
                      f"cannot be removed. Not retrying — a human must "
                      f"clear the orphan.")
                raise
            if attempt == retries:
                break
            delay = base_delay * (2 ** attempt)
            print(f"[direct] {push_fn.__name__} attempt {attempt + 1}/{retries + 1} "
                  f"failed (rc={e.returncode}); retrying in {delay}s")
            time.sleep(delay)
    raise last_exc


def _push_all(data_push, log_push):
    """Run `data_push`, then `log_push` — and run `log_push` even when
    `data_push` raises: a data-push failure is precisely when the log is
    the only surviving evidence (retries that die on the same
    tape-orphan 403 left zero forensic trace once, because the log push
    never ran).

    Failure precedence: the data-push exception always wins (a log-push
    failure next to it is just a symptom); a log-push failure after a
    successful data push still fails the job (CB2 — never silently
    leave a file unregistered).
    """
    data_exc = None
    try:
        data_push()
    except subprocess.CalledProcessError as exc:
        data_exc = exc
        print(f"[direct] data push failed (rc={exc.returncode}) — pushing the "
              f"log before failing the job")

    try:
        log_push()
    except subprocess.CalledProcessError as exc:
        if data_exc is None:
            raise
        print(f"[direct] WARNING: log push also failed (rc={exc.returncode}); "
              f"re-raising the data-push failure")

    if data_exc is not None:
        raise data_exc


def _execute_mu2e(fcl, simjob_setup, args):
    """Execute step for the direct worker: build the mu2e command, run
    it, return True iff it failed. Callers push data only on success but
    always push logs (so failures stay debuggable) — that split, plus
    retry/manifest/log-location, stay caller-side by design."""
    cmd = build_mu2e_cmd(fcl, simjob_setup, args)
    print(f"[direct] Executing: {cmd}")
    print(f"[direct] Working dir: {os.getcwd()}, FCL exists: {os.path.exists(fcl)}")
    print("=== Starting Mu2e execution ===")
    try:
        run(cmd, shell=False)
        print("=== Mu2e execution completed successfully ===")
        return False
    except subprocess.CalledProcessError as e:
        print(f"=== Mu2e execution failed with exit code {e.returncode} ===")
        # Don't re-raise — callers still upload logs (and decide on outputs)
        return True


def _direct_dispatch(args, ops, index):
    """Dispatch one direct-mode job: run the entry's
    prep — normal index mode via process_jobdef, or a draining batch
    (ops ships a `files` list) via process_direct_input — then the
    shared mu2e -c → manifest → push (with retries) tail."""
    jobdesc = ops['jobdesc']
    files = ops.get('files')

    mode = validate_jobdesc(jobdesc)
    if files is not None:
        # Draining batch: PROCESS → position in the batch → input file.
        if mode != 'direct_input':
            print(f"ERROR: ops carries a files list but the jobdesc is "
                  f"'{mode or 'normal'}' mode — draining batches ship "
                  f"direct-input entries only.")
            sys.exit(1)
        if not 0 <= index < len(files):
            print(f"ERROR: job index {index} out of range for files "
                  f"list of length {len(files)}")
            sys.exit(1)
        fname = files[index]
        print(f"[direct] files[{index}] = {fname}")
        inloc = jobdesc.get('inloc')
        # direct-input FCL writes the bare local filename (no xroot
        # fallback), so every draining input must be fetched. Resolve the
        # REAL location via SAM rather than trusting _fetch_file_local's
        # 'disk' default — draining entries typically ship inloc='tape'.
        locations = locate_file_strict(fname)
        if not locations or 'location_type' not in locations[0]:
            raise RuntimeError(f"Could not detect location for file: {fname}")
        file_inloc = locations[0]['location_type']
        print(f"Detected location of {fname}: {file_inloc}")
        _fetch_file_local(fname, src_location=file_inloc)
        fcl, simjob_setup, infiles, outputs = process_direct_input(
            jobdesc, fname, args)
    else:
        if mode != False:  # noqa: E712 — validate_jobdesc returns False for normal
            print(f"ERROR: direct mode supports normal-mode jobdescs "
                  f"only, got '{mode}'. direct_input entries run as "
                  f"draining batches (submissions resubmit --files).")
            sys.exit(1)
        fname = _synthesize_direct_fname(index)
        fcl, simjob_setup, infiles, outputs, inloc = process_jobdef(
            jobdesc, fname, args)

    # `dir:<path>` inloc means inputs come from a locally-mounted FS and
    # have no SAM parents.
    track_parents = not (isinstance(inloc, str) and inloc.startswith('dir:'))

    job_failed = _execute_mu2e(fcl, simjob_setup, args)

    # Append SHA256 manifest before pushing — mu2eClusterCheckAndMove
    # parses the log for `mu2egrid manifest`.
    log_file = replace_file_extensions(fcl, "log", "log")
    if Path(log_file).exists():
        manifest_files = []
        if not job_failed:
            for o in outputs:
                pattern = o['dataset']
                manifest_files.extend(sorted(Path('.').glob(pattern)))
        _emit_manifest(log_file, [str(f) for f in manifest_files])

    # Logs share the first output's location so the worker token's
    # storage.modify scope covers both. Without this, a non-mu2epro
    # account whose data goes to `scratch` would still try to push the
    # log to `disk` (push_logs default), which
    # `/mu2e/persistent/datasets/...` doesn't grant.
    log_location = log_storage_location(outputs)

    def data_push():
        if job_failed:
            return
        _push_with_retry(push_data, outputs, infiles,
                         simjob_setup=simjob_setup, track_parents=track_parents)

    def log_push():
        _push_with_retry(push_logs, fcl, simjob_setup=simjob_setup,
                         location=log_location)

    if args.dry_run:
        datasets = ('none (job failed)' if job_failed
                    else ', '.join(o['dataset'] for o in outputs))
        print(f"[direct] DRY RUN — would push data: {datasets}; "
              f"would push log to '{log_location}'. Skipping pushes.")
        return job_failed

    if job_failed:
        print("[direct] mu2e failed — skipping data push, still pushing log")
    # Push outputs only on success; the log ALWAYS — including when the
    # data push itself raises (see _push_all).
    _push_all(data_push, log_push)

    return job_failed


def _direct_main(args):
    """Entry for direct mode. Resolves index, dispatches via _direct_dispatch."""
    ops = _load_direct_ops()
    index = _resolve_direct_index(ops)

    print(f"[direct] PROCESS={os.environ.get('PROCESS', '0')} → job index {index}")
    print(f"[direct] jobdef={os.environ.get('MU2EGRID_JOBDEF')}")

    # -f dropbox:// drops the cnf tarball under $CONDOR_DIR_INPUT but
    # process_jobdef expects the basename in cwd (_fetch_file_local is a
    # no-op if already local).
    jobdef_basename = os.environ['MU2EGRID_JOBDEF']
    if not Path(jobdef_basename).is_file():
        src = Path(_direct_input_dir()) / jobdef_basename
        if src.is_file():
            os.symlink(src, jobdef_basename)
        # else: process_jobdef falls back to _fetch_file_local() from SAM.

    # Inputs stream via xroot by default (POMS-era worker never passed
    # --copy-input); a map entry opts in via "copy_input": true, read in
    # process_jobdef. Forcing args.copy_input=True here was reverted 2026-08-02.

    if _direct_dispatch(args, ops, index):
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Execute production jobs from job definitions.")
    parser.add_argument("--copy-input", action="store_true", help="Copy input files using mdh")
    parser.add_argument('--dry-run', action='store_true', help='Print commands without actually running pushOutput')
    parser.add_argument('--nevts', type=int, default=-1, help='Number of events to process (-1 for all events, default: -1)')
    parser.add_argument('--mu2e-options', type=str, default='', help='Extra options to pass to mu2e command (e.g., "--no-timing --debug")')

    args = parser.parse_args()

    if not _is_direct_mode():
        print("Error: MU2EGRID_JOBDEF is not set. runmu2e runs only as the "
              "direct-backend worker; the POMS --jobdesc mode was removed "
              "(recover it from the pre-poms-removal git tag).")
        sys.exit(1)
    _direct_main(args)


if __name__ == "__main__":
    main()
