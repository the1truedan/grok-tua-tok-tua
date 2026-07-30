"""Codex session index / latest rollout hint."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def codex_session_hint(home: Path | None = None) -> dict[str, Any]:
    root = (home or Path.home()) / ".codex"
    index = root / "session_index.jsonl"
    if not index.is_file():
        return {"available": False, "cli": "codex", "error": "no session_index.jsonl"}
    last: dict[str, Any] | None = None
    try:
        for line in index.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
    except OSError as exc:
        return {"available": False, "cli": "codex", "error": str(exc)}
    if not last:
        return {"available": False, "cli": "codex", "error": "empty index"}
    return {
        "available": True,
        "cli": "codex",
        "id": last.get("id"),
        "thread_name": last.get("thread_name") or last.get("title") or "",
        "updated_at": last.get("updated_at") or "",
    }
