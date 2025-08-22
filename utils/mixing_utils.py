#!/usr/bin/env python3
"""
Mixing utilities for Mu2e production scripts.
"""

import copy
import json
import sys
import itertools
from .prod_utils import *

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

def build_pileup_args(config):
    """Build command-line arguments for pileup mixing configuration."""
    args = []
    with open('template.fcl', 'a') as f:
        # Set output filenames for mixing jobs  
        f.write(f"outputs.TriggeredOutput.fileName: \"dig.{config['owner']}.{config['desc']}Triggered.{config['dsconf']}.sequencer.art\"\n")
        f.write(f"outputs.TriggerableOutput.fileName: \"dig.{config['owner']}.{config['desc']}Triggerable.{config['dsconf']}.sequencer.art\"\n")
        
        # Process pileup datasets
        for key, mixer in PILEUP_MIXERS.items():
            ds = config.get(f"{key}_dataset")
            cnt = config.get(f"{key}_count", 0)
            if not ds or cnt <= 0:
                continue
            pileup_list = f"{key}Cat.txt"
            # Get ALL pileup files from the dataset and write them to the catalog
            from .samweb_wrapper import list_files
            result = list_files(f"dh.dataset={ds} and event_count>0")
            with open(pileup_list, 'w') as pf:
                pf.write('\n'.join(result))
            nfiles, nevts = get_def_counts(ds)
            skip = nevts // nfiles if nfiles > 0 else 0
            print(f"physics.filters.{mixer}.mu2e.MaxEventsToSkip: {skip}", file=f)
            # Use the JSON count parameter - mu2ejobdef will select the first cnt files from the full list
            args += ['--auxinput', f"{cnt}:physics.filters.{mixer}.fileNames:{pileup_list}"]
    return args

def prepare_fields_for_mixing(config):
    """Prepare job configuration for mixing by adding mixing-specific fields."""
    # Create a copy of the config to modify
    modified_config = copy.deepcopy(config)
    
    # Extract desc field from input_data and pbeam
    dsdesc = config['input_data'].split('.')[2]
    modified_config['desc'] = dsdesc + config['pbeam']
    
    # Add pbeam-specific FCL includes
    if config['pbeam'] not in MIXING_FCL_INCLUDES:
        raise ValueError(f"pbeam value '{config['pbeam']}' is not supported. Supported values: {list(MIXING_FCL_INCLUDES.keys())}")
    
    modified_config['fcl_overrides'].setdefault('#include', []).append(MIXING_FCL_INCLUDES[config['pbeam']])
    
    return modified_config



def expand_configs(configs, mixing=False):
    """
    Expand configurations into individual job configurations.
    
    Args:
        configs: List of configuration dictionaries
        mixing: Whether to apply mixing-specific modifications
        
    Returns:
        List of expanded job configurations
    """
    # Generate jobs for each configuration
    all_jobs = []
    
    for config in configs:
        # Validate all values are lists
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
            # Modify job for mixing if requested
            if mixing:
                job = prepare_fields_for_mixing(job)
            
            all_jobs.append(job)
            print(json.dumps(job, indent=2))

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
    
    # Expand configurations with mixing enabled
    return expand_configs(configs, mixing=True)


