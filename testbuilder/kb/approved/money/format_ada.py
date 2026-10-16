# Oracle-Hash: ebe312e8bf35
#
# Oracle: AGENTS.md rule 9b (proven 2026-08-14 float-math fix): ADA is a display
#     representation only; format_ada must render integer lovelace using pure
#     integer arithmetic, never float division. Public behavior exercised across
#     all E2E records: 181717 lovelace renders as 0.181717 ADA; 1000001 as
#     1.000001 ADA. Scaling: 999999999999 lovelace renders as 999999.999999 ADA.
#
# Generator: Qwen/Qwen3-8B-AWQ, reviewed by the driving agent before write
#
# Date: 2026-09-27
#
from CardanoInterface import format_ada


def test_1_lovelace_formatted_as_0_000001():
    assert format_ada(1) == '0.000001'

def test_1000001_lovelace_formatted_as_1_000001():
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
