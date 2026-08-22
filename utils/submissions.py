#!/usr/bin/env python3
"""Direct-submission subsystem CLI (`submissions`): read-only status,
the verify-and-resubmit + sliced-campaign top-up tick (`run`), and
campaign management verbs.

Processes ledger rows written by the direct backend
(utils/submission_ledger.py). Per active row: skip while jobs are still
in the queue (held jobs reported, never touched), SAM-verify indices
via the cnf's expected output names, then close as complete, resubmit
exactly the missing indices (child row, attempt+1), or close exhausted
at the attempt cap.

Only SAM output-file existence is trusted (the Run1Ban lesson:
consumption-status recovery re-dispatches finished work). Deterministic
payloads re-run identical events, so systematic failures re-fail every
round — `exhausted` is where a human takes over.

Design: wiki/pages/2026-07-18-direct-recovery-loop.md
"""
import argparse
import fcntl
import fnmatch
import getpass
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import submission_ledger
from utils import submit
from utils.check_inputs import _default_locality, _LOC_TO_MDH
from utils.file_resolver import infer_dataset_location, sam_physical_path_or_none
from utils.job_common import Mu2eName, expected_outputs_for
from utils.jobdef_lookup import build_file_maps, extract_datasets_from_tarball
from utils.jobquery import Mu2eJobPars
from utils.jobdesc import njobs_of, is_draining
from utils.samweb_wrapper import (files_in_dataset, definitions_matching,
                                  dataset_file_count, metadata_for_files,
                                  _parse_sam_datetime)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_QUEUED = 5000


def resolve_cap(flag_value):
    """Queue cap for the top-up phase: --max-queued flag >
    MU2E_MAX_QUEUED env > DEFAULT_MAX_QUEUED. Resolved once per
    invocation; nothing persists between runs — the effective cap is
    always readable off the crontab line."""
    if flag_value is not None:
        return flag_value
    env = os.environ.get('MU2E_MAX_QUEUED')
    if env is not None:
        try:
            return int(env)
        except ValueError:
            sys.exit(f"MU2E_MAX_QUEUED is not an integer: {env!r}")
    return DEFAULT_MAX_QUEUED


# Queue-snapshot primitives (fail-closed drain detection) live in
# utils/queue_state.py since 2026-08-16, so jobwait.py can import them
# without dragging in the submit stack (this module -> submit ->
# prod_utils -> samweb_client). Re-imported here under their original
# names so every caller/test reading them off this module keeps working.
from utils.queue_state import (_jobsub_table_states,  # noqa: F401
                               cluster_queue_state, live_clusters,
                               queue_owner)


def verify_row(row, sam_lister=files_in_dataset):
    """SAM-verify one ledger row's indices.

    Returns (missing, partial): absolute cnf indices with ANY expected
    output file absent from SAM, and the subset where only SOME streams
    are absent (a re-run re-pushes the streams that already landed —
    see the duplicate-declare item in the design spec).

    Raises on anything that prevents verification (unlocatable tarball,
    no output datasets, SAM failure): the caller keeps the row active
    and reports. A row is never guessed complete.
    """
    tarball_path = sam_physical_path_or_none(row['tarball'])
    if not tarball_path or not os.path.exists(tarball_path):
        raise RuntimeError(f"cannot locate tarball {row['tarball']}")
    job_io = Mu2eJobPars(tarball_path)
    indices = row['indices']
    datasets = extract_datasets_from_tarball(job_io, len(indices))
    if not datasets:
        raise RuntimeError(f"no output datasets in {row['tarball']}")
    maps = build_file_maps(job_io, datasets, 0, indices=indices)
    expected = {}    # idx -> expected stream count
    missing_ct = {}  # idx -> missing stream count
    for ds in datasets:
        actual = set(sam_lister(ds))
        for fname, idx in maps[ds].items():
            expected[idx] = expected.get(idx, 0) + 1
            if fname not in actual:
                missing_ct[idx] = missing_ct.get(idx, 0) + 1
    unverifiable = [i for i in indices if i not in expected]
    if unverifiable:
        raise RuntimeError(
            f"indices {unverifiable} have no expected output files in "
            f"{row['tarball']} — cannot verify (output datasets: {datasets})")
    missing = sorted(missing_ct)
    partial = sorted(i for i in missing_ct if missing_ct[i] < expected[i])
    return missing, partial


def _draining_expected(camp, datasets, job_pars, count_fn):
    """Denominators for one DRAINING campaign's output datasets.

    A draining campaign has no njobs — its input dataset is still
    growing, so no fixed target exists. The honest denominator for a
    1:1 direct-input stage is the INPUT dataset's current file count:
    80 digis in means 80 mcs out.

    Only `datasets` (what the caller displays) are resolved, one SAM
    count each — never the campaign's whole desc space, which for au
    reco is 21 datasets nobody asked about.

    The output->input desc mapping is CONFIRMED via expected_outputs_for
    (the worker's own substitution), never assumed: CosmicCRYAll
    produces ...CosmicCRYAllOnSpill... while CosmicCRYExtracted takes no
    suffix, and FlatGamma is a prefix of FlatGammaCalo. A cnf that
    suffixes its outputs (`{desc}-KL`) fails the check and keeps its
    "—", the correct answer rather than a fabricated one.
    """
    pattern = camp['entry']['input_pattern']
    out = {}
    for ds in datasets:
        try:
            name = Mu2eName.parse(ds)
        except ValueError:
            continue
        # Candidate input: pattern's tier/owner/dsconf/format with this
        # dataset's own desc; must itself match the pattern or it
        # belongs to some other campaign.
        pat = Mu2eName.parse(pattern)
        candidate = f"{pat.tier}.{pat.owner}.{name.description}.{pat.dsconf}.{pat.extension}"
        if not _matches_pattern(candidate, pattern):
            continue
        probe = (f"{pat.tier}.{pat.owner}.{name.description}.{pat.dsconf}"
                 f".000000_00000000.{pat.extension}")
        try:
            produced = {_dataset_of(o)
                        for o in expected_outputs_for(probe, job_pars)}
        except Exception:
            continue
        if ds not in produced:
            continue
        try:
            out[ds] = count_fn(candidate)
        except Exception:
            continue
    return out


def ledger_expected(db_path, dsconfs=None, *, datasets=None,
                    locate=sam_physical_path_or_none, count_fn=dataset_file_count):
    """Map output dataset name -> expected job count, from the submission ledger.

    njobs is the SUBMITTED window, not the cnf's baked capacity, but the
    ledger names outputs with a glob ("*.art"), so the dataset NAME must
    come from the cnf tarball — same source verify_row uses, and the
    only sound one: CosmicCRYAll produces ...CosmicCRYAllOnSpill... while
    CosmicCRYExtracted takes no suffix, and FlatGamma is a prefix of
    FlatGammaCalo, so neither convention nor prefix matching can be
    trusted.

    dsconfs: optional filter, skips other-dsconf campaigns without
    resolving their tarball so a short listing doesn't pay for the
    whole ledger. datasets: optional set of output dataset names the
    caller will display, required to get a denominator for a DRAINING
    campaign (no njobs): that denominator is the dataset's INPUT file
    count, one SAM count each — resolving a campaign's whole desc space
    uninvited would be 21 queries for au reco alone. Omit it and
    draining campaigns contribute nothing. The denominator MOVES:
    "80/80" is complete against inputs that exist right now, and can
    drop below 100% once the upstream round gains files (see
    _draining_expected). locate/count_fn: injected for testing.

    Returns (expected, failures). expected maps dataset -> max njobs
    over every campaign producing it. njobs is an ABSOLUTE target, not
    an increment: re-enqueuing a tarball resumes the new campaign's
    cursor from where the earlier one stopped, so index windows overlap
    rather than partition (0..249 then 250..1666 — njobs=1667 already
    covers the first campaign's 250). Summing would double-count the
    overlap; max is the cheap equivalent of the true union of submitted
    indices. failures maps tarball -> reason for campaigns that
    couldn't be resolved, contributing nothing rather than a guessed
    denominator; a failed campaign's dataset stays unknown since its
    name was what the tarball would have supplied.
    """
    expected = {}
    failures = {}
    resolved = {}          # tarball -> [datasets], or None when unresolvable
    pars = {}              # draining only: tarball -> Mu2eJobPars, or None
    for camp in submission_ledger.all_campaigns(db_path):
        tarball = camp['tarball']
        entry = camp.get('entry') or {}
        njobs = entry.get('njobs')
        draining = is_draining(entry)
        # A draining campaign has no njobs by definition; skipped
        # unless the caller named the datasets it cares about, since
        # its denominators cost one SAM count apiece.
        if not njobs and not (draining and datasets):
            continue
        if dsconfs is not None:
            try:
                if Mu2eName.parse(tarball).dsconf not in dsconfs:
                    continue
            except ValueError:
                continue
        if draining:
            if tarball not in pars:
                try:
                    path = locate(tarball)
                    if not path:
                        raise RuntimeError("tarball not locatable")
                    pars[tarball] = Mu2eJobPars(path)
                except Exception as e:
                    failures[tarball] = str(e)
                    pars[tarball] = None
            if pars[tarball] is None:
                continue
            for ds, n in _draining_expected(camp, datasets, pars[tarball],
                                            count_fn).items():
                expected[ds] = max(expected.get(ds, 0), n)
            continue
        if tarball not in resolved:
            try:
                path = locate(tarball)
                if not path:
                    raise RuntimeError("tarball not locatable")
                out_datasets = extract_datasets_from_tarball(Mu2eJobPars(path),
                                                             njobs)
                if not out_datasets:
                    raise RuntimeError("no output datasets in tarball")
                resolved[tarball] = out_datasets
            except Exception as e:
                failures[tarball] = str(e)
                resolved[tarball] = None
        out_datasets = resolved[tarball]
        if out_datasets is None:
            continue
        for ds in out_datasets:
            expected[ds] = max(expected.get(ds, 0), njobs)
    return expected, failures


