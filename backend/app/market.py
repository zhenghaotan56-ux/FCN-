"""Fetch, validate and freeze daily closing data before pricing."""

import csv
from datetime import date, datetime, timedelta, timezone
import json
from math import isfinite
from pathlib import Path

import numpy as np
import requests

from .terms import (LOOKBACKS, ROOT, InputError, active_session, add_months, calendar, dates_between,
                    latest_closed_session, month_choice, resolve_instrument)


class MarketUnavailable(RuntimeError):
    pass


def fixture(instrument_id):
    with (ROOT / "demo/daily_prices.csv").open(encoding="utf-8", newline="") as handle:
        return [row for row in csv.DictReader(handle) if row["instrument_id"] == instrument_id]


def openbb_daily(instrument, start, end):
    try:
        from openbb import obb
    except ImportError as error:
        raise MarketUnavailable("请用 uv sync --extra openbb 安装 OpenBB 适配器依赖。") from error

    def closes(adjustment):
        result = obb.equity.price.historical(
            symbol=instrument["provider_symbol"], provider="yfinance", start_date=start.isoformat(),
            end_date=(end + timedelta(days=1)).isoformat(), interval="1d", adjustment=adjustment,
        ).to_df()
        return {str(day)[:10]: float(close) for day, close in result["close"].items()}

    try:
        raw = closes("splits_only")
        adjusted = closes("splits_and_dividends")
    except Exception as error:
        raise MarketUnavailable("OpenBB 暂未返回完整日线。请检查数据源、网络和运行环境。") from error
    return [
        {"date": day, "close": close, "adjusted_close": adjusted[day]}
        for day, close in raw.items() if day in adjusted
    ]


