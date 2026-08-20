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
from utils.samweb_wrapper import definitions_matching
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
        definitions = definitions_matching(defname=pattern)

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


def _raw_outfile_descs(tarball_path):
    """Raw output descriptions declared in a cnf's jobpars, WITHOUT resolving
    the sequencer. Returns list of (description, tier) parsed from each
    ``tbs.outfiles`` template. Safe on generic tarballs (whose {desc}/sequencer
    are deferred and would make job_outputs() raise)."""
    from utils.jobquery import Mu2eJobPars
    out = []
    try:
        outfiles = Mu2eJobPars(tarball_path).json_data.get('tbs', {}).get('outfiles', {})
    except Exception:
        return out
    for template in (outfiles or {}).values():
        try:
            n = Mu2eName.parse(template)
        except ValueError:
            continue
        out.append((n.description, n.tier))
    return out


def is_generic_cnf(tarball_path):
    """True if the cnf is a generic tarball: any output description still
    carries the literal ``{desc}`` placeholder (deferred to runtime)."""
    return any('{desc}' in d for d, _ in _raw_outfile_descs(tarball_path))


def _generic_desc_capture(template_desc, desc):
    """If a {desc}-templated output description matches a concrete one, return the
    value captured by ``{desc}`` (the input desc), else None. e.g. ``{desc}-KL``
    vs ``CeEndpoint-KL`` -> ``CeEndpoint``; ``{desc}`` vs ``X`` -> ``X``."""
    import re
    if '{desc}' not in template_desc:
        return None
    parts = template_desc.split('{desc}')
    m = re.fullmatch('(.+)'.join(re.escape(p) for p in parts), desc)
    return m.group(1) if m else None


def _generic_desc_matches(template_desc, desc):
    """True if a {desc}-templated output description matches a concrete request."""
    return _generic_desc_capture(template_desc, desc) is not None


def derive_generic_input(tarball_path, target):
    """Map a --target OUTPUT file to its INPUT art file for a generic cnf: strip
    the output-template suffix -> input desc, map tier (mcs->dig) via the chain,
    and find the input file in SAM by desc + sequencer (any dsconf)."""
    from utils.samweb_wrapper import files_like
    from utils.chain_emit import input_tier_for_output
    t = Mu2eName.parse(target)
    in_tier = input_tier_for_output(t.tier)
    input_desc = None
    for tmpl, tier in _raw_outfile_descs(tarball_path):
        if tier == t.tier:
            input_desc = _generic_desc_capture(tmpl, t.description)
            if input_desc:
                break
    if not input_desc:
        raise ValueError(f"cannot derive input for target '{target}'")
    matches = files_like(f"{in_tier}.mu2e.{input_desc}.%.art",
                         sequencer=t.sequencer)
    if not matches:
        raise ValueError(f"no {in_tier} input for desc '{input_desc}' seq '{t.sequencer}'")
    return matches[0]


# How many of a dataset's files to pull when sampling one. SAM returns them in
# arbitrary order, so a batch is sorted before indexing; big enough that
# --index has room, small enough that a 20k-file dataset costs one cheap query.
SAMPLE_BATCH = 200


def sample_dataset_target(dataset, index=0, batch=SAMPLE_BATCH):
    """Pick one file of ``dataset`` to stand in for a missing --target.

    A generic cnf defers the sequencer to runtime, so a bare --dataset cannot
    resolve one on its own; any file of the dataset supplies it. Returns None
    when there is nothing to sample (dataset empty, or index past the batch) —
    the caller reports that rather than guessing.
    """
    from utils.samweb_wrapper import list_files
    files = sorted(list_files(f"dh.dataset {dataset} with limit {batch}"))
    if index < 0 or index >= len(files):
        return None
    return files[index]


def _search_generic(jobdefs, desc, input_type):
    """Lowest-priority pass: match a generic cnf whose {desc}-templated output
    (of the right tier) can produce ``desc``. Used only when no exact per-desc
    or output-name match exists, so exact cnfs always win."""
    for jobdef in jobdefs:
        try:
            tarball_path = locate_tarball(jobdef)
        except RuntimeError as e:
            _log(f"Skipping {jobdef}: {e}")
            continue
        for tmpl_desc, tier in _raw_outfile_descs(tarball_path):
            if '{desc}' in tmpl_desc and tier == input_type \
                    and _generic_desc_matches(tmpl_desc, desc):
                _log(f"  Matched generic cnf '{jobdef}' via template '{tmpl_desc}'")
                return tarball_path
    return None


