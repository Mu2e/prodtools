# in-session 2026-04-27: G4Beamline runner integration into prodtools

What was added:
- New `runner: "g4bl"` field in jobdesc/JSON schema (sibling to existing 'template' / 'direct_input' / normal modes), routed through `validate_jobdesc` returning 'g4bl' mode.
- New function `process_g4bl_jobdef(jobdesc_entry, fname, args)` in `utils/prod_utils.py` that executes g4bl in-place (no separate `mu2e -c` step). Writes a transient bash script to the output dir; runs `apptainer exec` against an SL7 container; binds /cvmfs, /tmp, embed_dir, $HOME; runs `source setupmu2e-art.sh; setup G4beamline; cd embed_dir; g4bl <main_input> Num_Events=N First_Event=F param histoFile=<sequencer-named>.root`.
- New `runmu2e.py` g4bl branch that calls `process_g4bl_jobdef` and skips the FCL/mu2e dispatch.
- New constant `DEFAULT_G4BL_CONTAINER = /cvmfs/singularity.opensciencegrid.org/fermilab/fnal-dev-sl7:latest` (overridable via JSON `container` field).
- New `poms/g4bl.cfg` mirroring `fermigrid.cfg` but with `+SingularityImage` pointing at the SL7 dev image — for grid submission, the outer container *is* SL7, so no nesting needed at runtime.
- JSON schema fields: `runner`, `embed_dir` (single root dir to bundle), `main_input` (filename inside embed_dir), `events_per_job`, `njobs`. No `fcl`, no `simjob_setup` (different mechanism).
- Files: data/mdc2025/g4bl.json (canonical production config), test/g4bl_smoke_jobdesc.json (smoke fixture).

Decisions made (with rationale, ranked by surprise):

1. Embed strategy = "single root dir + main_input filename" (NOT separate input_lattice + embed_paths list). Mirrors what the user already does manually (`cd $IN_DIR && g4bl $(basename $IN)`). Geometry/*.txt files reference each other by relative path so they MUST travel with the .in. cvmfs-absolute references (BField maps under /cvmfs/mu2e.opensciencegrid.org/DataFiles/BFieldMaps/GA05/) stay where they are — container has /cvmfs bound. Default `embed_exclude` drops `*.root`/`*.tar`/`root_archive_*` so 1.7M of past outputs in the source dir don't bloat the tarball; embedded footprint for Mu2E.in is ~560K.

2. First_Event indexing is 0-based linear: `first_event = job_index * events_per_job + 1`. Matches the existing prodtools `range(njobs)` dispatch convention. NOT seed-based (defer if needed later).

3. Container path NOT in JSON — code constant + POMS cfg. Two homes:
   - Local-runner fallback: `DEFAULT_G4BL_CONTAINER` constant in prod_utils.py
   - Grid: `+SingularityImage` in poms/g4bl.cfg (outer container set by Condor)
   Single source of truth as a string in code; cfg duplicates it but cfg files don't reference Python, so the duplication is acceptable. JSON's `container` field is opt-in override.

4. fnal-wn-sl7 doesn't exist — only fnal-dev-sl7. Confirmed via /cvmfs/singularity.opensciencegrid.org/fermilab/ listing. fnal-dev-sl7 works as a Condor `+SingularityImage` (Condor accepts any path; the wn-* naming is FNAL convention, not requirement). Avoids nested containers entirely on grid.

5. Reuse cnf.*.tar prefix (NOT g4bl.cnf.*.tar) — uniform tarball naming across runners; content differs but naming convention stays.

Two non-obvious gotchas (would have been missed without smoke test):

A) **`--cleanenv` is mandatory** when launching apptainer from a Python subprocess whose parent has AL9 mu2e env sourced. The AL9 PYTHONHOME / UPS_DIR / PRODUCTS env vars leak into the SL7 container and break setupmu2e-art.sh's UPS init silently. Symptom: `bash: setup: command not found` at line 3 of the runner script, despite `source` returning 0. The same command run via shell `eval` works without --cleanenv (bash doesn't inject those vars). Discovered after multiple incorrect bisections (set -e? && vs newline? bash -lc vs bash <script>? shell=True quote handling? — none of those).

B) **`/tmp` must be bound wholesale**, not just the per-job output subdir. UPS init in setupmu2e-art.sh uses /tmp for scratch. Binding only `/tmp/g4bl_runner.X/` left the rest of /tmp inside the container as a separate empty tmpfs and UPS init failed silently — same `setup: command not found` symptom as gotcha A but a separate root cause. Both are needed.

Other notes:
- `_is_inside_sl7()` checks /etc/redhat-release for "release 7" — when grid Condor scheduled the job inside SL7 directly (via poms/g4bl.cfg), the runner detects this and skips the apptainer wrap, executing g4bl natively. Same code path, two execution modes.
- The transient bash script is kept in the output dir on failure for debugging, removed on success.
- Smoke test command (verified working as of 2026-04-27): export fname=cnf.mu2e.Mu2EBeamlineSmoke.MDC2025ai_g4bl_v1_0.000001.tar; python3 utils/runmu2e.py --jobdesc test/g4bl_smoke_jobdesc.json --dry-run → 81KB g4bl.mu2e.Mu2EBeamlineSmoke.MDC2025ai_g4bl_v1_0.000001.root, 0 fatal exceptions, simulation complete.

Deferred to next slice: json2jobdef integration so `data/mdc2025/g4bl.json` is consumed by the build pipeline (today the fixture is jobdesc-list shape, not the production config shape; the runner doesn't yet build a `cnf.*.tar` from the production config — the user runs against an explicit jobdesc-list-style fixture).
