#!/usr/bin/env python3
"""
json2jobdef.py: JSON to jobdef generator.

Usage:
  - As module:   python3 -m mu2e_poms_util.json2jobdef --help
  - Direct file: python3 mu2e_poms_util/json2jobdef.py --help
"""
import os, sys
# Allow running this file directly: make package root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import json
import subprocess
from pathlib import Path
from utils.prod_utils import *
from utils.mixing_utils import *
from utils.jobquery import Mu2eJobPars
from utils.jobdef import create_jobdef



def build_jobdef(config, job_args, json_output=False, original_fcl_path=None):
    # Create jobdef using the embed approach with custom template to preserve fcl_overrides
    # For mixing jobs, create template.fcl with actual FCL content (matching Perl behavior)
    # For non-mixing jobs, use the include-based approach
    if original_fcl_path and 'pbeam' in config:
        # Mixing job: create template.fcl with include-based template using ORIGINAL base FCL
        # This avoids self-include and relies on FHICL_FILE_PATH (like Perl)
        write_fcl_template(original_fcl_path, config.get('fcl_overrides', {}))
    else:
        # Non-mixing job: use include-based template
        write_fcl_template(config['fcl'], config.get('fcl_overrides', {}))
    
    # Build the Perl commands that would be equivalent (always build for potential display)
    setup = config['simjob_setup']
    dsconf = config['dsconf']
    desc = config['desc']
    owner = config['owner']
    
    # Build command parts dynamically based on what's in the config
    cmd_parts = [
        'mu2ejobdef',
        '--setup', setup,
        '--dsconf', dsconf,
        '--desc', desc,
        '--dsowner', owner
    ]
    
    # Only add --run-number if it's present in config
    if 'run' in config:
        cmd_parts.extend(['--run-number', str(config['run'])])
    
    # Only add --events-per-job if it's present in config
    if 'events' in config:
        cmd_parts.extend(['--events-per-job', str(config['events'])])
    
    # Add job_args and template
    cmd_parts.extend(job_args)
    cmd_parts.extend(['--embed', 'template.fcl'])
    
    # Always show the mu2ejobdef equivalent command when verbose logging is enabled
    import logging
    if logging.getLogger().level <= logging.DEBUG:
        print(f"🐪 mu2ejobdef equivalent command: {' '.join(cmd_parts)}")
    
    # Now create jobdef using the template.fcl (which contains analyzable content)
    create_jobdef(config, fcl_path='template.fcl', job_args=job_args, embed=True, quiet=json_output)
    
    if json_output:
        # Return structured data for machine consumption
        parfile_name = f"cnf.{config['owner']}.{config['desc']}.{config['dsconf']}.0.tar"
        fcl_file = f"cnf.{config['owner']}.{config['desc']}.{config['dsconf']}.0.fcl"
        
        result = {
            'success': True,
            'perl_commands': [
                {
                    'type': 'mu2ejobdef',
                    'command': ' '.join(cmd_parts),
                    'desc': desc,
                    'simjob_setup': setup
                },
                {
                    'type': 'mu2ejobfcl',
                    'command': f"mu2ejobfcl --jobdef {parfile_name} --default-location tape --default-protocol root --index 0 > {fcl_file}",
                    'desc': desc,
                    'index': 0
                }
            ]
        }
        return result
    else:
        # Human-readable output (current behavior)
        parfile_name = f"cnf.{config['owner']}.{config['desc']}.{config['dsconf']}.0.tar"
        fcl_file = f"cnf.{config['owner']}.{config['desc']}.{config['dsconf']}.0.fcl"
        
        print(f"Python mu2ejobdef equivalent command: {' '.join(cmd_parts)}")
        print(f"Running Perl equivalent of: mu2ejobfcl --jobdef {parfile_name} --default-location tape --default-protocol root --index 0 > {fcl_file}")
        return None

def save_jobdef(config, jobdefs_file=None):
    """
    Save job information to a jobdefs file with unique entries.
    """
    parfile_name = f"cnf.{config['owner']}.{config['desc']}.{config['dsconf']}.0.tar"
    
    # Query job count if njobs is -1
    njobs = config['njobs']
    if njobs == -1:
        jp = Mu2eJobPars(parfile_name)
        njobs = jp.njobs()
        print(f"Queried job count: {njobs}")
    
    new_entry = f"{parfile_name} {njobs} {config['inloc']} {config['outloc']}\n"
    
    # Use provided jobdefs file or default to jobdefs_list.txt
    if jobdefs_file:
        dsconf_file = Path(jobdefs_file)
    else:
        dsconf_file = Path("jobdefs_list.txt")
    
    if dsconf_file.exists() and parfile_name in dsconf_file.read_text():
        print(f"Entry already exists in {dsconf_file}")
        return
    
    with open(dsconf_file, 'a') as f:
        f.write(new_entry)
    print(f"Added {new_entry} to {dsconf_file}")

