#!/usr/bin/env python3
"""Declare a finished one-shot run's output files to SAM, with parents.

A one-shot run (`json2jobdef --once`) leaves its outputs in outstage and
declares nothing. This publishes a chosen set of those files through
pushOutput, in batches, each file carrying the parents its job read:

    publish_run --run cnf.u.Desc.Conf.0 --location scratch \\
                --files-from files.txt --work-dir /exp/mu2e/data/users/u/pub \\
                [--rename-from rename.json] [--batch 50]

The caller passes the files (one path per line). Each is matched to its
job index through the run's own cnf, `<runs_root>/<run>/<run>.tar`, and a
file the run did not produce is refused. A file's parents are every input
file of its job.

--rename-from is a JSON table `{old_dsconf: {"dsconf": new, "suffix": label}}`.
A name whose dsconf is in the table is published as
`tier.owner.desc.<new>.<run>_<subrun>_<label>.ext`, and so are its
parents (the same rule as the cnf key sequencer_suffix).

One publish of a run at a time: the call holds `<runs_root>/<run>/
publish.lock` throughout, and files are pushed in name order.

Refused before any token or push (exit 2): another publish_run of the
same run (its lock is held), no files, a missing file, a
file the run did not produce, a name owned by someone else, a name
already in SAM with a different size or adler32, the same file in SAM
with no location or none at --location (a declared file is never
moved), and a parent that is not in SAM. A file already in SAM with the
same size and adler32 and a location at --location is skipped, so a
rerun after a died push resumes. A failed `htgettoken` or pushOutput
exits 1, and so does a batch after which SAM does not hold every pushed
file at --location. Nothing else falls back.
"""
import argparse
import errno
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import zlib
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional

from utils import run_receipt
from utils.file_resolver import file_path_at, path_from_sam_location
from utils.job_common import Mu2eName, apply_sequencer_suffix, default_owner
from utils.jobquery import Mu2eJobPars
from utils.prod_utils import push_output
from utils.push_file import PUSH_LOCATIONS
from utils.run_receipt import runs_root
from utils.samweb_wrapper import (DefinitionNotFound, FileNotFound, create_definition,
                                  describe_definition, get_metadata, locate_file_strict)

Rename = Dict[str, Dict[str, str]]

TOKEN_CMD = ['htgettoken', '-a', 'htvaultprod.fnal.gov', '-i', 'mu2e',
             '--minsecs', '1800']
_SUFFIX = re.compile(r'[A-Za-z0-9_-]+')
_CHUNK = 1 << 20
LOCK_NAME = "publish.lock"      # in the run's directory, beside its receipt


class Item(NamedTuple):
    path: str            # where the file is now
    name: str            # its SAM name (renamed)
    parents: List[str]   # SAM names (renamed), sorted


class Refusal(Exception):
    """A reason to publish nothing; main exits 2."""


def load_rename(path: str) -> Rename:
    try:
        table = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise Refusal(f"publish_run: cannot read rename table {path}: {e}") from None
    if not isinstance(table, dict):
        raise Refusal(f"publish_run: {path} must hold a JSON object")
    for old, spec in table.items():
        ok = (isinstance(spec, dict) and set(spec) == {"dsconf", "suffix"}
              and all(isinstance(v, str) and v for v in spec.values()))
        if not ok:
            raise Refusal(f"publish_run: rename entry {old!r} must be "
                          f'{{"dsconf": ..., "suffix": ...}}, got {spec!r}')
        if not _SUFFIX.fullmatch(spec["suffix"]) or "." in spec["dsconf"]:
            raise Refusal(f"publish_run: rename entry {old!r} has a bad "
                          f"dsconf or suffix: {spec!r}")
    return table


def renamed(name: str, rename: Optional[Rename]) -> str:
    """The SAM name for `name` under the rename table; unchanged when the
    table is empty or does not hold the name's dsconf."""
    n = Mu2eName.parse(name)
    if not rename or n.sequencer is None or n.dsconf not in rename:
        return name
    spec = rename[n.dsconf]
    return Mu2eName.build(
        tier=n.tier, owner=n.owner, description=n.description,
        dsconf=spec["dsconf"], extension=n.extension,
        sequencer=apply_sequencer_suffix(n.sequencer, spec["suffix"])).filename


