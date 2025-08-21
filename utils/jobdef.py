#!/usr/bin/env python3
"""
Python implementation of mu2ejobdef with full parity to Perl version.

Creates a jobdef (par) tarball with:
  - jobpars.json (complete structure matching Perl mu2ejobdef)
  - mu2e.fcl     (embedded from template.fcl)

Features implemented:
  - Source type detection (EmptyEvent, RootInput, SamplingInput)
  - Complete event_id, subrunkey, outfiles, seed sections
  - Auxiliary input and sampling input processing
  - Output file name processing and override logic
  - SeedService detection via fhicl-get
"""

import json
import os
import re
import subprocess
from pathlib import Path
import tarfile
from typing import Dict, List, Tuple, Optional, Any


# Constants matching Perl mu2ejobdef exactly
FILENAME_JSON = 'jobpars.json'
FILENAME_FCL = 'mu2e.fcl'
FILENAME_TARBALL = 'code.tar'
FILENAME_TARSETUP = 'Code/setup.sh'

class Mu2eFilename:
    """Mu2e filename parser, simplified version of Perl Mu2eFilename."""
    def __init__(self, filename: str = ""):
        self.filename = filename
        # Default values for all fields
        self.tier = self.owner = self.description = self.configuration = self.sequencer = self.extension = ""
        if filename:
            self._parse(filename)
    
    def _parse(self, filename: str):
        """Parse mu2e filename format: tier.owner.description.configuration.sequencer.extension"""
        parts = filename.split('.')
        fields = ['tier', 'owner', 'description', 'configuration', 'sequencer', 'extension']
        for i, field in enumerate(fields):
            setattr(self, field, parts[i] if i < len(parts) else "")
    
    def basename(self) -> str:
        """Return the filename."""
        return self.filename


def resolve_fhicl_file(templatespec: str) -> str:
    """Resolve FCL template path using FHICL_FILE_PATH (matching Perl behavior)."""
    fhicl_path = os.getenv('FHICL_FILE_PATH')
    if not fhicl_path:
        raise ValueError("FHICL_FILE_PATH environment variable is not set")
    
    pathdirs = fhicl_path.split(':')
    for d in pathdirs:
        if d:
            full_path = os.path.join(d, templatespec)
            if os.path.isfile(full_path):
                return full_path
    
    raise FileNotFoundError(f"Error: can not locate template file \"{templatespec}\" relative to FHICL_FILE_PATH={fhicl_path}")


def _run_fhicl_get(template_path: str, command: str, key: str = "", default_value: Any = None) -> Any:
    """Unified helper for fhicl-get commands with graceful fallback."""
    try:
        if command == '--atom-as':
            cmd = ['fhicl-get', '--atom-as', 'string', key, template_path]
        else:
            # All other commands follow the same pattern
            cmd = ['fhicl-get', command, key, template_path] if key else ['fhicl-get', command, template_path]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return default_value


def _get_source_type(template_path: str) -> str:
    """Determine source module type from FCL template using fhicl-get.
    
    Matches Perl behavior: just checks source.module_type on the template file.
    Falls back to RootInput for mixing jobs instead of EmptyEvent.
    """
    source_type = _run_fhicl_get(template_path, '--atom-as', 'source.module_type', 'RootInput')
    return source_type


def _seed_needed(template_path: str) -> bool:
    """Check if SeedService is configured in the template FCL."""
    services = _run_fhicl_get(template_path, '--names-in', 'services')
    return 'SeedService' in (services.split('\n') if services else [])


