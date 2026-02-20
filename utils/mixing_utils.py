#!/usr/bin/env python3
"""
Mixing utilities for Mu2e production scripts.
"""

import copy
import json
import sys
import itertools
from .prod_utils import *
from .samweb_wrapper import list_files
from .config_utils import _get_first_if_list, prepare_fields_for_job, get_tarball_desc

def _create_pileup_catalog(dataset, filename):
    """Helper: create pileup catalog file from datasets with merge factors.
    
    Args:
        dataset: dict mapping dataset names to merge factors
                 e.g., {"dataset1": 100, "dataset2": 10}
        filename: Output filename for the catalog
    """
    if not isinstance(dataset, dict):
        raise ValueError(f"dataset must be a dict, got {type(dataset)}")
    
    all_files = []
    for ds, merge_factor in dataset.items():
        files = list_files(f"dh.dataset={ds} and event_count>0")
        all_files.extend(files)
    
    with open(filename, 'w') as f:
        f.write('\n'.join(all_files))

# Pileup mixer configurations
PILEUP_MIXERS = {
    'mubeam': 'MuBeamFlashMixer',
    'elebeam': 'EleBeamFlashMixer',
    'neutrals': 'NeutralsFlashMixer',
    'mustop': 'MuStopPileupMixer',
}

# Mixing-specific FCL includes
MIXING_FCL_INCLUDES = {
    "Mix1BB": "Production/JobConfig/mixing/OneBB.fcl",
    "Mix2BB": "Production/JobConfig/mixing/TwoBB.fcl", 
    "MixLow": "Production/JobConfig/mixing/LowIntensity.fcl",
    "MixSeq": "Production/JobConfig/mixing/NoPrimaryPBISequence.fcl"
}

def _map_dataset_to_mixer(dataset_name):
    """Map dataset name to mixer type based on dataset name patterns."""
    dataset_lower = dataset_name.lower()
    
    if 'mubeam' in dataset_lower or 'muonbeam' in dataset_lower:
        return 'mubeam'
    elif 'elebeam' in dataset_lower or 'electronbeam' in dataset_lower:
        return 'elebeam'
    elif 'neutral' in dataset_lower:
        return 'neutrals'
    elif 'mustop' in dataset_lower or 'muonstop' in dataset_lower:
        return 'mustop'
    else:
        raise ValueError(f"Could not determine mixer type for dataset: {dataset_name}")

def build_pileup_args(config):
    """Build command-line arguments for pileup mixing configuration.
    
    Args:
        config: Configuration dictionary with the following structure:
            - pileup_datasets: list containing dict mapping dataset names to file counts
              e.g., [{
                "dts.mu2e.MuBeamFlashCat.MDC2025ac.art": 1,
                "dts.mu2e.EleBeamFlashCat.MDC2025ac.art": 25,
                "dts.mu2e.NeutralsFlashCat.MDC2025ac.art": 50,
                "dts.mu2e.MuStopPileupCat.MDC2025ac.art": 2
              }]
              The count value specifies how many files to use from each pileup catalog.
    
    Returns:
        List of command-line arguments for mu2ejobdef
    """
    args = []
    
    # Always create template.fcl fresh for mixing jobs
    with open('template.fcl', 'w') as f:
        # Write base include directive
        f.write(f'#include "{config["fcl"]}"\n')
        
        # Get pileup datasets dict (extract from list if needed)
        pileup_datasets = _get_first_if_list(config.get('pileup_datasets', [{}]))
        
        if not isinstance(pileup_datasets, dict):
            raise ValueError(f"pileup_datasets must be a list containing a dict, got {type(config.get('pileup_datasets'))}")
        
        if not pileup_datasets:
            raise ValueError("No mixing component datasets found. Expected pileup_datasets field.")
        
        # Group datasets by mixer type
        mixer_datasets = {}
        for dataset, merge_factor in pileup_datasets.items():
            mixer_type = _map_dataset_to_mixer(dataset)
            if mixer_type not in mixer_datasets:
                mixer_datasets[mixer_type] = {}
            mixer_datasets[mixer_type][dataset] = merge_factor
        
        # Process each mixer type
        for mixer_type, datasets in mixer_datasets.items():
            mixer = PILEUP_MIXERS.get(mixer_type)
            if not mixer:
                continue
            
            pileup_list = f"{mixer_type}Cat.txt"
            
            # Create pileup catalog for this mixer type
            _create_pileup_catalog(datasets, pileup_list)
            # Use the first dataset for MaxEventsToSkip calculation
            first_dataset = list(datasets.keys())[0]
            nfiles, nevts = get_def_counts(first_dataset)
            skip = nevts // nfiles if nfiles > 0 else 0
            print(f"physics.filters.{mixer}.mu2e.MaxEventsToSkip: {skip}", file=f)
            
            # Use the merge factor from the first dataset as the count
            cnt = list(datasets.values())[0]
            # Use the JSON count parameter - mu2ejobdef will select the first cnt files from the full list
            args += ['--auxinput', f"{cnt}:physics.filters.{mixer}.fileNames:{pileup_list}"]
        
        # Add FCL overrides
        fcl_overrides = _get_first_if_list(config.get('fcl_overrides', {}))
        
        if fcl_overrides:
            for key, val in fcl_overrides.items():
                if key == '#include':
                    includes = val if isinstance(val, list) else [val]
                    for inc in includes:
                        f.write(f'#include "{inc}"\n')
                else:
                    if isinstance(val, str) and not val.startswith('"') and not val.isdigit():
                        f.write(f'{key}: "{val}"\n')
                    else:
                        f.write(f'{key}: {val}\n')
        
        # Add pbeam-specific FCL include based on the pbeam field
        pbeam = _get_first_if_list(config.get('pbeam'))
        if pbeam and pbeam in MIXING_FCL_INCLUDES:
            f.write(f'#include "{MIXING_FCL_INCLUDES[pbeam]}"\n')
        
        # Add output filename overrides for mixing jobs (after base FCL include)
        # This ensures they override the default values from the base templates
        owner = _get_first_if_list(config.get('owner', 'mu2e'))
        desc = _get_first_if_list(config.get('desc', 'unknown'))
        dsconf = _get_first_if_list(config.get('dsconf', 'unknown'))
        f.write(f'outputs.TriggeredOutput.fileName: "dig.{owner}.{desc}Triggered.{dsconf}.sequencer.art"\n')
        f.write(f'outputs.TriggerableOutput.fileName: "dig.{owner}.{desc}Triggerable.{dsconf}.sequencer.art"\n')
    
    return args

