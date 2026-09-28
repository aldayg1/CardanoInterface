---
type: Test Run Report
title: testbuilder — script_min_signatures (2026-09-27)
timestamp: '2026-09-27T00:32:40+00:00'
resource:
- CardanoInterface.py#script_min_signatures
tags:
- testing
- misc
- script_min_signatures
status: researched
okf_version: '1.0'
---

# testbuilder run — script_min_signatures

- **Concern:** misc
- **Model:** Qwen/Qwen3-8B-AWQ + post-hoc agent validation
- **Refinement rounds to mechanical-clean:** 3
- **Verification:** pass

## Oracle

CIP-1854 evaluation semantics as documented in the function contract, cross-validated by the AGENTS.md threshold-matrix records (any 1-of-2, atLeast 2-of-3, all 3-of-3 — every shape read correctly and proven on-chain 2026-08-20): a bare atLeast(n, keys) script needs exactly n signatures; all([...]) needs EVERY signature-bearing branch (a single sig clause needs 1); any([...]) needs the CHEAPEST branch (a sig clause needs 1); a pure timelock script needs 0 signatures (satisfied by time).

## Assessment notes

Unattended refresh regenerated with 2 bare timelock constructor calls (InvalidBefore()/InvalidHereAfter() missing the slot); agent fixed to InvalidBefore(100)/InvalidHereAfter(200), recomposed, 16/16 concern-file tests green. Suite: 48 passed.