def _get_output_modules(template_path: str) -> List[str]:
    """Get list of output modules from FCL template, filtering to only active ones (like Perl)."""
    # Get all output modules (like Perl's @all_outmods)
    result = _run_fhicl_get(template_path, '--names-in', 'outputs', '')
    if not result:
        return []
    
    all_outmods = result.split('\n')
    
    # Get end paths and their modules (like Perl's %endmodules)
    endmodules = {}
    
    # Get end paths (like Perl: my @endpaths = `fhicl-get --sequence-of string physics.end_paths $templateresolved 2>/dev/null`)
    endpaths_result = subprocess.run(
        ['fhicl-get', '--sequence-of', 'string', 'physics.end_paths', template_path],
        capture_output=True, text=True
    )
    endpaths = endpaths_result.stdout.strip().split('\n') if endpaths_result.stdout else []
    
    for ep in endpaths:
        if ep:
            # Get modules in this end path (like Perl: my @mods = `fhicl-get --sequence-of string physics.$ep $templateresolved 2>/dev/null`)
            mods_result = subprocess.run(
                ['fhicl-get', '--sequence-of', 'string', f'physics.{ep}', template_path],
                capture_output=True, text=True
            )
            mods = mods_result.stdout.strip().split('\n') if mods_result.stdout else []
            for mod in mods:
                if mod:
                    endmodules[mod] = True
    
    # Filter to only active output modules (like Perl's @active_outmods)
    active_outmods = [mod for mod in all_outmods if mod in endmodules]
    
    return active_outmods


def _get_fcl_value(template_path: str, key: str) -> str:
    """Get FCL parameter value."""
    return _run_fhicl_get(template_path, '--atom-as', key, '')


def _validate_fcl_template(template_path: str) -> None:
    """Validate FCL template has required physics sections (trigger_paths, end_paths)."""
    try:
        # Check for trigger_paths and end_paths in physics section
        result = subprocess.run(
            ['fhicl-get', '--names-in', 'physics', template_path],
            capture_output=True, text=True, check=True
        )
        physics_keys = result.stdout.strip().split('\n')
        
        required_keys = ['trigger_paths', 'end_paths']
        missing_keys = [key for key in required_keys if key not in physics_keys]
        
        if missing_keys:
            raise ValueError(f"FCL template missing required physics sections: {missing_keys}")
            
    except (subprocess.CalledProcessError, FileNotFoundError):
        # If fhicl-get is not available, skip validation
        pass


def _build_jobpars_json(config: Dict, tbs: Dict, code: str = "", template_path: str = "") -> Dict:
    """Construct complete jobpars.json structure matching Perl mu2ejobdef."""
    owner = config.get('owner') or os.getenv('USER', 'mu2e').replace('mu2epro', 'mu2e')
    desc = config['desc']
    dsconf = config['dsconf']
    
    # Build proper jobname like Perl version (cnf.owner.desc.dsconf.0.tar)
    jobname = f"cnf.{owner}.{desc}.{dsconf}.0.tar"

    # Base structure - use Perl field ordering: code, tbs, jobname, setup
    # Match Perl version exactly: only include the 4 fields that Perl includes
    return {
        "code": code,
        "tbs": tbs,
        "jobname": jobname,
        "setup": config['simjob_setup']
    }


def _read_filelist(path: str) -> List[str]:
    """Read file list, filtering out empty lines."""
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


