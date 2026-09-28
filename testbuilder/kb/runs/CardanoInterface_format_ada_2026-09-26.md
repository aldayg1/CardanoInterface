---
type: Test Run Report
title: testbuilder — CardanoInterface/format_ada (2026-09-26)
timestamp: '2026-09-26T21:15:24+00:00'
resource:
- /home/dd/dev/CardanoInterface
tags:
- testing
- cardanointerface
- format_ada
status: researched
okf_version: '1.0'
---

# testbuilder run — format_ada

- **Repo:** `/home/dd/dev/CardanoInterface`
- **Model:** Qwen/Qwen3-8B-AWQ
- **Refinement rounds to mechanical-clean:** 1
- **Verification:** fail

## Oracle

AGENTS.md rule 9b (proven 2026-08-14 float-math fix): ADA is a display representation only; format_ada must render integer lovelace using pure integer arithmetic, never float division. Public behavior exercised across all E2E records: 181717 lovelace renders as 0.181717 ADA; 1000001 as 1.000001 ADA.

## Assessment notes

Phase-1 draft arrived mechanically clean after the multi-call chain (analyze/draft/critique + refine loop): authentic header, no fabricated provenance, no lint errors. Model conservatively covered only the two oracle-stated values; driving agent extended with three spec-derived integer-arithmetic cases (0, 1, 999999999999) justified by the oracle's 'pure integer arithmetic' clause, then approved.
