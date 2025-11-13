#!/usr/bin/env python3
"""
Shared base classes and utilities for Mu2e production tools.

This module consolidates common functionality that was previously duplicated
across multiple files to reduce code redundancy and ensure consistency.
"""

import json
import tarfile
import hashlib
from typing import Dict


class Mu2eFilename:
    """Parse and manipulate Mu2e filenames.
    
    Consolidated implementation from jobfcl.py and jobiodetail.py.
    Handles the standard Mu2e filename format:
    tier.owner.description.dsconf.sequencer.extension
    Example: dts.mu2e.CosmicCRYExtracted.MDC2020av.001205_00000000.art
    """
    
    def __init__(self, filename: str):
        self.filename = filename
        self._parse()
    
    def _parse(self):
        """Parse filename into components."""
        # Format: tier.owner.description.dsconf.sequencer.extension
        # Example: dts.mu2e.CosmicCRYExtracted.MDC2020av.001205_00000000.art
        parts = self.filename.split('.')
        if len(parts) < 6:
            raise ValueError(f"Invalid filename format: expected 6+ dot-separated fields, got {len(parts)} in '{self.filename}'")
        self.tier = parts[0]
        self.owner = parts[1]
        self.description = parts[2]
        self.dsconf = parts[3]
        self.sequencer = parts[4]
        self.extension = parts[5] if len(parts) > 5 else ''
    
    def basename(self) -> str:
        """Return the basename of the file."""
        return self.filename

def remove_storage_prefix(path: str) -> str:
    """Remove storage system prefixes (enstore:, dcache:) from a file path.
    
    Args:
        path: File path that may have storage prefix
    
    Returns:
        Path with storage prefix removed
    """
    if path.startswith('enstore:'):
        return path[8:]
    elif path.startswith('dcache:'):
        return path[7:]
    return path


class Mu2eJobBase:
    """Base class for Mu2e job handling classes.
    
    Provides common functionality for extracting data from job definition
    tarballs and generating deterministic random numbers.
    """
    
    def __init__(self, jobdef_path: str):
        """Initialize with path to job definition tarball."""
        self.jobdef = jobdef_path
    
    def _extract_json(self) -> dict:
        """Extract jobpars.json from tar file.
        
        Consolidated implementation from jobfcl.py, jobiodetail.py, and jobquery.py.
        """
        with tarfile.open(self.jobdef, 'r') as tar:
            # Find jobpars.json member
            json_member = None
            for member in tar.getmembers():
                if member.name.endswith('jobpars.json'):
                    json_member = member
                    break
            
            if not json_member:
                raise ValueError(f"jobpars.json not found in {self.jobdef}")
            
            # Extract and return JSON content
            json_file = tar.extractfile(json_member)
            return json.load(json_file)
    
    def _my_random(self, *args) -> int:
        """Generate deterministic random number from inputs.
        
        Consolidated implementation from jobfcl.py and jobiodetail.py.
        Uses SHA256 hash to create deterministic pseudo-random numbers.
        """
        h = hashlib.sha256()
        for arg in args:
            h.update(str(arg).encode())
        # Take first 8 hex digits (32 bits)
        return int(h.hexdigest()[:8], 16)


def get_samweb_wrapper():
    """Get SAM web wrapper instance with consistent import handling.
    
    Returns:
        SAMWebWrapper instance
    """
    try:
        from .samweb_wrapper import get_samweb_wrapper as _get_samweb_wrapper
    except ImportError:
        from utils.samweb_wrapper import get_samweb_wrapper as _get_samweb_wrapper
    return _get_samweb_wrapper()

