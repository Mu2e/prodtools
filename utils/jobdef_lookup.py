#!/usr/bin/env python3
"""Resolve an output dataset/description back to the cnf (job definition) that
produced it, and read ground-truth facts (e.g. njobs) from that cnf.

Extracted from ``fcldump`` so it can be reused by other tools (e.g.
``latestDatasets --complete-only``) without importing the fcldump entry point.

All diagnostics go to **stderr** so callers that emit machine-readable output on
stdout (config JSON, dataset lists) stay clean.

The cnf is NOT in SAM data lineage, so the mapping is by name: a fast 1:1 cnf
desc match, then a fallback that scans the dsconf's cnfs and matches on each
cnf's *declared* outputs (handles the suffixed-output case, e.g. cnf desc
``CeEndpoint`` produces ``dig.mu2e.CeEndpointOnSpillTriggerable.*``). See
``reference_cnf_to_output_desc_mismatch``.
"""

import os
import sys

from utils.job_common import Mu2eName
from utils.samweb_wrapper import list_definitions
from utils.datasetFileList import get_dataset_files, get_definition_files


_VERBOSE = False


def set_verbose(value=True):
    """Enable/disable this module's stderr diagnostics. Muted by default so
    callers that emit machine-readable output stay quiet unless they opt in."""
    global _VERBOSE
    _VERBOSE = bool(value)


def _log(*args):
    """Diagnostics to stderr, only when verbose (see set_verbose)."""
    if _VERBOSE:
        print(*args, file=sys.stderr)


def list_jobdefs(dsconf):
    """List all cnf job definitions for a given dsconf (server-side % wildcard)."""
    pattern = f"cnf.mu2e.%.{dsconf}.tar"
    _log(f"Searching for job definitions with pattern: cnf.mu2e.*.{dsconf}.tar")

    try:
        definitions = list_definitions(defname=pattern)

        if not definitions:
            _log(f"No job definitions found for dsconf: {dsconf}")
            return []

        _log(f"Found {len(definitions)} job definitions:")
        for definition in definitions:
            if definition.strip():
                _log(f"  {definition}")

        return definitions

    except Exception as e:
        _log(f"Error accessing SAM: {e}")
        return []


def find_matching_jobdef(jobdefs, desc, input_type=None):
    """Find the cnf tarball that produces output description ``desc``.

    Two-pass search:
      1. Fast: pre-filter cnfs whose own desc matches ``desc`` (1:1 case).
      2. Fallback: scan all cnfs at the dsconf, matching on declared output
         filenames (catches the suffixed-output case).

    ``input_type`` is the tier of the dataset being resolved (e.g. 'dig'); only
    cnf outputs of that tier are matched.
    """
    if not input_type:
        raise ValueError("input_type must be specified")

    result = _search_jobdefs(jobdefs, desc, input_type, name_filter=True)
    if result:
        return result

    _log(f"No 1:1 cnf desc match for '{desc}'; scanning {len(jobdefs)} cnfs at dsconf for output-name match...")
    return _search_jobdefs(jobdefs, desc, input_type, name_filter=False, verbose_match=True)


def _search_jobdefs(jobdefs, desc, input_type, name_filter, verbose_match=False):
    """Iterate jobdefs and return the first tarball whose output desc + type matches."""
    from utils.jobiodetail import Mu2eJobIO

    matches = []
    for jobdef in jobdefs:
        if name_filter:
            try:
                jobdef_desc = Mu2eName.parse(jobdef).description
            except ValueError:
                continue
            if jobdef_desc != desc:
                continue

        try:
            tarball_path = locate_tarball(jobdef)
        except RuntimeError as e:
            _log(f"Skipping {jobdef}: {e}")
            continue

        outputs = Mu2eJobIO(tarball_path).job_outputs(0)
        for output_file in outputs.values():
            try:
                out_name = Mu2eName.parse(output_file)
            except ValueError:
                continue
            if out_name.is_file and out_name.description == desc:
                matches.append((jobdef, tarball_path, output_file, out_name.tier))

    for jobdef, tarball_path, output_file, output_type in matches:
        if output_type == input_type:
            if verbose_match:
                _log(f"  Matched cnf '{jobdef}' via output: {output_file}")
            else:
                _log(f"Found match in output files (type priority): {jobdef}")
                _log(f"Output file: {output_file}")
            return tarball_path

    return None