DEFAULT_MIN_AGE_MINUTES = 60


def _dataset_of(fname):
    """Dataset name of a Mu2e file name (drop the sequencer)."""
    return str(Mu2eName.parse(fname).dataset)


def _matches_pattern(name, pattern):
    """True if a dot-name field-by-field matches a SAM '%'-wildcard
    pattern (same field count, each field an fnmatch of the pattern
    field with '%' translated to '*'; a literal pattern field must
    match exactly).

    `definitions_matching` (SAM's `list-definitions --defname`) does a
    substring/prefix match, not a field-grammar match — it can hand
    back draining-era junk names whose LAST field merely starts with
    the pattern's extension (e.g. `art_slice_0_stage_2` against `art`),
    and such a name still parses as a legal 5-field dataset, so the
    ValueError/is_dataset guard alone doesn't catch it. This is the
    real filter for that case.
    """
    fields = pattern.split('.')
    parts = name.split('.')
    return len(parts) == len(fields) and all(
        fnmatch.fnmatchcase(v, f.replace('%', '*'))
        for v, f in zip(parts, fields))


def draining_state(camp, db_path, *,
                   defs_fn=definitions_matching,
                   sam_lister=files_in_dataset,
                   locate=sam_physical_path_or_none):
    """One draining campaign's file sets, computed fresh from SAM + the
    ledger — draining has NO cursor; nothing counts as done until its
    output exists (fix for the POMS-era draining launch-time cursor).

        inputs    pattern datasets' files (exclude_desc removed)
        landed    inputs whose expected outputs ALL exist in SAM
        in_flight files in this campaign's ACTIVE rows
        parked    files in exhausted rows whose outputs are still missing
        pending   inputs − landed − in_flight − parked   (sorted)

    Dataset enumeration is definition-based (production convention).
    Each name SAM hands back is re-verified: must parse as a 5-field
    dataset, description not excluded, and match `input_pattern`
    field-by-field (see `_matches_pattern` — SAM's own match is
    substring-based and lets draining-era `_slice_`/`_full_` junk
    through). Raises on an unlocatable tarball or malformed input
    filename — never guesses.
    """
    entry = camp['entry']
    exclude = set(entry.get('exclude_desc', []))
    path = locate(camp['tarball'])
    if not path or not os.path.exists(path):
        raise RuntimeError(f"cannot locate tarball {camp['tarball']}")
    jp = Mu2eJobPars(path)
    datasets = []
    for d in defs_fn(entry['input_pattern']):
        try:
            n = Mu2eName.parse(d)
        except ValueError:
            continue
        if not n.is_dataset or n.description in exclude:
            continue
        if not _matches_pattern(d, entry['input_pattern']):
            continue
        datasets.append(d)
    inputs = set()
    for ds in datasets:
        inputs.update(sam_lister(ds))
    out_of = {f: expected_outputs_for(f, jp) for f in sorted(inputs)}
    out_datasets = {_dataset_of(o)
                    for outs in out_of.values() for o in outs}
    existing = {ds: set(sam_lister(ds)) for ds in sorted(out_datasets)}
    landed = {f for f, outs in out_of.items()
              if all(o in existing[_dataset_of(o)] for o in outs)}
    in_flight, exhausted = set(), set()
    for r in submission_ledger.all_rows(db_path):
        if r['tarball'] != camp['tarball'] or not is_draining(r['entry']):
            continue
        if r['state'] == 'active':
            in_flight.update(r['indices'])
        elif r['state'] == 'exhausted':
            exhausted.update(r['indices'])
    parked = exhausted - landed
    pending = sorted(inputs - landed - in_flight - parked)
    return {'inputs': inputs, 'landed': landed, 'in_flight': in_flight,
            'parked': parked, 'pending': pending}


def _gate_batch(entry, candidates, *,
                locality=_default_locality,
                metadata_fn=metadata_for_files,
                dataset_location=infer_dataset_location,
                now=None):
    """Gate a candidate batch: (dispatch, young, tape_only).

    Settling age first (the POMS fts= idea: pushOutput declares metadata
    before locations settle — never race a half-pushed upstream batch),
    then dCache residency (never a job that hangs on tape recall).
    Raises RuntimeError whenever age or residency cannot be established:
    fail closed, no dispatch on unknowns.
    """
    now = now or datetime.now(timezone.utc)
    min_age = entry.get('min_age_minutes', DEFAULT_MIN_AGE_MINUTES)
    cutoff = now - timedelta(minutes=min_age)
    md_by_name = {}
    for md in metadata_fn(list(candidates)):
        md_by_name[md.get('file_name')] = md
    old_enough, young = [], []
    for f in candidates:
        # Live SAM metadata carries 'create_date' (ISO string). Fall back
        # to 'create_datetime' for tolerance with older/alternate servers
        # — same key-tuple precedent as definition_creation_date.
        md = md_by_name.get(f) or {}
        stamp = next((md[k] for k in ('create_date', 'create_datetime')
                     if md.get(k)), None)
        dt = _parse_sam_datetime(stamp) if stamp else None
        if dt is None:
            raise RuntimeError(
                f"no SAM create time for {f} — age unknown (fail closed)")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        (old_enough if dt <= cutoff else young).append(f)
    by_ds = {}
    for f in old_enough:
        by_ds.setdefault(_dataset_of(f), []).append(f)
    dispatch, tape_only = [], []
    for ds, fl in sorted(by_ds.items()):
        mdh_loc = _LOC_TO_MDH.get(dataset_location(ds))
        if mdh_loc is None:
            raise RuntimeError(f"unknown storage location for {ds}")
        states = locality(mdh_loc, fl)
        for f in fl:
            st = states.get(f, 'ERROR')
            if st in ('ONLINE', 'ONLINE_AND_NEARLINE'):
                dispatch.append(f)
            elif st == 'NEARLINE':
                tape_only.append(f)
            else:
                raise RuntimeError(f"locality {st!r} for {f} — "
                                   f"residency unknown (fail closed)")
    return dispatch, young, tape_only


def _request_prestage(files, runner=subprocess.run):
    """One batched `mdh prestage-files` request for tape-only pending
    files (entry opts in with `prestage: true`). Never raises — the
    request is an optimization and idempotent server-side; the tick
    continues either way. No-op on an empty batch; the scratch file is
    always unlinked (this repo has a known /tmp-leak history)."""
    if not files:
        return
    path = None
    try:
        with tempfile.NamedTemporaryFile(
                'w', suffix='.txt', delete=False) as fh:
            path = fh.name
            fh.write('\n'.join(sorted(files)) + '\n')
        res = runner(['mdh', 'prestage-files', path],
                     capture_output=True, text=True, timeout=600)
        if res.returncode != 0:
            print(f"  prestage request failed rc={res.returncode}: "
                  f"{(res.stderr or '').strip()[:200]}")
    except Exception as e:
        print(f"  prestage request failed: {e}")
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


# Recoveries get resource headroom by default: a recovery is the tail of
# a population that already succeeded (typically 1-3 jobs of hundreds),
# so the prior that it needs more is high and the blast radius is tiny.
# NOT applied to first submissions — there a memory bump masks an
# oversized merge factor instead of exposing it (MDC2025au RPC,
# 2026-07-26: 300 jobs died at merge 20/100; fix was merge 3/6, not
# memory).
RECOVERY_MEMORY = '4000MB'
RECOVERY_LIFETIME = '48h'


def recovery_resource_kwargs(entry):
    """Recovery resource FLOOR as SubmitOptions kwargs.

    Applies RECOVERY_MEMORY / RECOVERY_LIFETIME only where the row's own
    snapshot entry names nothing — an unset value is what earns a
    recovery the floor, so a row that already carries a value keeps it.
    """
    kwargs = {}
    for key, floor in (('memory', RECOVERY_MEMORY),
                       ('expected_lifetime', RECOVERY_LIFETIME)):
        if not entry.get(key):
            kwargs[key] = floor
    return kwargs


