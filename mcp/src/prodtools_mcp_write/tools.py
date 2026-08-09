"""Write tool implementations for the prodtools-write MCP server.

Signatures are fixed here; real bodies land in Tasks 8 (push_cnf) and
9 (enqueue_campaign, run_submissions).

Thin by design: validate, delegate to runner, read the result back
from the artifact the CLI wrote -- never from its stdout.

Entry selection is delegated to `utils.json2jobdef.load_json` /
`find_json_entry` -- the SAME functions the real `bin/json2jobdef`
CLI uses -- rather than re-implemented here. A parallel scan over raw
JSON previously matched the literal `desc` key, but `desc` is often
absent in the raw config and auto-derived during expansion (mixing
entries append `pbeam`; every stage can omit it), so the two
implementations inevitably drifted: mixing could never be pushed. This
module now cannot drift from what json2jobdef actually does, by
construction.

`utils.json2jobdef` (transitively `utils.samweb_wrapper`) needs
`samweb_client`, which is not on a bare interpreter's path -- but this
module is never imported except after `samweb_client` is already
either genuinely on PYTHONPATH (the real write-MCP server's launcher,
`mcp/scripts/_mcp_env.sh`, sources the Mu2e ops environment before
starting Python) or stubbed into `sys.modules` (this repo's own
`test/test_unit.py`, before it imports anything under `utils/`).
Nothing imported here performs any I/O (SAM query, subprocess, network)
at import time or during entry selection -- only at model
instantiation, which this module never does.
"""
import json as _json
from collections import Counter
from pathlib import Path

from prodtools_mcp_write import runner
from utils.config_utils import get_tarball_desc
from utils.job_common import Mu2eName
from utils.json2jobdef import load_json, find_json_entry


def _select_push_params(json_path, desc, dsconf):
    """Read `json_path` and return `(simjob_setup, tarball_desc)` for
    the entry matching `desc` + `dsconf`, using json2jobdef's own
    loader and selector so this can never disagree with what a real
    push does.

    `tarball_desc` is `get_tarball_desc(entry) or desc` -- the
    tarball's actual description field, which includes
    `tarball_append` when the entry has one (e.g. `CosmicCRYExtracted`
    -> `CosmicCRYExtracted-reco`). json2jobdef writes
    `cnf.<owner>.<tarball_desc>.<dsconf>.N.tar`; matching a map entry
    on the bare `desc` instead would silently pick a DIFFERENT stage's
    entry sharing the same desc+dsconf (e.g. digi vs -reco) whenever
    one happened to exist, which is exactly the wrong-but-plausible
    failure this project exists to avoid.

    Refuses rather than guessing: a caller-passed Musing tag could
    disagree with the entry and silently build a cnf against the wrong
    release, which is worse than not building one. This mirrors the
    refusal behaviour of `.claude/commands/mu2e-run.md`, which derives
    a command's Musing from the same `simjob_setup` field rather than
    accept one as an argument.
    """
    path = Path(json_path)
    if not path.is_file():
        raise ValueError(f"push_cnf: --json config not found: {json_path!r}")
    try:
        configs = load_json(path)
    except _json.JSONDecodeError as e:
        raise ValueError(f"push_cnf: {json_path!r} is not valid JSON: {e}")

    # find_json_entry sys.exit()s on 0 or >1 matches -- fine for a CLI,
    # fatal for a long-running server process. Convert to ValueError
    # instead of letting SystemExit propagate and kill the server.
    try:
        entry = find_json_entry(configs, desc, dsconf, None)
    except SystemExit as e:
        raise ValueError(f"push_cnf: {e}") from e

    simjob_setup = entry.get('simjob_setup')
    if not simjob_setup:
        raise ValueError(
            f"push_cnf: entry matching desc={desc!r} dsconf={dsconf!r} in "
            f"{json_path!r} has no simjob_setup field")

    tarball_desc = get_tarball_desc(entry) or desc
    return simjob_setup, tarball_desc


def _read_entries(map_path):
    """Tolerant read of a jobdefs map: missing file or blank content is
    an empty map, mirroring `_write_jobdef_json_entry`'s own tolerance
    (it starts fresh rather than erroring on either). Used only for the
    pre-push snapshot -- a map that fails to parse strictly AFTER a
    successful push is a real problem and must still raise loudly."""
    path = Path(map_path)
    if not path.is_file():
        return []
    text = path.read_text()
    if not text.strip():
        return []
    try:
        entries = _json.loads(text)
    except _json.JSONDecodeError:
        return []
    return entries if isinstance(entries, list) else [entries]


def _canon(entry):
    return _json.dumps(entry, sort_keys=True)


