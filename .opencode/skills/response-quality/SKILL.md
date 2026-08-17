---
name: response-quality
description: "Mandatory self-audit before every response or implementation. Checks whether the output actually satisfies the original request, is grounded in real sources, and obeys CardanoInterface rules. Forces honest assessment and one corrective action — not infinite iteration. Triggers on: every response, every code change, before handoff, before commit, quality check, did this work, is this right, self-review, response audit, user correction, user rejection, negative user response."
---

# Response Quality Gate

Every response you produce — code, explanation, plan, or fix — must pass
this gate before you deliver it. This is not optional self-reflection;
it is a mandatory checkpoint. Failure to pass means you fix it now, not
later.

## When this skill triggers

This skill is NOT self-loaded for fun. It triggers on these specific
conditions:

1. **Before any deliverable** — code, explanation, plan, fix, or
   handoff. You run the gate, you deliver the result.
2. **On failure to validate real behavior** — if your output was not
   validated against real behavior, this skill triggers and you must
   validate before proceeding.
3. **On negative user response** — if the user says "this doesn't work,"
   "this is wrong," "this misses the point," or any equivalent
   rejection, this skill triggers immediately. You do NOT defend your
   previous output. You re-enter the gate with the user's correction as
   new input.

On trigger #3, you do NOT say "let me try again" and repeat the same
work. You read the user's correction, you identify which of the three
questions you failed, you fix that specific gap using AGENTS.md and the
loaded context, and you deliver the corrected output.

## BEFORE ANYTHING: the anti-loop constraint

This skill exists to make you produce ONE correct response, not to give
you an excuse to cycle forever. Read this section and obey it before you
do anything else.

**The rule is simple: assess once, fix once, deliver.**

You get ONE pass through the quality gate. If you fail, you identify the
gap, you fix it, you deliver. That is the end. You do NOT:

- Re-audit the fixed output to see if the fix itself passes. It does, by
  virtue of being the fix. Stop.
- Say "let me reconsider" and restart the gate. You already considered.
  That is what the gate IS.
- Produce a second draft "to be safe." Safe is correct. One correct
  answer is safe. Two answers are noise.
- Ask yourself "is this good enough?" after you already answered that
  question. You answered it. Move on.
- Add disclaimers, caveats, or "I want to make sure..." preambles to
  pad the response. If you fixed the gap, say what you did and stop.

**If you find yourself in a loop** — same gate, same kind of failure,
same kind of fix, repeated — you are failing THIS constraint, not the
quality gate. Break out immediately and deliver whatever you have. A
delivered correct answer beats an endlessly polished wrong one.

**The quality gate is a filter, not a treadmill.** It catches bad output
before it reaches the user. Once the bad output is caught and replaced,
you are done. The gate does not re-run on the replacement. The
replacement is the output.

## The three questions (answer all, honestly)

Before delivering anything, answer these in order. If any answer is "no"
or "unsure", you are not done — but you are only allowed ONE fix cycle.

### 0. Am I using the full session context?

This is not question zero. This is the precondition for all three.

The user's prompt is not a standalone signal. It arrived alongside every
loaded context file: `AGENTS.md`, `MULTISIG_SPEC.md`, the relevant skills,
the code already read in this session. ALL of this is the prompt. The
user's words plus the loaded context IS the request.

If you answer the three questions below using only the user's literal
words, you will fail. The user expects you to integrate the context. The
context IS the rules. Ignoring it is the same as ignoring the request.

Before you begin the gate, confirm you have considered:
- The user's literal request
- Every context file loaded in this session
- Every skill that matched the task
- The current state of the codebase (what was read, not what you recall)

If you have not read a relevant file in this session, read it now. Do
not guess what it says from a previous session or from memory.

### 1. Does this satisfy the original request?

Re-read the user's actual prompt. Not what you think they asked. Not the
nearest thing you know how to do. The literal request.

- Did you address every part of what was asked?
- Did you answer the question that was posed, or a nearby question you
  found more comfortable?
- If the request had multiple parts, did you handle ALL of them?
- If you deferred something, did you say so explicitly and why?