def resubmit(row, missing, db_path, dry_run=False, submit_fn=None,
            origin=None):
    """Resubmit missing indices in-process. Returns True on success.

    The reconstructed entry DROPS firstjob: --indices values are absolute
    cnf indices, so the worker-side firstjob+index resolution must
    degenerate to the identity. The original windowed entry stays in the
    parent row's snapshot.

    `origin` defaults to the automatic-recovery-loop string; a caller
    acting for an operator (`submissions resubmit`) passes a
    distinguishable one, so the ledger's origin column can tell a hand
    re-fire apart from an automatic recovery.
    """
    submit_fn = submit_fn or submit.submit_entry
    entry = {k: v for k, v in row['entry'].items() if k != 'firstjob'}
    options = submit.SubmitOptions(
        ledger_db=str(db_path),
        indices=list(missing),
        ledger_parent=row['id'],
        dry_run=dry_run,
        origin=origin or f"recovery of row {row['id']}",
        **recovery_resource_kwargs(entry))
    print(f"  resubmit row {row['id']}: {len(missing)} indices")
    return _guarded_submit(f"row {row['id']}",
                           lambda: submit_fn(entry, 0, options))


def verify_files_row(row, sam_lister=files_in_dataset):
    """SAM-verify one file-keyed (draining) ledger row.

    The exact analog of verify_row, keyed by input FILENAMES: expected
    outputs come from expected_outputs_for — the worker's own name
    substitution — so verification can never drift from what the job
    actually produced. Returns (missing, partial) as input filenames.
    Raises on anything that prevents verification (unlocatable tarball,
    SAM failure): a row is never guessed complete.
    """
    tarball_path = sam_physical_path_or_none(row['tarball'])
    if not tarball_path or not os.path.exists(tarball_path):
        raise RuntimeError(f"cannot locate tarball {row['tarball']}")
    jp = Mu2eJobPars(tarball_path)
    files = row['indices']            # filenames for a draining row
    out_of = {f: expected_outputs_for(f, jp) for f in files}
    out_datasets = {_dataset_of(o)
                    for outs in out_of.values() for o in outs}
    existing = {ds: set(sam_lister(ds)) for ds in sorted(out_datasets)}
    missing, partial = [], []
    for f in files:
        absent = [o for o in out_of[f]
                  if o not in existing[_dataset_of(o)]]
        if absent:
            missing.append(f)
            if len(absent) < len(out_of[f]):
                partial.append(f)
    return missing, partial


def resubmit_files(row, missing, db_path, dry_run=False, submit_fn=None,
                   origin=None):
    """Draining analog of resubmit(): child submission of exactly the
    missing input files. Returns True on success.

    See resubmit()'s docstring for `origin`'s default/override contract.
    """
    submit_fn = submit_fn or submit.submit_entry
    entry = row['entry']
    options = submit.SubmitOptions(
        ledger_db=str(db_path),
        files=list(missing),
        ledger_parent=row['id'],
        dry_run=dry_run,
        origin=origin or f"recovery of row {row['id']}",
        **recovery_resource_kwargs(entry))
    print(f"  resubmit row {row['id']}: {len(missing)} files")
    return _guarded_submit(f"row {row['id']}",
                           lambda: submit_fn(entry, 0, options))


def total_queued(user=None, runner=subprocess.run):
    """Total idle+running jobs for `user` — the top-up throttle gate —
    or None when the count cannot be trusted (caller skips the phase).

    Counts states I (idle) and R (running) from the default
    `jobsub_q --user` table (see _jobsub_table_states); held/removed/
    other states don't consume cap headroom. Covers ALL the user's jobs
    regardless of how they were launched, so the cap bounds the
    account's whole farm footprint.

    `user` defaults to the submitting identity (see queue_owner). A
    fixed 'mu2epro' here throttled a self run against PRODUCTION's
    footprint: the caller's own jobs never counted toward their cap,
    and a busy production farm could block a self top-up outright."""
    res = runner(['jobsub_q', '--user', user or queue_owner()],
                 capture_output=True, text=True)
    if res.returncode != 0:
        return None
    states = _jobsub_table_states(res.stdout)
    if states is None:
        return None
    return sum(1 for s in states if s in ('I', 'R'))


def _guarded_submit(what, fn):
    """Run one in-process submission; return True on success, False on
    any failure, never propagating.

    Replaces the process boundary a standalone submission CLI used to
    provide when the engine ran out-of-process: a dead subprocess gave
    the tick a nonzero return code and the loop moved on; an in-process
    call that raises would end the tick for every campaign.

    SystemExit is caught EXPLICITLY: submit_entry raises it on an input
    pre-flight failure, and it derives from BaseException, so a bare
    `except Exception` would let it escape — the exact regression this
    helper exists to prevent. KeyboardInterrupt is deliberately NOT
    caught: Ctrl-C must still stop the tick.
    """
    try:
        fn()
        return True
    except (Exception, SystemExit) as e:
        print(f"  {what}: submit FAILED ({type(e).__name__}: {e})")
        return False


def _camp_tarball(camp):
    """The campaign's tarball for evidence-keying: the top-level column
    (every real campaign row from submission_ledger has one), else the
    snapshot entry's own 'tarball' key — a fallback for test doubles
    that only set the latter; production entries always agree with the
    campaign row's own tarball."""
    return camp.get('tarball') or camp['entry'].get('tarball')


def _guarded_submit_with_evidence(what, tarball, db_path, fn):
    """_guarded_submit, plus proof the ledger actually gained a new
    ACTIVE row for `tarball`. Returns True only when both hold.

    submit_entry RETURNS {'status': 'failed'} — it does NOT raise —
    when jobsub_submit exits non-zero, or exits 0 with no parseable
    cluster id (utils/submit.py's _fail_reservation leaves a 'failed'
    reservation). _guarded_submit alone can't see that (it only catches
    exceptions): a returned-but-failed submission would read as
    success, the cursor would advance past a slice never submitted, and
    the 'failed' reservation sits BELOW every future slice window where
    _slice_overlaps_ledger can't reach it — the gap is silent. Same
    evidence check `submissions resubmit` already applies, keyed on
    `parent_id == row['id']`; a tick's slice has no parent row, so this
    keys on TARBALL plus a before/after diff of active row ids — narrow
    enough that a concurrent row for a DIFFERENT tarball can't
    false-positive it, and open_rows() already filters to
    state='active', so a reservation stuck in 'submitting' or closed
    'failed' doesn't count as evidence either.

    Both open_rows() reads sit inside the SAME try as _guarded_submit's
    catch: top_up/drain_tick have no outer try, so an exception here
    (e.g. a locked ledger) would otherwise escape raw and abort the
    whole tick — including a POST-submit read raising AFTER the slice
    already went to the grid, wedging every remaining campaign that
    tick. SystemExit is caught explicitly, same as _guarded_submit;
    KeyboardInterrupt is deliberately NOT caught.
    """
    try:
        before = {r['id'] for r in submission_ledger.open_rows(db_path)
                 if r['tarball'] == tarball}
        if not _guarded_submit(what, fn):
            return False
        after = {r['id'] for r in submission_ledger.open_rows(db_path)
                 if r['tarball'] == tarball}
    except (Exception, SystemExit) as e:
        print(f"  {what}: submit FAILED ({type(e).__name__}: {e})")
        return False
    if not (after - before):
        print(f"  {what}: submit FAILED (no new active ledger row for "
              f"{tarball} — jobsub_submit likely exited non-zero, or "
              f"0 with no parseable cluster id, after already "
              f"returning instead of raising)")
        return False
    return True


def submit_slice(camp, n, db_path, submit_fn=None):
    """Submit the campaign's next slice in-process. The snapshot entry
    ships VERBATIM: firstjob is preserved because cursor and first/num
    are entry-relative, exactly like a manual windowed submission.
    Returns True on submit success — verified by a new active ledger
    row for this tarball, not merely the absence of an exception (see
    _guarded_submit_with_evidence)."""
    submit_fn = submit_fn or submit.submit_entry
    options = submit.SubmitOptions(
        ledger_db=str(db_path),
        first=camp['cursor'],
        num=n,
        origin=f"campaign {camp['id']}",
    )
    print(f"  campaign {camp['id']}: slice first={camp['cursor']} num={n}")
    return _guarded_submit_with_evidence(
        f"campaign {camp['id']}", _camp_tarball(camp), db_path,
        lambda: submit_fn(camp['entry'], 0, options))


def _slice_overlaps_ledger(db_path, tarball, firstjob, cursor, n):
    """The blocking ledger row (truthy) if any row for `tarball` already
    has an absolute cnf index inside the slice's absolute window
    [firstjob+cursor, firstjob+cursor+n), else None. Returns the ROW,
    not a bool, so the caller can name its id in the pause note rather
    than leaving the operator to hunt for which row overlaps.

    Sibling guard: _rows_blocking_indices (below) is the resubmit-verb
    analog, with a DIFFERENT skip list on purpose — this one skips
    draining rows outright (no index space to overlap) and only
    'reconciled' (a campaign slice must stay blocked by every other
    open state, since top-up must never re-cover ground the ledger
    remembers submitting); the other includes draining rows and skips
    the full _SETTLED_STATES list (checking "is this exact index/file
    still live", not "has this cursor position ever been submitted").
    Don't make the two lists match.

    Crash-window guard: rows are RESERVED before jobsub_submit
    (submission_ledger.reserve_submission), so a process that dies
    between claiming the window and recording the cluster still leaves
    a row here to overlap against — deterministic payloads make a
    re-send duplicate physics, not a harmless retry. Also catches a
    human submitting `--first/--num` on a tarball with a live campaign,
    and a 'failed' reservation whose window isn't proven free. Not a
    false-positive source for the recovery loop's OWN resubmits: a
    child row's indices sit strictly below the cursor of the ALREADY
    ADVANCED-PAST parent slice.

    A 'reconciled' row is skipped: only a human `submissions reconcile
    <id>` sets it, asserting the window's jobs are genuinely absent
    (see submission_ledger.reconcile_row). Kept for the audit trail but
    no longer claims index space, or it would block its campaign
    forever with no way out short of hand-editing sqlite.
    """
    lo = firstjob + cursor
    hi = lo + n
    for row in submission_ledger.all_rows(db_path):
        if row['tarball'] != tarball:
            continue
        if is_draining(row['entry']):
            continue   # file-keyed row — no index space to overlap
        if row.get('state') == 'reconciled':
            continue   # human-cleared window — see the docstring
        if any(lo <= idx < hi for idx in row['indices']):
            return row
    return None


