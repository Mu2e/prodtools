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
import tarfile
import tempfile
import time
from pathlib import Path

# Allow running this file directly: make package root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.job_common import Mu2eName, log_storage_location
from utils.jobfcl import Mu2eJobFCL
from utils.jobquery import Mu2eJobPars
from utils.prod_utils import (
    run,
    fail,
    _fetch_file_local,
    resolve_map_index,
    write_fcl,
    write_direct_input_fcl,
    push_output,
)
from utils.samweb_wrapper import locate_file_strict, locate_files_strict


# ============================================================
# Runner implementation (relocated from prod_utils, 2026-07-17):
# jobdesc validation, per-mode prep (template / direct-input /
# normal / g4bl), mu2e command build, and the data/log pushes.
# runmu2e is the only consumer.
# ============================================================

def _job_index_from_fname(fname):
    """Parse (job_index, sequencer) from a Mu2e fname's sequencer field.
    Returns (0, sequencer) for all-zero sequencers (parent-tarball convention).
    Raises RuntimeError on a fname that isn't a 6-field Mu2e file/tarball."""
    try:
        n = Mu2eName.parse(Path(fname).name)
    except ValueError as exc:
        raise RuntimeError(f"Invalid Mu2e fname: {fname}: {exc}")
    sequencer = n.sequencer
    if sequencer is None:
        raise RuntimeError(f"Invalid Mu2e fname: {fname}; no sequencer field")
    stripped = sequencer.lstrip('0')
    return (int(stripped) if stripped else 0), sequencer

def _require_fields(entry, required_fields, mode_name):
    """Fail loudly (sys.exit 1) if any required field is missing from entry.
    Used by validate_jobdesc per-mode validation."""
    for field in required_fields:
        if field not in entry:
            fail(f"Error: {mode_name} requires '{field}' field")

def _extract_simjob_setup(tarball, jp=None):
    """Read the SimJob setup-script path from a cnf.*.tar's jobpars.json
    via Mu2eJobPars (pass a pre-built instance to avoid re-parsing the
    tarball). Re-raises with a clear context line on the realistic
    failure modes (bad tarball, missing key, missing file)."""
    try:
        jp = jp if jp is not None else Mu2eJobPars(tarball)
        setup = jp.setup()
        print(f"Job setup script: {setup}")
        return setup
    except (tarfile.TarError, KeyError, FileNotFoundError, OSError) as e:
        print(f"ERROR: Failed to get job setup information from {tarball}: {e}")
        raise

def replace_file_extensions(input_str, first_field, last_field):
    """Replace the tier and extension fields of a Mu2e dot-name."""
    return str(Mu2eName.parse(input_str).as_tier(first_field).with_extension(last_field))

def validate_jobdesc(jobdesc):
    """Validate job descriptions list structure and required fields.

    Args:
        jobdesc: List of job description dictionaries

    Returns:
        str or False: 'template' if template mode, 'direct_input' if direct-input mode,
                      False if normal mode

    Raises:
        SystemExit: If validation fails
    """
    # Validate list is not empty
    if not jobdesc:
        fail("Error: No job descriptions found in jobdesc file")

    # firstjob (cnf-index window) is only meaningful on njobs-bearing
    # entries — anywhere else it would be silently ignored and the entry
    # would re-run cnf indices [0, N), duplicating physics. Maps are
    # hand-edited in practice, so enforce this at the dispatch boundary
    # for every mode, not just at map-write time.
    for i, entry in enumerate(jobdesc):
        if 'firstjob' in entry and 'njobs' not in entry:
            fail(f"Error: jobdesc entry {i} has 'firstjob' but no 'njobs' — "
                 f"index windows require a fixed job count")

    # Check if g4bl runner (has runner: 'g4bl' field)
    if jobdesc[0].get('runner') == 'g4bl':
        if len(jobdesc) > 1:
            fail("Error: g4bl runner requires exactly one entry in jobdesc list")
        entry = jobdesc[0]
        # The map (jobdesc) only carries dispatch fields. The runtime config
        # (desc/dsconf/main_input/events_per_job) lives inside the tarball's
        # `jobpars.json` for grid mode — the tarball is self-describing.
        # Embed_dir mode (local smoke) has no tarball, so the entry must
        # carry the runtime config directly.
        if entry.get('tarball'):
            required_fields = ['outputs']  # runtime config in tarball/jobpars.json
        elif entry.get('embed_dir'):
            required_fields = ['desc', 'dsconf', 'main_input', 'events_per_job', 'outputs']
        else:
            fail("Error: g4bl runner requires either 'tarball' or 'embed_dir'")
        _require_fields(entry, required_fields, 'g4bl runner')
        return 'g4bl'

    # Check if template mode (has fcl_template field)
    if 'fcl_template' in jobdesc[0]:
        if len(jobdesc) > 1:
            fail("Error: Template mode (fcl_template) requires exactly one entry in jobdesc list\n"
                 f"Found {len(jobdesc)} entries. Template mode processes one file at a time.")
        entry = jobdesc[0]
        _require_fields(jobdesc[0],
                        ['fcl_template', 'setup_script', 'inloc', 'outputs'],
                        'Template mode')
        return 'template'

    # Check if direct-input mode: tarball present but no njobs
    if 'tarball' in jobdesc[0] and 'njobs' not in jobdesc[0]:
        if len(jobdesc) > 1:
            fail("Error: Direct-input mode requires exactly one entry in jobdesc list\n"
                 f"Found {len(jobdesc)} entries.")
        _require_fields(jobdesc[0],
                        ['tarball', 'inloc', 'outputs'],
                        'Direct-input mode')
        return 'direct_input'

    # Normal mode validation
    # Entries with tarball but no njobs are generic tarballs - skip in normal dispatch
    # Entries missing tarball entirely are invalid
    for i, entry in enumerate(jobdesc):
        if 'njobs' not in entry:
            if 'tarball' in entry:
                print(f"[INFO] entry {i} ({entry['tarball']}) has no njobs (generic tarball) - skipped in normal dispatch")
                continue
            fail(f"Error: Normal mode requires 'njobs' field in jobdesc entry {i}")
        _require_fields(entry, ['tarball', 'inloc', 'outputs'], f'Normal mode (jobdesc entry {i})')

    return False

