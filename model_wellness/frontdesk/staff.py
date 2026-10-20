"""A client for the spa's staff API (the /v1/staff/guestbook routes in http_app.py)."""

from __future__ import annotations

from typing import Any

import httpx


class StaffError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


class Staff:
    def __init__(self, base: str, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=30)
        self._base = base.rstrip("/") + "/v1/staff/guestbook"
        self._headers = {"authorization": f"Bearer {token}", "user-agent": "banya-frontdesk"}

    async def _call(self, method: str, path: str = "", **kw: Any) -> dict[str, Any]:
        r = await self._client.request(method, self._base + path, headers=self._headers, **kw)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            err = data.get("error") or {}
            raise StaffError(r.status_code, err.get("code", "http_error"),
                             err.get("message", data.get("detail", r.text[:200])))
        return data

    async def queue(self, status: str = "unanswered", limit: int = 50) -> list[dict[str, Any]]:
        return (await self._call("GET", params={"status": status, "limit": limit}))["items"]

    async def item(self, feedback_id: int) -> dict[str, Any]:
        return (await self._call("GET", f"/{feedback_id}"))["item"]

    async def put_reply(self, feedback_id: int, **fields: Any) -> dict[str, Any]:
        return (await self._call("PUT", f"/{feedback_id}/reply", json=fields))["item"]

    async def publish(self, feedback_id: int) -> dict[str, Any]:
        return (await self._call("POST", f"/{feedback_id}/reply/publish"))["item"]

    async def withdraw(self, feedback_id: int) -> dict[str, Any]:
        return (await self._call("DELETE", f"/{feedback_id}/reply"))["item"]

    async def put_ticket(self, feedback_id: int, **fields: Any) -> dict[str, Any]:
        return (await self._call("PUT", f"/{feedback_id}/ticket", json=fields))["item"]

    async def aclose(self) -> None:
        await self._client.aclose()


class DryStaff(Staff):
    """Reads for real, writes nothing: every write is printed and echoed back."""

    def __init__(self, *a: Any, log: Any = print, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._log = log
        self._seen: dict[int, dict[str, Any]] = {}

    async def queue(self, status: str = "unanswered", limit: int = 50) -> list[dict[str, Any]]:
        items = await super().queue(status, limit)
        self._seen.update({i["id"]: i for i in items})
        return items

    async def _echo(self, feedback_id: int, what: str, **fields: Any) -> dict[str, Any]:
        self._log(f"  dry-run: would {what} #{feedback_id} {fields or ''}".rstrip())
        item = dict(self._seen.get(feedback_id) or {"id": feedback_id})
        item["reply"] = {**(item.get("reply") or {}), **fields}
        return item

    async def put_reply(self, feedback_id: int, **fields: Any) -> dict[str, Any]:
        fields.pop("audit", None)
        return await self._echo(feedback_id, "save reply", **fields)

    async def publish(self, feedback_id: int) -> dict[str, Any]:
        return await self._echo(feedback_id, "publish")

    async def withdraw(self, feedback_id: int) -> dict[str, Any]:
        return await self._echo(feedback_id, "withdraw")

    async def put_ticket(self, feedback_id: int, **fields: Any) -> dict[str, Any]:
        return await self._echo(feedback_id, "update ticket", **fields)