# Row states that cannot have live jobs. Everything else — 'active',
# 'submitting', 'failed' — blocks a resubmit. 'failed' blocks
# deliberately: a jobsub_submit that exits non-zero can still have
# created a cluster, so its window is NOT proven free. Clear it with
# `submissions reconcile <row-id>` after checking jobsub_q.
#
# Sibling guard: _slice_overlaps_ledger (above) is the campaign-slice
# analog and skips a shorter list ('reconciled' only, plus draining
# rows outright) — see its docstring for why. Do not "fix" one to
# match the other.
_SETTLED_STATES = ('complete', 'recovered', 'exhausted', 'reconciled')


def _rows_blocking_indices(db_path, tarball, indices):
    """The blocking ledger row (truthy) if any unsettled row for
    `tarball` already covers one of `indices`, else None.

    Scattered-set analog of _slice_overlaps_ledger. Returns the ROW so
    the caller can name its id in `submissions reconcile <row-id>`
    rather than leaving the operator hunting for which one overlaps.

    Works for both index rows and draining (file-keyed) rows:
    row['indices'] holds filenames for the latter, same set
    intersection either way — unlike _slice_overlaps_ledger, this one
    does NOT skip draining rows (see _SETTLED_STATES's comment).
    """
    want = set(indices)
    for row in submission_ledger.all_rows(db_path):
        if row['tarball'] != tarball:
            continue
        if row.get('state') in _SETTLED_STATES:
            continue
        if want & set(row['indices']):
            return row
    return None


def _live_campaign_cursor(db_path, tarball):
    """The ABSOLUTE cnf-index bound of the one LIVE (active or paused)
    campaign for `tarball` (firstjob + cursor), or None if no such
    campaign exists.

    'active or paused' matches the campaigns_live_tarball unique index
    (submission_ledger._connect): at most one campaign can hold this
    state pair per tarball, so this is never ambiguous. A paused
    campaign still counts as live — its bound names ground a future
    `submissions resume` will resubmit from.

    `cursor` on the campaign row is ENTRY-RELATIVE, but callers compare
    against ABSOLUTE `--indices` values (same as `row['indices']`,
    written as `firstjob + i`). Add `firstjob` here — the idiom top_up
    uses before _slice_overlaps_ledger — so the bound is directly
    comparable without the caller redoing the arithmetic (and risking
    the exact entry-relative-vs-absolute bug callers must not
    reintroduce).

    Backs the `submissions resubmit --indices` cursor-bound refusal
    (see main()): an index at or above this bound was never submitted
    by the campaign, so resubmitting it is new work the top-up tick
    will ALSO eventually submit — and if the hand-fired child lands
    first, _slice_overlaps_ledger permanently blocks the tick on it
    with no CLI escape (reconcile_row refuses anything not
    'failed'/'submitting').
    """
    for camp in submission_ledger.all_campaigns(db_path):
        if camp['tarball'] == tarball and camp['state'] in ('active', 'paused'):
            firstjob = camp['entry'].get('firstjob', 0)
            return firstjob + camp['cursor']
    return None


def top_up(db_path, cap, dry_run=False, count_fn=total_queued,
           submit_fn=submit_slice, only_campaign=None):
    """Feed slices from active campaigns while total idle+running stays
    under the cap. Whole slices only (short only at end of entry, never
    clamped to headroom); cycles oldest-first, one slice per campaign
    per cycle; the first slice that would exceed the cap stops the
    tick. Submission failure pauses the campaign (no blind retry —
    deterministic payloads make an unverified resubmit the Run1Ban
    failure mode). Before each slice, checks the ledger for indices
    already covering the slice's absolute window (crash-window guard,
    see _slice_overlaps_ledger) — an overlap pauses rather than
    submits. A campaign whose cursor is at njobs but still 'active'
    (crash between advance_campaign and the completion write)
    self-heals to 'complete'. Returns an action-count summary.

    `only_campaign`, when given, restricts feeding to that campaign;
    every other active campaign is untouched this tick. Default (None)
    feeds every active campaign — the cron's `submissions run`
    behaviour — and must not change when unused."""
    summary = {}

    def bump(key):
        summary[key] = summary.get(key, 0) + 1

    camps = submission_ledger.active_campaigns(db_path)
    if only_campaign is not None:
        camps = [c for c in camps if c['id'] == only_campaign]
    if not camps:
        return summary
    count = count_fn()
    if count is None:
        print("top-up: queue count failed — top-up skipped this tick")
        bump('count-error')
        return summary
    print(f"top-up: {count} idle+running (cap {cap}), "
          f"{len(camps)} active campaign(s)")
    progressed = True
    while progressed:
        progressed = False
        for camp in camps:
            if camp['state'] != 'active':
                continue
            if is_draining(camp['entry']):
                continue   # fed by drain_tick, not by index slices
            njobs = njobs_of(camp['entry'])
            remaining = njobs - camp['cursor']
            if remaining <= 0:
                # Cursor already at njobs but still 'active': a crash
                # between advance_campaign and set_campaign_state
                # ('complete') on a prior tick, or the completion write
                # never happened. Self-heal rather than leaving it
                # stuck active forever.
                if dry_run:
                    print(f"campaign {camp['id']}: cursor already at "
                          f"njobs — would close complete (self-heal)")
                    bump('would-campaign-complete')
                else:
                    submission_ledger.set_campaign_state(
                        db_path, camp['id'], 'complete',
                        note='fully submitted (self-heal)')
                    print(f"campaign {camp['id']}: cursor already at "
                          f"njobs — closed complete (self-heal)")
                    camp['state'] = 'complete'
                    bump('campaign-complete')
                continue
            n = min(camp['slice_size'], remaining)
            if count + n > cap:
                print(f"top-up: campaign {camp['id']}: {count}+{n} > {cap} "
                      f"— headroom < slice, waiting for next tick")
                bump('cap-wait')
                return summary
            firstjob = camp['entry'].get('firstjob', 0)
            blocker = _slice_overlaps_ledger(db_path, camp['tarball'],
                                             firstjob, camp['cursor'], n)
            if blocker:
                # Name the BLOCKING ROW, not the cursor: `resume` alone
                # can't clear this — the row keeps overlapping and the
                # next tick re-pauses the campaign immediately. The way
                # out is `submissions reconcile <row-id>` after checking
                # jobsub_q.
                bid = blocker.get('id')
                fix = (f"check jobsub_q, then `submissions reconcile "
                       f"{bid}` and `submissions resume {camp['id']}`"
                       if blocker.get('state') in
                       submission_ledger.RECONCILABLE_STATES else
                       f"row {bid} is {blocker.get('state')!r} — reconcile "
                       f"the campaign cursor before "
                       f"`submissions resume {camp['id']}`")
                if dry_run:
                    print(f"campaign {camp['id']}: ledger row {bid} already "
                          f"covers indices in [{firstjob + camp['cursor']}.."
                          f"{firstjob + camp['cursor'] + n - 1}] — would "
                          f"pause (crash-window suspected)")
                    bump('would-pause-overlap')
                else:
                    submission_ledger.set_campaign_state(
                        db_path, camp['id'], 'paused',
                        note=f'ledger row {bid} already covers indices in '
                             f'this slice — crash-window suspected; {fix}')
                    print(f"campaign {camp['id']}: ledger row {bid} already "
                          f"covers indices in this slice — PAUSED "
                          f"(crash-window suspected; {fix})")
                    camp['state'] = 'paused'
                    bump('campaign-paused')
                continue
            if dry_run:
                print(f"campaign {camp['id']}: would submit slice "
                      f"first={camp['cursor']} num={n}")
                bump('would-slice')
            else:
                if not submit_fn(camp, n, db_path):
                    # A failed submit usually leaves a 'failed'
                    # reservation covering this window, which keeps
                    # overlapping: `resume` alone re-pauses the
                    # campaign next tick. Say so, or the operator loops.
                    fix = ('check the submit log and jobsub_q, then '
                           '`submissions reconcile <ROW>` for the failed '
                           'reservation (if any) and `submissions resume '
                           f"{camp['id']}`")
                    submission_ledger.set_campaign_state(
                        db_path, camp['id'], 'paused',
                        note=f'submit failed — {fix}')
                    print(f"campaign {camp['id']}: submit FAILED — PAUSED "
                          f"(no blind retry; {fix})")
                    camp['state'] = 'paused'
                    bump('campaign-paused')
                    continue
                submission_ledger.advance_campaign(
                    db_path, camp['id'], camp['cursor'] + n)
                bump('slice')
            camp['cursor'] += n
            count += n
            progressed = True
            if camp['cursor'] >= njobs:
                if dry_run:
                    print(f"campaign {camp['id']}: would close complete")
                    bump('would-campaign-complete')
                else:
                    submission_ledger.set_campaign_state(
                        db_path, camp['id'], 'complete',
                        note='fully submitted')
                    print(f"campaign {camp['id']}: fully submitted — "
                          f"complete (verification continues per ledger "
                          f"row)")
                    bump('campaign-complete')
                camp['state'] = 'complete'
    return summary