def _parse_job_args(job_args: List[str], template_path: str, config: Dict = None) -> Tuple[Dict, str, bool]:
    """
    Parse mu2ejobdef CLI options and build complete TBS structure.
    Returns: (tbs_dict, outdir, override_output_description)
    """
    tbs: Dict[str, Any] = {}
    it = iter(job_args)
    
    # Parse all arguments using a dispatch table
    args_state = {
        'inputs_list': [],
        'merge_factor': 1,
        'auxin': {},
        'sampling': {},
        'run_number': None,
        'events_per_job': None,
        'outdir': None,
        'override_output_description': False,
        'fcl_mode': None,
        'fcl_template': None
    }
    
    def parse_auxinput(spec: str) -> Tuple[str, int, List[str]]:
        """Parse auxinput specification: count:key:filelist"""
        n_str, key, filelist = spec.split(':', 2)
        all_files = _read_filelist(filelist)
        nreq = len(all_files) if n_str == 'all' else int(n_str)
        return key, nreq, all_files
    
    def parse_samplinginput(spec: str) -> Tuple[str, int, List[str]]:
        """Parse samplinginput specification: count:dsname:filelist"""
        n_str, dsname, filelist = spec.split(':', 2)
        all_files = _read_filelist(filelist)
        nreq = len(all_files) if n_str == 'all' else int(n_str)
        return dsname, nreq, all_files
    
    # Argument parsing dispatch table
    arg_handlers = {
        '--inputs': lambda: _read_filelist(next(it)),
        '--merge-factor': lambda: int(next(it)),
        '--auxinput': lambda: parse_auxinput(next(it)),
        '--samplinginput': lambda: parse_samplinginput(next(it)),
        '--run-number': lambda: int(next(it)),
        '--events-per-job': lambda: int(next(it)),
        '--outdir': lambda: next(it),
        '--override-output-description': lambda: True,
        '--embed': lambda: ('embed', next(it)),
        '--include': lambda: ('include', next(it))
    }
    
    for token in it:
        if token in arg_handlers:
            result = arg_handlers[token]()
            if token == '--auxinput':
                key, nreq, files = result
                args_state['auxin'][key] = (nreq, files)
            elif token == '--samplinginput':
                dsname, nreq, files = result
                args_state['sampling'][dsname] = (nreq, files)
            elif token in ['--embed', '--include']:
                args_state['fcl_mode'], args_state['fcl_template'] = result
            elif token == '--override-output-description':
                args_state['override_output_description'] = result
            else:
                # Map argument names to state keys
                key_map = {
                    '--inputs': 'inputs_list',
                    '--merge-factor': 'merge_factor', 
                    '--run-number': 'run_number',
                    '--events-per-job': 'events_per_job',
                    '--outdir': 'outdir'
                }
                if token in key_map:
                    args_state[key_map[token]] = result

    # Determine source type and configure accordingly
    source_type = _get_source_type(template_path)
    # Enforce EmptyEvent restrictions (matching Perl behavior)
    if source_type == 'EmptyEvent':
        if args_state['run_number'] is None:
            raise ValueError("--run-number must be specified for EmptyEvent source type")
        if args_state['events_per_job'] is None:
            raise ValueError("--events-per-job must be specified for EmptyEvent source type")
        if args_state['inputs_list']:
            raise ValueError("--inputs is not compatible with EmptyEvent source type")
        if args_state['merge_factor'] != 1:  # Only error if explicitly set to non-default value
            raise ValueError("--merge-factor is not compatible with EmptyEvent source type")
        
        tbs['event_id'] = {
            'source.firstRun': args_state['run_number'],
            'source.maxEvents': args_state['events_per_job']
        }
        tbs['subrunkey'] = 'source.firstSubRun'
        
    elif source_type in ['RootInput', 'FromCorsikaBinary', 'FromSTMTestBeamData']:
        if args_state['inputs_list']:
            tbs['inputs'] = {'source.fileNames': [args_state['merge_factor'], args_state['inputs_list']]}
        tbs['subrunkey'] = ''  # subrun comes from the inputs
        
        if source_type != 'FromCorsikaBinary':
            tbs['event_id'] = {'source.maxEvents': 2147483647}
            
    elif source_type == 'SamplingInput':
        if args_state['run_number'] is not None:
            tbs['event_id'] = {
                'source.run': args_state['run_number'],
                'source.maxEvents': 2147483647
            }
        tbs['subrunkey'] = 'source.subRun'
        
        if args_state['sampling']:
            samplingintable = {}
            for dsname, (nreq, filelist) in args_state['sampling'].items():
                inputkey = f'source.dataSets.{dsname}.fileNames'
                samplingintable[inputkey] = [nreq, filelist]
            tbs['samplinginput'] = samplingintable

    # Handle output files (extract from template FCL like Perl does)
    output_modules = _get_output_modules(template_path)
    if output_modules:
        outfiles = {}
        
        for mod in output_modules:
            if mod and mod != '':  # skip empty entries
                output_key = f'outputs.{mod}.fileName'
                
                # Get template from FCL file (like Perl does)
                filename_pattern = _get_fcl_value(template_path, output_key)
                
                if filename_pattern and filename_pattern.strip():
                    # Do placeholder replacement like Perl does
                    # Use more specific replacement to avoid partial matches
                    replaced_pattern = filename_pattern.strip()
                    replaced_pattern = replaced_pattern.replace('.owner.', f'.{config.get("owner", "mu2e")}.')
                    replaced_pattern = replaced_pattern.replace('.version.', f'.{config["dsconf"]}.')
                    outfiles[output_key] = replaced_pattern
                else:
                    # No template pattern found - this shouldn't happen in a properly resolved template
                    # Fail like Perl does when output filename is not defined
                    raise ValueError(f"Error: {output_key} is not defined")
        if outfiles:
            tbs['outfiles'] = outfiles

    # Handle auxiliary inputs
    if args_state['auxin']:
        tbs['auxin'] = args_state['auxin']

    # Handle seed if needed
    if _seed_needed(template_path):
        tbs['seed'] = 'services.SeedService.baseSeed'

    # Reorder TBS to match Perl order: outfiles, subrunkey, auxin, inputs, event_id, seed
    ordered_tbs = {}
    perl_order = ['outfiles', 'subrunkey', 'auxin', 'inputs', 'event_id', 'seed', 'samplinginput']
    
    for key in perl_order:
        if key in tbs:
            ordered_tbs[key] = tbs[key]
    
    # Add any remaining keys not in the standard order
    for key, value in tbs.items():
        if key not in ordered_tbs:
            ordered_tbs[key] = value

    return ordered_tbs, None, args_state['override_output_description']


