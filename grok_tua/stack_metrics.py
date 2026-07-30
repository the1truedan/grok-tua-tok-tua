"""
Gateway stack metrics for grok-tua: Headroom + LiteLLM (+ optional Herdr status).

Spend / health are pulled from the same path agents use for conservation:
  clients → Headroom :8787 → LiteLLM :4000

Important: SuperGrok / Grok Build traffic hits xAI directly and does **not**
appear in LiteLLM spend. The dashboard labels gateway spend separately from
session $ and SuperGrok quota (passed into format_stack_metrics).

Environment (optional overrides):
  HEADROOM_BASE   default http://127.0.0.1:8787  (or a LAN gateway host's :8787)
  LITELLM_BASE    default http://127.0.0.1:4000
  LITELLM_SPEND_BASES  comma-separated bases to sum for spend (optional)
  LITELLM_MASTER_KEY / OPENAI_API_KEY
  GATEWAY_ENV_FILE  path to a gateway .env to load keys from (loads this
                    repo's own .env by default if unset)
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_HEADROOM = "http://127.0.0.1:8787"
DEFAULT_LITELLM = "http://127.0.0.1:4000"
AI_GATEWAY_ENV_CANDIDATES = (
    Path(os.environ.get("GATEWAY_ENV_FILE", "")),
    Path(__file__).resolve().parents[1] / ".env",
)


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass


def ensure_gateway_env() -> None:
    for candidate in AI_GATEWAY_ENV_CANDIDATES:
        if candidate and str(candidate) not in (".", ""):
            _load_env_file(candidate)


def _bases() -> Tuple[str, str, str]:
    ensure_gateway_env()
    headroom = os.environ.get("HEADROOM_BASE", DEFAULT_HEADROOM).rstrip("/")
    litellm = os.environ.get("LITELLM_BASE", DEFAULT_LITELLM).rstrip("/")
    # Prefer a configured LAN gateway host when local ports are down — optional
    # override (set NAS_HOST_IP in .env to enable).
    nas_host = os.environ.get("NAS_HOST_IP", "")
    if nas_host and os.environ.get("GROK_TUA_PREFER_NAS_HOST", "").lower() in ("1", "true", "yes"):
        headroom = os.environ.get("HEADROOM_BASE", f"http://{nas_host}:8787").rstrip("/")
        litellm = os.environ.get("LITELLM_BASE", f"http://{nas_host}:4000").rstrip("/")
    key = (
        os.environ.get("LITELLM_MASTER_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    )
    return headroom, litellm, key


def _litellm_spend_bases(primary: str) -> List[str]:
    """Bases used for spend aggregation (primary first, unique)."""
    raw = (os.environ.get("LITELLM_SPEND_BASES") or "").strip()
    bases: List[str] = []
    if raw:
        bases = [b.strip().rstrip("/") for b in raw.split(",") if b.strip()]
    else:
        bases = [primary]
        # When primary is local, optionally include a secondary LAN host's
        # ledger if reachable later (set NAS_HOST_IP in .env to enable).
        nas_host = os.environ.get("NAS_HOST_IP", "")
        nas_base = os.environ.get(
            "LITELLM_NAS_HOST_BASE", f"http://{nas_host}:4000" if nas_host else ""
        ).rstrip("/")
        if nas_base and primary.rstrip("/") != nas_base and os.environ.get(
            "LITELLM_SUM_NAS_HOST_SPEND", ""
        ).lower() in ("1", "true", "yes"):
            bases.append(nas_base)
    # Always ensure primary is first
    out: List[str] = []
    for b in [primary] + bases:
        b = b.rstrip("/")
        if b and b not in out:
            out.append(b)
    return out


def _http_json(
    url: str,
    *,
    key: str = "",
    timeout: float = 3.0,
) -> Tuple[Optional[Any], Optional[str], int]:
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
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            detail = str(exc)
        return None, f"HTTP {exc.code}: {detail}", exc.code
    except Exception as exc:
        return None, str(exc), 0


def _http_text(url: str, *, timeout: float = 3.0) -> Tuple[str, int]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace"), getattr(resp, "status", 200) or 200
    except urllib.error.HTTPError as exc:
        return "", exc.code
    except Exception:
        return "", 0


def parse_spend_logs(
    logs: Any,
    *,
    today: Optional[str] = None,
) -> Tuple[float, float, List[Dict[str, Any]]]:
    """Parse LiteLLM /global/spend/logs list into today, window, and day rows.

    *today* defaults to local calendar date (YYYY-MM-DD). Uses the row whose
    date matches *today* rather than assuming list order.
    """
    if today is None:
        today = date.today().isoformat()
    spend_days: List[Dict[str, Any]] = []
    spend_window = 0.0
    by_date: Dict[str, float] = {}

    if not isinstance(logs, list):
        return 0.0, 0.0, []

    for row in logs:
        if not isinstance(row, dict):
            continue
        try:
            spend = float(row.get("spend") or 0)
        except (TypeError, ValueError):
            spend = 0.0
        day = str(row.get("date") or row.get("startTime") or "")[:10]
        if not day:
            continue
        # Prefer higher spend if duplicate date rows appear
        prev = by_date.get(day)
        if prev is None or spend > prev:
            by_date[day] = spend
        spend_window += spend
        spend_days.append({"date": day, "spend": spend})

    spend_today = float(by_date.get(today, 0.0))
    # Stable last-7 by date key for display
    ordered = sorted(by_date.items(), key=lambda kv: kv[0])
    spend_days_out = [{"date": d, "spend": s} for d, s in ordered[-7:]]
    return spend_today, spend_window, spend_days_out


def fetch_headroom_status(base: str, key: str) -> Dict[str, Any]:
    ready_body, ready_code = _http_text(f"{base}/readyz")
    ok = ready_code == 200
    models: List[str] = []
    err = None
    if key:
        data, err, code = _http_json(f"{base}/v1/models", key=key)
        if isinstance(data, dict) and "data" in data:
            models = [m.get("id", "") for m in data.get("data") or [] if isinstance(m, dict)]
        elif code == 0 and not ok:
            err = err or "unreachable"
    return {
        "base": base,
        "ready": ok,
        "ready_code": ready_code,
        "ready_body": (ready_body or "")[:80],
        "model_count": len(models),
        "models_sample": models[:8],
        "error": err if not ok else None,
    }


def fetch_litellm_status(base: str, key: str) -> Dict[str, Any]:
    live_body, live_code = _http_text(f"{base}/health/liveliness")
    alive = live_code == 200 and "alive" in (live_body or "").lower()
    models: List[str] = []
    spend_days: List[Dict[str, Any]] = []
    spend_today = 0.0
    spend_window = 0.0
    err = None
    spend_sources: List[Dict[str, Any]] = []

    if key:
        data, m_err, _ = _http_json(f"{base}/v1/models", key=key)
        if isinstance(data, dict) and "data" in data:
            models = [m.get("id", "") for m in data.get("data") or [] if isinstance(m, dict)]
        elif m_err:
            err = m_err

        today = date.today().isoformat()
        total_today = 0.0
        total_window = 0.0
        merged_days: Dict[str, float] = {}
        for spend_base in _litellm_spend_bases(base):
            logs, s_err, code = _http_json(
                f"{spend_base}/global/spend/logs?limit=14", key=key, timeout=3.0
            )
            if isinstance(logs, list):
                t, w, days = parse_spend_logs(logs, today=today)
                total_today += t
                total_window += w
                for d in days:
                    merged_days[d["date"]] = merged_days.get(d["date"], 0.0) + float(
                        d.get("spend") or 0
                    )
                spend_sources.append(
                    {
                        "base": spend_base,
                        "spend_today": t,
                        "spend_window": w,
                        "ok": True,
                    }
                )
            else:
                spend_sources.append(
                    {
                        "base": spend_base,
                        "spend_today": 0.0,
                        "spend_window": 0.0,
                        "ok": False,
                        "error": s_err or f"HTTP {code}",
                    }
                )
                if spend_base == base.rstrip("/") and s_err and not err:
                    if "Enterprise" not in (s_err or ""):
                        err = s_err

        spend_today = total_today
        spend_window = total_window
        ordered = sorted(merged_days.items(), key=lambda kv: kv[0])
        spend_days = [{"date": d, "spend": s} for d, s in ordered[-7:]]

    return {
        "base": base,
        "alive": alive,
        "live_code": live_code,
        "model_count": len(models),
        "models_sample": models[:8],
        "spend_today": spend_today,
        "spend_window": spend_window,
        "spend_days": spend_days,
        "spend_sources": spend_sources,
        "spend_date": date.today().isoformat(),
        "ui": f"{base}/ui/login/",
        "error": err if not alive else None,
    }


def fetch_herdr_status() -> Dict[str, Any]:
    """Best-effort local Herdr status (agent multiplexer, not the model bus)."""
    herdr = os.environ.get("HERDR_BIN", "herdr")
    try:
        proc = subprocess.run(
            [herdr, "status"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        running = proc.returncode == 0 and "running" in out.lower()
        version = ""
        for line in out.splitlines():
            if "version:" in line.lower():
                version = line.split(":", 1)[-1].strip()
                break
        return {
            "available": True,
            "running": running,
            "version": version,
            "raw_preview": "\n".join(out.splitlines()[:12]),
            "error": None if running else (out.strip()[:160] or f"exit {proc.returncode}"),
        }
    except FileNotFoundError:
        return {
            "available": False,
            "running": False,
            "version": "",
            "raw_preview": "",
            "error": "herdr not on PATH",
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": False,
            "running": False,
            "version": "",
            "raw_preview": "",
            "error": str(exc),
        }


def fetch_stack_metrics(
    *,
    billing: Optional[Dict[str, Any]] = None,
    with_versions: bool = True,
    with_credits: bool = True,
    turnstone: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Snapshot of Headroom + LiteLLM (+ Herdr) for the dashboard.

    Optionally attaches ordered CLI/service version board and cloud credit tally.
    """
    headroom_base, litellm_base, key = _bases()
    headroom = fetch_headroom_status(headroom_base, key)
    litellm = fetch_litellm_status(litellm_base, key)
    herdr = fetch_herdr_status()

    # Path health: clients should hit Headroom which fronts LiteLLM
    if headroom.get("ready") and litellm.get("alive"):
        path = "headroom→litellm OK"
        path_ok = True
    elif litellm.get("alive") and not headroom.get("ready"):
        path = "litellm up; headroom down (bypass only)"
        path_ok = False
    elif headroom.get("ready") and not litellm.get("alive"):
        path = "headroom up; litellm down"
        path_ok = False
    else:
        path = "gateway unreachable"
        path_ok = False

    out: Dict[str, Any] = {
        "headroom": headroom,
        "litellm": litellm,
        "herdr": herdr,
        "path": path,
        "path_ok": path_ok,
        "has_key": bool(key),
    }
    if turnstone is not None:
        out["turnstone"] = turnstone

    if with_versions:
        try:
            from grok_tua.stack_versions import fetch_stack_versions

            out["versions"] = fetch_stack_versions(
                headroom=headroom,
                litellm=litellm,
                herdr=herdr,
                turnstone=turnstone or out.get("turnstone"),
            )
        except Exception as exc:
            out["versions"] = {"error": str(exc), "primary": [], "other": [], "all": []}

    if with_credits:
        try:
            from grok_tua.cloud_credits import fetch_cloud_credits

            out["credits"] = fetch_cloud_credits(billing=billing)
        except Exception as exc:
            out["credits"] = {"error": str(exc), "rows": [], "tips": []}

    return out


