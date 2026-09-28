---
type: Test Run Report
title: testbuilder — CardanoInterface/ada_to_lovelace, build_native_script_from_key_hashes, calculate_threshold (2026-09-26)
timestamp: '2026-09-26T23:23:47+00:00'
resource:
- /home/dd/dev/CardanoInterface
tags:
- testing
- cardanointerface
- ada_to_lovelace, build_native_script_from_key_hashes, calculate_threshold
status: researched
okf_version: '1.0'
---

# testbuilder run — ada_to_lovelace, build_native_script_from_key_hashes, calculate_threshold

- **Repo:** `/home/dd/dev/CardanoInterface`
- **Model:** Qwen/Qwen3-8B-AWQ
- **Refinement rounds to mechanical-clean:** 1
- **Verification:** pass

## Oracle

AGENTS.md rule 9b + the function's documented contract (proven past failure 2026-08-14): exact Decimal string parsing scaled by 10^6; binary floats banned because int(float('0.000249')*1_000_000) yields 248 not 249 and int(float('1.000001')*1_000_000) yields 1000000 not 1000001. Required behavior: '1.000001'->1000001; '0.000249'->249; '2'->2000000. Raises ValueError for malformed input, negative, zero, and more than six decimal places (lovelace is the smallest unit). ; CIP-1854 NativeScript grammar + the cardano-cli-11 cross-validated golden vector (AGENTS.md record 2026-08-17, tx-verified on preprod): threshold 2 with key hashes 96f9567aacf82787f78ca1879fac673910f7eb6e78118a3d0ee6f2e3, 7c3a5a879e224e08db8ab15949fdf861e5dfa053a597636e90764d1c, c925e8c1c5408b5fee058b7ae77e2719da99ddf38af7125bf308d0f4 MUST serialize to CBOR hex 830302838200581c96f9567aacf82787f78ca1879fac673910f7eb6e78118a3d0ee6f2e38200581c7c3a5a879e224e08db8ab15949fdf861e5dfa053a597636e90764d1c8200581cc925e8c1c5408b5fee058b7ae77e2719da99ddf38af7125bf308d0f4 (the 2-of-3 wallet recovered and spent on-chain from that exact script). Key hashes are 28-byte raw values constructed with bytes.fromhex of the hex above; the codebase's canonical script serialization is script.to_cbor().hex(). ; MULTISIG_SPEC operating model + the documented standard multisig floor convention in the function contract: P% means AT LEAST P% of cosigners must approve, computed by floor. Documented examples: 67% of 3 = 2 (2/3=66.7%); 50% of 6 = 3; 100% of any N = N.

## Assessment notes

Quality-upgrade validation batch (API grounding + lessons + derived-case analysis active). Compared to the pre-upgrade baseline: build_native_script_from_key_hashes went from 3 failed refine rounds with hex-literal bytes bugs and invented error messages to bytes.fromhex throughout, real grounded APIs, zero invented messages, and the golden-vector byte test passing (2 timelock tests needed assessment: model mixed pycardano objects into expected primitive structures — corrected to the documented envelope-byte assertions). calculate_threshold: 15 tests with broad rule-derived coverage; 1 invalidly derived case (1% of 1 asserted 0 — a threshold of 0 signatures is not a valid script state; floor bounded at 1) and source-copied error-message matches stripped per rule 3. ada_to_lovelace: 10/10 approved as generated, zero edits. Remaining model failure modes recorded as lessons.
