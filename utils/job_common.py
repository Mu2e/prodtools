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

    def relpathname(self) -> str:
        """SHA256 hash-prefixed relative path, matching Perl Mu2eFilename->relpathname()."""
        h = hashlib.sha256(self.filename.encode()).hexdigest()
        return f"{h[:2]}/{h[2:4]}/{self.filename}"

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
    tarballs, generating deterministic random numbers, and computing the
    per-job input file lists (primary / aux / sampling).
    """

    def __init__(self, jobdef_path: str):
        """Initialize with path to job definition tarball; extract jobpars.json."""
        self.jobdef = jobdef_path
        self.json_data = self._extract_json()

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

    def job_primary_inputs(self, index):
        """Get primary input files for job index.

        `tbs.inputs` maps each dataset to a (merge, filelist) tuple. Slices
        the filelist by `[index*merge : index*merge+merge]` (clamped at end).
        Raises ValueError if `index` is past the end.
        Returns {} if no primary inputs configured.
        """
        tbs = self.json_data.get('tbs', {})
        inputs = tbs.get('inputs')
        if not inputs:
            return {}

        result = {}
        for dataset, (merge, filelist) in inputs.items():
            nf = len(filelist)
            first = index * merge
            last = min(first + merge - 1, nf - 1)
            if first > last:
                raise ValueError(f"job_primary_inputs(): invalid index {index}")
            result[dataset] = filelist[first:last + 1]

        return result

    def job_aux_inputs(self, index):
        """Get auxiliary input files for job index.

        `tbs.auxin` maps each dataset to (nreq, infiles). When
        `tbs.sequential_aux` is True, slice deterministically with rollover;
        otherwise sample `nreq` files without repetition using `_my_random`.
        Returns {} if no auxin configured.
        """
        tbs = self.json_data.get('tbs', {})
        auxin = tbs.get('auxin')
        if not auxin:
            return {}

        sequential_aux = tbs.get('sequential_aux', False)

        result = {}
        for dataset, (nreq, infiles) in auxin.items():
            if nreq == 0:
                nreq = len(infiles)

            if sequential_aux:
                nf = len(infiles)
                first = index * nreq
                last = min(first + nreq - 1, nf - 1)
                if first >= nf:
                    first = first % nf
                    last = min(first + nreq - 1, nf - 1)
                if first > last:
                    raise ValueError(f"job_aux_inputs(): invalid index {index} for sequential selection")
                result[dataset] = infiles[first:last + 1]
            else:
                sample = []
                available_files = infiles.copy()
                for _ in range(nreq):
                    if not available_files:
                        break
                    rnd = self._my_random(index, *available_files)
                    file_index = rnd % len(available_files)
                    sample.append(available_files[file_index])
                    available_files.pop(file_index)
                result[dataset] = sample

        return result

    def job_sampling_inputs(self, index):
        """Get sampling input files for job index.

        `tbs.samplinginput` maps each dataset to (nreq, filelist), sliced
        sequentially by index. Returns {} if no sampling input configured.
        """
        tbs = self.json_data.get('tbs', {})
        samplinginput = tbs.get('samplinginput')
        if not samplinginput:
            return {}

        result = {}
        for dataset, (nreq, filelist) in samplinginput.items():
            if nreq == 0:
                nreq = len(filelist)
            nf = len(filelist)
            first = index * nreq
            last = min(first + nreq - 1, nf - 1)
            if first > last:
                raise ValueError(f"job_sampling_inputs(): invalid index {index}")
            result[dataset] = filelist[first:last + 1]

        return result

    def job_inputs(self, index):
        """Get all input files for job index — merged primary + aux + sampling."""
        result = {}
        result.update(self.job_primary_inputs(index))
        result.update(self.job_aux_inputs(index))
        result.update(self.job_sampling_inputs(index))
        return result


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

