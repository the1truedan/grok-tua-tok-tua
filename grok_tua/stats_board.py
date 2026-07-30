"""
Shared stats board for grok-tua, tok-tua, and Commodore Maintenance Deck.

Returns JSON-serializable:
  - ordered stack versions (Headroom → LiteLLM → Herdr → CLIs → OWUI → other)
  - multi-cloud credit rows
  - coding-agent provider list with versions
  - LiteLLM spend slice
  - first-class gateway service status (incl. Grafana + Prompt-I/O)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _service_row(
    sid: str,
    label: str,
    *,
    ok: bool,
    summary: str,
    base: str | None = None,
    version: str | None = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "id": sid,
        "label": label,
        "status": "ok" if ok else "down",
        "ok": ok,
        "summary": summary,
    }
    if base:
        row["base"] = base
    if version:
        row["version"] = version
    if extra:
        row.update(extra)
    return row


def _pick_version_row(versions: Dict[str, Any], sid: str) -> Optional[Dict[str, Any]]:
    for bucket in ("primary", "other", "all"):
        for row in versions.get(bucket) or []:
            if row.get("id") == sid:
                return row
    return None


def fetch_stats_board(
    *,
    billing: Optional[Dict[str, Any]] = None,
    with_provider_versions: bool = True,
) -> Dict[str, Any]:
    """Aggregate CLI versions + cloud credits for all MANAGER stats surfaces."""
    from grok_tua.stack_metrics import fetch_stack_metrics

    if billing is None:
        try:
            from context.grok_credit_monitor import fetch_billing_from_unified_log

            billing = fetch_billing_from_unified_log()
        except Exception:
            billing = None

    turnstone: Optional[Dict[str, Any]] = None
    try:
        from tok_tua.stack_metrics import fetch_turnstone_status

        turnstone = fetch_turnstone_status()
    except Exception as exc:
        turnstone = {
            "base": "",
            "ready": False,
            "version": "",
            "error": str(exc)[:160],
        }

    stack = fetch_stack_metrics(
        billing=billing,
        with_versions=True,
        with_credits=True,
        turnstone=turnstone,
    )
    if turnstone is not None:
        stack["turnstone"] = turnstone

    ll = stack.get("litellm") or {}
    hr = stack.get("headroom") or {}
    her = stack.get("herdr") or {}
    ts = stack.get("turnstone") or turnstone or {}
    versions = stack.get("versions") or {}
    credits = stack.get("credits") or {}

    providers: List[Dict[str, Any]] = []
    provider_error: Optional[str] = None
    try:
        from tok_tua.providers import enrich_provider_versions, provider_board

        if with_provider_versions:
            providers = enrich_provider_versions(with_versions=True)
        board = provider_board(with_versions=True)
    except Exception as exc:
        board = {
            "tools_total": 0,
            "tools_available": 0,
            "tools_missing": 0,
            "tools_soft": 0,
            "providers": [],
            "error": str(exc)[:200],
        }
        provider_error = str(exc)[:200]

    # First-class service health (tok-tua style + Grafana + Prompt-I/O)
    grafana_row = _pick_version_row(versions, "grafana") or {}
    prompt_io_row = _pick_version_row(versions, "prompt_io") or {}

    services = [
        _service_row(
            "headroom",
            "Headroom",
            ok=bool(hr.get("ready")),
            summary=f"models={hr.get('model_count', 0)} · {hr.get('base') or ''}",
            base=hr.get("base"),
            extra={"model_count": hr.get("model_count")},
        ),
        _service_row(
            "litellm",
            "LiteLLM",
            ok=bool(ll.get("alive")),
            summary=f"models={ll.get('model_count', 0)} · {ll.get('base') or ''}",
            base=ll.get("base"),
            extra={"model_count": ll.get("model_count")},
        ),
        _service_row(
            "herdr",
            "Herdr",
            ok=bool(her.get("running")),
            summary=(
                (her.get("version") or her.get("error") or "n/a")
                if her.get("available") or her.get("running")
                else (her.get("error") or "not on PATH")
            ),
            version=her.get("version") or None,
            extra={"available": her.get("available"), "running": her.get("running")},
        ),
        _service_row(
            "turnstone",
            "Turnstone",
            ok=bool(ts.get("ready")),
            summary=(ts.get("version") or ts.get("error") or ts.get("base") or "n/a"),
            base=ts.get("base"),
            version=ts.get("version") or None,
        ),
        _service_row(
            "grafana",
            "Grafana",
            ok=(grafana_row.get("status") in ("ok", "ok_no_ver", "ready")),
            summary=str(grafana_row.get("summary") or grafana_row.get("status") or "unchecked"),
            base=grafana_row.get("base"),
        ),
        _service_row(
            "prompt_io",
            "Prompt-I/O",
            ok=(prompt_io_row.get("status") in ("ok", "ok_no_ver", "ready")),
            summary=str(prompt_io_row.get("summary") or prompt_io_row.get("status") or "unchecked"),
            base=prompt_io_row.get("base"),
        ),
    ]

    down = [s["id"] for s in services if not s.get("ok")]
    gateway_line = (
        f"path={stack.get('path')}  "
        f"headroom={'ok' if hr.get('ready') else 'down'}  "
        f"litellm={'ok' if ll.get('alive') else 'down'}  "
        f"herdr={'ok' if her.get('running') else 'n/a'}  "
        f"turnstone={'ok' if ts.get('ready') else 'down'}  "
        f"grafana={'ok' if services[4]['ok'] else 'down'}  "
        f"prompt-io={'ok' if services[5]['ok'] else 'down'}"
    )

    return {
        "schema": "manager.stats_board.v1",
        "path": stack.get("path"),
        "path_ok": stack.get("path_ok"),
        "has_key": stack.get("has_key"),
        "headroom": hr,
        "litellm": ll,
        "herdr": her,
        "turnstone": ts,
        "services": services,
        "services_down": down,
        "gateway_line": gateway_line,
        "spend": {
            "today": ll.get("spend_today"),
            "window": ll.get("spend_window"),
            "date": ll.get("spend_date"),
            "base": ll.get("base"),
        },
        "versions": versions,
        "credits": credits,
        "providers": providers or board.get("providers") or [],
        "provider_summary": {
            "tools_total": board.get("tools_total"),
            "tools_available": board.get("tools_available"),
            "tools_missing": board.get("tools_missing"),
            "tools_soft": board.get("tools_soft"),
            "hint": board.get("hint")
            or "tok-tua --cli <id> · python -m grok_tua.dashboard --check",
            "error": provider_error or board.get("error"),
        },
        "billing_available": bool((billing or {}).get("available")),
    }
