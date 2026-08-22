#!/usr/bin/env python3
"""
Shared base classes and utilities for Mu2e production tools.

Consolidates functionality that used to be duplicated across files.
"""

import json
import os
import re
import tarfile
import hashlib
from typing import Dict, Optional, Union


# The relative setup path a code-mode cnf carries, and the layout `muse
# tarball` produces. Same string upstream uses: mu2ejobdef:45
# (filename_tarsetup) and mu2eprodsys:337 (MU2EGRID_USERSETUP).
CODE_SETUP_REL = 'Code/setup.sh'

# Filename prefixes marking a real Mu2e output file, vs. a sink like
# /dev/null or a relative path. Single home: job_outputs uses it to
# decide whether to re-sequence a name, runlocal to decide whether to
# glob for it — two copies would drift the first time a tier is added.
OUTPUT_TIERS = ('dts.', 'dig.', 'sim.', 'rec.', 'nts.', 'cnf.', 'mcs.')


def sha256_file(path, chunk_size=1 << 20):
    """(hex digest, size in bytes) of a file, read in chunks.

    Single home for content hashing: `jobdef` stamps a code tarball's
    digest into the cnf at build time and `check_inputs` re-derives it
    at submit time. Two implementations would eventually disagree on
    chunking or encoding and turn a match into a spurious refusal.
    """
    digest = hashlib.sha256()
    size = 0
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b''):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


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

    Grammar covers three forms:
      FILE     = tier.owner.description.dsconf.sequencer.extension     (6 fields)
      DATASET  = tier.owner.description.dsconf.extension               (5 fields)
      TARBALL  = cnf.owner.description.dsconf.<index>.tar              (6 fields)

    Sequencer (when present) is one opaque chunk like `001430_00000052`.
    Tarballs are syntactically 6-field but slot-4 is an integer index,
    not a sequencer. `Mu2eName.parse(s)` accepts any of the three forms;
    `Mu2eName.build(...)` assembles from named fields. Derivations
    (`.dataset`, `.with_sequencer`, `.as_tier`, ...) return new
    instances rather than mutating in place.

    `relpathname()` reproduces the Perl Mu2eFilename hash-prefixed path
    for parity with the legacy tooling.
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
    """Where a job's log dataset goes, given its map-entry outputs list.

    Mu2e convention: logs live on persistent disk regardless of where the
    data lands, so they stay cheap to read without a tape recall (matches
    push_logs()'s 'disk' default). Two exceptions where 'disk' is wrong:
    `scratch` — a non-mu2epro account with data on scratch lacks
    storage.modify on /mu2e/persistent/datasets, so a 'disk' log push
    would 403 (those runs keep logs beside their data); and `outstage` —
    the data was never declared to SAM, so a declared log would list
    parents SAM never heard of (the log follows the data into
    $MU2EGRID_WFOUTSTAGE, also undeclared).

    Do NOT let logs inherit 'tape' — small logs on tape are wasteful and
    diverge from every sibling dataset (regression fixed 2026-07-21 after
    the first direct campaign put 500 logs on tape).

    Accepts the bare outputs list or a map-entry dict containing one.
    """
    if isinstance(outputs, dict):
        outputs = outputs.get('outputs')
    if not outputs:
        return 'disk'
    location = outputs[0].get('location')
    return location if location in ('scratch', 'outstage') else 'disk'

def default_owner() -> str:
    """Dataset owner defaulted from $USER; mu2epro maps to mu2e (production
    artifacts are owned by 'mu2e', not the submitting account)."""
    return os.getenv('USER', 'mu2e').replace('mu2epro', 'mu2e')


def remove_storage_prefix(path: str) -> str:
    """Strip a leading storage-system prefix (enstore:, dcache:) if present."""
    if path.startswith('enstore:'):
        return path[8:]
    elif path.startswith('dcache:'):
        return path[7:]
    return path


