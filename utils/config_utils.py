#!/usr/bin/env python3
"""
Configuration utilities for Mu2e production scripts.

This module provides utilities for processing job configuration dictionaries,
including description extraction and auto-generation from input data.
"""

import copy


def _get_first_if_list(value):
    """Helper: get first element if value is a list, otherwise return value."""
    return value[0] if isinstance(value, list) and value else value


def prepare_fields_for_job(config, job_type='standard'):
    """Prepare job configuration by auto-generating desc from input_data and optional pbeam.
    
    Args:
        config: Configuration dictionary
        job_type: 'standard' or 'mixing'
        
    Returns:
        Modified copy of config with desc populated
    """
    # Create a copy of the config to modify
    modified_config = copy.deepcopy(config)
    
    # If desc is already present, don't override it
    if 'desc' in config and config['desc']:
        return modified_config
    
    # Auto-generate desc from input_data
    input_data = _get_first_if_list(config.get('input_data', ''))
    if not input_data:
        raise ValueError("input_data is required to auto-generate desc")
    
    if isinstance(input_data, dict):
        # New format: dict with dataset names as keys
        dataset_name = list(input_data.keys())[0]
    else:
        # Old format: string dataset name
        dataset_name = input_data
    
    # Dataset name format: tier.owner.desc.dsconf.ext (5 parts)
    parts = dataset_name.split('.')
    if len(parts) != 5:
        raise ValueError(f"Invalid dataset name format: '{dataset_name}'. Expected 5 dot-separated fields (tier.owner.desc.dsconf.ext)")
    
    dsdesc = parts[2]  # e.g., "CosmicSignal" from "dts.mu2e.CosmicSignal.MDC2025ac.art"
    
    # For mixing jobs, append pbeam to the desc
    if job_type == 'mixing':
        pbeam = _get_first_if_list(config.get('pbeam', ''))
        modified_config['desc'] = dsdesc + pbeam
    else:
        # For standard jobs (digi, reco, ntuple, etc.), just use the dataset name
        modified_config['desc'] = dsdesc
    
    return modified_config


def get_tarball_desc(config):
    """Get description for tarball naming.
    
    Args:
        config: Configuration dictionary
        
    Returns:
        Tarball description string: base_desc + tarball_append (if specified), or None
    """
    if 'tarball_append' not in config:
        return None
    
    base_desc = config.get('desc') or prepare_fields_for_job(config, job_type='standard').get('desc')
    return base_desc + config['tarball_append']
