"""Import a verified issuer quote for one completed valuation's exact terms."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from backend.app import store
from backend.app.terms import ROOT, number


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Quote timestamps require an explicit timezone offset.")
    return int(parsed.timestamp())


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="A JSON array with one item per issuer quote")
    parser.add_argument("--db", default=os.getenv("FCN_DB", ROOT / "var/fcn.sqlite3"))
    args = parser.parse_args()
    try:
        quotes = json.loads(args.input.read_text(encoding="utf-8-sig"))
        if not isinstance(quotes, list) or not quotes:
            raise ValueError("Expected a nonempty quote array.")
        with store.connect(args.db) as db, db:
            for quote in quotes:
                valuation = db.execute(
                    "SELECT terms_hash, market FROM valuations WHERE id=? AND result IS NOT NULL",
                    (quote["valuation_id"],),
                ).fetchone()
                if valuation is None or json.loads(valuation["market"])["mode"] != "vendor":
                    raise ValueError("Quotes require a completed valuation using an authorized vendor.")
                issuer = quote["issuer"].strip()
                quoted_at, expires_at = timestamp(quote["quoted_at"]), timestamp(quote["expires_at"])
                basis = quote["basis"]
                if not 1 <= len(issuer) <= 100 or quoted_at >= expires_at or basis not in {"annual", "full_term"}:
                    raise ValueError("Invalid issuer, quote validity or coupon basis.")
                coupon = float(number(quote["coupon"], "coupon ratio", 0, 5))
                db.execute("INSERT INTO issuer_quotes VALUES (?, ?, ?, ?, ?, ?)", (
                    valuation["terms_hash"], issuer, quoted_at, expires_at, basis, coupon,
                ))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        parser.exit(1, f"Import rejected: {error}\n")
    print(f"Imported {len(quotes)} quote(s).")


if __name__ == "__main__":
    main()