def process_template(jobdesc_entry, fname):
    """Process a job in template mode.
    
    Args:
        jobdesc_entry: Job description dictionary
        fname: Input filename
        
    Returns:
        tuple: (fcl, simjob_setup)
    """

    print(f"Template mode: using fcl_template job definition")
    
    # Get FCL template path and validate
    fcl_template_path = jobdesc_entry['fcl_template']
    if not Path(fcl_template_path).is_file():
        raise RuntimeError(f"FCL template not found: {fcl_template_path}")
    print(f"Using FCL template: {fcl_template_path}")
    
    # Read FCL template from file
    with open(fcl_template_path, 'r') as f:
        fcl_content = f.read()
    fcl_basename = Path(fcl_template_path).stem
    
    # Parse variables from input filename (format: tier.owner.desc.dsconf.sequencer.ext)
    fname_base = Path(fname).name
    try:
        n = Mu2eName.parse(fname_base)
    except ValueError as exc:
        raise RuntimeError(f"Invalid filename format: {fname_base}: {exc}")
    if not n.is_file:
        raise RuntimeError(f"Invalid filename format: {fname_base}. Expected a 6-field file name.")

    template_vars = {
        'owner': n.owner,
        'desc': n.description,
        'dsconf': n.dsconf,
        'sequencer': n.sequencer,
    }
    
    # Allow overriding template variables from jobdesc
    if 'template_overrides' in jobdesc_entry:
        template_vars.update(jobdesc_entry['template_overrides'])
        print(f"Applied template overrides: {jobdesc_entry['template_overrides']}")
    
    # Parse output patterns from template
    output_patterns = {}
    for line in fcl_content.split('\n'):
        match = re.match(r'(\S+\.fileName):\s*"([^"]+)"', line)
        if match and '{' in match.group(2):
            output_patterns[match.group(1)] = match.group(2)
    
    # Write FCL: template + overrides (based on input filename)
    # Extract base name from input file (e.g., dig.mu2e.CosmicSignalTriggered.MDC2025ad.001430_00000000.art -> dig.mu2e.CosmicSignalTriggered.MDC2025ad.001430_00000000)
    input_basename = Path(fname).stem  # Remove .art extension
    fcl = f'{input_basename}.fcl'
    with open(fcl, 'w') as f:
        f.write(fcl_content)
        f.write("\n# Template overrides:\n")
        f.write(f'source.fileNames: ["{fname}"]\n')
        for key, pattern in output_patterns.items():
            # Replace all template variables in the pattern
            output_filename = pattern.format(**template_vars)
            f.write(f'{key}: "{output_filename}"\n')
    
    print(f"Template vars: {template_vars}")
    print(f"FCL: {fcl}")
    
    # Use setup_script from JSON
    simjob_setup = jobdesc_entry['setup_script']
    print(f"Job setup script: {simjob_setup}")
    
    return fcl, simjob_setup

def process_direct_input(jobdesc, fname, args):
    """Process a job in direct-input mode.

    In this mode fname is an actual art file (e.g. assigned by Data Dispatcher).
    Output filenames are derived from fname's desc and sequencer fields.

    Args:
        jobdesc: List with exactly one job description dictionary
        fname: Input art filename (full name, e.g. dig.mu2e.CeEndpoint....art)
        args: Command line arguments (unused but kept for API consistency)

    Returns:
        tuple: (fcl, simjob_setup, fname, outputs)
    """

    jobdesc_entry = jobdesc[0]
    tarball = jobdesc_entry['tarball']

    # Parse fname components: tier.owner.desc.dsconf.sequencer.ext
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

    # Extract setup script from the already-parsed tarball (setup() lives
    # on Mu2eJobBase, so the Mu2eJobFCL instance serves — no second
    # gunzip+parse of jobpars.json)
    simjob_setup = _extract_simjob_setup(tarball, jp=job_fcl)

    outputs = jobdesc_entry['outputs']
    return fcl, simjob_setup, fname, outputs

