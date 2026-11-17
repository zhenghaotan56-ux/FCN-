"""Generate redistributable synthetic fixtures or save a private OpenBB snapshot."""

import argparse
import csv
from datetime import date
import json
from pathlib import Path

import numpy as np

from backend.app.market import market_snapshot
from backend.app.terms import BY_ID, ROOT, add_months, dates_between


def generate_fixture():
    end = date(2026, 11, 2)
    rows = []
    for identifier, close, vol, seed in (
        ("XHKG:00700", 392.20, .28583909677830954, 771),
        ("XNAS:MSFT", 507.12, .22035232311194058, 824),
    ):
        sessions = dates_between(BY_ID[identifier], add_months(end, -4), end)
        returns = np.random.default_rng(seed).normal(size=len(sessions) - 1)
        returns *= vol / (returns[-60:].std(ddof=1) * np.sqrt(252))
        prices = np.exp(np.r_[0, returns.cumsum()])
        prices *= close / prices[-1]
        prices[-1] = close
        rows.extend((identifier, day.isoformat(), f"{price:.8f}", f"{price:.8f}")
                    for day, price in zip(sessions, prices))
    output = ROOT / "demo/daily_prices.csv"
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("instrument_id", "date", "close", "adjusted_close"))
        writer.writerows(rows)
    print(f"{len(rows)} synthetic rows written to {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openbb", metavar="INSTRUMENT")
    parser.add_argument("--as-of")
    parser.add_argument("--assumptions", default="private/assumptions.json")
    args = parser.parse_args()
    if not args.openbb:
        generate_fixture()
        return
    config = {"MARKET_MODE": "openbb", "ASSUMPTIONS_FILE": args.assumptions}
    data = market_snapshot(config, args.openbb, args.as_of)
    private = ROOT / "private"
    private.mkdir(exist_ok=True)
    target = private / f"{args.openbb.replace(':', '_')}-{data['as_of']}.json"
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(data['rows'])} rows saved to {Path(target).relative_to(ROOT)}")


if __name__ == "__main__":
    main()
