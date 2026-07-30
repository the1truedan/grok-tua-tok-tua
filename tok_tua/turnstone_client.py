"""Thin Turnstone REST client (GPU-host workstream API reveal)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional
from urllib.parse import quote

DEFAULT_BASE = "http://127.0.0.1:8090"


class TurnstoneClient:
    def __init__(self, base_url: str | None = None, *, timeout: float = 15.0, token: str = "") -> None:
        self.base_url = (base_url or os.environ.get("TURNSTONE_BASE") or DEFAULT_BASE).rstrip("/")
        self.timeout = timeout
        self.token = token or os.environ.get("TURNSTONE_TOKEN") or os.environ.get("LITELLM_MASTER_KEY") or ""

    def _headers(self, *, json_body: bool = False) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if json_body:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers=self._headers(json_body=body is not None),
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                code = getattr(resp, "status", 200) or 200
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
            code = exc.code
            return {"ok": False, "status": code, "error": raw[:500], "result": None}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {"ok": False, "status": 0, "error": str(exc), "result": None}

        parsed: Any
        try:
            parsed = json.loads(raw) if raw.strip() else None
        except json.JSONDecodeError:
            parsed = raw[:1000]
        return {"ok": 200 <= code < 300, "status": code, "result": parsed, "error": None}

    def health(self) -> dict[str, Any]:
        openapi = self._request("GET", "/openapi.json")
        if openapi.get("ok") and isinstance(openapi.get("result"), dict):
            info = (openapi["result"] or {}).get("info") or {}
            return {
                "ok": True,
                "base": self.base_url,
                "version": info.get("version"),
                "title": info.get("title"),
            }
        return {"ok": False, "base": self.base_url, "error": openapi.get("error") or "unreachable"}

    def models(self) -> dict[str, Any]:
        return self._request("GET", "/v1/api/models")

    def list_workstreams(self) -> dict[str, Any]:
        return self._request("GET", "/v1/api/workstreams")

    def new_workstream(self, *, title: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if title:
            body["title"] = title
        # API may accept empty body for defaults
        return self._request("POST", "/v1/api/workstreams/new", body=body or None)

    def send(self, ws_id: str, message: str) -> dict[str, Any]:
        safe = quote(str(ws_id), safe="")
        # Common Turnstone shape: { "content": "..." } or { "message": "..." }
        # Try content first; callers can inspect error.
        return self._request(
            "POST",
            f"/v1/api/workstreams/{safe}/send",
            body={"content": message, "message": message, "text": message},
        )

    def history(self, ws_id: str) -> dict[str, Any]:
        safe = quote(str(ws_id), safe="")
        return self._request("GET", f"/v1/api/workstreams/{safe}/history")


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Turnstone REST reveal (GPU-host :8090)")
    parser.add_argument("--base", default=None, help="Turnstone base URL")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="OpenAPI / health probe")
    sub.add_parser("models", help="List model aliases")
    sub.add_parser("ws-list", help="List workstreams")

    p_new = sub.add_parser("ws-new", help="Create workstream")
    p_new.add_argument("--title", default=None)

    p_send = sub.add_parser("send", help="Send message to workstream")
    p_send.add_argument("ws_id")
    p_send.add_argument("message")

    p_hist = sub.add_parser("history", help="Workstream history")
    p_hist.add_argument("ws_id")

    args = parser.parse_args(argv)
    client = TurnstoneClient(args.base)

    if args.cmd == "health":
        out = client.health()
    elif args.cmd == "models":
        out = client.models()
    elif args.cmd == "ws-list":
        out = client.list_workstreams()
    elif args.cmd == "ws-new":
        out = client.new_workstream(title=args.title)
    elif args.cmd == "send":
        out = client.send(args.ws_id, args.message)
    elif args.cmd == "history":
        out = client.history(args.ws_id)
    else:
        parser.error(f"unknown command {args.cmd}")
        return 2

    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("ok", True) is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
