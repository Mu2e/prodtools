#!/usr/bin/env python3
"""
Python implementation of mu2ejobdef, with full parity to the Perl version.

Creates a jobdef (par) tarball containing jobpars.json (matching Perl's
structure) and mu2e.fcl (embedded from template.fcl): source-type detection
(EmptyEvent, RootInput, SamplingInput, ...), event_id/subrunkey/outfiles/seed
sections, aux/sampling input processing, and output filename overrides.
"""
import os
import sys
# Add parent directory to path when run directly
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import subprocess
from pathlib import Path
import tarfile
from typing import Dict, List, Tuple, Optional, Any

from utils.config_utils import cnf_name
from utils.job_common import Mu2eName, default_owner, tbs_capacity, CODE_SETUP_REL, sha256_file

# Constants matching Perl mu2ejobdef exactly
FILENAME_JSON = 'jobpars.json'
FILENAME_FCL = 'mu2e.fcl'


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


def _replace_placeholders(pattern: str, config: Dict, defer_keys: set = None) -> str:
    """Replace placeholders in output filename patterns (Perl parity).

    Handles legacy tokens `.owner.` and `.version.`, the literal word
    'configuration', and `{var}` placeholders for any string field in config.

    defer_keys: config key names whose {key} placeholders are left unresolved
                for runtime substitution (generic tarballs: {desc}).
    """
    if pattern is None:
        return pattern
    if defer_keys is None:
        defer_keys = set()
    replaced_pattern = pattern.strip()
    replaced_pattern = replaced_pattern.replace('.owner.', f'.{config.get("owner", "mu2e")}.')
    replaced_pattern = replaced_pattern.replace('.version.', f'.{config["dsconf"]}.')
    replaced_pattern = replaced_pattern.replace('configuration', config["dsconf"])
    for key, value in config.items():
        if key in defer_keys:
            continue  # leave {desc} etc. as a literal for runtime substitution
        if isinstance(value, str):
            replaced_pattern = replaced_pattern.replace(f'{{{key}}}', value)
    return replaced_pattern


def _add_outfile(tbs: Dict, key: str, pattern: str, config: Dict, defer_keys: set = None) -> None:
    """Replace placeholders and add an outfile entry to TBS."""
    replaced = _replace_placeholders(pattern, config, defer_keys=defer_keys)
    if 'outfiles' not in tbs:
        tbs['outfiles'] = {}
    tbs['outfiles'][key] = replaced


def _run_fhicl_get(template_path: str, command: str, key: str = "") -> str:
    """Run fhicl-get command and return output. Dies on failure like Perl."""
    if command == '--atom-as':
        cmd = ['fhicl-get', '--atom-as', 'string', key, template_path]
    elif command == '--sequence-of':
        cmd = ['fhicl-get', '--sequence-of', 'string', key, template_path]
    else:
        cmd = ['fhicl-get', command, key, template_path] if key else ['fhicl-get', command, template_path]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _get_source_type(template_path: str) -> str:
    """Determine source module type from FCL template using fhicl-get.

    Dies on fhicl-get failure (Perl parity): no source section is fatal.
    """
    source_type = _run_fhicl_get(template_path, '--atom-as', 'source.module_type')
    return source_type


def _seed_needed(template_path: str) -> bool:
    """True if services.SeedService is configured (Perl seedNeeded() parity)."""
    try:
        svclist = _run_fhicl_get(template_path, '--names-in', 'services')
        return any(service == 'SeedService' for service in svclist.split('\n'))
    except subprocess.CalledProcessError:
        return False  # no services block -> not needed (Perl's 2>/dev/null)


def _get_output_modules(template_path: str) -> List[str]:
    """Output modules from the FCL template that are active on an end path
    (Perl parity). Modules merely declared under `outputs` but not wired
    into physics.end_paths are excluded."""
    try:
        all_outmods = _run_fhicl_get(template_path, '--names-in', 'outputs').split('\n')
    except subprocess.CalledProcessError:
        return []  # no outputs section, e.g. EventNtuple uses TFileService

    if not all_outmods:
        return []

    # end_paths, not trigger_paths (past bug)
    endpaths = _run_fhicl_get(template_path, '--sequence-of', 'physics.end_paths').split('\n')

    endmodules = set()
    for ep in endpaths:
        if ep == '@nil':
            continue
        try:
            mods = _run_fhicl_get(template_path, '--sequence-of', f'physics.{ep}').split('\n')
            for m in mods:
                if m:
                    endmodules.add(m)
        except subprocess.CalledProcessError:
            continue

    active_outmods = []
    for mod in all_outmods:
        if mod and mod != '' and mod in endmodules:
            active_outmods.append(mod)

    return active_outmods


