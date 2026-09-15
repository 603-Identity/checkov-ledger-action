#!/usr/bin/env python3
"""Falsify `checkov_ledger.py`. Runs in `gates.green`.

Per A4 (Sprint 15 plan), this suite runs NO live Checkov scan -- it drives
fixture Checkov JSON into the `evaluate` subcommand on stdin, and exercises
`validate` and `run`'s early guards against small synthetic repo fixtures
(temp directories, real `.tf` files, a real `git init` where the coverage
check needs one). `run`'s own live-Checkov path is exercised by CI, and can
be run locally on demand (`python3 checkov_ledger.py run`) -- see A4.

Carries over every case `scripts/test_assert_checkov_known_findings.py`
proved (re-targeted to this evaluator's doctrine: no job in this org is
designed to be red, so an EXACT MATCH is now exit 0, not exit 1 -- IAC-D47)
plus the cases new to a ledger that covers every check_id and pins directory
coverage, version, and review dates: version mismatch, a known-invisible
directory producing results, a producing directory gone silent, a `.tf`
directory in neither set, a changed `suppress_comment`, a group past
`review_by`, and `validate`'s own schema/cross-reference checks.

Usage: python3 .github/actions/checkov-ledger/test_checkov_ledger.py
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent
SCRIPT = HERE / "checkov_ledger.py"

sys.path.insert(0, str(HERE))
import checkov_ledger as ledger_module  # noqa: E402

ORG = ledger_module.ORG

# A small, self-contained ledger used as the baseline for most `evaluate`
# cases below. `dirA` carries the one accepted failure, `dirB` the one
# accepted skip, `dirC` is known-invisible.
SMALL_LEDGER = {
    "checkov_version": "3.3.8",
    "failures": [
        {
            "check_id": "CKV_AWS_18",
            "reason": "test fixture failure",
            "tracking": f"https://github.com/{ORG}/infrastructure-core/issues/291",
            "review_by": "2099-01-01",
            "resources": [
                {"resource": "aws_s3_bucket.a", "file_path": "/dirA/main.tf"},
            ],
        },
    ],
    "skips": [
        {
            "check_id": "CKV_AWS_109",
            "tracking": f"https://github.com/{ORG}/infrastructure-core/issues/487",
            "review_by": "2099-01-01",
            "resources": [
                {
                    "resource": "aws_iam_policy_document.b",
                    "file_path": "/dirB/main.tf",
                    "suppress_comment": " test fixture false positive",
                },
            ],
        },
    ],
    "known_invisible": [
        {
            "directory": "dirC",
            "reason": "test fixture: provider with no Checkov policies",
            "tracking": f"https://github.com/{ORG}/infrastructure-core/issues/487",
            "review_by": "2099-01-01",
        },
    ],
}
TRACKED_DIRS = ["dirA", "dirB", "dirC"]


def _failed_check(check_id: str, resource: str, file_path: str) -> dict:
    return {"check_id": check_id, "resource": resource, "file_path": file_path}


def _skipped_check(check_id: str, resource: str, file_path: str, comment: str) -> dict:
    return {
        "check_id": check_id,
        "resource": resource,
        "file_path": file_path,
        "check_result": {"result": "SKIPPED", "suppress_comment": comment},
    }


def _passed_check(check_id: str, resource: str, file_path: str) -> dict:
    return {"check_id": check_id, "resource": resource, "file_path": file_path}


def _nested(
    passed: int,
    failed: int,
    failing: list,
    skipped: list = (),
    parsing_errors: int = 0,
    extra_passed: list = (),
    declare_skipped: bool = True,
) -> dict:
    return {
        "check_type": "terraform",
        "summary": {
            "passed": passed,
            "failed": failed,
            **({"skipped": len(skipped)} if declare_skipped else {}),
            "parsing_errors": parsing_errors,
            "resource_count": passed + failed,
        },
        "results": {
            "failed_checks": [_failed_check(*r) for r in failing],
            "skipped_checks": [_skipped_check(*r) for r in skipped],
            "passed_checks": [_passed_check(*r) for r in extra_passed],
        },
    }


def _flat(passed: int, failed: int, failing: list = (), parsing_errors: int = 0) -> dict:
    return {
        "passed": passed,
        "failed": failed,
        "skipped": 0,
        "parsing_errors": parsing_errors,
        "resource_count": passed + failed,
        "failed_checks": [_failed_check(*r) for r in failing],
    }


def _baseline_checkov_data() -> dict:
    return _nested(
        passed=5,
        failed=1,
        failing=[("CKV_AWS_18", "aws_s3_bucket.a", "/dirA/main.tf")],
        skipped=[("CKV_AWS_109", "aws_iam_policy_document.b", "/dirB/main.tf", " test fixture false positive")],
    )


def main() -> int:
    failures: list[str] = []
    checks = 0

    def check(
        label: str,
        argv: list[str],
        stdin_text: str,
        expected_rc: int,
        must_contain: tuple[str, ...] = (),
        must_not_contain: tuple[str, ...] = (),
        cwd: pathlib.Path | None = None,
        env: dict | None = None,
    ) -> str:
        nonlocal checks
        checks += 1
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), *argv],
            input=stdin_text,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else str(REPO),
            env=env,
        )
        out = proc.stdout + proc.stderr
        if proc.returncode != expected_rc:
            failures.append(f"{label}: expected exit {expected_rc}, got {proc.returncode}. Output: {out.strip()}")
        for needle in must_contain:
            if needle not in out:
                failures.append(f"{label}: expected output to mention {needle!r}. Output: {out.strip()}")
        for needle in must_not_contain:
            if needle in out:
                failures.append(f"{label}: output must NOT mention {needle!r} here. Output: {out.strip()}")
        return out

    def evaluate_argv(
        ledger_path: pathlib.Path,
        tracked_dirs: list,
        checkov_version: str = "3.3.8",
        today: str | None = None,
        tracked_files: list = (),
    ) -> list[str]:
        # `evaluate` now asserts tracked_files is non-empty whenever
        # tracked_dirs is (T4b /critic-gate fix, security-critic round 1):
        # tracked_files is always a real superset of what produced
        # tracked_dirs in `cmd_run`, so every dir gets its own baseline
        # `<dir>/main.tf` here -- inert for every existing case (never an
        # opaque suffix), additive to whatever opaque-specific files a case
        # passes explicitly.
        argv = ["evaluate", "--ledger", str(ledger_path), "--checkov-version", checkov_version]
        for d in tracked_dirs:
            argv += ["--tracked-dir", d]
        for f in [f"{d}/main.tf" for d in tracked_dirs] + list(tracked_files):
            argv += ["--tracked-file", f]
        if today:
            argv += ["--today", today]
        return argv

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        ledger_path = tmp_path / "checkov-ledger.json"
        ledger_path.write_text(json.dumps(SMALL_LEDGER), encoding="utf-8")

        # 1. EXACT MATCH -- re-targeted BASELINE. Doctrine (IAC-D47): no job
        #    in this org is designed to be red, so an exact match is exit 0.
        check(
            "EXACT MATCH (nested shape, exit 0 on doctrine's own terms)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps(_baseline_checkov_data()),
            0,
            must_contain=("Checkov evaluated:",),
        )

        # 2. VERSION MISMATCH -- checked before anything else (decision 6).
        check(
            "VERSION MISMATCH",
            evaluate_argv(ledger_path, TRACKED_DIRS, checkov_version="3.3.9"),
            json.dumps(_baseline_checkov_data()),
            1,
            must_contain=("ledger's checkov_version",),
        )

        # 3. NEW FAILURE not in the ledger.
        data = _nested(
            passed=5,
            failed=2,
            failing=[
                ("CKV_AWS_18", "aws_s3_bucket.a", "/dirA/main.tf"),
                ("CKV_AWS_18", "aws_s3_bucket.new", "/dirA/main.tf"),
            ],
            skipped=[("CKV_AWS_109", "aws_iam_policy_document.b", "/dirB/main.tf", " test fixture false positive")],
        )
        check(
            "NEW FAILURE (not in ledger)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps(data),
            1,
            must_contain=("NEW Checkov failure", "aws_s3_bucket.new"),
        )

        # 4. LEDGER FAILURE NOT OBSERVED.
        data = _nested(passed=6, failed=0, failing=[], skipped=[("CKV_AWS_109", "aws_iam_policy_document.b", "/dirB/main.tf", " test fixture false positive")])
        check(
            "LEDGER FAILURE NOT OBSERVED (fixed, renamed, or deleted)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps(data),
            1,
            must_contain=("no longer observed", "aws_s3_bucket.a"),
        )

        # 4b. PARTIAL REMOVAL -- restored (PR #490 architect review 6c; the
        #     old `scripts/assert_checkov_known_findings.py` suite carried
        #     this case and it was dropped when this evaluator replaced it).
        #     A `check_id` group with TWO resource rows where the live scan
        #     still reports one of them: the surviving row must not mask the
        #     missing one -- "no longer observed" for the removed row alone,
        #     no "NEW Checkov failure" for the one still there.
        multirow_ledger = copy.deepcopy(SMALL_LEDGER)
        multirow_ledger["failures"][0]["resources"].append(
            {"resource": "aws_s3_bucket.b", "file_path": "/dirA/other.tf"}
        )
        multirow_ledger_path = tmp_path / "multirow-ledger.json"
        multirow_ledger_path.write_text(json.dumps(multirow_ledger), encoding="utf-8")
        check(
            "PARTIAL REMOVAL (one row within a multi-row check_id group no longer observed)",
            evaluate_argv(multirow_ledger_path, TRACKED_DIRS),
            json.dumps(_baseline_checkov_data()),
            1,
            must_contain=("no longer observed", "aws_s3_bucket.b"),
            must_not_contain=("NEW Checkov failure",),
        )

        # 5. NEW SKIP not in the ledger -- removes a resource from
        #    failed_checks exactly as effectively as fixing it.
        data = _nested(
            passed=5,
            failed=1,
            failing=[("CKV_AWS_18", "aws_s3_bucket.a", "/dirA/main.tf")],
            skipped=[
                ("CKV_AWS_109", "aws_iam_policy_document.b", "/dirB/main.tf", " test fixture false positive"),
                ("CKV_AWS_18", "aws_s3_bucket.a", "/dirA/main.tf", " sneaky new skip"),
            ],
        )
        check(
            "NEW SKIP (not in ledger)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps(data),
            1,
            must_contain=("NEW #checkov:skip",),
        )

        # 6. LEDGER SKIP NOT OBSERVED.
        data = _nested(passed=6, failed=1, failing=[("CKV_AWS_18", "aws_s3_bucket.a", "/dirA/main.tf")], skipped=[])
        check(
            "LEDGER SKIP NOT OBSERVED",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps(data),
            1,
            must_contain=("Ledger-listed skip(s) no longer observed",),
        )

        # 7. SUPPRESS_COMMENT CHANGED -- the justification lives once, in
        #    the ledger; a changed inline comment is drift (decision 4).
        data = _nested(
            passed=5,
            failed=1,
            failing=[("CKV_AWS_18", "aws_s3_bucket.a", "/dirA/main.tf")],
            skipped=[("CKV_AWS_109", "aws_iam_policy_document.b", "/dirB/main.tf", " a DIFFERENT comment now")],
        )
        check(
            "SUPPRESS_COMMENT CHANGED",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps(data),
            1,
            must_contain=("suppress_comment changed",),
        )

        # 7b. PAIRING KEY (failure) -- the ledger's resource NAME is observed
        #     at a DIFFERENT file_path (a rename/move, or a second config
        #     reusing the same name -- the exact bypass
        #     scripts/assert_checkov_known_findings.py's own docstring
        #     records as live-reproduced against a resource-only key). Must
        #     read as BOTH a new failure at the new path AND the old path no
        #     longer observed -- never as a silent match.
        data = _nested(
            passed=5,
            failed=1,
            failing=[("CKV_AWS_18", "aws_s3_bucket.a", "/dirA/other.tf")],
            skipped=[("CKV_AWS_109", "aws_iam_policy_document.b", "/dirB/main.tf", " test fixture false positive")],
        )
        check(
            "PAIRING KEY (failure resource NAME reused at a DIFFERENT file_path)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps(data),
            1,
            must_contain=("NEW Checkov failure", "no longer observed", "/dirA/other.tf", "/dirA/main.tf"),
        )

        # 7c. PAIRING KEY (skip) -- the same collision, for a sanctioned
        #     skip: the ledger's skip resource NAME observed at a different
        #     file_path must not silently satisfy the pin.
        data = _nested(
            passed=5,
            failed=1,
            failing=[("CKV_AWS_18", "aws_s3_bucket.a", "/dirA/main.tf")],
            skipped=[("CKV_AWS_109", "aws_iam_policy_document.b", "/dirB/other.tf", " test fixture false positive")],
        )
        check(
            "PAIRING KEY (skip resource NAME reused at a DIFFERENT file_path)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps(data),
            1,
            must_contain=("NEW #checkov:skip", "no longer observed", "/dirB/other.tf", "/dirB/main.tf"),
        )

        # 8. PARSING ERRORS -- fails loudly and distinctly.
        data = _nested(
            passed=5,
            failed=1,
            failing=[("CKV_AWS_18", "aws_s3_bucket.a", "/dirA/main.tf")],
            skipped=[("CKV_AWS_109", "aws_iam_policy_document.b", "/dirB/main.tf", " test fixture false positive")],
            parsing_errors=2,
        )
        check(
            "PARSING ERRORS (nonzero, otherwise-clean set)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps(data),
            1,
            must_contain=("parsing error",),
        )

        # 9. RECONCILIATION -- a failed_checks entry missing file_path must
        #    not silently read as "that resource stopped failing".
        check(
            "RECONCILIATION (failed_checks entry missing file_path)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps(
                {
                    "summary": {"passed": 5, "failed": 1, "skipped": 0, "parsing_errors": 0},
                    "results": {
                        "failed_checks": [{"check_id": "CKV_AWS_18", "resource": "aws_s3_bucket.a"}],
                        "skipped_checks": [],
                    },
                }
            ),
            1,
            must_contain=("unreadable scan",),
        )

        # 10. MODULE-INSTANCE DUPLICATE COLLAPSE (decision 14, Amendment 2) --
        #     a repeated IDENTICAL failed_checks entry (Checkov's real shape
        #     when a shared module's callers collapse) collapses to one key,
        #     counts once against the declared `failed`, and is reported --
        #     never read as an unreadable scan. An otherwise-exact match
        #     with one such duplicate is still GREEN.
        data = _nested(
            passed=5,
            failed=2,
            failing=[
                ("CKV_AWS_18", "aws_s3_bucket.a", "/dirA/main.tf"),
                ("CKV_AWS_18", "aws_s3_bucket.a", "/dirA/main.tf"),
            ],
            skipped=[("CKV_AWS_109", "aws_iam_policy_document.b", "/dirB/main.tf", " test fixture false positive")],
        )
        check(
            "MODULE-INSTANCE DUPLICATE COLLAPSE (failure, identical entries, otherwise exact match -> green)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps(data),
            0,
            must_contain=("Checkov evaluated:", "1 module-instance duplicate collapsed."),
            must_not_contain=("unreadable scan",),
        )

        # 10b. The same collapse, for a SKIP, with IDENTICAL suppress_comment
        #      on both duplicate entries -- also green, also counted once
        #      (decision 14's skip case, T4a acceptance (iii)'s shape).
        data = _nested(
            passed=5,
            failed=1,
            failing=[("CKV_AWS_18", "aws_s3_bucket.a", "/dirA/main.tf")],
            skipped=[
                ("CKV_AWS_109", "aws_iam_policy_document.b", "/dirB/main.tf", " test fixture false positive"),
                ("CKV_AWS_109", "aws_iam_policy_document.b", "/dirB/main.tf", " test fixture false positive"),
            ],
        )
        check(
            "MODULE-INSTANCE DUPLICATE COLLAPSE (skip, identical comment, otherwise exact match -> green)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps(data),
            0,
            must_contain=("Checkov evaluated:", "1 module-instance duplicate collapsed."),
            must_not_contain=("unreadable scan",),
        )

        # 10c. The same duplicate triple, but the two entries' suppress_comment
        #      DIFFER -- decision 14 is explicit this is NOT a module-instance
        #      collapse (those agree by definition) and stays an error, never
        #      silently picking one comment or reading as unreadable.
        data = _nested(
            passed=5,
            failed=1,
            failing=[("CKV_AWS_18", "aws_s3_bucket.a", "/dirA/main.tf")],
            skipped=[
                ("CKV_AWS_109", "aws_iam_policy_document.b", "/dirB/main.tf", " test fixture false positive"),
                ("CKV_AWS_109", "aws_iam_policy_document.b", "/dirB/main.tf", " a DIFFERENT justification"),
            ],
        )
        check(
            "MODULE-INSTANCE DUPLICATE COLLAPSE (skip, DIFFERING comment -> error, not unreadable scan)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps(data),
            1,
            must_contain=("carry DIFFERENT", "suppress_comment"),
            must_not_contain=("unreadable scan",),
        )

        # 11. RECONCILIATION -- the same class, for skips.
        check(
            "RECONCILIATION (skipped_checks entry missing suppress_comment)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps(
                {
                    "summary": {"passed": 5, "failed": 1, "skipped": 1, "parsing_errors": 0},
                    "results": {
                        "failed_checks": [_failed_check("CKV_AWS_18", "aws_s3_bucket.a", "/dirA/main.tf")],
                        "skipped_checks": [{"check_id": "CKV_AWS_109", "resource": "aws_iam_policy_document.b", "file_path": "/dirB/main.tf"}],
                    },
                }
            ),
            1,
            must_contain=("unreadable scan",),
        )

        # 12. UNREADABLE INPUT.
        check("UNREADABLE (not JSON)", evaluate_argv(ledger_path, TRACKED_DIRS), "not json at all {{{", 1)
        # 12a. UNREADABLE -- empty stdin. Restored (PR #490 architect review
        #      6c; carried by the old script, dropped from this evaluator's
        #      suite). Empty input is not valid JSON either -- must fail the
        #      same way, not hang or read as "nothing to report".
        check(
            "UNREADABLE (empty stdin)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            "",
            1,
            must_contain=("Could not parse Checkov output as JSON",),
        )
        check(
            "UNREADABLE (JSON array, not an object)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps([1, 2, 3]),
            1,
            must_contain=("not a JSON object",),
        )
        # 12c. UNREADABLE -- `failed_checks` present but not a list (e.g. a
        #      malformed scan emitting a single object instead of an array).
        #      Restored (PR #490 architect review 6c; carried by the old
        #      script). `_list()` silently reads a non-list as `[]`, which
        #      the failed-count reconciliation must then catch as an
        #      unreadable scan, not as "zero failures".
        check(
            "UNREADABLE (failed_checks is not a list)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps(
                {
                    "summary": {"passed": 5, "failed": 1, "skipped": 1, "parsing_errors": 0},
                    "results": {
                        "failed_checks": {"check_id": "CKV_AWS_18", "resource": "aws_s3_bucket.a", "file_path": "/dirA/main.tf"},
                        "skipped_checks": [_skipped_check("CKV_AWS_109", "aws_iam_policy_document.b", "/dirB/main.tf", " test fixture false positive")],
                    },
                }
            ),
            1,
            must_contain=("unreadable scan",),
        )
        check(
            "UNREADABLE (summary present but passed/failed/parsing_errors missing)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps({"summary": {"resource_count": 2}, "results": {}}),
            1,
            must_contain=("Could not read the Checkov summary",),
        )
        check(
            "UNREADABLE (passed/failed are strings, not ints)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps({"summary": {"passed": "0", "failed": "1", "parsing_errors": 0}, "results": {}}),
            1,
            must_contain=("Could not read the Checkov summary",),
        )
        # 12b. UNREADABLE -- `skipped` absent entirely, everything else a
        #      normal int. Required like passed/failed/parsing_errors
        #      (/critic-gate finding, PR #490 architect review 6a): an
        #      absent key used to silently bypass the whole skip-
        #      reconciliation block rather than failing the scan.
        check(
            "UNREADABLE (skipped absent from an otherwise well-formed summary)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps({"summary": {"passed": 5, "failed": 1, "parsing_errors": 0}, "results": {
                "failed_checks": [_failed_check("CKV_AWS_18", "aws_s3_bucket.a", "/dirA/main.tf")],
                "skipped_checks": [],
            }}),
            1,
            must_contain=("Could not read the Checkov summary", "skipped"),
        )

        # 13. BOTH SHAPES -- the all-flat shape (Checkov's output when
        #     nothing at all is evaluated), with an empty repo whose only
        #     tracked directory is its own root, known-invisible.
        flat_ledger = {
            "checkov_version": "3.3.8",
            "failures": [],
            "skips": [],
            "known_invisible": [
                {
                    "directory": ".",
                    "reason": "structurally invisible to Checkov",
                    "tracking": f"https://github.com/{ORG}/infrastructure-core/issues/487",
                    "review_by": "2099-01-01",
                }
            ],
        }
        flat_ledger_path = tmp_path / "flat-ledger.json"
        flat_ledger_path.write_text(json.dumps(flat_ledger), encoding="utf-8")
        check(
            "SHAPE (all-flat, matched nothing, root is known-invisible)",
            evaluate_argv(flat_ledger_path, ["."]),
            json.dumps(_flat(0, 0, [])),
            0,
            must_contain=("Checkov evaluated:",),
        )

        # 13b. SHAPE (all-flat, WITH a real failure present) -- restored
        #      (PR #490 architect review 6c; the old script's own suite
        #      didn't stop at the 0/0 "matched nothing" case for this
        #      shape). A real failure must be read from the flat shape's OWN
        #      top-level `failed_checks`/`failed` fields exactly as the
        #      nested shape reads them from `summary`/`results` -- not
        #      silently accepted as "the special empty case".
        check(
            "SHAPE (all-flat, WITH a real failure present)",
            evaluate_argv(flat_ledger_path, ["."]),
            json.dumps(_flat(0, 1, [("CKV_AWS_18", "aws_s3_bucket.a", "/dirA/main.tf")])),
            1,
            must_contain=("NEW Checkov failure", "aws_s3_bucket.a"),
        )

        # 14. COVERAGE -- a known-invisible directory starts producing
        #     results: Checkov can see it now, and that needs a review.
        data = copy.deepcopy(_baseline_checkov_data())
        data["results"]["passed_checks"].append(_passed_check("CKV_GIT_1", "github_repository.x", "/dirC/main.tf"))
        check(
            "COVERAGE (known-invisible directory now producing results)",
            evaluate_argv(ledger_path, TRACKED_DIRS),
            json.dumps(data),
            1,
            must_contain=("known_invisible director", "now producing", "dirC"),
        )

        # 15. COVERAGE -- a tracked Terraform directory produced nothing and
        #     isn't known-invisible either.
        check(
            "COVERAGE (a .tf directory in neither the producing nor the known_invisible set)",
            evaluate_argv(ledger_path, [*TRACKED_DIRS, "dirD"]),
            json.dumps(_baseline_checkov_data()),
            1,
            must_contain=("Coverage gap", "dirD"),
        )

        # 15b. OPAQUE FILE -- a tracked file whose suffix Checkov's
        #      directory walk cannot read (OPAQUE_TERRAFORM_SUFFIXES) is red
        #      unless its OWN directory is a reviewed known_invisible entry
        #      -- the file-level rule the fresh-session review of PR #493
        #      named as a T4b candidate ("opacity is per file, not per
        #      directory": a `main.tf` neighbour keeps the directory
        #      producing, so decision 5's directory-level rule alone can
        #      never see this file drop out). `dirD` is outside TRACKED_DIRS
        #      entirely, so this is exercised in isolation from the
        #      directory-coverage check above.
        check(
            "OPAQUE FILE (tracked opaque-suffix file outside any known_invisible directory)",
            evaluate_argv(ledger_path, TRACKED_DIRS, tracked_files=["dirD/extra.tf.json"]),
            json.dumps(_baseline_checkov_data()),
            1,
            must_contain=("opaque Terraform-adjacent file", "dirD/extra.tf.json"),
        )

        # 15c. OPAQUE FILE -- the same suffix, but inside a directory
        #      ALREADY reviewed as known_invisible (`dirC`), is exempt: that
        #      directory's review already covers everything Checkov cannot
        #      see in it.
        check(
            "OPAQUE FILE (exempt inside an already-reviewed known_invisible directory)",
            evaluate_argv(ledger_path, TRACKED_DIRS, tracked_files=["dirC/extra.tofu.json"]),
            json.dumps(_baseline_checkov_data()),
            0,
            must_contain=("Checkov evaluated:",),
            must_not_contain=("opaque Terraform-adjacent file",),
        )

        # 15d. OPAQUE FILE -- the headline case: a directory ALREADY
        #      producing results (`dirA`, via the baseline's accepted
        #      failure) cannot STAY green if turned into a known_invisible
        #      entry -- `now_visible` (case 14 above) refuses any directory
        #      that produces anything, so the opaque error is traded for a
        #      DIFFERENT one ("known_invisible directory now producing
        #      Checkov results") -- the job is never actually greened. An
        #      earlier draft of the error message suggested that trade as a
        #      remedy anyway (/critic-gate finding, T4b architect pass, round 1,
        #      live-verified). The fixed message names the real remedy
        #      (move, rename, or delete the file) and makes no promise a
        #      ledger edit alone can satisfy.
        check(
            "OPAQUE FILE (in an already-producing directory -- no ledger row can accept it)",
            evaluate_argv(ledger_path, TRACKED_DIRS, tracked_files=["dirA/extra.tf.json"]),
            json.dumps(_baseline_checkov_data()),
            1,
            must_contain=("opaque Terraform-adjacent file", "dirA/extra.tf.json", "no ledger row shape accepts"),
            must_not_contain=("add a reviewed known_invisible entry",),
        )

        # 15e. OPAQUE FILE -- omitting `--tracked-file` while `--tracked-dir`
        #      is set must fail loudly, not pass vacuously (/critic-gate
        #      finding, T4b security-critic pass, round 1): an empty
        #      tracked_files list satisfies "no exposed opaque file" by
        #      construction, which is the exact wrong-direction failure mode
        #      `git_tracked_terraform_files`'s own docstring names for the
        #      sibling case. Built directly (not via `evaluate_argv`, which
        #      always supplies a baseline file per dir) to exercise the raw
        #      CLI contract a real caller could get wrong.
        check(
            "EVALUATE (tracked_dirs non-empty, tracked_files omitted -- fails loudly, not vacuously green)",
            ["evaluate", "--ledger", str(ledger_path), "--checkov-version", "3.3.8",
             *[x for d in TRACKED_DIRS for x in ("--tracked-dir", d)]],
            json.dumps(_baseline_checkov_data()),
            1,
            must_contain=("tracked_dirs is non-empty but tracked_files is empty",),
        )

        # 15f. The mirror direction (/critic-gate finding, T4b architect pass,
        #      round 2): `--tracked-file` supplied, `--tracked-dir` omitted,
        #      must also fail loudly -- an empty tracked_dirs would vacuously
        #      satisfy the WHOLE directory-coverage check, not just the
        #      opaque-file one.
        check(
            "EVALUATE (tracked_files non-empty, tracked_dirs omitted -- fails loudly, not vacuously green)",
            ["evaluate", "--ledger", str(ledger_path), "--checkov-version", "3.3.8",
             "--tracked-file", "dirA/main.tf"],
            json.dumps(_baseline_checkov_data()),
            1,
            must_contain=("tracked_files is non-empty but tracked_dirs is empty",),
        )

        # 15g. The third corner (/critic-gate finding, T4b security-critic
        #      pass, round 3): BOTH flags omitted satisfies neither of the
        #      two one-sided checks above, so every coverage check would
        #      vacuously pass -- the strictly worse case of the two already
        #      closed.
        check(
            "EVALUATE (both tracked_dirs and tracked_files omitted -- fails loudly, not vacuously green)",
            ["evaluate", "--ledger", str(ledger_path), "--checkov-version", "3.3.8"],
            json.dumps(_baseline_checkov_data()),
            1,
            must_contain=("tracked_dirs and tracked_files are both empty",),
        )

        # 16. REVIEW_BY -- a group past its date is red regardless of
        #     whether anything else drifted (decision 7).
        stale_ledger = copy.deepcopy(SMALL_LEDGER)
        stale_ledger["failures"][0]["review_by"] = "2000-01-01"
        stale_ledger_path = tmp_path / "stale-ledger.json"
        stale_ledger_path.write_text(json.dumps(stale_ledger), encoding="utf-8")
        check(
            "REVIEW_BY (a group's date has passed)",
            evaluate_argv(stale_ledger_path, TRACKED_DIRS, today="2026-09-14"),
            json.dumps(_baseline_checkov_data()),
            1,
            must_contain=("stale acceptance", "CKV_AWS_18"),
        )

        # 16b. REVIEW_BY -- the same rule, for a SKIP group. Decision 7 says
        #      "every group (failure, skip, or known-invisible)".
        stale_skip_ledger = copy.deepcopy(SMALL_LEDGER)
        stale_skip_ledger["skips"][0]["review_by"] = "2000-01-01"
        stale_skip_ledger_path = tmp_path / "stale-skip-ledger.json"
        stale_skip_ledger_path.write_text(json.dumps(stale_skip_ledger), encoding="utf-8")
        check(
            "REVIEW_BY (a SKIP group's date has passed)",
            evaluate_argv(stale_skip_ledger_path, TRACKED_DIRS, today="2026-09-14"),
            json.dumps(_baseline_checkov_data()),
            1,
            must_contain=("stale acceptance", "CKV_AWS_109"),
        )

        # 16c. REVIEW_BY -- the same rule, for a KNOWN_INVISIBLE entry.
        stale_ki_ledger = copy.deepcopy(SMALL_LEDGER)
        stale_ki_ledger["known_invisible"][0]["review_by"] = "2000-01-01"
        stale_ki_ledger_path = tmp_path / "stale-ki-ledger.json"
        stale_ki_ledger_path.write_text(json.dumps(stale_ki_ledger), encoding="utf-8")
        check(
            "REVIEW_BY (a KNOWN_INVISIBLE entry's date has passed)",
            evaluate_argv(stale_ki_ledger_path, TRACKED_DIRS, today="2026-09-14"),
            json.dumps(_baseline_checkov_data()),
            1,
            must_contain=("stale acceptance", "dirC"),
        )

        # 16d. REVIEW_BY -- TWO stale groups, one with NO `check_id` and one
        #      with a normal string `check_id`, must still exit 1 with a
        #      clean `::error::`, not a `TypeError` traceback from `sorted()`
        #      comparing `str` against `None` in the same list (/critic-gate
        #      finding, PR #490 architect review 6b) -- a SINGLE stale group
        #      missing `check_id` would not exercise the comparison at all,
        #      since `sorted()` on a one-element list never compares
        #      anything. `run` always validates first (which requires
        #      `check_id`), so this is reachable only via the `evaluate`
        #      subcommand directly on a malformed ledger -- exercised here
        #      exactly that way, never through `run`.
        stale_no_check_id_ledger = copy.deepcopy(SMALL_LEDGER)
        del stale_no_check_id_ledger["failures"][0]["check_id"]
        stale_no_check_id_ledger["failures"][0]["review_by"] = "2000-01-01"
        stale_no_check_id_ledger["skips"][0]["review_by"] = "2000-01-01"
        stale_no_check_id_ledger_path = tmp_path / "stale-no-check-id-ledger.json"
        stale_no_check_id_ledger_path.write_text(json.dumps(stale_no_check_id_ledger), encoding="utf-8")
        check(
            "REVIEW_BY (one stale group with no check_id, mixed with a normal one -- no traceback)",
            evaluate_argv(stale_no_check_id_ledger_path, TRACKED_DIRS, today="2026-09-14"),
            json.dumps(_baseline_checkov_data()),
            1,
            must_contain=("stale acceptance",),
            must_not_contain=("Traceback", "TypeError"),
        )

        # 16d2. `evaluate` matching a PROVIDER-shaped and a DOT-LESS-shaped
        #       failure (decision 13's other two address shapes; Amendment 2
        #       census). `evaluate`'s own comparison was always shape-agnostic
        #       (an exact string triple match) -- only `validate`'s
        #       cross-check was narrow -- so this locks in that the widened
        #       `validate` didn't accidentally narrow `evaluate` too.
        provider_dotless_ledger = {
            "checkov_version": "3.3.8",
            "failures": [
                {
                    "check_id": "CKV_AWS_41",
                    "reason": "provider-shaped address test fixture",
                    "tracking": f"https://github.com/{ORG}/infrastructure-core/issues/291",
                    "review_by": "2099-01-01",
                    "resources": [{"resource": "aws.default", "file_path": "/bootstrap/providers.tf"}],
                },
                {
                    "check_id": "CKV_TF_1",
                    "reason": "dot-less module address test fixture",
                    "tracking": f"https://github.com/{ORG}/infrastructure-core/issues/487",
                    "review_by": "2099-01-01",
                    "resources": [{"resource": "dns", "file_path": "/tenants/603identity/com/main.tf"}],
                },
            ],
            "skips": [],
            "known_invisible": [],
        }
        provider_dotless_ledger_path = tmp_path / "provider-dotless-ledger.json"
        provider_dotless_ledger_path.write_text(json.dumps(provider_dotless_ledger), encoding="utf-8")
        data = _nested(
            passed=0,
            failed=2,
            failing=[
                ("CKV_AWS_41", "aws.default", "/bootstrap/providers.tf"),
                ("CKV_TF_1", "dns", "/tenants/603identity/com/main.tf"),
            ],
        )
        check(
            "EVALUATE (matching a provider-shaped and a dot-less-shaped failure)",
            evaluate_argv(provider_dotless_ledger_path, ["bootstrap", "tenants/603identity/com"]),
            json.dumps(data),
            0,
            must_contain=("Checkov evaluated:",),
        )

        # --- `validate` -----------------------------------------------------
        fixture_root = tmp_path / "fixture-repo"
        (fixture_root / "dirA").mkdir(parents=True)
        (fixture_root / "dirB").mkdir(parents=True)
        (fixture_root / "dirC").mkdir(parents=True)
        (fixture_root / "dirA" / "main.tf").write_text('resource "aws_s3_bucket" "a" {\n  bucket = "x"\n}\n', encoding="utf-8")
        (fixture_root / "dirB" / "main.tf").write_text('data "aws_iam_policy_document" "b" {\n  statement {}\n}\n', encoding="utf-8")
        (fixture_root / "dirC" / "main.tf").write_text('resource "github_repository" "x" {\n  name = "x"\n}\n', encoding="utf-8")
        good_ledger_path = fixture_root / "checkov-ledger.json"
        good_ledger_path.write_text(json.dumps(SMALL_LEDGER), encoding="utf-8")
        # `validate` now needs `git` too (decision 13's module-block cross-file
        # search and decision 15's widened known_invisible check both read the
        # tracked Terraform file set) -- a real repo, not just real files.
        subprocess.run(["git", "init", "-q"], cwd=fixture_root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=fixture_root, check=True)
        subprocess.run(
            ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-q", "-m", "fixture"],
            cwd=fixture_root,
            check=True,
        )

        # 17. VALIDATE -- a well-formed ledger against real matching files.
        check(
            "VALIDATE (well-formed ledger, matching fixture files)",
            ["validate", "--ledger", "checkov-ledger.json"],
            "",
            0,
            must_contain=("OK: ledger validated",),
            cwd=fixture_root,
        )

        def _mutated_ledger(mutate) -> pathlib.Path:
            data = copy.deepcopy(SMALL_LEDGER)
            mutate(data)
            path = fixture_root / "mutated-ledger.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return path

        # 18. VALIDATE -- missing tracking.
        p = _mutated_ledger(lambda d: d["failures"][0].pop("tracking"))
        check(
            "VALIDATE (missing tracking)",
            ["validate", "--ledger", "mutated-ledger.json"],
            "",
            1,
            must_contain=("tracking",),
            cwd=fixture_root,
        )

        # 19. VALIDATE -- non-org tracking URL.
        p = _mutated_ledger(lambda d: d["failures"][0].__setitem__("tracking", "https://github.com/some-other-org/repo/issues/1"))
        check(
            "VALIDATE (non-org tracking URL)",
            ["validate", "--ledger", "mutated-ledger.json"],
            "",
            1,
            must_contain=("malformed `tracking`",),
            cwd=fixture_root,
        )

        # 20. VALIDATE -- missing reason (failure group).
        p = _mutated_ledger(lambda d: d["failures"][0].pop("reason"))
        check(
            "VALIDATE (missing reason on a failure group)",
            ["validate", "--ledger", "mutated-ledger.json"],
            "",
            1,
            must_contain=("missing `reason`",),
            cwd=fixture_root,
        )

        # 21. VALIDATE -- missing/unparseable review_by.
        p = _mutated_ledger(lambda d: d["skips"][0].__setitem__("review_by", "not-a-date"))
        check(
            "VALIDATE (unparseable review_by)",
            ["validate", "--ledger", "mutated-ledger.json"],
            "",
            1,
            must_contain=("unparseable `review_by`",),
            cwd=fixture_root,
        )

        # 22. VALIDATE -- a resource row whose file lacks the block.
        p = _mutated_ledger(lambda d: d["failures"][0]["resources"][0].__setitem__("resource", "aws_s3_bucket.does_not_exist"))
        check(
            "VALIDATE (resource row whose file lacks the block)",
            ["validate", "--ledger", "mutated-ledger.json"],
            "",
            1,
            must_contain=("has neither a `resource`/`data`", "tried as <type>.<name>", "nor a `provider \""),
            cwd=fixture_root,
        )

        # 23. VALIDATE -- a skip row missing suppress_comment.
        p = _mutated_ledger(lambda d: d["skips"][0]["resources"][0].pop("suppress_comment"))
        check(
            "VALIDATE (skip row missing suppress_comment)",
            ["validate", "--ledger", "mutated-ledger.json"],
            "",
            1,
            must_contain=("missing `suppress_comment`",),
            cwd=fixture_root,
        )

        # 24. VALIDATE -- a known_invisible directory that doesn't exist.
        p = _mutated_ledger(lambda d: d["known_invisible"][0].__setitem__("directory", "dirZ"))
        check(
            "VALIDATE (known_invisible directory does not exist)",
            ["validate", "--ledger", "mutated-ledger.json"],
            "",
            1,
            must_contain=("does not exist in this repo",),
            cwd=fixture_root,
        )

        # 25. VALIDATE -- a known_invisible directory holding no .tf files.
        (fixture_root / "dirEmpty").mkdir()
        (fixture_root / "dirEmpty" / "README.md").write_text("nothing terraform here\n", encoding="utf-8")
        p = _mutated_ledger(lambda d: d["known_invisible"][0].__setitem__("directory", "dirEmpty"))
        check(
            "VALIDATE (known_invisible directory holds no .tf files)",
            ["validate", "--ledger", "mutated-ledger.json"],
            "",
            1,
            must_contain=("holds no tracked Terraform file",),
            cwd=fixture_root,
        )

        # 22b. VALIDATE -- a known_invisible directory written with a trailing
        #      slash is named as the defect it is (it can never match the
        #      git-derived directory names `evaluate` compares against),
        #      not misdiagnosed as "holds no tracked Terraform file".
        mutated = json.loads(json.dumps(SMALL_LEDGER))
        mutated["known_invisible"][0]["directory"] = "dirC/"
        (fixture_root / "mutated-ledger.json").write_text(json.dumps(mutated), encoding="utf-8")
        check(
            "VALIDATE (known_invisible directory with a trailing slash is rejected by name)",
            ["validate", "--ledger", "mutated-ledger.json"],
            "",
            1,
            must_contain=("must be written exactly as",),
            must_not_contain=("holds no tracked Terraform file",),
            cwd=fixture_root,
        )

        # 22c. VALIDATE -- the same class, one step wider (`#494`, second
        #      fresh-session review of #493): `./dirC` has no leading or
        #      trailing slash, so 22b's original check let it through to
        #      "holds no tracked Terraform file" -- the wrong diagnosis,
        #      since no spelling but the exact `git ls-files` one can ever
        #      match in `evaluate`. `os.path.normpath` generalizes the fix.
        mutated = json.loads(json.dumps(SMALL_LEDGER))
        mutated["known_invisible"][0]["directory"] = "./dirC"
        (fixture_root / "mutated-ledger.json").write_text(json.dumps(mutated), encoding="utf-8")
        check(
            "VALIDATE (known_invisible directory spelled ./dirC is rejected by name)",
            ["validate", "--ledger", "mutated-ledger.json"],
            "",
            1,
            must_contain=("must be written exactly as",),
            must_not_contain=("holds no tracked Terraform file",),
            cwd=fixture_root,
        )

        # 22d. VALIDATE -- the same class, a spelling `os.path.normpath`
        #      alone does not touch: a LEADING `..` (`../dirC`) has nothing
        #      before it for normpath to cancel against, so it is already
        #      normpath-stable and `directory != normalized` alone missed it
        #      (/critic-gate finding, T4b architect pass, round 1,
        #      live-verified). Checked explicitly (`climbs_out`).
        mutated = json.loads(json.dumps(SMALL_LEDGER))
        mutated["known_invisible"][0]["directory"] = "../dirC"
        (fixture_root / "mutated-ledger.json").write_text(json.dumps(mutated), encoding="utf-8")
        check(
            "VALIDATE (known_invisible directory spelled ../dirC is rejected by name)",
            ["validate", "--ledger", "mutated-ledger.json"],
            "",
            1,
            must_contain=("must be written exactly as",),
            must_not_contain=("holds no tracked Terraform file",),
            cwd=fixture_root,
        )

        # 26. VALIDATE -- a duplicate check_id across two groups of the SAME
        #     kind. (A check_id shared across a failure group and a skip
        #     group is a different, ALLOWED shape -- see case 27b.)
        p = _mutated_ledger(lambda d: d["failures"].append(copy.deepcopy(d["failures"][0])))
        check(
            "VALIDATE (duplicate check_id across two failure groups)",
            ["validate", "--ledger", "mutated-ledger.json"],
            "",
            1,
            must_contain=("appears in more than one failure group",),
            cwd=fixture_root,
        )

        # 27. VALIDATE -- a duplicate (resource, file_path) within a group.
        p = _mutated_ledger(lambda d: d["failures"][0]["resources"].append(copy.deepcopy(d["failures"][0]["resources"][0])))
        check(
            "VALIDATE (duplicate resource row within a group)",
            ["validate", "--ledger", "mutated-ledger.json"],
            "",
            1,
            must_contain=("lists (", "more than once"),
            cwd=fixture_root,
        )

        # 27b. VALIDATE -- a check_id shared across a FAILURE group and a
        #      SKIP group is ALLOWED (Phase 5's own shape:
        #      sprint_plan.md's account of dissolving IAC-D29's F1 has one
        #      bucket's CKV_AWS_18 stay a ledger row while a sibling
        #      bucket's access-log skip cites the same check_id). Only a
        #      duplicate WITHIN one kind (case 26) is an error.
        p = _mutated_ledger(
            lambda d: d["skips"].append(
                {
                    "check_id": "CKV_AWS_18",
                    "tracking": d["failures"][0]["tracking"],
                    "review_by": "2099-01-01",
                    "resources": [
                        {"resource": "aws_iam_policy_document.b", "file_path": "/dirB/main.tf", "suppress_comment": " Phase 5 shape"}
                    ],
                }
            )
        )
        check(
            "VALIDATE (a check_id shared across a failure group and a skip group is allowed)",
            ["validate", "--ledger", "mutated-ledger.json"],
            "",
            0,
            must_contain=("OK: ledger validated",),
            cwd=fixture_root,
        )

        # 27b2. VALIDATE -- but the EXACT SAME (check_id, resource,
        #       file_path) triple pinned as BOTH a failure and a skip is
        #       rejected: Checkov can never report one check as both failed
        #       and skipped for the same resource, so that ledger could
        #       never match a real scan (/critic-gate round 2, architect --
        #       the per-kind check_id scoping above caught this only by
        #       accident before).
        p = _mutated_ledger(
            lambda d: d["skips"].append(
                {
                    "check_id": "CKV_AWS_18",
                    "tracking": d["failures"][0]["tracking"],
                    "review_by": "2099-01-01",
                    "resources": [
                        {"resource": "aws_s3_bucket.a", "file_path": "/dirA/main.tf", "suppress_comment": " unsatisfiable"}
                    ],
                }
            )
        )
        check(
            "VALIDATE (the same triple pinned as both a failure and a skip)",
            ["validate", "--ledger", "mutated-ledger.json"],
            "",
            1,
            must_contain=("is listed in more than one group",),
            cwd=fixture_root,
        )

        # 27c. VALIDATE -- a resource row's file_path escaping the repo via
        #      `..` must not be followed outside it.
        p = _mutated_ledger(lambda d: d["failures"][0]["resources"][0].__setitem__("file_path", "/../outside.tf"))
        check(
            "VALIDATE (resource file_path resolves outside the repo)",
            ["validate", "--ledger", "mutated-ledger.json"],
            "",
            1,
            must_contain=("resolves outside this repo",),
            cwd=fixture_root,
        )

        # 27d. VALIDATE -- the same containment concern for a known_invisible
        #      directory, now caught EARLIER: `#494`'s `climbs_out` check
        #      (case 22d) rejects a leading `..` by spelling before this
        #      containment check ever runs, so this is the spelling message,
        #      not "resolves outside this repo" -- confirming the two checks
        #      don't disagree about which one fires first for this input.
        p = _mutated_ledger(lambda d: d["known_invisible"][0].__setitem__("directory", "../outside"))
        check(
            "VALIDATE (known_invisible directory climbing out via .. is rejected by spelling first)",
            ["validate", "--ledger", "mutated-ledger.json"],
            "",
            1,
            must_contain=("must be written exactly as",),
            cwd=fixture_root,
        )

        # 28. VALIDATE -- THIS REPO'S OWN real ledger, against the real
        #     repo root. Living proof that T2's ledger is self-consistent --
        #     the current test 11, generalized.
        check(
            "VALIDATE (this repo's own checkov-ledger.json)",
            ["validate", "--ledger", "checkov-ledger.json"],
            "",
            0,
            must_contain=("OK: ledger validated",),
            cwd=REPO,
        )

        # --- `validate`'s address-shape dispatch (decision 13, Amendment 2) -

        shape_root = tmp_path / "shape-fixture"
        (shape_root / "module").mkdir(parents=True)
        (shape_root / "caller").mkdir(parents=True)
        (shape_root / "dnscaller").mkdir(parents=True)
        (shape_root / "providerfile").mkdir(parents=True)
        (shape_root / "module" / "main.tf").write_text(
            'resource "aws_iam_role" "tenant_state" {\n  name = "x"\n}\n', encoding="utf-8"
        )
        (shape_root / "caller" / "main.tf").write_text(
            'module "tenant_iam_role" {\n  source = "../module"\n}\n', encoding="utf-8"
        )
        (shape_root / "dnscaller" / "main.tf").write_text(
            'module "dns" {\n  source = "git::https://example.com/dns.git?ref=abc123"\n}\n', encoding="utf-8"
        )
        (shape_root / "providerfile" / "providers.tf").write_text(
            'provider "aws" {\n  region = "us-east-1"\n}\n\n'
            'provider "aws" {\n  alias  = "secondary"\n  region = "us-west-2"\n}\n',
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q"], cwd=shape_root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=shape_root, check=True)
        subprocess.run(
            ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-q", "-m", "fixture"],
            cwd=shape_root,
            check=True,
        )

        def _shape_ledger(resource: str, file_path: str, check_id: str = "CKV_TEST_1") -> dict:
            return {
                "checkov_version": "3.3.8",
                "failures": [
                    {
                        "check_id": check_id,
                        "reason": "shape dispatch test fixture",
                        "tracking": f"https://github.com/{ORG}/infrastructure-core/issues/487",
                        "review_by": "2099-01-01",
                        "resources": [{"resource": resource, "file_path": file_path}],
                    }
                ],
                "skips": [],
                "known_invisible": [],
            }

        def _check_shape(label: str, resource: str, file_path: str, expected_rc: int, *must_contain: str) -> None:
            path = shape_root / "shape-ledger.json"
            path.write_text(json.dumps(_shape_ledger(resource, file_path)), encoding="utf-8")
            check(
                label,
                ["validate", "--ledger", "shape-ledger.json"],
                "",
                expected_rc,
                must_contain=must_contain,
                cwd=shape_root,
            )

        # 28b. SHAPE (module-expanded) -- ACCEPT: the resource block is in
        #      the module's own file, and the caller's `module "<block>"`
        #      block is found in a DIFFERENT tracked file.
        _check_shape(
            "SHAPE module-expanded (accept)",
            "module.tenant_iam_role.aws_iam_role.tenant_state",
            "/module/main.tf",
            0,
            "OK: ledger validated",
        )

        # 28c. SHAPE (module-expanded) -- REJECT: no tracked file has the
        #      named `module` block (renamed/typo'd instance).
        _check_shape(
            "SHAPE module-expanded (reject: no module block anywhere)",
            "module.renamed_block.aws_iam_role.tenant_state",
            "/module/main.tf",
            1,
            "no tracked Terraform file has a `module \"renamed_block\"` block",
        )

        # 28d. SHAPE (module-expanded) -- REJECT: the resource/data block
        #      itself is missing from file_path (points at the caller's file
        #      instead of the module's own).
        _check_shape(
            "SHAPE module-expanded (reject: no resource block in file_path)",
            "module.tenant_iam_role.aws_iam_role.tenant_state",
            "/caller/main.tf",
            1,
            "has no `resource`/`data`",
            "module-expanded",
        )

        # 28e. SHAPE (module-expanded) -- REJECT: malformed segment count.
        _check_shape(
            "SHAPE module-expanded (reject: wrong segment count)",
            "module.tenant_iam_role.aws_iam_role",
            "/module/main.tf",
            1,
            "dot-separated segment",
        )

        # 28f. SHAPE (nested module address) -- REJECT explicitly, named,
        #      no attempt to match (decision 13).
        _check_shape(
            "SHAPE nested module address (reject, explicit)",
            "module.a.module.b.aws_iam_role.tenant_state",
            "/module/main.tf",
            1,
            "nested module address",
        )

        # 28f2. SHAPE (indexed count/for_each address) -- REJECT explicitly,
        #       naming the shape, for all three renderings Checkov 3.3.8
        #       emits (fresh-session review, PR #493). Before this branch
        #       existed the first and third fell through to a message
        #       blaming a typo/rename; the second, whose for_each key
        #       contains a dot, to "not a recognized address shape".
        for indexed in (
            "aws_s3_bucket.counted[0]",
            'aws_s3_bucket.eached["b.c"]',
            "module.tenant_iam_role[0].aws_iam_role.tenant_state",
        ):
            _check_shape(
                f"SHAPE indexed address (reject, explicit): {indexed}",
                indexed,
                "/module/main.tf",
                1,
                "indexed (`count`/`for_each`) address",
            )

        # 28g. SHAPE (dot-less module) -- ACCEPT.
        _check_shape(
            "SHAPE dot-less module (accept)",
            "dns",
            "/dnscaller/main.tf",
            0,
            "OK: ledger validated",
        )

        # 28h. SHAPE (dot-less module) -- REJECT: no matching module block.
        _check_shape(
            "SHAPE dot-less module (reject: no module block)",
            "notdns",
            "/dnscaller/main.tf",
            1,
            'has no `module "notdns"` block',
        )

        # 28i. SHAPE (provider, default alias) -- ACCEPT: no `alias =` check
        #      needed when the address's alias segment is "default".
        _check_shape(
            "SHAPE provider default alias (accept)",
            "aws.default",
            "/providerfile/providers.tf",
            0,
            "OK: ledger validated",
        )

        # 28j. SHAPE (provider, non-default alias) -- ACCEPT: the provider
        #      block AND a matching `alias = "..."` are both present.
        _check_shape(
            "SHAPE provider non-default alias (accept)",
            "aws.secondary",
            "/providerfile/providers.tf",
            0,
            "OK: ledger validated",
        )

        # 28k. SHAPE (provider) -- REJECT: the provider block exists but no
        #      matching alias -- tried only AFTER <type>.<name> fails
        #      (decision 13's fallback order), so this exercises that path.
        _check_shape(
            "SHAPE provider (reject: wrong alias)",
            "aws.tertiary",
            "/providerfile/providers.tf",
            1,
            'has a `provider "aws"` block but no',
            'alias = "tertiary"',
        )

        # 28l. SHAPE (provider) -- REJECT: neither a <type>.<name> block nor
        #      a matching provider block -- the combined fallback message,
        #      naming BOTH shapes it tried (decision 13's own requirement).
        _check_shape(
            "SHAPE (reject: neither <type>.<name> nor <provider>.<alias>)",
            "azurerm.default",
            "/providerfile/providers.tf",
            1,
            "has neither a `resource`/`data`",
            "nor a `provider \"",
        )

        # 28m. SHAPE -- REJECT: not a recognized address shape at all (more
        #      than one dot, not module-prefixed).
        _check_shape(
            "SHAPE (reject: unrecognized address, too many dots)",
            "aws_s3_bucket.a.b",
            "/module/main.tf",
            1,
            "not a recognized address shape",
        )

        # 28n. COVERAGE -- the widened tracked set (decision 15, U3, revised
        #      TWICE by /critic-gate: round 1 added the JSON-opaque trio
        #      tracked precisely BECAUSE Checkov can't see them; round 2
        #      added `.tofu`/`.tofu.json`, the same opacity class round 1
        #      missed). Every one of the eight patterns is tracked; a decoy
        #      non-matching file (`README.md`) must not create a tracked
        #      directory of its own.
        checks += 1
        widened_git_root = tmp_path / "widened-git-fixture"
        pattern_dirs = {
            "dirA": ("main.tf", "# tf\n"),
            "dirB": ("x.tftest.hcl", "# tftest\n"),
            "dirC": ("main.tf.json", "{}\n"),
            "dirD": ("x.tofutest.hcl", "# tofutest\n"),
            "dirE": ("x.tftest.json", "{}\n"),
            "dirF": ("x.tofutest.json", "{}\n"),
            "dirH": ("main.tofu", "# tofu\n"),
            "dirI": ("main.tofu.json", "{}\n"),
            # A non-ASCII path: without `git ls-files -z` this came back
            # C-quoted as the literal `"dirJ/caf\303\251.tf"` and produced
            # a directory named `"dirJ` (fresh-session review, PR #493).
            "dirJ": ("caf\u00e9.tf", "# non-ascii\n"),
        }
        for d, (fname, content) in pattern_dirs.items():
            (widened_git_root / d).mkdir(parents=True)
            (widened_git_root / d / fname).write_text(content, encoding="utf-8")
        (widened_git_root / "dirG").mkdir(parents=True)
        (widened_git_root / "dirG" / "README.md").write_text("not terraform\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=widened_git_root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=widened_git_root, check=True)
        subprocess.run(
            ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-q", "-m", "fixture"],
            cwd=widened_git_root,
            check=True,
        )
        got_files = ledger_module.git_tracked_terraform_files(widened_git_root)
        got_dirs = ledger_module._dirs_from_files(got_files)
        expected_dirs = set(pattern_dirs)
        if got_dirs != expected_dirs:
            failures.append(
                f"git_tracked_terraform_files: expected dirs {expected_dirs!r} (dirG's "
                f"README.md excluded), got {got_dirs!r} from files {got_files!r}"
            )

        # --- `run`'s early guards (no live Checkov -- see A4) ---------------

        # 29. RUN -- refuses on a planted .checkov.yaml, before ever
        #     shelling out to Checkov.
        (fixture_root / ".checkov.yaml").write_text("skip-path:\n  - dirA\n", encoding="utf-8")
        check(
            "RUN (refuses on a planted .checkov.yaml)",
            ["run", "--ledger", "checkov-ledger.json"],
            "",
            1,
            must_contain=(".checkov.yaml exists",),
            cwd=fixture_root,
        )
        (fixture_root / ".checkov.yaml").unlink()

        # 30. RUN -- fails loudly with no .git, never falls back to a
        #     filesystem walk (U2, #488). `fixture_root` is now a real repo
        #     (needed by `validate`'s own git dependency, above) -- this
        #     case needs its OWN, deliberately git-less, fixture.
        no_git_root = tmp_path / "no-git-fixture"
        (no_git_root / "dirA").mkdir(parents=True)
        (no_git_root / "dirB").mkdir(parents=True)
        (no_git_root / "dirC").mkdir(parents=True)
        (no_git_root / "dirA" / "main.tf").write_text('resource "aws_s3_bucket" "a" {\n  bucket = "x"\n}\n', encoding="utf-8")
        (no_git_root / "dirB" / "main.tf").write_text('data "aws_iam_policy_document" "b" {\n  statement {}\n}\n', encoding="utf-8")
        (no_git_root / "dirC" / "main.tf").write_text('resource "github_repository" "x" {\n  name = "x"\n}\n', encoding="utf-8")
        (no_git_root / "checkov-ledger.json").write_text(json.dumps(SMALL_LEDGER), encoding="utf-8")
        check(
            "RUN (fails loudly with no .git)",
            ["run", "--ledger", "checkov-ledger.json"],
            "",
            1,
            must_contain=("Could not list git-tracked Terraform files",),
            cwd=no_git_root,
        )

        # 30b. RUN -- a .checkov.yaml at $HOME (one of Checkov's own
        #      config-discovery locations, not just the scan root) is also
        #      caught. Needs a real .git so the run gets past the earlier
        #      guards to reach this one; the fixture never needs to scan.
        git_home_root = tmp_path / "home-fixture"
        (git_home_root / "dirA").mkdir(parents=True)
        (git_home_root / "dirB").mkdir(parents=True)
        (git_home_root / "dirC").mkdir(parents=True)
        (git_home_root / "dirA" / "main.tf").write_text('resource "aws_s3_bucket" "a" {\n  bucket = "x"\n}\n', encoding="utf-8")
        (git_home_root / "dirB" / "main.tf").write_text('data "aws_iam_policy_document" "b" {\n  statement {}\n}\n', encoding="utf-8")
        (git_home_root / "dirC" / "main.tf").write_text('resource "github_repository" "x" {\n  name = "x"\n}\n', encoding="utf-8")
        (git_home_root / "checkov-ledger.json").write_text(json.dumps(SMALL_LEDGER), encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=git_home_root, check=True)
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        (fake_home / ".checkov.yml").write_text("skip-check:\n  - CKV_AWS_18\n", encoding="utf-8")
        check(
            "RUN (a .checkov.yml at $HOME, not the scan root, is also caught)",
            ["run", "--ledger", "checkov-ledger.json"],
            "",
            1,
            must_contain=(".checkov.yml exists",),
            cwd=git_home_root,
            env={**os.environ, "HOME": str(fake_home)},
        )

        # 30c. RUN -- refuses when --directory does not resolve to the
        #      current working directory (coverage/validate assume they
        #      agree -- see "WHAT THIS DOES NOT DO").
        check(
            "RUN (--directory other than the working directory is refused)",
            ["run", "--ledger", "checkov-ledger.json", "--directory", "dirA"],
            "",
            1,
            must_contain=("does not resolve to the current working directory",),
            cwd=git_home_root,
        )

        # 31. `git_tracked_terraform_files` success path, exercised directly
        #     against a real (if minimal) git repository.
        checks += 1
        git_root = tmp_path / "git-fixture"
        (git_root / "dirA").mkdir(parents=True)
        (git_root / "dirB").mkdir(parents=True)
        (git_root / "dirA" / "main.tf").write_text("# tf\n", encoding="utf-8")
        (git_root / "dirB" / "main.tf").write_text("# tf\n", encoding="utf-8")
        (git_root / "dirB" / "README.md").write_text("not terraform\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=git_root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=git_root, check=True)
        subprocess.run(
            ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-q", "-m", "fixture"],
            cwd=git_root,
            check=True,
        )
        got = ledger_module._dirs_from_files(ledger_module.git_tracked_terraform_files(git_root))
        if got != {"dirA", "dirB"}:
            failures.append(f"git_tracked_terraform_files: expected dirs {{'dirA', 'dirB'}}, got {got!r}")

        # 32. `git_tracked_terraform_files` raises on an EMPTY result -- a
        #     coverage rule with zero tracked files is vacuously satisfied by
        #     anything, which is worse than a loud failure.
        checks += 1
        empty_git_root = tmp_path / "empty-git-fixture"
        empty_git_root.mkdir()
        (empty_git_root / "README.md").write_text("no terraform here\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=empty_git_root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=empty_git_root, check=True)
        subprocess.run(
            ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-q", "-m", "fixture"],
            cwd=empty_git_root,
            check=True,
        )
        try:
            ledger_module.git_tracked_terraform_files(empty_git_root)
            failures.append("git_tracked_terraform_files: expected RuntimeError on zero tracked files, got none")
        except RuntimeError as exc:
            if "no tracked files" not in str(exc):
                failures.append(f"git_tracked_terraform_files: wrong error on zero tracked files: {exc}")

        # 32b. `git_tracked_terraform_files` raises `RuntimeError`, not a bare
        #      `FileNotFoundError` traceback, when `git` itself is not on
        #      PATH -- newly reachable from `cmd_validate` as of T4a, which
        #      did not call this function before this diff (/critic-gate
        #      finding). A PATH with no `git` anywhere on it, rather than
        #      deleting the real binary.
        checks += 1
        no_git_path_root = tmp_path / "no-git-on-path-fixture"
        no_git_path_root.mkdir()
        real_path = os.environ.get("PATH", "")
        os.environ["PATH"] = "/nonexistent-bin-dir"
        try:
            ledger_module.git_tracked_terraform_files(no_git_path_root)
            failures.append("git_tracked_terraform_files: expected RuntimeError with no `git` on PATH, got none")
        except RuntimeError as exc:
            if "could not run" not in str(exc):
                failures.append(f"git_tracked_terraform_files: wrong error with no `git` on PATH: {exc}")
        except FileNotFoundError as exc:
            failures.append(f"git_tracked_terraform_files: bare FileNotFoundError escaped, not wrapped: {exc}")
        finally:
            os.environ["PATH"] = real_path

        # 32c. `classify_resource_address`'s file reader reports an invalid-
        #      UTF-8 tracked file as a validate error, not a bare
        #      `UnicodeDecodeError` traceback -- reachable because a
        #      module-expanded row's block search scans EVERY tracked file
        #      (/critic-gate finding).
        badenc_root = tmp_path / "bad-encoding-fixture"
        (badenc_root / "module").mkdir(parents=True)
        (badenc_root / "caller").mkdir(parents=True)
        (badenc_root / "module" / "main.tf").write_text(
            'resource "aws_iam_role" "x" {\n  name = "x"\n}\n', encoding="utf-8"
        )
        (badenc_root / "caller" / "main.tf").write_bytes(b'module "block" {\n  \xff\xfe garbage\n}\n')
        subprocess.run(["git", "init", "-q"], cwd=badenc_root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=badenc_root, check=True)
        subprocess.run(
            ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-q", "-m", "fixture"],
            cwd=badenc_root,
            check=True,
        )
        badenc_ledger = {
            "checkov_version": "3.3.8",
            "failures": [
                {
                    "check_id": "CKV_TEST_1",
                    "reason": "bad-encoding fixture",
                    "tracking": f"https://github.com/{ORG}/infrastructure-core/issues/487",
                    "review_by": "2099-01-01",
                    "resources": [{"resource": "module.block.aws_iam_role.x", "file_path": "/module/main.tf"}],
                }
            ],
            "skips": [],
            "known_invisible": [],
        }
        (badenc_root / "checkov-ledger.json").write_text(json.dumps(badenc_ledger), encoding="utf-8")
        check(
            "VALIDATE (a tracked file that is not valid UTF-8 does not crash the module-block search)",
            ["validate", "--ledger", "checkov-ledger.json"],
            "",
            1,
            must_contain=('no tracked Terraform file has a `module "block"` block',),
            must_not_contain=("Traceback", "UnicodeDecodeError"),
            cwd=badenc_root,
        )

        # 33. `_sanitize_env` drops every CKV_*/BC_*/PRISMA_* key (the
        #     silent, un-audited equivalents of --skip-check and friends)
        #     and keeps everything else -- exercised directly since the
        #     live checkov invocation it protects is CI's job, not this
        #     gate's (A4).
        checks += 1
        sanitized = ledger_module._sanitize_env(
            {
                "CKV_SKIP_CHECK": "CKV_AWS_18",
                "BC_API_KEY": "x",
                "PRISMA_API_URL": "y",
                "CHECKOV_ENABLE_FOREACH_HANDLING": "false",
                "DOWNLOAD_EXTERNAL_MODULES": "true",
                "EXTERNAL_MODULES_DIR": "/tmp/mods",
                "PATH": "/usr/bin",
            }
        )
        if sanitized != {"PATH": "/usr/bin"}:
            failures.append(f"_sanitize_env: expected only PATH to survive, got {sanitized!r}")

        # 34. `evaluate` does not crash (a bare Python traceback, not an
        #     `::error::`) on a ledger group missing `check_id` -- reachable
        #     directly through the `evaluate` subcommand, which (unlike
        #     `run`) never calls `validate_ledger` first.
        malformed_ledger_path = tmp_path / "malformed-ledger.json"
        malformed_ledger = copy.deepcopy(SMALL_LEDGER)
        del malformed_ledger["failures"][0]["check_id"]
        malformed_ledger_path.write_text(json.dumps(malformed_ledger), encoding="utf-8")
        check(
            "EVALUATE (a ledger group missing check_id does not crash)",
            evaluate_argv(malformed_ledger_path, TRACKED_DIRS),
            json.dumps(_baseline_checkov_data()),
            1,
            must_contain=("NEW Checkov failure",),
            must_not_contain=("Traceback",),
        )

        # --- `run`'s live-Checkov integration, via a stub `checkov` on PATH
        #     (pattern: scripts/test_post_merge_watch.sh does this for `gh` --
        #     drive the REAL `run` end-to-end, stub only the external tool;
        #     #490 review item 6d): crash guard (`returncode not in (0, 1)`),
        #     `--version` plumbing, bad JSON, and `write_step_summary`. -----

        stub_repo = tmp_path / "stub-checkov-repo"
        (stub_repo / "dirA").mkdir(parents=True)
        (stub_repo / "dirB").mkdir(parents=True)
        (stub_repo / "dirC").mkdir(parents=True)
        (stub_repo / "dirA" / "main.tf").write_text('resource "aws_s3_bucket" "a" {\n  bucket = "x"\n}\n', encoding="utf-8")
        (stub_repo / "dirB" / "main.tf").write_text('data "aws_iam_policy_document" "b" {\n  statement {}\n}\n', encoding="utf-8")
        (stub_repo / "dirC" / "main.tf").write_text('resource "github_repository" "x" {\n  name = "x"\n}\n', encoding="utf-8")
        (stub_repo / "checkov-ledger.json").write_text(json.dumps(SMALL_LEDGER), encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=stub_repo, check=True)
        subprocess.run(["git", "add", "-A"], cwd=stub_repo, check=True)
        subprocess.run(
            ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-q", "-m", "fixture"],
            cwd=stub_repo,
            check=True,
        )

        stub_bin = tmp_path / "stub-bin"
        stub_bin.mkdir()
        stub_checkov = stub_bin / "checkov"
        stub_checkov.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "if '--version' in sys.argv:\n"
            "    print(os.environ.get('STUB_CHECKOV_VERSION', '3.3.8'))\n"
            "    sys.exit(0)\n"
            "mode = os.environ.get('STUB_CHECKOV_SCAN_MODE', 'ok')\n"
            "if mode == 'crash':\n"
            "    sys.stderr.write('kaboom\\n')\n"
            "    sys.exit(2)\n"
            "if mode == 'badjson':\n"
            "    sys.stdout.write('not json {{{')\n"
            "    sys.exit(0)\n"
            "sys.stdout.write(os.environ.get('STUB_CHECKOV_JSON', '{}'))\n"
            "sys.exit(int(os.environ.get('STUB_CHECKOV_EXIT', '0')))\n",
            encoding="utf-8",
        )
        stub_checkov.chmod(0o755)

        def _stub_env(**extra) -> dict:
            env = dict(os.environ)
            env["PATH"] = f"{stub_bin}{os.pathsep}{env.get('PATH', '')}"
            env.pop("GITHUB_STEP_SUMMARY", None)
            env.update(extra)
            return env

        matching_json = json.dumps(_baseline_checkov_data())

        # 35. RUN end-to-end (stub checkov) -- `--version` plumbing: the
        #     installed version genuinely reaches `evaluate`'s comparison,
        #     not a value read from the scan JSON (decision 6).
        check(
            "RUN end-to-end (stub checkov, --version plumbing: mismatch is red)",
            ["run", "--ledger", "checkov-ledger.json"],
            "",
            1,
            must_contain=("ledger's checkov_version",),
            cwd=stub_repo,
            env=_stub_env(STUB_CHECKOV_VERSION="9.9.9", STUB_CHECKOV_SCAN_MODE="ok", STUB_CHECKOV_JSON=matching_json),
        )

        # 36. RUN end-to-end (stub checkov) -- crash guard: an exit code
        #     outside {0, 1} is a crash, reported before any JSON is read,
        #     and the crash's own exit code is propagated.
        check(
            "RUN end-to-end (stub checkov, crash guard: exit code outside {0,1})",
            ["run", "--ledger", "checkov-ledger.json"],
            "",
            2,
            must_contain=("a crash, not just",),
            cwd=stub_repo,
            env=_stub_env(STUB_CHECKOV_VERSION="3.3.8", STUB_CHECKOV_SCAN_MODE="crash"),
        )

        # 37. RUN end-to-end (stub checkov) -- bad JSON: checkov exits clean
        #     but its stdout does not parse.
        check(
            "RUN end-to-end (stub checkov, bad JSON output)",
            ["run", "--ledger", "checkov-ledger.json"],
            "",
            1,
            must_contain=("Could not parse Checkov output as JSON",),
            cwd=stub_repo,
            env=_stub_env(STUB_CHECKOV_VERSION="3.3.8", STUB_CHECKOV_SCAN_MODE="badjson"),
        )

        # 38. RUN end-to-end (stub checkov) -- full match: exit 0, AND
        #     `write_step_summary` actually wrote $GITHUB_STEP_SUMMARY (a
        #     green run still SHOWS the accepted debt -- decision 1).
        step_summary_path = tmp_path / "step-summary.md"
        check(
            "RUN end-to-end (stub checkov, exact match -> green, step summary written)",
            ["run", "--ledger", "checkov-ledger.json"],
            "",
            0,
            must_contain=("Checkov evaluated:",),
            cwd=stub_repo,
            env=_stub_env(
                STUB_CHECKOV_VERSION="3.3.8",
                STUB_CHECKOV_SCAN_MODE="ok",
                STUB_CHECKOV_JSON=matching_json,
                GITHUB_STEP_SUMMARY=str(step_summary_path),
            ),
        )
        checks += 1
        summary_text = step_summary_path.read_text(encoding="utf-8") if step_summary_path.exists() else ""
        if "CKV_AWS_18" not in summary_text or "CKV_AWS_109" not in summary_text:
            failures.append(
                f"write_step_summary: expected the accepted failure and skip groups in "
                f"$GITHUB_STEP_SUMMARY, got: {summary_text!r}"
            )

    EXPECTED_CHECK_COUNT = 88
    if checks != EXPECTED_CHECK_COUNT:
        failures.append(
            f"COVERAGE: ran {checks} checks, expected {EXPECTED_CHECK_COUNT}. Update "
            f"EXPECTED_CHECK_COUNT deliberately when adding a case -- never to make a red "
            f"suite green."
        )

    if failures:
        print(f"FAIL: {len(failures)} of {checks} guard checks did not hold:", file=sys.stderr)
        for msg in failures:
            print(f"  - {msg}", file=sys.stderr)
        return 1

    print(f"OK: checkov_ledger.py falsified -- {checks} checks all behaved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
