# Threat model

Ground truth for `security-critic` (and any other reviewer) on this action's untrusted
inputs, dangerous sinks, credential holders, the trust boundaries its design depends on
holding, and its accepted gaps. This file names the **what** — its own scannable list, kept
separate from `README.md` so a reviewer never has to extract it from usage prose first. It
points into `README.md`, `action.yml` and `checkov_ledger.py`'s module docstring for the
**why** and the mechanics of where each boundary is enforced, rather than duplicating that
detail here. Shape follows `terraform-cloudflare-dns`'s and `terraform-microsoft365-entra`'s
`docs/threat_model.md`.

This repo publishes a composite GitHub Action. It runs **inside a consumer's job**, with that
job's checkout, environment and token permissions, installs Checkov from PyPI at the version
the consumer's ledger names, scans the consumer's tree, and compares the result against the
consumer's `checkov-ledger.json`. It holds no credentials of its own and reads no cloud
provider. The property everything below is organized around: **a green result must mean the
scan matched the reviewed ledger exactly** — anything that could make the job pass without a
real, complete scan is the failure that matters most — and the action must never widen what a
consumer's job can do beyond running that scan.

## Untrusted / reviewer-unwitnessed inputs

- **The three inputs** (`directory`, `framework`, `ledger`, `action.yml`). A consumer may wire
  them from event data. They reach the shell only through `env:`, never interpolated into a
  `run:` body or a nested Python string literal (`action.yml`'s own comment). `directory` is
  additionally refused unless it resolves to the repository root (`run`).
- **The consumer's ledger file.** It is reviewed in the consumer's PR, but the action cannot
  tell a reviewed ledger from an unreviewed one: on a PR run it reads the PR head's copy.
  `checkov_version` decides what `pip install` fetches; `check_id`, `reason`, `directory`,
  `tracking` and `review_by` reach `::error::` lines and the step summary.
  `validate_ledger` checks shape, `tracking` against an issue-URL pattern in this org, and
  `review_by` as an ISO date.
- **The consumer's scanned tree** — Terraform files, `git ls-files` output, and any
  `.checkov.yaml`/`.checkov.yml` at the scan directory, the working directory or `$HOME`.
- **The consumer job's environment.** Checkov honors several `CKV_*`, `BC_*` and `PRISMA_*`
  variables as silent equivalents of `--skip-check`.
- **PyPI**: the `checkov` package and its dependency tree, unpinned by hash.

## Credential holders

- **None in this action.** It declares no secret input and reads no provider credential.
- **The consumer job's own `GITHUB_TOKEN` and any secrets that job exposes.** Not passed to
  the action, but present in the same runner, so any code the action executes — including
  `pip install`'s build and import-time code — runs alongside them. Bounding them is the
  consumer's job (`permissions:` on the calling workflow).
- **This repo's own CI** (`pull-request.yml`) runs with `permissions: contents: read` and no
  secrets.

## Dangerous sinks

- **`pip install "checkov==<ledger version>"`** (`action.yml`): installs and runs third-party
  code in the consumer's job, at a version a ledger edit controls.
- **`checkov -d <dir> --framework <fw> --output json`** and **`checkov --version`**
  (`checkov_ledger.py` `run`): list-form `subprocess.run`, no shell, with timeouts.
- **`git ls-files -z`** (`run`): list-form, no shell; its output decides coverage.
- **Workflow-command output** (`::error::`, `::notice::` on stdout): ledger values printed
  here are formatted with `!r`, so an embedded newline cannot start a new workflow command.
- **`$GITHUB_STEP_SUMMARY`** (`write_step_summary`): ledger values written into a Markdown
  table, after the ledger has validated.
- **The job's exit status itself.** A consumer gates merges on it, so a false green is a
  security-relevant sink, not just a correctness bug.

## Boundaries meant to hold, and where each is enforced

- **Inputs never reach a shell string.** Enforced in `action.yml`: every input goes through
  `env:`; only `github.action_path` (trusted action metadata) is interpolated.
- **The ledger is the only source of the Checkov version.** No version input, no default in
  `action.yml`; `evaluate` fails if the installed version differs from the ledger's.
- **Nothing narrows the scan silently.** `run` refuses on any auto-discovered `.checkov.yaml`
  or `.checkov.yml`, strips `CKV_*`/`BC_*`/`PRISMA_*` from the scan's environment, refuses a
  `directory` other than the repository root, and fails if `git` cannot list tracked files
  (no filesystem-walk fallback).
- **Every way a scan can come back empty or partial is red.** `evaluate` fails on parse
  errors, on a tracked directory that produced no results, on a `known_invisible` directory
  that starts producing results, on summary counts that disagree with the result rows, and on
  any Checkov exit code outside `{0, 1}`. The test job refuses a green that reports zero
  checks.
- **The action never writes the ledger.** It only reads it; every ledger change is a human
  edit in the consumer's reviewed PR.
- **Consumers can verify what they pin.** Tags are annotated, signed, and only on `main`'s
  first-parent line (`CHANGELOG.md`), and consumers pin by SHA. `main` is protected by the
  `main-required-checks` ruleset (PR required, `Secret scan (detect-secrets)` and `test`
  required).
- **No secret lands in this repo.** `detect-secrets` runs as a pre-commit hook and as a
  required CI job against `.secrets.baseline`; GitHub secret scanning and push protection are
  enabled on the repo.

## Accepted gaps

- **Checkov is installed from PyPI without hash pinning.** A compromised release at the
  pinned version, or in its dependency tree, runs with the consumer job's access. The
  compensating control is that the version only changes through a reviewed ledger edit; the
  consumer's `permissions:` bound the blast radius.
- **A PR can change the ledger and the scanned tree together.** On a PR run the action
  evaluates the PR's own ledger, so a PR that adds a finding and accepts it in the same diff
  goes green. That is by design: the human review of the ledger diff is the control, and the
  step summary shows every accepted group on every run so the debt stays visible.
- **Ledger text reaches the step summary unescaped.** `check_id` is only checked to be a
  non-empty string, so a value containing `|` or Markdown renders as such in the summary
  table. Display only — it cannot change the job's result or start a workflow command.
- **`main-required-checks` has no explicit `deletion` or `non_fast_forward` rule.** Its
  `pull_request` rule (no bypass actors) already routes every update to `main` through a PR,
  so the remaining exposure is narrower: whether branch deletion is blocked has not been
  verified, and a repo admin can still edit or disable the ruleset outside review. A rewrite
  of `main` would break consumers' "tag is on `main`'s first-parent line" check rather than
  slip past it.