def main():
    p = argparse.ArgumentParser(description='Generate Mu2e job definitions from JSON configuration')
    p.add_argument('--json', required=True, help='Input JSON file')
    p.add_argument('--desc', help='Dataset descriptor')
    p.add_argument('--dsconf', help='Dataset configuration')
    p.add_argument('--index', type=int, help='Entry index in JSON list')
    p.add_argument('--pushout', action='store_true', help='Enable SAM pushOutput')
    p.add_argument('--verbose', action='store_true', help='Verbose logging')
    p.add_argument('--no-cleanup', action='store_true', help='Keep temporary files (inputs.txt, template.fcl, *Cat.txt)')
    p.add_argument('--jobdefs-list', help='Custom filename for jobdefs list (default: jobdefs_list.txt)')
    p.add_argument('--json-output', action='store_true', help='Output structured JSON instead of human-readable text')
    args = p.parse_args()

    setup_logging(args.verbose)
    
    # Load and expand the JSON configuration once
    expanded_configs = load_json(Path(args.json))
    
    # Helper function to check if argument is effectively None
    def is_none_or_empty(arg):
        return arg is None or arg == 'None' or arg == ''
    
    # If both desc and dsconf are specified, process single entry
    if args.desc and args.dsconf and args.index is None:
        config = find_json_entry(expanded_configs, args.desc, args.dsconf, None)
        process_single_entry(config, json_output=True, pushout=False, no_cleanup=True, jobdefs_list=None)
    # If dsconf is specified but no desc and no index, process all entries for that dsconf
    elif args.dsconf and is_none_or_empty(args.desc) and args.index is None:
        process_all_for_dsconf(expanded_configs, args.dsconf, args)
    # If only index is specified, process single entry by index
    elif args.index is not None and is_none_or_empty(args.desc) and is_none_or_empty(args.dsconf):
        config = find_json_entry(expanded_configs, None, None, args.index)
        process_single_entry(config, json_output=True, pushout=False, no_cleanup=True, jobdefs_list=None)
    else:
        # No filtering specified, show usage
        sys.exit("Please specify either --desc AND --dsconf, --dsconf only, or --index only")

def process_single_entry(config, json_output=True, pushout=False, no_cleanup=True, jobdefs_list=None):
    """Process a single configuration entry (original behavior)"""
    for req in ('simjob_setup', 'fcl', 'dsconf', 'outloc'):
        if not config.get(req):
            sys.exit(f"Missing required field: {req}")
    config['owner'] = config.get('owner', os.getenv('USER', 'mu2e').replace('mu2epro', 'mu2e'))
    config['inloc'] = config.get('inloc', 'tape')
    config['njobs'] = config.get('njobs', -1)
    
    # Store the original FCL path for source type detection
    original_fcl_path = config['fcl']
    write_fcl_template(config['fcl'], config.get('fcl_overrides', {}))
    if config.get('input_data'):
        from utils.samweb_wrapper import list_files
        result = list_files(f"dh.dataset={config['input_data']} and event_count>0")
        with open('inputs.txt', 'w') as f:
            f.write('\n'.join(result))
    
    job_args = []
    
    # Add run number and events per job if present in config (like Perl version)
    if 'run' in config:
        job_args.extend(['--run-number', str(config['run'])])
    if 'events' in config:
        job_args.extend(['--events-per-job', str(config['events'])])
    
    if 'resampler_name' in config:
        nfiles, nevts = get_def_counts(config['input_data'])
        skip = nevts // nfiles
        with open('template.fcl', 'a') as f:
            f.write(f"physics.filters.{config['resampler_name']}.mu2e.MaxEventsToSkip: {skip}\n")
        job_args = ['--auxinput', f"{config.get('merge_factor',1)}:physics.filters.{config['resampler_name']}.fileNames:inputs.txt"]
        # For resampler jobs, also call build_jobdef to generate the job definition
        result = build_jobdef(config, job_args, json_output=json_output, original_fcl_path=original_fcl_path)
    elif 'merge_factor' in config:
        job_args = ['--inputs','inputs.txt','--merge-factor', str(config['merge_factor'])]
        # For merge jobs, also call build_jobdef to generate the job definition
        result = build_jobdef(config, job_args, json_output=json_output, original_fcl_path=original_fcl_path)
    elif 'pbeam' in config:
        merge_factor = calculate_merge_factor(config)
        job_args = ['--inputs','inputs.txt','--merge-factor', str(merge_factor)]
        job_args += build_pileup_args(config)
        
        # For mixing jobs, use the modified template.fcl instead of the original template
        # This ensures MaxEventsToSkip parameters are included
        modified_config = config.copy()
        modified_config['fcl'] = 'template.fcl'
        
        # For mixing jobs, analyze the ORIGINAL FCL file (not template.fcl)
        # This matches Perl's behavior exactly: analyze base FCL, embed template.fcl
        result = build_jobdef(modified_config, job_args, json_output=json_output, original_fcl_path=config['fcl'])
    else:
        # Pass original FCL path to build_jobdef for source type detection  
        config['original_fcl'] = original_fcl_path
        result = build_jobdef(config, job_args, json_output=json_output)
    
    if not json_output:
        # Only output human-readable text when not using JSON output
        print(json.dumps(config, indent=2, sort_keys=True))
        
        parfile_name = f"cnf.{config['owner']}.{config['desc']}.{config['dsconf']}.0.tar"
        if pushout:
            # Push file to SAM if it doesn't already exist there
            from utils.samweb_wrapper import locate_file
            
            # Check if file exists on SAM
            loc = locate_file(parfile_name)
            if not loc:
                # File doesn't exist on SAM, push it
                print(f"Pushing {parfile_name} to SAM...")
                with open('outputs.txt', 'w') as f:
                    f.write(f"disk {parfile_name} none\n")
                run('pushOutput outputs.txt', shell=True)
            else:
                # File exists on SAM, don't push
                print(f"File {parfile_name} already exists on SAM, skipping push")
        save_jobdef(config, jobdefs_list)
        write_fcl(parfile_name, config.get('inloc', 'tape'), 'root')
        print(f"Generated: {parfile_name}")
    
    # Clean up temporary files AFTER job definition is created (unless --no-cleanup is specified)
    if not no_cleanup:
        temp_files = ['inputs.txt', 'template.fcl', 'mubeamCat.txt', 'elebeamCat.txt', 'neutralsCat.txt', 'mustopCat.txt']
        for temp_file in temp_files:
            if Path(temp_file).exists():
                Path(temp_file).unlink()
                print(f"Cleanup: {temp_file}")
    else:
        print("Temporary files kept (--no-cleanup specified)")
    
    return result

