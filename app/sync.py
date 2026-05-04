from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Chat, Message


_USER_TYPES = {"initial_user_message", "user_message"}
_ASSISTANT_TYPES = {
    "devin_message",
    "agent_message",
    "assistant_message",
    "agent_chat_message",
}


def _classify_role(event_type: str | None, fallback: str = "system") -> str:
    if not event_type:
        return fallback
    et = event_type.lower()
    if et in _USER_TYPES or et.endswith("user_message"):
        return "user"
    if et in _ASSISTANT_TYPES or "devin" in et or "assistant" in et or "agent" in et:
        return "assistant"
    return "system"


def upsert_messages_from_session(
    db: Session, chat: Chat, session_payload: dict[str, Any]
) -> int:
    """Read the ``messages`` array from a v1 ``GET /v1/session/{id}`` payload and
    upsert each event into the local ``messages`` table.

    Returns the number of newly inserted messages.
    """
    events = session_payload.get("messages") or []
    if not isinstance(events, list):
        return 0

    existing_event_ids: set[str] = {
        row[0]
        for row in db.execute(
            select(Message.devin_event_id).where(
                Message.chat_id == chat.id, Message.devin_event_id.is_not(None)
            )
        ).all()
    }

    # Local rows that were saved optimistically (no event_id yet). On the next
    # refresh, when Devin returns the same message with an event_id, we adopt
    # that id onto the local row instead of inserting a duplicate.
    pending_unmatched: list[Message] = list(
        db.execute(
            select(Message)
            .where(Message.chat_id == chat.id, Message.devin_event_id.is_(None))
            .order_by(Message.id.asc())
        )
        .scalars()
        .all()
    )

    def _adopt_local(role: str, content: str) -> Message | None:
        for m in pending_unmatched:
            if m.role == role and (m.content or "").strip() == content.strip():
                pending_unmatched.remove(m)
                return m
        return None

    inserted = 0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        event_id = ev.get("event_id") or ev.get("id")
        if event_id and event_id in existing_event_ids:
            continue
        event_type = ev.get("type") or ev.get("event_type")
        role = _classify_role(event_type)
        content = ev.get("message") or ev.get("text") or ev.get("content") or ""
        if not isinstance(content, str):
            try:
                content = json.dumps(content, ensure_ascii=False)
            except Exception:
                content = str(content)

        adopted = _adopt_local(role, content) if role == "user" else None
        if adopted is not None:
            adopted.devin_event_id = event_id
            adopted.devin_event_type = event_type
            adopted.raw_json = json.dumps(ev, ensure_ascii=False)
            if event_id:
                existing_event_ids.add(event_id)
            continue

        msg = Message(
            chat_id=chat.id,
            role=role,
            content=content,
            devin_event_id=event_id,
            devin_event_type=event_type,
            raw_json=json.dumps(ev, ensure_ascii=False),
        )
        db.add(msg)
        inserted += 1
        if event_id:
            existing_event_ids.add(event_id)

    # also persist top-level status/title onto the chat
    status = session_payload.get("status_enum") or session_payload.get("status")
    if status and isinstance(status, str):
        chat.status = status[:64]
    title = session_payload.get("title")
    if title and isinstance(title, str):
        chat.title = title[:512]
    return inserted
