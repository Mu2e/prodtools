#!/usr/bin/env python3
"""
Shared base classes and utilities for Mu2e production tools.

This module consolidates common functionality that was previously duplicated
across multiple files to reduce code redundancy and ensure consistency.
"""

import json
import os
import re
import tarfile
import hashlib
from typing import Dict, Optional, Union


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

    `relpathname()` reproduces the Perl Mu2eFilename hash-prefixed path
    (SHA256 of the basename) for parity with the legacy tooling.
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

    def relpathname(self) -> str:
        """SHA256 hash-prefixed relative path, matching Perl Mu2eFilename->relpathname()."""
        h = hashlib.sha256(self.filename.encode()).hexdigest()
        return f"{h[:2]}/{h[2:4]}/{self.filename}"


def log_storage_location(outputs) -> str:
    """Where a job's log dataset goes, given its POMS-map outputs list.

    Mu2e convention: logs live on persistent disk
    (`/pnfs/mu2e/persistent/datasets/phy-etc/log/...`) regardless of where
    the data lands, so they stay cheap to read without a tape recall. This
    matches the POMS path, which calls push_logs() with its 'disk' default.

    The one exception is `scratch`: a non-mu2epro account whose data goes to
    scratch has no storage.modify scope on /mu2e/persistent/datasets, so a
    'disk' log push would 403. Those runs keep logs beside their data.

    Do NOT let logs inherit 'tape' from the data outputs — small log files
    on tape are wasteful and diverge from every POMS-submitted sibling
    dataset. (Regression fixed 2026-07-21 after the first direct campaign
    put 500 logs on tape.)

    Accepts the bare outputs list (`[{'location': ..., 'dataset': ...}, ...]`)
    or a POMS-map entry dict containing one. Used by submit.py (to scope the
    worker token) and runmu2e.py (to place the push).
    """
    if isinstance(outputs, dict):
        outputs = outputs.get('outputs')
    if not outputs:
        return 'disk'
    return 'scratch' if outputs[0].get('location') == 'scratch' else 'disk'

def default_owner() -> str:
    """Dataset owner defaulted from $USER; mu2epro maps to mu2e (production
    artifacts are owned by 'mu2e', not the submitting account)."""
    return os.getenv('USER', 'mu2e').replace('mu2epro', 'mu2e')


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


def tbs_capacity(tbs, context=''):
    """Max job count supported by a tbs dict's frozen input lists — the
    single home of the ceil-div arithmetic, shared by the tarball reader
    (Mu2eJobBase.njobs) and the writer (jobdef._resolve_njobs).

    Returns None when tbs carries neither inputs nor samplinginput
    (generator / generic jobdefs — capacity is not derivable from tbs).
    """
    where = f" in {context}" if context else ""

    inputs = tbs.get('inputs')
    if inputs:
        for dataset, (merge, filelist) in inputs.items():
            if not isinstance(merge, int) or merge <= 0:
                raise ValueError(
                    f"tbs_capacity: invalid merge factor {merge!r} for {dataset}{where}")
            return (len(filelist) + merge - 1) // merge

    samplinginput = tbs.get('samplinginput')
    if samplinginput:
        for dataset, (nreq, filelist) in samplinginput.items():
            if nreq == 0:
                # nreq 0 = "all files in one job" (job_sampling_inputs semantics)
                return 1
            if not isinstance(nreq, int) or nreq < 0:
                raise ValueError(
                    f"tbs_capacity: invalid nreq {nreq!r} for {dataset}{where}")
            return (len(filelist) + nreq - 1) // nreq

    return None


