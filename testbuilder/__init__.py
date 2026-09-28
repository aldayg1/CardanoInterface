"""Test-builder: self-contained test generation pipeline for contributors.

This is the CardanoInterface port of the toolkit's canonical builder —
deliberately DEPENDENT ON NOTHING OUTSIDE THIS REPOSITORY: the repo venv,
langgraph (dev dependency), and the local vLLM endpoint are all it touches.
Contributors run it with `uv run python -m testbuilder ...` from a clean
clone; no external admin stack is required.

Enforcement model (the operator's test-suite standard, mechanically applied):
  inventory → plan → draft → refine → assess ⟶ INTERRUPT ⟶ write → verify → record
See graph.py for the full contract. Oracles are external by construction;
the knowledge base (testbuilder/kb/, committed) keeps provenance, lessons,
and run reports so every test's expectations stay traceable.
"""
