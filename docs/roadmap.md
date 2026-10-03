# checkov-ledger-action roadmap

The reference of record for this repo's status and decisions (`CLA-D*`).
Sprints are GitHub milestones in this repo; a milestone's description is its plan and its
issues are its task list. The action's origin and design argument live in
`603-Identity/infrastructure-core` (decision **IAC-D47**, `sprints/15-checkov-ledger/`)
and are cited from there, not restated here.

## Status

Released: **v0.1.2** (see `CHANGELOG.md`). The evaluator, `action.yml` and the test
suite were extracted from infrastructure-core in its Sprint 15, Phase 2.

Sprint 1 (milestone #1, "test hermeticity and dependency bump") is done: Checkov 3.3.22 /
bc-detect-secrets 1.5.52 (PR #9) and hermetic gpgsign fixture commits (PR #11), released as
v0.1.2 (PR #13). Hermetically verified only (suite run in WSL); the live smoke run of the
action is deferred and tracked in #14.

## Decisions

### CLA-D1 — sprints are GitHub milestones, the backlog is GitHub issues

Adopted with the way-of-working plugin (`planning.kind: github_milestones`,
`backlog.kind: github_issues`, both in this repo). The repo is small and public, its
work arrives as issues, and a milestone groups them without a sprint-plan file to keep
in sync.

## Threat model

See [`threat_model.md`](threat_model.md).
