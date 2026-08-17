"""Static scan: find console.print calls whose concatenated literal carries
unbalanced Rich markup tags (opens without closes, closes without opens)."""

import ast
import re
import sys

SRC = open(sys.argv[1] if len(sys.argv) > 1 else "CardanoInterface.py").read()
tree = ast.parse(SRC)

TAG_RE = re.compile(r"\[(/?)([a-zA-Z_][a-zA-Z0-9_ #]+)\]")
# things that are style-tag-like but not colors/styles we treat as findings only
# when unbalanced; escaped \[ and bracket-classes like [0-9] are ignored.

def literal_of(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        l, r = literal_of(node.left), literal_of(node.right)
        return None if l is None or r is None else l + r
    return None

findings = []
for n in ast.walk(tree):
    if not isinstance(n, ast.Call):
        continue
    func = n.func
    name = ""
    if isinstance(func, ast.Attribute):
        name = func.attr
    elif isinstance(func, ast.Name):
        name = func.id
    if name != "print":
        continue
    if n.args:
        lit = literal_of(n.args[0])
        if lit is None:
            continue
        # unescape \'\'\' etc not needed; skip escaped brackets
        tags = TAG_RE.findall(lit)
        opens = [t for slash, t in tags if slash == ""]
        closes = [t for slash, t in tags if slash == "/"]
        if sorted(opens) != sorted(closes):
            findings.append((n.lineno, opens, closes, lit[:110]))

for lineno, opens, closes, prev in findings:
    print(f"line {lineno}: opens={opens} closes={closes}")
    print(f"    {prev!r}")
print(f"\n{len(findings)} unbalanced print call(s)")