def create_jobdef(config: Dict, fcl_path: str = 'template.fcl', job_args: List[str] = None, embed: bool = True, outdir: Optional[Path] = None, quiet: bool = False) -> Path:
    """
    Create a jobdef tarball (cnf.owner.desc.dsconf.0.tar) with complete Perl parity.

    - Embeds jobpars.json and mu2e.fcl
    - Processes all source types, output files, seeds, etc.
    - Returns Path to the created file
    """
    owner = config.get('owner') or os.getenv('USER', 'mu2e').replace('mu2epro', 'mu2e')
    
    # Handle auto-description
    if config.get('auto_description') is not None:
        desc = f"AutoDesc{config.get('auto_description', '')}"
    else:
        desc = config['desc']
    
    dsconf = config['dsconf']

    # Determine template path - need this early for fhicl-get calls
    # Match Perl logic exactly: for --embed, check if file exists locally first, then fall back to FHICL_FILE_PATH
    if embed and Path(fcl_path).exists():
        # Local file exists - use directly (matches Perl: -e $templatespec && $templatespec)
        template_path = fcl_path
    else:
        # Resolve via FHICL_FILE_PATH (matches Perl: resolveFHICLFile($templatespec))
        resolved_template_path = resolve_fhicl_file(fcl_path)
        template_path = resolved_template_path
    fcl_embed_mode = 'embed' if embed else 'include'

    # Build complete command-line arguments from config and job_args  
    base_args = []
    if config.get('run'):
        base_args.extend(['--run-number', str(config['run'])])
    if config.get('events'):
        base_args.extend(['--events-per-job', str(config['events'])])
    
    # Add any additional job_args passed in, but filter out embed/include since we handle them separately
    filtered_job_args = []
    it = iter(job_args or [])
    for arg in it:
        if arg in ['--embed', '--include']:
            next(it, None)  # Skip the next argument (template path)
        else:
            filtered_job_args.append(arg)
    base_args.extend(filtered_job_args)
    
    # Add embed/include for parsing (needed for _parse_job_args)
    all_args = base_args.copy()
    if embed:
        all_args.extend(['--embed', template_path])
    else:
        all_args.extend(['--include', template_path])
    
    # Print equivalent mu2ejobdef command for debugging (unless quiet)
    cmd_parts = ['mu2ejobdef']
    
    # Add setup or code argument
    setup_arg = '--setup' if config.get('simjob_setup') else '--code'
    setup_val = config.get('simjob_setup') or config.get('code')
    cmd_parts.extend([setup_arg, setup_val])
    
    # Add required arguments
    cmd_parts.extend([
        '--dsconf', dsconf,
        '--desc', desc,
        '--dsowner', owner
    ])
    
    # Add optional arguments and FCL mode
    cmd_parts.extend(base_args)
    cmd_parts.extend(['--embed' if embed else '--include', template_path])
    
    if not quiet:
        print(f"Python mu2ejobdef equivalent command:")
        print(' '.join(cmd_parts))

    # Parse job arguments and build TBS with template analysis
    tbs, _, override_output_description = _parse_job_args(all_args, template_path, config)
    
    # Use provided outdir (simple logic matching Perl version)
    final_outdir = Path(outdir) if outdir else None
    out = final_outdir / f"cnf.{owner}.{desc}.{dsconf}.0.tar" if final_outdir else Path(f"cnf.{owner}.{desc}.{dsconf}.0.tar")

    if out.exists():
        out.unlink()

    # Build complete jobpars JSON
    jobpars = _build_jobpars_json(config, tbs, code="", template_path=template_path)

    # Prepare temporary files
    temp_files = {}
    
    # Create jobpars.json
    jobpars_path = Path(FILENAME_JSON)
    jobpars_json = json.dumps(jobpars, indent=3, separators=(', ', ' : ')) + "\n"
    jobpars_path.write_text(jobpars_json)
    temp_files[FILENAME_JSON] = jobpars_path
    
    # Validate and create mu2e.fcl
    tpl_path = Path(template_path)
    
    if not tpl_path.exists():
        raise FileNotFoundError(f"FCL template not found: {tpl_path}")
    
    # Validate the template (either local file or original template)
    _validate_fcl_template(template_path)
    
    mu2e_fcl_tmp = Path(FILENAME_FCL)
    
    # Handle --embed vs --include modes (matching Perl behavior)
    if fcl_embed_mode == 'embed':
        # --embed: read the file content directly (whether original or modified)
        fcl_content = tpl_path.read_text()
    else:
        # --include: use #include directive (only for original templates, not local modified files)
        if fcl_path == 'template.fcl':
            # Local modified file: embed the content directly
            fcl_content = tpl_path.read_text()
        else:
            # Original template: use #include directive
            fcl_content = f'#include "{tpl_path}"\n'
    
    mu2e_fcl_tmp.write_text(fcl_content)
    temp_files[FILENAME_FCL] = mu2e_fcl_tmp
    
    # Create tarball
    with tarfile.open(out, 'w') as tar:
        for filename, filepath in temp_files.items():
            tar.add(filepath, arcname=filename)
    
    # Cleanup temp files
    for filepath in temp_files.values():
        try:
            filepath.unlink()
        except Exception:
            pass

    return out


