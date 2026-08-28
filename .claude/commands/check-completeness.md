# Check dataset completeness and triage what is actually wrong

Runs `listNewDatasets --completeness` and turns its `INCOMPLETE` rows
into a verdict: still running, genuinely short, or dead on arrival.
The raw table cannot tell those apart, and two of the three failure
modes are invisible in it.

## Usage

```
/check-completeness [--days N] [--query SQL] [--campaign ID]
```

- `--days N` — lookback window, default 1. `listNewDatasets` itself
  defaults to 7.
- `--query SQL` — restrict to one round, e.g.
  `"dh.dataset like '%.Run1Baw_best_v1_5.%'"`.
- `--campaign ID` — skip the sweep, triage a single campaign.

## Instructions

### 1. Run the listing

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops
python3 <REPO>/bin/listNewDatasets --days <N> --completeness --color never
```

**Invoke it with `python3`.** `bin/listNewDatasets` is a Python
script; `bin/json2jobdef` is a shell wrapper. Running this one with
`bash` dies at `syntax error near unexpected token '('` — that is the
wrong interpreter, not a broken tool.

`--completeness` compares SAM file count against the job count in the
ledger, so only direct-backend datasets get a verdict. Legacy
POMS-launched datasets are never in the ledger and always read blank.

### 2. Classify every INCOMPLETE row

An `INCOMPLETE` row is not automatically a problem. Get the campaign's
cursor (MCP `campaign_status`, or `list_campaigns`) and split:

| Condition | Meaning | Action |
|---|---|---|
| `cursor < njobs` | still feeding, more slices to come | none — expected |
| `cursor == njobs`, count short | fully submitted but output missing | recovery |
| `queue.state == "unknown"` | the query failed, not a zero | **do not recover** — re-check later |

Never read a missing count as zero. A campaign at `cursor == njobs`
whose row is still `active` may simply have jobs in flight.

### 3. Re-read before firing a recovery

Counts climb as `pushOutput` lands. A dataset reading 77/100 may read
85/100 minutes later. Re-run the listing before dispatching a
recovery, or the pass re-submits indices whose files are already
arriving.

### 4. Find the datasets that are NOT in the table

**This is the failure mode the table cannot show.** A dataset with
zero files does not appear at all, so a campaign whose every job died
looks identical to a campaign that was never run. Cross-check the
ledger for campaigns with `produced: 0`:

```
campaign_status(campaign="<name-or-substring>")
```

and confirm each recently-submitted campaign appears in the listing.
One that does not is a total failure, not an absence of news.

Note `campaign_status`'s `campaign` argument matches on **substring**,
not id: passing `82` matches any tarball containing `82`, including
`1809`. Match on the tarball or dataset name instead.

### 5. Triage a campaign that produced nothing

```bash
# exit codes -- always pass -name <schedd>; jobsub_history silently
# drops it and queries jobsub01 regardless
condor_history -name <schedd>.fnal.gov <cluster> -limit 2000 \
  -af ExitCode ExitBySignal HoldReasonCode JobStatus | sort | uniq -c

# how long they survived
condor_history -name <schedd>.fnal.gov <cluster> -limit 2000 \
  -af RemoteWallClockTime | sort -n | \
  awk '{a[NR]=$1} END{print "min="a[1], "med="a[int(NR/2)], "max="a[NR]}'
```

Read the signature:

- **All jobs `ExitCode 1`, no hold, no signal, median wallclock ~1-3
  min** → died in setup, before `art`. Almost always input
  resolution. Confirm with "no log dataset in SAM" — a job that
  reaches `pushOutput` writes a log even when it fails.
- **Code 12/2, ~30 s walltimes, one execution point** → black-hole
  slot. Group by slot before recovering.
- **Held with age** → held rows block recovery; check `hold_reasons`.
- **Long wallclock then failure** → real physics/resource problem;
  read an actual job log.

### 6. Check `inloc` against where the files really are

`inloc` is a declaration, not a lookup. The resolver probes the
declared area first, then falls back through
`disk, resilient, tape, stash, scratch` and announces the substitution
on the worker's stderr. So a wrong `inloc` usually still runs — but it
reads from wherever the fallback lands, which can be tape.

```bash
samweb locate-file <one-file>          # LOCATION (which path family)
mdh query-dcache -o <dataset>          # RESIDENCY (online / nearline)
```

**These answer different questions.** `locate-file` printing
`enstore:/pnfs/mu2e/tape/...(nearline)` is the location record. It
does **not** mean the file is cold: `mdh query-dcache -o` returning
`ONLINE_AND_NEARLINE` means it is cached and reads are cheap.

Make the declaration match the copy you want read. Inputs a whole
campaign hammers (resampler stops, pileup Cats) belong on resilient
with `inloc: resilient`; copy with
`/mu2epro-run copy_to_stash --dataset <ds> --dest resilient --source tape`
(prestage first if any file is NEARLINE-only, else it lands 0-byte).

`inloc` lives in the ledger entry, not the cnf tarball, so this is
fixed without rebuilding anything:

```
submissions set-entry <ID> inloc resilient --include-open-rows
```

`--include-open-rows` is what makes the change reach rows already
open — without it the fix does not affect the recovery.

Also fix the JSON entry that produced it, or the next clone inherits
the same trap.

### 7. Report

Per dataset: count, expected, and one of `ok` / `in-progress` /
`recover N indices` / `dead — <cause>`. Name the campaign id and the
cluster for anything needing action. State explicitly which datasets
were expected but absent from the listing.

## Pitfalls

- Do not wrap `submissions run` in `timeout` — a kill after
  `jobsub_submit` but before the ledger write orphans the cluster.
- Ticks go through the MCP `run_submissions`, never a hand-rolled
  `ksu` loop.
- Never fetch or refresh the `mu2epro` token; if it is missing, stop
  and report.
- Status checks need no elevation — run them as the current user.
- A short smoke does not prove a job works on the grid: `jobfcl` +
  `mu2e` read inputs by path and skip the `runmu2e` input-resolution
  step, which is exactly where `inloc` bugs live.

## Examples

```
/check-completeness
/check-completeness --days 3
/check-completeness --query "dh.dataset like '%.Run1Baw_best_v1_5.%'"
/check-completeness --campaign 94
```
