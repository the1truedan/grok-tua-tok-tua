"""Generic session hint (no provider-specific files)."""

from __future__ import annotations

from typing import Any


def generic_hint(cli_id: str) -> dict[str, Any]:
    return {
        "available": False,
        "cli": cli_id,
        "note": "session burn via Headroom/LiteLLM spend only for this CLI",
    }