def _get_fcl_value(template_path: str, key: str) -> str:
    """Get FCL parameter value."""
    return _run_fhicl_get(template_path, '--atom-as', key)


def _validate_fcl_template(template_path: str) -> None:
    """Validate FCL template has required physics sections (trigger_paths, end_paths).

    Dies on fhicl-get failure (Perl parity).
    """
    result = subprocess.run(
        ['fhicl-get', '--names-in', 'physics', template_path],
        capture_output=True, text=True, check=True
    )
    physics_keys = result.stdout.strip().split('\n')
    
    required_keys = ['trigger_paths', 'end_paths']
    missing_keys = [key for key in required_keys if key not in physics_keys]
    
    if missing_keys:
        raise ValueError(f"FCL template missing required physics sections: {missing_keys}")


def _reorder(d: Dict, order: List[str]) -> Dict:
    """Copy keys in the given preferred order, then append the rest
    (Perl mu2ejobdef key-order parity)."""
    ordered = {k: d[k] for k in order if k in d}
    for key, value in d.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def validate_code_tarball(path):
    """Refuse a code tarball that could not work on a worker.

    Same three checks Perl mu2ejobdef makes (mu2ejobdef:808-828):
    readable, bzip2-compressed, and containing Code/setup.sh. Done at
    BUILD time so a broken tarball costs one command instead of a
    thousand grid jobs.

    Scanning stops at the first match. bzip2 is not seekable, so a full
    walk of a ~1 GB archive is slow; `museTarball.sh` writes setup.sh
    early, so the first-match exit is nearly free in practice.

    Content decides, never the filename — a correctly built tarball is
    accepted under any name.
    """
    if not os.path.isfile(path) or not os.access(path, os.R_OK):
        raise ValueError(f"code tarball is not readable: {path}")
    try:
        with tarfile.open(path, 'r:bz2') as tar:
            for member in tar:
                if member.name == CODE_SETUP_REL:
                    return
    except tarfile.ReadError as exc:
        raise ValueError(
            f"code tarball is not a bzip2-compressed tar archive: "
            f"{path} ({exc})")
    raise ValueError(
        f"code tarball has no {CODE_SETUP_REL}: {path} — "
        f"build it with `muse tarball`")


def build_code_ref(path):
    """Provenance for a code-mode cnf: what build it was made against.

    The bytes are NOT embedded (sidecar delivery), so this digest is the
    only thing binding the cnf to a particular Offline build.
    `check_inputs.check_code_tarball` re-derives it at submit time and
    refuses a mismatch.
    """
    digest, size = sha256_file(path)
    return {'sha256': digest, 'size': size,
            'source_path': os.path.abspath(path)}


def _build_jobpars_json(config: Dict, tbs: Dict) -> Dict:
    """Construct complete jobpars.json structure matching Perl mu2ejobdef.

    Perl field ordering: code, setup, tbs, jobname. `code_ref` is ours
    and sits after `setup`, next to the field it explains.

    `code` is ALWAYS empty. Upstream uses it for the name of an embedded
    archive member; prodtools ships the code as a jobsub sidecar and
    embeds nothing, so an empty string is the truthful answer and keeps
    a cnf of ours readable by mu2ejobquery.
    """
    setup = config.get('simjob_setup')
    code_path = config.get('code')
    if bool(setup) == bool(code_path):
        raise ValueError(
            "exactly one of 'simjob_setup' and 'code' must be set "
            f"(simjob_setup={setup!r}, code={code_path!r})")
    pars = {
        "code": "",
        "setup": setup or CODE_SETUP_REL,
    }
    if code_path:
        pars["code_ref"] = build_code_ref(code_path)
    pars["tbs"] = _reorder(tbs, ['seed', 'subrunkey', 'event_id', 'outfiles'])
    pars["jobname"] = cnf_name(config, 'tar')
    return pars


