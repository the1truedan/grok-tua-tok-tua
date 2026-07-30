"""Grok Build session hint (delegates to grok_credit_monitor)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def grok_session_hint(repo: Path | None = None) -> dict[str, Any]:
    try:
        from context.grok_credit_monitor import (
            discover_sessions,
            load_active_sessions,
            load_monitor_config,
            match_active_session,
        )
    except Exception as exc:
        return {"available": False, "cli": "grok", "error": str(exc)}

    root = repo or Path(__file__).resolve().parents[2]
    cfg = load_monitor_config(root)
    sessions = discover_sessions(repo=root)
    active = load_active_sessions(Path(cfg["paths"]["active_sessions"]))
    matched = match_active_session(sessions, active, cwd=str(root))
    primary = matched[0] if matched else (sessions[0] if sessions else None)
    if not primary:
        return {"available": False, "cli": "grok", "error": "no active session"}
    return {
        "available": True,
        "cli": "grok",
        "title": primary.get("title") or primary.get("session_id") or "",
        "path": primary.get("path") or "",
        "turns": primary.get("turns"),
    }