def locate_tarball(jobdef):
    """Resolve a cnf defname to a readable tarball path (dataset, then definition)."""
    _log(f"Using datasetFileList to locate: {jobdef}")

    try:
        try:
            file_paths = get_dataset_files(jobdef)
        except RuntimeError as e:
            if "No files with dh.dataset" in str(e):
                file_paths = get_definition_files(jobdef)
            else:
                raise

        if not file_paths:
            raise RuntimeError(f"Tarball not found for: {jobdef}")

        tarball_path = file_paths[0]
        if not os.path.exists(tarball_path):
            raise RuntimeError(f"Tarball not found for: {jobdef}")

        _log(f"Found tarball at: {tarball_path}")
        return tarball_path

    except Exception as e:
        raise RuntimeError(f"Error locating tarball for {jobdef}: {e}")


def cnf_for_output(dataset_name):
    """Return the cnf tarball path that produced ``dataset_name`` (an output
    dataset or file name). Raises RuntimeError if none is found — fail loud
    rather than guess (no silent fallback)."""
    n = Mu2eName.parse(dataset_name)
    jobdefs = list_jobdefs(n.dsconf)
    if not jobdefs:
        raise RuntimeError(f"No cnf at dsconf '{n.dsconf}' for {dataset_name}")
    tarball = find_matching_jobdef(jobdefs, n.description, input_type=n.tier)
    if not tarball:
        raise RuntimeError(f"No producing cnf found for {dataset_name}")
    return tarball


def output_njobs_map(dsconf):
    """Map {(output_description, output_tier): njobs} for every cnf at <dsconf>,
    opening each cnf exactly once.

    Use this for batch completeness checks: per-dataset resolution re-runs
    list_jobdefs and re-opens tarballs for every dataset (O(datasets x cnfs)),
    which crawls; this is O(cnfs) and the caller just does dict lookups.
    njobs is 0 for open-ended generator cnfs (indeterminate)."""
    from utils.jobiodetail import Mu2eJobIO
    from utils.jobquery import Mu2eJobPars
    out = {}
    for jobdef in list_jobdefs(dsconf):
        try:
            tarball = locate_tarball(jobdef)
            njobs = Mu2eJobPars(tarball).njobs()
            outputs = Mu2eJobIO(tarball).job_outputs(0)
        except Exception as e:
            _log(f"Skipping {jobdef}: {e}")
            continue
        for output_file in outputs.values():
            try:
                n = Mu2eName.parse(output_file)
            except ValueError:
                continue
            if n.is_file:
                out[(n.description, n.tier)] = njobs
    return out


def cnf_njobs_for_output(dataset_name):
    """Ground-truth expected file count for an output dataset: the njobs of the
    cnf that produced it. Reused as the completeness target (a finished
    input-driven stage emits exactly njobs files per output stream).

    Raises RuntimeError when the cnf has no inherent job count — i.e. an
    open-ended EmptyEvent *generator* cnf (njobs determined at submit time, not
    stored in the cnf). Callers must not treat that as "incomplete"; the cnf
    simply can't answer (fail loud rather than compare against a bogus 0)."""
    from utils.jobquery import Mu2eJobPars
    cnf = cnf_for_output(dataset_name)
    n = Mu2eJobPars(cnf).njobs()
    if not n or n <= 0:
        raise RuntimeError(
            f"{cnf} reports njobs={n}: open-ended generator cnf has no inherent "
            f"job count; completeness is indeterminate from the cnf")
    return n