def _flat(values) -> List[str]:
    out: List[str] = []
    for v in values:
        out.extend([v] if isinstance(v, str) else list(v))
    return out


def plan(run_name: str, files: List[str], rename: Optional[Rename], *,
         job_pars, owner: str) -> List[Item]:
    if not files:
        raise ValueError(f"publish_run: no files given for run {run_name}")
    missing = [f for f in files if not Path(f).is_file()]
    if missing:
        raise ValueError(f"publish_run: {len(missing)} file(s) do not exist "
                         f"(purged?), nothing pushed: " + ", ".join(missing))
    index_of: Dict[str, int] = {}
    for i in range(job_pars.njobs()):
        for out in job_pars.job_outputs(i).values():
            index_of[os.path.basename(out)] = i
    items = []
    seen: Dict[str, str] = {}
    for f in files:
        base = os.path.basename(f)
        if base not in index_of:
            raise ValueError(f"publish_run: {base} is not an output of run {run_name}")
        name = renamed(base, rename)
        if name in seen:
            raise ValueError(f"publish_run: {name} would be published twice, from "
                             f"{seen[name]} and {f}")
        seen[name] = f
        if Mu2eName.parse(name).owner != owner:
            raise ValueError(f"publish_run: {name} is owned by "
                             f"{Mu2eName.parse(name).owner!r} but this identity "
                             f"publishes as {owner!r}")
        ins = _flat(job_pars.job_inputs(index_of[base]).values())
        parents = sorted({renamed(os.path.basename(p), rename) for p in ins})
        items.append(Item(os.path.abspath(f), name, parents))
    # By name, so a rerun lists the files in the same order whatever order
    # the caller gave: a file a died call left copied but undeclared is
    # then first in its rerun batch, where pushOutput's rollback of that
    # batch deletes nothing it declared.
    return sorted(items, key=lambda i: i.name)


def batches(items, size: int = 50) -> list:
    if size < 1:
        raise ValueError(f"publish_run: batch size must be >= 1, got {size}")
    return [items[i:i + size] for i in range(0, len(items), size)]


def adler32_hex(path: str) -> str:
    a = 1
    with open(path, 'rb') as fh:
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            a = zlib.adler32(chunk, a)
    return "%08x" % (a & 0xffffffff)


class Where(NamedTuple):
    status: str          # 'ok', 'absent', 'nolocation' or 'elsewhere'
    text: str            # why not 'ok', naming the file


def _where(name: str, location: str) -> Where:
    """Is `name` in SAM with a location of kind `location`?

    pushOutput declares a file, then adds its location in a separate call,
    as `<enstore|dcache>:<locprefix[action]>/<family>/<tier>/<owner>/<desc>/
    <dsconf>/<ext>/<h01>/<h23>` (OfflineOps Util/pushOutput.py, fillDataFile
    and declareSam). That is file_path_at(name, action) without the file
    name, so a record is of kind `location` when its path, storage prefix
    and tape label removed, is that path. SAM's own location_type cannot
    tell disk from scratch (both are 'disk').
    """
    try:
        records = locate_file_strict(name)
    except FileNotFound:
        return Where("absent", f"{name} is not in SAM")
    if not records:
        return Where("nolocation", f"{name} is declared in SAM with no location "
                     f"(a push that died between declaring it and adding its "
                     f"location leaves this)")
    try:
        paths = [path_from_sam_location(name, r) for r in records]
    except ValueError as e:
        return Where("nolocation", f"{name} has an unreadable SAM location: {e}")
    if file_path_at(name, location) in paths:
        return Where("ok", "")
    kinds = []
    for p in paths:
        kind = next((loc for loc in PUSH_LOCATIONS if p == file_path_at(name, loc)), p)
        kinds.append(kind)
    return Where("elsewhere", f"{name} is in SAM on {', '.join(kinds)}, not on "
                 f"{location}; a declared file is never moved")