def _read_filelist(path: str) -> List[str]:
    """Read file list, filtering out empty lines."""
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


def _resolve_njobs(config: Dict, tbs: Dict) -> Optional[int]:
    """Job count to embed as tbs.njobs (tarball self-description).

    The declared config value wins after validation against the capacity
    derived from the frozen input lists; -1 or absent means "use derived".
    Returns None when the count is unknowable (generator without a declared
    njobs, generic tarball) — the key is then omitted and readers treat the
    jobdef as open-ended (job count is a submit-time decision, authoritative
    in the submission map).
    """
    if config.get('generic_tarball'):
        return None

    capacity = tbs_capacity(tbs)

    declared = config.get('njobs')
    if declared is None or declared == -1:
        return capacity
    declared = int(declared)
    if capacity is not None and declared > capacity:
        raise ValueError(
            f"njobs={declared} exceeds the {capacity} jobs supported by the "
            f"input file list; indices past {capacity - 1} would fail at runtime "
            f"with job_primary_inputs(): invalid index")
    return declared


def _validate_options_for_source_type(source_type: str, args_state: Dict) -> None:
    """Validate CLI options against the required/allowed set for source_type
    (Perl validateOptionsForSourceType parity)."""
    validation_rules = {
        'EmptyEvent': {
            'required': ['run_number', 'events_per_job', 'description'],
            'allowed': []
        },
        'RootInput': {
            'required': ['inputs', 'merge_factor'],
            'allowed': ['description', 'auto_description']
        },
        'FromCorsikaBinary': {
            'required': ['inputs', 'merge_factor'],
            'allowed': ['description', 'auto_description']
        },
        'FromSTMTestBeamData': {
            'required': ['inputs', 'merge_factor'],
            'allowed': ['description', 'auto_description']
        },
        'SamplingInput': {
            'required': ['run_number', 'description', 'samplinginput'],
            'allowed': []
        },
        'PBISequence': {
            # inputs + merge_factor are used by `dir:` inloc workflows;
            # chunk_mode workflows skip them entirely (per-job slice is
            # materialized at runtime, no SAM-tracked inputs). Both valid.
            'required': ['run_number'],
            'allowed': ['description', 'auto_description', 'events_per_job',
                        'inputs', 'merge_factor']
        }
    }
    
    if source_type not in validation_rules:
        raise ValueError(f"Unknown source type {source_type}")
    
    rule = validation_rules[source_type]

    # All options across every source type, for the incompatibility pass below.
    all_options = set()
    for rule_set in validation_rules.values():
        all_options.update(rule_set['required'])
        all_options.update(rule_set['allowed'])

    # Required options (Perl's nonempty() logic)
    for option in rule['required']:
        if option == 'description':
            continue  # always available from config
        elif option == 'samplinginput':
            if not args_state.get('sampling'):
                raise ValueError(f"Error: --samplinginput must be specified and nonempty for fcl files that use source type {source_type}.")
        elif option == 'inputs':
            if not args_state.get('inputs_list'):
                raise ValueError(f"Error: --inputs must be specified and nonempty for fcl files that use source type {source_type}.")
        elif option == 'merge_factor':
            if not args_state.get('merge_factor') or args_state['merge_factor'] <= 0:
                raise ValueError(f"Error: --merge-factor must be specified and positive for fcl files that use source type {source_type}.")
        elif option == 'run_number':
            if args_state.get('run_number') is None:
                raise ValueError(f"Error: --run-number must be specified for fcl files that use source type {source_type}.")
        elif option == 'events_per_job':
            if args_state.get('events_per_job') is None:
                raise ValueError(f"Error: --events-per-job must be specified for fcl files that use source type {source_type}.")

    # Incompatible options (Perl's veto logic): anything not required/allowed
    # for this source type is rejected if the caller actually supplied it.
    for option in all_options:
        if option in rule['required'] or option in rule['allowed']:
            continue

        if option == 'samplinginput' and args_state.get('sampling'):
            raise ValueError(f"Error: --samplinginput is not compatible with fcl files that use source type {source_type}.")
        elif option == 'inputs' and args_state.get('inputs_list'):
            raise ValueError(f"Error: --inputs is not compatible with fcl files that use source type {source_type}.")
        elif option == 'merge_factor' and args_state.get('merge_factor') != 1:
            raise ValueError(f"Error: --merge-factor is not compatible with fcl files that use source type {source_type}.")
        elif option == 'run_number' and args_state.get('run_number') is not None:
            raise ValueError(f"Error: --run-number is not compatible with fcl files that use source type {source_type}.")
        elif option == 'events_per_job' and args_state.get('events_per_job') is not None:
            raise ValueError(f"Error: --events-per-job is not compatible with fcl files that use source type {source_type}.")