def tbs_capacity(tbs, context=''):
    """Max job count supported by a tbs dict's frozen input lists — single
    home of the ceil-div arithmetic, shared by the tarball reader
    (Mu2eJobBase.njobs) and the writer (jobdef._resolve_njobs).

    Returns None when tbs carries neither inputs nor samplinginput
    (generator / generic jobdefs — capacity isn't derivable from tbs).
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
    """Base class for Mu2e job handling classes: extracting data from job
    definition tarballs, generating deterministic random numbers, and
    computing per-job input file lists (primary / aux / sampling).
    """

    def __init__(self, jobdef_path: str):
        self.jobdef = jobdef_path
        self._member_cache = {}
        self.json_data = self._extract_json()
        # Feed the `.owner.`/`.version.` placeholder substitution in
        # job_outputs(). mu2ejobdef's jobpars.json has no top-level
        # owner/dsconf keys, so these normally resolve to env defaults.
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
        """Extract jobpars.json from the tarball."""
        return json.loads(self._extract_member('jobpars.json'))

    def _my_random(self, *args) -> int:
        """Deterministic pseudo-random int from inputs, via SHA256."""
        h = hashlib.sha256()
        for arg in args:
            h.update(str(arg).encode())
        # Take first 8 hex digits (32 bits)
        return int(h.hexdigest()[:8], 16)

    def job_primary_inputs(self, index):
        """Primary input files for job index.

        `tbs.inputs` maps each dataset to a (merge, filelist) tuple, sliced
        by `[index*merge : index*merge+merge]` (clamped at end). Raises
        ValueError if `index` is past the end; {} if none configured.
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
        """Auxiliary input files for job index.

        `tbs.auxin` maps each dataset to (nreq, infiles). When
        `tbs.sequential_aux` is True, slice deterministically with
        rollover; otherwise sample `nreq` files without repetition via
        `_my_random`. {} if none configured.
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
                result[dataset] = self._sequential_slice(infiles, nreq, index)
            else:
                result[dataset] = self._sampled(infiles, nreq, index)

        return result

    @staticmethod
    def _sequential_slice(infiles, nreq, index):
        """`nreq` files starting at index*nreq, rolling over past the end."""
        nf = len(infiles)
        first = index * nreq
        last = min(first + nreq - 1, nf - 1)
        if first >= nf:
            first = first % nf
            last = min(first + nreq - 1, nf - 1)
        if first > last:
            raise ValueError(f"job_aux_inputs(): invalid index {index} for sequential selection")
        return infiles[first:last + 1]

    def _sampled(self, infiles, nreq, index):
        """`nreq` files sampled without repetition. The
        `_my_random(index, *available_files)` call order is a seed
        contract with mu2ejobfcl — do not change the argument order or
        the number of calls."""
        sample = []
        available_files = infiles.copy()
        for _ in range(nreq):
            if not available_files:
                break
            rnd = self._my_random(index, *available_files)
            file_index = rnd % len(available_files)
            sample.append(available_files[file_index])
            available_files.pop(file_index)
        return sample

    def job_sampling_inputs(self, index):
        """Sampling input files for job index.

        `tbs.samplinginput` maps each dataset to (nreq, filelist), sliced
        sequentially by index. {} if none configured.
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
        """All input files for job index — merged primary + aux + sampling."""
        result = {}
        result.update(self.job_primary_inputs(index))
        result.update(self.job_aux_inputs(index))
        result.update(self.job_sampling_inputs(index))
        return result

    # ------------------------------------------------------------------
    # Per-index job arithmetic. THE single implementation — the worker
    # names its actual output files through them (Mu2eJobFCL.generate_fcl),
    # so every other consumer (submit, submissions, jobdef_lookup) must
    # get identical answers. Formerly duplicated (divergently) in the
    # deleted jobiodetail.py and in jobquery.py.
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

        primary_inputs = self.job_primary_inputs(index)
        if not primary_inputs:
            raise ValueError("Error: get_sequencer(): unsupported JSON content")

        sequencers = []
        for dataset, files in primary_inputs.items():
            for filename in files:
                sequencers.append(Mu2eName.parse(filename).sequencer)

        if not sequencers:
            raise ValueError("Error: get_sequencer(): no sequencers found in input files")

        sequencers.sort()
        parent_sequencer = sequencers[0]

        # sequencer_from_index: keep the parent's run, use index as subrun
        if tbs.get('sequencer_from_index', False) and '_' in parent_sequencer:
            parent_run = parent_sequencer.split('_')[0]
            return f"{parent_run}_{index:08d}"

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
            resolved_template = template
            resolved_template = resolved_template.replace('.owner.', f'.{self.owner}.')
            resolved_template = resolved_template.replace('.version.', f'.{self.dsconf}.')
            resolved_template = resolved_template.replace('.sequencer.', f'.{seq}.')
            # {sequencer}: Python-style placeholder, same substitution
            resolved_template = resolved_template.replace('{sequencer}', seq)
            # {desc} comes from fname at runtime (direct-input / generic tarball mode)
            if override_desc is not None:
                resolved_template = resolved_template.replace('{desc}', override_desc)

            # Not a Mu2e-named file (e.g. /dev/null, a relative path) — leave as-is
            if not resolved_template.startswith(OUTPUT_TIERS):
                result[key] = resolved_template
                continue

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
        """Number of jobs in the set.

        Precedence: tbs.njobs (declared/resolved campaign size, embedded
        at build time) -> capacity from the frozen input lists
        (tbs_capacity) -> 0. 0 means "open-ended": a legacy generator
        tarball built before tbs.njobs existed, or a generic tarball (1
        job per input fname) — job count is a submit-time decision,
        authoritative in the submission map, so 0 is deliberately not a
        guess.
        """
        tbs = self.json_data.get('tbs', {})

        if 'njobs' in tbs:
            return int(tbs['njobs'])

        capacity = tbs_capacity(tbs, context=self.jobdef)
        return 0 if capacity is None else capacity


def expected_outputs_for(input_fname, job_pars):
    """Expected output filenames for one direct-input (draining) job.

    THE single home for the input->output name mapping: delegates to
    job_outputs(0, override_desc=, override_seq=) — the exact
    substitution process_direct_input performs on the worker — so
    dispatcher, verifier and worker can't drift. Non-Mu2e-named streams
    (e.g. /dev/null) are dropped, mirroring submit._read_cnf_facts.
    Raises ValueError on a malformed input name, RuntimeError when the
    cnf yields no Mu2e-named outputs (fail loud, never guess).
    """
    n = Mu2eName.parse(os.path.basename(input_fname))
    if not n.is_file:
        raise ValueError(f"not a Mu2e file name: {input_fname}")
    out = job_pars.job_outputs(0, override_desc=n.description,
                               override_seq=n.sequencer) or {}
    names = sorted(v for v in out.values() if v and '/' not in v)
    if not names:
        raise RuntimeError(f"no Mu2e-named outputs in cnf for {input_fname}")
    return names
