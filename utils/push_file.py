#!/usr/bin/env python3
"""Publish one already-built file to SAM through pushOutput.

The worker publishes its outputs by writing an `output.txt` of
`<location> <file> <parents>` lines plus a `parents_list.txt` and
running `pushOutput output.txt` (utils/runmu2e.push_data). This is the
same call for a file built off the grid -- a beam file assembled from a
run's ntuples, for instance -- so the file lands in the dataset path its
name implies and is declared to SAM with its parents, exactly as a job
output would be.

    push_file --file /path/etc.u.MuBeamBeam-bm.e470313.0.txt \\
              --location scratch \\
              --parent nts.u.MuBeam.e470313.00000000.root [--parent ...]

Refused before anything is written: a missing file, a name that is not
a six-field Mu2e FILE name (five fields is a dataset), an owner other
than this identity's ($USER, mu2epro -> mu2e), a location pushOutput has
no action for, a parent that is not itself a file name, and a name that
is already in SAM (a name is never reused). pushOutput's own exit code
is this program's exit code; validation refusals exit 2.

Needs the Mu2e ops environment plus OfflineOps (pushOutput). The
prodtools-write MCP tool `push_file` runs it inside that chain as the
requested identity; `bin/push_file` is the direct form.
"""
import argparse
import subprocess
import sys
from pathlib import Path

from utils.job_common import Mu2eName, default_owner
from utils.jobdesc import OUTLOC_VALID, OUTSTAGE_LOCATION
from utils.prod_utils import push_output
from utils.samweb_wrapper import locate_file

# The pushOutput actions. 'outstage' is a worker-side copy with no SAM
# declare (utils/jobdesc.py), which is the opposite of publishing.
PUSH_LOCATIONS = tuple(loc for loc in OUTLOC_VALID if loc != OUTSTAGE_LOCATION)


def _file_name(text, what):
    try:
        name = Mu2eName.parse(text)
    except ValueError as e:
        raise ValueError(f"push_file: {what} {text!r} is not a Mu2e file name: {e}") from e
    if name.sequencer is None:
        raise ValueError(
            f"push_file: {what} {text!r} has five fields, which is a dataset name; a file "
            f"name needs a sequencer: tier.owner.description.dsconf.sequencer.extension")
    return name


def validate(path, location, parents, *, owner) -> Mu2eName:
    """Everything knowable without SAM. `owner` is who this identity
    publishes as; the CLI passes default_owner(), the MCP tool derives it
    from run_as."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"push_file: {path} is not a file")
    name = _file_name(p.name, "file")
    if name.owner != owner:
        raise ValueError(f"push_file: {p.name} is owned by {name.owner!r} but this identity "
                         f"publishes as {owner!r}")
    if location not in PUSH_LOCATIONS:
        raise ValueError(f"push_file: location must be one of {PUSH_LOCATIONS}, got {location!r}")
    for parent in parents:
        _file_name(parent, "parent")
    return name


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True, help="the file to publish; its basename is its SAM name")
    ap.add_argument("--location", required=True, choices=PUSH_LOCATIONS)
    ap.add_argument("--parent", action="append", default=[], metavar="SAM_FILE",
                    help="a SAM parent of the file; repeat per parent")
    args = ap.parse_args(argv)
    try:
        name = validate(args.file, args.location, args.parent, owner=default_owner())
        if locate_file(name.filename):
            raise ValueError(f"push_file: {name.filename} is already in SAM; a file name is never reused")
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    parents_field = "none"
    if args.parent:
        Path("parents_list.txt").write_text("\n".join(args.parent) + "\n")
        parents_field = "parents_list.txt"
    try:
        return push_output([(args.location, str(Path(args.file).resolve()), parents_field)], "output.txt")
    except subprocess.CalledProcessError as e:
        return e.returncode or 1


if __name__ == "__main__":
    sys.exit(main())
