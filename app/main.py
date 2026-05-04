from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx
from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import auth, github_export
from .config import get_settings
from .db import Chat, DevinAccount, Message, get_db, get_default_account, init_engine
from .devin_client import DevinClient
from .sync import upsert_messages_from_session

logger = logging.getLogger("dca")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_engine()
    yield


app = FastAPI(title="Devin Chat Aggregator", version="0.1.0", lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True}


# -----------------------------
# UI routes
# -----------------------------


@app.get("/", response_class=HTMLResponse)
def index(request: Request, dca_session: Optional[str] = Cookie(default=None)) -> Any:
    if not auth.is_authenticated(dca_session):
        return RedirectResponse("/login", status_code=302)
    return TEMPLATES.TemplateResponse(request, "app.html")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Any:
    return TEMPLATES.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login_submit(request: Request, password: str = Form(...)) -> Any:
    if not auth.check_password(password):
        return TEMPLATES.TemplateResponse(
            request,
            "login.html",
            {"error": "Wrong password"},
            status_code=401,
        )
    token = auth.make_session_token()
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=auth.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return resp


@app.post("/logout")
def logout() -> Any:
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


# -----------------------------
# JSON API
# -----------------------------


def _json_error(msg: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status_code)


@app.get("/api/me")
def api_me(_: bool = Depends(auth.require_auth)) -> dict[str, Any]:
    return {"ok": True}


