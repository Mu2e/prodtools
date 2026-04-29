# POMS SAM-dataset / map wiring — agent fetch from github.com/fermitools/poms

**Source:** https://github.com/fermitools/poms (research 2026-04-28)
**Method:** WebFetch + GitHub API
**Agent slice:** how POMS picks the SAM dataset for a stage

⚠️ **Editor's note:** This agent's findings include some confusion between POMS internals and Mu2e tooling — specifically, it cited `prod_utils.py:create_index_definition` (which is OUR tool, mkidxdef.py's helper) as a "POMS hardcoded convention". The truth is `i<map_stem>` is the Mu2e mkidxdef convention; POMS itself takes a SAM def name from a `CampaignStage` field configured per-stage. Treat the operational answer below with that caveat. Cross-reference with the [user-ops research](2026-04-28-poms-user-ops.md) which found `poms_client.update_campaign_stage()` — proving stages ARE configurable.

---

## Question driving the research

We have a Mu2e workflow where POMS scans a dropbox for `<map>.json` files and dispatches against a SAM def named `i<map_stem>`. Where does this mapping happen — in POMS's launcher, or in Mu2e tooling?

## Mixed findings (verbatim agent text)

The mapping is implemented as a Python f-string in OUR `utils/prod_utils.py:create_index_definition`:

```python
idx_name = f"i{output_index_dataset}"
```

Called from `utils/mkidxdef.py:30` when `mkidxdef --prod` runs. So the `i<stem>` naming is enforced by mu2e prodtools, not by POMS itself.

The POMS-side wiring (which SAM def a stage iterates over) is per-stage config in POMS's database, accessed via the web UI or programmatically. The agent did NOT pin down the exact column name in `campaign_stages` table — left as an unverified claim.

## Operational answer (with caveats)

**Standard Mu2e workflow:**
1. User creates `MDC2025-NNN.json` map in `/exp/mu2e/app/users/mu2epro/production_manager/poms_map/`
2. `json2jobdef --prod --jobdefs MDC2025-NNN.json` pushes tarballs to SAM and writes the map
3. `mkidxdef --prod --jobdefs MDC2025-NNN.json` creates SAM def `iMDC2025-NNN`
4. POMS web UI has a campaign stage configured to dispatch this map against `iMDC2025-NNN`
5. POMS auto-discovers (presumably by polling the dropbox dir) and launches

**Reuse case (G4BL-000.json against iMDC2025-025):**
- The agent's claim that POMS "hard-couples filename to SAM def" appears wrong. Instead, the stage's SAM dataset field can be set to any def name.
- Per the user-ops agent: `poms_client.update_campaign_stage()` exists. So a stage can be reconfigured to point at `iMDC2025-025` while dispatching `G4BL-000.json`.
- The exact API call hasn't been validated against the running Mu2e POMS instance.

## Open questions left unanswered

- The exact column name on `campaign_stages` for the SAM dataset
- Whether the map filename actually drives anything in POMS, or just our local mkidxdef convention
- Whether the running Mu2e POMS instance has stages that decouple map filename from SAM def name

## Files referenced (in github.com/fermitools/poms)

- `webservice/CampaignsPOMS.py`, `StagesPOMS.py` — campaign/stage logic
- `webservice/poms_model.py` — ORM models
- `webservice/SAMSpecifics.py` — SAM integration

## Files referenced (in mu2e prodtools — our local repo)

- `utils/prod_utils.py:203` — `create_index_definition()` — does the `i<stem>` naming
- `utils/mkidxdef.py:30` — calls `create_index_definition` with map_stem
- `bin/mkidxdef` — bash wrapper