def is_already_expanded(configs):
    """Check if the configuration is already expanded (has scalar values, not lists)"""
    if not isinstance(configs, list) or len(configs) == 0:
        return False
    
    # Check all entries, not just the first one
    for i, config in enumerate(configs):
        if not isinstance(config, dict):
            raise ValueError(f"Entry {i} is not a dictionary: {type(config)}")
        
            # Check if all values are either all scalars or all lists
    values = list(config.values())
    has_lists = any(isinstance(v, list) for v in values)
    has_scalars = any(not isinstance(v, list) for v in values)
    
    # Mixed configurations are not allowed
    if has_lists and has_scalars:
        raise ValueError(f"Mixed configuration detected in entry {i}: some values are lists and some are scalars. This is not allowed.")
    
    # If all values are scalars (not lists), it's already expanded
    return not has_lists

def load_json(json_path):
    """Load and expand JSON configuration if needed"""
    from utils.mixing_utils import expand_mix_config
    
    json_text = json_path.read_text()
    configs = json.loads(json_text)
    
    if is_already_expanded(configs):
        return configs
    else:
        return expand_mix_config(json_path)

def find_json_entry(configs, desc=None, dsconf=None, index=None):
    """Find a matching JSON entry from configuration list"""
    if index is not None:
        try: 
            return configs[index]
        except IndexError: 
            sys.exit(f"Index {index} out of range.")
    
    matches = [e for e in configs if e.get('desc') == desc and e.get('dsconf') == dsconf]
    if len(matches) != 1:
        sys.exit(f"Expected 1 match for desc={desc}, dsconf={dsconf}; found {len(matches)}.")
    return matches[0]

def process_all_for_dsconf(expanded_configs, dsconf, args):
    """Process all entries matching the specified dsconf and generate job definitions for all permutations"""
    
    # Filter to only entries matching the specified dsconf (partial match)
    matching_configs = [config for config in expanded_configs if config.get('dsconf', '').startswith(dsconf)]
    
    if not matching_configs:
        sys.exit(f"No entries found matching dsconf: {dsconf}")
    
    print(f"Found {len(matching_configs)} entries matching dsconf: {dsconf}")
    
    # Process each matching configuration using the existing process_single_entry function
    for i, config in enumerate(matching_configs):
        print(f"\nProcessing entry {i+1}/{len(matching_configs)}: {config.get('desc', 'Unknown')}")
        
        # Check required fields before calling process_single_entry
        for req in ('simjob_setup', 'fcl', 'dsconf', 'outloc'):
            if not config.get(req):
                print(f"Warning: Missing required field {req}, skipping entry")
                continue
        
        # Use the existing process_single_entry function
        process_single_entry(config, args)
        
        # Clean up template.fcl for next iteration (since process_single_entry cleans up)
        if Path('template.fcl').exists():
            Path('template.fcl').unlink()

if __name__ == '__main__':
    main()
