import subprocess
import sys
import logging
import json
import os
from pathlib import Path
from datetime import datetime
from .jobfcl import Mu2eJobFCL
from .jobdef import create_jobdef

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

def run(cmd, shell=False):
    """
    Run a shell command with real-time output streaming.
    If shell=True, cmd is a string.
    Returns the exit code (0 for success) or raises CalledProcessError for failure.
    """
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
    
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, cmd)
    return return_code




def write_fcl(jobdef, inloc='tape', proto='root', index=0, target=None):
    """
    Generate and write an FCL file using mu2ejobfcl.
    """
    # Extract fcl filename from jobdef and write to current directory
    jobdef_name = Path(jobdef).name  # Get just the filename, not the full path
    fcl = jobdef_name.replace('.0.tar', f'.{index}.fcl')  # cnf.mu2e.RPCInternalPhysical.MDC2020az.{index}.fcl
    
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
    """Calculate merge factor based on dataset counts and requested merge_events.
    
    If merge_factor is directly specified, use it.
    Otherwise, calculate using: MERGE_FACTOR = MERGE_EVENTS/npevents + 1
    where npevents = nevts/nfiles (events per file)
    """
    # If merge_factor is directly specified, use it
    if 'merge_factor' in fields:
        return fields['merge_factor']
    
    # Otherwise, calculate from merge_events
    if 'merge_events' not in fields:
        raise KeyError("Either 'merge_factor' or 'merge_events' must be specified")
    
    nfiles, nevts = get_def_counts(fields['input_data'])
    if nfiles == 0:
        raise ValueError(f"Input dataset '{fields['input_data']}' has no files")
    
    # Calculate events per file
    npevents = nevts // nfiles
    
    # Calculate merge factor: MERGE_EVENTS/npevents + 1
    # This ensures we get enough files to cover the requested merge_events
    merge_factor = fields['merge_events'] // npevents + 1
    
    return merge_factor

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
                f.write(f'{key}: {json.dumps(val) if isinstance(val, str) else val}\n')

def parse_jobdef_fields(jobdefs_file, index=None):
    """
    Extract job definition fields from a jobdefs file and index.
    
    Args:
        jobdefs_file: Path to the jobdefs file
        index: Index of the job definition to extract (optional, will extract from fname env var if not provided)
        
    Returns:
        tuple: (tarfile, job_index, inloc, outloc)
    """

    #check token before proceeding
    try:
        run(f"httokendecode -H", shell=True)
    except SystemExit:
        print("Warning: Token validation failed. Please check your token.")
    run("pwd", shell=True)
    run("ls -ltr", shell=True)

    # Extract index from fname environment variable if not provided
    if index is None:
        fname = os.getenv("fname")
        if not fname:
            print("Error: fname environment variable is not set.")
            sys.exit(1)
        try:
            index = int(fname.split('.')[4].lstrip('0') or '0')
        except (IndexError, ValueError) as e:
            print("Error: Unable to extract index from filename.")
            sys.exit(1)

    if not os.path.exists(jobdefs_file):
        print(f"Error: Jobdefs file {jobdefs_file} does not exist.")
        sys.exit(1)
    
    jobdefs_list = make_jobdefs_list(Path(jobdefs_file))
    
    if len(jobdefs_list) < index:
        print(f"Error: Expected at least {index} job definitions, but got only {len(jobdefs_list)}")
        sys.exit(1)
    
    # Get the index-th job definition (adjusting for Python's 0-index).
    jobdef = jobdefs_list[index]
    print(f"The {index}th job definition is: {jobdef}")

    # Split the job definition into fields (parfile job_index inloc outloc).
    fields = jobdef.split()
    if len(fields) != 4:
        print(f"Error: Expected 4 fields (parfile job_index inloc outloc) in the job definition, but got: {jobdef}")
        sys.exit(1)

    # Return the fields: (tarfile, job_index, inloc, outloc)
    print(f"IND={fields[1]} TARF={fields[0]} INLOC={fields[2]} OUTLOC={fields[3]}")
    return fields[0], int(fields[1]), fields[2], fields[3]

