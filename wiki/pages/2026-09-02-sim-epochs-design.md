---
title: Sim epochs — design proposal (derive, don't curate)
tags: [decision, sim-epochs, catalog, retirement, mcp, metacat, provenance]
sources: [slack-dm-ray-2026-07-30, slack-dm-ray-2026-09-02, slack-groupdm-sophie-2026-07-13, sim-epochs-demo-mcp-2026-02-19]
updated: 2026-09-02
---

# Sim epochs — design proposal

**Status: design settled internally 2026-09-03 (Yuri + Claude grill,
19 questions, section 17); not yet agreed with Ray.** Drafted 2026-09-02
after the EventNtuple calo-hit remake (see
[[2026-09-02-eventntuple-v0200-calo-hits-cluster-gated]]). Sections
marked "decided" or "grill" override the original text where they
differ. Glossary: `CONTEXT.md` (Epoch, Root, Input, Family, Sibling,
status, Hold, Gap, Generation). ADRs 0003 and 0004.

## 1. Where the idea comes from

- **2026-02-19** — Ray built a demo MCP server, `sim-epochs`, in about
  1.5 h: a JSON catalog `{"epochs": [{"name", "datasets"}]}` and two
  tools, `get_simulation_epochs()` and `get_datasets_for_epoch(epoch)`.
  Fake data. His framing: "the work is in designing what you want it to
  say and deliver." Deployed at
  `/exp/mu2e/app/users/mu2epro/mcp/deploy/sim-epochs/releases/0.1.0/`.
- **2026-07-13** — Group DM with Sophie. Ray adopted Simon's idea of sim
  "epochs" as "a bunch of datasets produced at once for a purpose, which
  should probably be retired together." Ray: "only you can define the
  epochs ... I can't responsibly define 'not the latest'. Give me a
  dataset list, or an algorithm that I can apply to produce a list."
- **2026-07-30** — Yuri proposed four-letter epoch codes, one letter per
  tier (`aaaa` → `abaa` when dig/mcs/nts are remade → `abba` when mcs is
  remade again). Ray's objection: the letters look too much like code
  versions, a fixed four steps is too constraining, and "have you made a
  list of use cases?"
- **2026-09-02** — Sophie is asking for sim-epochs. Ray's requirements:
  "a machine-ready system for understanding what datasets exist, what is
  their content and purpose, what is the difference between versions,
  what technically works with what, and what are conceptually related.
  The epochs part is what can be managed together as a logical set, for
  retiring, for example." Yuri asked what happens when a single tier is
  remade (dig → mcs → nts): a new epoch? Two overlapping epochs? Ray:
  "best answered through the requirements."

Three "current dataset" lists already exist and drift:
`docs/latest_datasets.md` (generated 2026-08-06, did not know today's
`-001` names when Ray looked), the wiki page
`MDC2025#Current_MDC2025_datasets`, and `bin/latestDatasets`. That drift
is the pain Sophie and Ray are feeling.

## 2. Requirements (Ray's list, restated as queries)

1. What datasets exist, and which are current.
2. What is the content and purpose of each.
3. What is the difference between two versions.
4. What technically works with what (reco musing vs dig, EventNtuple
   version vs analysis code).
5. What is conceptually related, so it can be retired as one set.

## 3. Core principle: derive, don't curate

Parentage already exists in metacat: dts → dig → mcs → nts. A person
supplies only what a machine cannot know: an epoch's name, a one-line
purpose, a status, and the root patterns. Everything else is computed.
Nobody assigns letters, so the four-letter objection disappears.

**Correction (grill, 2026-09-02):** the cnf tarball is NOT a declared
parent of any output. `samweb file-lineage parents` on an mcs file
returns only its dig; an nts and its log return only the mcs. Musing,
Offline version, fcl and conditions are therefore not reachable from
parentage. They live in the submission ledger, in the cnf found by the
`cnf.<owner>.<desc>.<dsconf>.0.tar` name convention (which fails for
generic `evnt` cnfs, whose desc is not the output's desc), or in the
text of the log file. The generation attribute (section 9) depends on
one of those three paths being chosen; it is not free.

## 4. Data model

One file per epoch in git, `data/epochs/<name>.json`. **Granularity
(decided 2026-09-03): one epoch per dig campaign-letter family**, named
by the letters, auto-proposed by the tool whenever a letter family has
no file yet; a person fills `purpose` and later flips `status`. Merging
families by hand was rejected: status is family-wide anyway, so the
only remaining job of an epoch is to be retired as a unit, and one
campaign is that unit. MDC2025 has five today (ad, ai, an, ap, au).
Note the mcs letters track the RECO musing (`mcs@ar` are `an` digs
re-reco'd; no `ar` digs exist), so the epoch name is not the reco tag.

```json
{
  "name": "MDC2025au",
  "purpose": "Run-1 nominal geometry, Sim_best conditions, Offline v13_35",
  "status": "current",
  "roots": [
    "dig.mu2e.%.MDC2025au_%.art"
  ],
  "expected_tiers": ["mcs", "nts"],
  "pins": {
    "exclude": [],
    "frozen": [],
    "order": {}
  }
}
```

- `roots` are 5-field SAM patterns. Tier-agnostic in principle, **dig by
  convention** (section 6). A root wildcards purpose and conditions
  version both (`dig.mu2e.%.MDC2025au_%.art`): a point-fix `v1_3`→`v1_5`
  is absorbed with no edit, and `best`/`perfect` digs (real in MDC2020,
  e.g. `RMCExternalOnSpillTriggerable.MDC2020ar_{best,perfect}_v1_3`)
  are one epoch, told apart by the group key. A current set spans
  several letter families, so roots are a short list, not one.
- `status` ∈ {current, frozen, retired}: current = being worked on;
  frozen = a hold on every member, nothing expected to change, no gap
  report; retired = every member is a delete candidate. **A freeze also
  holds the epoch's inputs** (decided 2026-09-04, **deferred**: input
  retirement itself was removed from the branch before this could be
  verified safe on production data — §17 row 20, ADR 0005 — so no input
  hold is implemented today; the rest of this bullet records the design
  intent for the rebuild, not current behavior): an input is held when
  every dig it feeds is a member of a frozen or retired epoch and at
  least one of them is frozen. Freezing Run1Bah means "nothing here gets
  deleted", and its digs are normally superseded — a newer letter is why
  it was frozen — so without this its dts were retire candidates. An
  input that also feeds a current epoch's dig is judged by that line's
  status as before; one feeding only retired epochs stays retirable.
- `pins` are the escape hatch for the rare case the rule gets wrong:
  exclude a dataset, hold one dataset or generation as a reference,
  override a dsconf ordering inversion. Every pin carries a free-text
  `reason` that reports print verbatim. **A pin of any kind that matches
  nothing is a refusal, not a no-op** (2026-09-04): an `exclude`/`hold`
  naming an unknown dataset, an `order` pin whose group no member
  competes in, a `not_expected` pin naming a desc no dig of the epoch
  has — each is reported and makes `retire` refuse, because a protection
  the human believes they placed and does not have is how a live dataset
  reaches a delete list.
- **Renames are manual (Yuri, 2026-09-03).** There is no `supersedes`
  relation across desc names and no inference: `RMCExternalOnSpill@ar`
  replaced by `RMCPhaseSpace*@au` stays current until a person writes
  an `exclude` pin with a reason. A rule for it was judged too loosely
  defined; corner cases are handled by hand.

Members are computed, never listed by hand.

## 5. Membership rule

1. **Members = parentage closure of the roots**, walking children in
   metacat. dts (and anything above the roots) are conceptually
   `inputs`, not members — **not tracked by the code in this version**
   (input retirement removed 2026-09-04, §17 row 20; ADR 0005): the
   catalog no longer records a separate collection for them, and a
   lookup on one reads as `unknown`, the same as a name the catalog
   never saw at all.
2. **Group key = (family, tier, desc, purpose)** for a lettered member,
   where family is the leading dsconf token (`MDC2025`, `Run1B`). Status crosses epochs
   (grill, 2026-09-03): `CeEndpointOnSpill@ar` and `@au` are in
   different epochs and must still compete, or Sophie gets two current
   answers. `CosmicCalibOnSpill@ar`, with no `au` sibling, stays current
   and is reported as "current member of a non-newest epoch". An epoch
   is retirable when it has no current or stale member. Membership
   never overlaps; only status looks across epochs.** `purpose` is the token
   in the dsconf grammar `<letters>_<purpose>_v<N>_<M>[-NNN]` (`best`,
   `perfect`, ...). `best` and `perfect` reco of the same mcs are
   siblings, not versions of each other. The parent is deliberately NOT
   in the key (grill, 2026-09-02): RMC primaries remade `af`→`ap` and
   re-digitized under the same letters must supersede the old dig, and
   an nts re-made from `mcs@au_best_v1_5` must supersede the one from
   `mcs@au_best_v1_1`. With the parent in the key both would stay
   current. Parent is used only for status propagation (rule 4). A
   `purpose=None` member — the dead own-series `MDC20xx-NNN` convention,
   rule 3 — has no purpose to key on, so it is NOT a plain 4-tuple key:
   it competes in EVERY purpose group of its `(family, tier, desc)` and
   ranks lowest in each (implemented 2026-09-04, `status.groups`), and
   with no lettered sibling at all it keeps a group of its own and stays
   current. Keying it strictly on the 4-tuple gave it a group nothing
   could ever supersede it in, and the 2026-09-03 run published two
   simultaneously current answers on 15 `(family, tier, desc)` lines.
   Because it competes in several groups, winning ANY of them makes it
   current — which is what an `order` pin naming it is placed to do.
   Accepted consequence (2026-09-04): an `order` pin naming an
   own-series member in ONE purpose group leaves it current in the other
   purpose groups of that base triple as well, so those groups show two
   current members — the pinned own-series one and their own lettered
   winner. A hand-placed pin is a person saying "keep this"; keeping it
   is the fail-closed direction for a tool whose output is a delete
   list. The rule's alternative, last-write-wins over the groups dict,
   was the NEW-5 bug.
3. **Within a group, newest dsconf is `current`, older siblings are
   `superseded`.** "Newest" is PARSED dsconf order, never a clock:
   grammar `<family><letters><rev?>_<purpose>_v<N>_<M>[-NNN]`, compared
   as (letters as base-26, revision digit, N, M, NNN). This fixes the
   two measured lexical failures (`Run1Bab` vs `Run1Bab2`; `-` vs `a`).
   Time is used NOWHERE, not even as a diagnostic (Yuri, 2026-09-03:
   timestamps are not reliable; the `NeutralsFlashCat.MDC2025ac` 40-file
   top-up that postdates `ad` is the standing example). File count is
   the one diagnostic: the tool warns when the winner has far fewer
   files than a sibling, the signature of a top-up masquerading as a
   version, and a pin resolves a warned case. The own-series `MDC2025-NNN` ntuple convention is dead
   (no new datasets, per Yuri 2026-09-02); it parses as "no letters" and
   ranks below any lettered sibling, which is the right answer for the
   legacy sets.
4. **Status flows down the parent chain, with THREE states** (grill,
   2026-09-03). `current`: newest in its group and parent current.
   `stale`: newest in its group but parent superseded or stale; still
   the dataset to run on, a remake is owed, never retired. `superseded`:
   a newer sibling exists; the only retire candidate. Two states would
   have put the old nts on the retire list the moment its mcs was
   remade, before any replacement nts existed. Remaking an mcs makes its
   old nts `stale`, and `superseded` only once the new nts lands.
   **Holds are not a status.** `frozen` is a human protection against
   deletion, on one dataset (paper reference) or on a whole epoch
   (Run1B frozen campaigns), orthogonal to status: a held dataset can be
   superseded for new work and undeletable at once. Retire list =
   superseded AND not held, plus every member of a `retired` epoch.
5. **The same rule applies at the root level.** A dig point-fix (same
   desc, `v1_3` → `v1_5`) supersedes inside the epoch. A new epoch file
   is written only when we actually re-digitize on new geometry,
   conditions or code, which in practice is when the campaign letters
   change. This is the same judgment already made when choosing to bump
   the letter versus the conditions version.
6. **Deferred (removed 2026-09-04, §17 row 20; ADR 0005): input
   retirement is not implemented in this version.** `retire()` reports
   member candidates only. The rule below is the ORIGINAL design intent,
   kept as the record of what a rebuild in a separate branch targets —
   read it as history, not current behavior. **Input retire rule
   (derived, generalized 2026-09-03):** any input
   above the roots (dts, stop and pileup catalogues, at any depth) is
   retirable when no `current` or `stale` member descends from it, and
   it is not held. An `excluded` member counts as a live descendant
   (2026-09-04): the tool refuses to delete it, so it must not propose
   deleting what made it.

   "No `current` or `stale` member descends from it" is decided by ONE
   relation over EVERY parentage edge the catalog holds (2026-09-04,
   round 4). The catalog records the same DAG several ways —
   `Member.parents`, `Member.inputs` (the parents of a downstream member
   that no walk claimed), `cat.inputs[x]['parents']`, the dig
   `descendants` sets — and reading the `descendants` set alone, which
   names only the dig each upward walk started from, proposed deleting
   an input that a `current` mcs below a superseded dig consumes. Every
   fix before round 4 patched that one path while the other edges kept
   their own route to the same false DELETE.

   The upward walk runs to natural closure by default (2026-09-04), so
   no CAP cuts it; `--input-depth N` survives only as an explicit opt-in
   cap for a cheap partial run. No cap is not a complete graph: the cap,
   an unparseable dsconf above or below a member, a non-`mu2e` owner in
   the lineage and a dropped tier all sever a walk identically. All of
   them go in one register, and while it is non-empty the retire report
   proposes NO input at all — the same fail-closed refusal it already
   makes on an incomplete catalog. That replaced four rounds of
   per-input truncation detectors: each was correct for the shape it was
   shown, and the next review found another shape of incompleteness
   reaching the same false DELETE.

   **Expect the input section to be refused on real data.** 30 legacy
   dsconfs did not parse in the 2026-09-03 pre-flight, so a production
   `epochs retire` will very likely report members only. That is the
   correct outcome, not a bug to design around, and there is
   deliberately no flag to bypass it; the remedy is to make the names
   parseable (a dsconf grammar extension is tracked separately). Guard, letters only, no clock: an input whose
   dsconf letters outrank the family's newest epoch (a fresh `dts@av`
   awaiting its mix while `au` is newest) is never listed.

**The test for "new epoch":** did truth content change? Geometry,
pileup, generator, Offline at the G4 step are truth → new epoch. Reco
code, reco conditions, ntuple format are processing → same epoch,
superseded member.

## 6. Why roots are dig, not dts

- dig is where the epoch is actually decided: geometry, conditions,
  pileup mix and Offline version all converge at digitization. The dig
  dsconf is already the label people use ("au_v1_5").
- dts are shared across generations by design. Rooting at dts makes
  every re-digitization overlap with the previous epoch. Rooting at dig,
  each dig belongs to exactly one epoch, so overlap stops being a
  question.
- Compatibility binds at dig: VD enum shift, CRV era, DbService version
  all key on the dig's Offline version.
- Clean remake line: re-reco or re-ntuple from the same dig is the same
  epoch; re-digitize is a new one.

Caveats, all handled:

1. Not everything has a dig (MDC2020 unmixed lives only at dts; STM and
   g4bl chains have their own tiers). Roots are tier-agnostic patterns;
   a dts-only study is an epoch rooted at dts.
2. One current set spans several dig dsconfs (today: `au_best_v1_3`
   Mix1BB, `au_best_v1_5` OnSpill, `an_best_v1_1` CosmicSignalOffSpill).
   Roots are a short list; that list is the human-curated part.
3. Point-fix dig remakes are superseded members, not new epochs (rule 5).

A discipline the rule imposes: a reco remade with a different
configuration but the same physics must carry a different desc (`-KK`,
`-CH`, `-reco`) or it silently supersedes the other. That is already the
naming convention; the rule makes it load-bearing.

## 7. Remake cases

| What is remade | Result |
|---|---|
| nts from the same mcs | new nts child becomes current, old nts superseded. Same epoch, no list edit. |
| some mcs from the same dig | new mcs current, old mcs AND its nts superseded (rule 4). New mcs has no nts yet → **gap** until re-ntupled. Current set becomes heterogeneous in dsconf; that is normal. |
| dig (new geometry / conditions / code) | new epoch file. Old epoch's status set by a human (frozen or retired). |
| dig point-fix, same desc | superseded within the epoch (rule 5). |

Today's case: `nts.*.MDC2025au_best_v1_5-001` (EventNtuple v02_01_00)
is a new child of the same mcs, becomes current, `nts.*.MDC2025au_best_v1_5`
(v02_00_00, calo hits gated behind clusters) becomes superseded and
lands on the retire list. Same epoch, nothing edited. `-001` is a
collision suffix, not a version of anything.

## 8. Ntuple format is an attribute, not an epoch

Ntuples have a second axis: the EventNtuple release that wrote them.
Analysis code compiles against one and reads only that. That is a
"works with" question, orthogonal to the sim epoch: the sim epoch says
what physics is in the file, the format says who can open it.

- Per nts member, `format: AnalysisMDC2025/v02_01_00`, read off the
  cnf's musing. Queries take it as a filter.
- `current` stays newest per group. Do not make it per format. An
  analyzer pinned to an old format asks for it explicitly and gets the
  superseded member, labeled as such. Ntuples are the cheapest tier to
  regenerate; a per-format retention policy is not worth its complexity.
- The diff must name the format or the retire list is unreadable.
  "Retire `nts.*.au_best_v1_5`, keep `-001`" says nothing. "Superseded
  by `-001`: EventNtuple v02_00_00 → v02_01_00, calo hits fix" is the
  line Ray and Sophie need.

Naming decision (Yuri's): two ntuple dsconf conventions coexist, the
older own series `MDC2025-NNN` (effectively a hand-rolled format axis)
and the newer "inherit the reco dsconf plus a suffix". Under
derive-don't-curate the name need not carry the format. If names should
be readable without the catalog, the suffix should say what changed;
`-001` says only "second attempt".

## 9. Generation attribute and consistency report (no per-tier epochs)

The real hazard per-desc "current" leaves open: CE at `au_best_v1_5-001`
(new reco) and DIO at `au_best_v1_5` (old reco) are both current with
different processing. A sensitivity study needs signal and background
reco'd the same way.

This is not a second epoch. An epoch needs a human because roots are a
choice; a processing generation is fully determined by the cnf and is
therefore an attribute:

- `generation` per member = musing + conditions off the cnf. For mcs
  roughly `SimJob/MDC2025au + Sim_best/v1_5`; for nts
  `AnalysisMDC2025/v02_01_00`. Same field every tier. **Source
  (decided 2026-09-03, ADR 0003):** the cnf becomes a declared SAM
  parent of every output going forward (one line in `push_data`), and
  everything else resolves through a cnf INDEX built once from every
  cnf's own `tbs.outfiles` templates — a template without `{desc}`
  claims its dataset, a generic one claims every dataset of that tier
  and dsconf no explicit cnf claims. **Logs are not read** (ADR 0003
  rejects that route outright: a generic entry names the log dataset
  after its input, so two output generations share one log dataset).
  Both routes end at the cnf; `jobquery` does the rest.
- Query: `epoch_members(tier=mcs, generation=G)` gives a uniform set.
- Report (confirmed 2026-09-03, report only, never a status): "current
  mcs spans 2 generations: G1 on 18 descs, G2 on 3 descs", with the
  descs on the minority generation named. Marking the minority members
  "inconsistent" was rejected: a good dataset would be flagged for what
  its neighbors did not do yet. A partial remake is visible
  as an inconsistency. Today: "nts format v02_01_00 covers 21 of 48
  descs", the other 27 named. Nobody keeps the pasted list.
- The one human case: two generations both legitimately current (a reco
  frozen as a paper's reference while a newer one is default). That is
  a `frozen` pin on the generation, not a new epoch.

The hierarchy stays flat: one epoch rooted at dig; below it, members
with a status, a generation, and a parent.

## 10. Derived products (one `bin/epochs` command)

- `members` per tier / status / generation / format. Replaces
  `latest_datasets.md`, the wiki table, and hand-pasted lists.
- `gaps` (decided 2026-09-03): every dig desc is expected to reach mcs
  and nts, no history lookup; a desc that legitimately stops early
  gets a per-desc `not_expected` pin with a reason. Report = expected
  tier with no member, plus every `stale` member. Sizing at `au`: one
  gap today, `ensembleMDS3bOnSpill` dig with no mcs. Would have flagged
  `dig.mu2e.CosmicCRYExtracted.MDC2025au_best_v1_5` with no nts, which
  nobody noticed until 2026-09-02.
- `retire` (decided 2026-09-03): superseded-and-not-held members, plus
  every member of a `retired` epoch, plus dts with no current or stale
  descendant, emitted in Ray's exact `purge_proposal` line format
  (`VERDICT created last-access has-children nfiles dataset children|NONE`,
  see `/exp/mu2e/app/home/mu2epro/rlc/mcscripts/purge_proposal_260729_3.txt`)
  with a trailing `# reason` field: the superseding dataset and the
  generation diff, or a pin's reason verbatim. Last-access is left as
  `-` for his tool: we use no timestamps. Division of labor: our list
  replaces "Yuri's judgement" as the source of a purge round; his
  3-year-access and no-active-child gates stay on top.
- `provenance` per member: musing, Offline version, fcl, conditions,
  from the cnf via `jobquery`. Answers "what works with what" without a
  hand table, since incompatibilities (VD enum shift at v13_36_00, CRV
  era) key on Offline version.
- `consistency`: generation spread per tier (section 9).
- `diff(epoch_a, epoch_b)` and `diff(member, superseded)` (decided
  2026-09-03): mechanical fields from the two cnfs, always present:
  musing and version from the setup path, Offline version read from
  that musing, fcl checksum, and named fcl values (DbService version,
  geometry file, bfield file). Plus an OPTIONAL human `notes` pin in the
  epoch file keyed by dataset pattern, one sentence written when the
  remake is launched ("EventNtuple calo hits were gated behind
  clusters"). The retire line carries both.

## 11. Serving

**Decided 2026-09-03 (ADR 0004):** curated epoch files in prodtools git;
derived catalog written by `bin/epochs` to Ray's `sim_catalog.json`
path (his server re-loads the file on every call, so no code change on
his side); rich queries as tools on the read-only `prodtools` MCP;
nothing derived is ever written to SAM or metacat.

**Cadence (decided 2026-09-03): on demand only, no hook, no cron.** A
person runs `bin/epochs publish` when a snapshot matters (before a
purge round, after a campaign completes, when asked). Coupling it to
`submissions run` was considered and rejected: membership changes when
files land in SAM, hours after a tick and also from jobs the ledger
never saw, so a tick is not the event. The MCP tools compute live from
SAM on every call and never read the file; the file exists for Ray's
server alone and carries `generated_at`.

Emit the exact shape Ray's demo parses, `{"epochs": [{"name",
"datasets"}]}`, with the extra fields alongside so his server reads it
unchanged. Add the rich queries as tools on the read-only `prodtools`
MCP: `list_epochs`, `epoch_members`, `dataset_epoch`,
`retire_candidates`, `epoch_diff`, `epoch_gaps`. Hosting is Ray's call
(central MCP hosting at mu2eai@mu2eaigpvm01); the file format is the
contract.

## 12. Production hook (deferred, decided 2026-09-03)

Not in phase 1 or 2. Built only after one real round has been planned
by reading the catalog by hand, so the entry key encodes a query we
have actually needed. Constraint it imposes now: the catalog must stay
importable without the ledger (it reads only SAM and the cnf).

A campaign entry gets

```json
"input_epoch": {"name": "MDC2025-nominal", "tier": "mcs", "stream": "OnSpill"}
```

and `json2jobdef` resolves it to an explicit dataset list. This is the
include-list primitive; `input_pattern` + `exclude_desc` remains the
fallback for datasets outside any epoch. After a campaign lands,
membership re-derives itself; no list to update.

## 12b. Scope (decided 2026-09-03)

- Owner `mu2e` only; user-owned datasets never enter the catalog.
- Families discovered from every 5-field `dig.mu2e.*` dataset whose
  dsconf parses as `<family><letters>...`; unparseable dsconfs are
  reported, never guessed.
- Generation backfill: MDC2025 and Run1B. MDC2020 gets status and holds
  but no generation backfill: it is mid-purge, still analyzed, never
  remade.

## 13. Phases

1. DONE 2026-09-03 (branch sim-epochs, commits a8dadb7..7bd03a8):
   `bin/epochs` with parser, closure, status, gaps, consistency, count
   warnings, retire, publish; `data/epochs` seeded for MDC2025 (5),
   Run1B (10) and MDC2020 (11) letter families (MDC2020r has a
   definition but no files); cnf index built (1343 cnfs, 1668 explicit
   claims, 13 generic).
2. MCP tools; `retire` in purge format; `gaps`; `consistency`.
3. `input_epoch` in `json2jobdef`; compatibility notes on provenance;
   `diff`.

## 14. Open decisions (human, not derivable)

All four resolved in the 2026-09-03 grill (see section 17):
- Epoch granularity: one per dig letter family.
- Run1B frozen campaigns: one epoch per Run1B letter family, each `frozen`.
- Ntuple dsconf naming: `-NNN` stays a bare collision counter; meaning
  lives in the catalog (generation from the cnf, reason from a `notes`
  pin).
- Identity: roots = dig, purpose and version wildcarded. Geometry +
  conditions become a derived attribute once the cnf is a parent (ADR
  0003), not the identity. Still the part Ray may push on.

## 15. Use case to send Ray (his 2026-07-30 ask)

"One tier remade for a bug fix: EventNtuple v02_00_00 gated standalone
calo hits behind cluster filling, so sub-threshold samples (1809 keV)
lost the whole calo block. Re-ntupled 21 datasets under v02_01_00 as
`nts.*.MDC2025au_best_v1_5-001`. Epoch keeps its identity, one member
per desc replaced, old members retired. Meanwhile 27 other descs are
still on the old format, and `CosmicCRYExtracted` never had an nts at
all."

## 16. Slack draft — membership rule for Ray (rewritten 2026-09-03)

```
Re sim-epochs. I now have a rule instead of a list. Before anyone builds anything, does it hold up against your use cases?

An EPOCH is one digitization campaign, named by its letters (MDC2025au), found automatically from the dig dataset names. A person adds one sentence of purpose and a standing: current, frozen, or retired. Members are everything downstream of those digs by SAM parentage. dts and the stop/pileup catalogues are inputs, shared, not members.

STATUS is per dataset, computed, never stored: within a family (MDC2025, Run1B, ...) and across its epochs, for each (tier, desc, purpose) the newest dsconf is "current", older siblings are "superseded", and a dataset whose parent has moved on but which has no replacement yet is "stale": still what you run on, remake owed, never deleted. Newest means parsed dsconf order; no timestamps anywhere, they have bitten us both ways.

FROZEN is a hold, not a status: a paper's reference reco is superseded for new work and still undeletable. Holds go on one dataset or a whole epoch (the Run1B campaigns).

What falls out: the current-datasets list Sophie wants; a gap list (a dig with no mcs or nts, or a stale member); a consistency list (current mcs on two Offline versions, which descs); and a delete list in your purge_proposal line format with a trailing reason ("superseded by ...-001: EventNtuple v02_00_00 to v02_01_00, calo hits fix"). Inputs (dts, catalogues) are deletable when nothing current or stale descends from them. Your 3-year and no-active-child gates stay on top; renames (RMC to RMCPhaseSpace) stay a manual exclusion with a reason, no inference.

One change on the production side: the cnf tarball becomes a declared SAM parent of every output, so "what code made this" is one parents lookup like "what data went in". Existing datasets get it backfilled from the cnf name in their logs.

Output is the same {"epochs":[{"name","datasets"}]} file your server reads, extra fields alongside, written on demand; the live queries sit on the read-only prodtools MCP.

Your overlap question: two epochs never overlap in membership (a dig has one set of letters), and status looks across them, so CeEndpoint@ar is superseded by CeEndpoint@au while CosmicCalib@ar, never remade, stays current and keeps the ar epoch open until someone decides.

The part I expect you to push on: roots = dig rather than geometry+conditions. With the cnf as a parent, geometry+conditions become a derived attribute of every member, so I think dig is the right handle and the tuple is a query, not an identity.
```

## 17. Grill decisions, 2026-09-03

| # | Question | Decision |
|---|---|---|
| 1 | Primary output | Per-dataset status, derived. Epoch is a grouping on top. |
| 2 | Epoch identity | Dig root patterns, purpose and version wildcarded. |
| 3 | Group key | `(family, tier, desc, purpose)` for a lettered member; parent NOT in the key. A `purpose=None` (own-series) member competes in every purpose group of its base triple and ranks lowest in each. |
| 4 | "Newest" | Parsed dsconf order. No timestamps anywhere. File count is the one warning. |
| 5 | Status set | current / stale / superseded. Stale = newest but parent moved on; never retired. |
| 6 | Generation source | cnf declared as SAM parent (ADR 0003); everything else via the cnf index built from `tbs.outfiles`. Logs are NOT read. |
| 7 | Status scope | Across epochs within a family. Epoch retirable when no current/stale member. |
| 8 | Renames | Manual `exclude` pin with reason. No `supersedes` inference. |
| 9 | Granularity | One epoch per dig letter family, auto-proposed. |
| 10 | Storage/serving | Curated files in git; derived catalog to Ray's path; live tools on prodtools MCP; nothing stored in metacat (ADR 0004). |
| 11 | Gaps | Every dig desc expected at mcs and nts; `not_expected` pin with reason. |
| 12 | Retire output | Ray's purge_proposal line format + trailing reason; his gates stay on top. |
| 13 | Inputs | Retirable when nothing current/stale descends; letters-only guard for fresh inputs. |
| 14 | Scope | Owner mu2e only; families discovered; backfill MDC2025 + Run1B, MDC2020 status-only. |
| 15 | Cadence | On demand only. No hook on `submissions run`, no cron. |
| 16 | Production hook | Deferred until one round has been planned from the catalog by hand. |
| 17 | Diff | Mechanical cnf fields + optional human `notes` pin. |
| 18 | Consistency | Report only, never a status. |
| 19 | `-NNN` suffix | Bare collision counter; meaning lives in the catalog. |
| 20 | Input retirement | Removed 2026-09-04; rebuilt in its own branch as positive proof over the downward closure. |

**Row 20, one paragraph.** Rule 13's input retirement (row 13 above) was
a negative search: walk upward from each root, propose deleting
whatever no live member's reachability was found to reach. Across five
review rounds that produced eight false-DELETE Criticals and three
"this is now structural" claims each broken by the next round, because
a negative search over a best-effort traversal always has an unwalked
frontier somewhere — hardening the detector for one shape of
incompleteness (a depth cap, an unparseable dsconf, a foreign owner)
kept leaving another shape (a downward-walk edge never expanded
upward, a dropped dig definition) for the next reviewer to find. It is
removed rather than hardened a sixth time (ADR 0005) and will be
rebuilt in its own branch on the inverted design: prove an input dead
by enumerating its full downward closure and requiring every node in
it to be a known member that is superseded or in a retired epoch —
any unknown, unparseable, foreign, held or live node blocks the
proposal. `retire()` reports member candidates only until then.

Follow-ups outside this design: the log dataset for generic entries is
named after the INPUT (`log…MDC2025au_best_v1_5.log` for the `-001`
ntuples), mixing two generations' logs; `famtree.get_parents` needs a
`cnf.*.tar` filter once ADR 0003 lands; `push_data` one-line change;
the dsconf grammar could accept a legacy `_vNN_NN_NN` Offline tail
BEFORE the purpose so the MDC2020an nts/bck names parse; a `members`
row for MDC2020 has no generation until the POMS-era cnf claim
question is answered.

## 18. First live run, 2026-09-03

- The full catalog builds to 26 epochs, 396 dig datasets and 1013
  members: 770 current, 14 stale, 229 superseded.
- The retire list runs 49 lines: 26 superseded members and 23 inputs
  (dts of Run1Baa, Run1Bag and Run1Bai, whose digs are all
  superseded), and 0 lines from a retired epoch.
- The MDC2025 nts tier sits on three generations at once
  (AnalysisMDC2025 v01_01_01 on 4 `-KL` descs, v02_00_00 on 27,
  v02_01_00 on 26) — this is the consistency report Ray asked for.
- Five count warnings, all in-flight remakes:
  `dig.mu2e.NoPrimaryMix1BB.Run1Bav_best_v1_5-003.art` at 2000 of
  20000 files, `NoPrimaryMix1BB-KL.Run1Baw_best_v1_5-002` at 1998 of
  20000 at both the mcs and the nts tier,
  `nts.mu2e.CosmicSignalMix1BB.MDC2025au_best_v1_1-001.root` at 100 of
  500, and
  `nts.mu2e.RPCExternalOnSpillTriggered.MDC2020az_perfect_v1_3_v06_06_00.root`
  at 200 of 2000.
- The 14 stale members are all legacy `-KL` reco/ntuple lines under
  Run1Bab2 and Run1Bah, plus the Run1Baw NoPrimaryMix1BB `-002` pair
  whose parent has moved on.
- 17 dig definitions do not parse: the `MDC2024_perfect_v1_3`
  hyphenated ensemble names, and one `pipenu` name.
- 27 distinct member or input names do not parse: the MDC2020an
  nts/bck names carry the Offline version before the purpose
  (`MDC2020an_v06_01_01_perfect_v1_3`), and two ancient inputs
  (`sim…010622`, `dts…v5hi1`) predate the current grammar entirely.
- 24 cnf tarballs are unreadable from a gpvm — 22 permission denied,
  2 missing on scratch — and are listed by name in `cnf_index.json`
  under `__unreadable__`.
- MDC2020 members come back mostly `generation: unknown` (dig 141 of
  243, mcs 79 of 98, bck 44 of 44, nts 23 of 49). That is consistent
  with decision 14 (MDC2020 is status-only), but it is worth checking
  separately whether POMS-era cnfs even carry `tbs.outfiles`.
- Four digs carry a bare dsconf with no letters-and-purpose split
  (`MDC2020aq` twice, `MDC2020aw`, `MDC2025ad`), which is why proposed
  roots now come in three distinct patterns instead of one.
- Timings: a full catalog build takes about 7 minutes, `index-cnfs`
  about 3.5 minutes, and every individual SAM query is sub-second. The
  earlier 10-minute stall traced to un-memoized ancestor walking, not
  to SAM itself.
- **2026-09-04, for the record:** the "23 inputs" line above (this
  run's retire list) and the "27 distinct member or input names do not
  parse" line both describe input retirement, which was removed the
  same day (§17 row 20; ADR 0005). Numbers on this page are left
  exactly as measured — nothing here is corrected or re-run — but a
  retire list built with today's code reports 26 superseded members and
  no input lines at all, from this same catalog.