@app.get("/api/accounts")
def list_accounts(
    _: bool = Depends(auth.require_auth), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = db.execute(select(DevinAccount).order_by(DevinAccount.id.asc())).scalars().all()
    return [
        {
            "id": a.id,
            "label": a.label,
            "is_default": a.is_default,
            "key_preview": (a.api_key[:9] + "…" + a.api_key[-4:]) if a.api_key else "",
            "created_at": a.created_at.isoformat(),
        }
        for a in rows
    ]


@app.post("/api/accounts")
async def create_account(
    payload: dict[str, Any],
    _: bool = Depends(auth.require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    label = (payload.get("label") or "").strip() or "default"
    api_key = (payload.get("api_key") or "").strip()
    if not api_key.startswith("apk_"):
        raise HTTPException(status_code=400, detail="API key must start with apk_")
    is_default = bool(payload.get("is_default")) or db.execute(select(DevinAccount).limit(1)).first() is None

    if is_default:
        for a in db.execute(select(DevinAccount)).scalars().all():
            a.is_default = False

    acc = DevinAccount(label=label, api_key=api_key, is_default=is_default)
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return {
        "id": acc.id,
        "label": acc.label,
        "is_default": acc.is_default,
        "key_preview": (acc.api_key[:9] + "…" + acc.api_key[-4:]),
    }


@app.delete("/api/accounts/{account_id}")
def delete_account(
    account_id: int,
    _: bool = Depends(auth.require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    acc = db.get(DevinAccount, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="account not found")
    db.delete(acc)
    db.commit()
    return {"ok": True}


@app.post("/api/accounts/{account_id}/default")
def set_default_account(
    account_id: int,
    _: bool = Depends(auth.require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    acc = db.get(DevinAccount, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="account not found")
    for a in db.execute(select(DevinAccount)).scalars().all():
        a.is_default = a.id == account_id
    db.commit()
    return {"ok": True}


@app.get("/api/chats")
def list_chats(
    _: bool = Depends(auth.require_auth), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = (
        db.execute(select(Chat).order_by(Chat.updated_at.desc(), Chat.id.desc())).scalars().all()
    )
    return [_chat_summary(c) for c in rows]


def _chat_summary(c: Chat) -> dict[str, Any]:
    return {
        "id": c.id,
        "title": c.title,
        "account_id": c.account_id,
        "devin_session_id": c.devin_session_id,
        "devin_url": c.devin_url,
        "model_hint": c.model_hint,
        "status": c.status,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
    }


def _msg_dto(m: Message) -> dict[str, Any]:
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "event_type": m.devin_event_type,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "attachments": json.loads(m.attachments_json) if m.attachments_json else [],
    }


@app.get("/api/chats/{chat_id}")
def get_chat(
    chat_id: int,
    _: bool = Depends(auth.require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    chat = db.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    return {**_chat_summary(chat), "messages": [_msg_dto(m) for m in chat.messages]}


def _build_prompt(message: str, model_hint: str | None) -> str:
    """Embed a model hint at the top of the prompt since v1 API does not accept
    model selection directly. The agent picks up the hint."""
    if not model_hint:
        return message
    hint = (
        f"[Site preference: prefer the **{model_hint}** model where possible. "
        f"This is a hint, not a hard constraint.]\n\n"
    )
    return hint + message


@app.post("/api/chats")
async def create_chat(
    payload: dict[str, Any],
    _: bool = Depends(auth.require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    message = (payload.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    title = (payload.get("title") or "").strip() or None
    model_hint = (payload.get("model_hint") or "").strip() or None
    account_id = payload.get("account_id")
    attachments: list[dict[str, Any]] = payload.get("attachments") or []

    if account_id:
        acc = db.get(DevinAccount, int(account_id))
    else:
        acc = get_default_account(db)
    if acc is None:
        raise HTTPException(
            status_code=400,
            detail="No Devin account configured. Add one in Settings.",
        )

    full_message = _build_prompt(message, model_hint)
    if attachments:
        urls = [a.get("url") for a in attachments if a.get("url")]
        if urls:
            full_message += "\n\n" + "\n".join(f"![attached]({u})" for u in urls)

    client = DevinClient(acc.api_key)
    try:
        resp = await client.create_session(prompt=full_message, title=title)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Devin API error {e.response.status_code}: {e.response.text[:300]}",
        ) from e

    chat = Chat(
        title=title or message[:80],
        account_id=acc.id,
        devin_session_id=resp.get("session_id"),
        devin_url=resp.get("url"),
        model_hint=model_hint,
        status="working",
    )
    db.add(chat)
    db.flush()

    user_msg = Message(
        chat_id=chat.id,
        role="user",
        content=message,
        attachments_json=json.dumps(attachments) if attachments else None,
    )
    db.add(user_msg)
    db.commit()
    db.refresh(chat)
    return {**_chat_summary(chat), "messages": [_msg_dto(m) for m in chat.messages]}


@app.post("/api/chats/{chat_id}/messages")
async def send_chat_message(
    chat_id: int,
    payload: dict[str, Any],
    _: bool = Depends(auth.require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    chat = db.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    if not chat.devin_session_id:
        raise HTTPException(status_code=400, detail="chat has no devin session")

    message = (payload.get("message") or "").strip()
    attachments: list[dict[str, Any]] = payload.get("attachments") or []
    if not message and not attachments:
        raise HTTPException(status_code=400, detail="message or attachments required")

    full_message = message
    if attachments:
        urls = [a.get("url") for a in attachments if a.get("url")]
        if urls:
            extra = "\n".join(f"![attached]({u})" for u in urls)
            full_message = (message + "\n\n" + extra).strip()

    acc = db.get(DevinAccount, chat.account_id)
    if acc is None:
        raise HTTPException(status_code=400, detail="account missing for chat")
    client = DevinClient(acc.api_key)

    try:
        await client.send_message(chat.devin_session_id, full_message)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Devin API error {e.response.status_code}: {e.response.text[:300]}",
        ) from e

    user_msg = Message(
        chat_id=chat.id,
        role="user",
        content=message,
        attachments_json=json.dumps(attachments) if attachments else None,
    )
    db.add(user_msg)
    chat.status = "working"
    db.commit()
    db.refresh(chat)
    return {**_chat_summary(chat), "messages": [_msg_dto(m) for m in chat.messages]}


@app.post("/api/chats/{chat_id}/refresh")
async def refresh_chat(
    chat_id: int,
    _: bool = Depends(auth.require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    chat = db.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    if not chat.devin_session_id:
        return {**_chat_summary(chat), "messages": [_msg_dto(m) for m in chat.messages]}

    acc = db.get(DevinAccount, chat.account_id)
    if acc is None:
        raise HTTPException(status_code=400, detail="account missing for chat")
    client = DevinClient(acc.api_key)
    try:
        payload = await client.get_session(chat.devin_session_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Devin API error {e.response.status_code}: {e.response.text[:300]}",
        ) from e
    upsert_messages_from_session(db, chat, payload)
    db.commit()
    db.refresh(chat)
    return {**_chat_summary(chat), "messages": [_msg_dto(m) for m in chat.messages]}


@app.delete("/api/chats/{chat_id}")
def delete_chat(
    chat_id: int,
    _: bool = Depends(auth.require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    chat = db.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    db.delete(chat)
    db.commit()
    return {"ok": True}


@app.post("/api/chats/{chat_id}/attachments")
async def attach_to_chat(
    chat_id: int,
    file: UploadFile = File(...),
    _: bool = Depends(auth.require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    chat = db.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    acc = db.get(DevinAccount, chat.account_id)
    if acc is None:
        raise HTTPException(status_code=400, detail="account missing")

    content = await file.read()
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (>25MB)")

    client = DevinClient(acc.api_key)
    try:
        result = await client.upload_attachment(
            file.filename or "upload.bin", content, file.content_type or "application/octet-stream"
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Devin upload error {e.response.status_code}: {e.response.text[:300]}",
        ) from e

    return {"upload": result, "size": len(content), "mime": file.content_type}


@app.post("/api/standalone/attachments")
async def standalone_upload(
    file: UploadFile = File(...),
    account_id: Optional[int] = Form(default=None),
    _: bool = Depends(auth.require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Upload an attachment before a chat exists (used when starting a new chat
    that includes a photo)."""
    if account_id is not None:
        acc = db.get(DevinAccount, int(account_id))
    else:
        acc = get_default_account(db)
    if acc is None:
        raise HTTPException(status_code=400, detail="no devin account configured")

    content = await file.read()
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (>25MB)")

    client = DevinClient(acc.api_key)
    try:
        result = await client.upload_attachment(
            file.filename or "upload.bin", content, file.content_type or "application/octet-stream"
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Devin upload error {e.response.status_code}: {e.response.text[:300]}",
        ) from e
    return {"upload": result, "size": len(content), "mime": file.content_type}


@app.post("/api/chats/{chat_id}/export-github")
async def export_chat_github(
    chat_id: int,
    payload: dict[str, Any] | None = None,
    _: bool = Depends(auth.require_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    chat = db.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    payload = payload or {}
    repo = (payload.get("repo") or "").strip() or None
    branch = (payload.get("branch") or "main").strip() or "main"
    path = (payload.get("path") or "").strip() or None
    try:
        result = await github_export.export_chat_to_repo(
            chat, repo=repo, branch=branch, path=path
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"GitHub error {e.response.status_code}: {e.response.text[:300]}",
        ) from e
    return result


@app.get("/api/config")
def get_config(_: bool = Depends(auth.require_auth)) -> dict[str, Any]:
    s = get_settings()
    return {
        "github_default_repo": s.github_default_repo,
        "github_token_set": bool(s.github_token),
    }