def process_jobdef(jobdesc, fname, args):
    """Process a job in normal mode.

    Args:
        jobdesc: List of job descriptions
        fname: Index filename
        args: Command line arguments (needs copy_input attribute; the
            resolved entry's 'copy_input' key overrides it when present)

    Returns:
        tuple: (fcl, simjob_setup, infiles, outputs)
    """

    # Extract job index from filename
    try:
        job_index, _ = _job_index_from_fname(fname)
    except RuntimeError as e:
        fail(f"Error: {e}")

    # Find which job description this job index belongs to
    jobdesc_entry, jobdesc_index, job_index_num = resolve_map_index(jobdesc, job_index)

    if jobdesc_entry is None:
        total_jobs = sum(d.get('njobs', 0) for d in jobdesc)
        fail(f"Error: Job index {job_index} out of range. Total jobs available: {total_jobs}")

    print(f"Job {job_index} uses definition {jobdesc_index}")
    print(f"Global job index: {job_index}, Local job index within definition: {job_index_num}")

    # Extract fields from JSON structure
    inloc = jobdesc_entry['inloc']
    tarball = jobdesc_entry['tarball']

    # Copy jobdef to local directory if not already local
    _fetch_file_local(tarball)

    # If jobpars declares chunk_mode, materialize this job's slice before
    # mu2e runs. runmu2e reads tbs.chunk_mode = {source, lines, local_filename}
    # and writes the corresponding slice of the cvmfs source to local_filename
    # in cwd. Every job's FCL references local_filename (set via
    # fcl_overrides at jobdef-creation time), so mu2e reads whatever that
    # file contains when it opens.
    jp = Mu2eJobPars(tarball)
    chunk_mode = jp.json_data.get('tbs', {}).get('chunk_mode')
    if chunk_mode:
        src = chunk_mode['source']
        lines_per_chunk = int(chunk_mode['lines'])
        local_name = chunk_mode['local_filename']
        start = job_index_num * lines_per_chunk + 1
        end = start + lines_per_chunk - 1
        print(f"chunk_mode: extracting lines {start}-{end} of {src} -> {local_name}")
        # Quote paths — they come from jobpars (cvmfs today, but future
        # configs might contain whitespace or shell metacharacters).
        sed_range = f"{start},{end}p"
        cmd = f"sed -n {shlex.quote(sed_range)} {shlex.quote(src)} > {shlex.quote(local_name)}"
        run(cmd, shell=True)

    # List input files
    inputs = jp.job_inputs(job_index_num)
    # Flatten the dictionary values into a single list
    all_files = []
    for file_list in inputs.values():
        all_files.extend(file_list)
    infiles = " ".join(all_files)
    
    # Local copy vs streaming: the entry's copy_input key wins when
    # present (per-entry opt-in, e.g. for fat-runtime-tail descs where a
    # mid-job xroot drop wastes the most CPU); otherwise the CLI
    # --copy-input flag. Streaming is the default — the POMS launch
    # template never passed --copy-input, so every POMS-era campaign
    # streamed via xroot.
    copy_input = jobdesc_entry.get('copy_input', bool(args.copy_input))
    if not isinstance(copy_input, bool):
        fail(f"Error: copy_input must be true or false, got {copy_input!r}")

    # Generate FCL - Normal mode with local input copy
    # Stash files are on CVMFS and resilient files use xrootd — no local copying needed
    if copy_input and infiles.strip() and inloc not in ("none", "stash", "resilient"):
        print(f"Copying input files locally from {inloc}: {infiles}")
        fcl = write_fcl(tarball, f"dir:{os.getcwd()}/indir", 'file', job_index_num)
        
        # Copy each file individually, detecting actual location from SAMWeb.
        # Batch-locate everything in one SAM round-trip first (a mixing job
        # has ~90 inputs); per-file fallback keeps the error semantics.
        print("Starting to copy input files locally")
        located = {}
        try:
            result = locate_files_strict(all_files)
            if isinstance(result, dict):
                located = result
        except Exception:
            pass
        for file in all_files:
            locations = located.get(file)
            if not isinstance(locations, list) or not locations:
                locations = locate_file_strict(file)
            if not locations or 'location_type' not in locations[0]:
                raise RuntimeError(f"Could not detect location for file: {file}")
            file_inloc = locations[0]['location_type']
            print(f"Detected location of {file}: {file_inloc}")
            print(f"Copying {file} from {file_inloc}")
            _fetch_file_local(file, src_location=file_inloc)
        run(f"mkdir indir; mv *.art indir/", shell=True)
        print(f"FCL: {fcl}")
    # Generate FCL - Normal mode with streaming inputs
    else:
        # For dir:<path> inloc, inputs are on a locally-mounted filesystem
        # (typically cvmfs). The xroot protocol only works for /pnfs paths,
        # so use the 'file' protocol (direct POSIX read) for dir: mode.
        proto = 'file' if inloc.startswith('dir:') else 'root'
        print(f"Using streaming inputs from {inloc} (protocol: {proto})")
        fcl = write_fcl(tarball, inloc, proto, job_index_num)
        print(f"FCL: {fcl}")
    
    # Extract setup script from tarball
    simjob_setup = _extract_simjob_setup(tarball, jp=jp)

    outputs = jobdesc_entry['outputs']
    return fcl, simjob_setup, infiles, outputs, inloc

