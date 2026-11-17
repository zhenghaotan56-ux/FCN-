import json
import re
from datetime import date
from time import time

import pytest
from werkzeug.security import generate_password_hash

from backend.app import assistant, market, store
from backend.app.main import create_app
from backend.app.terms import ROOT


@pytest.fixture
def app(tmp_path):
    users = tmp_path / "users.json"
    users.write_text(json.dumps({name: generate_password_hash("test-pass-long") for name in ("alice", "bob")}))
    return create_app({
        "TESTING": True, "SECRET_KEY": "test-only-key" * 4, "DEMO_LOGIN": False,
        "MARKET_MODE": "fixture", "SESSION_COOKIE_SECURE": False,
        "DB": str(tmp_path / "test.sqlite"), "USERS_FILE": str(users), "AI_KEY": "", "AI_MODEL": "",
    })


def login(client, username="alice"):
    html = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf" value="([^"]+)"', html)[1]
    response = client.post("/login", data={"username": username, "password": "test-pass-long", "csrf": token})
    assert response.status_code == 303
    html = client.get("/").get_data(as_text=True)
    return {"X-CSRF-Token": re.search(r'name="csrf-token" content="([^"]+)"', html)[1]}


def draft(name="terms_monthly_ko.json"):
    return json.loads((ROOT / "demo" / name).read_text(encoding="utf-8"))


def test_preview_confirmation_replay_pdf_and_isolation(app):
    first, other = app.test_client(), app.test_client()
    headers, other_headers = login(first), login(other, "bob")
    preview = first.post("/api/terms/preview", json=draft(), headers=headers)
    assert preview.status_code == 201
    saved = preview.get_json()
    key = saved["id"]
    assert saved["terms"]["ko"] == 411.81
    assert saved["market"]["rows"][0]["date"] >= "2026-08-02"
    assert other.post("/api/valuations", json={"preview_id": key}, headers=other_headers).status_code == 404
    assert first.post("/api/valuations", json={"preview_id": key, "rate": 0}, headers=headers).status_code == 422
    computed = first.post("/api/valuations", json={"preview_id": key}, headers=headers)
    assert computed.status_code == 201
    assert first.post("/api/valuations", json={"preview_id": key}, headers=headers).get_json() == computed.get_json()
    assert first.get(f"/api/valuations/{key}").get_json() == computed.get_json()
    assert len(first.get("/api/valuations").get_json()) == 1
    assert other.get("/api/valuations").get_json() == []
    assert other.get(f"/api/valuations/{key}/report").status_code == 404
    pdf = first.get(f"/api/valuations/{key}/report")
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")


def test_form_rejects_malformed_or_unsupported_contract(app):
    client = app.test_client()
    assert client.get("/api/valuations").status_code == 401
    headers = login(client)
    assert client.post("/api/terms/preview", json=draft()).status_code == 403
    assert client.post("/api/terms/preview", json={**draft(), "tenor": 6}, headers=headers).status_code == 422
    assert client.post("/api/terms/preview", json={**draft(), "ko_value": 80}, headers=headers).status_code == 422
    assert client.post("/api/terms/parse", json={"message":"微软，85% 接货，无敲出"}, headers=headers).status_code == 503
    us = client.post("/api/terms/preview", json={**draft("terms_no_ko.json"), "instrument_id":"XNAS:MSFT"}, headers=headers)
    assert us.status_code == 201 and us.get_json()["terms"]["currency"] == "USD"


def test_ai_structured_draft_and_form_use_same_terms(app, monkeypatch):
    app.config.update(AI_KEY="test", AI_MODEL="test-model")
    contract = {**draft(), "unsupported": []}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(contract)},
            ]}]}

    def mock_post(url, **kwargs):
        assert url == "https://api.openai.com/v1/responses"
        assert kwargs["json"]["store"] is False
        assert kwargs["json"]["text"]["format"]["strict"] is True
        return Response()

    monkeypatch.setattr(assistant.requests, "post", mock_post)
    client = app.test_client()
    headers = login(client)
    parsed = client.post("/api/terms/parse", json={"message": "腾讯，85% 接货，105% 月度敲出"}, headers=headers)
    assert parsed.status_code == 200
    draft_result = parsed.get_json()
    draft_result.pop("unsupported")
    ai_preview = client.post("/api/terms/preview", json=draft_result, headers=headers)
    form_preview = client.post("/api/terms/preview", json=draft(), headers=headers)
    assert ai_preview.get_json()["terms"] == form_preview.get_json()["terms"]
    key = ai_preview.get_json()["id"]
    assert client.post(f"/api/valuations/{key}/explain", json={}, headers=headers).status_code == 404
    assert client.post("/api/valuations", json={"preview_id": key}, headers=headers).status_code == 201
    monkeypatch.setattr(assistant, "structured_reply", lambda *_: {"explanation": "首次敲出时结算应计票息。"})
    explained = client.post(f"/api/valuations/{key}/explain", json={}, headers=headers)
    assert explained.get_json() == {"explanation": "首次敲出时结算应计票息。"}


def test_vendor_adapter_snapshot_reaches_pricer(app, monkeypatch):
    assumptions = ROOT / "demo/market_assumptions.json"
    app.config.update(MARKET_MODE="vendor", VENDOR_URL="https://vendor.example/daily",
                      VENDOR_TOKEN="test", ASSUMPTIONS_FILE=str(assumptions))

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"instrument_id": "XHKG:00700", "currency": "HKD", "source": "Test Feed",
                    "rows": market.fixture("XHKG:00700")}

    def mock_get(url, **kwargs):
        assert kwargs["params"] == {"instrument": "XHKG:00700", "start": "2026-07-02", "end": "2026-11-02"}
        assert kwargs["allow_redirects"] is False
        return Response()

    monkeypatch.setattr(market.requests, "get", mock_get)
    monkeypatch.setattr(market, "latest_closed_session", lambda _: date(2026, 11, 2))
    client = app.test_client()
    headers = login(client)
    preview = client.post("/api/terms/preview", json=draft("terms_no_ko.json"), headers=headers)
    assert preview.status_code == 201
    assert preview.get_json()["market"]["source"] == "Test Feed"
    response = client.post("/api/valuations", json={"preview_id": preview.get_json()["id"]}, headers=headers)
    assert response.status_code == 201
    assert response.get_json()["result"]["annual_coupon"] == pytest.approx(.06589870, abs=2e-7)
    key = response.get_json()["id"]
    with store.connect(app.config["DB"]) as db, db:
        digest = db.execute("SELECT terms_hash FROM valuations WHERE id=?", (key,)).fetchone()[0]
        now = int(time())
        db.executemany("INSERT INTO issuer_quotes VALUES (?, ?, ?, ?, ?, ?)", [
            (digest, "有效报价", now - 60, now + 300, "annual", .07),
            (digest, "过期报价", now - 120, now - 60, "annual", .09),
            ("different-terms", "其他条款", now - 60, now + 300, "full_term", .02),
        ])
    quotes = client.get(f"/api/valuations/{key}").get_json()["issuer_quotes"]
    assert [(quote["issuer"], quote["basis"], quote["coupon"]) for quote in quotes] == [("有效报价", "annual", .07)]