def submit_drain_batch(camp, files, db_path, submit_fn=None):
    """Submit one draining batch in-process. The snapshot entry ships
    VERBATIM. Returns True on submit success — verified by a new active
    ledger row for this tarball (see _guarded_submit_with_evidence)."""
    submit_fn = submit_fn or submit.submit_entry
    options = submit.SubmitOptions(
        ledger_db=str(db_path),
        files=list(files),
        origin=f"campaign {camp['id']} drain",
    )
    print(f"  campaign {camp['id']}: batch of {len(files)}")
    return _guarded_submit_with_evidence(
        f"campaign {camp['id']}", _camp_tarball(camp), db_path,
        lambda: submit_fn(camp['entry'], 0, options))


def drain_tick(db_path, cap, dry_run=False, count_fn=total_queued,
               submit_fn=submit_drain_batch, state_fn=draining_state,
               gate_fn=_gate_batch, prestage_fn=_request_prestage):
    """Feed draining campaigns: ONE gated batch per campaign per tick,
    oldest-first, under the same queue cap as index top-up (fresh
    count — index slices submitted moments earlier are already in it).
    Draining state is recomputed from SAM each tick and every unknown
    fails closed; a batch-submit failure pauses the campaign (no blind
    retry — the Run1Ban rule)."""
    summary = {}

    def bump(key):
        summary[key] = summary.get(key, 0) + 1

    camps = [c for c in submission_ledger.active_campaigns(db_path)
             if is_draining(c['entry'])]
    if not camps:
        return summary
    count = count_fn()
    if count is None:
        print("drain: queue count failed — draining skipped this tick")
        bump('count-error')
        return summary
    print(f"drain: {count} idle+running (cap {cap}), "
          f"{len(camps)} draining campaign(s)")
    for camp in camps:
        cid = camp['id']
        try:
            st = state_fn(camp, db_path)
        except Exception as e:
            print(f"campaign {cid}: draining state failed: {e} — "
                  f"skipped this tick (fail-closed)")
            bump('drain-error')
            continue
        n_in = len(st['inputs'])
        pct = 100.0 * len(st['landed']) / n_in if n_in else 0.0
        print(f"campaign {cid}: landed {len(st['landed'])}/{n_in} "
              f"({pct:.1f}%) | in-flight {len(st['in_flight'])} | "
              f"parked {len(st['parked'])} | pending {len(st['pending'])}")
        if not st['pending']:
            bump('drain-idle')
            continue
        candidates = st['pending'][:camp['slice_size']]
        try:
            batch, young, tape_only = gate_fn(camp['entry'], candidates)
        except Exception as e:
            print(f"campaign {cid}: batch gate failed: {e} — no "
                  f"dispatch this tick (fail-closed)")
            bump('drain-error')
            continue
        if young or tape_only:
            print(f"campaign {cid}: withheld {len(young)} too-young, "
                  f"{len(tape_only)} tape-only")
        if tape_only and camp['entry'].get('prestage'):
            if dry_run:
                print(f"campaign {cid}: would request prestage of "
                      f"{len(tape_only)} file(s)")
            else:
                prestage_fn(tape_only)
                print(f"campaign {cid}: prestage requested for "
                      f"{len(tape_only)} file(s)")
        if not batch:
            bump('drain-gated')
            continue
        if count + len(batch) > cap:
            print(f"drain: campaign {cid}: {count}+{len(batch)} > {cap} "
                  f"— headroom < batch, waiting for next tick")
            bump('drain-cap-wait')
            break
        if dry_run:
            print(f"campaign {cid}: would submit batch of {len(batch)}")
            bump('would-drain-batch')
            count += len(batch)
            continue
        if not submit_fn(camp, batch, db_path):
            submission_ledger.set_campaign_state(
                db_path, cid, 'paused',
                note='batch submit failed — check the submit log and '
                     'jobsub_q, then `submissions resume <ID>`')
            print(f"campaign {cid}: batch submit FAILED — PAUSED "
                  f"(no blind retry)")
            bump('campaign-paused')
            continue
        count += len(batch)
        bump('drain-batch')
    return summary


def manage_campaign(db_path, camp_id, action, note=None, close_rows=False):
    """Operator switches. cancel closes the campaign only —
    already-submitted rows still get recovered normally. note applies
    to pause/cancel; resume never writes one (the pause reason is
    preserved). complete is the operator close-out for draining
    campaigns — non-blocking, since closing with parked files is
    legitimate. A paused campaign must be resumed first; paused ->
    complete isn't a ledger transition.

    close_rows=True (cancel only): abandon the round. Moves every open
    row on this tarball to 'exhausted' ("no more attempts") so the
    next tick recovers nothing. Not the default — it discards the
    ledger's ability to finish partially-run work, which a plain
    cancel preserves. Rows match by tarball (no campaign_id column);
    closed ids are RETURNED so a stray predecessor row isn't hidden in
    a count.

    A 'submitting' row is refused outright, not skipped: it's a claimed
    window whose jobsub_submit may still be in flight, and closing
    under it orphans the cluster — nothing is written, not even the
    campaign state.

    Cancelling an already-cancelled campaign is a no-op here (the flag
    is normally reached AFTER a cancel), not an error; bare cancel
    still raises on it.
    """
    target = {'pause': 'paused', 'resume': 'active',
              'cancel': 'cancelled', 'complete': 'complete'}[action]
    if not close_rows:
        submission_ledger.set_campaign_state(
            db_path, camp_id, target,
            note=note if note is not None else f'operator {action}')
        print(f"campaign {camp_id}: {action} -> {target}")
        return []

    if action != 'cancel':
        raise ValueError(f"--close-rows is only valid with cancel, "
                         f"not {action}")
    camp = next((c for c in submission_ledger.all_campaigns(db_path)
                 if c['id'] == camp_id), None)
    if camp is None:
        raise ValueError(f"no campaign {camp_id}")
    reserved = [r['id'] for r in submission_ledger.reserved_rows(db_path)
                if r['tarball'] == camp['tarball']]
    if reserved:
        raise ValueError(
            f"campaign {camp_id}: rows "
            f"{', '.join(str(i) for i in reserved)} are still "
            f"'submitting' — a jobsub_submit may be in flight. Check "
            f"jobsub_q and `submissions reconcile <id>` them first; "
            f"nothing was closed")
    if note is None:
        note = ('operator cancel --close-rows: indices deliberately '
                'not recovered')
    if camp['state'] == 'cancelled':
        print(f"campaign {camp_id}: already cancelled")
    else:
        submission_ledger.set_campaign_state(db_path, camp_id, 'cancelled',
                                             note=note)
        print(f"campaign {camp_id}: cancel -> cancelled")
    closed = []
    for row in submission_ledger.open_rows(db_path):
        if row['tarball'] != camp['tarball']:
            continue
        submission_ledger.close_row(db_path, row['id'], 'exhausted',
                                    note=note)
        print(f"row {row['id']}: active -> exhausted")
        closed.append(row['id'])
    if not closed:
        print(f"campaign {camp_id}: no open rows to close")
    return closed


