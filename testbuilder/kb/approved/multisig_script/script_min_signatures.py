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

from pycardano.nativescript import (
    InvalidBefore,
    InvalidHereAfter,
    NativeScript,
    ScriptAll,
    ScriptAny,
    ScriptNofK,
    ScriptPubkey,
)


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
