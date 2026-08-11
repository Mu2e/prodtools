#!/usr/bin/env python3
"""
Pure-Python builder for the jobsub_submit argv that drives our direct-mode
worker (utils/runmu2e.py via bin/runjob.sh). Replicates the submit-side
behavior of mu2egrid's mu2ejobsub but routes the worker to our Python
pipeline so that per-job pushOutput happens.

Phase 2 Step 2 of the plan in
wiki/pages/2026-04-30-phase2-direct-jobsub-implementation.md.

Pure functions only — no I/O, no subprocess. Use submit.py's direct
backend to actually invoke jobsub_submit with the resulting argv.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.job_common import Mu2eName
from utils.map_entry import tarball_of, inloc_of
from utils.file_resolver import storage_scope


# --- Mu2egrid-compatible defaults (mirrors mu2egrid::commonOptDefaultsJobsub) ---
DEFAULT_DISK = "30GB"
# Deliberately ABOVE mu2egrid's 2000MB. Mu2e primaries cluster just over
# that line -- measured VmHWM 2266 MB (PiTargetStops) and 2377 MB (the
# RPC primaries), both base-10 -- so the mu2egrid value OOM'd entries
# whose only sin was not naming a memory key. 2500MB clears the observed
# peaks while staying low enough not to distort slot matching. Raising an
# entry above this is still an entry-key decision; note that naming the
# key forfeits the 4000MB recovery floor (see submissions.RECOVERY_MEMORY
# and recovery_resource_argv, which apply only when the key is absent).
DEFAULT_MEMORY = "2500MB"
DEFAULT_LIFETIME = "24h"
DEFAULT_RESOURCE = "usage_model=OPPORTUNISTIC,DEDICATED"
DEFAULT_GROUP = "mu2e"
DEFAULT_SINGULARITY = "/cvmfs/singularity.opensciencegrid.org/fermilab/fnal-wn-el9:latest"
DEFAULT_MU2E_SETUP = "/cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh"


def role_for_user(user):
    """mu2epro implies Production role; everyone else inherits jobsub default."""
    return "Production" if user == "mu2epro" else None


def default_wftop(role):
    """Outstage wftop default: persistent for Production, scratch otherwise.
    Mirrors the Perl `mu2ejobsub:294-298` logic."""
    if role == "Production":
        return "/pnfs/mu2e/persistent/users"
    return "/pnfs/mu2e/scratch/users"


def compute_outstage(*, wftop, submitter, wfproject):
    """`$wftop/$USER/workflow/$wfproject/outstage` — same shape as the Perl."""
    return f"{wftop}/{submitter}/workflow/{wfproject}/outstage"


def storage_modify_dir(outstage):
    """Token request directory: `/pnfs/mu2e/...` → `/mu2e/...`. Per
    `mu2ejobsub:142-146`, dCache token paths drop the `/pnfs` prefix."""
    return re.sub(r"^/pnfs/mu2e", "/mu2e", outstage)


def storage_scope_for_file(filename, location):
    """Narrowest dCache scope that covers writes of `filename` to `location`.
    Delegates to file_resolver.storage_scope (the layout's single home)."""
    return storage_scope(filename, location)


def output_storage_dirs(output_filenames, outputs):
    """Derive `--need-storage-modify` scopes for direct-mode pushOutput.

    Args:
        output_filenames: actual filenames the cnf will produce, e.g. from
            `Mu2eJobPars(jobdef).job_outputs(0).values()`.
        outputs: map-entry `outputs[]` — list of ``{dataset, location}``
            globs that map dataset patterns to a location.

    Each filename is matched against the dataset globs to find its
    location, then the narrowest scope is computed via
    `storage_scope_for_file`. Returns a sorted, deduped list.

    `mu2ejobsub.sh` only writes to `$WFOUTSTAGE`, so the Perl side needs
    one scope. Direct mode runs `pushOutput` on the worker, which writes
    to `/pnfs/mu2e/<area>/datasets/<owner-class>-<tier>/<tier>/<owner>/...` —
    a path the WFOUTSTAGE-only token does NOT cover.
    """
    import fnmatch
    dirs = set()
    for fname in output_filenames or []:
        for spec in outputs or []:
            pattern = spec.get("dataset") or "*"
            if fnmatch.fnmatch(fname, pattern):
                scope = storage_scope_for_file(fname, spec.get("location"))
                if scope:
                    dirs.add(scope)
                break
    return sorted(dirs)


# --- Filename → metadata ---

def description_from_tarball(tarball_name):
    """`cnf.<owner>.<desc>.<dsconf>.<seq>.tar` → `<desc>`.

    Lenient: unparseable names fall back to the raw basename.
    """
    bn = os.path.basename(tarball_name)
    try:
        return Mu2eName.parse(bn).description
    except ValueError:
        return bn


def campaign_from_tarball(tarball_name):
    """`cnf.<owner>.<desc>.<dsconf>.<seq>.tar` → MDC campaign token in `<dsconf>`,
    else the full `<dsconf>`. Lenient: unparseable names → 'default'."""
    bn = os.path.basename(tarball_name)
    try:
        n = Mu2eName.parse(bn)
    except ValueError:
        return "default"
    m = re.match(r"(MDC\d{4})", n.dsconf)
    return m.group(1) if m else n.dsconf


# --- Inspec / ops JSON ---

# mu2ejobsub disables the `file` protocol; valid choices on the worker are
# `ifdh` (stage-in) and `root` (stream via xrootd). Tape locations cannot be
# root-streamed, so they always map to `ifdh`.
_LOCATION_DEFAULT_PROTOCOL = {
    "tape": "ifdh",
    "disk": "ifdh",
    "scratch": "ifdh",
    "resilient": "root",
}


def default_protocol_for_inloc(inloc):
    """Pick a default protocol for a map entry's `inloc`. Returns `None` for
    `inloc == 'none'` (jobs without input data — e.g. POT generators)."""
    if not inloc or inloc == "none":
        return None
    if inloc.startswith("dir:"):
        return "ifdh"
    return _LOCATION_DEFAULT_PROTOCOL.get(inloc, "ifdh")


def build_inspec(input_datasets, inloc):
    """`{dataset: [protocol, location]}` for every input dataset.

    Submission maps carry one `inloc` per entry, so all input datasets share the
    same protocol/location in v1. mu2ejobsub allows per-dataset overrides
    (`--protocol ds:proto`) which we can fold in later if a campaign needs them.
    """
    proto = default_protocol_for_inloc(inloc) or "ifdh"
    return {ds: [proto, inloc] for ds in input_datasets}


def build_ops_json(*, entry, jobset, input_datasets, files=None):
    """Worker-side ops JSON. Top-level keys:

    - `jobs`: PROCESS → real-job-index lookup table (replaces `mu2ejobmap`)
    - `inspec`: per-input-dataset (protocol, location)
    - `jobdesc`: the submission entry, consumed by
      `runmu2e._direct_dispatch` via `process_jobdef`
    - `files` (draining batches only): job index → input art filename;
      the worker runs process_direct_input on files[index]
    """
    ops = {
        "jobs": list(jobset),
        "inspec": build_inspec(input_datasets, inloc_of(entry)),
        "jobdesc": dict(entry),
    }
    if files is not None:
        ops["files"] = list(files)
    return ops


# --- argv ---

def _env_args(env_dict):
    """Flatten {KEY: VAL} → ['-e', 'KEY=VAL', ...] in stable order, skipping
    empty values (jobsub_client rejects `-e KEY=`)."""
    out = []
    for k in sorted(env_dict):
        v = env_dict[k]
        if v == "" or v is None:
            continue
        out.extend(["-e", f"{k}={v}"])
    return out


def build_jobsub_argv(
    *,
    entry,
    jobset,
    jobdef_path,
    ops_json_path,
    prodtools_tar_path,
    worker_script_path,
    submitter,
    extra_storage_modify=(),
    role=None,
    wftop=None,
    wfproject=None,
    cluster_name=None,
    disk=None,
    memory=None,
    expected_lifetime=None,
    priority=0,
    singularity_image=None,
    mu2e_setup=None,
    extra_jobsub_args=None,
):
    """Build the full `jobsub_submit` argv (without the `jobsub_submit`
    command itself) for direct-mode submission.

    All path arguments are absolute paths on the submitter's filesystem.
    `-f dropbox://<path>` ships them to the worker under `$CONDOR_DIR_INPUT`.
    """
    if role is None:
        role = role_for_user(submitter)
    if wftop is None or wftop == "":
        wftop = default_wftop(role)
    tarball_name = tarball_of(entry)
    if wfproject is None:
        wfproject = campaign_from_tarball(tarball_name)
    if cluster_name is None:
        cluster_name = description_from_tarball(tarball_name)
    if singularity_image is None:
        singularity_image = DEFAULT_SINGULARITY
    if mu2e_setup is None:
        mu2e_setup = DEFAULT_MU2E_SETUP

    outstage = compute_outstage(wftop=wftop, submitter=submitter, wfproject=wfproject)

    env = {
        "EXPERIMENT": "mu2e",
        "MU2EGRID_OPSJSON": os.path.basename(ops_json_path),
        "MU2EGRID_JOBDEF": os.path.basename(jobdef_path),
        "MU2EGRID_PRODTOOLS_TAR": os.path.basename(prodtools_tar_path),
        "MU2EGRID_CLUSTERNAME": cluster_name,
        "MU2EGRID_WFOUTSTAGE": outstage,
        "MU2EGRID_MU2ESETUP": mu2e_setup,
    }

    argv = [
        "--resource-provides", DEFAULT_RESOURCE,
        "--disk", disk or DEFAULT_DISK,
        "--expected-lifetime", expected_lifetime or DEFAULT_LIFETIME,
        "--memory", memory or DEFAULT_MEMORY,
        "--group", DEFAULT_GROUP,
    ]
    if role:
        argv.extend(["--role", role])
    argv.extend(["-l", f"priority={priority}"])
    argv.extend(["--singularity-image", singularity_image])
    argv.extend(_env_args(env))
    # `--need-storage-modify` accumulates: WFOUTSTAGE plus whatever the
    # caller computed from cnf outputs (CB1). htvault rejects the broad
    # `/mu2e/<area>/datasets` scope, so callers should derive narrow
    # `/mu2e/<area>/datasets/<owner-class>-<tier>/<tier>/<owner>` paths
    # via `storage_scope_for_file`.
    scopes = [storage_modify_dir(outstage), *extra_storage_modify]
    for d in dict.fromkeys(scopes):  # dedupe, preserve order
        argv.extend(["--need-storage-modify", d])
    argv.extend(["-N", str(len(jobset))])
    argv.extend(["-f", f"dropbox://{ops_json_path}"])
    argv.extend(["-f", f"dropbox://{jobdef_path}"])
    argv.extend(["-f", f"dropbox://{prodtools_tar_path}"])
    if extra_jobsub_args:
        argv.extend(extra_jobsub_args)
    argv.append(f"file://{worker_script_path}")
    return argv
