---
title: Fcl-only code tarball for a self campaign (TS-at-30% study)
tags: [run, code-tarball, muse, push_cnf, run1b, field-map, self-campaign]
sources: []
updated: 2026-10-21
---

# Fcl-only code tarball for a self campaign

## Why a tarball at all
The Run1B study "μ⁻ yield at the TS5 exit with the TS at 30% field" needs
three new fcls and one 2-line field file. The field scale is
`bfield.scaleFactor`, a key in the bfgeom SimpleConfig file. It is not a
GeometryService FHiCL parameter (only `inputFile`, `bFieldFile` and a few
printing knobs exist). `bFieldFile` is resolved on `MU2E_SEARCH_PATH`, which
on a grid worker means the release and DataFiles on cvmfs. A file that is in
no Musing therefore has to ship with the job, and the code tarball is the one
channel prodtools has for that. fcl_overrides alone cannot do it.

## Recipe
Exact commands are in `/exp/mu2e/app/users/oksuzian/ts30_musing/README.md`.
The points that were not obvious:
- **Muse treats any directory with a `.muse` file as a repo.** An empty
  `TS30/.muse` holding `fcl/` and `geom/` is enough. No SConscript is needed,
  and `muse build` just creates `build/<platform>/TS30` and `.musebuild`.
- **Back the cvmfs SimJob Musing itself:**
  `muse backing /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2025ay`.
  The tarball then keeps `Code/backing` as a link to cvmfs, and Offline,
  Production and trig-config come from there on the worker.
- **Name files from the work dir root:** `TS30/fcl/POT_TS1.fcl`,
  `TS30/geom/bfgeom_DSOff_s30.txt`. That root is on `FHICL_FILE_PATH` and
  `MU2E_SEARCH_PATH`.
- **The tarball is tiny, and that is expected:** 2.8 KB, no `Code/build/*/lib`.
  The size check from [[reference_muse_tarball_silent_platform_mismatch]]
  applies to builds with compiled code, not here. Check with `tar -tjvf`
  instead: `muse tarball` exits 0 on tar errors.
- **A bfgeom file can `#include` another and redefine a key.**
  `bfgeom_DSOff_s30.txt` = `#include bfgeom_DSOff.txt` + `bfield.scaleFactor = 0.3`.
  Offline's `BFieldTest` module confirmed it: on the TS1 axis 2.474 T
  becomes 0.742 T.
- **Never overwrite a tarball a campaign uses.** The cnf records `code_ref`
  (sha256), and every slice and recovery re-hashes the file at the entry's
  path. Put a rebuild at a new path, under a new dsconf.

## Getting it to the grid with outputs declared
- `push_cnf` used to refuse every code entry. Mu2e/prodtools#81 (merged
  2026-10-21) accepts them for `run_as="self"` and still refuses mu2epro.
  Before that, the only MCP route was `submit_once`, which declares nothing.
  Its outputs stay on outstage, so the next stage cannot read them as a SAM
  dataset.
- The CLI `json2jobdef --prod --enqueue` always accepted code entries, but
  the Claude Code auto-mode classifier blocked it as a production deploy, even
  for a self campaign.
- With #81 the study ran as ordinary self campaigns: 52 (`TS1Beam`, 500 jobs ×
  2e4 POT, 500/500 declared), 53 and 54 (`TS1ToTS5s100` and `TS1ToTS5s30`,
  50 jobs each, merge 10), dsconf `MDC2025ay-001`. The config is
  `data/Run1B/ts30.json`.
- Prodtools' local smoke (`run_local` on a 2-job outstage entry, 2 × 5000 POT)
  gave the same total as a plain `mu2e -c` run of the same fcl: 1418 events
  with particles at TS1. The per-job counts (697 and 721) came out in the
  opposite seed order, which was not investigated. The job fcl and field came
  from the unpacked tarball.

## Physics caveat (for whoever reads the numbers)
`bfield.scaleFactor` scales every map, the PS map included. Stage 1 therefore
records particles at VD Coll1_In (z = −4004 mm, still about 0.3 m inside the
PS coil end) with the nominal field. Stage 2 starts there with everything at
30%. The real PS-100%/TS-30% field would fall off gradually through TS1, so
the abrupt step biases the yield low. `POT_TS1out.fcl` (score at Coll1_Out)
is the other bracket. Scoring at TS3 is not usable: the TS2 bend drift (∝ p/B)
is where the reduced field loses the muons.

## Result (2026-10-21, 1e7 POT)
μ⁻ entering DS2Vacuum (the TS5 exit) per POT: **5.24e-4 at 100%** (5235 μ⁻). This matches the DS-off one-stage 5.3e-4 from DocDB 55033, so the two-stage split reproduces the one-stage POT result. At **30%: 2.0e-7** (2 μ⁻; 68% 0.7–4.6e-7). The ratio is 3.8e-4 (68% 1.4e-4 to 8.9e-4), about 2600× lower. The surviving μ⁻ are 10–17 MeV/c, against a median of 45 MeV/c at 100%; e⁻ drop only 23%. This is a low estimate because of the abrupt field step at Coll1_In. The Coll1_Out bracket has not been run.

## Field scan 30–100% (2026-10-21)
The same stage-1 sample restarted at each scale (campaigns 55–60, code tarball v2). μ⁻ per POT at the TS5 exit: 30% 2.0e-7, 40% 5.6e-6, 50% 2.8e-5, 60% 7.3e-5, 70% 1.4e-4, 80% 2.4e-4, 90% 3.8e-4, 100% 5.2e-4. The median μ⁻ momentum falls with the field, from 45 to 13.5 MeV/c. The high-momentum edge is cut first. Below about 20 MeV/c the spectra coincide down to 40%; 30% loses even those.
