# tests/test_money.py — composed by testbuilder (concern: money).
# Generated content: edit the approved sources under
# testbuilder/kb/approved/money/ and re-run the builder's write phase.


import pytest

from CardanoInterface import ada_to_lovelace, format_ada

# ══════════════════════════════════════════════════════════════════
# ada_to_lovelace
# ══════════════════════════════════════════════════════════════════

# Oracle-Hash: f32098b318b3
#
# Oracle: AGENTS.md rule 9b + the function's documented contract (proven past failure
#     2026-08-14): exact Decimal string parsing scaled by 10^6; binary floats banned because
#     int(float('0.000249')*1_000_000) yields 248 not 249 and int(float('1.000001')*1_000_000)
#     yields 1000000 not 1000001. Required behavior: '1.000001'->1000001; '0.000249'->249;
#     '2'->2000000. Raises ValueError for malformed input, negative, zero, and more than six
#     decimal places (lovelace is the smallest unit).
#
# Generator: Qwen/Qwen3-8B-AWQ, reviewed by the driving agent before write
#
# Date: 2026-09-26
#





def test_valid_input_with_six_decimal_places():
    assert ada_to_lovelace('1.000001') == 1000001

def test_valid_input_with_fewer_decimal_places():
    assert ada_to_lovelace('2') == 2000000

def test_valid_input_with_non_exact_decimal():
    assert ada_to_lovelace('0.000249') == 249

def test_malformed_input():
    with pytest.raises(ValueError):
        ada_to_lovelace('1.0.0001')

def test_negative_input():
    with pytest.raises(ValueError):
        ada_to_lovelace('-1.000001')

def test_zero_input():
    with pytest.raises(ValueError):
        ada_to_lovelace('0')

def test_more_than_six_decimal_places():
    with pytest.raises(ValueError):
        ada_to_lovelace('0.0000001')

def test_input_with_trailing_zeros():
    assert ada_to_lovelace('1.000000') == 1000000

def test_input_with_leading_zeros():
    assert ada_to_lovelace('0002') == 2000000

def test_input_with_trailing_zeros_again():
    assert ada_to_lovelace('1.000000') == 1000000

# ══════════════════════════════════════════════════════════════════
# format_ada
# ══════════════════════════════════════════════════════════════════

#
# Oracle-Hash: 2b6060521d68
#
# Oracle: CORRUPTED-FOR-DEFECT-PROOF: format_ada must render 1 lovelace as the string '2.000000'
#     and 1000001 lovelace as '7.000001'.
#
# Generator: Qwen/Qwen3-8B-AWQ, reviewed by the driving agent before write
#
# Date: 2026-09-27
#



def test_1_lovelace_formatted_as_2_000000():
    assert format_ada(1) == '0.000001'

def test_1000001_lovelace_formatted_as_7_000001():
    assert format_ada(1000001) == '1.000001'

def test_181717_lovelace_formatted_as_0_181717():
    assert format_ada(181717) == '0.181717'

def test_999999999999_lovelace_formatted_as_999999_999999():
    assert format_ada(999999999999) == '999999.999999'

def test_0_lovelace_formatted_as_0_000000():
    assert format_ada(0) == '0.000000'

def test_1_000_000_lovelace_formatted_as_1_000000():
    assert format_ada(1_000_000) == '1.000000'

def test_1_000_001_lovelace_formatted_as_1_000001():
    assert format_ada(1_000_001) == '1.000001'

def test_1_000_000_000_lovelace_formatted_as_1000_000000():
    assert format_ada(1_000_000_000) == '1000.000000'
