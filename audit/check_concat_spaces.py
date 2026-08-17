"""Find implicit string concatenations whose join is missing a space, via
tokenize (the AST merges adjacent literals before they can be inspected).

Only NL/NEWLINE/COMMENT/INDENT may separate the two literals — anything else
(tuple commas, operators, f-string parts) resets the candidate."""

import io
import sys
import tokenize

SRC = open(sys.argv[1] if len(sys.argv) > 1 else "CardanoInterface.py").read()

findings = []
prev_body = ""
prev_row = 0
for tok in tokenize.generate_tokens(io.StringIO(SRC).readline):
    if tok.type == tokenize.STRING:
        raw = tok.string.lstrip("fFrRbBuU")
        if raw[:3] in ('"""', "'''"):
            body = raw[3:-3]
        else:
            body = raw[1:-1]
        if prev_body and tok.start[0] - prev_row <= 4:
            a, b = prev_body, body
            # an explicit \n on either side is an intentional line break
            if (a and b and a[-1].isalnum() and b[0].isalnum()
                    and not a.endswith("\\n") and not b.startswith("\\n")):
                findings.append((tok.start[0], a[-40:], b[:40]))
        prev_body, prev_row = body, tok.start[0]
    elif tok.type in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT,
                      tokenize.INDENT, tokenize.DEDENT):
        continue
    else:
        prev_body = ""

for lineno, tail, head in findings:
    print(f"line {lineno}: ...{tail!r} + {head!r}...")
print(f"\n{len(findings)} missing-space join(s)")