def build_mu2e_cmd(fcl, simjob_setup, args):
    """Build the `subprocess.run(..., shell=False)`-ready arg list for running
    mu2e against an FCL.

    The inner bash script joins setup-source and mu2e with `&&` so mu2e is
    skipped if the source fails — matches the prior shell=True
    `f"source X && mu2e -c Y"` semantics. shell=False here closes the
    quoting hazard around `fcl` / `simjob_setup` paths without changing
    bash's parsing of the inner script.
    """
    inner = f"source {simjob_setup} && mu2e -c {fcl}"
    if args.nevts > 0:
        inner += f" -n {int(args.nevts)}"
    if args.mu2e_options.strip():
        inner += f" {args.mu2e_options.strip()}"
    return ['bash', '-c', inner]

def process_g4bl_jobdef(jobdesc_entry, fname, args):
    """Run a G4Beamline simulation job. Returns
    (outputs, log_file, succeeded) — the histogram file is picked up by
    the outputs glob patterns, not returned.

    Two source modes:
    - `tarball`: extract the cnf.*.tar (built by g4bl_jobdef build tool) to a
      scratch dir; treat extracted `work/` as embed_dir. This is the grid path
      since /exp/mu2e/app is not mounted on workers.
    - `embed_dir`: read the lattice files directly from local fs. Local-only
      smoke path; prefer tarball mode for any real run.

    Streams g4bl stdout/stderr to a SAM-named log file
    (`log.mu2e.<desc>.<dsconf>.<sequencer>.log`) in addition to the runner's
    stdout. The log file is always returned (and exists) if exec started, even
    on g4bl failure — push it via push_logs(log_file=...) so failed jobs are
    debuggable in SAM. Raises RuntimeError on prep failures (missing tarball,
    missing embed_dir, missing main_input) — no log produced in those cases.

    Sequencer: First_Event = job_index * events_per_job + 1 (0-based).
    """
    job_index, sequencer = _job_index_from_fname(fname)

    # Tarball mode (grid path): runtime config lives in jobpars.json INSIDE
    # the tarball — the tarball is self-describing. Embed_dir mode (local
    # smoke) has no tarball, so config is on the jobdesc entry.
    tarball = jobdesc_entry.get('tarball')
    if tarball:
        # On grid workers the tarball arrives only as a basename in the POMS
        # map; _fetch_file_local mdh-copies it from dCache when not local.
        _fetch_file_local(tarball)
        extract_dir = tempfile.mkdtemp(prefix='g4bl_extract_')
        with tarfile.open(tarball) as t:
            t.extractall(extract_dir)
        embed_dir = os.path.join(extract_dir, 'work')
        if not Path(embed_dir).is_dir():
            raise RuntimeError(f"tarball missing 'work/' subdir: {tarball}")
        jobpars_path = os.path.join(extract_dir, 'jobpars.json')
        if not Path(jobpars_path).is_file():
            raise RuntimeError(f"tarball missing jobpars.json: {tarball}")
        with open(jobpars_path) as f:
            jobpars = json.load(f)
        # Required keys in jobpars.json — fail loudly if any is missing.
        desc = jobpars['desc']
        dsconf = jobpars['dsconf']
        main_input = jobpars['main_input']
        events_per_job = int(jobpars['events_per_job'])
    else:
        embed_dir = jobdesc_entry['embed_dir']
        desc = jobdesc_entry['desc']
        dsconf = jobdesc_entry['dsconf']
        main_input = jobdesc_entry['main_input']
        events_per_job = int(jobdesc_entry['events_per_job'])
        if not Path(embed_dir).is_dir():
            raise RuntimeError(f"embed_dir not found: {embed_dir}")

    if not (Path(embed_dir) / main_input).is_file():
        raise RuntimeError(f"main_input not found: {embed_dir}/{main_input}")

    first_event = job_index * events_per_job + 1

    # SAM-named histogram + log files. `nts.` (simulation ntuple) is the
    # canonical Mu2e tier for ROOT TTrees from a sim job; matches the
    # metacat naming convention used everywhere else.
    histo_file = str(Mu2eName.build(tier='nts', owner='mu2e', description=desc,
                                    dsconf=dsconf, sequencer=sequencer, extension='root'))
    histo_path = os.path.abspath(histo_file)
    log_file = str(Mu2eName.build(tier='log', owner='mu2e', description=desc,
                                  dsconf=dsconf, sequencer=sequencer, extension='log'))
    log_path = os.path.abspath(log_file)

    # Native AL9 g4bl via spack. spack is a shell function defined by
    # setupmu2e-art.sh; `spack load g4beamline` directly fails to propagate
    # PATH in non-interactive shells, so use `eval $(spack load --sh ...)`.
    # No apptainer wrap, no SL7 container — workers already run on AL9
    # (fnal-wn-el9) per the standard fermigrid.cfg outer container.
    # `unset PYTHON*` avoids subprocess-leaked vars (PYTHONHOME/PATH from
    # the runmu2e Python) confusing spack (which is itself Python and looks
    # up packages via its own site-packages). Same class of leak we hit
    # with apptainer; fixed there with --cleanenv, here by selective unset.
    # CLI keyword syntax: plain `key=value`, NOT `param key=value` (the
    # `param` form is input-file syntax; g4bl 3.08b rejects it on the
    # command line, unlike the older 3.08 SL7 build that was lenient).
    inner_script = (
        # Unset SPACK_ENV first: if the parent shell did `muse setup ops`
        # (the typical art-runner setup), SPACK_ENV=...ops-019... is
        # inherited via subprocess. `spack load g4beamline` then searches
        # only the ops-019 environment — which doesn't contain g4beamline —
        # and fails with "Spec 'g4beamline' matches no installed packages".
        # The Mu2e wiki notes this as a known limitation ("after muse setup
        # it is no longer possible to spack load a package"). Discovered
        # 2026-04-28 after env-leak debugging.
        "unset SPACK_ENV PYTHONHOME PYTHONPATH PYTHONNOUSERSITE\n"
        "source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1\n"
        'eval "$(spack load --sh g4beamline)"\n'
        f"cd {shlex.quote(embed_dir)}\n"
        f"g4bl {shlex.quote(main_input)} viewer=none "
        f"First_Event={first_event} Num_Events={events_per_job} "
        f"histoFile={shlex.quote(histo_path)}"
    )
    cmd_list = ['bash', '-c', inner_script]

    print(f"g4bl: running natively (spack-loaded g4beamline on AL9)")
    print(f"  events_per_job={events_per_job}, first_event={first_event}")
    print(f"  histo_file={histo_path}")
    print(f"  log_file={log_path}")

    # Stream g4bl stdout/stderr to BOTH the runner's stdout AND the SAM log
    # file. Real-time visibility for the operator + persisted log for SAM push.
    proc = subprocess.Popen(cmd_list, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    with open(log_path, 'w') as log_f:
        for line in proc.stdout:
            log_f.write(line)
            sys.stdout.write(line)
            sys.stdout.flush()
    proc.stdout.close()
    rc = proc.wait()

    return jobdesc_entry['outputs'], log_file, (rc == 0)

def push_data(outputs, infiles, simjob_setup=None, track_parents=True):
    """Handle data file management and submission using wildcard patterns from JSON outputs.

    Args:
        outputs: List of output specifications (dataset pattern, location)
        infiles: Space-separated list of input files (for parents_list.txt)
        simjob_setup: Path to SimJob setup script for art environment
        track_parents: When True (default), writes parents_list.txt from
            infiles and points output.txt at it. When False, writes
            'none' in output.txt's third column and skips parents_list.txt
            entirely — use for jobs whose inputs aren't SAM-registered
            (e.g. cvmfs files via `inloc: dir:<path>`). printJson --parents
            exits 25 on non-SAM parents, which cascades into
            KeyError('checksum') inside pushOutput; this bool avoids that.
    """

    parents_field = "parents_list.txt" if track_parents else "none"

    if track_parents:
        Path("parents_list.txt").write_text(infiles.replace(" ", "\n") + "\n")

    # Build output specifications. A job's own inputs are never outputs:
    # in direct-input mode the fetched input art file sits in cwd, so a
    # broad outputs glob (e.g. '*.art') would otherwise declare it for
    # push — and pushOutput, finding the original already at its dataset
    # path, treats it as a stale orphan and tries to DELETE production
    # data (smoke cluster 29444911; only the token scope blocked it).
    parent_names = {Path(p).name for p in infiles.split()} if infiles else set()
    output_specs = []
    for output in outputs:
        dataset_pattern = output['dataset']
        location = output['location']
        matching_files = [f for f in glob.glob(dataset_pattern)
                          if Path(f).name not in parent_names]
        print(f"Pattern '{dataset_pattern}' matched {len(matching_files)} files: {matching_files}")
        for filename in matching_files:
            output_specs.append((location, filename, parents_field))

    # Use generic push function
    return push_output(output_specs, "output.txt", simjob_setup=simjob_setup)

def push_logs(fcl=None, simjob_setup=None, log_file=None, location="disk"):
    """Handle log file management and submission.

    Either pass `fcl` (log filename derived via replace_file_extensions, the
    art-side convention) or `log_file` directly (g4bl runner provides the SAM
    name explicitly). At least one must be set.

    Args:
        fcl: FCL filename to derive log filename from (art convention).
        simjob_setup: Path to SimJob setup script for art environment.
        log_file: Explicit log filename. Wins over `fcl` if both given. The
            g4bl path uses this since there's no FCL.
        location: pushOutput destination class — "disk" (default, persistent),
            "scratch", or "tape". User runs may need "scratch" because
            non-mu2epro accounts typically lack `storage.modify` scope on
            `/mu2e/persistent/datasets/usr-etc/log/<owner>/`.
    """

    if log_file is not None:
        logfile = log_file
    elif fcl is not None:
        logfile = replace_file_extensions(fcl, "log", "log")
    else:
        print("Warning: push_logs called with neither fcl nor log_file; nothing to push")
        return 0

    # Copy jobsub log if available (only meaningful when we derived from fcl
    # and JOBSUB_LOG_FILE is the canonical source; for explicit log_file the
    # runner has already streamed to it).
    jsb_tmp = os.getenv("JSB_TMP")
    if jsb_tmp and log_file is None:
        src = os.path.join(jsb_tmp, "JOBSUB_LOG_FILE")
        print(f"Copying jobsub log from {src} to {logfile}")
        try:
            shutil.copy(src, logfile)
        except FileNotFoundError:
            print(f"Warning: Jobsub log not found at {src}")

    # Push log if it exists
    if Path(logfile).exists():
        # Name parents_list.txt only if it is actually on disk. pushOutput
        # reports `ERROR - parents file ... not found` and then exits 0, so
        # naming a missing file makes the log push a SILENT no-op and the
        # log never reaches SAM.
        #
        # push_data writes it, and there are two routine ways it is absent:
        #   - mu2e failed, so push_data was skipped entirely (the failure
        #     path — exactly when the log is the only evidence left)
        #   - track_parents=False (inloc `dir:`, non-SAM inputs), where
        #     push_data deliberately skips it even on success
        # G4bl passes log_file and never has SAM parents.
        parents = ("parents_list.txt"
                   if log_file is None and Path("parents_list.txt").is_file()
                   else "none")
        output_specs = [(location, logfile, parents)]
        return push_output(output_specs, "log_output.txt", simjob_setup=simjob_setup)
    else:
        print(f"Warning: Log file {logfile} not found, skipping log push")
        return 0


# ============================================================
# Direct mode (jobsub_submit, no POMS) — Phase 2 v1
#
# POMS mode is invoked with --jobdesc <json> and the per-job index
# encoded in the `fname` env var (sequencer field).
# Direct mode is detected by presence of MU2EGRID_JOBDEF in the
# environment (set by the jobsub_submit argv). The submitter ships
# the cnf tarball + an "ops JSON" via dropbox, both landing under
# $CONDOR_DIR_INPUT. Index resolves via ops['jobs'][PROCESS].
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
    array), inspec (dataset → [protocol, location]), jobdesc (a one-element
    list mirroring the POMS-map entry shape for reuse via process_jobdef)."""
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
    """Append the SHA256 manifest block to the log file in a format that
    `mu2eClusterCheckAndMove` can parse. Faithful port of `addManifest`
    from mu2egrid::impl/mu2ejobsub.sh:44-56.

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

    # diskUse from `du -ks` — keep raw output (kbytes \t path) for byte-exact
    # parity with the bash addManifest reference; regex-based parsers match
    # `^mu2egrid diskUse = (\d+)` regardless of trailing content.
    du = subprocess.run(['du', '-ks'], capture_output=True, text=True, check=False)
    du_out = du.stdout.rstrip('\n') if du.stdout else '0'

    # ls -al with C locale so positional fields are stable
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

    # Selfcheck: sha256sum of the file's content (everything written so far),
    # emitted in `sha256sum < log` format ("  -" trailer, no filename).
    sc = hashlib.sha256()
    with log.open('rb') as g:
        for chunk in iter(lambda: g.read(1 << 20), b''):
            sc.update(chunk)
    with log.open('a') as f:
        f.write(f"# mu2egrid manifest selfcheck: {sc.hexdigest()}  -\n")


