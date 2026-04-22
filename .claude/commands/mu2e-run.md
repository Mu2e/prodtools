---
description: Source Mu2e environment and run a prodtools command (json2jobdef, jobfcl, etc.)
argument-hint: [simjob-version] <command> [args...]
allowed-tools: Bash
---

# Run a prodtools command with the Mu2e environment

Source the Mu2e setup, configure the SimJob environment, and run the given command.

## Usage

```
/mu2e-run [simjob-version] <command> [args...]
```

- `simjob-version` — optional SimJob release tag (e.g. `Run1Bag`, `MDC2025af`). Default: `Run1Bag`
- `command` — the prodtools command to run, e.g. `json2jobdef`, `jobfcl`, `famtree`

## Examples

```
/mu2e-run json2jobdef --verb --json data/Run1B/primary_muon.json --dsconf Run1Bag --desc DIOtail0_60
/mu2e-run Run1Bab json2jobdef --verb --json data/Run1B/mix.json --dsconf Run1Bab_best_v1_2
/mu2e-run MDC2025af json2jobdef --json data/mdc2025/mds3a.json
```

## Instructions

You are given `$ARGUMENTS`. Follow these steps:

1. **Parse the arguments**: Check if the first word looks like a SimJob version tag (starts with a capital letter and contains no `/`, `-`, or spaces typical of a command name — e.g. `Run1Bag`, `MDC2025af`, `Run1Bab`). If so, use it as `SIMJOB_VERSION` and the rest as the command. Otherwise default `SIMJOB_VERSION` to `Run1Bag` and use the full `$ARGUMENTS` as the command.

2. **Run** the following as a single Bash command (everything in one shell so the sourced environment is active when the command runs):

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh \
  && muse setup ops \
  && muse setup SimJob <SIMJOB_VERSION> \
  && bash bin/<command> <remaining-args>
```

3. Show the output to the user. If the command fails, report the error clearly.
