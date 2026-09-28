"""Concern mapping: the suite mirrors the monolith's shape.

The app is one file; its test suite must not fragment into a file per symbol.
Every symbol belongs to one CONCERN, and tests/ holds one file per concern
(few files, forever). New symbols: add them to a concern here, or the mapper
assigns them to the module-level fallback.
"""
from __future__ import annotations

CONCERNS: dict[str, str] = {
    # Money is integer lovelace end to end (rule 9b)
    "ada_to_lovelace": "money",
    "format_ada": "money",
    # Native script identity and semantics
    "build_native_script_from_key_hashes": "multisig_script",
    "script_min_signatures": "multisig_script",
    "script_hash_from_script": "multisig_script",
    "script_to_address": "multisig_script",
    "script_timelocks": "multisig_script",
    "extract_key_hashes_from_script": "multisig_script",
    "native_script_candidate_readings": "multisig_script",
    "calculate_threshold": "thresholds",
    # Network guard doors (rule 9c)
    "_wallet_network_mismatch": "guards",
    "_refuse_wrong_network": "guards",
    "load_wallet_network": "guards",
    "network_to_pycardano": "guards",
}

FALLBACK_CONCERN = "misc"


def get_concern(symbol: str) -> str:
    return CONCERNS.get(symbol, FALLBACK_CONCERN)
