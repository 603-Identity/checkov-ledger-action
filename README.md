# checkov-ledger-action

A composite GitHub Action that runs [Checkov](https://www.checkov.io/) and compares the
result against a **reviewed ledger** committed in the consuming repo. The job is **green if
and only if the scan matches the ledger exactly**, and red on any drift in either direction:

- a finding not listed in the ledger
- a listed finding that is no longer observed (fixed, renamed, moved, or deleted)
- an inline `# checkov:skip=` that is not listed, or whose comment text changed
- a parse error
- a tracked directory that stopped producing results, or a "known invisible" one that started
- a Checkov version other than the one the ledger names
- an acceptance past its `review_by` date

The ledger is a human edit in a reviewed PR. This action never writes it.

**Why this exists:** a CI status is a change detector, not a to-do list. "Deliberately red"
jobs decay into unread ones. The full argument and the six-week record that led here are in
`603-Identity/infrastructure-core`'s roadmap decision **IAC-D47** and
`sprints/15-checkov-ledger/sprint_plan.md`.

## Status

**Scaffold.** The evaluator (`checkov_ledger.py`), `action.yml` and the test suite are being
built in `infrastructure-core` (Sprint 15, Phase 1) and are extracted here verbatim in Phase 2
(tracking: infrastructure-core#487). Until then this repo has no runnable action.

## Usage (once published)

```yaml
- uses: actions/checkout@<sha>
- uses: 603-Identity/checkov-ledger-action@<sha> # vX.Y.Z
  with:
    directory: .                 # scan root, default "."
    framework: terraform         # default "terraform"
    ledger: checkov-ledger.json  # default, relative to directory
```

Pin by SHA. The action installs the Checkov version the ledger names; there is no version
input.

## Ledger schema (`checkov-ledger.json`)

```json
{
  "schema_version": 1,
  "checkov_version": "3.3.8",
  "known_invisible": [
    { "directory": "tenants/x/github-environment",
      "reason": "GitHub-provider resources; Checkov ships no policies for them",
      "tracking": "https://github.com/603-Identity/<repo>/issues/<n>",
      "review_by": "2026-12-14" }
  ],
  "accepted_failures": [
    { "check_id": "CKV_AWS_18",
      "resources": [
        { "resource": "aws_s3_bucket.bootstrap_state", "file_path": "/bootstrap/main.tf" }
      ],
      "reason": "one sentence",
      "tracking": "https://github.com/603-Identity/<repo>/issues/<n>",
      "review_by": "2026-12-14" }
  ],
  "accepted_skips": [
    { "check_id": "CKV_AWS_109",
      "resources": [
        { "resource": "aws_iam_policy_document.cicd_secrets_kms", "file_path": "/bootstrap/main.tf",
          "suppress_comment": " key's own resource-based policy; \"*\" means \"this key\"" }
      ],
      "tracking": "https://github.com/603-Identity/<repo>/issues/<n>",
      "review_by": "2026-12-14" }
  ]
}
```

Rules the evaluator enforces, in one place:

- Every failure and skip row is **resource-scoped** (`resource` + `file_path`). No counts,
  no check-wide acceptance: a count cannot tell a renamed resource from the original.
- Skip rows pin the inline `suppress_comment` verbatim, so the justification lives once, in
  the code.
- **Coverage is by directory.** Every tracked directory containing `.tf` files (from
  `git ls-files`) must either produce at least one result or be listed in `known_invisible`.
  `resource_count` is never used: the same scanner reports 5 for five parsed `azuread_*`
  resources and 0 for one parsed `cloudflare_dns_record`.
- Every group carries `tracking` (a GitHub issue URL in the org), `reason`, and `review_by`
  (ISO date). Past `review_by` is red: re-review, re-date in a reviewed PR, or fix.
- A `.checkov.yaml` / `.checkov.yml` at the scan root aborts the run: Checkov auto-discovers
  it and no flag disables that, so its mere existence would silently narrow the scan.

## Development

Stdlib-only Python; no dependencies beyond Checkov itself. Tests drive the evaluator with
fixture JSON in both of Checkov's output shapes (nested `summary`/`results`, and the flat
shape emitted when nothing matched) and never need a live scan or cloud credentials.

Guardrails on this repo: `main` accepts PRs only (ruleset `main-required-checks`, no bypass
actors); a PR-time `detect-secrets` gate against `.secrets.baseline`, the same binary and
baseline the pre-commit hook uses; GitHub secret scanning and push protection enabled. The
`test` job becomes a required check when the code lands.
