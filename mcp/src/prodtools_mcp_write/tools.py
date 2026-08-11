"""Write tool implementations for the prodtools-write MCP server.

Exposes `push_cnf` and `run_submissions`. Campaign creation is
`push_cnf` (one call, mirroring `json2jobdef --prod --enqueue`) --
there is no separate `enqueue_campaign` tool; that would have paired
with a map file to enqueue, and no map file exists anywhere in this
codebase.

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
from pathlib import Path
from typing import Optional

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


def _both_streams(result):
    """Failure text for a run_cli result: stdout AND stderr, labelled.

    `stderr or stdout` looked reasonable and was actively harmful: a
    prodtools CLI dying inside pushOutput puts a Python traceback on
    stderr and the ACTUAL diagnosis on stdout (`gfal-copy error: 1 ...
    DESTINATION MAKE_PARENT HTTP 403 : Permission refused`, hundreds of
    lines of pushOutput log). The traceback wins the `or`, so the
    caller is told `pushOutput ... returned non-zero exit status 2` and
    nothing about why -- the one thing they need. Both streams are
    reported, each named, so which one carried the answer is never a
    guess.
    """
    parts = [f"--- {name} ---\n{text}"
             for name, text in (('stdout', result.get('stdout')),
                                ('stderr', result.get('stderr')))
             if (text or '').strip()]
    return '\n'.join(parts) if parts else '(no output on either stream)'


def _tarball_matches(tarball, tarball_desc, dsconf):
    if not tarball:
        return False
    try:
        name = Mu2eName.parse(tarball)
    except ValueError:
        return False
    return name.description == tarball_desc and name.dsconf == dsconf


# What an operator has to know after ANY failure on the enqueue path.
# `enqueue_entry` runs after `_pushout_to_sam` (utils/json2jobdef.py),
# so every refusal downstream of the push -- check_inputs not ready,
# njobs<1, a duplicate live campaign -- leaves the cnf REGISTERED and no
# campaign created. Re-running pushes it again, and there is no
# `submissions enqueue` verb to finish the job from where it stopped.
# This sentence is the whole recovery procedure, so it rides on every
# post-run_cli raise rather than living in a runbook nobody opens.
_ENQUEUE_RECOVERY = (
    "the cnf may already be REGISTERED in SAM while no campaign was "
    "created -- check list_campaigns (and SAM) before re-running, "
    "because a re-run pushes the cnf again")


def push_cnf(json: str, desc: str, dsconf: str, slice_size: int,
             run_as: str, confirm: bool = False):
    """Build a cnf tarball, register it in SAM, and create its campaign
    -- one call, mirroring `json2jobdef --prod --enqueue`.

    run_as="self" registers under your own dataset owner and scratch
    outstage. run_as="mu2epro" registers in PRODUCTION SAM and is not
    reversible; it requires confirm=true.

    No map file is involved anywhere. Returns `campaign_id`, ready to
    hand to `run_submissions`.

    `slice_size` is how many jobs each `submissions run` tick feeds to
    the grid; it is frozen into the campaign at creation. The CLI's own
    default is 1000, which is the right answer unless you have a reason.

    The Musing (simjob_setup) is never taken as an argument: it is
    derived from the `--json` config's own entry for desc+dsconf, so a
    caller-passed tag can never silently disagree with it.
    """
    runner.require_confirmed(run_as, confirm)

    # Checked HERE, not left to create_campaign: that runs inside the
    # CLI, after the irreversible SAM push, so a bad slice_size would
    # cost a pushed cnf with no campaign (see _ENQUEUE_RECOVERY).
    if not isinstance(slice_size, int) or isinstance(slice_size, bool):
        raise ValueError(f"slice_size must be an int, got {slice_size!r}")
    if slice_size < 1:
        raise ValueError(f"slice_size must be >= 1, got {slice_size}")

    simjob_setup, tarball_desc = _select_push_params(json, desc, dsconf)

    db = _ledger_path_for(run_as)
    try:
        before_ids = {c['id'] for c in _all_campaigns(db)}
    except RuntimeError:
        # A first-ever push creates the directory and the DB as it
        # enqueues, so an unreadable ledger here is expected. Record
        # UNKNOWN, not empty: this also catches a transient "database is
        # locked" from a concurrent tick, and claiming an empty snapshot
        # there would make a pre-existing campaign look freshly created.
        # None means "require exactly one live match" downstream --
        # strictly safer than a snapshot we do not actually have.
        before_ids = None

    argv = ['bin/json2jobdef', '--json', json, '--desc', desc,
            '--dsconf', dsconf, '--prod', '--enqueue',
            '--slice-size', str(slice_size)]
    result = runner.run_cli(argv, run_as, simjob_setup=simjob_setup)
    if result['rc'] != 0:
        raise RuntimeError(
            f"json2jobdef --prod --enqueue failed (rc={result['rc']}): "
            f"{_both_streams(result)} -- {_ENQUEUE_RECOVERY}")

    matches = [c for c in _all_campaigns(db)
               if c['state'] in _LIVE_CAMPAIGN_STATES
               and _tarball_matches(c['tarball'], tarball_desc, dsconf)]
    campaign = _sole_live_campaign(
        db, matches, f"desc={desc!r} dsconf={dsconf!r}",
        'json2jobdef --prod --enqueue', before_ids)
    entry = campaign.get('entry') or {}
    return {
        'tarball': campaign['tarball'],
        'datasets': [o.get('dataset') for o in entry.get('outputs', [])],
        'campaign_id': campaign['id'],
        'njobs': entry.get('njobs'),
    }


def _sole_live_campaign(db, matches, subject, what, before_ids=None):
    """The one live campaign `what` just created, or raise.

    Called by `push_cnf`, which matches on desc+dsconf -- that does NOT
    imply a unique tarball: `_tarball_matches` ignores the version index,
    and `cnf_name` puts `config['version']` there (`--extend` bumps it),
    so live campaigns for `...D.C.0.tar` and `...D.C.1.tar` both match
    and are both legal. `before_ids` (the snapshot taken before run_cli)
    is what disambiguates them, and it carries the whole burden on this
    path -- EXCEPT on a first-ever push, where the ledger doesn't exist
    yet to snapshot, and `before_ids` arrives as `None`; there the
    ledger's own campaigns_live_tarball uniqueness index (one live
    campaign per tarball) is the only guarantee available, so a second
    match means that DB invariant is broken rather than that this call
    created two campaigns.

    Never falls back to "the newest one", and never to "the only one" on
    the snapshot path: a campaign that already existed is by definition
    not the one this call created. Handing its id back would send
    `run_submissions` at an unrelated production campaign while the new
    one is never fed.
    """
    if not matches:
        raise RuntimeError(
            f"{what} reported success but no live campaign for "
            f"{subject} is in {db} -- {_ENQUEUE_RECOVERY}")

    if before_ids is None:
        if len(matches) == 1:
            return matches[0]
        ids = ', '.join(f"{c['id']} ({c['state']}, {c['tarball']})"
                        for c in matches)
        raise RuntimeError(
            f"{db} holds {len(matches)} live campaigns for {subject} "
            f"({ids}) -- the ledger's own uniqueness invariant is "
            f"broken; resolve by hand rather than guessing which one "
            f"{what} created")

    fresh = [c for c in matches if c['id'] not in before_ids]
    if len(fresh) == 1:
        return fresh[0]

    ids = ', '.join(f"{c['id']} ({c['state']}, {c['tarball']})"
                    for c in matches)
    if not fresh:
        raise RuntimeError(
            f"{what} reported success, but every live campaign matching "
            f"{subject} in {db} ({ids}) already existed before it ran, "
            f"so none of them is this call's -- {_ENQUEUE_RECOVERY}")
    fresh_ids = ', '.join(str(c['id']) for c in fresh)
    raise RuntimeError(
        f"{what} appears to have created {len(fresh)} campaigns matching "
        f"{subject} in {db} (new: {fresh_ids}; all live matches: {ids}) "
        f"-- refusing to guess which one to return; resolve by hand")


def _ledger_path_for(run_as):
    """Which ledger this identity writes.

    For run_as='mu2epro' this IS the production ledger (see
    submission_ledger.ledger_for) -- defaulting to THIS process's own
    user here would look up the CALLER's personal ledger while
    `json2jobdef --prod --enqueue` actually wrote the campaign row into
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


def run_submissions(campaign_id: int, run_as: str, confirm: bool = False):
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
            f"{_both_streams(result)}")
    return {'rc': result['rc'],
            'needs_attention': result['rc'] == 2,
            'campaign_id': campaign_id,
            'output': result['stdout']}
