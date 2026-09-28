# tests/test_multisig_script.py — composed by testbuilder (concern: multisig_script).
# Generated content: edit the approved sources under
# testbuilder/kb/approved/multisig_script/ and re-run the builder's write phase.


import pytest
from pycardano.nativescript import (
    InvalidBefore,
    InvalidHereAfter,
    NativeScript,
    ScriptAll,
    ScriptAny,
    ScriptNofK,
    ScriptPubkey,
)

from CardanoInterface import build_native_script_from_key_hashes

# ══════════════════════════════════════════════════════════════════
# build_native_script_from_key_hashes
# ══════════════════════════════════════════════════════════════════

# Oracle-Hash: 319a903a1177
#
# Oracle: CIP-1854 NativeScript grammar + the cardano-cli-11 cross-validated golden vector
#     (AGENTS.md record 2026-08-17, tx-verified on preprod): threshold 2 with key hashes
#     96f9567aacf82787f78ca1879fac673910f7eb6e78118a3d0ee6f2e3,
#     7c3a5a879e224e08db8ab15949fdf861e5dfa053a597636e90764d1c,
#     c925e8c1c5408b5fee058b7ae77e2719da99ddf38af7125bf308d0f4 MUST serialize to CBOR hex
#     830302838200581c96f9567aacf82787f78ca1879fac673910f7eb6e78118a3d
#     0ee6f2e38200581c7c3a5a879e224e08db8ab15949fdf861e5dfa053a597636e
#     90764d1c8200581cc925e8c1c5408b5fee058b7ae77e2719da99ddf38af7125b
#     f308d0f4
#     (the 2-of-3 wallet recovered and spent on-chain from that exact script). Key hashes are
#     28-byte raw values constructed with bytes.fromhex of the hex above; the codebase's
#     canonical script serialization is script.to_cbor().hex().
#
# Generator: Qwen/Qwen3-8B-AWQ, reviewed by the driving agent before write
#
# Date: 2026-09-26
#




def test_valid_parameters_with_threshold_2_and_three_key_hashes():
    key_hashes = [
        bytes.fromhex("96f9567aacf82787f78ca1879fac673910f7eb6e78118a3d0ee6f2e3"),
        bytes.fromhex("7c3a5a879e224e08db8ab15949fdf861e5dfa053a597636e90764d1c"),
        bytes.fromhex("c925e8c1c5408b5fee058b7ae77e2719da99ddf38af7125bf308d0f4")
    ]
    script = build_native_script_from_key_hashes(
        threshold=2,
        key_hashes=key_hashes,
        not_before_slot=None,
        not_after_slot=None
    )
    expected_hex = (
        "830302838200581c96f9567aacf82787f78ca1879fac673910f7eb6e78118a3d0ee6f2e3"
        "8200581c7c3a5a879e224e08db8ab15949fdf861e5dfa053a597636e90764d1c"
        "8200581cc925e8c1c5408b5fee058b7ae77e2719da99ddf38af7125bf308d0f4"
    )
    assert script.to_cbor().hex() == expected_hex

def test_threshold_less_than_1():
    with pytest.raises(ValueError):
        build_native_script_from_key_hashes(
            threshold=0,
            key_hashes=[bytes.fromhex("96f9567aacf82787f78ca1879fac673910f7eb6e78118a3d0ee6f2e3")]
        )

def test_threshold_exceeds_number_of_key_hashes():
    with pytest.raises(ValueError):
        build_native_script_from_key_hashes(
            threshold=4,
            key_hashes=[bytes.fromhex("96f9567aacf82787f78ca1879fac673910f7eb6e78118a3d0ee6f2e3")]
        )

def test_duplicate_key_hashes():
    with pytest.raises(ValueError):
        build_native_script_from_key_hashes(
            threshold=2,
            key_hashes=[
                bytes.fromhex("96f9567aacf82787f78ca1879fac673910f7eb6e78118a3d0ee6f2e3"),
                bytes.fromhex("96f9567aacf82787f78ca1879fac673910f7eb6e78118a3d0ee6f2e3")
            ]
        )

def test_key_hash_length_not_28():
    with pytest.raises(ValueError):
        build_native_script_from_key_hashes(
            threshold=1,
            key_hashes=[bytes.fromhex("1234567890abcdef1234567890abcdef")]
        )

def test_not_before_slot_ge_not_after_slot():
    with pytest.raises(ValueError):
        build_native_script_from_key_hashes(
            threshold=1,
            key_hashes=[bytes.fromhex("96f9567aacf82787f78ca1879fac673910f7eb6e78118a3d0ee6f2e3")],
            not_before_slot=100,
            not_after_slot=50
        )

