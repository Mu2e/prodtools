# Source: production session notes, week of 2026-11-02 to 2026-11-08

Provenance: pasted into `/wiki-ingest` on 2026-11-08 from the Claude Code
production session (campaigns 193–197, PRs #83/#84/#87/#88).

This week's (2026-11-02 to 2026-11-08) production lessons from the session:
(a) 24 h-limit stragglers in campaigns 193/194 (Run 1A COL5 flat gamma) were
the node, not the seed — a same-seed resubmit at the 48 h recovery floor
finished in <6.3 h; workflow: hold code 26 -> jobsub-rm the held proc -> tick
recovers at 4000MB/48h;
(b) rule: push a downstream cnf only after its input campaign has stopped (0
jobs in queue, rows verified) — no partial-input pushes plus --extend;
(c) push_cnf / json2jobdef under ksu needs an ABSOLUTE --json path;
(d) push-order bug: --prod --enqueue registered the cnf in SAM before the
input check, leaving orphan cnfs three times that week; fixed in PR #88
(preflight_entry before _pushout_to_sam);
(e) the drain-check fix PRs #83/#84 merged 2026-11-08 and the production
checkout moved from drain-fix-live to main.
