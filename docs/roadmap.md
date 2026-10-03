# checkov-ledger-action roadmap

The reference of record for this repo's status, decisions (`CLA-D*`) and threat model.
Sprints are GitHub milestones in this repo; a milestone's description is its plan and its
issues are its task list. The action's origin and design argument live in
`603-Identity/infrastructure-core` (decision **IAC-D47**, `sprints/15-checkov-ledger/`)
and are cited from there, not restated here.

## Status

Released: **v0.1.1** (see `CHANGELOG.md`). The evaluator, `action.yml` and the test
suite were extracted from infrastructure-core in its Sprint 15, Phase 2. No sprint has
been planned in this repo yet.

## Decisions

### CLA-D1 — sprints are GitHub milestones, the backlog is GitHub issues

Adopted with the way-of-working plugin (`planning.kind: github_milestones`,
`backlog.kind: github_issues`, both in this repo). The repo is small and public, its
work arrives as issues, and a milestone groups them without a sprint-plan file to keep
in sync.

## Threat model

The `security-critic` agent's ground truth. Source to sink:

- **Inputs** (`directory`, `framework`, `ledger`) may be wired from event data by a
  consumer. They reach the shell only through `env:`, never interpolated into a `run:`
  body or a nested string literal. Any new input follows the same rule.
- **The ledger is untrusted-until-reviewed content in the consumer's repo.** Its
  `checkov_version` decides what `pip install` fetches, so a PR that edits the ledger can
  change the installed package. The defense is the consumer's review of the ledger
  change; this action never writes the ledger.
- **`checkov` itself is a supply-chain dependency**, installed from PyPI at the version
  the ledger names. No hash pinning today.
- **Consumers pin this action by SHA.** Tags are annotated, signed and only on `main`'s
  first-parent line (`CHANGELOG.md`), and consumers verify that. A tag elsewhere, or a
  history rewrite on `main`, breaks that verification.
- **No credentials.** Nothing in the action reads a cloud provider or a secret. A change
  that adds one is a threat-model change and is recorded here.
- **Silent green is the failure mode that matters most.** A scan that reads nothing, a
  config file that narrows the scan (`.checkov.yaml` aborts the run), or a test suite
  that collects zero cases must turn the job red, never pass.