def _parse_job_args(job_args: List[str], template_path: str, config: Dict = None) -> Dict:
    """
    Parse mu2ejobdef CLI options and build complete TBS structure.
    Returns the tbs dict. Unknown tokens are ignored (historical behavior).
    """
    tbs: Dict[str, Any] = {}

    args_state = {
        'inputs_list': [],
        'merge_factor': 1,
        'auxin': {},
        'sampling': {},
        'run_number': None,
        'events_per_job': None,
        'fcl_mode': None,
        'fcl_template': None
    }

    def parse_counted_filelist(spec: str) -> Tuple[str, int, List[str]]:
        """Parse count:key:filelist (auxinput) / count:dsname:filelist
        (samplinginput) — same grammar for both."""
        n_str, key, filelist = spec.split(':', 2)
        all_files = _read_filelist(filelist)
        nreq = len(all_files) if n_str == 'all' else int(n_str)
        return key, nreq, all_files

    it = iter(job_args)
    for token in it:
        if token == '--inputs':
            args_state['inputs_list'] = _read_filelist(next(it))
        elif token == '--merge-factor':
            args_state['merge_factor'] = int(next(it))
        elif token == '--auxinput':
            key, nreq, files = parse_counted_filelist(next(it))
            args_state['auxin'][key] = (nreq, files)
        elif token == '--samplinginput':
            dsname, nreq, files = parse_counted_filelist(next(it))
            args_state['sampling'][dsname] = (nreq, files)
        elif token == '--run-number':
            args_state['run_number'] = int(next(it))
        elif token == '--events-per-job':
            args_state['events_per_job'] = int(next(it))
        elif token in ('--embed', '--include'):
            args_state['fcl_mode'] = token[2:]
            args_state['fcl_template'] = next(it)

    source_type = _get_source_type(template_path)

    # Skip for generic tarballs — no inputs list at creation time by design
    if not (config and config.get('generic_tarball')):
        _validate_options_for_source_type(source_type, args_state)

    if source_type == 'EmptyEvent':
        tbs['event_id'] = {
            'source.firstRun': args_state['run_number'],
            'source.maxEvents': args_state['events_per_job']
        }
        tbs['subrunkey'] = 'source.firstSubRun'
        
    elif source_type in ['RootInput', 'FromCorsikaBinary', 'FromSTMTestBeamData']:
        if args_state['inputs_list']:
            tbs['inputs'] = {'source.fileNames': [args_state['merge_factor'], args_state['inputs_list']]}
        tbs['subrunkey'] = ''  # subrun comes from the inputs

        if args_state['run_number'] is not None or args_state['events_per_job'] is not None:
            tbs['event_id'] = {}
            if args_state['run_number'] is not None:
                tbs['event_id']['source.firstRun'] = args_state['run_number']
            if args_state['events_per_job'] is not None:
                tbs['event_id']['source.maxEvents'] = args_state['events_per_job']
        elif source_type != 'FromCorsikaBinary':
            tbs['event_id'] = {'source.maxEvents': 2147483647}  # default: unlimited
            
    elif source_type == 'SamplingInput':
        if args_state['run_number'] is not None:
            tbs['event_id'] = {
                'source.run': args_state['run_number'],
                'source.maxEvents': 2147483647
            }
        tbs['subrunkey'] = 'source.subRun'

    elif source_type == 'PBISequence':
        # One text-chunk file per job. Up to MDC2025ai the pset validator
        # accepted only fileNames + runNumber and rejected source.maxEvents /
        # firstSubRunNumber / firstEventNumber. MDC2025aj (Offline PR #1799 +
        # Production #533, merged 2026-04-15) adds firstSubRunNumber and
        # firstEventNumber as optional atoms (default 0), so per-index offsets
        # via `event_id_per_index` are accepted there; maxEvents is still
        # rejected. Sequencer uniqueness comes from the input chunk basename
        # (e.g. ".00" in dts.mu2e.PBINormal_33344.MDC2025ac.00.txt) — no
        # subrunkey needed.
        has_inputs = bool(args_state.get('inputs_list'))
        has_chunk_mode = bool(config and config.get('chunk_mode'))
        if not (has_inputs or has_chunk_mode):
            raise ValueError(
                "PBISequence source requires either 'inputs' + 'merge_factor' "
                "(for SAM-tracked or dir:-mode inputs) or 'chunk_mode' "
                "(for on-the-fly grid chunking) in the config."
            )
        if args_state.get('run_number') is None:
            raise ValueError("PBISequence source requires 'run' in the config.")
        if has_inputs:
            tbs['inputs'] = {'source.fileNames': [args_state['merge_factor'], args_state['inputs_list']]}
        tbs['event_id'] = {
            'source.runNumber': args_state['run_number'],
        }
        tbs['subrunkey'] = ''  # explicit: no per-job subrun assignment

    # Sampling table, for whichever source type carries it. Deliberately
    # OUTSIDE the chain above: it used to sit inside the PBISequence branch,
    # which the validator vetoes --samplinginput on, so SamplingInput (the
    # type that REQUIRES it) reached no writer and produced a cnf that
    # silently resamples nothing. The validator already decides which
    # source types may carry sampling; this only writes it.
    if args_state['sampling']:
        samplingintable = {}
        for dsname, (nreq, filelist) in args_state['sampling'].items():
            inputkey = f'source.dataSets.{dsname}.fileNames'
            samplingintable[inputkey] = [nreq, filelist]
        tbs['samplinginput'] = samplingintable

    # _get_output_modules returns only active module names;
    # _add_outfile creates tbs['outfiles'] on first add.
    for mod in _get_output_modules(template_path):
        output_key = f'outputs.{mod}.fileName'
        filename_pattern = _get_fcl_value(template_path, output_key)

        if filename_pattern and filename_pattern.strip():
            defer_keys = config.get('_defer_keys', set()) if config else set()
            _add_outfile(tbs, output_key, filename_pattern, config, defer_keys=defer_keys)
        else:
            # Shouldn't happen in a resolved template; fail like Perl does.
            raise ValueError(f"Error: {output_key} is not defined")

    try:
        tfileservice_filename = _get_fcl_value(template_path, 'services.TFileService.fileName')
        if tfileservice_filename and tfileservice_filename.strip() and tfileservice_filename.strip() != '/dev/null':
            defer_keys = config.get('_defer_keys', set()) if config else set()
            _add_outfile(tbs, 'services.TFileService.fileName', tfileservice_filename, config, defer_keys=defer_keys)
    except subprocess.CalledProcessError:
        pass  # not defined; skip

    if args_state['auxin']:
        tbs['auxin'] = args_state['auxin']

    if _seed_needed(template_path):
        # String reference only; mu2ejobfcl resolves it to the actual baseSeed.
        tbs['seed'] = 'services.SeedService.baseSeed'

    if 'sequential_aux' in config:
        tbs['sequential_aux'] = config['sequential_aux']

    # sequencer_from_index: generate sequencers from job index instead of
    # input files. Fixes different indices producing the same output filename.
    if 'sequencer_from_index' in config:
        tbs['sequencer_from_index'] = config['sequencer_from_index']

    # event_id_per_index: per-job linear overrides, e.g.
    # {"source.firstEventNumber": {"offset": 0, "step": 1000}}, evaluated as
    # value = offset + index * step. Added for PBISequence (firstEventNumber
    # must be globally unique across chunks) but generic to any integer key.
    if 'event_id_per_index' in config:
        tbs['event_id_per_index'] = config['event_id_per_index']

    # chunk_mode: on-the-fly chunking at grid, e.g.
    # {"source": "/cvmfs/.../file.txt", "lines": 1000, "local_filename": "chunk.txt"}.
    # runmu2e reads this from jobpars at grid time, extracts the per-job slice
    # into local_filename before mu2e runs; the FCL points at local_filename
    # via fcl_overrides (set by json2jobdef).
    if 'chunk_mode' in config:
        tbs['chunk_mode'] = config['chunk_mode']

    return _reorder(tbs, ['outfiles', 'subrunkey', 'auxin', 'inputs',
                          'event_id', 'seed', 'samplinginput'])


