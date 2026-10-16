#!/usr/bin/env python3
"""Rich markup tag-balance check (AGENTS.md rule 11: opening/closing tags match).

Parses the source with ast, extracts every console.print/console.log call,
joins implicitly-concatenated string literals the way Python does, and checks
tag balance per rendered argument — the unit Rich actually applies markup to.
Per-line checking is wrong here: markup legally spans physical lines inside a
multi-part string literal.

Fails (exit 1) on: closing tag with empty/mismatched stack, unclosed tags.
Conservative shapes only: [style], [/style], [/] with style names ≥2 chars
containing a letter (skips literal [x] checkboxes, [7] indices, [ ] boxes);
[[ escapes are ignored.

Usage: rich_balance.py FILE [FILE...]
"""
from __future__ import annotations

import ast
import re
import sys

TAG = re.compile(r"(?<!\[)\[(/?)([a-zA-Z0-9_ ]{2,})\](?!\])")
BARE_CLOSE = re.compile(r"(?<!\[)\[/\](?!\])")
HAS_LETTER = re.compile(r"[a-zA-Z]")


def string_parts(node: ast.AST) -> list[str]:
    """String literals reachable in an expression, in source order (implicit
    concatenation, JoinedStr literal chunks)."""
    parts: list[str] = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        parts.append(node.value)
    elif isinstance(node, ast.JoinedStr):
        for v in node.values:
            parts.extend(string_parts(v))
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        parts.extend(string_parts(node.left))
        parts.extend(string_parts(node.right))
    elif isinstance(node, ast.Paren) if hasattr(ast, "Paren") else False:  # pragma: no cover
        pass
    return parts


def check_text(text: str, where: str) -> list[str]:
    # One stack per rendered argument: Rich markup legally spans newlines
    # inside a single string (multi-line banners), so there is no per-line
    # reset — the argument is the balance unit.
    errors: list[str] = []
    stack: list[str] = []
    events = sorted(
        [(m.start(), "tag", m) for m in TAG.finditer(text)]
        + [(m.start(), "bare", m) for m in BARE_CLOSE.finditer(text)]
    )
    for _, kind, m in events:
        if kind == "bare":
            if not stack:
                errors.append(f"{where}: bare [/] with empty stack")
            else:
                stack.pop()
            continue
        closing, name = m.group(1), m.group(2).strip()
        if not HAS_LETTER.search(name):
            continue
        if closing == "/":
            if not stack:
                errors.append(f"{where}: closing [/{name}] with empty stack")
            else:
                top = stack.pop()
                if top != name:
                    errors.append(f"{where}: [/{name}] closes [{top}]")
        else:
            stack.append(name)
    if stack:
        errors.append(f"{where}: unclosed {stack}")
    return errors


def main() -> int:
    errors: list[str] = []
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8", errors="replace") as fh:
            tree = ast.parse(fh.read(), filename=path)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("print", "log")
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "console"):
                continue
            for arg in node.args:
                parts = string_parts(arg)
                if parts:
                    errors.extend(check_text("".join(parts), f"{path}:{node.lineno}"))
    for err in errors:
        print(err, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
