#!/usr/bin/env python3
"""Utilities for processing job configuration dicts: description extraction
and auto-generation from input data."""

import copy
from typing import List, NamedTuple, Optional

from utils.job_common import Mu2eName, default_owner


class InputSpec(NamedTuple):
    """One normalized input_data entry. `per_job` is None only for dict
    specs carrying neither count nor merge_factor (split/chunk shapes, or
    malformed merge specs — the consumer decides which error applies)."""
    source: str
    per_job: Optional[int]
    random: bool
    max_nfiles: Optional[int]
    split_lines: Optional[int]
    chunk_lines: Optional[int]


_INPUT_SPEC_KEYS = {'count', 'merge_factor', 'random', 'max_nfiles',
                    'split_lines', 'chunk_lines'}


def normalize_input_data(input_data) -> List[InputSpec]:
    """Parse the `input_data` config field into InputSpec entries — the
    single home of the field's shape grammar. Accepted shapes:

        {source: N}                                  merge factor N per job
        {source: {"count"|"merge_factor": N,
                  "random": bool, "max_nfiles": M}}  SAM selection spec
        {source: {"split_lines": N}}                 pre-split local text file
        {source: {"chunk_lines": N}}                 chunk-on-grid (tbs.chunk_mode)

    Fails loud on non-dict input_data, unknown spec keys, and non-positive
    max_nfiles. Entry order is preserved (consumers key off the first)."""
    if not isinstance(input_data, dict):
        raise ValueError(f"input_data must be a dict, got {type(input_data)}")
    specs = []
    for source, value in input_data.items():
        if isinstance(value, dict):
            unknown = set(value) - _INPUT_SPEC_KEYS
            if unknown:
                raise ValueError(
                    f"input_data spec for {source}: unknown key(s) {sorted(unknown)} "
                    f"(known: {sorted(_INPUT_SPEC_KEYS)})")
            max_nfiles = value.get('max_nfiles')
            if max_nfiles is not None and (not isinstance(max_nfiles, int) or max_nfiles <= 0):
                raise ValueError(
                    f"input_data spec for {source}: max_nfiles must be a positive int, got {max_nfiles!r}")
            per_job = value.get('count') or value.get('merge_factor')
            specs.append(InputSpec(source,
                                   int(per_job) if per_job is not None else None,
                                   bool(value.get('random')),
                                   max_nfiles,
                                   value.get('split_lines'),
                                   value.get('chunk_lines')))
        else:
            specs.append(InputSpec(source, int(value), False, None, None, None))
    return specs


def _get_first_if_list(value):
    """Helper: get first element if value is a list, otherwise return value."""
    return value[0] if isinstance(value, list) and value else value


def mixing_desc(input_desc: str, pbeam: str) -> str:
    """Derived desc for a mixing job: input description + beam-intensity
    tag. Single home of the rule — prepare_fields_for_job derives it at
    generation time and chain_emit reconstructs it for --skip-produced
    matching; the two must agree or skip-dedup fails open."""
    return input_desc + pbeam


def prepare_fields_for_job(config, job_type='standard'):
    """Return a copy of config with `desc` auto-generated from input_data
    (and, for job_type='mixing', pbeam) if not already set."""
    modified_config = copy.deepcopy(config)

    if 'desc' in config and config['desc']:
        return modified_config

    input_data = _get_first_if_list(config.get('input_data', ''))
    if not input_data:
        raise ValueError("input_data is required to auto-generate desc")

    if isinstance(input_data, dict):
        # Dict form: validate the whole shape, take the first source
        dataset_name = normalize_input_data(input_data)[0].source
    else:
        dataset_name = input_data  # old format: string dataset name

    # Dataset name format: tier.owner.desc.dsconf.ext (5 parts)
    n = Mu2eName.parse(dataset_name)
    if not n.is_dataset:
        raise ValueError(f"Invalid dataset name format: '{dataset_name}'. Expected 5 dot-separated fields (tier.owner.desc.dsconf.ext)")
    dsdesc = n.description  # e.g., "CosmicSignal" from "dts.mu2e.CosmicSignal.MDC2025ac.art"

    if job_type == 'mixing':
        pbeam = _get_first_if_list(config.get('pbeam', ''))
        modified_config['desc'] = mixing_desc(dsdesc, pbeam)
    else:
        modified_config['desc'] = dsdesc

    return modified_config


def get_tarball_desc(config):
    """Tarball description string: base_desc + tarball_append if specified,
    else None."""
    if 'tarball_append' not in config:
        return None

    base_desc = config.get('desc') or prepare_fields_for_job(config, job_type='standard').get('desc')
    return base_desc + config['tarball_append']


def cnf_name(config, extension='tar', desc=None, dataset=False):
    """Canonical cnf name for a config — the single home of the cnf-name
    contract: json2jobdef's parfile/dataset names and jobdef's written
    tarball must be byte-identical, or a --prod push registers a map entry
    whose tarball was never written. Mu2eName.build validates fields (a
    desc/dsconf containing '.' fails loudly here instead of producing an
    unparseable name downstream).

    Args:
        desc: already-resolved base description override (create_jobdef's
              auto_description path); defaults to config['desc'].
              tarball_append still wins via get_tarball_desc.
        dataset: True for the 5-field dataset form (no version sequencer).
    """
    base = get_tarball_desc(config) or desc or config['desc']
    kwargs = {} if dataset else {'sequencer': str(config.get('version', 0))}
    return str(Mu2eName.build(tier='cnf',
                              owner=config.get('owner') or default_owner(),
                              description=base, dsconf=config['dsconf'],
                              extension=extension, **kwargs))
