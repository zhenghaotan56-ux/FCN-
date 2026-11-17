"""Validate customer inputs and build the dated FCN schedule."""

from calendar import monthrange
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
import json
from pathlib import Path
import re

import pandas_market_calendars as mcal

ROOT = Path(__file__).resolve().parents[2]
TERMS_VERSION = "fcn-fixed-v2"
TENORS = tuple(range(1, 13))
LOOKBACKS = {1: 20, 3: 60, 6: 120, 12: 252}
INSTRUMENTS = json.loads((ROOT / "demo/instruments.json").read_text(encoding="utf-8"))
BY_ID = {item["id"]: item for item in INSTRUMENTS}


class InputError(ValueError):
    pass


@lru_cache(maxsize=2)
def calendar(name):
    return mcal.get_calendar(name)


def add_months(day, count):
    year, month = divmod(day.year * 12 + day.month - 1 + count, 12)
    month += 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def dates_between(instrument, start, end):
    schedule = calendar(instrument["calendar"]).schedule(start, end)
    return [index.date() for index in schedule.index]


def resolve_instrument(value):
    if not isinstance(value, str) or len(value) > 24:
        raise InputError("请输入港股或美股代码。")
    symbol = value.strip().upper()
    if symbol in BY_ID:
        return BY_ID[symbol]
    hk = re.fullmatch(r"(?:(?:XHKG:|HK[:.]))?(\d{1,5})(?:\.HK)?", symbol)
    if hk and int(hk[1]) > 0:
        ticker = hk[1].zfill(5)
        identifier = f"XHKG:{ticker}"
        return BY_ID.get(identifier, {
            "id": identifier, "ticker": ticker, "name": ticker, "currency": "HKD", "calendar": "HKEX",
            "provider_symbol": f"{int(ticker):04d}.HK",
        })
    us = re.fullmatch(r"(?:(US|XNAS|XNYS|XASE|ARCX|BATS|OTC):)?([A-Z][A-Z0-9]{0,9}(?:[.-][A-Z0-9]{1,4})?)(?:\.US)?", symbol)
    if us:
        exchange, ticker = us[1], us[2].replace("-", ".")
        identifier = f"{exchange or 'US'}:{ticker}"
        if not exchange or exchange == "US":
            existing = next((item for item in INSTRUMENTS if item["currency"] == "USD" and item["ticker"] == ticker), None)
            if existing:
                return existing
        return {
            "id": identifier, "ticker": ticker, "name": ticker, "currency": "USD",
            "calendar": "NASDAQ" if exchange == "XNAS" else "NYSE", "provider_symbol": ticker.replace(".", "-"),
        }
    raise InputError("请输入有效的港股或美股代码，例如 00700.HK 或 MSFT。")


def latest_closed_session(instrument, now=None):
    now = now or datetime.now(timezone.utc)
    local_day = now.astimezone(calendar(instrument["calendar"]).tz).date()
    schedule = calendar(instrument["calendar"]).schedule(local_day - timedelta(days=14), local_day)
    auction = timedelta(minutes=10 if instrument["calendar"] == "HKEX" else 0)
    closed = schedule[schedule.market_close + auction <= now]
    if closed.empty:
        raise InputError("无法确定最近完整交易日。")
    return closed.index[-1].date()


def active_session(instrument, now):
    exchange = calendar(instrument["calendar"])
    day = now.astimezone(exchange.tz).date()
    schedule = exchange.schedule(day, day)
    if schedule.empty:
        return None
    hours = schedule.iloc[0]
    if not hours.market_open <= now < hours.market_close:
        return None
    if "break_start" in hours and hours.break_start <= now < hours.break_end:
        return None
    return day


def month_choice(value, choices, label):
    if value is None:
        return 3
    if isinstance(value, bool) or str(value) not in {str(month) for month in choices}:
        raise InputError(f"请选择有效的{label}。")
    return int(value)


def number(value, label, minimum, maximum):
    try:
        result = Decimal(str(value))
        if result.is_finite() and Decimal(str(minimum)) <= result <= Decimal(str(maximum)):
            return result
    except (InvalidOperation, ValueError):
        pass
    raise InputError(f"{label}须在 {minimum} 至 {maximum} 之间。")


def rounded(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def level(spot, unit, value, label):
    decimals = max(4 if spot < 1 else 2, min(6, -spot.normalize().as_tuple().exponent))
    precision = Decimal(1).scaleb(-decimals)
    if unit == "percent":
        return (spot * number(value, label, 0.01, 1000) / 100).quantize(precision, rounding=ROUND_HALF_UP)
    if unit == "price":
        return number(value, label, precision, 10_000_000).quantize(precision, rounding=ROUND_HALF_UP)
    raise InputError(f"请选择{label}的输入单位。")


@dataclass(frozen=True)
class Event:
    observation: str
    accrual_end: str
    payment: str
    settlement: str
    can_call: bool


def build_terms(draft, snapshot):
    instrument = snapshot.get("instrument") or resolve_instrument(snapshot["instrument_id"])
    tenor = month_choice(draft.get("tenor_months"), TENORS, "票据期限")
    if draft.get("knockout") not in ("none", "monthly"):
        raise InputError("请选择无敲出或每月敲出。")
    mode = draft["knockout"]
    if mode == "none" and (draft.get("ko_unit") is not None or draft.get("ko_value") is not None):
        raise InputError("无敲出条款须清空敲出价。")
    spot = Decimal(str(snapshot.get("reference_price", snapshot["close"])))
    strike = level(spot, draft.get("strike_unit"), draft.get("strike_value"), "接货价")
    if not 0 < strike <= spot:
        raise InputError("接货价须大于零且不高于参考价。")
    ko = None
    if mode == "monthly":
        ko = level(spot, draft.get("ko_unit"), draft.get("ko_value"), "敲出价")
        if ko <= strike:
            raise InputError("敲出价须高于接货价。")
    notional = rounded(number(draft.get("notional"), "面值", 100, 100_000_000))
    start = date.fromisoformat(snapshot["as_of"])
    events = []
    for month in (range(1, tenor + 1) if mode == "monthly" else (tenor,)):
        target = add_months(start, month)
        sessions = dates_between(instrument, target, target + timedelta(days=14))
        if not sessions:
            raise InputError("交易日历缺少估值日。")
        observation = sessions[0].isoformat()
        events.append(asdict(Event(observation, observation, observation, observation, month < tenor)))
    return {
        "version": TERMS_VERSION, "instrument_id": instrument["id"], "currency": instrument["currency"],
        "reference_price": float(spot), "strike": float(strike),
        "strike_pct": float(strike / spot * 100), "knockout": mode,
        "ko": float(ko) if ko is not None else None,
        "ko_pct": float(ko / spot * 100) if ko is not None else None,
        "notional": float(notional), "issue_date": start.isoformat(),
        "tenor_months": tenor, "day_count": "ACT/365F",
        "settlement": "share_delivery_equivalent", "events": events,
    }