def get_output_dataset_names(config: Dict) -> List[str]:
    """Extract output dataset names by parsing the FCL template.

    Creates a temporary template.fcl, uses fhicl-get to pull output module
    filenames, resolves placeholders, and derives SAM dataset names, e.g.
    ['mcs.mu2e.DIOtail0_60Mix1BB-KL.Run1Bah_best_v1_4-001.art'].
    """
    from utils.prod_utils import write_fcl_template

    fcl_path = config['fcl']
    write_fcl_template(fcl_path, config.get('fcl_overrides', {}))

    template_path = 'template.fcl'
    datasets = []

    try:
        output_mods = _get_output_modules(template_path)
        for mod in output_mods:
            try:
                pattern = _run_fhicl_get(
                    template_path, '--atom-as', f'outputs.{mod}.fileName')
            except subprocess.CalledProcessError as e:
                # An output module on an end path with no fileName is a
                # template bug, not an "unknown dataset".
                raise RuntimeError(
                    f"active output module '{mod}' has no outputs.{mod}.fileName "
                    f"in {fcl_path}") from e
            resolved = _replace_placeholders(pattern, config)
            try:
                n = Mu2eName.parse(resolved)
            except ValueError:
                continue
            if n.is_file:
                datasets.append(str(n.dataset))
    finally:
        if os.path.exists(template_path):
            os.unlink(template_path)

    return datasets


