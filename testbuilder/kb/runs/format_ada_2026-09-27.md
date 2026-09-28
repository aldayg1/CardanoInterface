---
type: Test Run Report
title: testbuilder — format_ada (2026-09-27)
timestamp: '2026-09-27T00:23:48+00:00'
resource:
- CardanoInterface.py#format_ada
tags:
- testing
- money
- format_ada
status: researched
okf_version: '1.0'
---

# testbuilder run — format_ada

- **Concern:** money
- **Model:** Qwen/Qwen3-8B-AWQ
- **Refinement rounds to mechanical-clean:** 1
- **Verification:** pass

## Oracle

AGENTS.md rule 9b (proven 2026-08-14 float-math fix): ADA is a display representation only; format_ada must render integer lovelace using pure integer arithmetic, never float division. Public behavior exercised across all E2E records: 181717 lovelace renders as 0.181717 ADA; 1000001 as 1.000001 ADA. Scaling: 999999999999 lovelace renders as 999999.999999 ADA.

## Assessment notes

(none)
