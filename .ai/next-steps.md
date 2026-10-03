# Next steps — cursor (2026-10-02, Sprint 1 implementing)

**Now:** **Sprint 1**, `sprint_status: **implementing**`. Milestone #1, "Sprint 1: test
hermeticity and dependency bump"; build order #6 (done, in review) then #3.

**Just done:**
- Task #6: bumped Checkov 3.3.8 → 3.3.22 and bc-detect-secrets 1.5.47 → 1.5.52, claims
  re-checked against a real 3.3.22 install. Open as PR #9 (`2a77bf8`), not yet merged.
- `/way-of-working:critic-gate` on that diff: docs-consistency + architect, 3 rounds,
  converged (round 3 tightenings only); no second-opinion round (`second_opinion` is null).
- Untracked and gitignored `.ai/state.json` (PR #8); merged the Sprint 1 anchor sync (PR #7).
- Plan anchor re-verified (`match`) and re-pointed at task #3.

---

**Next:** task #3 — make `test_checkov_ledger.py`'s fixture commits hermetic against the
caller's `commit.gpgsign` (force it off inside the suite, per #3's acceptance list), keep
the suite green, add a CHANGELOG entry, then run `/way-of-working:critic-gate` and
`/way-of-working:ship`. Cut its branch from `main`; #9 need not merge first.

**Model:** **sonnet** (`coder`), from `models.coder`.

**HITL Gate:** NONE OPEN. The release (v0.1.2, bundling #6 and #3, due 2026-10-16) is a
separate human decision once both merge.

**Pointers:** `docs/roadmap.md` · sprint milestone:
https://github.com/603-Identity/checkov-ledger-action/milestone/1
