#!/usr/bin/env python3
"""
stage_eff_reader - read generated/passed/prescale counts out of art files.

Standalone on purpose: it needs ROOT with the Mu2e dictionaries (any SimJob
or Offline musing), which the ops environment does not have, so stage_eff
runs it as a subprocess. It imports nothing from prodtools.

No art job and no event loop: the SubRuns tree holds one GenEventCount (and
any PrescaleFilterFraction) per subrun, including subruns that passed zero
events, and the Events tree knows its own entry count. Works on any readable
art file, declared in SAM or not.

Prints one JSON object as the LAST line of stdout:
  {"files": N, "subruns": N, "gen": N, "passed": N,
   "prescales": {"<module label>": {"seen": N, "passed": N}}}
"""
import json
import sys

GEN_PREFIX = 'mu2e::GenEventCount_'
PRESCALE_PREFIX = 'mu2e::PrescaleFilterFraction_'


def module_label(branch_name):
    """Module label out of an art branch name
    `<friendly type>_<module label>_<instance>_<process>.`"""
    return branch_name.split('_')[1]


def read(filenames):
    import ROOT
    ROOT.gErrorIgnoreLevel = ROOT.kError

    gen = passed = 0
    subrun_file = {}
    prescales = {}
    for fn in filenames:
        tfile = ROOT.TFile.Open(fn)
        if not tfile or tfile.IsZombie():
            raise RuntimeError(f'cannot open {fn}')
        subruns = tfile.Get('SubRuns')
        events = tfile.Get('Events')
        if not subruns or not events:
            raise RuntimeError(f'{fn} is not an art file: no SubRuns/Events tree')

        names = [b.GetName() for b in subruns.GetListOfBranches()]
        gen_branches = [n for n in names if n.startswith(GEN_PREFIX)]
        prescale_branches = [n for n in names if n.startswith(PRESCALE_PREFIX)]
        if len(gen_branches) != 1:
            # None: the denominator is unknown. Several: a file that went
            # through more than one counting stage, and summing them would
            # double count.
            raise RuntimeError(
                f'{fn} has {len(gen_branches)} GenEventCount products '
                f'{gen_branches}; expected exactly one')

        passed += events.GetEntries()
        for entry in subruns:
            aux = entry.SubRunAuxiliary
            key = (aux.run(), aux.subRun())
            if key in subrun_file:
                if subrun_file[key] == fn:
                    continue  # fragments of one subrun inside one file
                # The same subrun in two files means the same events twice (a
                # copy, or a concatenation listed next to its inputs). Counting
                # its events per file and its counters once would inflate the
                # efficiency without a trace.
                raise RuntimeError(
                    f'run {key[0]} subRun {key[1]} is in both '
                    f'{subrun_file[key]} and {fn}: the file list overlaps')
            subrun_file[key] = fn
            gen += getattr(entry, gen_branches[0]).product().count()
            for name in prescale_branches:
                product = getattr(entry, name).product()
                tally = prescales.setdefault(module_label(name),
                                             {'seen': 0, 'passed': 0})
                tally['seen'] += product.nSeen()
                tally['passed'] += product.nPassed()
        tfile.Close()

    return {'files': len(filenames), 'subruns': len(subrun_file),
            'gen': gen, 'passed': passed, 'prescales': prescales}


def main(argv):
    if not argv:
        print('usage: stage_eff_reader.py FILE [FILE ...]', file=sys.stderr)
        return 2
    try:
        result = read(argv)
    except Exception as exc:
        print(f'stage_eff_reader: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
