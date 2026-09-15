# checkov-ledger-action

A composite GitHub Action that runs [Checkov](https://www.checkov.io/) and compares the
result against a **reviewed ledger** committed in the consuming repo. The job is **green if
and only if the scan matches the ledger exactly**, and red on any drift in either direction:

- a finding not listed in the ledger
- a listed finding that is no longer observed (fixed, renamed, moved, or deleted)
- an inline `# checkov:skip=` that is not listed, or whose comment text changed
- a parse error
- a tracked directory that stopped producing results, or a "known invisible" one that started
- a tracked file in a format Checkov's scan cannot read, beside files it can
- a Checkov version other than the one the ledger names
- an acceptance past its `review_by` date

The ledger is a human edit in a reviewed PR. This action never writes it.

**Why this exists:** a CI status is a change detector, not a to-do list. "Deliberately red"
jobs decay into unread ones. The full argument and the six-week record that led here are in
`603-Identity/infrastructure-core`'s roadmap decision **IAC-D47** and
`sprints/15-checkov-ledger/sprint_plan.md`.

## Status

The evaluator (`checkov_ledger.py`), `action.yml` and the test suite were built, reviewed
and enforced in `infrastructure-core` (Sprint 15, Phase 1: PRs #490, #493, #496) and
extracted here in Phase 2 (tracking: infrastructure-core#487). The code is the same; the
only edits on extraction are the test suite's usage path and its case 28, which validates
`examples/checkov-ledger.json` here instead of a consuming repo's real ledger.

## Usage

```yaml
- uses: actions/checkout@<sha>
- uses: 603-Identity/checkov-ledger-action@<sha> # vX.Y.Z
  with:
    directory: .                 # scan root; must be the workspace root (default)
    framework: terraform         # Checkov --framework value (default)
    ledger: checkov-ledger.json  # path to the ledger, relative to the workspace (default)
```

- **Pin by SHA.** Dependabot's `github-actions` ecosystem moves the pin on each tag.
- The action installs the Checkov version the ledger names (`pip install checkov==<that>`).
  There is no version input; the ledger is the single source of the version.
- `directory` must resolve to the workspace root. Coverage and ledger paths are repo-root
  relative while Checkov's own `file_path`s are relative to the scan root, and the two only
  agree there; `run` refuses anything else.
- Coverage uses `git ls-files`, so check out the full tree (no sparse checkout) before
  this step. No cloud credentials are needed; nothing here reads a provider.
- A `.checkov.yaml` / `.checkov.yml` at the scan root aborts the run: Checkov auto-discovers
  it and no flag disables that, so its mere existence would silently narrow the scan.
- Green still *shows* the debt: the accepted groups, their tracking links and review dates
  are written to `$GITHUB_STEP_SUMMARY`, with a `::notice::` count.

## Ledger schema (`checkov-ledger.json`)

Four top-level keys. `examples/checkov-ledger.json` is a complete, validated example (test
case 28 validates it against `examples/main.tf`, so it cannot go stale).

```json
{
  "checkov_version": "3.3.8",
  "failures": [
    { "check_id": "CKV_AWS_18",
      "reason": "one sentence",
      "tracking": "https://github.com/603-Identity/<repo>/issues/<n>",
      "review_by": "2026-12-14",
      "resources": [
        { "resource": "aws_s3_bucket.state", "file_path": "/main.tf" }
      ] }
  ],
  "skips": [
    { "check_id": "CKV_AWS_109",
      "tracking": "https://github.com/603-Identity/<repo>/issues/<n>",
      "review_by": "2026-12-14",
      "resources": [
        { "resource": "aws_iam_policy_document.kms", "file_path": "/main.tf",
          "suppress_comment": " key's own resource-based policy; \"*\" means \"this key\"" }
      ] }
  ],
  "known_invisible": [
    { "directory": "github-only",
      "reason": "GitHub-provider resources; Checkov ships no policies for them.",
      "tracking": "https://github.com/603-Identity/<repo>/issues/<n>",
      "review_by": "2026-12-14" }
  ]
}
```

Rules the evaluator enforces, in one place:

- **Every failure and skip row is resource-scoped** (`resource` + `file_path`), keyed by the
  exact `(check_id, resource, file_path)` triple. No counts, no check-wide acceptance: a
  count cannot tell a renamed resource from the original, and a `resource`-only key lets two
  directories' identically named resources collapse into one entry.
- **`resource` is Checkov's address verbatim.** `validate` cross-checks each row against the
  named file by the address's shape: `<type>.<name>` (a `resource`/`data` block),
  `module.<block>.<type>.<name>` (the block in the module's own file, plus a `module "<block>"`
  block somewhere tracked), a dot-less `<name>` (a `module "<name>"` block), or
  `<provider>.<alias>` (a `provider` block, plus `alias = "<alias>"` unless the alias is
  `default`). Nested module addresses and indexed `count`/`for_each` addresses are rejected
  with a message naming the shape; widen the schema when a consumer needs one.
- **Skip rows pin the inline `suppress_comment` verbatim**, so the justification lives once,
  in the code. Skip groups carry no `reason` of their own for the same reason.
- **Exact duplicate entries collapse to one key** (a shared module reported once per
  caller), count once, and are reported as collapsed. A duplicate whose `suppress_comment`
  differs stays an error.
- **Coverage is by directory.** Every tracked directory holding a Terraform/OpenTofu file
  (`*.tf`, `*.tf.json`, `*.tofu`, `*.tofu.json`, `*.tftest.hcl`, `*.tofutest.hcl`,
  `*.tftest.json`, `*.tofutest.json`, from `git ls-files`) must either produce at least one
  result or be listed in `known_invisible`. `resource_count` is never used: the same scanner
  reports 5 for five parsed `azuread_*` resources and 0 for one parsed `cloudflare_dns_record`.
- **Coverage is also by file for the formats Checkov's walk cannot read** (`.tf.json`,
  `.tofu`, `.tofu.json`, `.tftest.json`, `.tofutest.json`). Such a file beside a readable
  `.tf` keeps its directory "producing", so it is red unless its own directory is a reviewed
  `known_invisible` entry; no row shape accepts one in place.
- **Every group carries `tracking`** (a GitHub issue URL under `github.com/603-Identity/`)
  **and `review_by`** (ISO date); failure and known-invisible groups also carry `reason`.
  Past `review_by` is red: re-review and re-date in a reviewed PR, or fix.
- **The Checkov version is pinned by the ledger.** `run` compares `checkov --version`'s own
  output to `checkov_version`; a mismatch is red whatever the scan found.
- Both of Checkov 3.3.8's JSON shapes are handled: the nested `summary`/`results` shape and
  the flat shape it emits when nothing at all was evaluated.

What it does not do is documented at the top of `checkov_ledger.py` ("WHAT THIS DOES NOT
DO"), including the module-instance collapse (infrastructure-core#491) and the regex-based,
not parse-tree-based, cross-checks in `validate`.

## Development

Stdlib-only Python; no dependencies beyond Checkov itself, and the tests need none at all:

```sh
python3 test_checkov_ledger.py          # 88 checks, no live scan, no credentials
python3 checkov_ledger.py validate --ledger examples/checkov-ledger.json
```

The suite pins its own case count and fails if it drifts; update `EXPECTED_CHECK_COUNT`
deliberately when adding a case, never to make a red suite green.

Guardrails on this repo: `main` accepts PRs only (ruleset `main-required-checks`, no bypass
actors). Its required checks are the PR-time `detect-secrets` gate against
`.secrets.baseline` (the same binary and baseline the pre-commit hook uses) and the `test`
job, which is added to the ruleset only after the first merge to `main` that carries it,
never before it can report (a context no job reports parks every PR at "Expected" forever,
infrastructure-core IAC-D14). GitHub secret scanning and push protection are enabled.
