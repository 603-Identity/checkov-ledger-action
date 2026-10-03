# Next steps — cursor (2026-10-02, Sprint 1 done)

**Now:** **Sprint 1**, `sprint_status: **done**`. Milestone #1, "Sprint 1: test hermeticity
and dependency bump": both tasks merged, both issues closed.

**Just done:**
- Task #6 (PR #9): Checkov 3.3.8 → 3.3.22 and bc-detect-secrets 1.5.47 → 1.5.52.
  `/way-of-working:critic-gate`: docs-consistency + architect, 3 rounds, converged.
- Task #3 (PR #11): fixture commits pin `commit.gpgsign=false`. `/way-of-working:critic-gate`:
  architect + docs-consistency, 1 round, no defects, converged.
- Hermetically verified only: the suite ran in WSL; no live consumer run of the action is claimed.
- Cursor at `351ef7a` (`main`).

---

**Next:** release v0.1.2 — the owner's decision, not an unattended step. Rename
`CHANGELOG.md`'s "Unreleased (v0.1.2)" to "v0.1.2 -- <date>" in a small PR, merge it, then
the owner places a signed annotated tag on `main`'s tip (tags go only on the first-parent
line). Then `/way-of-working:archive-sprint` for Sprint 1.

**Model:** **sonnet** (`coder`), from `models.coder`; the release steps are mechanical.

**HITL Gate:** OPEN. The owner decides whether and when to release v0.1.2 (milestone due
2026-10-16) and signs the tag.

**Pointers:** `docs/roadmap.md` · sprint milestone:
https://github.com/603-Identity/checkov-ledger-action/milestone/1
