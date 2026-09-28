# Oracle-Hash: 75b9afce535e
#
# Oracle: MULTISIG_SPEC operating model + the documented standard multisig floor convention in
#     the function contract: P% means AT LEAST P% of cosigners must approve, computed by floor.
#     Documented examples: 67% of 3 = 2 (2/3=66.7%); 50% of 6 = 3; 100% of any N = N.
#
# Generator: Qwen/Qwen3-8B-AWQ, reviewed by the driving agent before write
#
# Date: 2026-09-26
#

import pytest

from CardanoInterface import calculate_threshold


def test_invalid_percentage_0():
    with pytest.raises(ValueError):
        calculate_threshold(3, 0)

def test_invalid_percentage_101():
    with pytest.raises(ValueError):
        calculate_threshold(3, 101)

def test_invalid_total_cosigners_0():
    with pytest.raises(ValueError):
        calculate_threshold(0, 50)

def test_valid_case_67_percent_of_3():
    assert calculate_threshold(3, 67) == 2

def test_valid_case_50_percent_of_6():
    assert calculate_threshold(6, 50) == 3

def test_valid_case_100_percent_of_5():
    assert calculate_threshold(5, 100) == 5

def test_derived_case_50_percent_of_5():
    assert calculate_threshold(5, 50) == 2

def test_derived_case_50_percent_of_7():
    assert calculate_threshold(7, 50) == 3

def test_derived_case_67_percent_of_4():
    assert calculate_threshold(4, 67) == 2

def test_valid_case_100_percent_of_1():
    assert calculate_threshold(1, 100) == 1

def test_derived_case_1_percent_of_100():
    assert calculate_threshold(100, 1) == 1

def test_derived_case_1_percent_of_101():
    assert calculate_threshold(101, 1) == 1

def test_derived_case_1_percent_of_1():
    # A threshold of 0 would let the script spend with zero approvals —
    # "at least 1% of 1" therefore means 1 signature (floor bounded at 1).
    assert calculate_threshold(1, 1) == 1

def test_derived_case_100_percent_of_0():
    with pytest.raises(ValueError):
        calculate_threshold(0, 100)

def test_derived_case_0_percent_of_1():
    with pytest.raises(ValueError):
        calculate_threshold(1, 0)