def prepare_fields_for_mixing(config):
    """Prepare job configuration for mixing by adding mixing-specific fields."""
    # Create a copy of the config to modify
    modified_config = copy.deepcopy(config)
    
    # Extract desc field from input_data and pbeam
    input_data = _get_first_if_list(config['input_data'])
    dsdesc = input_data.split('.')[2] if input_data else "unknown"
    
    pbeam = _get_first_if_list(config['pbeam'])
    modified_config['desc'] = dsdesc + pbeam



def _job_type_for_config(job):
    """Determine job type from config content (e.g. mixing if pbeam present)."""
    return 'mixing' if ('pbeam' in job) else 'standard'


def expand_configs(configs, mixing=False):
    """
    Expand configurations into individual job configurations.
    Job type (mixing vs standard) is determined per config from content (e.g. pbeam),
    so desc gets pbeam appended for mixing jobs regardless of filename.
    
    Args:
        configs: List of configuration dictionaries
        mixing: Deprecated, ignored. Kept for backward compatibility.
        
    Returns:
        List of expanded job configurations
    """
    # Generate jobs for each configuration
    all_jobs = []

    for i, config in enumerate(configs):
        # Validate that each config is a dictionary
        if not isinstance(config, dict):
            raise ValueError(f"Configuration at index {i} is not a dictionary: {type(config)} - {config}")

        # Check if this config is already expanded (has non-list values)
        has_non_lists = any(not isinstance(value, list) for value in config.values())

        if has_non_lists:
            # Config has mixed list and non-list values - need partial expansion
            # Find which fields are lists and need expansion
            list_fields = {k: v for k, v in config.items() if isinstance(v, list)}
            non_list_fields = {k: v for k, v in config.items() if not isinstance(v, list)}

            if list_fields:
                # Generate combinations for list fields, keeping non-list fields constant
                param_names = list(list_fields.keys())
                param_values = list(list_fields.values())

                for combination in itertools.product(*param_values):
                    # Create job with this combination
                    job = dict(zip(param_names, combination))
                    # Add the non-list fields (create deep copy to avoid reference issues)
                    job.update(copy.deepcopy(non_list_fields))

                    # Ensure fcl_overrides is completely fresh for each job
                    if 'fcl_overrides' in job:
                        job['fcl_overrides'] = copy.deepcopy(_get_first_if_list(config.get('fcl_overrides', {})))

                    # Auto-generate desc; use mixing if this config has pbeam
                    job = prepare_fields_for_job(job, _job_type_for_config(job))

                    all_jobs.append(job)
            else:
                # All values are non-list, just add directly
                config = prepare_fields_for_job(config, _job_type_for_config(config))
                all_jobs.append(config)
            continue

        # Validate all values are lists for expansion
        for key, value in config.items():
            if not isinstance(value, list):
                raise ValueError(f"All values must be lists. Found non-list value for key '{key}': {value}")
            if len(value) == 0:
                raise ValueError(f"List for key '{key}' is empty. All lists must have at least one value.")

        # Generate all combinations of list parameters
        param_names = list(config.keys())

        for combination in itertools.product(*config.values()):
            # Create job with this combination
            job = dict(zip(param_names, combination))
            # Auto-generate desc; use mixing if this config has pbeam
            job = prepare_fields_for_job(job, _job_type_for_config(job))

            all_jobs.append(job)

    return all_jobs

def expand_mix_config(json_path):
    """Expand mixing configuration using expand_configs."""
    # Load JSON config
    if not json_path.exists():
        raise FileNotFoundError(f"JSON file not found: {json_path}")
    
    try:
        with json_path.open() as f:
            configs = json.load(f)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse JSON file {json_path}: {e}")
    
    # Validate JSON structure
    if not isinstance(configs, list):
        raise ValueError(f"JSON file {json_path} must contain a list of configurations, got {type(configs)}")
    
    if len(configs) == 0:
        raise ValueError(f"JSON file {json_path} contains no configurations")
    
    # Expand configurations with mixing enabled
    return expand_configs(configs, mixing=True)