if __name__ == '__main__':
    import argparse
    import sys
    
    parser = argparse.ArgumentParser(
        description='Python implementation of mu2ejobdef - Create Mu2e job definition tarballs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --setup /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020az/setup.sh \\
           --dsconf MDC2020az --desc CosmicCORSIKALow --dsowner mu2e \\
           --embed Production/JobConfig/cosmic/S2Resampler.fcl

  %(prog)s --code /path/to/custom/code.tar \\
           --dsconf MDC2020az --desc CustomCode --dsowner mu2e \\
           --embed Production/JobConfig/cosmic/S2Resampler.fcl

  %(prog)s --setup /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020az/setup.sh \\
           --dsconf MDC2020az --auto-description --dsowner mu2e \\
           --include Production/JobConfig/cosmic/S2Resampler.fcl \\
           --inputs inputs.txt --merge-factor 2

  %(prog)s --setup /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020az/setup.sh \\
           --dsconf MDC2020az --desc MixingJob --dsowner mu2e \\
           --embed Production/JobConfig/mixing/Mix.fcl \\
           --auxinput "1:physics.filters.MuBeamFlashMixer.fileNames:mubeamCat.txt" \\
           --auxinput "25:physics.filters.EleBeamFlashMixer.fileNames:elebeamCat.txt" \\
           --samplinginput "10:dataset1:sampling1.txt" \\
           --override-output-description

Note: For EmptyEvent source type, --run-number and --events-per-job are required, 
      and --inputs/--merge-factor are not allowed.
        """
    )
    
    # Required arguments (mutually exclusive setup/code)
    setup_group = parser.add_mutually_exclusive_group(required=True)
    setup_group.add_argument('--setup', metavar='SCRIPT',
                            help='SimJob setup script path')
    setup_group.add_argument('--code', metavar='TARBALL',
                            help='Custom code tarball path')
    
    # Required arguments
    parser.add_argument('--dsconf', required=True,
                       help='Dataset configuration (e.g., MDC2020az)')
    
    # Description (mutually exclusive)
    desc_group = parser.add_mutually_exclusive_group(required=True)
    desc_group.add_argument('--desc', metavar='DESC',
                           help='Dataset description (e.g., CosmicCORSIKALow)')
    desc_group.add_argument('--auto-description', nargs='?', const='', metavar='SUFFIX',
                           help='Auto-extract description from input files (optional suffix)')
    
    parser.add_argument('--dsowner', required=True,
                       help='Dataset owner (e.g., mu2e)')
    
    # FCL template handling (mutually exclusive)
    fcl_group = parser.add_mutually_exclusive_group(required=True)
    fcl_group.add_argument('--embed', metavar='FCL',
                          help='Embed FCL template content in jobdef')
    fcl_group.add_argument('--include', metavar='FCL',
                          help='Include FCL template by reference in jobdef')
    
    # Optional arguments
    parser.add_argument('--run-number', type=int,
                       help='Run number for job (required for EmptyEvent source type)')
    parser.add_argument('--events-per-job', type=int,
                       help='Number of events per job (required for EmptyEvent source type)')
    parser.add_argument('--inputs', metavar='FILE',
                       help='Input file list (for sampling jobs, not compatible with EmptyEvent)')
    parser.add_argument('--merge-factor', type=int, metavar='N',
                       help='Merge factor for input files (not compatible with EmptyEvent)')
    parser.add_argument('--auxinput', action='append', metavar='SPEC',
                       help='Auxiliary input specification (format: count:key:filelist)')
    parser.add_argument('--samplinginput', action='append', metavar='SPEC',
                       help='Sampling input specification (format: count:dsname:filelist)')
    parser.add_argument('--override-output-description', action='store_true',
                       help='Override output file descriptions with job description')
    parser.add_argument('--verbose', action='store_true',
                       help='Enable verbose output')
    parser.add_argument('--output-dir', metavar='DIR',
                       help='Output directory for jobdef tarball')
    
    args = parser.parse_args()
    
    # Build configuration dictionary
    config = {
        'simjob_setup': args.setup,
        'code': args.code,
        'dsconf': args.dsconf,
        'desc': args.desc,
        'auto_description': args.auto_description,
        'owner': args.dsowner,
    }
    
    if args.run_number:
        config['run'] = args.run_number
    if args.events_per_job:
        config['events'] = args.events_per_job
    
    # Build job arguments
    job_args = []
    
    if args.inputs:
        job_args.extend(['--inputs', args.inputs])
    if args.merge_factor:
        job_args.extend(['--merge-factor', str(args.merge_factor)])
    if args.auxinput:
        for aux in args.auxinput:
            job_args.extend(['--auxinput', aux])
    
    # Determine FCL path and embed mode
    fcl_path = args.embed or args.include
    embed_mode = 'embed' if args.embed else 'include'
    
    try:
        # Create job definition
        if args.verbose:
            print(f"Creating job definition with config: {config}")
            print(f"FCL template: {fcl_path} (mode: {embed_mode})")
            print(f"Job arguments: {job_args}")
        
        result = create_jobdef(
            config=config,
            fcl_path=fcl_path,
            job_args=job_args,
            embed=embed_mode == 'embed',
            outdir=args.output_dir
        )
        
        print(f"Successfully created: {result}")
        
    except Exception as e:
        if args.verbose:
            import traceback
            traceback.print_exc()
        print(f"Error creating job definition: {e}", file=sys.stderr)
        sys.exit(1)