def process_row(row, db_path, max_attempts, clusters=None, dry_run=False,
                queue_state_fn=cluster_queue_state, verify_fn=None,
                resubmit_fn=None):
    """Drive one ledger row through the gate/verify/act sequence.
    `clusters` is the per-tick live_clusters() snapshot the drain-check
    reads (None → the snapshot failed → every row skips as queue-error).

    verify_fn/resubmit_fn default per row kind: file-keyed (draining)
    rows verify via verify_files_row and recover via resubmit_files;
    index rows keep verify_row/resubmit. Explicit injections win.

    Returns the action taken: 'running' | 'held' | 'queue-error' |
    'verify-error' | 'complete' | 'resubmitted' | 'resubmit-error' |
    'exhausted' | 'would-resubmit' | 'would-complete' | 'would-exhaust' |
    'child-active' | 'child-reserved' | 'child-missing' | 'would-recover'.
    """
    if verify_fn is None:
        verify_fn = (verify_files_row if is_draining(row['entry'])
                     else verify_row)
    if resubmit_fn is None:
        resubmit_fn = (resubmit_files if is_draining(row['entry'])
                       else resubmit)
    rid = row['id']
    if not row['cluster_id']:
        print(f"row {rid}: no cluster id recorded — cannot "
              f"drain-check; update the row manually")
        return 'queue-error'
    state = queue_state_fn(row['cluster_id'], clusters)
    if state == 'running':
        print(f"row {rid}: jobs still in queue — skip")
        return 'running'
    if state == 'held':
        print(f"row {rid}: HELD jobs in cluster {row['cluster_id']} — human "
              f"decision needed (release or rm); loop will not act")
        return 'held'
    if state == 'error':
        print(f"row {rid}: jobsub_q --user failed — skip (fail-closed)")
        return 'queue-error'
    try:
        missing, partial = verify_fn(row)
    except Exception as e:
        print(f"row {rid}: verify failed: {e} — row stays active")
        return 'verify-error'
    if partial:
        print(f"row {rid}: PARTIAL outputs at indices {partial} — some "
              f"streams landed; a re-run re-pushes the existing files")
    if not missing:
        if dry_run:
            print(f"row {rid}: would close complete "
                  f"({len(row['indices'])} indices verified)")
            return 'would-complete'
        submission_ledger.close_row(
            db_path, rid, 'complete',
            note=f"{len(row['indices'])} indices verified")
        print(f"row {rid}: complete ({len(row['indices'])} indices)")
        return 'complete'
    print(f"row {rid}: {len(missing)}/{len(row['indices'])} indices "
          f"missing outputs")
    # A RESERVED child — 'submitting', window claimed, no cluster
    # attached — is checked BEFORE the active-child repair below; it is
    # not the same case. Measured 2026-08-09 by killing `submissions
    # run` mid-recovery: open_rows() selects state='active' only, so the
    # orphan child was invisible and the loop cheerfully cut a SECOND
    # child for the same indices. That kill landed before jobsub_submit
    # created anything, so nothing duplicated — but landing in the
    # window the two-phase write exists to survive (cluster created,
    # attach not yet written) would run two clusters on the same
    # deterministic payload.
    #
    # _slice_overlaps_ledger doesn't cover this: it guards CAMPAIGN
    # slices, and a recovery child's indices sit strictly below the
    # campaign cursor by construction. Recoveries need their own
    # parent-scoped guard, which is this.
    #
    # Whether that cluster exists can't be decided from the ledger, so
    # this refuses rather than guessing: the parent stays active, no
    # resubmit issued, and the operator is pointed at the one row to
    # resolve (`submissions reconcile <id>` after checking jobsub_q).
    # Fail-closed — an unproven window is not a free window.
    reserved = [r for r in submission_ledger.reserved_rows(db_path)
                if r['parent_id'] == rid]
    if reserved:
        print(f"row {rid}: child row {reserved[0]['id']} is RESERVED with "
              f"no cluster — a prior recovery died mid-submit. NOT "
              f"resubmitting: its jobs may be live. Check jobsub_q, then "
              f"`submissions reconcile {reserved[0]['id']}`")
        return 'child-reserved'
    children = [r for r in submission_ledger.open_rows(db_path)
                if r['parent_id'] == rid]
    if children:
        if dry_run:
            print(f"row {rid}: child row {children[0]['id']} already "
                  f"active — would close recovered (crash-window repair)")
            return 'would-recover'
        submission_ledger.close_row(
            db_path, rid, 'recovered',
            note=f"child row {children[0]['id']} already active "
                 f"(crash-window repair)")
        print(f"row {rid}: child row {children[0]['id']} already active — "
              f"closed recovered (crash-window repair)")
        return 'child-active'
    if row['attempt'] >= max_attempts:
        if dry_run:
            print(f"row {rid}: would mark EXHAUSTED (attempt "
                  f"{row['attempt']} at cap; {len(missing)} missing)")
            return 'would-exhaust'
        submission_ledger.close_row(
            db_path, rid, 'exhausted',
            note=f"{len(missing)} indices missing after attempt "
                 f"{row['attempt']}: {missing[:50]}")
        print(f"row {rid}: EXHAUSTED after attempt {row['attempt']} — "
              f"human takes over. Missing: {missing}")
        return 'exhausted'
    if dry_run:
        print(f"row {rid}: would resubmit {len(missing)} indices "
              f"(attempt {row['attempt'] + 1})")
        return 'would-resubmit'
    if resubmit_fn(row, missing, db_path):
        children = [r for r in submission_ledger.open_rows(db_path)
                    if r['parent_id'] == rid]
        if not children:
            submission_ledger.close_row(
                db_path, rid, 'recovered',
                note=f"resubmitted {len(missing)} indices but child ledger "
                     f"row MISSING — chain unwatched, verify manually")
            print(f"row {rid}: resubmit succeeded but NO child ledger row "
                  f"found — the new submission is UNWATCHED; verify "
                  f"manually (indices {missing})")
            return 'child-missing'
        submission_ledger.close_row(
            db_path, rid, 'recovered',
            note=f"{len(missing)} indices -> child row {children[0]['id']}")
        return 'resubmitted'
    print(f"row {rid}: resubmit FAILED — row stays active")
    return 'resubmit-error'


def print_status(db_path):
    """Read-only ledger table (safe under any account — status checks
    never need mu2epro)."""
    rows = submission_ledger.all_rows(db_path)
    if not rows:
        # A freshly-enqueued draining campaign has no ledger rows yet
        # but is not "nothing to see" — fall through to the campaigns
        # block instead of hiding it.
        print(f"Ledger is empty ({db_path}).")
    else:
        print(f"{'id':>4} {'state':<10} {'att':>3} {'parent':>6} {'#idx':>5}  "
              f"{'created':<20} tarball")
        for r in rows:
            print(f"{r['id']:>4} {r['state']:<10} {r['attempt']:>3} "
                  f"{str(r['parent_id'] or ''):>6} {len(r['indices']):>5}  "
                  f"{r['created_utc']:<20} {r['tarball']}")
    camps = submission_ledger.all_campaigns(db_path)
    if camps:
        print(f"\n{'id':>4} {'state':<10} {'cursor':>12} {'slice':>6}  "
              f"{'created':<20} tarball")
        for c in camps:
            if is_draining(c['entry']):
                mine = [r for r in rows
                        if r['tarball'] == c['tarball']
                        and is_draining(r['entry'])]
                infl = sum(len(r['indices']) for r in mine
                           if r['state'] == 'active')
                exh = sum(len(r['indices']) for r in mine
                          if r['state'] == 'exhausted')
                print(f"{c['id']:>4} {c['state']:<10} "
                      f"{'draining':>12} {c['slice_size']:>6}  "
                      f"{c['created_utc']:<20} {c['tarball']}")
                print(f"{'':>4} pattern {c['entry']['input_pattern']}  "
                      f"in-flight {infl}  exhausted-files {exh}  "
                      f"(drained fraction: `submissions run --dry-run`)")
                continue
            njobs = njobs_of(c['entry'])
            print(f"{c['id']:>4} {c['state']:<10} "
                  f"{str(c['cursor']) + '/' + str(njobs):>12} "
                  f"{c['slice_size']:>6}  {c['created_utc']:<20} "
                  f"{c['tarball']}")
    stuck = submission_ledger.reserved_rows(db_path)
    if stuck:
        print(f"\nNEEDS RECONCILIATION — {len(stuck)} reserved row(s) with "
              f"no cluster. A submit died mid-flight; check jobsub_q, then "
              f"`submissions reconcile <ROW>` to free these windows:")
        for row in stuck:
            idx = row['indices']
            span = f"{idx[0]}..{idx[-1]}" if idx else 'none'
            print(f"  row {row['id']}  {row['tarball']}  indices {span}  "
                  f"reserved {row['created_utc']}")


# `status` is the only read verb. Every other verb mutates, and a
# non-mu2epro caller can't write the production ledger at all, so a
# mutating default of "production" would only ever fail. For mu2epro,
# ledger_for() IS the production path, so nothing changes there.
_READ_VERBS = ('status',)


def resolve_db(opts):
    """Ledger path for this invocation: explicit --db, else --mine or
    the per-verb default.

    A DEFAULTED (derived) path — the --mine branch and the mutating-verb
    fallback, both ledger_for() — gets its directory created here,
    since it can't be a typo. An explicit --db, and the read verb
    `status`'s production default, never do: a typo there must fail
    loudly, not silently make a stray database (see
    submission_ledger.ensure_ledger_dir).
    """
    if getattr(opts, 'db', None):
        return opts.db
    if getattr(opts, 'mine', False):
        return submission_ledger.ensure_ledger_dir(
            submission_ledger.ledger_for())
    if getattr(opts, 'verb', None) in _READ_VERBS:
        return submission_ledger.DEFAULT_DB
    return submission_ledger.ensure_ledger_dir(submission_ledger.ledger_for())


