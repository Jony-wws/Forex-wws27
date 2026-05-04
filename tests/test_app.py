from __future__ import annotations

from typing import Any

import httpx
from fastapi.testclient import TestClient


def _client():
    from app.main import app
    from app.db import init_engine

    init_engine()
    return TestClient(app)


def _login(client: TestClient) -> TestClient:
    r = client.post("/login", data={"password": "test-password"}, follow_redirects=False)
    assert r.status_code == 302, r.text
    return client


def test_healthz_no_auth():
    c = _client()
    r = c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_login_redirects():
    c = _client()
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_login_wrong_password():
    c = _client()
    r = c.post("/login", data={"password": "nope"})
    assert r.status_code == 401


def test_login_ok_and_me():
    c = _client()
    _login(c)
    r = c.get("/api/me")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_me_unauthenticated():
    c = _client()
    r = c.get("/api/me")
    assert r.status_code == 401


def test_account_crud():
    c = _client()
    _login(c)
    assert c.get("/api/accounts").json() == []

    r = c.post(
        "/api/accounts",
        json={"label": "main", "api_key": "apk_user_xxxx_yyyyyyyy"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["label"] == "main"
    assert body["is_default"] is True  # first account is auto-default
    assert "key_preview" in body and "…" in body["key_preview"]

    rows = c.get("/api/accounts").json()
    assert len(rows) == 1

    # add a second one and mark it default
    c.post(
        "/api/accounts",
        json={"label": "alt", "api_key": "apk_user_aaaa_bbbbbbbb", "is_default": True},
    )
    rows = c.get("/api/accounts").json()
    labels = {r["label"]: r["is_default"] for r in rows}
    assert labels == {"main": False, "alt": True}

    # rejection: bad prefix
    bad = c.post("/api/accounts", json={"label": "x", "api_key": "not-a-key"})
    assert bad.status_code == 400


def test_create_chat_invokes_devin(monkeypatch):
    c = _client()
    _login(c)
    c.post(
        "/api/accounts",
        json={"label": "main", "api_key": "apk_user_xxxx_yyyyyyyy"},
    )

    captured: dict[str, Any] = {}

    async def fake_create_session(self, prompt, *, title=None, **kwargs):  # noqa: ANN001
        captured["prompt"] = prompt
        captured["title"] = title
        captured["api_key"] = self.api_key
        return {"session_id": "devin-fake123", "url": "https://app.devin.ai/sessions/fake123"}

    monkeypatch.setattr("app.devin_client.DevinClient.create_session", fake_create_session)

    r = c.post(
        "/api/chats",
        json={"message": "hello there", "model_hint": "claude-sonnet-4.5"},
    )
    assert r.status_code == 200, r.text
    chat = r.json()
    assert chat["devin_session_id"] == "devin-fake123"
    assert chat["devin_url"].startswith("https://")
    assert chat["model_hint"] == "claude-sonnet-4.5"
    assert "claude-sonnet-4.5" in captured["prompt"]
    assert "hello there" in captured["prompt"]
    assert captured["api_key"] == "apk_user_xxxx_yyyyyyyy"
    assert chat["messages"] and chat["messages"][0]["role"] == "user"


def test_send_message_and_refresh(monkeypatch):
    c = _client()
    _login(c)
    c.post(
        "/api/accounts",
        json={"label": "main", "api_key": "apk_user_xxxx_yyyyyyyy"},
    )

    async def fake_create_session(self, prompt, *, title=None, **kwargs):  # noqa: ANN001
        return {"session_id": "devin-fake123", "url": "https://app.devin.ai/sessions/fake123"}

    sent: list[str] = []

    async def fake_send_message(self, session_id, message):  # noqa: ANN001
        sent.append(message)
        return None

    refresh_payload: dict[str, Any] = {
        "session_id": "devin-fake123",
        "status_enum": "finished",
        "title": "Pretty title",
        "messages": [
            {
                "type": "initial_user_message",
                "event_id": "ev-user-1",
                "message": "hello there",
            },
            {
                "type": "devin_message",
                "event_id": "ev-devin-1",
                "message": "Hi! Working on it.",
            },
        ],
    }

    async def fake_get_session(self, session_id):  # noqa: ANN001
        return refresh_payload

    monkeypatch.setattr("app.devin_client.DevinClient.create_session", fake_create_session)
    monkeypatch.setattr("app.devin_client.DevinClient.send_message", fake_send_message)
    monkeypatch.setattr("app.devin_client.DevinClient.get_session", fake_get_session)

    chat = c.post("/api/chats", json={"message": "hello there"}).json()
    chat_id = chat["id"]

    r = c.post(f"/api/chats/{chat_id}/messages", json={"message": "another one"})
    assert r.status_code == 200
    assert sent == ["another one"]

    r = c.post(f"/api/chats/{chat_id}/refresh")
    assert r.status_code == 200
    refreshed = r.json()
    assert refreshed["status"] == "finished"
    assert refreshed["title"] == "Pretty title"
    roles = [m["role"] for m in refreshed["messages"]]
    assert "assistant" in roles

    # idempotent refresh: doesn't double-insert
    r2 = c.post(f"/api/chats/{chat_id}/refresh")
    assert len(r2.json()["messages"]) == len(refreshed["messages"])


def test_create_chat_without_account_fails():
    c = _client()
    _login(c)
    r = c.post("/api/chats", json={"message": "hi"})
    assert r.status_code == 400
    assert "No Devin account" in r.json()["detail"]


def test_render_chat_markdown(tmp_path, monkeypatch):
    from app.db import Chat, DevinAccount, Message, get_db, init_engine
    from app.github_export import render_chat_markdown

    init_engine()
    gen = get_db()
    db = next(gen)
    try:
        acc = DevinAccount(label="m", api_key="apk_user_x", is_default=True)
        db.add(acc)
        db.flush()
        chat = Chat(title="Hello", account_id=acc.id, devin_session_id="devin-1")
        db.add(chat)
        db.flush()
        db.add(Message(chat_id=chat.id, role="user", content="hi"))
        db.add(Message(chat_id=chat.id, role="assistant", content="hello back"))
        db.commit()
        db.refresh(chat)
        md = render_chat_markdown(chat)
    finally:
        try:
            next(gen)
        except StopIteration:
            pass

    assert md.startswith("# Hello\n")
    assert "## User" in md
    assert "## Devin" in md
    assert "hello back" in md


def test_send_message_devin_502_propagates(monkeypatch):
    c = _client()
    _login(c)
    c.post("/api/accounts", json={"label": "m", "api_key": "apk_user_x"})

    async def fail_create(self, prompt, *, title=None, **kw):  # noqa: ANN001
        request = httpx.Request("POST", "https://api.devin.ai/v1/sessions")
        response = httpx.Response(500, request=request, content=b"boom")
        raise httpx.HTTPStatusError("boom", request=request, response=response)

    monkeypatch.setattr("app.devin_client.DevinClient.create_session", fail_create)

    r = c.post("/api/chats", json={"message": "hello"})
    assert r.status_code == 502
    assert "Devin API error" in r.json()["detail"]
