"""Gateway + Herdr + Turnstone stack metrics for tok-tua."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

# Reuse Headroom / LiteLLM / Herdr collectors from grok-tua.
from grok_tua.stack_metrics import (  # noqa: F401
    ensure_gateway_env,
    fetch_headroom_status,
    fetch_herdr_status,
    fetch_litellm_status,
    fetch_stack_metrics as _fetch_gateway_stack,
    format_stack_metrics as _format_gateway_stack,
)

DEFAULT_TURNSTONE = "http://127.0.0.1:8090"


def _http_json(url: str, *, timeout: float = 2.5, key: str = "") -> Tuple[Optional[Any], Optional[str], int]:
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            code = getattr(resp, "status", 200) or 200
            if not body.strip():
                return None, None, code
            try:
                return json.loads(body), None, code
            except json.JSONDecodeError:
                return body, None, code
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}", exc.code
    except Exception as exc:
        return None, str(exc), 0


def fetch_turnstone_status(base: str | None = None) -> Dict[str, Any]:
    """Best-effort Turnstone health (GPU-host primary :8090)."""
    base = (base or os.environ.get("TURNSTONE_BASE") or DEFAULT_TURNSTONE).rstrip("/")
    # Prefer lightweight openapi/docs probe; models may need auth.
    data, err, code = _http_json(f"{base}/openapi.json", timeout=2.0)
    if code == 200 and isinstance(data, dict):
        info = data.get("info") or {}
        return {
            "base": base,
            "ready": True,
            "version": info.get("version") or "",
            "title": info.get("title") or "turnstone",
            "error": None,
            "api": True,
        }
    # Fallback: root HTML
    try:
        req = urllib.request.Request(base + "/", headers={"Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            ok = (getattr(resp, "status", 200) or 200) < 500
            return {
                "base": base,
                "ready": ok,
                "version": "",
                "title": "turnstone",
                "error": None if ok else f"status {getattr(resp, 'status', 0)}",
                "api": False,
            }
    except Exception as exc:
        return {
            "base": base,
            "ready": False,
            "version": "",
            "title": "turnstone",
            "error": err or str(exc),
            "api": False,
            "code": code,
        }


def fetch_stack_metrics(
    *,
    billing: Optional[Dict[str, Any]] = None,
    with_versions: bool = True,
    with_credits: bool = True,
) -> Dict[str, Any]:
    """Headroom + LiteLLM + Herdr + Turnstone + version board + cloud credits."""
    turnstone = fetch_turnstone_status()
    stack = _fetch_gateway_stack(
        billing=billing,
        with_versions=with_versions,
        with_credits=with_credits,
        turnstone=turnstone,
    )
    stack["turnstone"] = turnstone
    return stack


def format_stack_metrics(
    stack: Dict[str, Any],
    *,
    markup: bool = True,
    session: Optional[Dict[str, Any]] = None,
    billing: Optional[Dict[str, Any]] = None,
    show_versions: bool = True,
    show_credits: bool = True,
) -> str:
    # Version board already includes Turnstone under "other" when present.
    return _format_gateway_stack(
        stack,
        markup=markup,
        session=session,
        billing=billing,
        show_versions=show_versions,
        show_credits=show_credits,
    )
