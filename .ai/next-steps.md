# Next steps — cursor (2026-10-02, Sprint 1 implementing)

**Now:** **Sprint 1**, `sprint_status: **implementing**`. Milestone #1, "Sprint 1: test
hermeticity and dependency bump"; build order #6 then #3.

**Just done:**
- Adopted the way-of-working plugin at v0.15.0 (`fc0ad53`) and added
  `docs/threat_model.md` (`6359ea2`).
- Opened #6 (bump Checkov to 3.3.22 and bc-detect-secrets to 1.5.52).
- Ran `/way-of-working:plan-sprint`: created milestone #1 (due 2026-10-16, ships as
  v0.1.2), placed #6 and #3 on it, and posted a triage comment on each.
- First anchor for milestone 1, description sha
  `cd8bbc6564cc4804a3162ad9cd53703a5c95f4ab7a071212751889900484ea6c`.

---

**Next:** task #6 — bump Checkov 3.3.8 → 3.3.22 and bc-detect-secrets 1.5.47 → 1.5.52
per #6's acceptance list, check each version-specific claim against 3.3.22 before
rewording it, keep `test_checkov_ledger.py` green, add a CHANGELOG entry, then run
`/way-of-working:critic-gate` and `/way-of-working:ship`.

**Model:** **sonnet** (`coder`), from `models.coder`.

**HITL Gate:** OPEN. First anchor for milestone 1: the owner confirms the anchored Sprint 1
plan (the milestone #1 description and task #6) before work starts.

**Pointers:** `docs/roadmap.md` · sprint milestone:
https://github.com/603-Identity/checkov-ledger-action/milestone/1