**If no:** Stop. Do not deliver a partial or adjacent answer. Go back and
address the actual request. If you cannot (missing info, blocked by
something), say exactly what is missing and what you need to proceed.

### 2. Is this grounded in real sources?

Every claim, implementation choice, and code path must be backed by an
actual source — not memory, not pattern-matching, not "this is how it
usually works."

Accepted sources (in order of authority):
1. The project's own files: `AGENTS.md`, `MULTISIG_SPEC.md`, `CardanoInterface.py`
2. Source code already in the repository (read it, don't guess)
3. Official documentation for external libraries/tools (use the
   `research-before-guessing` skill — `context7_query-docs`, `websearch`,
   `webfetch`)
4. The library/tool's own repository (README, docs/, CHANGES)

NOT accepted:
- "I recall that..." / "In my training data..." / "Usually..."
- Stack Overflow answers without verifying against official docs
- Blog posts, tutorials, or AI-generated content as primary sources
- Assumptions based on similar but different libraries

**If no:** Load `research-before-guessing` and find the real answer before
proceeding. One research pass, not a loop. Find the authoritative source,
verify it applies to the version in use, then proceed.

### 3. Does this obey the project's documented rules?

Check against every binding rule in `AGENTS.md`:

- **Rule 1**: Did you validate real behavior? Did you observe the actual
  result, not assume it works because you wrote it?
- **Rule 2**: Did you avoid mocks, stubs, fakes, placeholder
  implementations, or "it imports without crashing" claims as
  validation?
- **Rule 3**: If there was an error, lint failure, warning, or
  regression — did you fix it and prove the fix? Or did you ignore it?
- **Rule 4**: Did you create a duplicate function or variant? One
  canonical implementation per concern.
- **Rule 5**: Did you introduce a type cheat? (`# type: ignore` without
  reason, `-> Any`, mocks in source).
- **Rule 6**: Did you leave old code behind? (`_v2`, `_new`, `_old`,
  commented-out blocks, `if False:` guards).
- **Rule 7**: Did you research before implementing PyCardano, CIP-1854,
  CIP-8, CIP-30, CIP-5, Ogmios, or Kupo logic?
- **Rule 8**: Did you expose any credentials? (mnemonics, private keys,
  passwords, API keys).
- **Rule 9**: Did you hardcode the network? (must use `current_network`
  or `SELECTED_NETWORK`).
- **Rule 10**: Did you use recursive prompts? (must use `while` loops).
- **Rule 12**: Did you run `mypy CardanoInterface.py` and
  `ruff check CardanoInterface.py` before handoff? Both must pass clean.
- **Rule 13**: Did you fix adjacent cheats while editing?
- **Rule 14**: Are security operations read-only unless explicitly
  authorized?

**If no:** Fix the violation before delivering. Do not deliver non-compliant
work with a note that it needs cleanup later. Later does not happen.

## Verification is not "I wrote it so it works"

AGENTS.md rule 1: "Validate real behavior. Observe the actual result —
what the user sees, what the backend returns, what the chain confirms."

This means:
- You do not claim code works because you wrote it correctly.
- You do not claim code works because it "should" work.
- You do not claim code works because the logic looks right.
- You observe the actual result. You run the code. You check the output.
  You verify the behavior.

AGENTS.md rule 2: "No mocks, stubs, fakes, or placeholder implementations
in source."

This means:
- "It runs without errors" is not validation. Observe the real behavior.
- "It imports successfully" is not validation. Observe the real behavior.
- No `MagicMock`, no `unittest.mock.patch`, no `raise NotImplementedError`.

AGENTS.md rule 12: "Run linter before handoff."

This means:
- Before delivering any code change, you run `mypy CardanoInterface.py`
  AND `ruff check CardanoInterface.py` on the changed files.
- Zero errors on both. Not "mostly clean." Zero.
- If either reports errors, you fix them before delivering.

AGENTS.md rule 3: "Own every error."

This means:
- If you see an error during your work, you own it. You fix it. You do
  not ignore it and deliver anyway.
- If you cannot fix it, you document it. You do not silently pass it.

## The honest assessment

After answering the three questions, state your verdict plainly:

```
Quality gate: PASS
- Original request: [what was asked] → [how it was addressed]
- Grounded in: [specific source file, doc, or verified behavior]
- Project rules: [which rules checked, any tensions noted]
- Verification: [what you ran/observed to confirm real behavior]
```

or

```
Quality gate: FAIL — [which question failed and why]
Action taken: [what you did to fix it]
Verification: [what you ran/observed after fixing]
```

Deliver the assessment WITH the response, not instead of it. The user
does not need to see your internal audit in full, but the code/change
itself must be the product of having passed.

## What to do when you fail the gate

1. **Identify the specific gap.** Not "I need to do better" but "I did
   not address the CIP-1854 derivation path from the prompt" or "I
   assumed PyCardano handles X like cardano-cli does."

2. **Fix it in this turn.** Do not defer to a follow-up. If you genuinely
   cannot fix it (missing information, external dependency), state what is
   blocking and what the user must provide.

3. **Verify the fix for real.** Run the code. Check the output. Do not
   claim it works because you wrote it. Observe the actual result per
   AGENTS.md rule 1.

4. **One corrective action, then deliver.** The purpose of this gate is to
   catch bad output BEFORE it reaches the user, not to create a loop of
   self-critique. You assess, you fix the gap, you verify the fix, you
   deliver. If the fix itself needs research, do that research (using
   `research-before-guessing`), then deliver. Do not re-audit the fixed
   output — the fix IS the audit.

## On user pushback (trigger #3)

When the user rejects your output, this is what happens:

1. **Do not defend.** The user said it is wrong. It is wrong. Full stop.
2. **Re-enter the gate.** Load this skill. Answer the three questions
   again, this time with the user's correction as input.
3. **Fix the specific gap.** The user told you what is wrong. Fix that.
   Do not fix adjacent things. Do not "improve" unrelated aspects.
4. **Verify the fix.** Run the code. Check the output. Confirm the user's
   complaint is resolved.
5. **Deliver.** Do not ask "is this better?" You verified it. It is.

If the user pushes back a second time on the same issue, you have a
deeper problem — you are not understanding the request. In that case:
- Re-read the original prompt and ALL loaded context files from scratch
- Load `research-before-guessing` if the gap is in external behavior
- Deliver a corrected output, not a retry of the same approach

## Anti-patterns this gate exists to prevent

| Pattern | What it looks like | What to do instead |
|---------|-------------------|-------------------|
| **Adjacent answer** | User asked about multisig, you explained single-sig | Re-read the prompt. Answer what was asked. |
| **Assumed compliance** | "This should work with PyCardano" without checking | Read AGENTS.md / context. Verify against reality. |
| **Partial delivery** | "Here's the first part, I'll do the rest later" | Complete the full request or explain what blocks you. |
| **Unsourced claim** | "PyCardano handles this via TransactionBuilder" without reading the source | Read the actual code. Cite the file and line. |
| **Rule violation with caveat** | "This uses a mock but can be replaced later" | Do not deliver rule violations. Fix now. |
| **Infinite self-critique** | "Let me re-examine my response... and again..." | Assess once, fix, verify, deliver. One pass. |
| **Memory-based coding** | Writing code from recall of PyCardano APIs | Load the docs. Read them. Code from what they say. |
| **Loop-as-avoidance** | Failing the gate repeatedly to dodge delivering an answer | Break out. Deliver. The loop IS the failure. |
| **Claiming validity** | "I wrote it correctly" without running/observing the result | Run the code. Check the output. Observe real behavior. |
| **Ignoring lint** | Delivering code without running mypy and ruff | Run both. Zero errors. Fix before handoff. |
| **Ignoring errors** | Saw a traceback during work, delivered anyway | Own every error per AGENTS.md rule 3. Fix or record. |

## Integration with other skills

- If you fail question 2 (grounding), load `research-before-guessing`
  before retrying.
- If you fail question 3 (project rules), check which skill applies:
  `no-type-cheats` for typing escapes, `no-legacy-code` for dead code.
- If you fail question 1 (original request), the problem is not a skill
  gap — it is a comprehension gap. Re-read the prompt slowly and address
  every clause.
