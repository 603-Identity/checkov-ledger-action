# Changelog

Tags are annotated, signed, and placed only on `main`'s first-parent line: consumers'
pin helpers verify that the pinned SHA was `main`'s tip at some point and that the
`# vX.Y.Z` label's tag names it (infrastructure-core `scripts/checkov_ledger_action_pin.py`).

## Unreleased (v0.1.2)

Dependency bump; no change to the evaluator's behaviour or to `action.yml`.

- Checkov 3.3.8 -> 3.3.22. Every live 3.3.8 reference (the example ledger, test
  fixtures, usage example and version claims) moved to 3.3.22; provenance notes keep
  3.3.8 where a past review verified it, annotated as re-checked on 3.3.22. The
  version-specific claims were re-checked against 3.3.22 itself: both JSON shapes (nested, and
  all-flat when nothing is evaluated), `skipped` always present in the summary, the
  `count`/`for_each`/`module.<block>[0]` address renderings, the `.checkov.yaml`/
  `.checkov.yml` discovery order (`--directory`, cwd, home), and the flag-backed
  env vars the sanitizer strips (e.g. `CKV_CHECK`, `CKV_FRAMEWORK`, `CKV_SKIP_CHECK`).
- bc-detect-secrets 1.5.47 -> 1.5.52 (the version Checkov 3.3.22 requires), in the CI
  pin, the pre-commit comment and `.secrets.baseline`. The regenerated baseline adds
  the `is_baseline_file` filter and has no results.

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