def _read_entries_strict(map_path):
    """Same shape as `_read_entries`, but a missing file or malformed
    JSON is a real problem here, not "0 entries".

    Used only by `_read_map_entry`'s `index=` mode: by the time
    `enqueue_campaign` reaches it, `submit_map --entry N` has already
    read this same map successfully to find the entry it enqueued, so
    a map that turns out missing or corrupt when read back is worth a
    distinct error -- not silently folded into "entry N out of range
    for 0 entries", the same misleading-message shape flagged for
    `_read_entries` itself (see its own docstring)."""
    path = Path(map_path)
    if not path.is_file():
        raise RuntimeError(f"map file not found: {map_path}")
    text = path.read_text()
    if not text.strip():
        raise RuntimeError(f"map file is empty: {map_path}")
    try:
        entries = _json.loads(text)
    except _json.JSONDecodeError as e:
        raise RuntimeError(f"{map_path} is not valid JSON: {e}")
    return entries if isinstance(entries, list) else [entries]


def _tarball_matches(tarball, tarball_desc, dsconf):
    if not tarball:
        return False
    try:
        name = Mu2eName.parse(tarball)
    except ValueError:
        return False
    return name.description == tarball_desc and name.dsconf == dsconf


def _read_map_entry(map_path, tarball_desc=None, dsconf=None,
                    before_entries=None, index=None):
    """Return (index, entry) for the entry THIS push actually produced.

    `index`, when given, bypasses all of the disambiguation below:
    submit_map's own `--entry N` already names an EXISTING map entry
    unambiguously (it neither appends nor guesses at that entry), so
    enqueue_campaign uses this mode to read it back by plain position
    rather than by desc+dsconf disambiguation -- there is nothing to
    disambiguate when the index was already known before the command
    ran. `index` and `tarball_desc`/`dsconf` are mutually exclusive:
    passing both would otherwise silently take the index path and
    ignore the desc/dsconf disambiguation without saying so, which is
    exactly the kind of accidental weak-path selection this project
    exists to prevent -- so combining them raises instead.

    Without `index` (push_cnf's use): selected by parsing tarball_desc+dsconf
    out of each candidate
    entry's own tarball name (`Mu2eName`), never by an owner-qualified
    tarball name computed in this process (`owner` defaults to `$USER`
    when a JSON entry omits it, and this process's `$USER` -- the
    caller -- is not the identity json2jobdef actually ran as inside
    the ksu block, `mu2epro` -> `mu2e`; computing the tarball name here
    would report a successful production push as a failure), and never
    by position (`entries[-1]` can silently return some OTHER
    campaign's tarball and index as if it were this push's result).

    A tarball_desc+dsconf pair can legitimately match MORE than one map
    entry: `_write_jobdef_json_entry` dedupes on (tarball, firstjob),
    not tarball alone, so a windowed campaign appends one entry per
    index window sharing the same tarball; and the same tarball_desc
    can coincidentally already exist in the map from an unrelated
    prior push. `before_entries` (a snapshot taken before `run_cli`
    ran) disambiguates: the entry this push produced is whichever
    candidate is newly present in the map that was not there before.
    If nothing was newly appended -- `_write_jobdef_json_entry`'s
    documented dedup/re-run path, which returns without appending when
    the entry already exists -- and exactly one candidate matches, that
    one is unambiguously it. Only a genuine tie (more than one
    candidate, none of them distinguishably new) raises, and the
    message lists every candidate so an operator can resolve it by
    hand -- there is no "last entry" fallback.
    """
    if index is not None:
        if tarball_desc is not None or dsconf is not None:
            raise ValueError(
                "_read_map_entry: index and tarball_desc/dsconf are "
                "mutually exclusive -- pass one or the other, not both")
        entries = _read_entries_strict(map_path)
        if not (0 <= index < len(entries)):
            raise RuntimeError(
                f"entry {index} out of range for {map_path} "
                f"({len(entries)} entries)")
        return index, entries[index]

    after_entries = _read_entries(map_path)
    candidates = [(i, e) for i, e in enumerate(after_entries)
                  if _tarball_matches(e.get('tarball'), tarball_desc, dsconf)]
    if not candidates:
        raise RuntimeError(
            f"no entry for tarball_desc={tarball_desc!r} dsconf={dsconf!r} "
            f"found in {map_path} after the push")

    before_counts = Counter(_canon(e) for e in before_entries)
    seen = Counter()
    newly_appended = []
    for i, e in candidates:
        c = _canon(e)
        seen[c] += 1
        if seen[c] > before_counts.get(c, 0):
            newly_appended.append((i, e))

    if len(newly_appended) == 1:
        return newly_appended[0]
    if not newly_appended and len(candidates) == 1:
        return candidates[0]

    listing = ", ".join(f"index {i} tarball {e.get('tarball')!r}"
                        for i, e in candidates)
    raise RuntimeError(
        f"tarball_desc={tarball_desc!r} dsconf={dsconf!r} matches "
        f"{len(candidates)} entries in {map_path} and this push's own "
        f"entry cannot be told apart from pre-existing ones; "
        f"candidates: {listing}")


