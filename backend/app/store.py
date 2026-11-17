"""Store immutable valuation inputs and isolate records by customer account."""

from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from time import time
from uuid import uuid4

from .terms import ROOT


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    return closing(db)


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        for migration in sorted((ROOT / "backend/migrations").glob("*.sql")):
            db.executescript(migration.read_text(encoding="utf-8"))


def create_preview(path, username, terms, market):
    key, created = uuid4().hex, int(time())
    digest = sha256(encode(terms).encode("utf-8")).hexdigest()
    with connect(path) as db, db:
        db.execute(
            "INSERT INTO valuations VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            (key, username, created, created + 900, digest, encode(terms), encode(market)),
        )
    return key


def get_valuation(path, username, key):
    with connect(path) as db:
        row = db.execute("SELECT * FROM valuations WHERE id=? AND username=?", (key, username)).fetchone()
    if row is None:
        return None
    record = dict(row)
    for field in ("terms", "market", "result"):
        if record[field] is not None:
            record[field] = json.loads(record[field])
    return record


def save_result(path, username, key, result):
    with connect(path) as db, db:
        db.execute(
            "UPDATE valuations SET result=? WHERE id=? AND username=? AND result IS NULL",
            (encode(result), key, username),
        )
    return get_valuation(path, username, key)


def history(path, username):
    with connect(path) as db:
        rows = db.execute(
            "SELECT id, created_at, terms, result FROM valuations "
            "WHERE username=? AND result IS NOT NULL ORDER BY created_at DESC, rowid DESC LIMIT 30",
            (username,),
        ).fetchall()
    return [{
        "id": row["id"], "created_at": row["created_at"], "terms": json.loads(row["terms"]),
        "annual_coupon": json.loads(row["result"])["annual_coupon"],
    } for row in rows]


def active_quotes(path, terms_hash, now=None):
    now = int(time()) if now is None else now
    with connect(path) as db:
        return [dict(row) for row in db.execute(
            "SELECT issuer, quoted_at, expires_at, basis, coupon FROM issuer_quotes "
            "WHERE terms_hash=? AND quoted_at<=? AND expires_at>? ORDER BY quoted_at DESC",
            (terms_hash, now, now),
        )]
