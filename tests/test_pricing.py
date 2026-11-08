from __future__ import annotations

import datetime as dt

import pytest
from conftest import ROOT

from agenttrace.pricing import PriceTable, Usage

T = PriceTable.load(ROOT / "prices.yaml")


def test_basic_cost():
    assert T.cost("small-model", Usage(1_000_000, 1_000_000)) == pytest.approx(0.75)


def test_cached_tokens_are_billed_at_the_cache_rate():
    # 10,000 input of which 6,000 read from cache, 500 output, at the old large-model rate
    u = Usage(10_000, 500, cache_read_tokens=6_000)
    expected = (4_000 * 3.00 + 6_000 * 0.30 + 500 * 15.00) / 1e6
    assert T.cost("large-model", u, dt.date(2026, 9, 1)) == pytest.approx(expected)


def test_price_in_effect_on_the_date():
    u = Usage(1_000_000, 0)
    assert T.cost("large-model", u, dt.date(2026, 9, 30)) == pytest.approx(3.00)
    assert T.cost("large-model", u, dt.date(2026, 10, 1)) == pytest.approx(2.50)


def test_globs_and_unknown_models():
    assert T.cost("embed-v3", Usage(1_000_000, 0)) == pytest.approx(0.02)
    assert T.cost("someone-elses-model", Usage(10, 10)) is None


def test_exact_name_beats_a_glob():
    t = PriceTable.model_validate({"prices": [{"model": "m-*", "input": 1, "output": 1}, {"model": "m-1", "input": 5, "output": 5}]})
    assert t.cost("m-1", Usage(1_000_000, 0)) == pytest.approx(5)


def test_unknown_keys_are_refused():
    with pytest.raises(ValueError):
        PriceTable.model_validate({"prices": [{"model": "x", "input": 1, "output": 1, "ouput": 2}]})
