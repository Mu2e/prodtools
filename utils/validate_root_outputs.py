#!/usr/bin/env python3
"""Read back every entry of every TTree in each ROOT/art file given;
exit 1 if anything is unreadable.

Runs on the worker right after `mu2e` exits 0 and before anything is
pushed (runmu2e._validate_outputs). It is the only gate that looks at
the payload: art's exit code says the file was written and closed, the
SHA256 manifest and pushOutput's CRC certify the bytes as they are on
disk, dCache checks its copy against that same CRC. Bytes that went
wrong between art's write and the file on disk pass all of them —
2026-08-20, fnpc18003: two digs each with one displaced ~256 KiB block
were declared with matching checksums and only failed in reco a week
later. A full GetEntry sweep decompresses every basket (each zlib stream
carries its own adler32), so any such damage surfaces here as a
GetEntry() <= 0, the job is marked failed, and recovery re-runs the
index on another node.

Cost: ~15 s per GB, CPU-bound (a 6.5 GB dig takes ~100 s).

Needs the ROOT that the job's musing / code tarball provides, so the
caller sources that setup first — the ops environment has no ROOT.
"""

import sys


def scan(path):
    """{tree_name: (n_bad, first_bad, last_bad)} for `path`; an
    unopenable file reports as {'<file>': (1, -1, -1)}."""
    import ROOT
    ROOT.gROOT.SetBatch(True)
    f = ROOT.TFile.Open(path)
    if not f or f.IsZombie():
        return {'<file>': (1, -1, -1)}
    bad = {}
    # Keys repeat per cycle (Events;1, Events;2) — read each tree once.
    for name in dict.fromkeys(k.GetName() for k in f.GetListOfKeys()):
        t = f.Get(name)
        if not isinstance(t, ROOT.TTree):
            continue
        b = [i for i in range(t.GetEntries()) if t.GetEntry(i) <= 0]
        if b:
            bad[name] = (len(b), b[0], b[-1])
    f.Close()
    return bad


def main(paths):
    failed = False
    for p in paths:
        bad = scan(p)
        print(f"{'BAD ' if bad else 'OK  '} {p} {bad if bad else ''}".rstrip(),
              flush=True)
        failed = failed or bool(bad)
    return 1 if failed else 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit("usage: validate_root_outputs.py FILE [FILE...]")
    sys.exit(main(sys.argv[1:]))