def _sam_state(item: Item, location: str) -> str:
    """'absent', or 'same'; raises Refusal when SAM holds a different file,
    or the same file without a location of kind `location`."""
    try:
        meta = get_metadata(item.name)
    except FileNotFound:
        return "absent"
    sums = []
    for c in (meta.get("checksum") or []):
        if c.startswith("adler32:"):
            try:
                sums.append(int(c.split(":", 1)[1], 16))
            except ValueError:
                raise Refusal(f"publish_run: SAM's adler32 for {item.name} is "
                              f"not hexadecimal: {c!r}") from None
    if not sums:
        raise Refusal(f"publish_run: SAM's record of {item.name} has no adler32 "
                      f"checksum, so it cannot be compared with {item.path}")
    if meta.get("file_size") != os.path.getsize(item.path) \
            or sums[0] != int(adler32_hex(item.path), 16):
        raise Refusal(f"publish_run: {item.name} is in SAM with a different size "
                      f"or adler32 than {item.path}; a name is never reused")
    where = _where(item.name, location)
    if where.status == "ok":
        return "same"
    if where.status == "nolocation":
        raise Refusal(f"publish_run: {where.text}; a rerun cannot add it, so fix "
                      f"or retire that SAM record first")
    raise Refusal(f"publish_run: {where.text}")


def _confirm_pushed(batch: List[Item], location: str) -> Optional[str]:
    """None when SAM holds every pushed name with a location of kind
    `location`, else the failure message (rerun advice only when a rerun
    would push the missing files again)."""
    bad = [w for w in (_where(i.name, location) for i in batch) if w.status != "ok"]
    if not bad:
        return None
    text = "; ".join(w.text for w in bad)
    if all(w.status == "absent" for w in bad):
        return f"{text}; rerun to resume"
    return f"{text}; a rerun refuses these too, so fix their SAM records first"


def _in_sam(name: str) -> bool:
    try:
        get_metadata(name)
    except FileNotFound:
        return False
    return True


def _stage(item: Item, batch_dir: Path, stage: str) -> str:
    """Path pushOutput should read: the file itself, or, when renamed,
    a copy (default) or symlink under its SAM name in the batch dir.
    An existing destination or a failed link raises; nothing falls back."""
    if item.name == os.path.basename(item.path):
        return os.path.abspath(item.path)
    dest = str(batch_dir / item.name)
    if os.path.lexists(dest):
        raise FileExistsError(f"publish_run: {dest} already exists; "
                              f"refusing to write through it")
    if stage == "link":
        os.symlink(os.path.abspath(item.path), dest)
    else:
        shutil.copyfile(item.path, dest)
    return dest


def _push_batch(k: int, batch: List[Item], location: str, work_dir: Path,
                stage: str) -> int:
    batch_dir = work_dir / f"batch_{k:04d}"
    batch_dir.mkdir(parents=True)
    cwd = os.getcwd()
    try:
        specs = []
        for j, item in enumerate(batch):
            staged = _stage(item, batch_dir, stage)
            field = "none"
            if item.parents:
                field = f"parents_{j}.txt"
                (batch_dir / field).write_text("\n".join(item.parents) + "\n")
            specs.append((location, staged, field))
        os.chdir(batch_dir)
        try:
            rc = push_output(specs, "output.txt")
        finally:
            os.chdir(cwd)
        if rc != 0:
            return rc
        # push_output skips a missing file with only a warning; do not
        # count what it did not hand to pushOutput.
        listed = set()
        out = batch_dir / "output.txt"
        if out.is_file():
            listed = {ln.split()[1] for ln in out.read_text().splitlines() if ln.strip()}
        lost = [s[1] for s in specs if s[1] not in listed]
        if lost or len(listed) != len(specs):
            raise OSError("push_output left out " + ", ".join(
                os.path.basename(x) for x in lost) + " (missing at push time?)")
        return 0
    finally:
        shutil.rmtree(batch_dir)


def _renew_token(run) -> Optional[str]:
    try:
        rc = run(TOKEN_CMD, stdout=subprocess.DEVNULL).returncode
    except OSError as e:
        rc = str(e)
    if rc != 0:
        return f"publish_run: `{' '.join(TOKEN_CMD)}` failed ({rc})"
    return None


