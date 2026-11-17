from datetime import datetime, timezone
import json

import numpy as np
import pytest

from backend.app import market, pricing, terms


def fixture(name):
    return json.loads((terms.ROOT / "demo" / name).read_text(encoding="utf-8"))


@pytest.fixture
def snapshot():
    return market.market_snapshot({"MARKET_MODE": "fixture"}, "XHKG:00700")


def test_no_knockout_recovers_analytic_benchmark(snapshot):
    contract = terms.build_terms(fixture("terms_no_ko.json"), snapshot)
    result = pricing.price(contract, snapshot)
    assert snapshot["volatility"] == pytest.approx(.28583909677830954, abs=1e-8)
    assert result["annual_coupon"] == pytest.approx(fixture("expected_results.json")["annual_coupon"]["no_ko"], abs=2e-7)
    assert result["full_term_coupon"] == pytest.approx(result["annual_coupon"] * 92 / 365)
    assert result["standard_error"] is None


def test_monthly_observation_and_path_payoffs(snapshot):
    contract = terms.build_terms(fixture("terms_monthly_ko.json"), snapshot)
    cases = fixture("payoff_cases.json")
    assert [e["observation"] for e in contract["events"]] == cases["observations"]
    assert contract["strike"] == 333.37
    assert contract["ko"] == 411.81
    paths = np.array([case["prices"] for case in cases["cases"]])
    principal, endings = pricing.cash_flow_legs(contract, paths)
    for case, fraction, end in zip(cases["cases"], principal, endings, strict=True):
        assert end == case["ending_event"]
        outcome = pricing.scenario(contract, contract["events"][end], cases["annual_coupon"], "test", fraction)
        assert outcome["principal_value"] == pytest.approx(case["principal_value"], abs=.006)
        assert outcome["coupon"] == pytest.approx(case["coupon"], abs=.006)


def test_monthly_solver_is_reproducible(snapshot):
    contract = terms.build_terms(fixture("terms_monthly_ko.json"), snapshot)
    result = pricing.price(contract, snapshot)
    expected = fixture("expected_results.json")
    assert result["annual_coupon"] == pytest.approx(expected["annual_coupon"]["monthly_ko"], abs=3e-7)
    assert result["standard_error"] == pytest.approx(expected["monthly_ko_standard_error"], abs=1e-7)
    assert result["range"][0] <= result["annual_coupon"] <= result["range"][1]
    assert result == pricing.price(contract, snapshot)


def test_latest_complete_market_close():
    before_close = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
    after_close = datetime(2026, 11, 2, 22, 0, tzinfo=timezone.utc)
    stock = terms.BY_ID["XNAS:MSFT"]
    assert terms.latest_closed_session(stock, before_close).isoformat() == "2026-10-30"
    assert terms.latest_closed_session(stock, after_close).isoformat() == "2026-11-02"


def test_rejects_incomplete_market_data(monkeypatch):
    rows = market.fixture("XHKG:00700")[:-1]
    monkeypatch.setattr(market, "fixture", lambda _: rows)
    with pytest.raises(market.MarketUnavailable, match="61"):
        market.market_snapshot({"MARKET_MODE": "fixture"}, "XHKG:00700")
