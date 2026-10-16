# Git state knowledgebase

A claim about the repo is admitted only with evidence mechanically anchored
in real git history (verified by `verify_git_claims.py`, which derives the
supporting sha from `git log -S`).

## 2026-10-16 — Audit run-3 remediation and testbuilder hardening

- claim: The merge-path CI gate asserts the source-guard battery's sibling script exists before running it, so a fresh clone fails with an infrastructure error instead of a false violation.
  evidence-path: .gitlab-ci.yml
  evidence-quote: |
      - test -f audit/ci/rich_balance.py || { echo 'audit/ci/rich_balance.py is not tracked — battery incomplete'; exit 1; }
- claim: CHANGELOG records the audit run-3 remediation batch as a new Unreleased section dated 2026-10-16.
  evidence-path: CHANGELOG.md
  evidence-quote: |
      ## [Unreleased] — 2026-10-16
- claim: The corrupted format_ada oracle test names are replaced with correct expectations (1 lovelace renders as 0.000001).
  evidence-path: tests/test_money.py
  evidence-quote: |
      def test_1_lovelace_formatted_as_0_000001():
- claim: Oracle stamp handling is centralized in a helper that returns every Oracle-Hash stamp in a file, in order.
  evidence-path: testbuilder/knowledge.py
  evidence-quote: |
      def _oracle_stamps(text: str) -> list[str]:
- claim: The wholesale audit/ ignore is narrowed so exactly the two CI battery files are tracked on a fresh clone.
  evidence-path: .gitignore
  evidence-quote: |
      !audit/ci/source_guards.py