def main(argv=None, *, run: Callable = subprocess.run) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--location", required=True, choices=PUSH_LOCATIONS)
    ap.add_argument("--files-from", required=True, help="one outstage path per line")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--rename-from")
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--stage", choices=("copy", "link"), default="copy",
                    help="how a renamed file reaches its batch dir (default copy)")
    args = ap.parse_args(argv)

    lock = None
    try:
        try:
            owner = default_owner()
            if owner == "mu2e" or os.getenv("USER") == "mu2epro":
                raise Refusal("publish_run: publishes as the user only; "
                              "mu2epro is out of scope")
            root = runs_root()
            try:
                run_receipt.read(root, args.run)
            except (run_receipt.RunNotFound, ValueError) as e:
                raise Refusal(f"publish_run: {e}") from None
            run_dir = os.path.join(root, args.run)
            cnf = os.path.join(run_dir, args.run + ".tar")
            if not os.path.isfile(cnf):
                raise Refusal(f"publish_run: run {args.run} has no cnf at {cnf}")
            lock = _lock_run(run_dir, args.run)
        except Refusal as e:
            print(e, file=sys.stderr)
            return 2
        return _publish(args, cnf, owner, run)
    finally:
        if lock is not None:
            os.close(lock)      # releases the run's publish lock


def _lock_run(run_dir: str, run_name: str) -> int:
    """Take the run's publish lock, `<run_dir>/publish.lock`, exclusive and
    non-blocking, and return its descriptor; it is held until main closes
    it. Two publishes of one run at once can make pushOutput's rollback
    delete the bytes of files the other already declared (it calls rmFile,
    not retireSam, on a declared file), so a second one is refused."""
    path = os.path.join(run_dir, LOCK_NAME)
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as e:
        raise Refusal(f"publish_run: cannot open the run's lock {path}: {e}") from None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        os.close(fd)
        if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            raise Refusal(f"publish_run: another publish_run of {run_name} holds "
                          f"{path}; wait for it to end") from None
        raise Refusal(f"publish_run: cannot lock {path}: {e}") from None
    return fd


def _publish(args, cnf: str, owner: str, run: Callable) -> int:
    """Everything after the run's checks, under its lock."""
    try:
        try:
            rename = load_rename(args.rename_from) if args.rename_from else None
            files = [ln.strip() for ln in Path(args.files_from).read_text().splitlines()
                     if ln.strip()]
        except OSError as e:
            raise Refusal(f"publish_run: cannot read input: {e}") from None
        try:
            items = plan(args.run, files, rename, job_pars=Mu2eJobPars(cnf),
                         owner=owner)
            batches([], args.batch)  # refuse a bad --batch before any SAM call
        except ValueError as e:
            raise Refusal(str(e)) from None

        todo, skipped = [], 0
        for item in items:
            if _sam_state(item, args.location) == "same":
                skipped += 1
            else:
                todo.append(item)
        for parent in sorted({p for it in items for p in it.parents}):
            if not _in_sam(parent):
                raise Refusal(f"publish_run: parent {parent} is not in SAM "
                              f"(needed by {next(i.name for i in items if parent in i.parents)})")
        groups = batches(todo, args.batch)
    except Refusal as e:
        print(e, file=sys.stderr)
        return 2

    work_dir = Path(os.path.abspath(args.work_dir))
    work_dir.mkdir(parents=True, exist_ok=True)
    for stale in sorted(work_dir.glob("batch_*")):
        print(f"publish_run: removing {stale}, left by an earlier run "
              f"(this tool's own staging)", file=sys.stderr)
        shutil.rmtree(stale)
    for k, batch in enumerate(groups):
        bad = _renew_token(run)
        if bad:
            print(f"{bad}; batch {k} and later not pushed", file=sys.stderr)
            return 1
        try:
            rc = _push_batch(k, batch, args.location, work_dir, args.stage)
        except subprocess.CalledProcessError as e:
            rc = e.returncode or 1
        except OSError as e:
            print(f"publish_run: batch {k} not fully pushed: {e}; rerun to resume",
                  file=sys.stderr)
            return 1
        if rc != 0:
            print(f"publish_run: pushOutput failed (exit {rc}) on batch {k}; "
                  f"rerun to resume", file=sys.stderr)
            return 1
        bad = _confirm_pushed(batch, args.location)
        if bad:
            print(f"publish_run: batch {k} pushed, but {bad}", file=sys.stderr)
            return 1

    datasets = sorted({Mu2eName.parse(i.name).dataset.filename for i in items})
    for ds in datasets:
        try:
            describe_definition(ds)
        except DefinitionNotFound:
            bad = _renew_token(run)
            if bad:
                print(f"{bad}; definition {ds} not created", file=sys.stderr)
                return 1
            create_definition(ds, f"dh.dataset {ds}")
    print(json.dumps({"pushed": len(todo), "skipped": skipped, "datasets": datasets}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