def find_matching_jobdef(jobdefs, desc, input_type=None):
    """Find the cnf tarball that produces output description ``desc``.

    Three-pass search (exact wins, generic is last resort):
      1. Fast: pre-filter cnfs whose own desc matches ``desc`` (1:1 case).
      2. Fallback: scan all cnfs at the dsconf, matching on declared output
         filenames (catches the suffixed-output case).
      3. Generic: match a generic cnf whose {desc}-templated output can produce
         ``desc`` (e.g. cnf.mu2e.reco -> {desc}-KL matches CeEndpoint-KL).

    ``input_type`` is the tier of the dataset being resolved (e.g. 'dig'); only
    cnf outputs of that tier are matched.
    """
    if not input_type:
        raise ValueError("input_type must be specified")

    result = _search_jobdefs(jobdefs, desc, input_type, name_filter=True)
    if result:
        return result

    _log(f"No 1:1 cnf desc match for '{desc}'; scanning {len(jobdefs)} cnfs at dsconf for output-name match...")
    result = _search_jobdefs(jobdefs, desc, input_type, name_filter=False, verbose_match=True)
    if result:
        return result

    _log(f"No exact cnf for '{desc}'; checking for a generic ({{desc}}-templated) cnf...")
    return _search_generic(jobdefs, desc, input_type)


def _search_jobdefs(jobdefs, desc, input_type, name_filter, verbose_match=False):
    """Iterate jobdefs and return the first tarball whose output desc + type matches."""
    from utils.jobquery import Mu2eJobPars

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

        try:
            outputs = Mu2eJobPars(tarball_path).job_outputs(0)
        except Exception as e:
            # Generic tarballs defer {desc}/sequencer -> job_outputs() raises.
            # They are handled by the generic pass; skip here, don't abort.
            _log(f"Skipping {jobdef} in output scan ({e})")
            continue
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


def output_njobs_map(dsconf):
    """Map {(output_description, output_tier): njobs} for every cnf at <dsconf>,
    opening each cnf exactly once.

    Use this for batch completeness checks: per-dataset resolution re-runs
    list_jobdefs and re-opens tarballs for every dataset (O(datasets x cnfs)),
    which crawls; this is O(cnfs) and the caller just does dict lookups.
    njobs is 0 for open-ended generator cnfs (indeterminate)."""
    from utils.jobquery import Mu2eJobPars
    out = {}
    for jobdef in list_jobdefs(dsconf):
        try:
            tarball = locate_tarball(jobdef)
            jp = Mu2eJobPars(tarball)
            njobs = jp.njobs()
            outputs = jp.job_outputs(0)
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


def build_file_maps(job_io, datasets, njobs, firstjob=0, indices=None):
    """One pass over the cnf's index window building, for each dataset in
    `datasets`, its {filename: window-relative index} map. job_outputs
    returns every output stream per call, so a single scan serves all of
    an entry's datasets (previously one full njobs-scan per dataset —
    and one fresh tarball parse each, megabytes for mixing cnfs).

    With `indices` given, scan exactly those indices instead of
    range(njobs) — map values are the indices as passed (the recovery
    loop passes ABSOLUTE cnf indices with firstjob=0, so values come
    back in the caller's own index space). njobs is ignored in that
    case.

    Structured dataset compare — a substring test would false-match
    sibling dsconfs where one is a prefix of the other (e.g. ..._v1_4 vs
    ..._v1_4-000).
    """
    wanted = set(datasets)
    maps = {ds: {} for ds in datasets}
    scope = indices if indices is not None else range(njobs)
    for job_idx in scope:
        for filename in job_io.job_outputs(firstjob + job_idx).values():
            try:
                ds = str(Mu2eName.parse(filename).dataset)
            except ValueError:
                continue
            if ds in wanted:
                maps[ds][filename] = job_idx
    return maps


def extract_datasets_from_tarball(job_pars, njobs):
    """Extract output dataset names from an already-parsed job definition
    (a Mu2eJobPars instance — parsing is the expensive part, so the caller
    parses once and shares the instance with build_file_maps)."""
    output_datasets = job_pars.output_datasets()

    # If output_datasets is empty, extract from actual output files
    if not output_datasets:
        dataset_set = set()
        for idx in range(min(10, njobs)):
            for filename in job_pars.job_outputs(idx).values():
                # Extract dataset name from filename (force .art extension to
                # match historical behavior — outputs may have other exts).
                try:
                    n = Mu2eName.parse(filename)
                except ValueError:
                    continue
                dataset_set.add(str(n.with_extension('art').dataset))
        output_datasets = list(dataset_set)

    return output_datasets