def build_parser():
    p = argparse.ArgumentParser(
        prog='submissions',
        description='Direct-submission subsystem CLI: status (default '
                    'verb, read-only), the verify/resubmit/'
                    'top-up tick (run), campaign management '
                    '(pause/resume/cancel/complete), and row '
                    'reconciliation (reconcile).')
    p.add_argument('--db', default=None,
                   help='Submission-ledger sqlite DB. Default: the '
                        f'production ledger ({submission_ledger.PRODUCTION_DB}) '
                        'for `status`, your own ledger for every mutating '
                        'verb. Env MU2E_SUBMISSION_DB overrides the '
                        'production default.')
    p.add_argument('--mine', action='store_true',
                   help='Use your own ledger '
                        '(/exp/mu2e/data/users/$USER/prodtools/submissions.db) '
                        'instead of the per-verb default.')
    sub = p.add_subparsers(dest='verb')

    sub.add_parser('status',
                   help='Print ledger + campaigns + queue cap and exit '
                        '(read-only; the default verb)')

    run = sub.add_parser('run',
                         help='One tick: recovery pass then campaign '
                              'top-up (the cron entry point)')
    run.add_argument('--dry-run', action='store_true',
                     help='Report would-* actions only; no submissions, '
                          'no state changes')
    run.add_argument('--row', type=int, default=None,
                     help='Process only this ledger row id (skips '
                          'top-up)')
    run.add_argument('--max-attempts', type=int,
                     default=DEFAULT_MAX_ATTEMPTS,
                     help=f'Attempt cap per chain (default '
                          f'{DEFAULT_MAX_ATTEMPTS}); at the cap the row '
                          f'is marked exhausted for a human')
    run.add_argument('--max-queued', type=int, default=None,
                     help=f'Total mu2epro idle+running cap for the '
                          f'top-up phase (default: MU2E_MAX_QUEUED env, '
                          f'then {DEFAULT_MAX_QUEUED})')
    run.add_argument('--campaign', type=int, default=None,
                     help='Top up only this campaign id (the recovery '
                          'pass still runs). Without it every active '
                          'campaign is ticked, which is the cron '
                          'behaviour.')

    pause = sub.add_parser('pause', help='Pause an active campaign')
    pause.add_argument('camp_id', type=int)
    pause.add_argument('--note', default=None,
                       help='Reason recorded on the campaign (default: '
                            '"operator pause")')
    resume = sub.add_parser('resume',
                            help='Reactivate a paused campaign')
    resume.add_argument('camp_id', type=int)
    cancel = sub.add_parser('cancel',
                            help='Cancel a campaign (already-submitted '
                                 'rows still get recovered)')
    cancel.add_argument('camp_id', type=int)
    cancel.add_argument('--note', default=None,
                        help='Reason recorded on the campaign (default: '
                             '"operator cancel")')
    cancel.add_argument(
        '--close-rows', action='store_true',
        help='Also close every open row on this campaign to '
             '"exhausted", so the next tick recovers NOTHING. Use when '
             'the round is being abandoned and its clusters removed; '
             'without it the already-submitted indices are recovered '
             'normally. Safe to run on an already-cancelled campaign.')
    comp = sub.add_parser('complete',
                          help='Close a campaign complete (operator '
                               'close-out for draining campaigns; '
                               'already-submitted rows still get '
                               'verified/recovered)')
    comp.add_argument('camp_id', type=int)
    comp.add_argument('--note', default=None,
                      help='Reason recorded on the campaign (default: '
                           '"operator complete")')
    slice_p = sub.add_parser('set-slice',
                             help='Retune a live campaign\'s batch size '
                                  '(takes effect on the next tick)')
    slice_p.add_argument('camp_id', type=int)
    slice_p.add_argument('slice_size', type=int)

    rec_p = sub.add_parser(
        'reconcile',
        help='Close a failed/stuck RESERVATION ROW after checking '
             'jobsub_q (the only way to unblock a campaign whose slice '
             'window a failed submit still covers)',
        description='Close a ledger row left in `failed` or `submitting` '
                    'so its index window stops blocking the campaign. '
                    'BY RUNNING THIS YOU ASSERT that you have checked '
                    'jobsub_q and that the jobs for this window are '
                    'genuinely absent from the queue: a jobsub_submit '
                    'that exits non-zero can still have created a '
                    'cluster, and re-feeding a window that is actually '
                    'running duplicates physics (deterministic '
                    'payloads). Nothing clears these rows '
                    'automatically, and this never touches a campaign '
                    'cursor — run `submissions resume <ID>` afterwards.')
    rec_p.add_argument('row_id', type=int)
    rec_p.add_argument('--note', default=None,
                       help='Reason recorded on the row (default: '
                            '"operator reconcile: jobsub_q checked, '
                            'window free")')

    mem_p = sub.add_parser('set-memory',
                           help='Set a live campaign\'s memory request '
                                '(takes effect on the next tick; does '
                                'NOT reach already-submitted rows)')
    mem_p.add_argument('camp_id', type=int)
    mem_p.add_argument('memory', help="e.g. 3000MB")

    entry_p = sub.add_parser(
        'set-entry',
        help='Set one key on a live campaign\'s entry (takes effect on '
             'the next tick)')
    entry_p.add_argument('camp_id', type=int)
    entry_p.add_argument('key',
                         choices=submission_ledger.EDITABLE_ENTRY_KEYS)
    entry_p.add_argument('value', help='e.g. resilient, 3000MB, 48h')
    entry_p.add_argument(
        '--include-open-rows', action='store_true',
        help='Also rewrite not-yet-closed rows on this campaign\'s '
             'tarball, so their RECOVERIES use the new value. Off by '
             'default because an unset memory is what earns a recovery '
             f'the {RECOVERY_MEMORY} floor.')

    resub_p = sub.add_parser(
        'resubmit',
        help='Re-fire specific work from a ledger row by hand',
        description='Submit a named set of indices or input files from an '
                    'existing ledger row, as a child submission (attempt+1). '
                    'The entry comes from the row, so there is no file to '
                    'write. REFUSES when any named index or file is still '
                    'covered by an unsettled row for the same tarball: '
                    'payloads are deterministic, so re-sending live work '
                    'duplicates physics. Clear a stuck row with '
                    '`submissions reconcile <row-id>` first.')
    resub_p.add_argument('row_id', type=int)
    resub_group = resub_p.add_mutually_exclusive_group(required=True)
    resub_group.add_argument('--indices', default=None,
                             help='Comma/space-separated ABSOLUTE cnf '
                                  'indices')
    resub_group.add_argument('--indices-file', default=None,
                             help='File of absolute cnf indices; `#` '
                                  'comment lines ignored')
    resub_group.add_argument('--files', default=None,
                             help='File of input art filenames, one per '
                                  'line, for a draining row')
    resub_p.add_argument('--dry-run', action='store_true',
                         help='Print what would be submitted, submit '
                              'nothing')

    # Bare invocation (no verb) IS status — an explicit default, not a
    # hidden fallthrough (spec Change 1). Must come AFTER
    # add_subparsers(dest='verb'): the subparsers action sets its own
    # default of None for that dest, which otherwise clobbers this.
    p.set_defaults(verb='status')
    return p


