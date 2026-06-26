#!/usr/bin/env python3
"""
Shared base classes and utilities for Mu2e production tools.

This module consolidates common functionality that was previously duplicated
across multiple files to reduce code redundancy and ensure consistency.
"""

import json
import re
import tarfile
import hashlib
from typing import Dict, Optional


# Mu2e dataset path puts every tier under one of four umbrella owner-classes.
# Single source of truth — folded in from jobsub_argv._TIER_TO_OWNER_CLASS.
_TIER_TO_OWNER_CLASS = {
    "sim": "sim", "dig": "sim", "dts": "sim", "mcs": "sim", "mix": "sim",
    "log": "etc", "etc": "etc", "cnf": "etc", "bck": "etc",
    "rec": "dat", "ntd": "dat",
    "nts": "nts",
}

_CAMPAIGN_RE = re.compile(r"^(MDC\d{4}[a-z]*|Run\d+[A-Z]?[a-z]*)")


class Mu2eName:
    """Parse and build Mu2e dot-names (file / dataset / tarball).

    The Mu2e dot-name grammar covers three forms:
      FILE     = tier.owner.description.dsconf.sequencer.extension     (6 fields)
      DATASET  = tier.owner.description.dsconf.extension               (5 fields)
      TARBALL  = cnf.owner.description.dsconf.<index>.tar              (6 fields)

    Sequencer (when present) is one opaque chunk like `001430_00000052`.
    Tarballs are syntactically 6-field but their slot-4 is an integer index,
    not a sequencer.

    Construction:
      Mu2eName.parse(s)        - accept any of the three forms
      Mu2eName.build(...)      - assemble from named fields

    Fields are exposed as read-only attributes; derivations
    (`.dataset`, `.with_sequencer`, `.as_tier`, ...) return new
    Mu2eName instances rather than mutating in place.

    The legacy `Mu2eFilename` symbol is an alias of this class, preserved
    to keep the Perl-parity contract on `relpathname()` traceable.
    """

    __slots__ = ("filename", "tier", "owner", "description", "dsconf",
                 "sequencer", "extension")

    def __init__(self, filename: str):
        self.filename = filename
        self._parse()

    @classmethod
    def parse(cls, s: str) -> "Mu2eName":
        """Parse a Mu2e dot-name string (file / dataset / tarball). Fail-loud."""
        return cls(s)

    @classmethod
    def build(cls, *, tier: str, owner: str, description: str, dsconf: str,
              extension: str, sequencer: Optional[str] = None) -> "Mu2eName":
        """Assemble a Mu2e name from named fields. `sequencer=None` → 5-field dataset."""
        for fld, val in (("tier", tier), ("owner", owner), ("description", description),
                         ("dsconf", dsconf), ("extension", extension)):
            if not val or "." in str(val):
                raise ValueError(f"Mu2eName.build: invalid {fld}={val!r}")
        if sequencer is not None:
            if "." in str(sequencer):
                raise ValueError(f"Mu2eName.build: sequencer must not contain '.': {sequencer!r}")
            s = f"{tier}.{owner}.{description}.{dsconf}.{sequencer}.{extension}"
        else:
            s = f"{tier}.{owner}.{description}.{dsconf}.{extension}"
        return cls(s)

    def _parse(self):
        parts = self.filename.split('.')
        n = len(parts)
        if n == 6:
            self.tier, self.owner, self.description, self.dsconf, self.sequencer, self.extension = parts
        elif n == 5:
            self.tier, self.owner, self.description, self.dsconf, self.extension = parts
            self.sequencer = None
        else:
            raise ValueError(
                f"Invalid Mu2e name: expected 5 (dataset) or 6 (file/tarball) "
                f"dot-separated fields, got {n} in '{self.filename}'"
            )

    def __str__(self) -> str:
        return self.filename

    def __repr__(self) -> str:
        return f"Mu2eName({self.filename!r})"

    def __eq__(self, other) -> bool:
        return isinstance(other, Mu2eName) and self.filename == other.filename

    def __hash__(self) -> int:
        return hash(self.filename)

    # discriminators ---------------------------------------------------------

    @property
    def is_dataset(self) -> bool:
        return self.sequencer is None

    @property
    def is_file(self) -> bool:
        return self.sequencer is not None and not self.is_tarball

    @property
    def is_tarball(self) -> bool:
        return (self.tier == "cnf" and self.extension == "tar"
                and self.sequencer is not None)

    # sub-field conventions --------------------------------------------------

    @property
    def index(self) -> int:
        """Tarball job index (int). Raises if this is not a tarball."""
        if not self.is_tarball:
            raise ValueError(f"Mu2eName.index: not a cnf tarball: {self.filename}")
        return int(self.sequencer)

    @property
    def campaign(self) -> Optional[str]:
        """Campaign prefix of dsconf, e.g. 'MDC2025af' from 'MDC2025af_best_v1_3'."""
        m = _CAMPAIGN_RE.match(self.dsconf)
        return m.group(1) if m else None

    @property
    def dsconf_base(self) -> str:
        """dsconf with the build-version suffix stripped: 'MDC2025af_best_v1_3' → 'MDC2025af'."""
        return self.dsconf.split('_', 1)[0]

    @property
    def dsconf_version(self) -> Optional[str]:
        """Build-version suffix after the first '_', or None."""
        if '_' not in self.dsconf:
            return None
        return self.dsconf.split('_', 1)[1]

    # tier semantics ---------------------------------------------------------

    @property
    def tier_class(self) -> str:
        """Owner-class umbrella for dCache layout. Unknown tier passes through."""
        return _TIER_TO_OWNER_CLASS.get(self.tier, self.tier)

    # derivations ------------------------------------------------------------

    @property
    def dataset(self) -> "Mu2eName":
        """Drop the sequencer (file/tarball → dataset). Idempotent on a dataset."""
        if self.is_dataset:
            return self
        return Mu2eName.build(tier=self.tier, owner=self.owner,
                              description=self.description, dsconf=self.dsconf,
                              extension=self.extension)

    def with_sequencer(self, sequencer: str) -> "Mu2eName":
        return Mu2eName.build(tier=self.tier, owner=self.owner,
                              description=self.description, dsconf=self.dsconf,
                              sequencer=sequencer, extension=self.extension)

    def with_extension(self, extension: str) -> "Mu2eName":
        return Mu2eName.build(tier=self.tier, owner=self.owner,
                              description=self.description, dsconf=self.dsconf,
                              sequencer=self.sequencer, extension=extension)

    def as_tier(self, tier: str) -> "Mu2eName":
        return Mu2eName.build(tier=tier, owner=self.owner,
                              description=self.description, dsconf=self.dsconf,
                              sequencer=self.sequencer, extension=self.extension)

    def log_dataset(self) -> "Mu2eName":
        """For a cnf tarball, derive the matching log dataset name."""
        if not self.is_tarball:
            raise ValueError(f"Mu2eName.log_dataset: not a cnf tarball: {self.filename}")
        return Mu2eName.build(tier="log", owner=self.owner,
                              description=self.description, dsconf=self.dsconf,
                              extension="log")

    # path / parity ----------------------------------------------------------

    def basename(self) -> str:
        return self.filename

    def relpathname(self) -> str:
        """SHA256 hash-prefixed relative path, matching Perl Mu2eFilename->relpathname()."""
        h = hashlib.sha256(self.filename.encode()).hexdigest()
        return f"{h[:2]}/{h[2:4]}/{self.filename}"


# Legacy alias — preserves the Perl-parity association on the original symbol.
Mu2eFilename = Mu2eName


def log_storage_location(outputs) -> str:
    """First output's location from a POMS-map outputs list, or 'disk' if absent.

    Accepts the bare outputs list (`[{'location': ..., 'dataset': ...}, ...]`)
    or a POMS-map entry dict containing one. Logs share this location so the
    worker token's storage.modify scope covers both data and log writes.
    Used by submit.py and runmu2e.py.
    """
    if isinstance(outputs, dict):
        outputs = outputs.get('outputs')
    if not outputs:
        return 'disk'
    return outputs[0].get('location', 'disk')

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

