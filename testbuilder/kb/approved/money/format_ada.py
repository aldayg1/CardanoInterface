# Oracle-Hash: ebe312e8bf35
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

from CardanoInterface import format_ada


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
