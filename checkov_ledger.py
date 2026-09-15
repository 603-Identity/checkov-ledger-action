#!/usr/bin/env python3
"""Compare a live Checkov scan against a reviewed ledger of accepted findings.

Sprint 15 (`IAC-D47`, tracked by `#487`; the real `CKV_AWS_18` gap stays
`#291`). Replaces `scripts/assert_checkov_known_findings.py`'s "one
deliberately-red job pinning one check_id" with a general-purpose evaluator:
it reads its pins from a data file (`checkov-ledger.json`) instead of two
Python constants, it covers every check Checkov reports rather than one, and
it exits 0 on an exact match -- no job in this org is designed to be red
(`IAC-D47`). Every falsification case that script's test suite carried over
(re-targeted to the new exit-0-on-match semantics) -- see
`test_checkov_ledger.py`.

WHAT IS PINNED, resource-scoped (`checkov-ledger.json`'s schema)
------------------------------------------------------------------
* **Failures**: grouped by `check_id`. Each group carries `reason`,
  `tracking` (a GitHub issue URL in this org), `review_by` (ISO date), and a
  `resources` list of `{resource, file_path}` rows. The key is
  `(check_id, resource, file_path)` -- not `resource` alone, which is the
  exact bypass the old script's own docstring records: two tenant configs
  declaring identically-named resources collapse into one pinned entry under
  a `resource`-only key.
* **Skips**: grouped the same way, but each row also pins the inline
  `# checkov:skip=` comment Checkov reports as `suppress_comment` -- the
  justification lives once, in the code a reader already sees; a changed
  justification is drift, not a value this ledger duplicates.
* **Coverage by directory**: every directory `git ls-files` shows holding at
  least one tracked Terraform file (`TRACKED_TERRAFORM_PATTERNS`) must
  either produce at least one Checkov result or appear in `known_invisible`
  (with its own `reason`/`tracking`/`review_by`).
  A known-invisible directory that starts producing results is red --
  Checkov can see it now, and that needs a human look. A producing directory
  that goes silent is red too. This is the precise, data-driven fix for "a
  directory contributing zero results without being excluded" (`#291`), and
  it replaces the old `resource_count == 0` / "matched nothing" guards
  (T3 deleted `scripts/assert_checkov_known_findings.py`'s copy; T4b deleted
  `security-scan`'s own inline copy in `pull-request.yml`, folding that
  job's Checkov invocation into this same evaluator -- see that file's own
  comment for the history).
* **Coverage by file, for the suffixes Checkov's directory walk cannot read
  at all (T4b).** Coverage-by-directory alone cannot see an opaque file
  (`OPAQUE_TERRAFORM_SUFFIXES`) sitting beside a readable `.tf` file, since
  the directory keeps producing results from its neighbour. Every tracked
  file matching one of those suffixes is red unless its OWN directory is a
  reviewed `known_invisible` entry -- and unlike every other row in this
  ledger, **no row shape accepts one in place**: the only path to green is
  moving, renaming, or deleting the file (see "WHAT THIS DOES NOT DO" for
  why the directory-level `known_invisible` route cannot work here, and for
  the gap this rule narrows without closing).
* **The Checkov version**: `checkov-ledger.json`'s `checkov_version` is the
  single source of truth. `run` passes `checkov --version`'s own output to
  `evaluate`, never a field from the scan's JSON body -- that field is not
  present in every shape Checkov emits.
* **`review_by`**: every group (failure, skip, or known-invisible) carries
  one. A group whose date has passed is red ("stale acceptance: re-review or
  fix"), whether or not anything else drifted.

Both of Checkov 3.3.8's JSON shapes are handled: the normal
`{summary: {...}, results: {...}}` shape, and the ALL-FLAT shape it emits
when nothing is evaluated at all (`resource_count: 0` -- e.g.
`terraform-cloudflare-dns`, whose one resource type Checkov has no policies
for; that repo's ledger lists its root directory as `known_invisible`
instead of relying on this evaluator inferring a matched-nothing scan).

Subcommands
-----------
`validate` -- schema, uniqueness (a `check_id` may repeat across a failure
    group and a skip group -- Phase 5's own shape needs exactly that, see
    "WHAT THIS DOES NOT DO" below -- but not twice within the same kind),
    tracking-URL shape, `reason` presence (failure and known-invisible
    groups; skip groups deliberately do not duplicate a reason -- see
    above), `review_by` parseability, and that every resource-scoped row's
    `resource` -- Checkov's address verbatim -- is cross-checked against the
    repo by its SHAPE (decision 13, Sprint 15 Amendment 2; no `kind`
    discriminator, the shape is derivable from the address itself):
    `<type>.<name>` needs a `resource`/`data` `"<type>" "<name>"` block in
    `file_path` (the old script's test 11, generalized to `data` blocks --
    most of this repo's pinned rows are `aws_iam_policy_document` *data*
    sources, not `resource` blocks); `module.<block>.<type>.<name>` needs
    that same block in `file_path` (the module's own file) AND a
    `module "<block>"` block in at least one tracked Terraform file (the
    block name is part of the address -- a renamed instance is a stale
    row); a dot-less `<name>` needs a `module "<name>"` block in
    `file_path`; `<provider>.<alias>` is tried only after the
    `<type>.<name>` match fails (Checkov's address space collides with it
    at the string level, so this is a fallback, not a discriminator --
    `evaluate` still requires the exact triple to be observed live, so a
    mis-shaped row can only ever produce red) and needs a
    `provider "<provider>"` block in `file_path`, plus `alias = "<alias>"`
    in that file unless `<alias>` is `default`. Nested module addresses
    (`module.a.module.b.<type>.<name>`) are rejected explicitly, naming the
    shape, with no attempt to match -- no consumer in this sprint has one;
    widen when one appears, not before. Every rejection names the shape it
    tried. `validate` also checks that every `known_invisible` directory
    exists and holds a tracked Terraform file (the same widened tracked set
    `run`'s coverage check uses -- decision 15). Always run first by `run`;
    also runnable on its own so a coder can check a ledger edit before
    pushing (no live Checkov, no AWS -- but it does need `git`, to enumerate
    the tracked Terraform files the module-block and known-invisible checks
    search).
`evaluate` -- the pure comparison, reachable directly on Checkov JSON (via
    stdin) plus `--checkov-version` and one or more `--tracked-dir` flags, so
    every case above can be exercised against a constructed fixture with no
    live scan, `.git`, or ledger file changes.
`run` -- the real thing, in this order: refuses if `.checkov.yaml`/
    `.checkov.yml` exists at the scan directory, the current working
    directory, or `$HOME` -- everywhere Checkov itself auto-discovers one,
    with no CLI flag able to override that discovery (IAC-BL-69's own prior
    finding, generalized so every consumer of this action gets the guard,
    not just this repo's `gates.green`); refuses if `--directory` is not the
    repository root (see "WHAT THIS DOES NOT DO"); lists `git`-tracked
    Terraform files (fails loudly, no filesystem-walk fallback and no silent
    "zero tracked files", if `git` cannot answer -- a shallow clone is fine,
    a missing `.git` is not, see U2) -- needed before the ledger can be
    validated, since decision 13's module-block cross-check and decision
    15's `known_invisible` check both search that same tracked set; validates
    the ledger; reads `checkov --version`; runs
    `checkov -d <dir> --framework <fw> --output json` with every `CKV_*`/
    `BC_*`/`PRISMA_*` environment variable stripped (Checkov honors several
    as silent, un-audited equivalents of `--skip-check` -- see "WHAT THIS
    DOES NOT DO"), treating any exit code outside `{0, 1}` as a crash
    reported before any JSON is read; then evaluates. Writes an
    `$GITHUB_STEP_SUMMARY` table of every accepted group plus a `::notice::`
    count on every run, so a green result still *shows* the debt instead of
    going quiet about it.

WHAT THIS DOES NOT DO
----------------------
* **Module-instance collapsing (what remains after T4a; `#491`, review by
  2026-12-14).** Checkov keys a module's expanded resources by (module
  source file, module BLOCK NAME) -- not by caller. Two directories that
  both write `module "tenant_iam_role" { source = ... }` (this repo's own
  `tenants/*/{aws,github}/main.tf` do exactly this) collapse in a
  whole-repo scan. T4a (decision 14) makes the SYMPTOM harmless: when both
  callers' copies emit the identical `(check_id, resource, file_path[,
  suppress_comment])`, the duplicate now collapses to one key, counts once
  against the summary reconciliation, and is reported ("N module-instance
  duplicate(s) collapsed") instead of misreading as an unreadable scan. It
  does NOT close the underlying gap: one tenant's copy of the module can
  still contribute ZERO passed/failed/skipped results to the combined scan
  at all (an absence, not a duplicate) -- live-verified against this repo,
  `bootstrap/modules/tenant-iam-role`'s 38 evaluated results in a
  whole-repo scan all key to `caller_file_path:
  /tenants/jrg-consulting/aws/main.tf`, `603identity`'s identically-shaped
  instantiation contributes none -- and because the module's own directory
  still shows up in `producing_dirs` (from the caller that DID survive),
  decision 5's coverage-by-directory rule cannot see that gap; the silent
  caller looks covered by proxy. Decision 14 deliberately does not key rows
  or coverage on `caller_file_path` to close this (it is absent on 3 of 41
  of this module's own results -- the graph-check ones -- so it cannot key a
  failure, and which caller survives the collapse is a scanner artifact, not
  a property of the code). Closing it needs a different schema decision,
  evaluated after Phase 2, not inside this sprint.
* **Duplicate multiplicity is reported, not pinned (/critic-gate finding,
  T4a security-critic pass; the rationale below was itself wrong on its
  first two drafts -- round 3 finally traced it to the raw scan JSON rather
  than to a plausible-sounding neighboring fact).** Decision 14 collapses an
  exact duplicate to one key and prints how many collapsed -- it does not
  compare that count against anything in the ledger, and the collapse
  triggers on ANY duplicate entry Checkov's live output emits, independent
  of what address shape any ledger row uses (`_triples`/`_skip_map` key off
  Checkov's own `(check_id, resource, file_path)`, not off the ledger, and
  only dedupe FAILED and SKIPPED entries -- passed entries are never
  deduped, since they only ever feed `producing_dirs`). Today's live scan of
  this repo emits no such duplicate at all -- verified, `run`'s output
  carries no "duplicate collapsed" line -- for a narrower reason than it may
  look: `tenant_iam_role`'s 41 live results are ALL passing checks today (0
  failed, 0 skipped, live-verified against the raw scan JSON); the module's
  own caller-collapse (the bullet above) governs which caller a PASS is
  attributed to, but has no bearing on whether a duplicate FAILURE or SKIP
  could occur -- that is gated purely on whether any check on the shared
  module is currently failing or skipped, which is independent of `#491`
  and does not require a second caller to ever start contributing results.
  The gap goes live the moment ANY finding or skip lands on ANY resource in
  ANY shared module -- T4a's own live falsification (mutation (iii) on PR
  #493, "1 module-instance duplicate collapsed") is exactly that: a single
  planted skip on the ALREADY-single-caller-collapsed module, needing no
  change in caller count. Once any duplicate is live, a newly-appearing
  THIRD instance (or a second caller's inputs happening to produce the
  identical `(check_id, resource, file_path)` as an already-accepted row)
  collapses and stays green with no signal that the instance count
  changed -- the same class of gap `#291`'s incident 2 records ("the red
  job could not see its own finding double"). Decision 14 as ratified does
  not ask for multiplicity to be pinned; closing this needs a schema call
  (e.g. an optional per-row `instances: N`, or treating a 0-to-nonzero
  duplicate-count transition as drift without a schema change) an
  architect/owner should make deliberately, not one this evaluator should
  invent unilaterally -- flagged here for that decision, not resolved.
* **`validate`'s block/provider/alias matching is a whole-file substring
  search, not parse-tree-aware (pre-existing pattern, `_block_pattern`;
  extended by decision 13 to `_module_block_pattern`/
  `_provider_block_pattern`/`_alias_pattern`).** `.search()` over raw file
  text matches inside a comment, a string, or a different block than the one
  it looks like it names -- e.g. `_alias_pattern` is not scoped to the
  specific `provider` block `_provider_block_pattern` matched, so an
  `aws.<alias>` row validates if ANY block in that file carries
  `alias = "<alias>"`, even a different provider's. Bounded: `validate` is
  documented as a stale-row *hint*, and `evaluate` still requires the exact
  triple to be observed live, so this can only produce a wrong (or wrongly
  reassuring) message, never a false green on a real drift -- same class
  `.ai/project.yml` calls "the guard matches bytes, the runtime obeys the
  parse tree" for the unrelated `hcl2`-based guards. A parse-tree rewrite is
  future work, not a T4a fix.
* **Representability beyond decision 13's four shapes.** `validate` (T4a,
  decision 13) now cross-checks `<type>.<name>`, `module.<block>.<type>.
  <name>`, dot-less `<name>` (a module block), and `<provider>.<alias>` --
  together the shapes behind roughly 55 of this repo's 331 live results as
  of 2026-09-14 (Sprint 15 Amendment 2's census). What remains unrepresented,
  each rejected explicitly by name rather than guessed at: **nested module
  addresses** (`module.a.module.b.<type>.<name>`) and **indexed
  `count`/`for_each` addresses** (`<type>.<name>[0]`,
  `<type>.<name>["key"]`, `module.<block>[0].<type>.<name>` -- Checkov 3.3.8
  emits all three, verified on a fixture by the fresh-session review of PR
  `#493`; this repo has no `count`/`for_each` in any tracked file, but both
  Phase 2 consumer repos use `for_each`, so the shape WILL reach a
  consumer's ledger). No consumer in this sprint has either; widen
  `validate` when one appears, not before. A finding on either shape goes
  red with no ledger row able to accept it -- fail-closed (the right
  direction), same as every other unrepresentable shape before it, so the
  only path to green there is still a code change, not a reviewed ledger
  edit.
* **A resource silently dropping out of an already-producing directory --
  narrowed by T4b's file-level opaque check (see "WHAT IS PINNED" above),
  not closed.** Decision 5 pins directory coverage, not `resource_count`
  (the plan's own "Why `resource_count` is not pinned anywhere" explains
  the rejected alternative) -- so a directory that keeps producing SOME
  results after losing one passing resource is invisible to this
  evaluator. The resource-scoped failure/skip pins catch this for anything
  that was already failing or skipped; a resource that goes from passing
  to simply ABSENT (e.g. deleted alongside its `.tf` file, in a directory
  with other resources left) is still not caught -- that remains open. T4b
  DOES now surface the adjacent case a `.tf.json`/`.tofu`/`.tofu.json`/
  `.tftest.json`/`.tofutest.json` file caused (fresh-session review of PR
  #493, "opacity is per file, not per directory"): every tracked file
  matching `OPAQUE_TERRAFORM_SUFFIXES` is red unless its own directory is a
  reviewed `known_invisible` entry, so an opaque file beside readable `.tf`
  files can no longer look covered by its neighbours. **This is fail-closed
  with no accepting mechanism, same doctrine as the unrepresentable address
  shapes above, not a new kind of ledger row** (/critic-gate finding, T4b
  architect pass, round 1): a directory already producing SOME results can
  never become a `known_invisible` entry (the `now_visible` check above
  forbids it), so the only way to green this is a code change -- move the
  file to its own directory, rename it to a readable suffix, or delete it.
  An earlier draft of `evaluate`'s error message suggested "add a
  known_invisible entry" as a remedy for exactly the case where that can
  never work; fixed to say so plainly instead.
* **Tracking-URL liveness.** `validate`/`evaluate` check only that a
  `tracking` URL has the right shape (an issue URL in this org) -- never
  whether the issue is still open. That is `gates.green`'s job, using the
  developer's own `gh` auth (A5), because the PR workflow this action runs
  in holds `contents: read` only.
* **Scanning a subdirectory.** `run` refuses unless `--directory` resolves
  to the current working directory. Coverage (`git ls-files`) and
  `validate`'s file lookups are both repo-root-relative; Checkov's own
  `file_path`s are relative to `--directory`. Those only agree when the two
  are the same path, which is what every consumer this plan names
  (`infrastructure-core`, `terraform-cloudflare-dns`,
  `terraform-microsoft365-entra`) already does by scanning from their root.

Usage:
  checkov -d . --framework terraform --output json | \\
      python3 checkov_ledger.py evaluate --checkov-version 3.3.8 \\
          --tracked-dir bootstrap --tracked-dir tenants/603identity/github ...
  python3 checkov_ledger.py validate --ledger checkov-ledger.json
  python3 checkov_ledger.py run --ledger checkov-ledger.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ORG = "603-Identity"
TRACKING_URL_RE = re.compile(
    r"^https://github\.com/" + re.escape(ORG) + r"/[A-Za-z0-9_.-]+/issues/\d+$"
)

_BLOCK_PATTERN_CACHE: dict[tuple[str, str], re.Pattern] = {}
_MODULE_BLOCK_PATTERN_CACHE: dict[str, re.Pattern] = {}
_PROVIDER_BLOCK_PATTERN_CACHE: dict[str, re.Pattern] = {}
_ALIAS_PATTERN_CACHE: dict[str, re.Pattern] = {}


def _block_pattern(resource_type: str, resource_name: str) -> re.Pattern:
    key = (resource_type, resource_name)
    pattern = _BLOCK_PATTERN_CACHE.get(key)
    if pattern is None:
        pattern = re.compile(
            r'(?:resource|data)\s+"' + re.escape(resource_type) + r'"\s+"' + re.escape(resource_name) + r'"\s*\{'
        )
        _BLOCK_PATTERN_CACHE[key] = pattern
    return pattern


def _module_block_pattern(block_name: str) -> re.Pattern:
    pattern = _MODULE_BLOCK_PATTERN_CACHE.get(block_name)
    if pattern is None:
        pattern = re.compile(r'module\s+"' + re.escape(block_name) + r'"\s*\{')
        _MODULE_BLOCK_PATTERN_CACHE[block_name] = pattern
    return pattern


def _provider_block_pattern(provider: str) -> re.Pattern:
    pattern = _PROVIDER_BLOCK_PATTERN_CACHE.get(provider)
    if pattern is None:
        pattern = re.compile(r'provider\s+"' + re.escape(provider) + r'"\s*\{')
        _PROVIDER_BLOCK_PATTERN_CACHE[provider] = pattern
    return pattern


def _alias_pattern(alias: str) -> re.Pattern:
    pattern = _ALIAS_PATTERN_CACHE.get(alias)
    if pattern is None:
        pattern = re.compile(r'alias\s*=\s*"' + re.escape(alias) + r'"')
        _ALIAS_PATTERN_CACHE[alias] = pattern
    return pattern


def _dirname(file_path: str) -> str:
    return str(Path(file_path.lstrip("/")).parent)


# ---------------------------------------------------------------------------
# Address-shape dispatch (decision 13, Sprint 15 Amendment 2)
# ---------------------------------------------------------------------------


def classify_resource_address(
    resource: str,
    file_path: str,
    repo_root: Path,
    tracked_files: list[str],
    file_text_cache: dict[str, tuple[str, None] | tuple[None, str]],
) -> str | None:
    """Cross-checks `resource` (Checkov's address verbatim) against the repo
    by its shape. Returns `None` if it is representable and matches, else an
    error message NAMING the shape that was tried -- decision 13 requires
    every rejection to do so. Never raises: a missing/unreadable file is a
    validation failure, reported the same way as any other stale row."""

    def read(rel_path: str) -> tuple[str | None, str | None]:
        """(text, error) for a repo-relative file, cached across calls in
        one `validate_ledger` run so the module-block search (which may
        scan every tracked file for many rows) reads each file once."""
        cached = file_text_cache.get(rel_path)
        if cached is not None:
            return cached
        resolved_root = repo_root.resolve()
        target = (repo_root / rel_path.lstrip("/")).resolve()
        if not target.is_relative_to(resolved_root):
            result: tuple[str | None, str | None] = (None, "resolves outside this repo")
        elif not target.is_file():
            result = (None, "does not exist in this repo")
        else:
            try:
                result = (target.read_text(encoding="utf-8"), None)
            except (UnicodeDecodeError, OSError) as exc:
                # A tracked file that is not valid UTF-8 (or otherwise
                # unreadable) used to raise here uncaught -- every tracked
                # file reaches this path when a module-expanded row's block
                # search scans the whole tracked set (/critic-gate finding).
                # Fail closed the same way every other malformed-input path
                # in this function does: an `::error::`-shaped rejection,
                # never a bare traceback.
                result = (None, f"could not be read as UTF-8 text ({exc})")
        file_text_cache[rel_path] = result
        return result

    if "[" in resource or "]" in resource:
        # Checkov 3.3.8 renders `count`/`for_each` instances as
        # `<type>.<name>[0]`, `<type>.<name>["key"]` and
        # `module.<block>[0].<type>.<name>` (fresh-session review, PR #493).
        # Without this branch each fell through to a message blaming a typo
        # or a rename; checked first because a `for_each` key may itself
        # contain dots, which would otherwise mis-count the segments below.
        return (
            f"resource {resource!r} is an indexed (`count`/`for_each`) address "
            f"(<type>.<name>[<index>] or module.<block>[<index>]...) -- not "
            f"representable, rejected explicitly. Widen `validate` when a "
            f"consumer needs one, not before."
        )

    segments = resource.split(".")

    if segments[0] == "module":
        if len(segments) >= 3 and segments[2] == "module":
            return (
                f"resource {resource!r} is a nested module address "
                f"(module.<a>.module.<b>...) -- not representable, rejected "
                f"explicitly. Widen `validate` when a consumer needs one, "
                f"not before."
            )
        if len(segments) != 4:
            return (
                f"resource {resource!r} looks like a module-expanded address "
                f"(module.<block>.<type>.<name>) but has {len(segments)} "
                f"dot-separated segment(s), not 4."
            )
        _, block_name, resource_type, resource_name = segments
        text, err = read(file_path)
        if err:
            return f"resource {resource!r} (module-expanded) names {file_path!r}, which {err}."
        if not _block_pattern(resource_type, resource_name).search(text or ""):
            return (
                f"{file_path} has no `resource`/`data` \"{resource_type}\" "
                f"\"{resource_name}\" block -- module-expanded address "
                f"{resource!r} may be stale (typo, or the resource moved)."
            )
        found_block = False
        for tf_file in tracked_files:
            tf_text, tf_err = read(tf_file)
            if tf_err:
                continue
            if _module_block_pattern(block_name).search(tf_text or ""):
                found_block = True
                break
        if not found_block:
            return (
                f"no tracked Terraform file has a `module \"{block_name}\"` "
                f"block -- module-expanded address {resource!r} names a "
                f"block that does not exist (typo, or the instance was "
                f"renamed)."
            )
        return None

    if "." not in resource:
        text, err = read(file_path)
        if err:
            return f"resource {resource!r} (dot-less module block) names {file_path!r}, which {err}."
        if not _module_block_pattern(resource).search(text or ""):
            return (
                f"{file_path} has no `module \"{resource}\"` block -- "
                f"dot-less address {resource!r} may be stale (typo, or the "
                f"module block was renamed)."
            )
        return None

    if resource.count(".") != 1:
        return (
            f"resource {resource!r} is not a recognized address shape "
            f"(tried <type>.<name>, module.<block>.<type>.<name>, a "
            f"dot-less module name, and <provider>.<alias> -- none fit a "
            f"resource with {resource.count('.')} dots that does not start "
            f"with 'module.')."
        )

    first, _, second = resource.partition(".")
    text, err = read(file_path)
    if err:
        return f"resource {resource!r} names {file_path!r}, which {err}."
    text = text or ""

    # Fallback order (decision 13): <type>.<name> tried first, <provider>.
    # <alias> only after it fails -- Checkov's address space collides with
    # this shape at the string level, so this is a heuristic, not a
    # discriminator. `evaluate` still requires the exact triple to be
    # observed live, so a wrong guess here can only ever produce red.
    if _block_pattern(first, second).search(text):
        return None

    if _provider_block_pattern(first).search(text):
        if second == "default" or _alias_pattern(second).search(text):
            return None
        return (
            f"{file_path} has a `provider \"{first}\"` block but no "
            f"`alias = \"{second}\"` -- provider address {resource!r} may "
            f"be stale."
        )

    return (
        f"{file_path} has neither a `resource`/`data` \"{first}\" "
        f"\"{second}\" block (tried as <type>.<name>) nor a "
        f"`provider \"{first}\"` block (tried as <provider>.<alias>) -- "
        f"address {resource!r} may be stale (typo, or the resource moved)."
    )


# ---------------------------------------------------------------------------
# Ledger validation
# ---------------------------------------------------------------------------


def validate_ledger(ledger: object, repo_root: Path, tracked_files: list[str]) -> tuple[bool, list[str]]:
    """Schema, uniqueness, tracking/review_by shape, and cross-checks against
    the repo's own tracked files -- never inferred, never trusted on faith.
    `tracked_files` is the widened set (decision 15, U3; see
    `TRACKED_TERRAFORM_PATTERNS`): every extension OpenTofu treats as real
    config or real test config, whether or not Checkov's own directory scan
    can see it -- the ones it cannot see are tracked anyway, precisely so a
    directory holding only one of them shows up as an unreviewed coverage
    gap rather than vanishing."""
    errors: list[str] = []
    file_text_cache: dict[str, tuple[str, None] | tuple[None, str]] = {}

    if not isinstance(ledger, dict):
        return False, [f"::error::the ledger is a {type(ledger).__name__}, not a JSON object."]

    checkov_version = ledger.get("checkov_version")
    if not isinstance(checkov_version, str) or not checkov_version:
        errors.append("::error::ledger.checkov_version must be a non-empty string.")

    failures = ledger.get("failures")
    skips = ledger.get("skips")
    known_invisible = ledger.get("known_invisible")
    if not isinstance(failures, list):
        errors.append("::error::ledger.failures must be a list.")
        failures = []
    if not isinstance(skips, list):
        errors.append("::error::ledger.skips must be a list.")
        skips = []
    if not isinstance(known_invisible, list):
        errors.append("::error::ledger.known_invisible must be a list.")
        known_invisible = []

    # Scoped PER KIND, not shared: a check_id may legitimately appear as
    # both a failure group and a skip group (Phase 5's own shape --
    # `sprints/15-checkov-ledger/sprint_plan.md`'s account of dissolving
    # `IAC-D29`'s F1 has one bucket's CKV_AWS_18 stay a ledger row while a
    # sibling bucket's access-log skip cites the same check_id). Only a
    # duplicate WITHIN one kind is a schema error.
    seen_check_ids: dict[str, set[str]] = {"failure": set(), "skip": set()}
    # Shared ACROSS kinds, unlike seen_check_ids above: Checkov can never
    # report one check as both failed and skipped for the same resource, so
    # an exact (check_id, resource, file_path) triple may appear at most
    # once in the whole ledger, even though its check_id may legitimately
    # head both a failure group and a skip group (/critic-gate round 2,
    # architect: the per-kind check_id scoping alone let a ledger pin a
    # triple as both, which is unsatisfiable by any real scan and would
    # stay red forever).
    seen_triples: set[tuple[str, str, str]] = set()

    def _validate_group(group: object, kind: str, require_reason: bool) -> None:
        if not isinstance(group, dict):
            errors.append(f"::error::a {kind} group is not a JSON object: {group!r}")
            return

        check_id = group.get("check_id")
        if not isinstance(check_id, str) or not check_id:
            errors.append(f"::error::a {kind} group is missing `check_id`.")
            check_id = f"<{kind} group without check_id>"
        elif check_id in seen_check_ids[kind]:
            errors.append(f"::error::check_id {check_id!r} appears in more than one {kind} group.")
        seen_check_ids[kind].add(check_id)

        tracking = group.get("tracking")
        if not isinstance(tracking, str) or not TRACKING_URL_RE.match(tracking):
            errors.append(
                f"::error::{kind} group {check_id!r} has a missing or malformed `tracking` "
                f"URL -- must be https://github.com/{ORG}/<repo>/issues/<n>, got {tracking!r}."
            )

        review_by = group.get("review_by")
        if not isinstance(review_by, str):
            errors.append(f"::error::{kind} group {check_id!r} is missing `review_by`.")
        else:
            try:
                date.fromisoformat(review_by)
            except ValueError:
                errors.append(f"::error::{kind} group {check_id!r} has an unparseable `review_by`: {review_by!r}")

        if require_reason:
            reason = group.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                errors.append(f"::error::{kind} group {check_id!r} is missing `reason`.")

        resources = group.get("resources")
        if not isinstance(resources, list) or not resources:
            errors.append(f"::error::{kind} group {check_id!r} has no `resources`.")
            return

        seen_keys: set[tuple[str, str]] = set()
        for row in resources:
            if not isinstance(row, dict):
                errors.append(f"::error::{kind} group {check_id!r} has a non-object resource row: {row!r}")
                continue
            resource = row.get("resource")
            file_path = row.get("file_path")
            if not isinstance(resource, str) or not resource or not isinstance(file_path, str) or not file_path:
                errors.append(
                    f"::error::{kind} group {check_id!r} has a resource row missing "
                    f"`resource`/`file_path`: {row!r}"
                )
                continue

            key = (resource, file_path)
            if key in seen_keys:
                errors.append(f"::error::{kind} group {check_id!r} lists {key} more than once.")
            seen_keys.add(key)

            triple = (check_id, resource, file_path)
            if triple in seen_triples:
                errors.append(
                    f"::error::{triple} is listed in more than one group. Checkov can "
                    f"never report the same check as both failed and skipped for the "
                    f"same resource -- a ledger pinning it as both can never match a "
                    f"real scan."
                )
            seen_triples.add(triple)

            if kind == "skip":
                comment = row.get("suppress_comment")
                if not isinstance(comment, str) or not comment.strip():
                    errors.append(
                        f"::error::skip group {check_id!r} resource {resource!r} is missing "
                        f"`suppress_comment`."
                    )

            shape_error = classify_resource_address(resource, file_path, repo_root, tracked_files, file_text_cache)
            if shape_error:
                errors.append(f"::error::{kind} group {check_id!r} {shape_error}")

    for group in failures:
        _validate_group(group, "failure", require_reason=True)
    for group in skips:
        _validate_group(group, "skip", require_reason=False)

    seen_dirs: set[str] = set()
    for entry in known_invisible:
        if not isinstance(entry, dict):
            errors.append(f"::error::a known_invisible entry is not a JSON object: {entry!r}")
            continue
        directory = entry.get("directory")
        if not isinstance(directory, str) or not directory:
            errors.append("::error::a known_invisible entry is missing `directory`.")
            continue
        if directory in seen_dirs:
            errors.append(f"::error::known_invisible directory {directory!r} is listed more than once.")
        seen_dirs.add(directory)
        normalized = os.path.normpath(directory)
        # A leading `..` (`..`, `../dirC`) is already normpath-stable --
        # nothing before it for `normpath` to cancel against -- so
        # `directory != normalized` alone does not catch it (/critic-gate
        # finding, T4b architect pass, round 1, live-verified). Checked
        # explicitly rather than folded into the inequality.
        climbs_out = normalized == ".." or normalized.startswith("../")
        if directory != normalized or directory.startswith("/") or climbs_out:
            # `evaluate` compares this string byte-for-byte against the
            # directory names derived from `git ls-files` (no leading slash,
            # no `.`/`..` segments, no trailing slash), so any other spelling
            # can never match. The first fix here (fresh-session review, PR
            # #493) caught only a leading/trailing slash; `./dirC` and
            # `dirC/../dirC` still fell through to "holds no tracked
            # Terraform file" below -- the wrong diagnosis, blaming the
            # directory's contents rather than its spelling (`#494`, second
            # fresh-session review of #493). `os.path.normpath` keeps `.` as
            # `.`, so the flat-shape root entry (test 13,
            # `terraform-cloudflare-dns`'s own shape) still passes.
            errors.append(
                f"::error::known_invisible directory {directory!r} must be written exactly as "
                f"`git ls-files` names it -- no leading `/`, no trailing `/`, no `.`/`..` "
                f"segments (e.g. 'tenants/x/y', not './tenants/x/y' or 'tenants/x/y/') -- "
                f"`evaluate` matches it byte-for-byte."
            )
            continue

        tracking = entry.get("tracking")
        if not isinstance(tracking, str) or not TRACKING_URL_RE.match(tracking):
            errors.append(
                f"::error::known_invisible {directory!r} has a missing or malformed "
                f"`tracking` URL: {tracking!r}."
            )
        review_by = entry.get("review_by")
        if not isinstance(review_by, str):
            errors.append(f"::error::known_invisible {directory!r} is missing `review_by`.")
        else:
            try:
                date.fromisoformat(review_by)
            except ValueError:
                errors.append(
                    f"::error::known_invisible {directory!r} has an unparseable `review_by`: {review_by!r}"
                )
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"::error::known_invisible {directory!r} is missing `reason`.")

        target_dir = (repo_root / directory.lstrip("/")).resolve()
        if not target_dir.is_relative_to(repo_root.resolve()):
            errors.append(f"::error::known_invisible directory {directory!r} resolves outside this repo.")
        elif not target_dir.is_dir():
            errors.append(f"::error::known_invisible directory {directory!r} does not exist in this repo.")
        elif not any(_dirname(f) == directory.lstrip("/") for f in tracked_files):
            errors.append(
                f"::error::known_invisible directory {directory!r} holds no tracked Terraform "
                f"file (see `TRACKED_TERRAFORM_PATTERNS`)."
            )

    if errors:
        return False, errors
    return True, [
        f"OK: ledger validated -- {len(failures)} failure group(s), {len(skips)} skip "
        f"group(s), {len(known_invisible)} known-invisible director"
        f"{'y' if len(known_invisible) == 1 else 'ies'}."
    ]


# ---------------------------------------------------------------------------
# Checkov output parsing helpers
# ---------------------------------------------------------------------------


def _list(container: object, key: str) -> list:
    if not isinstance(container, dict):
        return []
    value = container.get(key)
    return value if isinstance(value, list) else []


def _triples(entries: list) -> tuple[frozenset, int, int]:
    """`(check_id, resource, file_path)` keys parseable from `entries`, how
    many entries this could actually key (so a malformed entry is
    reconciled against the declared count, never silently read as 'that
    finding is gone'), and how many of those were EXACT duplicates of an
    already-seen key. A duplicate triple collapses to one key rather than
    reading as an unreadable scan (decision 14, Sprint 15 Amendment 2) --
    Checkov's module-instance collapse can genuinely emit the identical
    entry more than once for the same underlying finding."""
    keys: set[tuple[str, str, str]] = set()
    counted = 0
    duplicates = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        check_id = entry.get("check_id")
        resource = entry.get("resource")
        file_path = entry.get("file_path")
        if isinstance(check_id, str) and isinstance(resource, str) and isinstance(file_path, str):
            counted += 1
            key = (check_id, resource, file_path)
            if key in keys:
                duplicates += 1
            keys.add(key)
    return frozenset(keys), counted, duplicates


def _skip_map(entries: list) -> tuple[dict[tuple[str, str, str], str], int, int, list[tuple[str, str, str]]]:
    """As `_triples`, but for skips: the mapping value is the
    `suppress_comment`. An exact duplicate (identical triple AND comment)
    collapses like a failure duplicate does. A duplicate triple whose
    comment DIFFERS is not a module-instance collapse -- it is two
    different justifications pinned to what the ledger treats as one skip
    -- and stays an error (decision 14), reported via `conflicts`."""
    mapping: dict[tuple[str, str, str], str] = {}
    counted = 0
    duplicates = 0
    conflicts: list[tuple[str, str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        check_id = entry.get("check_id")
        resource = entry.get("resource")
        file_path = entry.get("file_path")
        check_result = entry.get("check_result")
        comment = check_result.get("suppress_comment") if isinstance(check_result, dict) else None
        if comment is None:
            comment = entry.get("suppress_comment")
        if (
            isinstance(check_id, str)
            and isinstance(resource, str)
            and isinstance(file_path, str)
            and isinstance(comment, str)
        ):
            counted += 1
            key = (check_id, resource, file_path)
            if key in mapping:
                if mapping[key] == comment:
                    duplicates += 1
                elif key not in conflicts:
                    conflicts.append(key)
            else:
                mapping[key] = comment
    return mapping, counted, duplicates, conflicts


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


def evaluate(
    checkov_data: object,
    ledger: dict,
    tracked_dirs: set,
    tracked_files: tuple[str, ...],
    checkov_version: str,
    today: date,
) -> tuple[int, list[str]]:
    """The pure decision: (exit_code, lines to report). Every branch --
    including exit 0, only reachable on an exact match -- is exercised
    directly here in `test_checkov_ledger.py`, never only through `run`'s
    subprocess/`.git`/live-Checkov contract."""
    lines: list[str] = []

    if not isinstance(checkov_data, dict):
        lines.append(f"::error::Checkov output is a {type(checkov_data).__name__}, not a JSON object.")
        return 1, lines

    ledger_version = ledger.get("checkov_version")
    if checkov_version != ledger_version:
        lines.append(
            f"::error::Installed Checkov is {checkov_version!r} but the ledger's "
            f"checkov_version is {ledger_version!r}. The ledger is the single source of "
            f"the Checkov version -- bump it in a reviewed PR, or fix the install step."
        )
        return 1, lines

    # `tracked_files` (T4b's opaque-file check, below) is always a superset
    # `tracked_dirs` is derived FROM in `cmd_run` (`_dirs_from_files`) -- if
    # any directory is tracked, at least one file produced it. `cmd_run`
    # itself can never violate this (`git_tracked_terraform_files` raises
    # rather than returning empty), but the bare `evaluate` CLI subcommand
    # takes `--tracked-dir`/`--tracked-file` as two INDEPENDENT flags with
    # independent defaults, and omitting `--tracked-file` defaults it to
    # `[]` (/critic-gate finding, T4b security-critic pass, round 1): a
    # caller who wired `--tracked-dir` (for coverage) but forgot
    # `--tracked-file` (for the opaque-file check) would get a silent GREEN
    # from the opaque check -- an empty list vacuously satisfies "no exposed
    # opaque file" -- exactly the wrong-direction failure mode
    # `git_tracked_terraform_files`'s own docstring already names ("a
    # coverage rule with zero tracked files is satisfied by anything").
    if tracked_dirs and not tracked_files:
        lines.append(
            "::error::tracked_dirs is non-empty but tracked_files is empty -- "
            "tracked_files must be a superset of the files that produced "
            "tracked_dirs. Treating this as an unreadable input, not as 'no opaque "
            "files to check' (an omitted --tracked-file would otherwise silently "
            "disable the opaque-file coverage check)."
        )
        return 1, lines
    # The mirror direction (/critic-gate finding, T4b architect pass, round
    # 2): `--tracked-file` supplied with `--tracked-dir` omitted would leave
    # `tracked_dirs` empty, which vacuously satisfies the WHOLE
    # coverage-by-directory check (`silent`/`now_visible` below iterate an
    # empty set) -- the identical "a coverage rule with zero X is satisfied
    # by anything" shape the check above exists to close, one flag over.
    # Same unreachable-from-`cmd_run` scope as that check (`tracked_dirs` is
    # always derived FROM `tracked_files` there).
    if tracked_files and not tracked_dirs:
        lines.append(
            "::error::tracked_files is non-empty but tracked_dirs is empty -- "
            "tracked_dirs must be the set of directories tracked_files produced. "
            "Treating this as an unreadable input, not as 'no directories to cover' "
            "(an omitted --tracked-dir would otherwise silently disable the "
            "directory-coverage check)."
        )
        return 1, lines
    # The third corner of the same class (/critic-gate finding, T4b
    # security-critic pass, round 3): the two checks above each require ONE
    # of the pair to be non-empty before complaining about the other, so
    # omitting BOTH `--tracked-dir` and `--tracked-file` satisfies neither
    # and falls through -- every directory- and file-level coverage check
    # below then iterates empty containers and is vacuously green. No real
    # repo has zero tracked directories (decision 5's whole premise), so
    # this is never legitimate.
    if not tracked_dirs and not tracked_files:
        lines.append(
            "::error::tracked_dirs and tracked_files are both empty -- neither "
            "--tracked-dir nor --tracked-file was supplied. Treating this as an "
            "unreadable input, not as 'nothing to cover' (every directory- and "
            "file-level coverage check would otherwise be vacuously satisfied)."
        )
        return 1, lines

    summary = checkov_data.get("summary")
    summary = summary if isinstance(summary, dict) else checkov_data
    passed = summary.get("passed")
    failed = summary.get("failed")
    parsing_errors = summary.get("parsing_errors")
    # `skipped` used to be read separately, further down, and only checked
    # `isinstance(declared_skipped, int)` -- an ABSENT key silently skipped
    # the whole skip-reconciliation block rather than failing the scan
    # (/critic-gate finding, PR #490 architect review 6a). Real Checkov
    # 3.3.8 always emits it; required here, alongside the other three, for
    # the same reason those are: an unreadable summary is an unreadable
    # scan, never a clean one.
    skipped = summary.get("skipped")
    if (
        not isinstance(passed, int)
        or not isinstance(failed, int)
        or not isinstance(parsing_errors, int)
        or not isinstance(skipped, int)
    ):
        lines.append(
            "::error::Could not read the Checkov summary (passed/failed/parsing_errors/"
            "skipped). Treating this as a failed scan, not a clean one."
        )
        return 1, lines

    if parsing_errors != 0:
        lines.append(
            f"::error::Checkov reported {parsing_errors} parsing error(s). A file it could "
            f"not parse is a file it could not scan -- 'nothing was scanned' must never read "
            f"as 'nothing is wrong'."
        )
        return 1, lines

    results = checkov_data.get("results")
    results = results if isinstance(results, dict) else checkov_data

    failed_entries = _list(results, "failed_checks")
    skipped_entries = _list(results, "skipped_checks")
    passed_entries = _list(results, "passed_checks")

    actual_failing, counted_failed, failing_duplicates = _triples(failed_entries)
    if counted_failed != failed:
        lines.append(
            f"::error::Checkov reported {failed} failure(s) but only {counted_failed} "
            f"failed_checks entries had usable check_id/resource/file_path fields. Treating "
            f"this as an unreadable scan, not a change to the failing set."
        )
        return 1, lines

    actual_skip_map, counted_skipped, skip_duplicates, skip_conflicts = _skip_map(skipped_entries)
    if counted_skipped != skipped:
        lines.append(
            f"::error::Checkov reported {skipped} skip(s) but only {counted_skipped} "
            f"skipped_checks entries had usable check_id/resource/file_path/suppress_comment "
            f"fields. Treating this as an unreadable scan, not a change to the skipped set."
        )
        return 1, lines

    errors: list[str] = []

    # --- Module-instance duplicate collapse (decision 14) -----------------
    # Reported unconditionally, on both the green and red paths, so a red
    # caused by something else still shows the collapse happened (T4a
    # acceptance case iii: a planted skip inside a shared module reports as
    # "1 module-instance duplicate collapsed", never as "unreadable scan").
    total_duplicates = failing_duplicates + skip_duplicates
    if total_duplicates:
        lines.append(f"{total_duplicates} module-instance duplicate{'s' if total_duplicates != 1 else ''} collapsed.")
    if skip_conflicts:
        errors.append(
            f"::error::duplicate skip entries for {sorted(skip_conflicts)} carry DIFFERENT "
            f"suppress_comment values. This is not a module-instance collapse (those agree by "
            f"definition) -- two different justifications are pinned to what the ledger treats "
            f"as one skip; resolve which is current."
        )

    # --- Coverage by directory (decision 5) -----------------------------
    producing_dirs = {
        _dirname(e["file_path"])
        for e in (*failed_entries, *skipped_entries, *passed_entries)
        if isinstance(e, dict) and isinstance(e.get("file_path"), str)
    }
    known_invisible_dirs = {
        e["directory"]
        for e in ledger.get("known_invisible", [])
        if isinstance(e, dict) and isinstance(e.get("directory"), str)
    }

    silent = sorted(d for d in tracked_dirs if d not in producing_dirs and d not in known_invisible_dirs)
    if silent:
        errors.append(
            f"::error::Coverage gap: {silent} hold tracked Terraform files but produced no "
            f"Checkov result and are not listed in known_invisible. Something stopped being "
            f"scanned, this directory's file type is structurally invisible to Checkov (see "
            f"`TRACKED_TERRAFORM_PATTERNS`), or this directory needs a reviewed "
            f"known_invisible entry."
        )

    now_visible = sorted(d for d in known_invisible_dirs if d in producing_dirs)
    if now_visible:
        errors.append(
            f"::error::known_invisible director{'y' if len(now_visible) == 1 else 'ies'} "
            f"now producing Checkov results: {now_visible}. Checkov can see this now; "
            f"review and remove the known_invisible entry."
        )

    # --- Opaque files outside a reviewed known_invisible directory --------
    # T4b (fresh-session review of PR #493, "opacity is per file, not per
    # directory"): a directory holding `main.tf` next to an `extra.tf.json`
    # keeps producing results from the readable neighbour, so it can never
    # be listed as `known_invisible` (the `now_visible` check above rejects
    # any producing directory there) and never shows up in `silent` either.
    # The opaque file itself is scanned by nothing and flagged by nothing --
    # exactly the "resource silently dropping out" gap the module docstring
    # names. This closes that one sub-case: every tracked file matching
    # `OPAQUE_TERRAFORM_SUFFIXES` is red unless its OWN directory is a
    # reviewed known_invisible entry (which only ever holds for a directory
    # Checkov sees nothing in at all, so the review already covers it).
    #
    # NO LEDGER ROW SHAPE ACCEPTS AN OPAQUE FILE IN A PRODUCING DIRECTORY
    # (/critic-gate finding, T4b architect pass, round 1, live-verified): an
    # earlier draft of the message below suggested adding a `known_invisible`
    # entry for the file's own directory as a remedy -- for exactly the
    # directory this branch fires on (one already producing SOME results),
    # that is immediately refused by the `now_visible` check above ("Checkov
    # can see this now"), so the suggested fix could never work. Same
    # doctrine as `classify_resource_address`'s unrepresentable shapes
    # (nested/indexed addresses, see the module docstring): fail-closed is
    # the right direction, but the message must say plainly that no ledger
    # edit can turn this green -- only moving the file (to its own directory,
    # which then CAN become a reviewed known_invisible entry), renaming it to
    # a suffix Checkov can read, or deleting it.
    exposed_opaque = sorted(
        f
        for f in tracked_files
        if f.endswith(OPAQUE_TERRAFORM_SUFFIXES) and _dirname(f) not in known_invisible_dirs
    )
    if exposed_opaque:
        errors.append(
            f"::error::opaque Terraform-adjacent file(s) outside any known_invisible "
            f"directory: {exposed_opaque}. Checkov's directory scan cannot read these "
            f"suffixes (see TRACKED_TERRAFORM_PATTERNS), and no ledger row shape accepts "
            f"one -- fail-closed, not a reviewed-edit case. The only ways to green this: "
            f"move the file to its own directory (which can then become a reviewed "
            f"known_invisible entry, if nothing else in it produces results), rename it to "
            f"a suffix Checkov's directory walk reads, or delete it."
        )

    # --- Failures (decision 3) ------------------------------------------
    expected_failing = {
        (group.get("check_id"), row.get("resource"), row.get("file_path"))
        for group in ledger.get("failures", [])
        if isinstance(group, dict) and isinstance(group.get("check_id"), str)
        for row in group.get("resources", [])
        if isinstance(row, dict)
        and isinstance(row.get("resource"), str)
        and isinstance(row.get("file_path"), str)
    }

    added = actual_failing - expected_failing
    removed = expected_failing - actual_failing
    if added:
        errors.append(
            f"::error::NEW Checkov failure(s), not in the ledger: {sorted(added)}. Add a "
            f"reviewed row (with tracking and review_by) after confirming this is an "
            f"accepted finding, or fix it."
        )
    if removed:
        errors.append(
            f"::error::Ledger-listed failure(s) no longer observed: {sorted(removed)}. "
            f"Confirm each is genuinely fixed, or was renamed, moved, or deleted, before "
            f"removing its row."
        )

    # --- Skips (decision 4) ----------------------------------------------
    expected_skip_map = {
        (group.get("check_id"), row.get("resource"), row.get("file_path")): row.get("suppress_comment")
        for group in ledger.get("skips", [])
        if isinstance(group, dict) and isinstance(group.get("check_id"), str)
        for row in group.get("resources", [])
        if isinstance(row, dict)
        and isinstance(row.get("resource"), str)
        and isinstance(row.get("file_path"), str)
        and isinstance(row.get("suppress_comment"), str)
    }

    skip_added = set(actual_skip_map) - set(expected_skip_map)
    skip_removed = set(expected_skip_map) - set(actual_skip_map)
    skip_changed = sorted(
        key
        for key in set(actual_skip_map) & set(expected_skip_map)
        if actual_skip_map[key] != expected_skip_map[key]
    )
    if skip_added:
        errors.append(
            f"::error::NEW #checkov:skip suppression(s), not in the ledger: "
            f"{sorted(skip_added)}. A skip removes a resource from the failing set exactly "
            f"as effectively as fixing it -- it must be a reviewed addition."
        )
    if skip_removed:
        errors.append(
            f"::error::Ledger-listed skip(s) no longer observed: {sorted(skip_removed)}. "
            f"Confirm before removing them."
        )
    if skip_changed:
        errors.append(
            f"::error::suppress_comment changed for {skip_changed}. The justification "
            f"lives once, in the ledger -- a changed inline comment is drift."
        )

    # --- review_by (decision 7) ------------------------------------------
    # Entries are stringified BEFORE sorting, not after (/critic-gate finding,
    # PR #490 architect review 6b): a group reaching this code with no
    # `check_id` -- unreachable via `run`, which validates first, but
    # directly reachable via the `evaluate` subcommand on a hand-built or
    # malformed ledger -- used to append the raw `None` here. `sorted()` on a
    # list mixing `str` and `NoneType` raises `TypeError`, which is an
    # unhandled traceback, not the `::error::`-and-exit-1 every other
    # malformed-input path in this function produces.
    stale = []
    for group in (*ledger.get("failures", []), *ledger.get("skips", [])):
        if not isinstance(group, dict):
            continue
        review_by = group.get("review_by")
        if isinstance(review_by, str):
            try:
                if date.fromisoformat(review_by) < today:
                    stale.append(str(group.get("check_id")))
            except ValueError:
                pass
    for entry in ledger.get("known_invisible", []):
        if not isinstance(entry, dict):
            continue
        review_by = entry.get("review_by")
        if isinstance(review_by, str):
            try:
                if date.fromisoformat(review_by) < today:
                    stale.append(str(entry.get("directory")))
            except ValueError:
                pass
    if stale:
        errors.append(f"::error::stale acceptance: re-review or fix -- past review_by: {sorted(stale)}.")

    if errors:
        lines.extend(errors)
        return 1, lines

    lines.append(
        f"Checkov evaluated: {passed} passed, {failed} failed, exactly the ledger's "
        f"{len(expected_failing)} accepted failure(s) across {len(ledger.get('failures', []))} "
        f"group(s) and {len(expected_skip_map)} sanctioned skip(s) across "
        f"{len(ledger.get('skips', []))} group(s); {len(tracked_dirs)} tracked director"
        f"{'y' if len(tracked_dirs) == 1 else 'ies'}, {len(known_invisible_dirs)} known-invisible."
    )
    return 0, lines


# ---------------------------------------------------------------------------
# `run` plumbing: git, checkov itself, the step summary
# ---------------------------------------------------------------------------


GIT_TIMEOUT_SECONDS = 30
CHECKOV_VERSION_TIMEOUT_SECONDS = 30
CHECKOV_SCAN_TIMEOUT_SECONDS = 600

# Environment variables Checkov reads as silent, un-audited equivalents of
# CLI flags (checkov/common/util/ext_argument_parser.py and env_vars_config.py,
# 3.3.8) -- most are already caught by this evaluator some other way
# (CKV_CHECK/CKV_FRAMEWORK would show up as a coverage or version-shaped
# drift), but CKV_SKIP_CHECK is not: it narrows the scan exactly like
# `--skip-check`, with nothing in `failed_checks` OR `skipped_checks` to
# show for it -- the same laundering shape decision 9's `.checkov.yaml`
# guard exists to close, through a channel that guard does not watch.
# NOT exhaustive (/critic-gate round 2, both critics): DOWNLOAD_EXTERNAL_MODULES
# and EXTERNAL_MODULES_DIR match no prefix here, and neither prefix nor these
# two names were found to move this repo's real scan when tried live -- listed
# anyway, on the theory that a denylist for a security control should not stay
# narrower than its own stated basis. Prefer widening this list on a
# genuine future finding over trusting it as complete.
_CHECKOV_ENV_PREFIXES = ("CKV_", "BC_", "PRISMA_", "CHECKOV_")
_CHECKOV_ENV_EXTRA_KEYS = ("DOWNLOAD_EXTERNAL_MODULES", "EXTERNAL_MODULES_DIR")


def _sanitize_env(env: dict) -> dict:
    return {
        k: v
        for k, v in env.items()
        if not k.startswith(_CHECKOV_ENV_PREFIXES) and k not in _CHECKOV_ENV_EXTRA_KEYS
    }


# Every extension this repo's OWN guards already treat as real OpenTofu
# configuration or real OpenTofu test config (decision 15, T4a's U3 --
# revised twice after /critic-gate: round 1 found the first draft inverted
# the repo's own settled doctrine on tracking-what-Checkov-can't-see; round
# 2 found the revised set still omitted the `.tofu`/`.tofu.json` half of
# that same doctrine). Two different reasons put a pattern here, and BOTH
# matter -- neither list is this evaluator's own invention; both are lifted
# from `scripts/assert_no_pull_request_trust.py`'s `HCL_SUFFIXES`/
# `JSON_CONFIG_SUFFIXES` and `scripts/assert_tenant_iam_role.py`'s
# `TEST_FILE_HCL_SUFFIXES`/`TEST_FILE_JSON_SUFFIXES`, all four already
# load-bearing elsewhere in this repo:
#
# * `*.tf`, `*.tftest.hcl`, `*.tofutest.hcl` -- Checkov's OWN directory-walk
#   file filter reads all three of these (`checkov/terraform/tf_parser.py`'s
#   `handle_variables`: `file.name.endswith(".tf") or
#   file.name.endswith(".hcl")`, U3-verified in source and empirically for
#   `.tftest.hcl`).
# * `*.tf.json`, `*.tofu`, `*.tofu.json`, `*.tftest.json`, `*.tofutest.json`
#   -- Checkov's directory walk does NOT read any of these (U3: a
#   `.tf.json`-only directory produced `resource_count: 0`, confirmed
#   against the same source, which also carries its own `# TODO: add
#   support for .tf.json`; `.tofu`/`.tofu.json` verified ABSENT from that
#   same source file entirely -- Checkov's terraform framework has no
#   knowledge of the `.tofu` extension at all)
#   -- and that is PRECISELY why they belong here, not why they were
#   dropped. T4a's first draft of this decision got this backwards: it read
#   "Checkov cannot see it" as "so do not track it", when decision 5's whole
#   mechanism is built to catch exactly that shape -- a directory Checkov
#   structurally cannot see must either produce known_invisible's reviewed
#   exemption or show up in `silent`, the same way
#   `terraform-cloudflare-dns`'s `cloudflare_dns_record` root already does.
#   Precisely: that holds when EVERY tracked file in the directory is
#   opaque. Opacity is a property of the file, not the directory -- an
#   opaque file beside readable ones (verified: `main.tf` + `extra.tf.json`
#   scans as `resource_count: 1`, the JSON resource simply absent, nothing
#   red) used to be scanned by nothing and flagged by nothing; that was the
#   "resource silently dropping out" class in the docstring (fresh-session
#   review, PR #493, named as a T4b candidate rather than a T4a fix). T4b
#   closes it: `OPAQUE_TERRAFORM_SUFFIXES` names the five opaque patterns
#   below, and `evaluate`'s file-level check reds any tracked file matching
#   one of them whose own directory is not a reviewed `known_invisible`
#   entry.
#   This repo has hit and fixed the identical "glob the opaque variant too,
#   let its opacity propagate as unreadable/uncovered rather than silently
#   vanish" bypass more than once before (`.ai/project.yml`'s
#   `scripts/assert_tenant_variables.py` entry, IAC-BL-55 round 2, and
#   `.tfvars.json`/IAC-BL-54 before that) -- dropping a pattern here on the
#   theory that Checkov's blindness makes it safe to ignore would repeat
#   that same class again.
# Confirmed today's ledger baseline is unaffected: `bootstrap/modules/
# tenant-iam-role/tests/` (matched by `*.tftest.hcl`) is the ONLY one of the
# seven patterns beyond `*.tf` that matches any file in this repo today --
# it is the 12th tracked directory decision 15 already names. The other six
# match nothing, so widening from `*.tf`+`*.tftest.hcl` to all eight
# patterns changes zero of the 12 currently tracked directories.
TRACKED_TERRAFORM_PATTERNS = (
    "*.tf",
    "*.tf.json",
    "*.tofu",
    "*.tofu.json",
    "*.tftest.hcl",
    "*.tofutest.hcl",
    "*.tftest.json",
    "*.tofutest.json",
)

# The five of the eight patterns above that Checkov's directory walk does NOT
# read (see the comment on TRACKED_TERRAFORM_PATTERNS) -- suffixes for which
# a tracked file "counts" for coverage purposes but contributes zero
# passed/failed/skipped results of its own. T4b (Sprint 15, fresh-session
# review of PR #493, "opacity is per file, not per directory"): a directory
# stays visibly producing as long as ONE neighbouring `.tf` file has results,
# so decision 5's directory-level coverage rule (and `known_invisible`, which
# `evaluate`'s `now_visible` check forbids for any directory that produces
# anything) can never see one of these files drop out silently. This tuple is
# what the file-level check in `evaluate` walks.
OPAQUE_TERRAFORM_SUFFIXES = (
    ".tf.json",
    ".tofu",
    ".tofu.json",
    ".tftest.json",
    ".tofutest.json",
)


def git_tracked_terraform_files(repo_root: Path) -> list[str]:
    """Repo-relative paths `git` shows tracking under
    `TRACKED_TERRAFORM_PATTERNS`. Fails loudly (raises) on any git error OR
    an empty result -- a shallow clone is fine, a missing `.git` is not, and
    this never falls back to a filesystem walk that would happily count
    ignored worktrees instead (see U2, `#488`). An empty result is treated
    the same as an error: a coverage rule with zero tracked files is
    vacuously satisfied by anything, which is a worse failure mode than a
    loud one."""
    try:
        proc = subprocess.run(
            # `-z`: NUL-separated, never C-quoted. Without it, `core.quotePath`'s
            # default renders a non-ASCII path as the literal string
            # `"d/caf\303\251.tf"` (quotes included), which then reads as a
            # directory named `"d` -- a coverage-gap red naming a directory
            # that does not exist (fresh-session review, PR #493; pre-dated
            # T4a in `git_tracked_tf_dirs`). `surrogateescape` keeps a path
            # that is not valid UTF-8 round-trippable through `Path` rather
            # than raising inside the decode.
            ["git", "ls-files", "-z", "--", *TRACKED_TERRAFORM_PATTERNS],
            cwd=repo_root,
            capture_output=True,
            encoding="utf-8",
            errors="surrogateescape",
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"`git ls-files` did not finish within {GIT_TIMEOUT_SECONDS}s: {exc}") from exc
    except OSError as exc:
        # `git` itself missing/unexecutable (`FileNotFoundError`, a
        # `PermissionError`) used to escape as a bare traceback rather than
        # this function's own `::error::`-and-exit-1 contract -- newly
        # reachable from `cmd_validate` too as of T4a, which did not touch
        # `git` before this diff (/critic-gate finding).
        raise RuntimeError(f"could not run `git ls-files`: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"`git ls-files` exited {proc.returncode}: {proc.stderr.strip()}")
    files = sorted({entry for entry in proc.stdout.split("\0") if entry})
    if not files:
        quoted_patterns = " ".join(f"'{p}'" for p in TRACKED_TERRAFORM_PATTERNS)
        raise RuntimeError(
            f"`git ls-files -- {quoted_patterns}` returned no tracked files. Treating this "
            f"as unreadable, not as 'nothing to cover' -- a coverage rule with zero tracked "
            f"files is satisfied by anything."
        )
    return files


def _dirs_from_files(files: list[str]) -> set[str]:
    return {str(Path(f).parent) for f in files}


def write_step_summary(ledger: dict) -> None:
    """Even a green run keeps the accepted debt visible -- decision 1 makes
    green mean 'matches exactly', not 'nothing to see here'."""
    failures = ledger.get("failures", []) if isinstance(ledger, dict) else []
    skips = ledger.get("skips", []) if isinstance(ledger, dict) else []
    known_invisible = ledger.get("known_invisible", []) if isinstance(ledger, dict) else []
    accepted = sum(len(g.get("resources", [])) for g in failures if isinstance(g, dict)) + sum(
        len(g.get("resources", [])) for g in skips if isinstance(g, dict)
    )
    print(
        f"::notice::checkov-ledger: {accepted} accepted finding(s)/skip(s) across "
        f"{len(failures)} failure group(s), {len(skips)} skip group(s), "
        f"{len(known_invisible)} known-invisible director"
        f"{'y' if len(known_invisible) == 1 else 'ies'}."
    )

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    out = ["# Checkov ledger", "", "| type | check_id | resources | tracking | review_by |", "|---|---|---|---|---|"]
    for group in failures:
        if isinstance(group, dict):
            out.append(
                f"| failure | {group.get('check_id')} | {len(group.get('resources', []))} | "
                f"{group.get('tracking')} | {group.get('review_by')} |"
            )
    for group in skips:
        if isinstance(group, dict):
            out.append(
                f"| skip | {group.get('check_id')} | {len(group.get('resources', []))} | "
                f"{group.get('tracking')} | {group.get('review_by')} |"
            )
    for entry in known_invisible:
        if isinstance(entry, dict):
            out.append(
                f"| known_invisible | -- ({entry.get('directory')}) | 1 | "
                f"{entry.get('tracking')} | {entry.get('review_by')} |"
            )
    with open(summary_path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


def _load_ledger_file(path: str) -> tuple[dict | None, str | None]:
    p = Path(path)
    if not p.is_file():
        return None, f"::error::ledger file {path!r} does not exist."
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"::error::could not parse ledger {path!r} as JSON: {exc}"
    if not isinstance(data, dict):
        return None, f"::error::ledger {path!r} must be a JSON object."
    return data, None


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    ledger, err = _load_ledger_file(args.ledger)
    if err:
        print(err, file=sys.stderr)
        return 1
    repo_root = Path.cwd()
    try:
        tracked_files = git_tracked_terraform_files(repo_root)
    except RuntimeError as exc:
        print(f"::error::Could not list git-tracked Terraform files: {exc}", file=sys.stderr)
        return 1
    ok, lines = validate_ledger(ledger, repo_root, tracked_files)
    for line in lines:
        print(line, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


def cmd_evaluate(args: argparse.Namespace) -> int:
    ledger, err = _load_ledger_file(args.ledger)
    if err:
        print(err, file=sys.stderr)
        return 1
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"::error::Could not parse Checkov output as JSON: {exc}", file=sys.stderr)
        print(raw[:2000], file=sys.stderr)
        return 1

    if args.today:
        try:
            today = date.fromisoformat(args.today)
        except ValueError:
            print(f"::error::--today {args.today!r} is not an ISO date.", file=sys.stderr)
            return 1
    else:
        today = date.today()

    exit_code, lines = evaluate(
        data, ledger, set(args.tracked_dir), tuple(args.tracked_file), args.checkov_version, today
    )
    stream = sys.stderr if exit_code else sys.stdout
    for line in lines:
        print(line, file=stream)
    return exit_code


def cmd_run(args: argparse.Namespace) -> int:
    repo_root = Path.cwd()

    # Cheapest and most load-bearing guard first, before the ledger is even
    # read: every location CHECKOV ITSELF auto-discovers a config from
    # (--directory, cwd, $HOME -- verified against
    # checkov/common/util/config_utils.py, 3.3.8), for both spellings, with
    # no CLI flag able to override or disable that discovery. A skip-path/
    # skip-check entry there would silently narrow this scan behind both
    # this job's and any sibling scan's back. This needs a deliberate
    # design decision (and ledger update), not a file added on its own.
    scan_root = Path(args.directory)
    discovery_dirs = {scan_root, Path.cwd()}
    # Checkov resolves its home-directory config via `Path.home()`
    # (`expanduser('~')`), which falls back to the passwd database when
    # `$HOME` is unset -- reading only `os.environ["HOME"]` would silently
    # skip the location Checkov still auto-discovers in that case
    # (live-verified, /critic-gate round 2 security-critic).
    try:
        discovery_dirs.add(Path.home())
    except RuntimeError:
        pass
    for candidate in discovery_dirs:
        for name in (".checkov.yaml", ".checkov.yml"):
            if (candidate / name).exists():
                print(
                    f"::error::{name} exists at {candidate}, one of Checkov's own "
                    f"config-discovery locations ({sorted(str(d) for d in discovery_dirs)}). "
                    f"This needs a deliberate design decision (and ledger update), not a "
                    f"file added on its own.",
                    file=sys.stderr,
                )
                return 1

    # Coverage (git ls-files) and validate's file lookups are repo-root-
    # relative; Checkov's own file_paths are relative to --directory. Those
    # only agree when the two are the same path -- see "WHAT THIS DOES NOT
    # DO" in the module docstring.
    if Path(args.directory).resolve() != repo_root.resolve():
        print(
            f"::error::--directory {args.directory!r} does not resolve to the current "
            f"working directory {repo_root}. This evaluator's coverage and validate "
            f"checks assume the scan root is the repository root -- scanning a "
            f"subdirectory is not supported.",
            file=sys.stderr,
        )
        return 1

    ledger, err = _load_ledger_file(args.ledger)
    if err:
        print(err, file=sys.stderr)
        return 1

    try:
        tracked_files = git_tracked_terraform_files(repo_root)
    except RuntimeError as exc:
        print(f"::error::Could not list git-tracked Terraform files: {exc}", file=sys.stderr)
        return 1
    tracked_dirs = _dirs_from_files(tracked_files)

    ok, lines = validate_ledger(ledger, repo_root, tracked_files)
    for line in lines:
        print(line, file=sys.stdout if ok else sys.stderr)
    if not ok:
        return 1

    try:
        version_proc = subprocess.run(
            ["checkov", "--version"],
            capture_output=True,
            text=True,
            timeout=CHECKOV_VERSION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        print(f"::error::`checkov --version` did not finish within {CHECKOV_VERSION_TIMEOUT_SECONDS}s: {exc}", file=sys.stderr)
        return 1
    if version_proc.returncode != 0:
        print(
            f"::error::`checkov --version` exited {version_proc.returncode}: "
            f"{version_proc.stderr.strip()}",
            file=sys.stderr,
        )
        return 1
    checkov_version = version_proc.stdout.strip()

    # Every CKV_*/BC_*/PRISMA_* env var stripped -- Checkov honors several
    # (CKV_SKIP_CHECK chief among them) as silent equivalents of a CLI flag
    # this evaluator otherwise has no visibility into (see _sanitize_env).
    try:
        scan_proc = subprocess.run(
            ["checkov", "-d", args.directory, "--framework", args.framework, "--output", "json"],
            capture_output=True,
            text=True,
            env=_sanitize_env(dict(os.environ)),
            timeout=CHECKOV_SCAN_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        print(f"::error::checkov did not finish within {CHECKOV_SCAN_TIMEOUT_SECONDS}s: {exc}", file=sys.stderr)
        return 1
    if scan_proc.returncode not in (0, 1):
        print(
            f"::error::checkov itself exited {scan_proc.returncode} (a crash, not just "
            f"'a check failed').",
            file=sys.stderr,
        )
        print((scan_proc.stdout + scan_proc.stderr)[:2000], file=sys.stderr)
        return scan_proc.returncode or 1

    try:
        data = json.loads(scan_proc.stdout)
    except json.JSONDecodeError as exc:
        print(f"::error::Could not parse Checkov output as JSON: {exc}", file=sys.stderr)
        print(scan_proc.stdout[:2000], file=sys.stderr)
        return 1

    exit_code, eval_lines = evaluate(
        data, ledger, tracked_dirs, tuple(tracked_files), checkov_version, date.today()
    )
    stream = sys.stderr if exit_code else sys.stdout
    for line in eval_lines:
        print(line, file=stream)

    write_step_summary(ledger)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="checkov_ledger.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="validate the ledger, run Checkov, and evaluate it")
    p_run.add_argument("--directory", default=".")
    p_run.add_argument("--framework", default="terraform")
    p_run.add_argument("--ledger", default="checkov-ledger.json")
    p_run.set_defaults(func=cmd_run)

    p_validate = sub.add_parser("validate", help="validate a ledger file with no live scan")
    p_validate.add_argument("--ledger", default="checkov-ledger.json")
    p_validate.set_defaults(func=cmd_validate)

    p_evaluate = sub.add_parser(
        "evaluate", help="evaluate Checkov JSON (stdin) against the ledger with no live scan"
    )
    p_evaluate.add_argument("--ledger", default="checkov-ledger.json")
    p_evaluate.add_argument("--checkov-version", required=True)
    p_evaluate.add_argument("--tracked-dir", action="append", default=[])
    p_evaluate.add_argument("--tracked-file", action="append", default=[])
    p_evaluate.add_argument("--today", default=None, help="ISO date; defaults to today (for tests)")
    p_evaluate.set_defaults(func=cmd_evaluate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
