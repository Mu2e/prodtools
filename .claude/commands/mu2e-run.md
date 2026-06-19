---
description: Source Mu2e environment and run a prodtools command (json2jobdef, jobfcl, etc.)
argument-hint: [musing[/version]] <command> [args...]
allowed-tools: Bash
---

# Run a prodtools command with the Mu2e environment

Source the Mu2e setup, configure a Musing release (SimJob, AnalysisMDC2025, etc.), and run the given command.

## Usage

```
/mu2e-run [musing[/version]] <command> [args...]
```

- `musing[/version]` — optional Musing release.
  - Bare tag like `Run1Bag`, `MDC2025af`, `Run1Bab` → treated as `SimJob/<tag>`.
  - `<Musing>/<Version>` form like `AnalysisMDC2025/v02_00_00` → sources that musing's `setup.sh` directly (works for any Musing under `/cvmfs/mu2e.opensciencegrid.org/Musings/`).
  - Omitted → the skill parses `simjob_setup` from the `--json` config of the command (json2jobdef, jobfcl, fcldump, runmu2e) and sources that path directly. If `--json` is absent or the file has no `simjob_setup`, refuse to run and report which field was missing.
- `command` — the prodtools command to run, e.g. `json2jobdef`, `jobfcl`, `famtree`

## Examples

```
/mu2e-run json2jobdef --verb --json data/Run1B/primary_muon.json --dsconf Run1Bag --desc DIOtail0_60
/mu2e-run Run1Bab json2jobdef --verb --json data/Run1B/mix.json --dsconf Run1Bab_best_v1_2
/mu2e-run MDC2025af json2jobdef --json data/mdc2025/mds3a.json
/mu2e-run AnalysisMDC2025/v02_00_00 json2jobdef --json data/mdc2025/evntuple.json --desc CosmicSignalOffSpillTriggered-CH --dsconf MDC2025-003
```

## Instructions

You are given `$ARGUMENTS`. Follow these steps:

1. **Parse the arguments.** Examine the first whitespace-separated token:
   - If it contains `/` and matches `<Musing>/<Version>` (e.g. `AnalysisMDC2025/v02_00_00`), set `MUSING=<Musing>`, `MUSING_VERSION=<Version>`, drop it from the command.
   - Else if it looks like a bare SimJob tag (starts with uppercase letter, no `--`, no `.`, no slash — e.g. `Run1Bag`, `MDC2025af`, `Run1Bab`), set `MUSING=SimJob`, `MUSING_VERSION=<tag>`, drop it from the command.
   - Otherwise (no tag): scan the remaining args for `--json <path>` (or `--json=<path>`). Read the JSON, take the first entry's `simjob_setup` field (it looks like `/cvmfs/mu2e.opensciencegrid.org/Musings/<MUSING>/<MUSING_VERSION>/setup.sh`), and parse `<MUSING>` and `<MUSING_VERSION>` from that path. Refuse to run (do NOT fall back to `Run1Bag`) if `--json` is absent, the file does not exist, or `simjob_setup` is missing — report which check failed.

2. **Run** the following as a single Bash command (everything in one shell so the sourced environment is active when the command runs):

   For `MUSING=SimJob`:
   ```bash
   source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh \
     && getToken > /dev/null \
     && muse setup ops \
     && muse setup SimJob <MUSING_VERSION> \
     && bash bin/<command> <remaining-args>
   ```

   For any other Musing (sourced directly — works for AnalysisMDC2025 and other non-SimJob musings):
   ```bash
   source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh \
     && getToken > /dev/null \
     && muse setup ops \
     && source /cvmfs/mu2e.opensciencegrid.org/Musings/<MUSING>/<MUSING_VERSION>/setup.sh \
     && bash bin/<command> <remaining-args>
   ```

   `getToken` (on PATH directly after `setupmu2e-art.sh`, before `muse setup ops`) refreshes the user's bearer token at `/run/user/$UID/bt_u$UID` so xrootd reads from dCache `/pnfs/...` work in `mu2e -c` smokes against `inloc: disk` or `tape` entries. Without it, you get `Auth failed: No protocols left to try` from `TNetXNGFile::Open`.

3. Show the output to the user. If the command fails, report the error clearly.

## Notes

- For ntuple-stage commands (`data/<campaign>/evntuple.json`), use `AnalysisMDC2025/<ver>` — NOT a SimJob tag. The simjob_setup field in evntuple.json entries points to AnalysisMDC2025 because mu2e_offline isn't the right Offline build for EventNtuple analyzers.
- For local mu2e -c smoke against tape inloc, also pass `--default-protocol root` to jobfcl (the default `file` proto returns SAM dir paths without filenames). See `reference_jobfcl_proto_root_for_tape_smoke.md`.
