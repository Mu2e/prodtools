#!/usr/bin/env python3
"""For each unique description (3rd field), pick the dataset with the
latest dsconf (4th field).

Mu2e dataset/definition names are dot-delimited:

    <tier>.<owner>.<description>.<dsconf>[.<sequencer>].<format>

This tool queries `samweb list-definitions` (or reads names from stdin),
groups by description, and prints the lexicographically-greatest dsconf
per group. For Mu2e dsconfs like `MDC2025af_best_v1_3`, lex order tracks
campaign letter then version.
"""

import argparse
import os
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.job_common import Mu2eName


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
    cmd = ["samweb", "list-definitions"]
    if user:
        cmd.append(f"--user={user}")
    if defname_pattern:
        cmd.append(f"--defname={defname_pattern}")
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def latest_per_description(names):
    """Return list of (description, latest_dsconf, latest_name, count)."""
    groups = defaultdict(list)
    skipped = []
    for name in names:
        parsed = parse_name(name)
        if parsed is None:
            skipped.append(name)
            continue
        description, dsconf = parsed
        groups[description].append((dsconf, name))

    rows = []
    for description, items in groups.items():
        items.sort(key=lambda x: x[0])
        latest_dsconf, latest_name = items[-1]
        rows.append((description, latest_dsconf, latest_name, len(items)))
    rows.sort(key=lambda r: r[0])
    return rows, skipped


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


def _dataset_file_count(name):
    """Number of files in a SAM dataset (completeness numerator)."""
    res = subprocess.run(["samweb", "count-files", f"dh.dataset={name}"],
                         capture_output=True, text=True, check=True)
    return int(res.stdout.strip())


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
            if _dataset_file_count(ds) == njobs:
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
        return _dataset_file_count(name) > 0
    except Exception:
        return False


def _filter_unproduced(inputs, template):
    """Drop inputs whose this-stage output already exists in SAM (the chain has
    already produced them). Output dataset names come from the synthesized entry.
    Diagnostics to stderr."""
    from utils import chain_emit
    print(f"Checking produced outputs for {len(inputs)} datasets, please wait...",
          file=sys.stderr)
    kept = []
    for ds in inputs:
        entry = chain_emit.synthesize_entry(template, ds)
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

    try:
        template = chain_emit.load_template(args.campaign, args.emit, args.templates_dir)
        defname = args.defname or chain_emit.derive_input_defname(template, args.campaign)
    except (FileNotFoundError, ValueError) as e:
        sys.exit(str(e))
    _vlog(f"# discovering inputs: {defname}")

    names = fetch_definitions(defname, args.user)
    names = _narrow_to_latest_release(names)
    rows, skipped = latest_per_description(names)
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
        latest = _filter_unproduced(latest, template)

    config = chain_emit.emit_config(template, latest)
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
    ap.add_argument("--names-only", action="store_true",
                    help="print only the latest defname per group (no description/count columns)")
    ap.add_argument("--show-count", action="store_true",
                    help="include a column with how many dsconfs were collapsed per description")
    ap.add_argument("--emit", choices=("digi", "reco", "ntuple"),
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

    rows, skipped = latest_per_description(names)

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

    if args.names_only:
        for _, _, latest_name, _ in rows:
            print(latest_name)
    else:
        for description, latest_dsconf, latest_name, count in rows:
            if args.show_count:
                print(f"{count:3d}  {latest_name}")
            else:
                print(latest_name)

    if skipped:
        _vlog(f"# skipped {len(skipped)} name(s) with <5 dotted fields")


if __name__ == "__main__":
    main()
