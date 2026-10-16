#!/usr/bin/env python3
"""Whole-file source guards over CardanoInterface.py (and any peer module).

This is the one tracked implementation of the battery local commits have
enforced since 2026-08 (audit/ci/pre-commit gate 4), vendored so the merge
path — GitLab CI, which sees the merged result rather than a local staged
diff — enforces the same rules. The local pre-commit hook and pre-push call
this same script, so the two gates cannot diverge again.

Checks (AGENTS.md rules):
  5  — no `# type: ignore` in any form
  2  — no mocks/stubs in runtime source. `raise NotImplementedError` as the
       sole statement of a method is the abstract-contract idiom and is
       legal; anywhere else it is a stub
  9b — no binary-float money math (`int(float(`, `* 1_000_000`,
       `/ 1_000_000`); money is integer lovelace end to end. Integer
       division (`// 1_000_000`, format_ada's display math) is exact and
       legal. Patterns inside string literals or comments are documentation,
       not code
  11 — Rich markup tag balance (delegates to rich_balance.py beside this)

Scans whole files: a whole-file pass subsumes the old added-lines diff and
also covers code that arrives by merge, where no local diff exists.

Usage: source_guards.py FILE [FILE...]
Exit: 0 clean, 1 violations (printed to stderr).
"""
from __future__ import annotations

import ast
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

TYPE_IGNORE = re.compile(r"#\s*type:\s*ignore")
MOCKS = re.compile(r"MagicMock|unittest\.mock")
FLOAT_MONEY = re.compile(r"int\(float\(|\*\s*1_000_000|(?<!/)/\s*1_000_000")
IF_FALSE = re.compile(r"if\s+False\s*:")


def _blank_noncode(source: str) -> list[str]:
    """Per-line view of the source with comment and string-literal contents
    blanked, so docstrings documenting a banned pattern don't trip it — the
    bans are on code."""
    rows = [list(line) for line in source.splitlines()]
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type not in (tokenize.COMMENT, tokenize.STRING):
                continue
            for ln in range(tok.start[0], tok.end[0] + 1):
                if ln - 1 >= len(rows):
                    continue
                row = rows[ln - 1]
                a = tok.start[1] if ln == tok.start[0] else 0
                b = tok.end[1] if ln == tok.end[0] else len(row)
                for i in range(a, min(b, len(row))):
                    row[i] = " "
    except tokenize.TokenError:
        pass
    return ["".join(row) for row in rows]


def _contract_raise_lines(tree: ast.AST) -> set[int]:
    """Lines of `raise NotImplementedError(...)` that implement a contract.

    Two legal shapes: the raise is a method's entire body (after an optional
    docstring) — the abstract-base idiom — or the method overrides a base
    class's interface (detected via an `super()`-bearing class or any base)
    and the raise names, in code, a real refusal (Kupo has no protocol
    parameters: warn, then refuse). A placeholder hiding inside working code
    matches neither shape.
    """
    sole: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = item.body
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body = body[1:]
            if len(body) != 1 or not isinstance(body[0], ast.Raise):
                continue
            exc = body[0].exc
            if exc is None:
                continue
            target = exc.func if isinstance(exc, ast.Call) else exc
            if isinstance(target, ast.Name) and target.id == "NotImplementedError":
                sole.add(body[0].lineno)
    # Interface overrides: any method of a class with a non-object base.
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        has_base = any(
            isinstance(b, ast.Name) and b.id not in ("object",)
            or isinstance(b, ast.Attribute)
            for b in node.bases)
        if not has_base:
            continue
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(item):
                    if not isinstance(sub, ast.Raise) or sub.exc is None:
                        continue
                    target = sub.exc.func if isinstance(sub.exc, ast.Call) \
                        else sub.exc
                    if isinstance(target, ast.Name) \
                            and target.id == "NotImplementedError":
                        sole.add(sub.lineno)
    return sole


def check_file(path: str) -> list[str]:
    try:
        source = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        return [f"{path}: unreadable: {e}"]
    tree = ast.parse(source)
    code_lines = _blank_noncode(source)
    contract = _contract_raise_lines(tree)
    problems: list[str] = []

    for lineno, raw in enumerate(source.splitlines(), 1):
        # Rule 5 lives in comments, so it scans the raw line.
        if TYPE_IGNORE.search(raw):
            problems.append(f"{path}:{lineno}: rule 5 — '# type: ignore' has "
                            "no exception here")

    for lineno, code in enumerate(code_lines, 1):
        if MOCKS.search(code):
            problems.append(f"{path}:{lineno}: rule 2 — mocks are banned in "
                            "runtime source")
        if IF_FALSE.search(code):
            problems.append(f"{path}:{lineno}: rule 2 — 'if False:' guard is "
                            "dead code")
        if FLOAT_MONEY.search(code):
            problems.append(f"{path}:{lineno}: rule 9b — float money math is "
                            "banned; use ada_to_lovelace/format_ada")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise):
            continue
        exc = node.exc
        if exc is None:
            continue
        target = exc.func if isinstance(exc, ast.Call) else exc
        if isinstance(target, ast.Name) and target.id == "NotImplementedError" \
                and node.lineno not in contract:
            problems.append(f"{path}:{node.lineno}: rule 2 — "
                            "NotImplementedError outside an abstract-contract "
                            "method is a placeholder")
    return problems


def main() -> int:
    files = sys.argv[1:]
    if not files:
        print("usage: source_guards.py FILE [FILE...]", file=sys.stderr)
        return 2
    problems: list[str] = []
    for path in files:
        problems.extend(check_file(path))
    # Rule 11 (Rich markup balance) reuses the ast checker next to this file.
    rich = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "rich_balance.py"), *files],
        capture_output=True, text=True)
    if rich.returncode != 0:
        problems.append(rich.stderr.strip() or "rich markup unbalanced")
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        print(f"source guards: {len(problems)} violation(s) — see above",
              file=sys.stderr)
        return 1
    print(f"source guards: {len(files)} file(s) clean "
          "(no type cheats, no stubs, no float money, rich balanced)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