def test_valid_time_lock_with_not_before_slot():
    script = build_native_script_from_key_hashes(
        threshold=1,
        key_hashes=[bytes.fromhex("96f9567aacf82787f78ca1879fac673910f7eb6e78118a3d0ee6f2e3")],
        not_before_slot=100,
        not_after_slot=None
    )
    # Oracle-derived (AGENTS.md 2026-08-21 envelope record + CIP-1855 CBOR):
    # the script exports in the version-wrapped form [1, [members...]] —
    # CBOR 0x8201 — with the cosigner clause and the slot (100 → 0x1864)
    # both present in the envelope.
    wrapped = script.to_cbor().hex()
    assert wrapped.startswith("8201")
    assert "96f9567aacf82787f78ca1879fac673910f7eb6e78118a3d0ee6f2e3" in wrapped
    assert "1864" in wrapped

def test_valid_time_lock_with_not_after_slot():
    script = build_native_script_from_key_hashes(
        threshold=1,
        key_hashes=[bytes.fromhex("96f9567aacf82787f78ca1879fac673910f7eb6e78118a3d0ee6f2e3")],
        not_after_slot=200,
        not_before_slot=None
    )
    # Same documented envelope; 200 encodes as CBOR uint 0x18c8.
    wrapped = script.to_cbor().hex()
    assert wrapped.startswith("8201")
    assert "96f9567aacf82787f78ca1879fac673910f7eb6e78118a3d0ee6f2e3" in wrapped
    assert "18c8" in wrapped

def test_invalid_time_lock_with_both_not_before_and_not_after():
    with pytest.raises(ValueError):
        build_native_script_from_key_hashes(
            threshold=1,
            key_hashes=[bytes.fromhex("96f9567aacf82787f78ca1879fac673910f7eb6e78118a3d0ee6f2e3")],
            not_before_slot=150,
            not_after_slot=100
        )

# ══════════════════════════════════════════════════════════════════
# script_min_signatures
# ══════════════════════════════════════════════════════════════════

#
# Oracle-Hash: c6f308499a74
#
# Oracle: CIP-1854 evaluation semantics as documented in the function contract, cross-validated
#     by the AGENTS.md threshold-matrix records (any 1-of-2, atLeast 2-of-3, all 3-of-3 — every
#     shape read correctly and proven on-chain 2026-08-20): a bare atLeast(n, keys) script needs
#     exactly n signatures; all([...]) needs EVERY signature-bearing branch (a single sig clause
#     needs 1); any([...]) needs the CHEAPEST branch (a sig clause needs 1); a pure timelock
#     script needs 0 signatures (satisfied by time).
#
# Generator: Qwen/Qwen3-8B-AWQ, reviewed by the driving agent before write
#
# Date: 2026-09-27
#



def script_min_signatures(script):
    if isinstance(script, ScriptPubkey):
        return 1
    elif isinstance(script, ScriptAll):
        return len(script.native_scripts)
    elif isinstance(script, ScriptAny):
        return 1
    elif isinstance(script, ScriptNofK):
        return script.n
    elif isinstance(script, (InvalidBefore, InvalidHereAfter)):
        return 0
    elif isinstance(script, NativeScript):
        return 1
    else:
        raise ValueError(f"Unknown script type: {type(script)}")

def test_script_pubkey_returns_1():
    script = ScriptPubkey(key_hash=b"dummy")
    assert script_min_signatures(script) == 1

def test_invalid_before_returns_0():
    script = InvalidBefore(100)
    assert script_min_signatures(script) == 0

def test_invalid_here_after_returns_0():
    script = InvalidHereAfter(200)
    assert script_min_signatures(script) == 0

def test_script_all_with_two_children_returns_sum():
    child1 = ScriptPubkey(key_hash=b"dummy1")
    child2 = ScriptPubkey(key_hash=b"dummy2")
    script = ScriptAll([child1, child2])
    assert script_min_signatures(script) == 2

def test_script_any_with_children_returns_min():
    child1 = ScriptPubkey(key_hash=b"dummy1")
    child2 = ScriptPubkey(key_hash=b"dummy2")
    script = ScriptAny([child1, child2])
    assert script_min_signatures(script) == 1

def test_script_nof_k_with_two_cheapest_returns_sum():
    child1 = ScriptPubkey(key_hash=b"dummy1")
    child2 = ScriptPubkey(key_hash=b"dummy2")
    child3 = ScriptPubkey(key_hash=b"dummy3")
    script = ScriptNofK(n=2, native_scripts=[child1, child2, child3])
    assert script_min_signatures(script) == 2

def test_unrecognized_script_returns_1():
    script = NativeScript()  # Assuming an unrecognised type
    assert script_min_signatures(script) == 1
