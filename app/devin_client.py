from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from .config import get_settings


class DevinClient:
    """Thin async wrapper around the Devin v1 API.

    Uses one Personal API Key (``apk_user_*``) per instance. The website may
    hold multiple ``DevinClient`` instances — one per Devin account the user
    has linked.
    """

    def __init__(self, api_key: str, base_url: Optional[str] = None) -> None:
        self.api_key = api_key
        self.base_url = (base_url or get_settings().devin_api_base).rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, headers=self._headers, timeout=60.0)

    async def create_session(
        self,
        prompt: str,
        *,
        title: Optional[str] = None,
        idempotent: bool = False,
        max_acu_limit: Optional[int] = None,
        tags: Optional[list[str]] = None,
        unlisted: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"prompt": prompt, "idempotent": idempotent, "unlisted": unlisted}
        if title:
            body["title"] = title
        if max_acu_limit:
            body["max_acu_limit"] = max_acu_limit
        if tags:
            body["tags"] = tags

        async with self._client() as client:
            r = await client.post("/v1/sessions", json=body)
            r.raise_for_status()
            return r.json()

    async def get_session(self, session_id: str) -> dict[str, Any]:
        async with self._client() as client:
            r = await client.get(f"/v1/session/{session_id}")
            r.raise_for_status()
            return r.json()

    async def send_message(self, session_id: str, message: str) -> Optional[dict[str, Any]]:
        async with self._client() as client:
            r = await client.post(
                f"/v1/sessions/{session_id}/message", json={"message": message}
            )
            r.raise_for_status()
            try:
                return r.json()
            except Exception:
                return None

    async def upload_attachment(self, filename: str, content: bytes, mime: str) -> dict[str, Any]:
        async with self._client() as client:
            files = {"file": (filename, content, mime)}
            r = await client.post("/v1/attachments", files=files)
            r.raise_for_status()
            try:
                data = r.json()
            except Exception:
                data = {"raw": r.text}
            return data

    async def whoami(self) -> dict[str, Any]:
        """Try a cheap call to validate the API key. Returns dict on success.

        We just hit the user info / list-secrets endpoint and treat any 2xx as
        a working key. If the endpoint shape changes, this still returns
        something the caller can show.
        """
        async with self._client() as client:
            for path in ("/v1/secrets",):
                try:
                    r = await client.get(path)
                except httpx.HTTPError:
                    continue
                if r.is_success:
                    try:
                        return {"ok": True, "endpoint": path, "data": r.json()}
                    except Exception:
                        return {"ok": True, "endpoint": path, "data": None}
                if r.status_code == 401:
                    return {"ok": False, "status": 401, "endpoint": path}
            return {"ok": False, "status": "unknown"}


async def gather_with_concurrency(n: int, *coros: Any) -> list[Any]:
    sem = asyncio.Semaphore(n)

    async def _run(c: Any) -> Any:
        async with sem:
            return await c

    return await asyncio.gather(*(_run(c) for c in coros))
