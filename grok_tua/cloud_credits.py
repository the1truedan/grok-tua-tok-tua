"""
Best-effort cloud credit / quota tally for grok-tua + tok-tua.

Sources differ by vendor — never invent balances:

| Provider   | Probe |
|------------|--------|
| SuperGrok  | unified.jsonl billing (caller passes billing dict) |
| OpenRouter | GET https://openrouter.ai/api/v1/auth/key |
| Gemini     | key + models list (no free remaining-credit API) |
| OpenAI     | credit_grants if authorized; else key-present only |
| Claude     | ANTHROPIC_API_KEY presence (usage API needs admin) |
| xAI API    | models list when XAI_API_KEY set (≠ SuperGrok OAuth) |

Env keys loaded from GATEWAY_ENV_FILE / this repo's own .env when unset.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from grok_tua.stack_metrics import ensure_gateway_env


def _http_json(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 4.0,
) -> Tuple[Optional[Any], Optional[str], int]:
    req = urllib.request.Request(url, headers=headers or {"Accept": "application/json"})
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
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:160]
        except Exception:
            detail = str(exc)
        return None, f"HTTP {exc.code}: {detail}", exc.code
    except Exception as exc:
        return None, str(exc), 0


def _key(*names: str) -> str:
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return ""


def fetch_openrouter_credits() -> Dict[str, Any]:
    key = _key("OPENROUTER_API_KEY")
    if not key:
        return {
            "id": "openrouter",
            "label": "OpenRouter",
            "available": False,
            "status": "no_key",
            "summary": "no OPENROUTER_API_KEY",
        }
    data, err, code = _http_json(
        "https://openrouter.ai/api/v1/auth/key",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    if not isinstance(data, dict) or "data" not in data:
        return {
            "id": "openrouter",
            "label": "OpenRouter",
            "available": False,
            "status": "error",
            "summary": err or f"HTTP {code}",
            "error": err,
        }
    d = data.get("data") or {}
    limit = d.get("limit")
    remaining = d.get("limit_remaining")
    usage = d.get("usage")
    usage_m = d.get("usage_monthly")
    free = d.get("is_free_tier")
    reset = d.get("limit_reset") or ""
    bits = []
    if remaining is not None and limit is not None:
        bits.append(f"${remaining:g}/${limit:g} left")
    elif remaining is not None:
        bits.append(f"${remaining:g} left")
    if usage_m is not None:
        bits.append(f"used mo ${float(usage_m):g}")
    elif usage is not None:
        bits.append(f"used ${float(usage):g}")
    if free:
        bits.append("free-tier")
    if reset:
        bits.append(f"reset {reset}")
    return {
        "id": "openrouter",
        "label": "OpenRouter",
        "available": True,
        "status": "ok",
        "limit": limit,
        "limit_remaining": remaining,
        "usage": usage,
        "usage_monthly": usage_m,
        "is_free_tier": free,
        "limit_reset": reset,
        "summary": " · ".join(bits) if bits else "key ok",
    }


def fetch_gemini_status() -> Dict[str, Any]:
    key = _key("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_AI_API_KEY")
    if not key:
        return {
            "id": "gemini",
            "label": "Gemini",
            "available": False,
            "status": "no_key",
            "summary": "no GEMINI_API_KEY",
        }
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}&pageSize=1"
    data, err, code = _http_json(url)
    if code == 200 and isinstance(data, dict):
        # Full count optional second call skipped — keep probe light
        models, _, _ = _http_json(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        )
        n = len((models or {}).get("models") or []) if isinstance(models, dict) else 0
        return {
            "id": "gemini",
            "label": "Gemini",
            "available": True,
            "status": "ok",
            "model_count": n,
            "summary": f"key ok · models≈{n} · remaining $ n/a (Google AI Studio UI)",
            "note": "Quota/credits live in Google AI Studio / Cloud billing, not this API",
        }
    return {
        "id": "gemini",
        "label": "Gemini",
        "available": False,
        "status": "error",
        "summary": err or f"HTTP {code}",
        "error": err,
    }


def fetch_openai_status() -> Dict[str, Any]:
    key = _key("OPENAI_API_KEY")
    # Avoid treating LiteLLM master key as OpenAI cloud
    litellm = _key("LITELLM_MASTER_KEY")
    if not key:
        return {
            "id": "openai",
            "label": "ChatGPT/OpenAI",
            "available": False,
            "status": "no_key",
            "summary": "no OPENAI_API_KEY",
        }
    if litellm and key == litellm:
        return {
            "id": "openai",
            "label": "ChatGPT/OpenAI",
            "available": False,
            "status": "gateway_key",
            "summary": "OPENAI_API_KEY is LiteLLM master (not OpenAI cloud)",
        }
    data, err, code = _http_json(
        "https://api.openai.com/v1/dashboard/billing/credit_grants",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    if isinstance(data, dict) and (
        "total_available" in data or "total_granted" in data or "data" in data
    ):
        avail = data.get("total_available")
        granted = data.get("total_granted")
        used = data.get("total_used")
        bits = []
        if avail is not None:
            bits.append(f"${float(avail):.2f} avail")
        if used is not None:
            bits.append(f"used ${float(used):.2f}")
        if granted is not None:
            bits.append(f"granted ${float(granted):.2f}")
        return {
            "id": "openai",
            "label": "ChatGPT/OpenAI",
            "available": True,
            "status": "ok",
            "total_available": avail,
            "total_used": used,
            "total_granted": granted,
            "summary": " · ".join(bits) if bits else "billing ok",
        }
    # models probe (key valid but no billing scope)
    mdata, merr, mcode = _http_json(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    if mcode == 200 and isinstance(mdata, dict):
        n = len(mdata.get("data") or [])
        return {
            "id": "openai",
            "label": "ChatGPT/OpenAI",
            "available": True,
            "status": "key_ok_no_billing",
            "model_count": n,
            "summary": f"key ok · models={n} · $ n/a (need billing scope / platform UI)",
        }
    return {
        "id": "openai",
        "label": "ChatGPT/OpenAI",
        "available": False,
        "status": "error",
        "summary": err or merr or f"HTTP {code or mcode}",
        "error": err or merr,
    }


def fetch_claude_status() -> Dict[str, Any]:
    key = _key("ANTHROPIC_API_KEY")
    if not key:
        return {
            "id": "claude",
            "label": "Claude",
            "available": False,
            "status": "no_key",
            "summary": "no ANTHROPIC_API_KEY · CLI soft-fail until account",
        }
    # Lightweight: message count not available without spend; hit models if any
    data, err, code = _http_json(
        "https://api.anthropic.com/v1/models",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Accept": "application/json",
        },
    )
    if code == 200 and isinstance(data, dict):
        n = len(data.get("data") or data.get("models") or [])
        return {
            "id": "claude",
            "label": "Claude",
            "available": True,
            "status": "ok",
            "model_count": n,
            "summary": f"key ok · models≈{n} · remaining $ → console.anthropic.com",
        }
    if code in (401, 403):
        return {
            "id": "claude",
            "label": "Claude",
            "available": False,
            "status": "auth",
            "summary": err or "auth failed",
        }
    # Some keys reject models list but work for messages — still mark key present
    return {
        "id": "claude",
        "label": "Claude",
        "available": True,
        "status": "key_present",
        "summary": f"key set · probe {err or code} · usage in Anthropic console",
        "error": err,
    }


def fetch_xai_api_status() -> Dict[str, Any]:
    """API key path (manager-grok-paid) — distinct from SuperGrok OAuth quota."""
    key = _key("XAI_API_KEY")
    if not key:
        return {
            "id": "xai_api",
            "label": "xAI API",
            "available": False,
            "status": "no_key",
            "summary": "no XAI_API_KEY",
        }
    data, err, code = _http_json(
        "https://api.x.ai/v1/models",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    if code == 200 and isinstance(data, dict):
        n = len(data.get("data") or [])
        return {
            "id": "xai_api",
            "label": "xAI API",
            "available": True,
            "status": "ok",
            "model_count": n,
            "summary": f"key ok · models={n} · $ via console.x.ai (≠ SuperGrok OAuth)",
        }
    return {
        "id": "xai_api",
        "label": "xAI API",
        "available": False,
        "status": "error",
        "summary": err or f"HTTP {code}",
        "error": err,
    }


def supergrok_from_billing(billing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    bill = billing or {}
    if not bill.get("available"):
        return {
            "id": "supergrok",
            "label": "SuperGrok",
            "available": False,
            "status": "unavailable",
            "summary": bill.get("error") or "run /usage in Grok TUI",
        }
    left = bill.get("usage_left_percent")
    used = bill.get("credit_usage_percent")
    prepaid = bill.get("prepaid_balance")
    tier = bill.get("subscription_tier") or "SuperGrok"
    bits = []
    if left is not None:
        bits.append(f"~{left:.0f}% left")
    if used is not None:
        bits.append(f"used {used:.0f}%")
    if prepaid is not None and prepaid > 0:
        bits.append(f"prepaid ${prepaid:.2f}")
    bits.append(str(tier))
    return {
        "id": "supergrok",
        "label": "SuperGrok",
        "available": True,
        "status": "ok",
        "usage_left_percent": left,
        "credit_usage_percent": used,
        "prepaid_balance": prepaid,
        "summary": " · ".join(bits),
    }


def fetch_cloud_credits(
    *,
    billing: Optional[Dict[str, Any]] = None,
    include: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Collect credit/status rows for dashboards.

    *include*: optional subset of ids:
      supergrok, openrouter, gemini, openai, claude, xai_api
    Default = all.
    """
    ensure_gateway_env()
    want = set(include) if include else None
    rows: List[Dict[str, Any]] = []

    def add(row: Dict[str, Any]) -> None:
        if want is None or row["id"] in want:
            rows.append(row)

    add(supergrok_from_billing(billing))
    add(fetch_openrouter_credits())
    add(fetch_gemini_status())
    add(fetch_openai_status())
    add(fetch_claude_status())
    add(fetch_xai_api_status())

    # Switch suggestion when SuperGrok is low
    sg = next((r for r in rows if r["id"] == "supergrok"), {})
    left = sg.get("usage_left_percent")
    tips: List[str] = []
    if isinstance(left, (int, float)) and left <= 25:
        orow = next((r for r in rows if r["id"] == "openrouter"), {})
        grow = next((r for r in rows if r["id"] == "gemini"), {})
        if orow.get("available"):
            tips.append("SuperGrok low → try tok-tua --cli openrouter-wrap (public only)")
        if grow.get("available"):
            tips.append("or tok-tua --cli gemini-wrap (consent · public)")
        tips.append("gateway local: --model manager-auto / manager-phi-local")

    return {
        "rows": rows,
        "tips": tips,
        "generated": True,
    }


def format_cloud_credits(
    credits: Dict[str, Any],
    *,
    markup: bool = True,
) -> str:
    rows = credits.get("rows") or []
    lines = ["Credits   cloud tally (best-effort; vendor UIs SoR)"]
    for r in rows:
        label = (r.get("label") or r.get("id") or "?").ljust(10)
        summary = r.get("summary") or r.get("status") or "—"
        if markup and r.get("status") in ("ok", "key_ok_no_billing", "key_present"):
            line = f"  {label} {summary}"
        elif markup and r.get("status") in ("no_key", "unavailable", "gateway_key"):
            line = f"  [dim]{label} {summary}[/dim]"
        elif markup:
            line = f"  [yellow]{label} {summary}[/yellow]"
        else:
            line = f"  {label} {summary}"
        lines.append(line)
    for t in credits.get("tips") or []:
        lines.append(f"  → {t}")
    return "\n".join(lines)
