from datetime import date, datetime, timezone
import json
from types import SimpleNamespace

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


def test_active_sessions_respect_hong_kong_lunch_and_us_early_close():
    hk = terms.resolve_instrument("700.HK")
    assert terms.active_session(hk, datetime(2026, 11, 3, 2, tzinfo=timezone.utc)) == date(2026, 11, 3)
    assert terms.active_session(hk, datetime(2026, 11, 3, 4, 30, tzinfo=timezone.utc)) is None
    assert terms.active_session(hk, datetime(2026, 11, 3, 5, 30, tzinfo=timezone.utc)) == date(2026, 11, 3)
    assert terms.latest_closed_session(hk, datetime(2026, 11, 3, 8, 5, tzinfo=timezone.utc)) == date(2026, 11, 2)
    assert terms.latest_closed_session(hk, datetime(2026, 11, 3, 8, 10, tzinfo=timezone.utc)) == date(2026, 11, 3)
    us = terms.resolve_instrument("AAPL")
    assert terms.active_session(us, datetime(2026, 11, 26, 17, tzinfo=timezone.utc)) is None
    assert terms.active_session(us, datetime(2026, 11, 27, 17, tzinfo=timezone.utc)) == date(2026, 11, 27)
    assert terms.active_session(us, datetime(2026, 11, 27, 18, tzinfo=timezone.utc)) is None


def test_live_quote_changes_reference_without_using_partial_daily_bar(monkeypatch):
    # The target is outside the original whitelist. Reuse known synthetic bars for a network-free test.
    rows = market.fixture("XHKG:00700")
    rows.append({"date": "2026-11-03", "close": 9000, "adjusted_close": 9000})
    clock = datetime(2026, 11, 3, 2, tzinfo=timezone.utc)
    quote = {"price": 510, "timestamp": "2026-11-03T01:59:00+00:00"}
    config = {"MARKET_MODE": "vendor", "ASSUMPTIONS_JSON": json.dumps({
        "rates": {"HKD": .03}, "default_dividend_yield": .01, "source": "测试假设",
    })}

    def vendor_data(_, instrument, start, end, active):
        assert instrument["id"] == "XHKG:00005"
        assert end == date(2026, 11, 2)
        return {**instrument, "name": "测试股票"}, rows, "Test Feed", quote if active else None

    monkeypatch.setattr(market, "vendor_data", vendor_data)
    live = market.market_snapshot(config, "5.HK", now=clock)
    assert live["reference_price"] == 510 and live["close"] == 392.20
    assert live["history_end"] == "2026-11-02" and live["as_of"] == "2026-11-03"
    assert live["price_kind"] == "realtime" and live["price_at"] == "2026-11-03T09:59:00+08:00"
    assert live["volatility"] == pytest.approx(.28583909677830954, abs=1e-8)
    contract = terms.build_terms({**fixture("terms_monthly_ko.json"), "tenor_months": 6}, live)
    assert contract["strike"] == 433.50 and contract["issue_date"] == "2026-11-03"
    assert contract["events"][-1]["can_call"] is False
    quote["timestamp"] = "2026-11-03T01:40:00+00:00"
    with pytest.raises(market.MarketUnavailable, match="5 分钟"):
        market.market_snapshot(config, "00005", now=clock)
    lunch = market.market_snapshot(config, "00005", now=datetime(2026, 11, 3, 4, 30, tzinfo=timezone.utc))
    assert lunch["reference_price"] == 392.20 and lunch["price_kind"] == "close"


def test_longbridge_new_stock_resolves_and_keeps_close_separate_from_live_trade(monkeypatch):
    sdk = pytest.importorskip("longbridge.openapi")
    from backend.app import longbridge_feed
    from zoneinfo import ZoneInfo

    us = terms.resolve_instrument("AAPL")
    ny = ZoneInfo("America/New_York")
    clock = datetime(2026, 11, 3, 10, tzinfo=ny)
    trade_time = datetime(2026, 11, 3, 9, 59, tzinfo=ny)

    class Feed:
        def static_info(self, symbols):
            assert symbols == ["AAPL.US"]
            return [SimpleNamespace(symbol=symbols[0], board=sdk.SecurityBoard.USMain, currency="USD",
                                    exchange="NASD", name_cn="苹果", name_en="Apple Inc.", dividend_yield=1)]

        def quote(self, symbols):
            return [SimpleNamespace(symbol=symbols[0], trade_status=sdk.TradeStatus.Normal,
                                    last_done=250, timestamp=trade_time)]

        def history_candlesticks_by_date(self, symbol, period, adjustment, start, end, session):
            assert symbol == "AAPL.US" and period == sdk.Period.Day and session == sdk.TradeSessions.Intraday
            assert end == date(2026, 11, 2)
            price = 240 if adjustment == sdk.AdjustType.NoAdjust else 239
            return [SimpleNamespace(timestamp=datetime.combine(day, datetime.min.time(), tzinfo=ny), close=price)
                    for day in terms.dates_between(us, start, end)]

    monkeypatch.setattr(longbridge_feed, "quote_context", Feed)
    config = {"MARKET_MODE": "longbridge", "ASSUMPTIONS_JSON": '{"rates":{"USD":0.04},"source":"测试假设"}'}
    live = market.market_snapshot(config, "AAPL", now=clock)
    assert live["instrument"]["id"] == "XNAS:AAPL" and live["instrument"]["name"] == "苹果"
    assert live["close"] == 240 and live["reference_price"] == 250
    assert live["rows"][-1]["adjusted_close"] == 239
    assert live["dividend_yield"] == pytest.approx(.004)


def test_rejects_incomplete_market_data(monkeypatch):
    rows = market.fixture("XHKG:00700")[:-1]
    monkeypatch.setattr(market, "fixture", lambda _: rows)
    with pytest.raises(market.MarketUnavailable, match="61"):
        market.market_snapshot({"MARKET_MODE": "fixture"}, "XHKG:00700")