class Mu2eJobBase:
    """Base class for Mu2e job handling classes.

    Provides common functionality for extracting data from job definition
    tarballs, generating deterministic random numbers, and computing the
    per-job input file lists (primary / aux / sampling).
    """

    def __init__(self, jobdef_path: str):
        """Initialize with path to job definition tarball; extract jobpars.json."""
        self.jobdef = jobdef_path
        self._member_cache = {}
        self.json_data = self._extract_json()
        # owner/dsconf feed the `.owner.`/`.version.` placeholder substitution
        # in job_outputs(). jobpars.json built by mu2ejobdef has no top-level
        # owner/dsconf keys, so these normally resolve to the environment
        # defaults — same behavior Mu2eJobFCL always had.
        self.owner = self.json_data.get('owner', default_owner())
        self.dsconf = self.json_data.get('dsconf', 'unknown')

    def setup(self):
        """The SimJob setup-script path recorded in jobpars.json."""
        return self.json_data.get('setup', '')

    def _extract_member(self, suffix: str) -> bytes:
        """Return the bytes of the first tar member whose name ends with ``suffix``.

        Consolidated tarball member-scan used by _extract_json (jobpars.json) and
        Mu2eJobFCL._extract_fcl (mu2e.fcl). Raises ValueError if none matches.
        Cached per instance — each call otherwise re-opens and fully
        decompresses the tarball (generate_fcl reads mu2e.fcl twice per job).
        """
        if suffix not in self._member_cache:
            with tarfile.open(self.jobdef, 'r') as tar:
                for member in tar.getmembers():
                    if member.name.endswith(suffix):
                        self._member_cache[suffix] = tar.extractfile(member).read()
                        break
                else:
                    raise ValueError(f"{suffix} not found in {self.jobdef}")
        return self._member_cache[suffix]

    def _extract_json(self) -> dict:
        """Extract jobpars.json from the tarball.

        Consolidated implementation from the former per-class copies.
        """
        return json.loads(self._extract_member('jobpars.json'))
    
    def _my_random(self, *args) -> int:
        """Generate deterministic random number from inputs.

        Consolidated implementation from the former per-class copies.
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

    # ------------------------------------------------------------------
    # Per-index job arithmetic. These are THE single implementation —
    # the worker names its actual output files through them (via
    # Mu2eJobFCL.generate_fcl), so every other consumer (mkrecovery,
    # submit, db_builder, jobdef_lookup) must get identical answers.
    # Formerly duplicated (divergently) in the deleted jobiodetail.py and in jobquery.py.
    # ------------------------------------------------------------------

    def sequencer(self, index: int) -> str:
        """Get sequencer for job index.

        Precedence: an explicit run number in tbs.event_id wins (the job
        family is run/index-addressed, e.g. mix and generator jobs);
        otherwise the sequencer comes from the primary input files.
        Different source types use different FCL parameter names for the
        run number:
          EmptyEvent / RootInput → source.firstRun
          SamplingInput          → source.run
          PBISequence            → source.runNumber
        """
        tbs = self.json_data.get('tbs', {})

        event_id = tbs.get('event_id', {})
        run = (event_id.get('source.firstRun')
               or event_id.get('source.run')
               or event_id.get('source.runNumber'))
        if run:
            return f"{run:06d}_{index:08d}"

        # Get sequencers from primary input files
        primary_inputs = self.job_primary_inputs(index)
        if not primary_inputs:
            raise ValueError("Error: get_sequencer(): unsupported JSON content")

        sequencers = []
        for dataset, files in primary_inputs.items():
            for filename in files:
                sequencers.append(Mu2eName.parse(filename).sequencer)

        if not sequencers:
            raise ValueError("Error: get_sequencer(): no sequencers found in input files")

        # Sort and get first sequencer
        sequencers.sort()
        parent_sequencer = sequencers[0]

        # If sequencer_from_index is enabled, extract run number and use index as subrun
        if tbs.get('sequencer_from_index', False) and '_' in parent_sequencer:
            parent_run = parent_sequencer.split('_')[0]
            return f"{parent_run}_{index:08d}"

        # Otherwise, use the sequencer from input files directly
        return parent_sequencer

    def job_outputs(self, index: int,
                    override_desc: str = None,
                    override_seq: str = None) -> Dict[str, str]:
        """Get output files for job index.

        override_desc: if provided, substitute {desc} in outfile patterns.
                       Used in direct-input mode where desc comes from fname.
        override_seq:  if provided, use this sequencer instead of computing
                       from input files. Used in direct-input mode.
        """
        tbs = self.json_data.get('tbs', {})
        outfiles = tbs.get('outfiles')

        if not outfiles:
            return {}

        result = {}
        seq = override_seq if override_seq is not None else self.sequencer(index)

        for key, template in outfiles.items():
            # The template may still contain placeholders that need to be resolved
            # Replace placeholders with actual values
            resolved_template = template
            resolved_template = resolved_template.replace('.owner.', f'.{self.owner}.')
            resolved_template = resolved_template.replace('.version.', f'.{self.dsconf}.')
            resolved_template = resolved_template.replace('.sequencer.', f'.{seq}.')
            # Also handle {sequencer} format (Python-style placeholder)
            resolved_template = resolved_template.replace('{sequencer}', seq)
            # Substitute {desc} from fname at runtime (direct-input / generic tarball mode)
            if override_desc is not None:
                resolved_template = resolved_template.replace('{desc}', override_desc)

            # Skip filenames that don't follow Mu2e naming convention (e.g., /dev/null, relative paths)
            if not resolved_template.startswith(('dts.', 'dig.', 'sim.', 'rec.', 'nts.', 'cnf.', 'mcs.')):
                result[key] = resolved_template
                continue

            # Update sequencer in the filename (parse then re-emit with new seq)
            result[key] = str(Mu2eName.parse(resolved_template).with_sequencer(seq))

        return result

    def job_event_settings(self, index: int) -> Dict[str, Union[int, str]]:
        """Get event settings for job index."""
        tbs = self.json_data.get('tbs', {})
        event_id = tbs.get('event_id')
        per_index = tbs.get('event_id_per_index', {})

        if not event_id and not per_index:
            return {}

        result = {}
        if event_id:
            for key, value in event_id.items():
                result[key] = value

        subrunkey = tbs.get('subrunkey')
        if subrunkey is not None:
            if subrunkey != '':
                result[subrunkey] = index
        else:
            # Old format
            result['source.firstSubRun'] = index

        # Per-index linear overrides: result[key] = offset + index * step.
        # Applied last so they override any fixed event_id entry on the same key.
        for key, spec in per_index.items():
            offset = int(spec.get('offset', 0))
            step = int(spec.get('step', 0))
            result[key] = offset + index * step

        return result

    def job_seed(self, index: int) -> Dict[str, int]:
        """Get seed settings for job index."""
        tbs = self.json_data.get('tbs', {})
        seed_key = tbs.get('seed')

        if not seed_key:
            return {}

        return {seed_key: 1 + index}

    def njobs(self) -> int:
        """Get the number of jobs in the set.

        Precedence: tbs.njobs (embedded at build time: the declared or
        resolved campaign size) → capacity derived from the frozen input
        lists (tbs_capacity) → 0.

        0 means "open-ended": a legacy generator tarball built before
        tbs.njobs existed, or a generic tarball (1 job per input fname).
        For those the job count is a submit-time decision, authoritative
        in the POMS map — 0 is deliberately not a guess.
        """
        tbs = self.json_data.get('tbs', {})

        if 'njobs' in tbs:
            return int(tbs['njobs'])

        capacity = tbs_capacity(tbs, context=self.jobdef)
        return 0 if capacity is None else capacity


