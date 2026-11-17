"""Validate customer inputs and build the dated FCN schedule."""

from calendar import monthrange
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
import json
from pathlib import Path

import pandas_market_calendars as mcal

ROOT = Path(__file__).resolve().parents[2]
TERMS_VERSION = "fcn-3m-fixed-v1"
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


def latest_closed_session(instrument, now=None):
    now = now or datetime.now(timezone.utc)
    local_day = now.astimezone(calendar(instrument["calendar"]).tz).date()
    schedule = calendar(instrument["calendar"]).schedule(local_day - timedelta(days=14), local_day)
    closed = schedule[schedule.market_close <= now]
    if closed.empty:
        raise InputError("无法确定最近完整交易日。")
    return closed.index[-1].date()


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
    if unit == "percent":
        return rounded(spot * number(value, label, 0.01, 1000) / 100)
    if unit == "price":
        return rounded(number(value, label, 0.01, 10_000_000))
    raise InputError(f"请选择{label}的输入单位。")


@dataclass(frozen=True)
class Event:
    observation: str
    accrual_end: str
    payment: str
    settlement: str
    can_call: bool


def build_terms(draft, snapshot):
    instrument = BY_ID[snapshot["instrument_id"]]
    if draft.get("knockout") not in ("none", "monthly"):
        raise InputError("请选择无敲出或每月敲出。")
    mode = draft["knockout"]
    if mode == "none" and (draft.get("ko_unit") is not None or draft.get("ko_value") is not None):
        raise InputError("无敲出条款须清空敲出价。")
    spot = Decimal(str(snapshot["close"]))
    strike = level(spot, draft.get("strike_unit"), draft.get("strike_value"), "接货价")
    if not 0 < strike <= spot:
        raise InputError("接货价须大于零且不高于参考收盘价。")
    ko = None
    if mode == "monthly":
        ko = level(spot, draft.get("ko_unit"), draft.get("ko_value"), "敲出价")
        if ko <= strike:
            raise InputError("敲出价须高于接货价。")
    notional = rounded(number(draft.get("notional"), "面值", 100, 100_000_000))
    start = date.fromisoformat(snapshot["as_of"])
    events = []
    for month in (range(1, 4) if mode == "monthly" else (3,)):
        target = add_months(start, month)
        sessions = dates_between(instrument, target, target + timedelta(days=14))
        if not sessions:
            raise InputError("交易日历缺少估值日。")
        observation = sessions[0].isoformat()
        events.append(asdict(Event(observation, observation, observation, observation, month < 3)))
    return {
        "version": TERMS_VERSION, "instrument_id": instrument["id"], "currency": instrument["currency"],
        "reference_price": float(spot), "strike": float(strike),
        "strike_pct": float(strike / spot * 100), "knockout": mode,
        "ko": float(ko) if ko is not None else None,
        "ko_pct": float(ko / spot * 100) if ko is not None else None,
        "notional": float(notional), "issue_date": start.isoformat(),
        "tenor_months": 3, "day_count": "ACT/365F",
        "settlement": "share_delivery_equivalent", "events": events,
    }
