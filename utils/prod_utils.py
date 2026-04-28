import subprocess
import sys
import logging
import json
import os
import re
import shlex
from pathlib import Path
from datetime import datetime
from .jobfcl import Mu2eJobFCL
from .jobdef import create_jobdef

# Default SL7 container for G4Beamline runs (overridable via JSON `container` field).
# G4Beamline binaries on cvmfs are linked against SL7 libs; running on AL9 hosts
# requires apptainer-wrapping. On grid, set the same image as the outer container
# in poms/g4bl.cfg's +SingularityImage so no wrapping is needed at runtime.
DEFAULT_G4BL_CONTAINER = "/cvmfs/singularity.opensciencegrid.org/fermilab/fnal-dev-sl7:latest"

def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="[%(levelname)s] %(message)s"
    )
    
    # Suppress debug messages from external libraries when verbose is enabled
    if verbose:
        # Suppress requests library debug messages
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("requests").setLevel(logging.WARNING)
        # Suppress samweb_client debug messages
        logging.getLogger("samweb_client").setLevel(logging.WARNING)

def run(cmd, shell=False, retries=0, retry_delay=60):
    """
    Run a shell command with real-time output streaming.
    If shell=True, cmd is a string.
    retries: number of retry attempts (0 = no retries, just run once)
    retry_delay: seconds to wait between retries
    Returns the exit code (0 for success) or raises CalledProcessError for failure.
    """
    import time
    attempts = retries + 1
    for attempt in range(1, attempts + 1):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] Running: {cmd}")

        # Real-time streaming
        process = subprocess.Popen(cmd, shell=shell, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in iter(process.stdout.readline, ''):
            print(line.rstrip())
            sys.stdout.flush()

        process.stdout.close()
        return_code = process.wait()

        if return_code == 0:
            return return_code

        if attempt < attempts:
            print(f"[{timestamp}] Command failed (attempt {attempt}/{attempts}), retrying in {retry_delay}s...")
            time.sleep(retry_delay)
        else:
            raise subprocess.CalledProcessError(return_code, cmd)
    return return_code




def write_fcl(jobdef, inloc='tape', proto='root', index=0, target=None):
    """
    Generate and write an FCL file using mu2ejobfcl.
    """
    # Extract fcl filename from jobdef and write to current directory
    jobdef_name = Path(jobdef).name  # Get just the filename, not the full path
    fcl = re.sub(r'\.\d+\.tar$', f'.{index}.fcl', jobdef_name)  # cnf.mu2e.RPCInternalPhysical.MDC2020az.{index}.fcl
    
    # Print Perl equivalent command
    perl_cmd = f"mu2ejobfcl --jobdef {jobdef} --default-location {inloc} --default-protocol {proto}"
    if target:
        perl_cmd += f" --target {target}"
    else:
        perl_cmd += f" --index {index}"
    perl_cmd += f" > {fcl}"
    print(f"Running Perl equivalent of:")
    print(f"{perl_cmd}")
    
    # Use Python mu2ejobfcl implementation
    try:
        job_fcl = Mu2eJobFCL(jobdef, inloc=inloc, proto=proto)
        
        # Find job index
        if target:
            job_index = job_fcl.find_index(target=target)
        else:
            job_index = job_fcl.find_index(index=index)
        
        # Generate FCL content
        result = job_fcl.generate_fcl(job_index)
        
        print(f"Wrote {fcl}")
        with open(fcl, 'w') as f:
            f.write(result + '\n')
        
        # Print the FCL content
        print(f"\n--- {fcl} content ---")
        print(result + '\n')

        return fcl
    
    except Exception as e:
        print(f"Error generating FCL: {e}")
        raise

def get_def_counts(dataset, include_empty=False):
    """Get file count and event count for a dataset."""
    from .samweb_wrapper import count_files, list_files
    
    # Count files
    query = f"defname: {dataset}" if include_empty else f"defname: {dataset} and event_count>0"
    nfiles = count_files(query)
    
    # Count events
    result = list_files(f"dh.dataset={dataset}", summary=True)
    nevts = 0
    if isinstance(result, dict):
        nevts = result.get('total_event_count', 0) or 0
    elif isinstance(result, list):
        # Handle list result (when summary=False)
        nevts = len(result)  # Fallback to file count
    else:
        # Handle string result (fallback)
        for line in result.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "Event":
                nevts = int(parts[2])
                break
    
    if nfiles == 0:
        sys.exit(f"No files found in dataset {dataset}")
    return nfiles, nevts

def calculate_merge_factor(fields):
    """Calculate merge factor from input_data dict.
    
    The input_data should be a dict mapping dataset names to merge factors.
    Returns the merge factor from the first dataset in the dict.
    """
    # input_data must be a dict, use the first dataset's merge factor
    input_data = fields.get('input_data')
    if not isinstance(input_data, dict):
        raise ValueError(f"input_data must be a dict, got {type(input_data)}")
    
    value = list(input_data.values())[0]

    if isinstance(value, dict):
        if 'split_lines' in value:
            # split_lines means "split a local text file into N-line chunks;
            # each job consumes one chunk" — merge_factor is implicitly 1.
            return 1
        if 'count' in value:
            return int(value['count'])
        if 'merge_factor' in value:
            return int(value['merge_factor'])
        raise ValueError("input_data dict spec must include 'count', 'merge_factor', or 'split_lines'")

    return int(value)

# Removed duplicate find_json_entry; use json2jobdef.load_json + json2jobdef.find_json_entry

def write_fcl_template(base, overrides):
    """
    Write FCL template file with just an include directive and overrides.
    
    Args:
        base: Base FCL file to include
        overrides: Dictionary of FCL overrides
    """
    with open('template.fcl', 'w') as f:
        # Write just the include directive for the base FCL
        f.write(f'#include "{base}"\n')
        
        # Add overrides
        for key, val in overrides.items():
            if key == '#include':
                includes = val if isinstance(val, list) else [val]
                for inc in includes:
                    f.write(f'#include "{inc}"\n')
            else:
                # Use json.dumps for all values to ensure proper FCL formatting
                # (strings get quotes, lists get proper syntax with double quotes)
                f.write(f'{key}: {json.dumps(val)}\n')

def replace_file_extensions(input_str, first_field, last_field):
    """Replace the first and last fields in a dot-separated string."""
    fields = input_str.split('.')
    fields[0] = first_field
    fields[-1] = last_field
    return '.'.join(fields)

def create_index_definition(output_index_dataset, job_count, input_index_dataset):
        
    from .samweb_wrapper import delete_definition, create_definition, describe_definition
    
    idx_name = f"i{output_index_dataset}"
    idx_format = f"{job_count:07d}"
    
    # Check if definition exists before trying to delete it
    try:
        describe_definition(idx_name)
        print(f"Definition {idx_name} exists, attempting to delete...")
        delete_definition(idx_name)
        print(f"Successfully deleted {idx_name}")
    except Exception as e:
        print(f"Definition {idx_name} does not exist, skipping deletion")

    # Create the new definition
    print(f"Creating definition {idx_name}...")
    create_definition(idx_name, f"dh.dataset {input_index_dataset} and dh.sequencer < {idx_format}")
    describe_definition(idx_name)

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
        print("Error: No job descriptions found in jobdesc file")
        sys.exit(1)

    # Check if g4bl runner (has runner: 'g4bl' field)
    if jobdesc[0].get('runner') == 'g4bl':
        if len(jobdesc) > 1:
            print("Error: g4bl runner requires exactly one entry in jobdesc list")
            sys.exit(1)
        entry = jobdesc[0]
        required_fields = ['embed_dir', 'main_input', 'events_per_job', 'outputs']
        for field in required_fields:
            if field not in entry:
                print(f"Error: g4bl runner requires '{field}' field")
                sys.exit(1)
        return 'g4bl'

    # Check if template mode (has fcl_template field)
    if 'fcl_template' in jobdesc[0]:
        if len(jobdesc) > 1:
            print("Error: Template mode (fcl_template) requires exactly one entry in jobdesc list")
            print(f"Found {len(jobdesc)} entries. Template mode processes one file at a time.")
            sys.exit(1)
        entry = jobdesc[0]
        required_fields = ['fcl_template', 'setup_script', 'inloc', 'outputs']
        for field in required_fields:
            if field not in entry:
                print(f"Error: Template mode requires '{field}' field")
                sys.exit(1)
        return 'template'

    # Check if direct-input mode: tarball present but no njobs
    if 'tarball' in jobdesc[0] and 'njobs' not in jobdesc[0]:
        if len(jobdesc) > 1:
            print("Error: Direct-input mode requires exactly one entry in jobdesc list")
            print(f"Found {len(jobdesc)} entries.")
            sys.exit(1)
        entry = jobdesc[0]
        required_fields = ['tarball', 'inloc', 'outputs']
        for field in required_fields:
            if field not in entry:
                print(f"Error: Direct-input mode requires '{field}' field")
                sys.exit(1)
        return 'direct_input'

    # Normal mode validation
    # Entries with tarball but no njobs are generic tarballs - skip in normal dispatch
    # Entries missing tarball entirely are invalid
    for i, entry in enumerate(jobdesc):
        if 'njobs' not in entry:
            if 'tarball' in entry:
                print(f"[INFO] entry {i} ({entry['tarball']}) has no njobs (generic tarball) - skipped in normal dispatch")
                continue
            print(f"Error: Normal mode requires 'njobs' field in jobdesc entry {i}")
            sys.exit(1)
        required_fields = ['tarball', 'inloc', 'outputs']
        for field in required_fields:
            if field not in entry:
                print(f"Error: Normal mode requires '{field}' field in jobdesc entry {i}")
                sys.exit(1)

    return False

def process_template(jobdesc_entry, fname):
    """Process a job in template mode.
    
    Args:
        jobdesc_entry: Job description dictionary
        fname: Input filename
        
    Returns:
        tuple: (fcl, simjob_setup)
    """
    import re
    
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
    
    # Parse variables from input filename (format: prefix.owner.desc.dsconf.sequencer.ext)
    # Extract filename from full path and split into parts
    fname_base = Path(fname).name  # Get just the filename, not the full path
    parts = fname_base.split('.')  # Split by dots: ['dig', 'mu2e', 'CosmicSignalTriggered', 'MDC2025ad', '001430_00000000', 'art']
    if len(parts) != 6:
        raise RuntimeError(f"Invalid filename format: {fname_base}. Expected exactly 6 dot-separated fields (prefix.owner.desc.dsconf.sequencer.ext).")
    
    template_vars = {
        'owner': parts[1],      # mu2e
        'desc': parts[2],       # CosmicSignalTriggered
        'dsconf': parts[3],     # MDC2025ad
        'sequencer': parts[4]   # 001430_00000000
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
    from .jobquery import Mu2eJobPars

    jobdesc_entry = jobdesc[0]
    tarball = jobdesc_entry['tarball']

    # Parse fname components: prefix.owner.desc.dsconf.sequencer.ext
    fname_base = Path(fname).name
    parts = fname_base.split('.')
    if len(parts) != 6:
        print(f"Error: Invalid filename format: {fname_base}. "
              f"Expected prefix.owner.desc.dsconf.sequencer.ext")
        sys.exit(1)
    desc = parts[2]
    seq = parts[4]

    print(f"Direct-input mode: fname={fname}, desc={desc}, seq={seq}")

    # Download tarball if not already local
    if not Path(tarball).is_file():
        run(f"mdh copy-file -e 3 -o -v -s disk -l local {tarball}", shell=True,
            retries=3, retry_delay=60)

    # Extract base FCL from tarball and resolve output filenames
    job_fcl = Mu2eJobFCL(tarball)
    base_fcl = job_fcl._extract_fcl()
    outputs_map = job_fcl.job_outputs(0, override_desc=desc, override_seq=seq)

    # Write FCL: base content + direct-input overrides appended
    # FHiCL last-definition-wins semantics handle the override
    fname_stem = Path(fname).stem  # strip .art
    fcl = f"{fname_stem}.fcl"
    with open(fcl, 'w') as f:
        f.write(base_fcl)
        f.write("\n# Direct-input overrides:\n")
        f.write(f'source.fileNames: ["{fname}"]\n')
        for key, filename in outputs_map.items():
            f.write(f'{key}: "{filename}"\n')

    print(f"Wrote {fcl}")
    print(f"\n--- {fcl} content ---")
    with open(fcl) as f:
        print(f.read())

    # Extract setup script from tarball
    try:
        jp = Mu2eJobPars(tarball)
        simjob_setup = jp.setup()
        print(f"Job setup script: {simjob_setup}")
    except Exception as e:
        print(f"ERROR: Failed to get job setup information from {tarball}")
        print(f"Exception: {e}")
        raise

    outputs = jobdesc_entry['outputs']
    return fcl, simjob_setup, fname, outputs


def process_jobdef(jobdesc, fname, args):
    """Process a job in normal mode.
    
    Args:
        jobdesc: List of job descriptions
        fname: Index filename
        args: Command line arguments (needs copy_input attribute)
        
    Returns:
        tuple: (fcl, simjob_setup, infiles, outputs)
    """
    from .jobiodetail import Mu2eJobIO
    from .jobquery import Mu2eJobPars
    from .samweb_wrapper import locate_file_full
    
    # Extract job index from filename
    try:
        job_index = int(fname.split('.')[4].lstrip('0') or '0')
    except (IndexError, ValueError) as e:
        print(f"Error: Unable to extract job index from filename: {e}")
        sys.exit(1)
    
    # Find which job description this job index belongs to
    cumulative_jobs = 0
    jobdesc_entry = None
    jobdesc_index = None
    
    for i, entry in enumerate(jobdesc):
        if 'njobs' not in entry:
            continue  # skip generic tarball entries
        if job_index < cumulative_jobs + entry['njobs']:
            jobdesc_entry = entry
            jobdesc_index = i
            break
        cumulative_jobs += entry['njobs']
    
    if jobdesc_entry is None:
        total_jobs = sum(d.get('njobs', 0) for d in jobdesc)
        print(f"Error: Job index {job_index} out of range. Total jobs available: {total_jobs}")
        sys.exit(1)
    
    print(f"Job {job_index} uses definition {jobdesc_index}")
    print(f"Global job index: {job_index}, Local job index within definition: {job_index - cumulative_jobs}")
    
    # Calculate local job index within this specific job definition
    job_index_num = job_index - cumulative_jobs
    
    # Extract fields from JSON structure
    inloc = jobdesc_entry['inloc']
    tarball = jobdesc_entry['tarball']

    # Copy jobdef to local directory if not already local
    if not Path(tarball).is_file():
        run(f"mdh copy-file -e 3 -o -v -s disk -l local {tarball}", shell=True, retries=3, retry_delay=60)

    # If jobpars declares chunk_mode, materialize this job's slice before
    # mu2e runs. runmu2e reads tbs.chunk_mode = {source, lines, local_filename}
    # and writes the corresponding slice of the cvmfs source to local_filename
    # in cwd. Every job's FCL references local_filename (set via
    # fcl_overrides at jobdef-creation time), so mu2e reads whatever that
    # file contains when it opens.
    import shlex
    jp_for_chunk = Mu2eJobPars(tarball)
    tbs = jp_for_chunk.json_data.get('tbs', {}) if isinstance(jp_for_chunk.json_data, dict) else {}
    chunk_mode = tbs.get('chunk_mode') if isinstance(tbs, dict) else None
    if isinstance(chunk_mode, dict):
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
    job_io = Mu2eJobIO(tarball)
    inputs = job_io.job_inputs(job_index_num)
    # Flatten the dictionary values into a single list
    all_files = []
    for file_list in inputs.values():
        all_files.extend(file_list)
    infiles = " ".join(all_files)
    
    # Generate FCL - Normal mode with local input copy
    # Stash files are on CVMFS and resilient files use xrootd — no local copying needed
    if args.copy_input and infiles.strip() and inloc not in ("none", "stash", "resilient"):
        print(f"Copying input files locally from {inloc}: {infiles}")
        fcl = write_fcl(tarball, f"dir:{os.getcwd()}/indir", 'file', job_index_num)
        
        # Copy each file individually, detecting actual location from SAMWeb
        run("echo 'Starting to copy input files locally'", shell=True)
        for file in all_files:
            locations = locate_file_full(file)
            if not locations or 'location_type' not in locations[0]:
                raise RuntimeError(f"Could not detect location for file: {file}")
            file_inloc = locations[0]['location_type']
            print(f"Detected location of {file}: {file_inloc}")
            print(f"Copying {file} from {file_inloc}")
            run(f"mdh copy-file -e 3 -o -v -s {file_inloc} -l local {file}", shell=True, retries=3, retry_delay=60)
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
    try:
        jp = Mu2eJobPars(tarball)
        simjob_setup = jp.setup()
        print(f"Job setup script: {simjob_setup}")
    except Exception as e:
        print(f"ERROR: Failed to get job setup information from {tarball}")
        print(f"Exception: {e}")
        raise
    
    outputs = jobdesc_entry['outputs']
    return fcl, simjob_setup, infiles, outputs, inloc


def _is_inside_sl7():
    """True if running inside an SL7 (Scientific Linux 7) container/host."""
    try:
        with open('/etc/redhat-release') as f:
            return 'release 7' in f.read()
    except OSError:
        return False


def process_g4bl_jobdef(jobdesc_entry, fname, args):
    """Run a G4Beamline simulation job.

    Unlike art-side process_jobdef which prepares an FCL for runmu2e to execute,
    this function executes g4bl in-place (no separate `mu2e -c` step). The
    runmu2e.py 'g4bl' branch skips its FCL/mu2e dispatch when this returns.

    Linear sequencer: First_Event = (job_index - 1) * events_per_job + 1.
    """
    parts = Path(fname).name.split('.')
    if len(parts) < 5:
        raise RuntimeError(f"Invalid g4bl fname: {fname}; expected dot-separated index in field 5")
    try:
        job_index = int(parts[4].lstrip('0') or '0')
    except ValueError as e:
        raise RuntimeError(f"Could not parse job index from {fname}: {e}")

    embed_dir = jobdesc_entry['embed_dir']
    main_input = jobdesc_entry['main_input']
    events_per_job = int(jobdesc_entry['events_per_job'])
    container = jobdesc_entry.get('container', DEFAULT_G4BL_CONTAINER)

    if not Path(embed_dir).is_dir():
        raise RuntimeError(f"embed_dir not found: {embed_dir}")
    if not (Path(embed_dir) / main_input).is_file():
        raise RuntimeError(f"main_input not found: {embed_dir}/{main_input}")

    # 0-based job index → events [job_index*N + 1, (job_index+1)*N]
    first_event = job_index * events_per_job + 1

    # Output histogram file: g4bl.<owner>.<desc>.<dsconf>.<sequencer>.root
    desc = jobdesc_entry.get('desc', parts[2] if len(parts) >= 3 else 'g4bl')
    dsconf = jobdesc_entry.get('dsconf', parts[3] if len(parts) >= 4 else 'unknown')
    sequencer = parts[4] if len(parts) >= 5 else f"{job_index:06d}_00000000"
    histo_file = f"g4bl.mu2e.{desc}.{dsconf}.{sequencer}.root"
    histo_path = os.path.abspath(histo_file)

    # Inline bash script passed via `bash -c <multi-line>`. shell=False on the
    # subprocess.run side avoids host /bin/sh quote-mangling. `--cleanenv` on
    # apptainer prevents AL9 env vars (PYTHONHOME, UPS_DIR, PRODUCTS, etc.)
    # from leaking into the SL7 container and breaking setupmu2e-art.sh's UPS
    # init — without it, `setup` ends up undefined despite source returning 0.
    # (Discovered 2026-04-27.)
    inner_script = (
        "source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh\n"
        "setup G4beamline\n"
        f"cd {shlex.quote(embed_dir)}\n"
        f"g4bl {shlex.quote(main_input)} "
        f"Num_Events={events_per_job} First_Event={first_event} "
        f"param histoFile={shlex.quote(histo_path)}"
    )

    if _is_inside_sl7():
        cmd_list = ['bash', '-c', inner_script]
        print(f"g4bl: running natively (already inside SL7)")
    else:
        cmd_list = [
            'apptainer', 'exec', '--cleanenv',
            '-B', '/cvmfs',
            '-B', '/tmp',
            '-B', embed_dir,
        ]
        home = os.environ.get('HOME', '')
        if home and Path(home).is_dir():
            cmd_list += ['-B', home]
        cmd_list += [container, 'bash', '-c', inner_script]
        print(f"g4bl: wrapping in apptainer ({container})")

    print(f"  events_per_job={events_per_job}, first_event={first_event}")
    print(f"  histo_file={histo_path}")
    result = subprocess.run(cmd_list, check=False)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd_list)

    return jobdesc_entry['outputs'], histo_file


def push_output(output_specs, output_file="output.txt", parents_file="parents_list.txt", simjob_setup=None):
    """
    Generic function to push output files.
    
    Args:
        output_specs: List of tuples (location, filename, parents_file)
        output_file: Name of the output specification file
        parents_file: Name of the parents list file (optional)
        simjob_setup: Path to SimJob setup script for art environment
    
    Returns:
        int: Exit code from pushOutput command
    """
    import glob
    
    output_lines = []
    for spec in output_specs:
        location, pattern, parents = spec
        # Handle glob patterns
        matching_files = glob.glob(pattern) if '*' in pattern else [pattern]
        for filename in matching_files:
            if Path(filename).exists():
                output_lines.append(f"{location} {filename} {parents}")
            else:
                print(f"Warning: File not found: {filename}")
    
    if not output_lines:
        print(f"Warning: No files to push for {output_file}")
        return 0
    
    Path(output_file).write_text("\n".join(output_lines) + "\n")
    print(f"Pushing {len(output_lines)} file(s) via {output_file}")
    push_cmd = f"pushOutput {output_file}"
    if simjob_setup:
        push_cmd = f"source {simjob_setup} && {push_cmd}"
    result = run(push_cmd, shell=True)
    if result != 0:
        print(f"Warning: pushOutput returned exit code {result}")
    return result

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
    import glob

    parents_field = "parents_list.txt" if track_parents else "none"

    if track_parents:
        Path("parents_list.txt").write_text(infiles.replace(" ", "\n") + "\n")

    # Build output specifications
    output_specs = []
    for output in outputs:
        dataset_pattern = output['dataset']
        location = output['location']
        matching_files = glob.glob(dataset_pattern)
        print(f"Pattern '{dataset_pattern}' matched {len(matching_files)} files: {matching_files}")
        for filename in matching_files:
            output_specs.append((location, filename, parents_field))

    # Use generic push function
    return push_output(output_specs, "output.txt", parents_field, simjob_setup=simjob_setup)

def push_logs(fcl, simjob_setup=None):
    """Handle log file management and submission.
    
    Args:
        fcl: FCL filename to derive log filename from
        simjob_setup: Path to SimJob setup script for art environment
    """
    import shutil
    
    logfile = replace_file_extensions(fcl, "log", "log")
    
    # Copy jobsub log if available
    jsb_tmp = os.getenv("JSB_TMP")
    if jsb_tmp:
        src = os.path.join(jsb_tmp, "JOBSUB_LOG_FILE")
        print(f"Copying jobsub log from {src} to {logfile}")
        try:
            shutil.copy(src, logfile)
        except FileNotFoundError:
            print(f"Warning: Jobsub log not found at {src}")
    
    # Push log if it exists
    if Path(logfile).exists():
        output_specs = [("disk", logfile, "parents_list.txt")]
        return push_output(output_specs, "log_output.txt", "parents_list.txt", simjob_setup=simjob_setup)
    else:
        print(f"Warning: Log file {logfile} not found, skipping log push")
        return 0