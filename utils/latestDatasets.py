#!/usr/bin/env python3
"""For each unique description (3rd field), pick the dataset with the
latest dsconf (4th field).

Mu2e dataset/definition names are dot-delimited:

    <tier>.<owner>.<description>.<dsconf>[.<sequencer>].<format>

This tool queries `samweb list-definitions` (or reads names from stdin),
groups by description, and prints the lexicographically-greatest dsconf
per group. For Mu2e dsconfs like `MDC2025af_best_v1_3`, lex order tracks
campaign letter then version.

`--latest-by time` orders by SAM definition creation date instead. Use it
when a description's dsconfs come from more than one naming series, where
lex order is meaningless: the ntuple series `MDC2020-001` sorts BELOW the
release series `MDC2020aw_best_v1_3_v06_06_00` ('-' < 'a') even though it
was created six months later.
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.job_common import Mu2eName
from utils.samweb_wrapper import (dataset_file_count, definition_creation_date,
                                  definitions_matching)


_VERBOSE = False


def _vlog(*args):
    """latestDatasets diagnostics to stderr, only when --verbose (muted by default)."""
    if _VERBOSE:
        print(*args, file=sys.stderr)


def parse_name(name):
    """Return (description, dsconf) or None if the name doesn't parse.

    Lenient at this boundary — SAM may return arbitrary strings here.
    """
    try:
        n = Mu2eName.parse(name)
    except ValueError:
        return None
    return n.description, n.dsconf


def fetch_definitions(defname_pattern, user):
    return definitions_matching(defname=defname_pattern, user=user)


def _group_by_description(names, order_key=None):
    """Group dataset names by description (3rd field), each group's members
    sorted ascending. Returns (groups, skipped) where groups maps
    description -> [(dsconf, name), ...] and skipped holds unparseable names.

    order_key: optional callable name -> sortable, deciding which member of a
    group counts as latest. Default (None) orders by dsconf lexicographically,
    which tracks campaign letter then version WITHIN a single naming series.
    Pass a key when a description spans series, where lex order is meaningless
    -- see _creation_date_key."""
    groups = defaultdict(list)
    skipped = []
    for name in names:
        parsed = parse_name(name)
        if parsed is None:
            skipped.append(name)
            continue
        description, dsconf = parsed
        groups[description].append((dsconf, name))
    if order_key is None:
        rank = lambda item: item[0]             # item = (dsconf, name)
    else:
        rank = lambda item: order_key(item[1])
    for items in groups.values():
        items.sort(key=rank)
    return groups, skipped


def latest_per_description(names, order_key=None):
    """Return list of (description, latest_dsconf, latest_name, count).

    order_key decides which member of each group wins -- see
    _group_by_description. Row order is always by description, independent of
    the key: selection changes, presentation stays stable."""
    groups, skipped = _group_by_description(names, order_key)
    rows = []
    for description, items in groups.items():
        latest_dsconf, latest_name = items[-1]
        rows.append((description, latest_dsconf, latest_name, len(items)))
    rows.sort(key=lambda r: r[0])
    return rows, skipped


def superseded_per_description(names, order_key=None):
    """Inverse of latest_per_description: every group member EXCEPT the latest,
    i.e. datasets replaced by a newer sibling of the same description.
    Returns (rows, skipped) with rows = (description, dsconf, name, count)
    sorted by (description, dsconf); count is the total number of versions in
    that description's group. Descriptions with a single version contribute
    nothing (they have no replacement).

    MUST be given the same order_key as latest_per_description -- the two are
    set complements, so differing keys would put a dataset in both listings or
    in neither."""
    groups, skipped = _group_by_description(names, order_key)
    rows = []
    for description, items in groups.items():
        for dsconf, name in items[:-1]:      # all but the latest = superseded
            rows.append((description, dsconf, name, len(items)))
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows, skipped


def _creation_date_key(names):
    """Build an order_key ranking datasets by SAM definition creation date.

    Only CONTENDED descriptions (2+ versions) are queried: a single-version
    group has nothing to compare against, so its date is never needed. On a
    ~20-desc --emit run where most descs have one version, that is ~2 SAM
    calls instead of ~20.

    Fails loudly if SAM has no date for a contended dataset. Quietly reverting
    to dsconf order would answer a --latest-by time question with a
    lexicographic result -- the exact bug this mode exists to fix, made
    invisible. definition_creation_date is fail-soft and returns None on a SAM
    error, so an outage lands here too and surfaces as a loud failure."""
    by_desc = defaultdict(list)
    for name in names:
        parsed = parse_name(name)
        if parsed is not None:
            by_desc[parsed[0]].append(name)
    contended = [n for group in by_desc.values() if len(group) > 1 for n in group]
    if contended:
        # Status to stderr (stdout stays machine-readable): one SAM round trip
        # per dataset is the slow part, so signal it even when muted.
        print(f"Querying creation dates for {len(contended)} dataset(s), "
              f"please wait...", file=sys.stderr)
    dates = {}
    for name in contended:
        dates[name] = definition_creation_date(name)
        _vlog(f"# created {dates[name]}: {name}")
    undated = sorted(n for n, d in dates.items() if d is None)
    if undated:
        sys.exit("latestDatasets: --latest-by time: SAM has no creation date "
                 "for:\n" + "\n".join(f"  {n}" for n in undated))
    # Uncontended names were never queried, so they are absent from `dates`.
    # Their rank is never consulted (they are alone in their group), but a bare
    # dates[name] would raise KeyError.
    return lambda name: dates.get(name, datetime.min)


def _order_key_for(latest_by, names):
    """Resolve the --latest-by choice to an order_key for
    _group_by_description. 'dsconf' -> None: lexicographic, zero SAM calls."""
    return _creation_date_key(names) if latest_by == "time" else None


def _narrow_to_latest_release(names):
    """From datasets spanning several releases of a family, keep only those of
    the single latest release (max campaign tag). The family wildcard discovers
    every release (MDC2025aa..MDC2025ap); the chain processes one."""
    rel = {}
    for n in names:
        try:
            rel[n] = Mu2eName.parse(n).campaign
        except ValueError:
            pass
    if not rel:
        return names
    latest = max(rel.values())  # campaign tags sort lexicographically
    return [n for n in names if rel.get(n) == latest]


def _filter_complete(names):
    """Keep only datasets that are complete (file count == producing cnf njobs).
    Datasets whose cnf has no inherent job count (open-ended generators) are kept
    with a warning — never silently dropped; only provably-incomplete ones go.
    Shared by lister mode and --emit. Diagnostics go to stderr."""
    from utils import jobdef_lookup
    jobdef_lookup.set_verbose(_VERBOSE)
    # Status to stderr (stdout stays machine-readable): scanning cnfs is the slow
    # part, so signal it even when diagnostics are muted.
    print(f"Checking completeness of {len(names)} datasets, please wait...",
          file=sys.stderr)
    njobs_maps = {}          # dsconf -> {(desc, tier): njobs}, built once per dsconf
    kept = []
    for ds in names:
        try:
            n = Mu2eName.parse(ds)
        except ValueError:
            _vlog(f"# unparseable (included): {ds}")
            kept.append(ds)
            continue
        if n.dsconf not in njobs_maps:
            njobs_maps[n.dsconf] = jobdef_lookup.output_njobs_map(n.dsconf)
        njobs = njobs_maps[n.dsconf].get((n.description, n.tier))
        if not njobs or njobs <= 0:
            _vlog(f"# completeness indeterminate (included): {ds} (cnf has no job count)")
            kept.append(ds)
            continue
        try:
            if dataset_file_count(ds) == njobs:
                kept.append(ds)
            else:
                _vlog(f"# incomplete (skipped): {ds}")
        except Exception as e:
            _vlog(f"# completeness indeterminate (included): {ds}: {e}")
            kept.append(ds)
    return kept


def _dataset_exists(name):
    """True if the SAM dataset has at least one file."""
    try:
        return dataset_file_count(name) > 0
    except Exception:
        return False


def _filter_unproduced(inputs, template, out_campaign=None, defer_desc=False,
                       dsconf=None):
    """Drop inputs whose this-stage output already exists in SAM (the chain has
    already produced them). Output dataset names come from the synthesized entry.

    out_campaign/defer_desc/dsconf MUST match what emit_config uses, so the
    computed output name is the actual target build (e.g. the ar reco output, not
    the input's ap-build output). Diagnostics to stderr."""
    from utils import chain_emit
    print(f"Checking produced outputs for {len(inputs)} datasets, please wait...",
          file=sys.stderr)
    kept = []
    for ds in inputs:
        entry = chain_emit.synthesize_entry(template, ds, out_campaign=out_campaign,
                                            defer_desc=defer_desc, dsconf=dsconf)
        produced = [o for o in chain_emit.output_datasets(entry) if _dataset_exists(o)]
        if produced:
            _vlog(f"# already produced (skipped): {ds} -> {','.join(produced)}")
        else:
            kept.append(ds)
    return kept


def _emit(args):
    """--emit: synthesize a json2jobdef config (JSON to stdout) for the next
    stage from the latest inputs of a campaign, via its per-campaign template.
    All diagnostics go to stderr so stdout stays valid JSON."""
    import json
    from utils import chain_emit

    if not args.campaign:
        sys.exit("--emit requires --campaign")

    # Family-wide stages discover inputs by latest build PER DESC across the
    # whole family, regardless of which release they were produced at, and write
    # a separately-tagged build. --campaign is the OUTPUT build.
    #   digi, mix ← dts primaries (the small primary set)
    #   reco      ← dig: take the latest dig of every desc anywhere in the family
    #               (Mix1BB, OnSpill, all streams) and reco them into the output
    #               build. Input build version varies per desc; the input pattern
    #               must not pin it.
    # ntuple stays pinned: it consumes mcs at a specific reco build and uses
    # {parent_dsconf} to carry that build through.
    FAMILY_WIDE = {'digi', 'mix', 'reco'}
    family_wide = (args.emit in FAMILY_WIDE)
    is_mix = (args.emit == 'mix')
    out_campaign = args.campaign

    try:
        template = chain_emit.load_template(args.campaign, args.emit, args.templates_dir)
        defname = args.defname or chain_emit.derive_input_defname(
            template, args.campaign, family_wide=family_wide)
    except (FileNotFoundError, ValueError) as e:
        sys.exit(str(e))
    _vlog(f"# discovering inputs: {defname}")

    names = fetch_definitions(defname, args.user)
    if not family_wide:
        names = _narrow_to_latest_release(names)
    rows, skipped = latest_per_description(names,
                                           _order_key_for(args.latest_by, names))
    latest = [latest_name for _, _, latest_name, _ in rows]

    # If the template names explicit descs (and has no {desc} wildcard), restrict
    # discovery to exactly those — don't propose primaries the template omits.
    # No desc field at all → discover everything (unchanged default).
    wanted = set(chain_emit.explicit_descriptions(template))
    if wanted and not chain_emit.has_wildcard(template):
        kept, dropped = [], []
        for ds in latest:
            (kept if Mu2eName.parse(ds).description in wanted else dropped).append(ds)
        for ds in dropped:
            _vlog(f"# not in template desc list (skipped): {ds}")
        missing = wanted - {Mu2eName.parse(ds).description for ds in kept}
        for d in sorted(missing):
            _vlog(f"# template desc not found in SAM: {d}")
        latest = kept

    if args.complete_only:
        latest = _filter_complete(latest)
    if args.skip_produced:
        latest = _filter_unproduced(latest, template, out_campaign=out_campaign,
                                    defer_desc=is_mix, dsconf=args.dsconf)

    config = chain_emit.emit_config(template, latest, out_campaign=out_campaign,
                                    defer_desc=is_mix, dsconf=args.dsconf)
    print(json.dumps(config, indent=2))
    if skipped:
        _vlog(f"# skipped {len(skipped)} unparseable name(s)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--defname", help="SAM defname pattern, e.g. 'dig.mu2e.%%.MDC2025%%.art'")
    ap.add_argument("--user", help="filter samweb list-definitions by --user")
    ap.add_argument("--stdin", action="store_true",
                    help="read definition names from stdin instead of querying samweb")
    ap.add_argument("--show-count", action="store_true",
                    help="include a column with how many dsconfs were collapsed per description")
    ap.add_argument("--superseded", action="store_true",
                    help="list mode: print the datasets REPLACED by a newer dsconf "
                         "(every non-latest version per description) instead of the "
                         "latest -- the inverse of the default output. Honors "
                         "--show-count (group version count) and --complete-only.")
    ap.add_argument("--latest-by", choices=("dsconf", "time"), default="dsconf",
                    help="how to pick the latest dataset per description: "
                         "'dsconf' (default) sorts dsconf lexicographically -- "
                         "correct within one naming series, and free of SAM "
                         "queries; 'time' sorts by SAM definition creation "
                         "date -- use it when a description spans naming "
                         "series, where lex order is meaningless (the ntuple "
                         "series MDC2020-001 sorts BELOW "
                         "MDC2020aw_best_v1_3_v06_06_00 because '-' < 'a', yet "
                         "was created six months later)")
    ap.add_argument("--emit", choices=("digi", "reco", "ntuple", "mix"),
                    help="synthesize a json2jobdef config for this stage, one entry "
                         "per latest input dataset (POMS-free chain hop)")
    ap.add_argument("--campaign",
                    help="campaign tag. With --emit: selects the per-campaign "
                         "template. Without --emit: lists that campaign's "
                         "primaries (dts.mu2e.%%.<campaign>.art).")
    ap.add_argument("--templates-dir",
                    default=os.path.join(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "templates"),
                    help="root of per-campaign stage templates (default: <repo>/templates)")
    ap.add_argument("--dsconf",
                    help="override the template's output dsconf for --emit (e.g. "
                         "MDC2025ar_best_v1_3). Pins the exact build so the emitted "
                         "config and --skip-produced check the same build, instead "
                         "of whatever version the template bakes.")
    ap.add_argument("--complete-only", action="store_true",
                    help="keep only datasets whose file count == producing cnf njobs "
                         "(works in list and --emit modes); inputs whose cnf has no "
                         "job count (open-ended generators) are kept with a warning, "
                         "never silently dropped")
    ap.add_argument("--skip-produced", action="store_true",
                    help="drop inputs whose this-stage output already exists in SAM "
                         "(with --emit: the emitted stage; in list mode: digi)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="show diagnostics (cnf/dataset resolution, completeness "
                         "decisions); muted by default")
    args = ap.parse_args()
    global _VERBOSE
    _VERBOSE = args.verbose

    if args.superseded and (args.emit or args.skip_produced):
        ap.error("--superseded cannot be combined with --emit or --skip-produced")

    if args.emit:
        _emit(args)
        return

    # Lister mode. Source precedence: stdin, explicit defname/user, or
    # --campaign (lists that campaign's primaries: dts.mu2e.%.<campaign>.art).
    if args.stdin:
        names = [line.strip() for line in sys.stdin if line.strip()]
    elif args.defname or args.user:
        names = fetch_definitions(args.defname, args.user)
    elif args.campaign:
        # Trailing % so a family tag (MDC2025) matches its releases (MDC2025ap).
        names = fetch_definitions(f"dts.mu2e.%.{args.campaign}%.art", args.user)
        names = _narrow_to_latest_release(names)
    else:
        ap.error("provide --defname/--user, --campaign, or --stdin")

    if args.superseded:
        srows, sk2 = superseded_per_description(
            names, _order_key_for(args.latest_by, names))
        if args.complete_only:
            complete = set(_filter_complete([r[2] for r in srows]))
            srows = [r for r in srows if r[2] in complete]
        for _, _, name, count in srows:
            print(f"{count:3d}  {name}" if args.show_count else name)
        if sk2:
            _vlog(f"# skipped {len(sk2)} name(s) with <5 dotted fields")
        return

    rows, skipped = latest_per_description(names,
                                           _order_key_for(args.latest_by, names))

    if args.complete_only:
        complete = set(_filter_complete([r[2] for r in rows]))
        rows = [r for r in rows if r[2] in complete]

    if args.skip_produced:
        if not args.campaign:
            ap.error("--skip-produced (list mode) requires --campaign")
        from utils import chain_emit
        digi_tmpl = chain_emit.load_template(args.campaign, "digi", args.templates_dir)
        unproduced = set(_filter_unproduced([r[2] for r in rows], digi_tmpl))
        rows = [r for r in rows if r[2] in unproduced]

    # Bare name output unless --show-count adds the count column
    show_count = args.show_count
    for _, _, latest_name, count in rows:
        print(f"{count:3d}  {latest_name}" if show_count else latest_name)

    if skipped:
        _vlog(f"# skipped {len(skipped)} name(s) with <5 dotted fields")


if __name__ == "__main__":
    main()