def _session_cost_usd(session: Optional[Dict[str, Any]]) -> Optional[float]:
    if not session:
        return None
    full = session.get("session_usage_full")
    if isinstance(full, dict) and full.get("costUsd") is not None:
        try:
            return float(full["costUsd"])
        except (TypeError, ValueError):
            pass
    tail = session.get("session_usage_tail")
    if isinstance(tail, dict) and tail.get("costUsd") is not None:
        try:
            return float(tail["costUsd"])
        except (TypeError, ValueError):
            pass
    return None


def format_stack_metrics(
    stack: Dict[str, Any],
    *,
    markup: bool = True,
    session: Optional[Dict[str, Any]] = None,
    billing: Optional[Dict[str, Any]] = None,
    show_versions: bool = True,
    show_credits: bool = True,
) -> str:
    hr = stack.get("headroom") or {}
    ll = stack.get("litellm") or {}
    path = stack.get("path", "?")
    path_ok = stack.get("path_ok", False)

    today = float(ll.get("spend_today") or 0)
    window = float(ll.get("spend_window") or 0)
    spend_date = ll.get("spend_date") or date.today().isoformat()
    sources = ll.get("spend_sources") or []
    multi = len([s for s in sources if s.get("ok")]) > 1
    src_note = f"  · {len(sources)} bases" if multi else ""

    lines = [
        f"Path      {path}  ({'healthy' if path_ok else 'degraded'})",
    ]

    # Ordered version board (Headroom → LiteLLM → Herdr → CLIs → OWUI → other)
    versions = stack.get("versions")
    if show_versions and versions and not versions.get("error"):
        try:
            from grok_tua.stack_versions import format_stack_versions

            # Compact by default — right stats pane is intentionally narrow.
            compact = os.environ.get("GROK_TUA_STACK_COMPACT", "1") not in (
                "0",
                "false",
                "no",
            )
            lines.append(
                format_stack_versions(versions, markup=markup, compact=compact)
            )
        except Exception as exc:
            lines.append(f"Stack     (version board error: {exc})")
    else:
        # Minimal fallback if versions not attached
        def ok(v: bool) -> str:
            if markup:
                return "[green]OK[/green]" if v else "[red]DOWN[/red]"
            return "OK" if v else "DOWN"

        her = stack.get("herdr") or {}
        lines.extend(
            [
                f"Headroom  {ok(bool(hr.get('ready')))}  models={hr.get('model_count', 0)}  {hr.get('base', '')}",
                f"LiteLLM   {ok(bool(ll.get('alive')))}  models={ll.get('model_count', 0)}  {ll.get('base', '')}",
            ]
        )
        if her.get("available"):
            lines.append(
                f"Herdr     {ok(bool(her.get('running')))}  {her.get('version') or ''}".rstrip()
            )

    lines.append(
        f"Spend $   (LiteLLM) today={today:.4f}  window={window:.4f}  [{spend_date}]{src_note}"
    )

    sess_cost = _session_cost_usd(session)
    if sess_cost is not None:
        lines.append(f"Session $ {sess_cost:.4f}  (Grok Build · not in LiteLLM)")
    elif session:
        lines.append("Session $ n/a  (OAuth / unstamped · SuperGrok ≠ LiteLLM)")

    # Cloud credits tally (SuperGrok + OpenRouter + Gemini + …)
    credits = stack.get("credits")
    if show_credits and credits and not credits.get("error"):
        try:
            from grok_tua.cloud_credits import format_cloud_credits, supergrok_from_billing

            # Refresh SuperGrok row from live billing without re-hitting every vendor
            if billing is not None and isinstance(credits.get("rows"), list):
                sg = supergrok_from_billing(billing)
                rows = [sg if r.get("id") == "supergrok" else r for r in credits["rows"]]
                if not any(r.get("id") == "supergrok" for r in credits["rows"]):
                    rows = [sg] + list(credits["rows"])
                credits = {**credits, "rows": rows}
            lines.append(format_cloud_credits(credits, markup=markup))
        except Exception as exc:
            lines.append(f"Credits   (error: {exc})")
    else:
        bill = billing or {}
        if bill.get("available"):
            left = bill.get("usage_left_percent")
            prepaid = bill.get("prepaid_balance")
            left_s = f"{left:.0f}% left" if left is not None else "? left"
            prepaid_s = f"  prepaid ${prepaid:.2f}" if prepaid is not None and prepaid > 0 else ""
            lines.append(f"Quota     SuperGrok ~{left_s}{prepaid_s}  (xAI billing · /usage)")
        else:
            lines.append("Quota     SuperGrok — (run /usage in left TUI)")

    if not stack.get("has_key"):
        lines.append("Key       missing (set LITELLM_MASTER_KEY or GATEWAY_ENV_FILE)")
    if hr.get("models_sample"):
        lines.append("HR models " + ", ".join(hr["models_sample"][:5]))
    if ll.get("models_sample"):
        lines.append("LL models " + ", ".join(ll["models_sample"][:5]))

    ts = stack.get("turnstone")
    if isinstance(ts, dict) and ts:
        if markup:
            tok = "[green]OK[/green]" if ts.get("ready") else "[red]DOWN[/red]"
        else:
            tok = "OK" if ts.get("ready") else "DOWN"
        ver = ts.get("version") or ""
        err = ts.get("error") or ""
        line = f"Turnstone {tok}  {ts.get('base', '')}  {ver}".rstrip()
        if err and not ts.get("ready"):
            line += f"  ({err[:60]})"
        # Avoid duplicate if already in version board
        if not (versions and any(c.get("id") == "turnstone" for c in (versions.get("all") or []))):
            lines.append(line)

    return "\n".join(lines)
