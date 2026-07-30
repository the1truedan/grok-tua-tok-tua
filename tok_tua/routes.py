"""Headroom manager-* model routes for tok-tua."""

from __future__ import annotations

from typing import Any

# Mirrors tools/launch_pad.MODELS (keep IDs in sync for Deck parity).
ROUTES: list[dict[str, Any]] = [
    {
        "id": "manager-auto",
        "label": "Auto · best local host (GPU-host preferred)",
        "cloud": False,
        "phi_safe": True,
        "description": "Headroom → orchestrator places to gpu-host/mac-client/nas-host",
    },
    {
        "id": "manager-code",
        "label": "Code · implement / debug / tools",
        "cloud": False,
        "phi_safe": True,
        "description": "Coding workers; prefer NVIDIA GPU host",
    },
    {
        "id": "manager-reason",
        "label": "Reason · plan / deep think",
        "cloud": False,
        "phi_safe": True,
        "description": "Reasoning/planning local role",
    },
    {
        "id": "manager-review",
        "label": "Review · second pass",
        "cloud": False,
        "phi_safe": True,
        "description": "Local review/audit pass",
    },
    {
        "id": "manager-vision",
        "label": "Vision · OCR / image understanding",
        "cloud": False,
        "phi_safe": True,
        "description": "Prefer LAN host vision workers",
    },
    {
        "id": "manager-phi-local",
        "label": "PHI · medical only (no cloud)",
        "cloud": False,
        "phi_safe": True,
        "description": "Never routes to cloud",
    },
    {
        "id": "manager-embed",
        "label": "Embed · vectors / search",
        "cloud": False,
        "phi_safe": True,
        "description": "Local embeddings",
    },
    {
        "id": "manager-openrouter-free",
        "label": "Free cloud · OpenRouter (public only)",
        "cloud": True,
        "phi_safe": False,
        "description": "Explicit free cloud; no PHI",
    },
    {
        "id": "manager-grok-paid",
        "label": "Paid · Grok (consent)",
        "cloud": True,
        "phi_safe": False,
        "description": "Requires cloud consent; no PHI",
    },
    {
        "id": "manager-gemini-paid",
        "label": "Paid · Gemini (consent)",
        "cloud": True,
        "phi_safe": False,
        "description": "Requires cloud consent; may hit quota",
    },
    {
        "id": "manager-claude-paid",
        "label": "Paid · Claude / Anthropic (consent)",
        "cloud": True,
        "phi_safe": False,
        "description": "Requires cloud consent; no PHI; QQQ1",
    },
    {
        "id": "manager-codex-paid",
        "label": "Paid · OpenAI (consent)",
        "cloud": True,
        "phi_safe": False,
        "description": "Requires cloud consent; no PHI; QQQ1",
    },
    {
        "id": "manager-hf-paid",
        "label": "Paid · Hugging Face Inference (consent)",
        "cloud": True,
        "phi_safe": False,
        "description": "Requires cloud consent; no PHI; QQQ1",
    },
]

KNOWN_ROUTES = {r["id"] for r in ROUTES}
DEFAULT_ROUTE = "manager-auto"


def get_route(model_id: str) -> dict[str, Any] | None:
    for row in ROUTES:
        if row["id"] == model_id:
            return dict(row)
    return None


def assert_model_allowed(
    model_id: str,
    *,
    data_class: str = "public_code",
) -> None:
    """Raise ValueError if model is unknown or forbidden for data_class."""
    route = get_route(model_id)
    if route is None:
        known = ", ".join(sorted(KNOWN_ROUTES))
        raise ValueError(f"unknown model route {model_id!r}; known: {known}")
    if data_class.lower() in {"phi", "private_personal"} and not route.get("phi_safe", True):
        raise ValueError(
            f"model {model_id!r} is cloud / non-PHI-safe; refused for data_class={data_class!r}"
        )
    if data_class.lower() == "phi" and route.get("cloud"):
        raise ValueError(f"cloud model {model_id!r} forbidden for PHI")


def routes_public() -> list[dict[str, Any]]:
    return [dict(r) for r in ROUTES]