def create_jobdef(config: Dict, fcl_path: str = 'template.fcl', job_args: List[str] = None, embed: bool = True, outdir: Optional[Path] = None, quiet: bool = False) -> Path:
    """Create a jobdef tarball (cnf.owner.desc.dsconf.0.tar), Perl parity.

    Embeds jobpars.json and mu2e.fcl; processes source types, output files,
    seeds, etc. Returns the Path to the created file.
    """
    owner = config.get('owner') or default_owner()

    if config.get('auto_description') is not None:
        desc = f"AutoDesc{config.get('auto_description', '')}"
    else:
        desc = config['desc']

    dsconf = config['dsconf']

    # Fail before building anything: a bad code tarball should cost one
    # command, not a cnf that only breaks on a worker.
    if config.get('code'):
        validate_code_tarball(config['code'])

    # --embed: a local file wins over FHICL_FILE_PATH (Perl parity).
    if embed and Path(fcl_path).exists():
        template_path = fcl_path
    else:
        template_path = resolve_fhicl_file(fcl_path)

    fcl_embed_mode = 'embed' if embed else 'include'

    base_args = []
    if config.get('run'):
        base_args.extend(['--run-number', str(config['run'])])
    if config.get('events'):
        base_args.extend(['--events-per-job', str(config['events'])])

    # job_args, minus embed/include (handled separately below)
    filtered_job_args = []
    it = iter(job_args or [])
    for arg in it:
        if arg in ['--embed', '--include']:
            next(it, None)  # skip its template-path argument
        else:
            filtered_job_args.append(arg)

    base_args.extend(filtered_job_args)

    all_args = base_args.copy()
    if embed:
        all_args.extend(['--embed', template_path])
    else:
        all_args.extend(['--include', template_path])

    # Equivalent mu2ejobdef command line, printed for debugging unless quiet.
    cmd_parts = ['mu2ejobdef']
    setup_arg = '--setup' if config.get('simjob_setup') else '--code'
    setup_val = config.get('simjob_setup') or config.get('code')
    cmd_parts.extend([setup_arg, setup_val])
    cmd_parts.extend([
        '--dsconf', dsconf,
        '--desc', desc,
        '--dsowner', owner
    ])
    cmd_parts.extend(base_args)
    cmd_parts.extend(['--embed' if embed else '--include', template_path])

    if not quiet:
        print(f"Python mu2ejobdef equivalent command:")
        print(' '.join(cmd_parts))

    tbs = _parse_job_args(all_args, template_path, config)

    # Embed the resolved job count so the tarball is self-descriptive.
    # Absent tbs.njobs = open-ended (generic tarball, or generator with no
    # declared count); readers then fall back to the submission map.
    embedded_njobs = _resolve_njobs(config, tbs)
    if embedded_njobs is not None:
        tbs['njobs'] = embedded_njobs
    
    # desc carries the auto_description resolution; tarball_append wins inside cnf_name
    final_outdir = Path(outdir) if outdir else None
    out_name = cnf_name(config, 'tar', desc=desc)
    out = final_outdir / out_name if final_outdir else Path(out_name)

    if out.exists():
        out.unlink()

    jobpars = _build_jobpars_json(config, tbs)

    temp_files = {}
    jobpars_path = Path(FILENAME_JSON)
    jobpars_json = json.dumps(jobpars, indent=3, separators=(', ', ' : ')) + "\n"
    jobpars_path.write_text(jobpars_json)
    temp_files[FILENAME_JSON] = jobpars_path

    tpl_path = Path(template_path)
    if not tpl_path.exists():
        raise FileNotFoundError(f"FCL template not found: {tpl_path}")

    _validate_fcl_template(template_path)

    mu2e_fcl_tmp = Path(FILENAME_FCL)

    if fcl_embed_mode == 'embed':
        fcl_content = tpl_path.read_text()
    else:
        # --include normally emits an #include directive, except a local
        # modified file (fcl_path == 'template.fcl') which is embedded directly.
        if fcl_path == 'template.fcl':
            fcl_content = tpl_path.read_text()
        else:
            fcl_content = f'#include "{fcl_path}"\n'

    mu2e_fcl_tmp.write_text(fcl_content)
    temp_files[FILENAME_FCL] = mu2e_fcl_tmp

    with tarfile.open(out, 'w:gz') as tar:
        for filename, filepath in temp_files.items():
            tar.add(filepath, arcname=filename)

    for filepath in temp_files.values():
        try:
            filepath.unlink()
        except OSError:
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
           --samplinginput "10:dataset1:sampling1.txt"