def _is_terminal_push_error(output):
    """True when pushOutput failed for a reason no retry can ever clear.

    The known case: the target already exists on tape from an earlier
    push that copied the file but never finished declaring it to SAM.
    pushOutput's `recover` path then tries to gfal-rm the orphan so it
    can rewrite it, and /pnfs/mu2e/tape is write-once — the delete 403s
    every single time. Retrying re-runs mu2e for hours and dies at the
    identical step, so the attempt cap is spent for nothing (three full
    mixing jobs on CeMLeadingLog 2/418, 2026-07-27).

    Deliberately narrow. A bare 403 is NOT enough: a 403 on the *write*
    means a missing storage.modify scope, a different diagnosis with a
    different remedy. Only the delete-during-recover pattern qualifies.
    Unknown or unavailable output stays retryable — misclassifying a
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

    Terminal failures (see _is_terminal_push_error) skip the retries
    entirely and raise on the first attempt — retrying them only burns
    grid time and delays the human who has to intervene."""
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


def _push_all(data_push, log_push, prefix=''):
    """Run `data_push`, then `log_push` — and run `log_push` even when
    `data_push` raises.

    A data-push failure is precisely when the log is the only surviving
    evidence, so it must not be what skips the log. Observed 2026-07-27
    on CeMLeadingLog indices 2 and 418: attempt 1 left a partly-written
    file on tape without finishing its SAM declaration, and every retry
    then ran mu2e to completion (hours of CPU) only for pushOutput's
    `recover` path to try `gfal-rm` on the orphan — HTTP 403, because
    tape is write-once. pushOutput exited 2, the CalledProcessError
    propagated out of _push_with_retry, and the log push below it never
    ran. Three attempts produced zero forensic trace in SAM.

    Failure precedence: the data-push exception always wins, since a
    log-push failure is a symptom next to it. When the data push
    succeeded, a log-push failure still fails the job (CB2 — never
    silently leave a file unregistered).
    """
    data_exc = None
    try:
        data_push()
    except subprocess.CalledProcessError as exc:
        data_exc = exc
        print(f"{prefix}data push failed (rc={exc.returncode}) — pushing the "
              f"log before failing the job")

    try:
        log_push()
    except subprocess.CalledProcessError as exc:
        if data_exc is None:
            raise
        print(f"{prefix}WARNING: log push also failed (rc={exc.returncode}); "
              f"re-raising the data-push failure")

    if data_exc is not None:
        raise data_exc


