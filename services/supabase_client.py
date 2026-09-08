"""Thin PostgREST client for Supabase over httpx. No SDK dependency; every call is one HTTP request.

    client = SupabaseClient(url, api_key=service_role_key)             # server-side
    client = SupabaseClient(url, api_key=anon_key, bearer=user_jwt)    # per-request, RLS applies
    rows = client.select("runs", {"loan_id": "eq.LN-1", "order": "completed_at.desc", "limit": 5})
    client.upsert("loans", [{"loan_id": "LN-1"}], on_conflict="loan_id")
    client.rpc("search_findings", {"q": "paystub", "max_rows": 20})

Errors raise SupabaseError with the status code and PostgREST message. Keys are never logged.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx


class SupabaseError(RuntimeError):
    def __init__(self, status: int, message: str, path: str = "") -> None:
        super().__init__(f"Supabase {status} on {path}: {message}")
        self.status = status
        self.message = message
        self.path = path


class SupabaseClient:
    def __init__(
        self,
        url: str,
        api_key: str,
        bearer: Optional[str] = None,
        *,
        timeout: float = 20.0,
        transport: Optional[httpx.BaseTransport] = None,
        schema: str = "public",
    ) -> None:
        if not url or not api_key:
            raise ValueError("SupabaseClient requires url and api_key")
        self.base = url.rstrip("/") + "/rest/v1"
        self._headers = {
            "apikey": api_key,
            "Authorization": f"Bearer {bearer or api_key}",
            "Accept-Profile": schema,
            "Content-Profile": schema,
            "Content-Type": "application/json",
        }
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def with_bearer(self, bearer: str) -> "SupabaseClient":
        """Same connection settings, different Authorization (per-request user token)."""
        clone = SupabaseClient.__new__(SupabaseClient)
        clone.base = self.base
        clone._headers = {**self._headers, "Authorization": f"Bearer {bearer}"}
        clone._http = self._http
        return clone

    def close(self) -> None:
        self._http.close()

    # ---- core
    def _request(self, method: str, path: str, *, params=None, json=None, prefer: Optional[str] = None) -> Any:
        headers = dict(self._headers)
        if prefer:
            headers["Prefer"] = prefer
        resp = self._http.request(method, f"{self.base}/{path}", params=params, json=json, headers=headers)
        if resp.status_code >= 400:
            try:
                detail = resp.json()
                message = detail.get("message") or detail.get("hint") or resp.text
            except ValueError:
                message = resp.text
            raise SupabaseError(resp.status_code, str(message)[:500], path)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def select(self, table: str, params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        params = {"select": "*", **(params or {})}
        data = self._request("GET", table, params=params)
        return data or []

    def select_one(self, table: str, params: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        rows = self.select(table, {**(params or {}), "limit": 1})
        return rows[0] if rows else None

    def insert(self, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return []
        return self._request("POST", table, json=rows, prefer="return=representation") or []

    def upsert(self, table: str, rows: list[dict[str, Any]], on_conflict: str) -> list[dict[str, Any]]:
        if not rows:
            return []
        return self._request(
            "POST", table, params={"on_conflict": on_conflict}, json=rows,
            prefer="resolution=merge-duplicates,return=representation",
        ) or []

    def update(self, table: str, match: dict[str, Any], values: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request("PATCH", table, params=match, json=values, prefer="return=representation") or []

    def delete(self, table: str, match: dict[str, Any]) -> None:
        if not match:
            raise ValueError("refusing to delete without a filter")
        self._request("DELETE", table, params=match, prefer="return=minimal")

    def rpc(self, function: str, args: Optional[dict[str, Any]] = None) -> Any:
        return self._request("POST", f"rpc/{function}", json=args or {})
