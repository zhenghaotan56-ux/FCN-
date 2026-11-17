"""Application factory and authenticated, same-origin FCN endpoints."""

from datetime import date, timedelta
from functools import wraps
import hmac
import json
import os
from pathlib import Path
import secrets
from time import time

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import assistant, market, pricing, store
from .report import quote_pdf
from .terms import INSTRUMENTS, LOOKBACKS, ROOT, TENORS, InputError, add_months, build_terms, month_choice, resolve_instrument

FORM_FIELDS = (set(assistant.FIELDS) - {"unsupported"}) | {"as_of"}


def create_app(overrides=None):
    load_dotenv(ROOT / ".env")
    demo_login = os.getenv("FCN_DEMO_LOGIN") == "1"
    frontend = ROOT / "frontend"
    app = Flask(__name__, template_folder=str(frontend), static_folder=str(frontend / "assets"),
                static_url_path="/assets")
    app.config.from_mapping(
        SECRET_KEY=os.getenv("FCN_SECRET_KEY") or (secrets.token_hex(32) if demo_login else None),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=os.getenv("FCN_SECURE_COOKIE", "0" if demo_login else "1") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8), SESSION_REFRESH_EACH_REQUEST=False,
        MAX_CONTENT_LENGTH=16_384, DEMO_LOGIN=demo_login, MARKET_MODE=os.getenv("FCN_MARKET_MODE", "fixture"),
        DB=os.getenv("FCN_DB", str(ROOT / "var/fcn.sqlite3")),
        USERS_FILE=os.getenv("FCN_USERS_FILE", str(ROOT / "private/users.json")),
        USERS_JSON=os.getenv("FCN_USERS_JSON", ""),
        ASSUMPTIONS_FILE=os.getenv("FCN_ASSUMPTIONS_FILE", str(ROOT / "private/assumptions.json")),
        ASSUMPTIONS_JSON=os.getenv("FCN_ASSUMPTIONS_JSON", ""),
        VENDOR_URL=os.getenv("FCN_VENDOR_URL", ""), VENDOR_TOKEN=os.getenv("FCN_VENDOR_TOKEN", ""),
        LONGBRIDGE_HK_REALTIME=os.getenv("FCN_LONGBRIDGE_HK_REALTIME") == "1",
        AI_KEY=os.getenv("OPENAI_API_KEY", ""), AI_MODEL=os.getenv("OPENAI_MODEL", ""),
        ALLOW_RESEARCH_DATA=os.getenv("FCN_ALLOW_RESEARCH_DATA") == "1",
        MC_PATHS=100_000,
    )
    if overrides:
        app.config.update(overrides)
    cfg = app.config
    if not cfg["SECRET_KEY"] or len(cfg["SECRET_KEY"]) < 32:
        raise RuntimeError("Set a stable FCN_SECRET_KEY of at least 32 characters.")
    if cfg["MARKET_MODE"] not in {"fixture", "openbb", "vendor", "longbridge"}:
        raise RuntimeError("FCN_MARKET_MODE must be fixture, openbb, vendor or longbridge.")
    if cfg["DEMO_LOGIN"] and cfg["MARKET_MODE"] != "fixture":
        raise RuntimeError("Demo credentials are restricted to fixture data.")
    if cfg["MARKET_MODE"] == "openbb" and not cfg["ALLOW_RESEARCH_DATA"]:
        raise RuntimeError("OpenBB/Yahoo requires FCN_ALLOW_RESEARCH_DATA=1 for internal evaluation.")
    if cfg["DEMO_LOGIN"]:
        users = {"demo": generate_password_hash("fcn-demo-only")}
    else:
        users = json.loads(cfg["USERS_JSON"] or Path(cfg["USERS_FILE"]).read_text(encoding="utf-8-sig"))
        if not isinstance(users, dict) or not users or any(
            not isinstance(value, str) or not value.startswith("scrypt:") for value in users.values()
        ):
            raise RuntimeError("Use scripts.manage_users to create a nonempty password-hash file.")
    fallback_hash = generate_password_hash(secrets.token_hex(16))
    store.initialize(cfg["DB"])
    available = [item for item in INSTRUMENTS if cfg["MARKET_MODE"] != "fixture" or item.get("demo")]

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
        )
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.before_request
    def csrf():
        if request.method == "POST":
            token = request.headers.get("X-CSRF-Token") or request.form.get("csrf", "")
            stored = session.get("csrf")
            if not stored or not hmac.compare_digest(token.encode(), stored.encode()):
                abort(403)

    @app.errorhandler(InputError)
    def bad_input(error):
        return jsonify(error=str(error)), 422

    @app.errorhandler(market.MarketUnavailable)
    @app.errorhandler(assistant.AssistantUnavailable)
    def unavailable(error):
        return jsonify(error=str(error)), 503

    @app.errorhandler(403)
    @app.errorhandler(404)
    def missing(error):
        message = "请求校验失败，请刷新页面后重试。" if error.code == 403 else "找不到该记录。"
        return jsonify(error=message), error.code

    def authenticated(handler):
        @wraps(handler)
        def wrapper(*args, **kwargs):
            if session.get("user") not in users:
                if request.path.startswith("/api/"):
                    return jsonify(error="请重新登录。"), 401
                return redirect(url_for("login"))
            return handler(*args, **kwargs)
        return wrapper

    def json_body():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise InputError("请求须为 JSON 对象。")
        return body

    def get_record(key):
        record = store.get_valuation(cfg["DB"], session["user"], key)
        if record is None:
            abort(404)
        return record

    def public_record(record):
        market_data = record["market"]
        window = market_data.get("lookback_months", 3)
        chart_start = add_months(date.fromisoformat(market_data.get("history_end", market_data["as_of"])), -window).isoformat()
        visible = {**market_data, "rows": [
            {"date": row["date"], "close": row["close"]}
            for row in market_data["rows"] if row["date"] >= chart_start
        ]}
        return {
            "id": record["id"], "terms": record["terms"], "market": visible,
            "result": record["result"],
            "instrument": market_data.get("instrument") or resolve_instrument(record["terms"]["instrument_id"]),
            "issuer_quotes": store.active_quotes(cfg["DB"], record["terms_hash"])
            if record["result"] and market_data["mode"] == "vendor" else [],
        }

    @app.get("/healthz")
    def health():
        return jsonify(status="ok")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET" and session.get("user") in users:
            return redirect(url_for("home"))
        error = None
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            valid = len(password) <= 256 and check_password_hash(users.get(username, fallback_hash), password)
            if valid and username in users:
                session.clear()
                session.permanent = True
                session["user"] = username
                session["csrf"] = secrets.token_urlsafe(24)
                return redirect(url_for("home"), code=303)
            error = "用户名或密码有误。"
        token = session.setdefault("csrf", secrets.token_urlsafe(24))
        return render_template("login.html", csrf=token, demo=cfg["DEMO_LOGIN"], error=error), 401 if error else 200

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"), code=303)

    @app.get("/")
    @authenticated
    def home():
        return render_template(
            "index.html", csrf=session["csrf"], instruments=available,
            fixture=cfg["MARKET_MODE"] == "fixture", research=cfg["MARKET_MODE"] == "openbb",
            ai_enabled=bool(cfg["AI_KEY"] and cfg["AI_MODEL"]), tenors=TENORS, lookbacks=LOOKBACKS,
        )

    @app.post("/api/terms/parse")
    @authenticated
    def parse():
        return jsonify(assistant.parse_terms(cfg, json_body().get("message"), available))

    @app.post("/api/terms/preview")
    @authenticated
    def preview():
        draft = json_body()
        if not set(draft) <= FORM_FIELDS:
            raise InputError("条款包含不支持的参数。")
        month_choice(draft.get("tenor_months"), TENORS, "票据期限")
        snapshot = market.market_snapshot(cfg, draft.get("instrument_id"), lookback_months=draft.get("lookback_months"))
        terms = build_terms(draft, snapshot)
        key = store.create_preview(cfg["DB"], session["user"], terms, snapshot)
        return jsonify(public_record(get_record(key))), 201

    @app.post("/api/valuations")
    @authenticated
    def valuation():
        body = json_body()
        if set(body) != {"preview_id"} or not isinstance(body["preview_id"], str):
            raise InputError("请先确认条款预览。")
        record = get_record(body["preview_id"])
        if record["result"] is not None:
            return jsonify(public_record(record))
        if record["expires_at"] <= time():
            return jsonify(error="条款预览已过期，请重新获取行情。"), 409
        result = pricing.price(record["terms"], record["market"], paths=cfg["MC_PATHS"])
        completed = store.save_result(cfg["DB"], session["user"], record["id"], result)
        return jsonify(public_record(completed)), 201

    @app.get("/api/valuations")
    @authenticated
    def valuations():
        return jsonify(store.history(cfg["DB"], session["user"]))

    @app.get("/api/valuations/<key>")
    @authenticated
    def read_valuation(key):
        record = get_record(key)
        if record["result"] is None:
            abort(404)
        return jsonify(public_record(record))

    @app.get("/api/valuations/<key>/report")
    @authenticated
    def report(key):
        record = get_record(key)
        if record["result"] is None:
            abort(404)
        return send_file(quote_pdf(public_record(record)), mimetype="application/pdf",
                         as_attachment=True, download_name=f"FCN-{key[:12]}.pdf")

    @app.post("/api/valuations/<key>/explain")
    @authenticated
    def explain(key):
        record = get_record(key)
        if record["result"] is None:
            abort(404)
        return jsonify(explanation=assistant.explain(cfg, record["terms"], record["result"]))

    return app