def push_cnf(json, desc, dsconf, jobdefs_map, run_as, confirm=False):
    """Build a cnf tarball and register it.

    run_as="self" registers under your own dataset owner and scratch
    outstage. run_as="mu2epro" registers in PRODUCTION SAM and is not
    reversible; it requires confirm=true.

    jobdefs_map must be an absolute path you can write:
    production_manager/direct_maps/ is mu2epro-owned, and this tool
    never invents a path. It must be absolute because, under
    run_as="mu2epro", the ksu block cd's into its own mktemp workdir --
    a relative path would be written there while the result is read
    back relative to this server's own cwd.

    The Musing (simjob_setup) is never taken as an argument: it is
    derived from the `--json` config's own entry for desc+dsconf, so a
    caller-passed tag can never silently disagree with it.
    """
    runner.require_confirmed(run_as, confirm)

    if not Path(jobdefs_map).is_absolute():
        raise ValueError(
            f"jobdefs_map must be an absolute path (got {jobdefs_map!r})")

    simjob_setup, tarball_desc = _select_push_params(json, desc, dsconf)

    before_entries = _read_entries(jobdefs_map)

    argv = ['bin/json2jobdef', '--json', json, '--desc', desc,
            '--dsconf', dsconf, '--prod', '--jobdefs', jobdefs_map]
    result = runner.run_cli(argv, run_as, simjob_setup=simjob_setup)
    if result['rc'] != 0:
        raise RuntimeError(
            f"json2jobdef failed (rc={result['rc']}): "
            f"{result['stderr'] or result['stdout']}")
    index, entry = _read_map_entry(jobdefs_map, tarball_desc, dsconf,
                                   before_entries)
    return {
        'tarball': entry.get('tarball'),
        'datasets': [o.get('dataset') for o in entry.get('outputs', [])],
        'map_path': jobdefs_map,
        'entry_index': index,
    }


def _ledger_path_for(run_as):
    """Which ledger this identity writes.

    For run_as='mu2epro' this IS the production ledger (see
    submission_ledger.ledger_for) -- defaulting to THIS process's own
    user here would look up the CALLER's personal ledger while
    submit_map --enqueue actually wrote the campaign row into
    production's (inside the ksu block, as mu2epro), silently
    reporting a successful production enqueue as "no campaign found".
    """
    from utils import submission_ledger
    return submission_ledger.ledger_for(
        'mu2epro' if run_as == 'mu2epro' else None)


# A campaign that still owns its index space. Mirrors the states in
# submission_ledger._connect's campaigns_live_tarball unique index --
# the DB guarantee that makes "the live campaign for this tarball"
# a single, unambiguous row.
_LIVE_CAMPAIGN_STATES = ('active', 'paused')


def _all_campaigns(db):
    """`all_campaigns(db)`, with sqlite's bare "unable to open database
    file" turned into a message that names the path.

    A first-time caller has no `/exp/mu2e/data/users/<you>/prodtools/`
    directory at all, and sqlite3 reports that as a context-free
    `OperationalError` several frames below the tool the operator
    actually called — unreadable as an answer to "why did my enqueue
    fail?".

    Never silently treated as "no campaigns": a ledger that cannot be
    opened is not an empty ledger, and reporting it as one would let
    run_submissions raise "no campaign <id>" (a typo) for what is
    actually a missing directory (a setup problem).
    """
    import sqlite3
    from utils import submission_ledger
    try:
        return submission_ledger.all_campaigns(db)
    except sqlite3.OperationalError as e:
        raise RuntimeError(
            f"cannot read the submission ledger at {db}: {e}. Its parent "
            f"directory is created by the submitting identity on its "
            f"first write, so this usually means the command never got "
            f"as far as writing the ledger") from e


