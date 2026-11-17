"""Fetch, validate and freeze daily closing data before pricing."""

import csv
from datetime import date, datetime, timedelta, timezone
import json
from math import isfinite
from pathlib import Path

import numpy as np
import requests

from .terms import BY_ID, ROOT, InputError, add_months, dates_between, latest_closed_session


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


def vendor_daily(config, instrument, start, end):
    url, token = config["VENDOR_URL"], config["VENDOR_TOKEN"]
    if not url or not url.startswith("https://") or not token:
        raise MarketUnavailable("授权行情接口需要 HTTPS 地址和服务端凭证。")
    try:
        response = requests.get(
            url, params={"instrument": instrument["id"], "start": start.isoformat(), "end": end.isoformat()},
            headers={"Authorization": f"Bearer {token}"}, timeout=(5, 12), allow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
        if (payload["currency"] != instrument["currency"] or payload["instrument_id"] != instrument["id"]
                or not isinstance(payload["rows"], list) or not str(payload["source"]).strip()):
            raise ValueError("Malformed vendor response")
        return payload["rows"], str(payload["source"])[:120]
    except (requests.RequestException, ValueError, KeyError, TypeError) as error:
        raise MarketUnavailable("授权行情接口未返回可用数据。") from error


def load_assumptions(path, instrument, as_of):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if raw["as_of"] != as_of.isoformat():
            raise ValueError("Assumption date mismatch")
        rate = float(raw["rates"][instrument["currency"]])
        dividend = float(raw["dividend_yields"][instrument["id"]])
        source = raw["source"]
        if not (-0.15 <= rate <= 0.5 and 0 <= dividend <= 0.5 and isinstance(source, str) and source.strip()):
            raise ValueError("Invalid rate/dividend")
        return rate, dividend, source[:120]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise MarketUnavailable("缺少同估值日、同币种的利率或该标的预期股息假设。") from error


def market_snapshot(config, instrument_id, requested_date=None):
    instrument = BY_ID.get(instrument_id)
    if instrument is None:
        raise InputError("请选择支持的港股或美股代码。")
    mode = config["MARKET_MODE"]
    if mode == "fixture" and not instrument.get("demo"):
        raise InputError("离线演示支持腾讯和微软。")
    latest = date(2026, 11, 2) if mode == "fixture" else latest_closed_session(instrument)
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

    assumptions = ROOT / "demo/market_assumptions.json" if mode == "fixture" else config["ASSUMPTIONS_FILE"]
    rate, dividend, input_source = load_assumptions(assumptions, instrument, as_of)
    start = add_months(as_of, -4)
    if mode == "fixture":
        rows, source = fixture(instrument_id), "合成日线 · 2026-11-02 样例"
    elif mode == "openbb":
        rows, source = openbb_daily(instrument, start, as_of), "OpenBB 4.7.2 / Yahoo Finance · 内部研究"
    else:
        rows, source = vendor_daily(config, instrument, start, as_of)
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
    if len(sessions) < 61 or not all(day in normalized for day in sessions[-61:]):
        raise MarketUnavailable("最近 61 个完整交易日的收盘价不齐，无法计算 60 笔日收益。")
    ordered = [normalized[day] for day in sessions if day in normalized]
    returns = np.diff(np.log([row["adjusted_close"] for row in ordered[-61:]]))
    vol = float(returns.std(ddof=1) * np.sqrt(252))
    if not 0 <= vol <= 5:
        raise MarketUnavailable("历史波动率超出首版模型的支持范围。")
    return {
        "instrument_id": instrument_id, "currency": instrument["currency"],
        "as_of": as_of.isoformat(), "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source, "mode": mode, "close": ordered[-1]["close"], "volatility": vol,
        "volatility_observations": len(returns), "rate": rate, "dividend_yield": dividend,
        "assumption_source": input_source, "rows": ordered,
    }