def _acquire_lock(db_path):
    """One mutating pass at a time per DB — guards manual runs racing
    the cron (both passing the drain gate before either closes a row =
    double submit). Read-only modes never call this. The fd is held for
    the process lifetime; released on exit."""
    lock_path = os.path.join(os.path.dirname(db_path) or '.',
                             'submissions.lock')
    _acquire_lock._fh = open(lock_path, 'w')
    try:
        fcntl.flock(_acquire_lock._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(f"another submissions run holds {lock_path} — exiting")


# Summary keys that mean "a human must look": any nonzero count makes
# the tick exit 2, repeated every tick until someone clears the cause.
ATTENTION_KEYS = ('held', 'exhausted', 'would-exhaust', 'child-missing',
                  'campaign-paused', 'would-pause-overlap', 'count-error',
                  'paused-campaign', 'drain-error', 'child-reserved')


def _run_pass(args):
    """The tick: recovery pass over active ledger rows, then campaign
    top-up. Exits 2 when anything needs human attention."""
    rows = submission_ledger.open_rows(args.db)
    if args.row is not None:
        rows = [r for r in rows if r['id'] == args.row]
        if not rows:
            sys.exit(f"no active row {args.row} in {args.db}")
    if not rows:
        print(f"No active submissions ({args.db}).")

    summary = {}
    if rows:
        # ONE queue snapshot per tick drives every row's drain-check.
        # None means the query couldn't be trusted — every row skips
        # as queue-error (fail-closed), never guessed drained.
        clusters = live_clusters()
        if clusters is None:
            print("drain-check: jobsub_q --user failed — no row verified "
                  "this tick (fail-closed)")
        for row in rows:
            action = process_row(row, args.db, args.max_attempts,
                                 clusters=clusters, dry_run=args.dry_run)
            summary[action] = summary.get(action, 0) + 1

    if args.row is None:
        # Top-up AFTER the recovery pass: resubmissions are already in
        # the queue when the count is taken, so the cap covers them.
        # drain_tick feeds draining campaigns separately (file-keyed
        # batches, no index cursor) but shares the same queue cap.
        for tick_fn in (top_up, drain_tick):
            kwargs = {'dry_run': args.dry_run}
            if tick_fn is top_up:
                # Only top_up understands --campaign: drain_tick feeds
                # draining campaigns separately and is unaffected by
                # the filter (see the flag's own help text).
                kwargs['only_campaign'] = args.campaign
            for k, v in tick_fn(args.db, resolve_cap(args.max_queued),
                                **kwargs).items():
                summary[k] = summary.get(k, 0) + v
        # A paused campaign means "waiting on a human" — repeat the
        # exit-2 signal EVERY tick until someone resumes or cancels,
        # not just on the tick that paused it.
        paused = [c for c in submission_ledger.all_campaigns(args.db)
                  if c['state'] == 'paused']
        if paused:
            ids = ', '.join(str(c['id']) for c in paused)
            print(f"ATTENTION: paused campaign(s) awaiting a human: "
                  f"{ids} (submissions resume/cancel to clear)")
            summary['paused-campaign'] = len(paused)

    if summary:
        print("submissions summary: "
              + ", ".join(f"{k}={v}" for k, v in sorted(summary.items())))
    if any(summary.get(k) for k in ATTENTION_KEYS):
        sys.exit(2)


def main(argv=None):
    args = build_parser().parse_args(argv)
    verb = args.verb
    # Resolved ONCE here; args.db is overwritten so every use below
    # (including inside _run_pass) sees this same value rather than
    # re-resolving and risking a second answer that disagrees.
    db = args.db = resolve_db(args)

    if verb == 'status':
        print(f"queue cap in effect: {resolve_cap(None)}")
        print_status(db)
        return

    if verb == 'set-slice':
        _acquire_lock(db)
        try:
            old = submission_ledger.set_campaign_slice(
                db, args.camp_id, args.slice_size)
        except ValueError as e:
            sys.exit(f"submissions: {e}")
        print(f"campaign {args.camp_id}: slice_size {old} -> "
              f"{args.slice_size} (applies from the next tick)")
        return

    if verb == 'set-memory':
        _acquire_lock(db)
        try:
            old = submission_ledger.set_campaign_memory(
                db, args.camp_id, args.memory)
        except ValueError as e:
            sys.exit(f"submissions: {e}")
        print(f"campaign {args.camp_id}: memory {old or 'unset'} -> "
              f"{args.memory} (applies from the next tick; rows already "
              f"submitted keep their own entry, so their recoveries use "
              f"the {RECOVERY_MEMORY} floor)")
        return

    if verb == 'set-entry':
        _acquire_lock(db)
        # Read the state BEFORE the write: on a settled campaign the
        # ledger deliberately leaves the campaign snapshot alone and
        # edits only the open rows, so claiming the campaign changed
        # would misreport what happened.
        was = next((c for c in submission_ledger.all_campaigns(db)
                    if c['id'] == args.camp_id), None)
        live = was is not None and was['state'] in ('active', 'paused')
        try:
            old, rows = submission_ledger.set_campaign_entry_key(
                db, args.camp_id, args.key, args.value,
                include_open_rows=args.include_open_rows)
        except ValueError as e:
            sys.exit(f"submissions: {e}")
        if live:
            print(f"campaign {args.camp_id}: {args.key} {old or 'unset'} -> "
                  f"{args.value} (applies from the next tick)")
        else:
            print(f"campaign {args.camp_id} is {was['state']}: campaign "
                  f"snapshot left unchanged (no future slice reads it); "
                  f"{args.key} set to {args.value} on its open rows only")
        if args.include_open_rows:
            print(f"  rows updated: "
                  f"{', '.join(str(r) for r in rows) if rows else 'none'}")
        else:
            print("  rows already submitted keep their own entry; pass "
                  "--include-open-rows to reach their recoveries")
        return

    if verb == 'reconcile':
        _acquire_lock(db)
        note = args.note or ('operator reconcile: jobsub_q checked, '
                             'window free')
        try:
            was = submission_ledger.reconcile_row(db, args.row_id, note)
        except ValueError as e:
            sys.exit(f"submissions: {e}")
        print(f"row {args.row_id}: {was} -> reconciled ({note}). Its "
              f"indices no longer block a campaign slice; "
              f"`submissions resume <ID>` to restart the campaign.")
        return

    if verb == 'resubmit':
        row = submission_ledger.row_by_id(db, args.row_id)
        if row is None:
            sys.exit(f"submissions: no ledger row {args.row_id} in {db}")
        try:
            if args.files is not None:
                payload = submit.parse_files(args.files)
                if not is_draining(row['entry']):
                    sys.exit(f"submissions: row {args.row_id} is an index "
                             f"row — use --indices, not --files")
            else:
                payload = submit.parse_indices(args.indices,
                                               args.indices_file)
                if is_draining(row['entry']):
                    sys.exit(f"submissions: row {args.row_id} is a "
                             f"draining (file-keyed) row — use --files, "
                             f"not --indices")
        except (ValueError, OSError) as e:
            sys.exit(f"submissions: {e}")
        if not payload:
            sys.exit("submissions: nothing to resubmit (empty selection)")

        # Lock BEFORE the overlap scan, not after: the scan and the
        # submit must be one atomic operation against the ledger, same
        # as every mutating tick (_acquire_lock: "both passing the
        # drain gate before either closes a row = double submit").
        # Scanning outside the lock would let a concurrent `submissions
        # run` reserve a window AFTER this scan sees it clear and
        # BEFORE this submits into it — the crash-window race the
        # ledger reservation protocol exists to close.
        if not args.dry_run:
            _acquire_lock(db)

        blocking = _rows_blocking_indices(db, row['tarball'], payload)
        if blocking:
            sys.exit(
                f"submissions: refusing — row {blocking['id']} "
                f"(state={blocking['state']}) already covers part of this "
                f"selection for {row['tarball']}. Deterministic payloads "
                f"mean re-sending live work duplicates physics. Check "
                f"jobsub_q, then `submissions reconcile {blocking['id']}` "
                f"if the window is genuinely free.")

        # --indices/--indices-file only: --files is file-keyed and has
        # no index space to bound. Nothing checks that a requested
        # index actually belongs to `row` — a typo (e.g. row 4231's
        # indices meant, cursor 5000 typed) sails through
        # _rows_blocking_indices above, since nothing else covers it
        # YET. If a child lands there and closes before the campaign's
        # own cursor reaches it, _slice_overlaps_ledger permanently
        # blocks the tick on that row with no CLI escape (reconcile_row
        # refuses anything not 'failed'/'submitting' — it would need
        # hand-edited sqlite). The cursor is the correct bound: below it
        # is legitimate recovery of work already submitted; at or above
        # it is ground the campaign hasn't reached yet and will submit
        # itself.
        if args.files is None:
            # _live_campaign_cursor returns the ABSOLUTE bound
            # (firstjob + cursor) — comparable directly against
            # `payload`, which is absolute cnf indices (same space as
            # row['indices']).
            abs_cursor = _live_campaign_cursor(db, row['tarball'])
            if abs_cursor is None:
                # No active/paused campaign owns this tarball's index
                # space right now — there is no cursor to bound against,
                # so nothing is refused here.
                pass
            else:
                over = sorted(i for i in payload if i >= abs_cursor)
                if over:
                    sys.exit(
                        f"submissions: refusing — index(es) {over} are at "
                        f"or above the live campaign's cursor "
                        f"({abs_cursor}) for {row['tarball']}. That ground "
                        f"has not been submitted yet; a future top-up "
                        f"slice will cover it itself, and a hand-fired "
                        f"child there can permanently block the tick with "
                        f"no CLI escape. If you meant to recover work the "
                        f"campaign has ALREADY submitted, use an index "
                        f"below {abs_cursor}.")

        fn = resubmit_files if args.files is not None else resubmit
        # _guarded_submit (inside fn) returns True for ANY non-raising
        # call — including submit_entry returning {'status': 'failed'}
        # when jobsub_submit exits non-zero (possibly after already
        # creating a cluster) or exits 0 with no parseable cluster id.
        # `ok` alone can't distinguish that from a real submission, so
        # confirm by evidence: a NEW active child row parented on this
        # one. No such row means nothing was confirmed submitted — exit
        # nonzero rather than let a caller's `&& next-step.sh` run on a
        # false success. Skipped on --dry-run: submit_entry returns
        # before touching the ledger, so there's nothing to find and
        # nothing was promised.
        before = {r['id'] for r in submission_ledger.open_rows(db)
                 if r['parent_id'] == row['id']}
        ok = fn(row, payload, db, dry_run=args.dry_run,
               origin=f"operator resubmit of row {row['id']}")
        if not ok:
            sys.exit(f"submissions: resubmit of row {args.row_id} FAILED")
        if args.dry_run:
            return
        new_children = [r for r in submission_ledger.open_rows(db)
                        if r['parent_id'] == row['id']
                        and r['id'] not in before]
        if not new_children:
            sys.exit(
                f"submissions: resubmit of row {args.row_id} did NOT "
                f"confirm a submission — no new active child ledger row "
                f"appeared. jobsub_submit likely failed (possibly after "
                f"creating a cluster); nothing is confirmed re-fired. "
                f"Check jobsub_q, look for a 'failed' child row on "
                f"row {args.row_id}, and `submissions reconcile <id>` "
                f"it before retrying.")
        print(f"row {args.row_id}: resubmitted -> child row "
              f"{new_children[0]['id']}")
        return

    if verb in ('pause', 'resume', 'cancel', 'complete'):
        _acquire_lock(db)
        try:
            manage_campaign(db, args.camp_id, verb,
                            note=getattr(args, 'note', None),
                            close_rows=getattr(args, 'close_rows', False))
        except ValueError as e:
            sys.exit(f"submissions: {e}")
        return

    # verb == 'run'
    if not args.dry_run:
        _acquire_lock(db)
    _run_pass(args)


if __name__ == '__main__':
    main()
