#!/usr/bin/env python3
"""
Mixing utilities for Mu2e production scripts.
"""

import copy
import itertools
from .prod_utils import *
from .samweb_wrapper import files_in_dataset
from .config_utils import _get_first_if_list, prepare_fields_for_job

def _create_pileup_catalog(dataset, filename):
    """Write a pileup catalog file listing all files from the given datasets.

    dataset: dict mapping dataset names to merge factors, e.g.
             {"dataset1": 100, "dataset2": 10}
    """
    if not isinstance(dataset, dict):
        raise ValueError(f"dataset must be a dict, got {type(dataset)}")
    
    all_files = []
    for ds, merge_factor in dataset.items():
        files = files_in_dataset(ds, with_events=True)
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
    "MixSeq": "Production/JobConfig/mixing/NoPrimaryPBISequence.fcl",
    "MixFlat": "Production/JobConfig/mixing/FlatPBI.fcl",
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

    config['pileup_datasets'] is a list containing one dict mapping dataset
    names to file counts, e.g.::

        [{"dts.mu2e.MuBeamFlashCat.MDC2025ac.art": 1,
          "dts.mu2e.EleBeamFlashCat.MDC2025ac.art": 25,
          "dts.mu2e.NeutralsFlashCat.MDC2025ac.art": 50,
          "dts.mu2e.MuStopPileupCat.MDC2025ac.art": 2}]

    Each count is how many files to use from that pileup catalog.
    Returns a list of command-line arguments for mu2ejobdef.
    """
    args = []
    pre_lines = []

    # pbeam-specific FCL include goes right after the base FCL (BEFORE
    # overrides) so fcl_overrides can actually override the pbeam settings
    pbeam = _get_first_if_list(config.get('pbeam'))
    if pbeam and pbeam in MIXING_FCL_INCLUDES:
        pre_lines.append(f'#include "{MIXING_FCL_INCLUDES[pbeam]}"')

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

    for mixer_type, datasets in mixer_datasets.items():
        # _map_dataset_to_mixer always returns a PILEUP_MIXERS key or raises
        mixer = PILEUP_MIXERS[mixer_type]

        pileup_list = f"{mixer_type}Cat.txt"
        _create_pileup_catalog(datasets, pileup_list)

        # MaxEventsToSkip and file count both come from the first dataset
        first_dataset = list(datasets.keys())[0]
        skip = max_events_to_skip(first_dataset)
        pre_lines.append(f"physics.filters.{mixer}.mu2e.MaxEventsToSkip: {skip}")

        cnt = list(datasets.values())[0]
        # mu2ejobdef selects the first `cnt` files from pileup_list
        args += ['--auxinput', f"{cnt}:physics.filters.{mixer}.fileNames:{pileup_list}"]

    write_fcl_template(config['fcl'],
                       _get_first_if_list(config.get('fcl_overrides', {})),
                       pre_lines=pre_lines)
    return args

def _job_type_for_config(job):
    """Determine job type from config content (e.g. mixing if pbeam present)."""
    return 'mixing' if ('pbeam' in job) else 'standard'


def expand_configs(configs):
    """Expand a list of config dicts into individual job configurations.

    Job type (mixing vs standard) is determined per config from content
    (e.g. pbeam), so desc gets pbeam appended for mixing jobs regardless
    of filename.

    One expansion path handles every shape: all-list, mixed list/non-list,
    and fully-scalar configs.
    """
    all_jobs = []

    for i, config in enumerate(configs):
        if not isinstance(config, dict):
            raise ValueError(f"Configuration at index {i} is not a dictionary: {type(config)} - {config}")

        list_fields = {k: v for k, v in config.items() if isinstance(v, list)}
        non_list_fields = {k: v for k, v in config.items() if not isinstance(v, list)}

        for key, value in list_fields.items():
            if len(value) == 0:
                raise ValueError(f"List for key '{key}' is empty. All lists must have at least one value.")

        if not list_fields:
            # already scalar-only: add directly
            config = prepare_fields_for_job(config, _job_type_for_config(config))
            all_jobs.append(config)
            continue

        # cartesian product over list fields; non-list fields stay constant
        param_names = list(list_fields.keys())

        for combination in itertools.product(*list_fields.values()):
            job = dict(zip(param_names, combination))
            job.update(copy.deepcopy(non_list_fields))  # deep copy: avoid shared refs

            # keep fcl_overrides independent per job
            if 'fcl_overrides' in job:
                job['fcl_overrides'] = copy.deepcopy(_get_first_if_list(config.get('fcl_overrides', {})))

            # Auto-generate desc; use mixing if this config has pbeam
            job = prepare_fields_for_job(job, _job_type_for_config(job))

            all_jobs.append(job)

    return all_jobs