def make_jobdefs_list(input_file):
    """
    Create a list of individual job definitions from a jobdef jobdesc file.
    
    Args:
        input_file: Path to jobdef jobdesc file
        
    Returns:
        List of individual job definition strings: parfile job_index inloc outloc
    """
    if not input_file.exists():
        sys.exit(f"Input file not found: {input_file}")
    
    jobdefs_list = []
    for line in input_file.read_text().splitlines():
        parfile, njobs, inloc, outloc = line.strip().split()
        for i in range(int(njobs)):
            jobdefs_list.append(f"{parfile} {i} {inloc} {outloc}")
    print(f"Generated the list of {len(jobdefs_list)} jobdefs from {input_file}")
    return jobdefs_list

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
        bool: True if template mode, False if normal mode
        
    Raises:
        SystemExit: If validation fails
    """
    # Validate list is not empty
    if not jobdesc:
        print("Error: No job descriptions found in jobdesc file")
        sys.exit(1)
    
    # Check if template mode (has fcl_template field)
    is_template_mode = 'fcl_template' in jobdesc[0]
    
    if is_template_mode:
        # Template mode validation
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
    else:
        # Normal mode validation
        for i, entry in enumerate(jobdesc):
            required_fields = ['tarball', 'njobs', 'inloc', 'outputs']
            for field in required_fields:
                if field not in entry:
                    print(f"Error: Normal mode requires '{field}' field in jobdesc entry {i}")
                    sys.exit(1)
    
    return is_template_mode

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
        if job_index < cumulative_jobs + entry['njobs']:
            jobdesc_entry = entry
            jobdesc_index = i
            break
        cumulative_jobs += entry['njobs']
    
    if jobdesc_entry is None:
        total_jobs = sum(d['njobs'] for d in jobdesc)
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
        run(f"mdh copy-file -e 3 -o -v -s disk -l local {tarball}", shell=True)

    # List input files
    job_io = Mu2eJobIO(tarball)
    inputs = job_io.job_inputs(job_index_num)
    # Flatten the dictionary values into a single list
    all_files = []
    for file_list in inputs.values():
        all_files.extend(file_list)
    infiles = " ".join(all_files)
    
    # Generate FCL - Normal mode with local input copy
    if args.copy_input and infiles.strip() and inloc != "none":
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
            run(f"mdh copy-file -e 3 -o -v -s {file_inloc} -l local {file}", shell=True)
        run(f"mkdir indir; mv *.art indir/", shell=True)
        print(f"FCL: {fcl}")
    # Generate FCL - Normal mode with streaming inputs
    else:
        print(f"Using streaming inputs from {inloc}")
        fcl = write_fcl(tarball, inloc, 'root', job_index_num)
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
    return fcl, simjob_setup, infiles, outputs

def push_data(outputs, infiles):
    """Handle data file management and submission using wildcard patterns from JSON outputs.
    
    Args:
        outputs: List of output specifications (dataset pattern, location)
        infiles: Space-separated list of input files (for parents_list.txt)
    """
    import glob
    
    # Write parents list (input files for SAM metadata)
    Path("parents_list.txt").write_text(infiles.replace(" ", "\n") + "\n")
    
    output_lines = []
    
    # Process only the files specified in the JSON outputs
    for output in outputs:
        dataset_pattern = output['dataset']
        location = output['location']
        
        # Find files that match the pattern (works for both wildcards and exact names)
        matching_files = glob.glob(dataset_pattern)
        print(f"Pattern '{dataset_pattern}' matched {len(matching_files)} files: {matching_files}")
        
        # Process each matching file
        for filename in matching_files:
            print(f"Processing file '{filename}' -> location: {location}")
            output_lines.append(f"{location} {filename} parents_list.txt")

    Path("output.txt").write_text("\n".join(output_lines) + "\n")

    # Push output using shell command directly (environment already set up by shell wrapper)
    result = run("pushOutput output.txt", shell=True)
    if result != 0:
        print(f"Warning: pushOutput returned exit code {result}")

def push_logs(fcl):
    """Handle log file management and submission.
    
    Args:
        fcl: FCL filename to derive log filename from
    """
    import shutil
    
    logfile = replace_file_extensions(fcl, "log", "log")

    # Copy the jobsub log if JSB_TMP is defined
    jsb_tmp = os.getenv("JSB_TMP")
    if jsb_tmp:
        jobsub_log = "JOBSUB_LOG_FILE"
        src = os.path.join(jsb_tmp, jobsub_log)
        print(f"Copying jobsub log from {src} to {logfile}")
        shutil.copy(src, logfile)

    # Create and push log output if the log file exists
    if Path(logfile).exists():
        Path("log_output.txt").write_text(f"disk {logfile} parents_list.txt\n")
        print(f"Pushing log file: {logfile}")
        result = run("pushOutput log_output.txt", shell=True)
        if result != 0:
            print(f"Warning: pushOutput returned exit code {result}")
    else:
        print(f"Warning: Log file {logfile} not found, skipping log push")