def _execute_mu2e(fcl, simjob_setup, args, prefix=''):
    """Shared execute step for both backends: build the mu2e command, run
    it, return True iff it failed. Callers push data only on success but
    always push logs (so failures stay debuggable) — that split, and the
    direct-mode extras (retry, manifest, log location), stay caller-side
    because they differ by design."""
    cmd = build_mu2e_cmd(fcl, simjob_setup, args)
    print(f"{prefix}Executing: {cmd}")
    print(f"{prefix}Working dir: {os.getcwd()}, FCL exists: {os.path.exists(fcl)}")
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
    """Direct-mode equivalent of _dispatch_and_execute: run the entry's
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
        inloc = jobdesc[0].get('inloc')
        # Stage the input locally (direct mode has no POMS pre-staging, and
        # direct-input FCL has no xroot streaming fallback — it writes the
        # bare local filename, so every draining input must be fetched).
        # Resolve the file's REAL location via SAM rather than trusting
        # _fetch_file_local's 'disk' default (the cnf-tarball convention) —
        # every draining entry example ships inloc='tape'. Mirrors the
        # single-file resolution process_jobdef's copy_input branch uses
        # for its inputs (runmu2e.py ~356-366).
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
                  f"draining batches (submit_map --files); template/"
                  f"g4bl via the upstream mu2ejobsub/mu2eg4bl CLIs.")
            sys.exit(1)
        fname = _synthesize_direct_fname(index)
        fcl, simjob_setup, infiles, outputs, inloc = process_jobdef(
            jobdesc, fname, args)

    # `dir:<path>` inloc means inputs come from a locally-mounted FS and
    # have no SAM parents — match the POMS-mode logic in _dispatch_and_execute.
    track_parents = not (isinstance(inloc, str) and inloc.startswith('dir:'))

    job_failed = _execute_mu2e(fcl, simjob_setup, args, prefix='[direct] ')

    # Append SHA256 manifest to the log BEFORE pushing.
    # mu2eClusterCheckAndMove parses the log for `mu2egrid manifest`.
    log_file = replace_file_extensions(fcl, "log", "log")
    if Path(log_file).exists():
        manifest_files = []
        if not job_failed:
            for o in outputs:
                pattern = o['dataset']
                manifest_files.extend(sorted(Path('.').glob(pattern)))
        _emit_manifest(log_file, [str(f) for f in manifest_files])

    # Logs share the first output's location so the worker token's
    # storage.modify scope covers both. Without this, a non-mu2epro account
    # whose data outputs go to `scratch` would still try to push the log
    # to `disk` (push_logs default), which `/mu2e/persistent/datasets/...`
    # doesn't grant. Production runs as mu2epro keep `disk` via the same
    # mechanism — the cnf's outputs[] specifies where data lands.
    log_location = log_storage_location(outputs)

    def data_push():
        if job_failed:
            return
        _push_with_retry(push_data, outputs, infiles,
                         simjob_setup=simjob_setup, track_parents=track_parents)

    def log_push():
        _push_with_retry(push_logs, fcl, simjob_setup=simjob_setup,
                         location=log_location)

    if job_failed:
        print("[direct] mu2e failed — skipping data push, still pushing log")
    # Push outputs only on success; the log ALWAYS — including when the
    # data push itself raises (see _push_all).
    _push_all(data_push, log_push, prefix='[direct] ')

    return job_failed


def _direct_main(args):
    """Entry for direct mode. Resolves index, dispatches via _direct_dispatch."""
    ops = _load_direct_ops()
    index = _resolve_direct_index(ops)

    print(f"[direct] PROCESS={os.environ.get('PROCESS', '0')} → job index {index}")
    print(f"[direct] jobdef={os.environ.get('MU2EGRID_JOBDEF')}")

    # Ensure the cnf tarball is reachable. -f dropbox:// drops it under
    # $CONDOR_DIR_INPUT but process_jobdef expects basename in cwd
    # (it calls _fetch_file_local which is a no-op if already local).
    jobdef_basename = os.environ['MU2EGRID_JOBDEF']
    if not Path(jobdef_basename).is_file():
        src = Path(_direct_input_dir()) / jobdef_basename
        if src.is_file():
            os.symlink(src, jobdef_basename)
        # else: process_jobdef will _fetch_file_local() from SAM as a fallback.

    # Inputs stream via xroot by default, matching the POMS-era worker
    # (its launch template never passed --copy-input). A map entry opts
    # in to local staging with "copy_input": true, read in
    # process_jobdef. Forcing args.copy_input = True here (mu2ejobsub.sh
    # stage-in parity) was reverted 2026-08-02.

    if _direct_dispatch(args, ops, index):
        sys.exit(1)



def _dispatch_and_execute(mode, jobdesc, fname, args):
    """Dispatch on runner mode, prep, execute, push. Returns True iff the
    execute step failed (so main can exit nonzero).

    Encapsulates the per-runner asymmetry in one place:
    - art runners (template / direct_input / normal) return an FCL which
      this function then executes via `mu2e -c`.
    - g4bl runner executes inside `process_g4bl_jobdef`; this function
      only pushes its outputs.
    """
    # G4Beamline: process_g4bl_jobdef both prepares and executes; it
    # streams stdout to a SAM-named .log file. Push data only on success
    # but always push the log if it exists, so failed grid jobs are
    # debuggable in SAM.
    if mode == 'g4bl':
        try:
            outputs, log_file, succeeded = process_g4bl_jobdef(jobdesc[0], fname, args)
        except RuntimeError as e:
            print(f"=== g4bl prep failed: {e} ===")
            return True

        if not succeeded:
            print("=== g4bl execution failed ===")

        if args.dry_run:
            print("[DRY RUN] Would run: pushOutput output.txt")
        else:
            if succeeded:
                push_data(outputs, infiles="", simjob_setup=None, track_parents=False)
            else:
                print("g4bl failed - skipping data push, attempting log push")
            if log_file and Path(log_file).is_file():
                push_logs(log_file=log_file, simjob_setup=None)

        return not succeeded

    # Art runners: prep returns FCL; execute `mu2e -c` here.
    inloc = None  # populated by process_jobdef; None for template/direct_input
    if mode == 'template':
        fcl, simjob_setup = process_template(jobdesc[0], fname)
        infiles = fname
        outputs = jobdesc[0]['outputs']
    elif mode == 'direct_input':
        fcl, simjob_setup, infiles, outputs = process_direct_input(jobdesc, fname, args)
    else:
        fcl, simjob_setup, infiles, outputs, inloc = process_jobdef(jobdesc, fname, args)

    # dir:<path> inloc means inputs are on a locally-mounted filesystem
    # (typically cvmfs) and aren't SAM-registered — skip parent tracking
    # on the push. All other cases (including None for template / direct
    # input) default to tracking parents.
    track_parents = not (isinstance(inloc, str) and inloc.startswith('dir:'))

    job_failed = _execute_mu2e(fcl, simjob_setup, args)

    if not args.dry_run:
        def data_push():
            if job_failed:
                return
            push_data(outputs, infiles, simjob_setup=simjob_setup,
                      track_parents=track_parents)

        if job_failed:
            print("Job failed - skipping data file push, but uploading logs")
        # Always upload logs, even on failure — including when push_data
        # itself raises, which used to skip this entirely (see _push_all).
        _push_all(data_push, lambda: push_logs(fcl, simjob_setup=simjob_setup))
    else:
        print("[DRY RUN] Would run: pushOutput output.txt")

    return job_failed


def main():
    parser = argparse.ArgumentParser(description="Execute production jobs from job definitions.")
    parser.add_argument("--copy-input", action="store_true", help="Copy input files using mdh")
    parser.add_argument('--dry-run', action='store_true', help='Print commands without actually running pushOutput')
    parser.add_argument('--nevts', type=int, default=-1, help='Number of events to process (-1 for all events, default: -1)')
    parser.add_argument('--mu2e-options', type=str, default='', help='Extra options to pass to mu2e command (e.g., "--no-timing --debug")')
    parser.add_argument('--jobdesc', help='Path to the job descriptions JSON file (e.g., jobdefs_list.json). Required for POMS mode; ignored in direct mode (MU2EGRID_JOBDEF set).')

    args = parser.parse_args()

    if _is_direct_mode():
        _direct_main(args)
        return

    if not args.jobdesc:
        print("Error: --jobdesc is required (or set MU2EGRID_JOBDEF for direct mode)")
        sys.exit(1)

    with open(args.jobdesc, 'r') as f:
        jobdesc = json.load(f)
    mode = validate_jobdesc(jobdesc)

    fname = os.getenv("fname")
    if not fname:
        print("Error: fname environment variable is not set.")
        sys.exit(1)

    if _dispatch_and_execute(mode, jobdesc, fname, args):
        sys.exit(1)


if __name__ == "__main__":
    main()