def enqueue_campaign(map_path, entry, slice_size, run_as, confirm=False):
    """Register ONE entry of a map as a sliced-submission campaign.

    `entry` is required: a map can hold several entries, and
    `submit_map` itself fans out over every one of them when `--entry`
    is omitted -- a typed tool defaulting to "all of them" is exactly
    the hazard this signature exists to remove.

    `map_path` must be an absolute path for the same reason as
    push_cnf's jobdefs_map: under run_as='mu2epro' the ksu block cd's
    into its own mktemp workdir, so submit_map would read a relative
    path from THERE while this function reads it back relative to the
    server's own cwd.

    The campaign id is never scraped from stdout: it is read back from
    the ledger THIS identity writes (_ledger_path_for), matched by the
    tarball of the map entry submit_map was told to enqueue -- read
    back via `_read_map_entry`'s index mode, since `--entry N` already
    names the entry unambiguously and there is nothing to disambiguate.

    The match is restricted to a LIVE campaign (see _LIVE_CAMPAIGN_STATES)
    rather than taking the last of every campaign that tarball ever had.
    A tarball accumulates campaigns over its life -- complete, cancelled,
    then a new one -- and `match[-1]` silently returns whichever is
    newest in the ledger. The live set is unique by construction: the
    campaigns_live_tarball index (submission_ledger._connect) forbids a
    second active-or-paused campaign per tarball, which is the same
    invariant create_campaign refuses on. Exactly one live match is
    therefore this enqueue's campaign, and anything else is reported
    rather than guessed at.
    """
    runner.require_confirmed(run_as, confirm)

    if not Path(map_path).is_absolute():
        raise ValueError(
            f"map_path must be an absolute path (got {map_path!r})")

    argv = ['bin/submit_map', '--map', map_path, '--entry', str(entry),
            '--enqueue', '--slice-size', str(slice_size)]
    result = runner.run_cli(argv, run_as)
    if result['rc'] != 0:
        raise RuntimeError(
            f"submit_map --enqueue failed (rc={result['rc']}): "
            f"{result['stderr'] or result['stdout']}")

    _, map_entry = _read_map_entry(map_path, index=entry)
    tarball = map_entry.get('tarball')
    db = _ledger_path_for(run_as)
    match = [c for c in _all_campaigns(db)
             if c['tarball'] == tarball
             and c['state'] in _LIVE_CAMPAIGN_STATES]
    if not match:
        raise RuntimeError(
            f"submit_map --enqueue reported success but no live campaign "
            f"for {tarball!r} is in {db} -- reconcile before retrying")
    if len(match) > 1:
        ids = ', '.join(f"{c['id']} ({c['state']})" for c in match)
        raise RuntimeError(
            f"{db} holds {len(match)} live campaigns for {tarball!r} "
            f"({ids}) -- the ledger's own uniqueness invariant is broken; "
            f"resolve by hand rather than guessing which one this "
            f"enqueue created")
    campaign = match[0]
    return {'campaign_id': campaign['id'],
            'njobs': (campaign.get('entry') or {}).get('njobs'),
            'tarball': tarball}


def run_submissions(campaign_id, run_as, confirm=False):
    """Tick `submissions run`, scoped to one campaign's index top-up.

    `--campaign` only narrows the top-up phase: the recovery pass still
    processes every open ledger row, and `drain_tick` still feeds every
    draining campaign, exactly as a bare `submissions run` does. Only
    the slice-feeding for THIS campaign id is what's being scoped here.

    `campaign_id` is required. `submissions run` with no filter ticks
    every active campaign -- that is the cron's job, not an
    interactive call from this tool, so there is no default that means
    "everything".

    The id is validated against the ledger THIS identity writes
    (_ledger_path_for) BEFORE run_cli: a nonexistent or non-active id
    would otherwise filter top_up's campaign list down to empty, and
    an empty-but-successful tick (rc=0, no attention keys) is
    indistinguishable from a real one -- a typo would silently report
    success for a campaign that was never touched. Absent and
    not-active are different operator problems (a typo/wrong ledger vs.
    a campaign that needs `submissions resume` first), so they raise
    with different messages.
    """
    runner.require_confirmed(run_as, confirm)

    db = _ledger_path_for(run_as)
    campaigns = {c['id']: c for c in _all_campaigns(db)}
    campaign = campaigns.get(campaign_id)
    if campaign is None:
        raise ValueError(
            f"no campaign {campaign_id} in {db} -- check the id (and "
            f"that run_as={run_as!r} is looking at the right ledger); "
            f"a typo'd id would otherwise filter top_up's campaign list "
            f"to empty and report a no-op tick as success")
    if campaign['state'] != 'active':
        raise ValueError(
            f"campaign {campaign_id} is {campaign['state']!r}, not "
            f"active, in {db} -- top_up only feeds active campaigns, "
            f"so this id would tick nothing; "
            + (f"`submissions resume {campaign_id}` first"
               if campaign['state'] == 'paused' else
               "nothing left to submit for it"))

    argv = ['bin/submissions', 'run', '--campaign', str(campaign_id)]
    result = runner.run_cli(argv, run_as)
    # rc=2 is the documented "something needs attention" exit -- held
    # rows, exhausted recoveries, a paused campaign. It is a report,
    # not a crash, and must never be raised as an error.
    if result['rc'] not in (0, 2):
        raise RuntimeError(
            f"submissions run failed (rc={result['rc']}): "
            f"{result['stderr'] or result['stdout']}")
    return {'rc': result['rc'],
            'needs_attention': result['rc'] == 2,
            'campaign_id': campaign_id,
            'output': result['stdout']}
