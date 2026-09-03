---
status: accepted
---

# The cnf tarball is declared as a SAM parent of every output

SAM parentage records what data a file was made from (nts -> mcs -> dig
-> dts) and nothing about what code made it: two ntuple generations
written from the same mcs with different EventNtuple releases have
identical parents. The musing, Offline version, fcl and conditions live
only in the cnf tarball, which until now was not a parent of anything;
the only route from an output to its cnf was reading the cnf name out
of the job log's text. We add the cnf name to `parents_list.txt` in
`runmu2e.push_data`, so every output and log declares its cnf as a
parent alongside its input files. Provenance ("what code made this")
then costs one parents lookup, the same as lineage, and the sim-epochs
catalog derives a dataset's generation from the cnf via `jobquery`
without a side table. Legacy datasets are backfilled once by reading
the cnf name from one log per dataset and feeding the same code path.

## Considered Options

Log text as the provenance record: every log, direct-backend and
POMS-era alike, names its cnf. Rejected as the permanent path because a
generic entry names the log dataset after its input, so two output
generations share one log dataset and are told apart only by opening
each file. Kept as the one-time backfill.

The submission ledger: private to mu2epro, direct backend only, blind to
everything before 2026-08. Rejected.

## Consequences

`trace_provenance` and `famtree` up-walks show a cnf node above every
output; `famtree.get_parents` already filters `etc.*.txt` parents and
gets a sibling filter for `cnf.*.tar` where an art-only view is wanted.
`printJson --parents` requires every parent to be SAM-registered, which
holds for the cnf because `json2jobdef --prod` and the self-owned path
both push it before any job runs.
