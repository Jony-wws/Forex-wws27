from __future__ import annotations

import base64
import re
from typing import Any

import httpx

from .config import get_settings
from .db import Chat


def _safe_filename(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")
    return s or "chat"


def render_chat_markdown(chat: Chat) -> str:
    lines: list[str] = []
    lines.append(f"# {chat.title}")
    lines.append("")
    lines.append(f"- Devin session: `{chat.devin_session_id or 'n/a'}`")
    if chat.devin_url:
        lines.append(f"- URL: {chat.devin_url}")
    lines.append(f"- Created: {chat.created_at.isoformat()}")
    lines.append(f"- Updated: {chat.updated_at.isoformat()}")
    lines.append("")
    lines.append("---")
    lines.append("")
    for m in chat.messages:
        role_label = {"user": "User", "assistant": "Devin", "system": "System"}.get(
            m.role, m.role.title()
        )
        ts = m.created_at.isoformat() if m.created_at else ""
        lines.append(f"## {role_label} — {ts}")
        lines.append("")
        lines.append(m.content or "")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


async def export_chat_to_repo(
    chat: Chat,
    *,
    repo: str | None = None,
    path: str | None = None,
    branch: str = "main",
    token: str | None = None,
) -> dict[str, Any]:
    """Commit ``render_chat_markdown(chat)`` to a path in a GitHub repo.

    Uses the GitHub Contents API. Creates or updates the file in one commit.
    """
    settings = get_settings()
    repo = repo or settings.github_default_repo
    token = token or settings.github_token
    if not repo:
        raise ValueError("No GitHub repo configured. Set GITHUB_DEFAULT_REPO or pass repo=...")
    if not token:
        raise ValueError("No GitHub token configured. Set GITHUB_TOKEN.")

    fname = _safe_filename(chat.title)
    path = path or f"chats/{chat.id:06d}-{fname}.md"

    body = render_chat_markdown(chat)
    encoded = base64.b64encode(body.encode("utf-8")).decode("ascii")

    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        sha: str | None = None
        r = await client.get(api, params={"ref": branch})
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                sha = data.get("sha")
        elif r.status_code not in (404,):
            r.raise_for_status()

        payload: dict[str, Any] = {
            "message": f"chat-export: {chat.title} (#{chat.id})",
            "content": encoded,
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha

        r2 = await client.put(api, json=payload)
        r2.raise_for_status()
        out = r2.json()

    commit = (out or {}).get("commit") or {}
    content = (out or {}).get("content") or {}
    return {
        "repo": repo,
        "path": path,
        "branch": branch,
        "commit_sha": commit.get("sha"),
        "html_url": content.get("html_url"),
    }
