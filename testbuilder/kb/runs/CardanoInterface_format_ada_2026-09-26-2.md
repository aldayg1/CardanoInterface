---
type: Test Run Report
title: testbuilder — CardanoInterface/format_ada (2026-09-26)
timestamp: '2026-09-26T21:15:42+00:00'
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
- **Verification:** pass

## Oracle

AGENTS.md rule 9b (proven 2026-08-14 float-math fix): ADA is a display representation only; format_ada must render integer lovelace using pure integer arithmetic, never float division. Public behavior exercised across all E2E records: 181717 lovelace renders as 0.181717 ADA; 1000001 as 1.000001 ADA.

## Assessment notes

Second pass: first verify failed on import formatting after agent hand-edit (KB holds the fail record); ruff --fix applied to the reviewed draft, re-approved. Nodes behaved correctly: draft arrived mechanically clean, verify failed closed on the agent's own edit.
