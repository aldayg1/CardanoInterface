"""Fix missing-space implicit string concatenations in place: inserts a space
before the closing quote of the FIRST literal of each flagged pair."""

import io
import sys
import tokenize

path = sys.argv[1] if len(sys.argv) > 1 else "CardanoInterface.py"
SRC = open(path).read()

pairs = []  # (end_offset_of_first_literal, line_for_report)
prev_body, prev_end = "", 0
for tok in tokenize.generate_tokens(io.StringIO(SRC).readline):
    if tok.type == tokenize.STRING:
        raw = tok.string.lstrip("fFrRbBuU")
        body = raw[3:-3] if raw[:3] in ('"""', "'''") else raw[1:-1]
        if prev_body and tok.start[0] - SRC[:prev_end].count("\n") >= 0:
            a, b = prev_body, body
            if (a and b and a[-1].isalnum() and b[0].isalnum()
                    and not a.endswith("\\n") and not b.startswith("\\n")):
                # tok.start[0] - prev_line_within_4 handled loosely; rely on
                # adjacency: only flag when the gap contains no code token.
                pass
        prev_body, prev_end = body, tok.end[1] + sum(
            len(l) for l in SRC.splitlines(keepends=True)[:tok.end[0] - 1])
    elif tok.type in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT,
                      tokenize.INDENT, tokenize.DEDENT):
        # remember candidate across whitespace only
        continue
    else:
        prev_body = ""

# The single-pass above is hard to carry state across tokens cleanly; do a
# simpler two-pass: collect string tokens with absolute offsets, then pair
# adjacent ones (only whitespace between them).
toks = []
for tok in tokenize.generate_tokens(io.StringIO(SRC).readline):
    if tok.type == tokenize.STRING:
        toks.append(tok)
src_lines = SRC.splitlines(keepends=True)

def abs_pos(row, col):
    return sum(len(l) for l in src_lines[:row - 1]) + col

string_spans = [(abs_pos(t.start[0], t.start[1]),
                 abs_pos(t.end[0], t.end[1]), t) for t in toks]

edits = []
for (s1, e1, t1), (s2, e2, t2) in zip(string_spans, string_spans[1:]):
    between = SRC[e1:s2]
    if between.strip() != "" or abs(t2.start[0] - t1.end[0]) > 4:
        continue
    raw1 = t1.string.lstrip("fFrRbBuU")
    raw2 = t2.string.lstrip("fFrRbBuU")
    b1 = raw1[3:-3] if raw1[:3] in ('"""', "'''") else raw1[1:-1]
    b2 = raw2[3:-3] if raw2[:3] in ('"""', "'''") else raw2[1:-1]
    if (b1 and b2 and b1[-1].isalnum() and b2[0].isalnum()
            and not b1.endswith("\\n") and not b2.startswith("\\n")):
        edits.append((e1, t2.start[0], b1[-25:], b2[:25]))

for e1, line, tail, head in sorted(edits, reverse=True):
    SRC = SRC[:e1 - 1] + " " + SRC[e1 - 1:]
    print(f"fixed line {line}: ...{tail!r} + {head!r}")

open(path, "w").write(SRC)
print(f"\n{len(edits)} join(s) fixed")