Note: For EmptyEvent source type, --run-number and --events-per-job are required, 
      and --inputs/--merge-factor are not allowed.
        """
    )
    
    setup_group = parser.add_mutually_exclusive_group(required=True)
    setup_group.add_argument('--setup', metavar='SCRIPT',
                            help='SimJob setup script path')
    setup_group.add_argument('--code', metavar='TARBALL',
                            help='Custom code tarball path')

    parser.add_argument('--dsconf', required=True,
                       help='Dataset configuration (e.g., MDC2020az)')

    desc_group = parser.add_mutually_exclusive_group(required=True)
    desc_group.add_argument('--desc', metavar='DESC',
                           help='Dataset description (e.g., CosmicCORSIKALow)')
    desc_group.add_argument('--auto-description', nargs='?', const='', metavar='SUFFIX',
                           help='Auto-extract description from input files (optional suffix)')

    parser.add_argument('--dsowner', required=True,
                       help='Dataset owner (e.g., mu2e)')

    fcl_group = parser.add_mutually_exclusive_group(required=True)
    fcl_group.add_argument('--embed', metavar='FCL',
                          help='Embed FCL template content in jobdef')
    fcl_group.add_argument('--include', metavar='FCL',
                          help='Include FCL template by reference in jobdef')

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
    parser.add_argument('--verbose', action='store_true',
                       help='Enable verbose output')
    parser.add_argument('--output-dir', metavar='DIR',
                       help='Output directory for jobdef tarball')
    
    args = parser.parse_args()

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

    job_args = []

    if args.inputs:
        job_args.extend(['--inputs', args.inputs])
    if args.merge_factor:
        job_args.extend(['--merge-factor', str(args.merge_factor)])
    if args.auxinput:
        for aux in args.auxinput:
            job_args.extend(['--auxinput', aux])
    if args.samplinginput:
        for spec in args.samplinginput:
            job_args.extend(['--samplinginput', spec])

    fcl_path = args.embed or args.include
    embed_mode = 'embed' if args.embed else 'include'

    try:
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