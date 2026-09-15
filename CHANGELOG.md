# Changelog

Tags are annotated, signed, and placed only on `main`'s first-parent line: consumers'
pin helpers verify that the pinned SHA was `main`'s tip at some point and that the
`# vX.Y.Z` label's tag names it (infrastructure-core `scripts/checkov_ledger_action_pin.py`).

## v0.1.1 -- 2026-09-15

No change to `action.yml`, `checkov_ledger.py` or `test_checkov_ledger.py`. This
release exists to exercise a consumer's Dependabot bump end to end: Dependabot only
opens a PR when the newest tag names a commit that differs from the pinned SHA, so
a tag on the same commit as v0.1.0 would prove nothing. The expected result in
infrastructure-core is a bump PR that is RED on its gates until a human reads the
upstream diff (this file) and regenerates the lock -- red, not inert.

## v0.1.0 -- 2026-09-15

First release. The evaluator, action and test suite extracted verbatim from
`603-Identity/infrastructure-core` (Sprint 15, Phase 2; PR #1, merge `da47eaa`), with
two extraction-only edits: the suite's usage path, and case 28 validating
`examples/checkov-ledger.json` instead of a consuming repo's real ledger.
