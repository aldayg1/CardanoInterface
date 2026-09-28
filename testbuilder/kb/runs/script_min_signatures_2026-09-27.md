---
type: Test Run Report
title: testbuilder — script_min_signatures (2026-09-27)
timestamp: '2026-09-27T00:05:02+00:00'
resource:
- CardanoInterface.py#script_min_signatures
tags:
- testing
- multisig_script
- script_min_signatures
status: researched
okf_version: '1.0'
---

# testbuilder run — script_min_signatures

- **Concern:** multisig_script
- **Model:** Qwen/Qwen3-8B-AWQ
- **Refinement rounds to mechanical-clean:** 1
- **Verification:** fail

## Oracle

CIP-1854 evaluation semantics as documented in the function contract, cross-validated by the AGENTS.md threshold-matrix records (any 1-of-2, atLeast 2-of-3, all 3-of-3 — every shape read correctly and proven on-chain 2026-08-20): a bare atLeast(n, keys) script needs exactly n signatures; all([...]) needs EVERY signature-bearing branch (a single sig clause needs 1); any([...]) needs the CHEAPEST branch (a sig clause needs 1); a pure timelock script needs 0 signatures (satisfied by time). Tests may construct scripts with build_native_script_from_key_hashes (documented 2-of-3 golden-vector hashes) and pycardano constructors per the API grounding.

## Assessment notes

First repo-builder smoke. Model invented a mode-selector calling convention for build_native_script_from_key_hashes (passed 'pubkey'/'all' strings as threshold) — all 10 draft tests died on one root cause; revise-call emission also failed (no code block). Assessment rebuilt the drafts on the real signatures: 7/7 passing (atLeast/all/any/NofK/timelock/nested per CIP-1854 semantics + threshold matrix). New lesson recorded via KB composition: function-under-test signatures must come from grounding, not invention.
