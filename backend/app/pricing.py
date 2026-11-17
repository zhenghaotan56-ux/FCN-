"""Par coupon for a single-share FCN, under flat rates and dividend yield."""

from datetime import date
from math import erf, exp, log, sqrt

import numpy as np

MODEL_VERSION = "fcn-flat-gbm-v1"


def year_fraction(start, end):
    return (date.fromisoformat(end) - date.fromisoformat(start)).days / 365


def simulate_paths(spot, rate, dividend_yield, volatility, times, paths, seed):
    # Adapted from ShrishDhuria/autocallable-pricer at db8f59a, MIT.
    # Use actual observation intervals and retain antithetic pairing for error estimates.
    if paths < 4 or paths % 2:
        raise ValueError("Simulation requires an even path count of at least four.")
    steps = np.diff(np.r_[0.0, times])
    if np.any(steps <= 0):
        raise ValueError("Observation times must be strictly increasing.")
    rng = np.random.default_rng(seed)
    drift = (rate - dividend_yield - 0.5 * volatility ** 2) * steps
    scale = volatility * np.sqrt(steps)
    shocks = rng.standard_normal((paths // 2, len(steps)))
    shocks = np.vstack([shocks, -shocks])
    return spot * np.exp(np.cumsum(drift + scale * shocks, axis=1))


def european_put(spot, strike, rate, dividend_yield, vol, time):
    discounted_strike = strike * exp(-rate * time)
    discounted_spot = spot * exp(-dividend_yield * time)
    if vol == 0:
        return max(0.0, discounted_strike - discounted_spot)
    scale = vol * sqrt(time)
    d1 = (log(spot / strike) + (rate - dividend_yield) * time) / scale + scale / 2
    d2 = d1 - scale

    def cdf(x):
        return (1 + erf(x / sqrt(2))) / 2

    return max(0.0, discounted_strike * cdf(-d2) - discounted_spot * cdf(-d1))


def cash_flow_legs(terms, paths):
    """Return redemption values and the ending event index for each price path."""
    events = terms["events"]
    final = len(events) - 1
    hits = np.zeros(paths.shape, dtype=bool)
    if terms["ko"] is not None:
        hits = (paths >= terms["ko"]) & np.array([e["can_call"] for e in events])
    called = hits.any(axis=1)
    ending = np.where(called, hits.argmax(axis=1), final)
    redemption = np.where(called, 1.0, np.minimum(1.0, paths[:, -1] / terms["strike"]))
    return redemption, ending


def solve_coupon(terms, market, volatility, paths=100_000, seed=42):
    start = terms["issue_date"]
    events = terms["events"]
    observations = [year_fraction(start, e["observation"]) for e in events]
    accruals = np.array([year_fraction(start, e["accrual_end"]) for e in events])
    payments = np.array([year_fraction(start, e["payment"]) for e in events])
    settlements = np.array([year_fraction(start, e["settlement"]) for e in events])
    rate, dividend = market["rate"], market["dividend_yield"]
    coupon_annuity = accruals * np.exp(-rate * payments)
    if terms["ko"] is None or not any(event["can_call"] for event in events):
        time = observations[-1]
        put = european_put(terms["reference_price"], terms["strike"], rate, dividend, volatility, time)
        discount = exp(-rate * settlements[-1])
        redemption_pv = discount - put / terms["strike"] * exp(-rate * (settlements[-1] - time))
        return float((1 - redemption_pv) / coupon_annuity[-1]), None

    samples = simulate_paths(terms["reference_price"], rate, dividend, volatility, observations, paths, seed)
    redemption, ending = cash_flow_legs(terms, samples)
    base = redemption * np.exp(-rate * settlements[ending])
    annuity = coupon_annuity[ending]
    coupon = (1 - base.mean()) / annuity.mean()
    residual = base + coupon * annuity
    half = paths // 2
    paired = (residual[:half] + residual[half:]) / 2
    stderr = paired.std(ddof=1) / sqrt(half) / annuity.mean()
    return float(coupon), float(stderr)


def price(terms, market, paths=100_000, seed=42):
    coupon, stderr = solve_coupon(terms, market, market["volatility"], paths, seed)
    variants = [
        solve_coupon(terms, market, market["volatility"] * multiplier, paths, seed)[0]
        for multiplier in (0.8, 1.2)
    ]
    scenarios = []
    for event in terms["events"]:
        if event["can_call"]:
            scenarios.append(scenario(terms, event, coupon, f"达到敲出价 · {event['observation']}", 1.0))
    final = terms["events"][-1]
    scenarios.extend([
        scenario(terms, final, coupon, "到期股价达到接货价", 1.0),
        scenario(terms, final, coupon, "到期股价为接货价的 80%", 0.8),
        scenario(terms, final, coupon, "到期股价为接货价的 50%", 0.5),
    ])
    return {
        "annual_coupon": coupon, "full_term_coupon": scenarios[-3]["coupon_ratio"],
        "range": [min(coupon, *variants), max(coupon, *variants)],
        "range_method": "历史波动率相对上下调整 20%",
        "standard_error": stderr, "scenarios": scenarios,
        "model": MODEL_VERSION, "method": "Black–Scholes" if stderr is None else "风险中性 GBM",
        "paths": paths if stderr is not None else None, "seed": seed if stderr is not None else None,
        "shares_equivalent": terms["notional"] / terms["strike"],
    }


def scenario(terms, event, annual_coupon, label, principal_fraction):
    days = (date.fromisoformat(event["accrual_end"]) - date.fromisoformat(terms["issue_date"])).days
    coupon_ratio = annual_coupon * days / 365
    notional = terms["notional"]
    return {
        "label": label, "days": days, "payment": event["payment"], "coupon_ratio": coupon_ratio,
        "coupon": notional * coupon_ratio, "principal_value": notional * principal_fraction,
        "net": notional * (principal_fraction + coupon_ratio - 1),
        "settlement": "股票交割等值" if principal_fraction < 1 else "现金还本",
    }