def vendor_data(config, instrument, start, end, active):
    url, token = config["VENDOR_URL"], config["VENDOR_TOKEN"]
    if not url or not url.startswith("https://") or not token:
        raise MarketUnavailable("授权行情接口需要 HTTPS 地址和服务端凭证。")
    try:
        params = {"instrument": instrument["id"], "start": start.isoformat(), "end": end.isoformat()}
        if active:
            params["quote"] = "realtime"
        response = requests.get(
            url, params=params,
            headers={"Authorization": f"Bearer {token}"}, timeout=(5, 12), allow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
        if (payload["currency"] != instrument["currency"] or payload["instrument_id"] != instrument["id"]
                or not isinstance(payload["rows"], list) or not isinstance(payload["source"], str)
                or not payload["source"].strip()):
            raise ValueError("Malformed vendor response")
        quote = payload.get("quote") if active else None
        if active and (not isinstance(quote, dict) or quote.get("realtime") is not True):
            raise MarketUnavailable("行情供应商未返回已标明实时的盘中价格。")
        name = payload.get("name")
        if isinstance(name, str) and name.strip():
            instrument = {**instrument, "name": name.strip()[:120]}
        return instrument, payload["rows"], payload["source"][:120], quote
    except (requests.RequestException, ValueError, KeyError, TypeError) as error:
        raise MarketUnavailable("授权行情接口未返回可用数据。") from error


def load_assumptions(path, instrument, as_of, *, text=None, spot=None, dividend_per_share=None):
    try:
        raw = json.loads(text) if text else json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if raw.get("as_of", as_of.isoformat()) != as_of.isoformat():
            raise ValueError("Assumption date mismatch")
        rate = float(raw["rates"][instrument["currency"]])
        dividends = raw.get("dividend_yields", {})
        source = raw["source"]
        if instrument["id"] in dividends:
            dividend = float(dividends[instrument["id"]])
        elif dividend_per_share is not None:
            dividend = float(dividend_per_share) / spot
            source += " · 股息率用长桥每股股息÷参考价估算"
        else:
            dividend = float(raw["default_dividend_yield"])
            source += " · 使用配置的默认股息率"
        if not (-0.15 <= rate <= 0.5 and 0 <= dividend <= 0.5 and isinstance(source, str) and source.strip()):
            raise ValueError("Invalid rate/dividend")
        return rate, dividend, source[:160]
    except (AttributeError, OSError, ValueError, KeyError, TypeError) as error:
        raise MarketUnavailable("缺少当期币种利率或标的股息输入，请检查服务端的市场假设配置。") from error


def intraday_price(quote, instrument, now, active):
    try:
        spot = float(quote["price"])
        stamped = datetime.fromisoformat(quote["timestamp"])
        if not isfinite(spot) or spot <= 0 or stamped.tzinfo is None:
            raise ValueError("Invalid live quote")
        if not -5 <= (now - stamped).total_seconds() <= 300 or active_session(instrument, stamped) != active:
            raise ValueError("Stale or out-of-session quote")
    except (KeyError, ValueError, TypeError, OverflowError) as error:
        raise MarketUnavailable("未取得 5 分钟内的正常时段成交价，请检查实时权限或稍后重试。") from error
    return spot, stamped.astimezone(calendar(instrument["calendar"]).tz).isoformat(timespec="seconds")


def market_snapshot(config, instrument_id, requested_date=None, *, lookback_months=None, now=None):
    instrument = resolve_instrument(instrument_id)
    identifier = instrument["id"]
    lookback = month_choice(lookback_months, LOOKBACKS, "波动率回看区间")
    observations = LOOKBACKS[lookback]
    mode = config["MARKET_MODE"]
    if mode == "fixture" and not instrument.get("demo"):
        raise InputError("离线演示支持腾讯和微软。")
    clock = now or datetime.now(timezone.utc)
    latest = date(2026, 11, 2) if mode == "fixture" else latest_closed_session(instrument, clock)
    try:
        as_of = date.fromisoformat(requested_date) if requested_date else latest
    except (TypeError, ValueError) as error:
        raise InputError("估值日须为 YYYY-MM-DD。") from error
    if as_of > latest or as_of < latest - timedelta(days=730):
        raise InputError("估值日须处于最近两年内，且不能晚于最近完整交易日。")
    if mode == "fixture" and as_of != latest:
        raise InputError("演示数据固定在 2026-11-02。")
    if not dates_between(instrument, as_of, as_of):
        raise InputError("请选择该市场的交易日。")

    active = active_session(instrument, clock) if mode in {"vendor", "longbridge"} and not requested_date else None
    start = add_months(as_of, -max(4, lookback + 1 + (lookback == 12)))
    quote, dividend_per_share = None, None
    if mode == "fixture":
        rows, source = fixture(identifier), "合成日线 · 2026-11-02 样例"
    elif mode == "openbb":
        rows, source = openbb_daily(instrument, start, as_of), "OpenBB 4.7.2 / Yahoo Finance · 内部研究"
    elif mode == "longbridge":
        from . import longbridge_feed

        if active and instrument["currency"] == "HKD" and not config.get("LONGBRIDGE_HK_REALTIME"):
            raise MarketUnavailable("港股盘中估值需要开通长桥 OpenAPI 港股实时权限，并在服务端启用。")
        instrument, rows, quote, dividend_per_share = longbridge_feed.fetch(instrument, start, as_of, active)
        source = longbridge_feed.SOURCE
    else:
        instrument, rows, source, quote = vendor_data(config, instrument, start, as_of, active)
    normalized = {}
    try:
        for row in rows:
            day = date.fromisoformat(row["date"])
            if not start <= day <= as_of:
                continue
            raw, adjusted = float(row["close"]), float(row["adjusted_close"])
            if day in normalized or not (isfinite(raw) and isfinite(adjusted) and min(raw, adjusted) > 0):
                raise ValueError("Invalid daily row")
            normalized[day] = {"date": day.isoformat(), "close": raw, "adjusted_close": adjusted}
    except (KeyError, ValueError, TypeError) as error:
        raise MarketUnavailable("行情包含重复日期或无效价格。") from error
    sessions = dates_between(instrument, start, as_of)
    needed = observations + 1
    if len(sessions) < needed or not all(day in normalized for day in sessions[-needed:]):
        raise MarketUnavailable(f"最近 {needed} 个完整交易日的收盘价不齐，请尝试缩短波动率回看区间。")
    ordered = [normalized[day] for day in sessions if day in normalized]
    returns = np.diff(np.log([row["adjusted_close"] for row in ordered[-needed:]]))
    vol = float(returns.std(ddof=1) * np.sqrt(252))
    if not 0 <= vol <= 5:
        raise MarketUnavailable("历史波动率超出模型的支持范围。")
    fetched = now or datetime.now(timezone.utc)
    reference, price_at = ordered[-1]["close"], as_of.isoformat()
    if active:
        reference, price_at = intraday_price(quote, instrument, fetched, active)
    day = (active or as_of).isoformat()
    assumptions = ROOT / "demo/market_assumptions.json" if mode == "fixture" else config.get("ASSUMPTIONS_FILE")
    rate, dividend, input_source = load_assumptions(
        assumptions, instrument, date.fromisoformat(day),
        text=None if mode == "fixture" else config.get("ASSUMPTIONS_JSON"), spot=reference,
        dividend_per_share=dividend_per_share,
    )
    return {
        "instrument_id": instrument["id"], "instrument": instrument, "currency": instrument["currency"],
        "as_of": day, "fetched_at": fetched.isoformat(timespec="seconds"),
        "source": source, "mode": mode, "close": ordered[-1]["close"], "reference_price": reference,
        "price_kind": "realtime" if active else "fixture" if mode == "fixture" else "close", "price_at": price_at,
        "history_end": as_of.isoformat(), "lookback_months": lookback, "volatility": vol,
        "volatility_observations": len(returns), "rate": rate, "dividend_yield": dividend,
        "assumption_source": input_source, "rows": ordered,
    }
