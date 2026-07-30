"""Grok Build session burn monitoring, warnings, and pre-search indexes."""

from __future__ import annotations

import json
import re
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO = Path(__file__).resolve().parents[2]

RECON_PATTERNS = re.compile(
    r"explore|audit|inventory|recon|structural|mirror|canonical|module.?acronym|"
    r"search.+(repo|vault|grokcode|obsidian)|grep|glob|scan all|deep audit",
    re.I,
)
INGEST_PATTERNS = re.compile(
    r"ingest|reconcile|pinned|bookmark|grok\.com/share|dedup|defrag|placement",
    re.I,
)
BROAD_TOOL_PATTERNS = re.compile(
    r"^\*\*$|^\.$|^/$|/\*\*|grokcode/?$|\.grok/worktrees",
)

# Grok headless / turn_completed: 1 USD = 10^10 ticks (see user-guide/14-headless-mode.md).
COST_USD_TICKS_PER_DOLLAR = 10_000_000_000

# mtime+size cache for full-session usage scans (dashboard polls every ~2s).
_session_usage_full_cache: Dict[str, Tuple[float, int, Dict[str, Any]]] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def cost_usd_from_ticks(ticks: Any) -> Optional[float]:
    """Convert costUsdTicks / total_cost_usd_ticks to float USD, or None if missing."""
    if ticks is None:
        return None
    try:
        value = int(ticks)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value / COST_USD_TICKS_PER_DOLLAR


def _usage_cost_ticks(usage: Dict[str, Any]) -> Optional[int]:
    for key in ("costUsdTicks", "total_cost_usd_ticks", "cost_usd_ticks"):
        if usage.get(key) is not None:
            try:
                return int(usage[key])
            except (TypeError, ValueError):
                return None
    return None


def _decode_session_dir(name: str) -> str:
    try:
        return urllib.parse.unquote(name)
    except Exception:
        return name


def load_monitor_config(repo: Optional[Path] = None) -> Dict[str, Any]:
    repo = repo or _REPO
    cfg_path = repo / "config" / "token_conservation.json"
    defaults: Dict[str, Any] = {
        "enabled": True,
        "paths": {
            "grok_sessions_root": str(Path.home() / ".grok" / "sessions"),
            "active_sessions": str(Path.home() / ".grok" / "active_sessions.json"),
            "repo_search_index": "data/catalog/repo_search_index.json",
            "volumes_github_index": "data/catalog/volumes_github_index.json",
            "usage_report": "logs/token_conservation/grokcode_usage_report.json",
            "burn_snapshot": "logs/token_conservation/session_burn_snapshot.json",
        },
        "thresholds": {
            "warn_tool_calls": 200,
            "high_tool_calls": 1000,
            "critical_tool_calls": 5000,
            "warn_compactions": 5,
            "high_compactions": 20,
            "critical_compactions": 50,
            "warn_context_pct": 55,
            "critical_context_pct": 75,
            "warn_turns": 25,
            "high_turns": 50,
            "index_max_age_hours": 24,
            "watch_poll_seconds": 30,
        },
        "features": {
            "credit_monitor": True,
            "preflight_index_hint": True,
            "session_tips": True,
            "block_broad_grep_glob": False,
        },
    }
    if not cfg_path.is_file():
        return defaults
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    merged = dict(defaults)
    merged.update({k: v for k, v in data.items() if k not in ("paths", "thresholds", "features")})
    if isinstance(data.get("paths"), dict):
        merged["paths"] = {**defaults["paths"], **data["paths"]}
    if isinstance(data.get("thresholds"), dict):
        merged["thresholds"] = {**defaults["thresholds"], **data["thresholds"]}
    if isinstance(data.get("features"), dict):
        merged["features"] = {**defaults["features"], **data["features"]}
    return merged


def burn_level(signals: Dict[str, Any], thresholds: Optional[Dict[str, Any]] = None) -> str:
    th = thresholds or load_monitor_config()["thresholds"]
    tools = int(signals.get("toolCallCount") or 0)
    compactions = int(signals.get("compactionCount") or 0)
    ctx = int(signals.get("contextWindowUsage") or 0)
    turns = int(signals.get("turnCount") or 0)
    if (
        tools >= th["critical_tool_calls"]
        or compactions >= th["critical_compactions"]
        or ctx >= th["critical_context_pct"]
    ):
        return "CRITICAL"
    if (
        tools >= th["high_tool_calls"]
        or compactions >= th["high_compactions"]
        or ctx >= th["warn_context_pct"]
        or turns >= th["high_turns"]
    ):
        return "HIGH"
    if (
        tools >= th["warn_tool_calls"]
        or compactions >= th["warn_compactions"]
        or turns >= th["warn_turns"]
    ):
        return "MODERATE"
    return "LOW"


def _session_title(session_dir: Path) -> Optional[str]:
    summary = session_dir / "summary.json"
    if not summary.is_file():
        return None
    try:
        data = json.loads(summary.read_text(encoding="utf-8"))
        return data.get("generated_title") or data.get("session_summary")
    except (OSError, json.JSONDecodeError):
        return None


def _parse_iso_ts(value: Optional[str]) -> Optional[float]:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _tail_text(path: Path, max_bytes: int = 768_000) -> str:
    """Read the last max_bytes of a file (for large updates.jsonl)."""
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size <= 0:
        return ""
    try:
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(-max_bytes, 2)
                handle.readline()  # drop partial first line
            return handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return ""


def _parse_updates_usage_text(text: str) -> Dict[str, Any]:
    """Parse a updates.jsonl text slice into context + turn usage fields."""
    latest_context_tokens: Optional[int] = None
    last_turn_usage: Optional[Dict[str, Any]] = None
    session_input = 0
    session_output = 0
    session_reasoning = 0
    session_cached = 0
    session_model_calls = 0
    session_cost_ticks = 0
    turns_with_cost = 0
    turn_completed_count = 0
    last_model: Optional[str] = None

    for line in text.splitlines():
        if "totalTokens" not in line and "turn_completed" not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        params = obj.get("params") if isinstance(obj, dict) else None
        if not isinstance(params, dict):
            continue
        update = params.get("update")
        if not isinstance(update, dict):
            continue
        su = update.get("sessionUpdate")

        # Live context fill on streaming chunks.
        # Grok Build puts _meta as a sibling of update under params (not inside update).
        meta = params.get("_meta")
        if not isinstance(meta, dict):
            meta = update.get("_meta") if isinstance(update.get("_meta"), dict) else {}
        if isinstance(meta, dict) and meta.get("totalTokens") is not None:
            try:
                latest_context_tokens = int(meta["totalTokens"])
            except (TypeError, ValueError):
                pass

        if su == "turn_completed" and isinstance(update.get("usage"), dict):
            usage = update["usage"]
            turn_completed_count += 1
            last_turn_usage = usage
            try:
                session_input += int(usage.get("inputTokens") or 0)
                session_output += int(usage.get("outputTokens") or 0)
                session_reasoning += int(usage.get("reasoningTokens") or 0)
                session_cached += int(usage.get("cachedReadTokens") or 0)
                session_model_calls += int(usage.get("modelCalls") or 0)
            except (TypeError, ValueError):
                pass
            ticks = _usage_cost_ticks(usage)
            if ticks is not None:
                session_cost_ticks += ticks
                turns_with_cost += 1
            model_usage = usage.get("modelUsage")
            if isinstance(model_usage, dict) and model_usage:
                last_model = next(iter(model_usage.keys()), None)

    out: Dict[str, Any] = {}
    if latest_context_tokens is not None:
        out["contextTokensUsed"] = latest_context_tokens
    if last_turn_usage is not None:
        out["last_turn_usage"] = last_turn_usage
        # Prefer last turn input as context fill when _meta missing (post-turn).
        if latest_context_tokens is None and last_turn_usage.get("inputTokens") is not None:
            try:
                out["contextTokensUsed"] = int(last_turn_usage["inputTokens"])
            except (TypeError, ValueError):
                pass
        last_ticks = _usage_cost_ticks(last_turn_usage)
        out["session_usage_tail"] = {
            "inputTokens": session_input,
            "outputTokens": session_output,
            "reasoningTokens": session_reasoning,
            "cachedReadTokens": session_cached,
            "modelCalls": session_model_calls,
            "turnCompletedInTail": turn_completed_count,
            "costUsdTicks": session_cost_ticks if turns_with_cost else None,
            "costUsd": cost_usd_from_ticks(session_cost_ticks) if turns_with_cost else None,
            "turnsWithCost": turns_with_cost,
            "lastTurnCostUsd": cost_usd_from_ticks(last_ticks),
            "lastTurnCostUsdTicks": last_ticks,
        }
    if last_model:
        out["primaryModelId"] = last_model
    return out


def extract_live_usage_from_updates(
    session_dir: Path,
    *,
    max_bytes: int = 1_500_000,
) -> Dict[str, Any]:
    """Parse live context + turn usage from updates.jsonl (what the left CLI streams).

    Sources (Grok Build session log):
    - Mid-turn: ``params._meta.totalTokens`` on agent/tool chunks ≈ context fill
    - End-turn: ``sessionUpdate=turn_completed`` + ``usage`` (input/output/reasoning)

    Hot path reads the last ~1.5 MiB. If a long tool-heavy turn pushed
    ``turn_completed`` out of that window, a second wider scan pulls it back.
    """
    updates_path = session_dir / "updates.jsonl"
    if not updates_path.is_file():
        return {}

    text = _tail_text(updates_path, max_bytes=max_bytes)
    if not text.strip():
        return {}

    out = _parse_updates_usage_text(text)
    # Mid-turn floods can push turn_completed out of the hot tail; widen once.
    if "last_turn_usage" not in out:
        wider = _tail_text(updates_path, max_bytes=max(max_bytes, 6_000_000))
        if wider and wider != text:
            wide_out = _parse_updates_usage_text(wider)
            if wide_out.get("last_turn_usage"):
                out["last_turn_usage"] = wide_out["last_turn_usage"]
            if wide_out.get("session_usage_tail"):
                out["session_usage_tail"] = wide_out["session_usage_tail"]
            if wide_out.get("primaryModelId") and not out.get("primaryModelId"):
                out["primaryModelId"] = wide_out["primaryModelId"]
            # Keep the fresher contextTokensUsed from the hot tail if present.
            if out.get("contextTokensUsed") is None and wide_out.get("contextTokensUsed") is not None:
                out["contextTokensUsed"] = wide_out["contextTokensUsed"]
    return out


def sum_session_usage_from_updates(
    session_dir: Path,
    *,
    max_bytes: Optional[int] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Sum all ``turn_completed.usage`` rows for a session (true session Σ).

    Full-file scan by default, cached by (path, mtime, size) so the 2s dashboard
    poll does not re-read multi‑MB logs every tick. Pass ``max_bytes`` to bound
    the scan (tail) for huge files if needed.
    """
    updates_path = session_dir / "updates.jsonl"
    if not updates_path.is_file():
        return {}

    try:
        st = updates_path.stat()
    except OSError:
        return {}

    cache_key = str(updates_path)
    if use_cache:
        cached = _session_usage_full_cache.get(cache_key)
        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            return dict(cached[2])

    if max_bytes is not None and st.st_size > max_bytes:
        text = _tail_text(updates_path, max_bytes=max_bytes)
        parsed = _parse_updates_usage_text(text)
        tail = parsed.get("session_usage_tail") or {}
        result = {
            "inputTokens": int(tail.get("inputTokens") or 0),
            "outputTokens": int(tail.get("outputTokens") or 0),
            "reasoningTokens": int(tail.get("reasoningTokens") or 0),
            "cachedReadTokens": int(tail.get("cachedReadTokens") or 0),
            "modelCalls": int(tail.get("modelCalls") or 0),
            "turnCompleted": int(tail.get("turnCompletedInTail") or 0),
            "costUsdTicks": tail.get("costUsdTicks"),
            "costUsd": tail.get("costUsd"),
            "turnsWithCost": int(tail.get("turnsWithCost") or 0),
            "lastTurnCostUsd": tail.get("lastTurnCostUsd"),
            "last_turn_usage": parsed.get("last_turn_usage"),
            "primaryModelId": parsed.get("primaryModelId"),
            "contextTokensUsed": parsed.get("contextTokensUsed"),
            "complete": False,
            "scannedBytes": min(st.st_size, max_bytes),
            "fileBytes": st.st_size,
        }
    else:
        try:
            text = updates_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return {}
        parsed = _parse_updates_usage_text(text)
        tail = parsed.get("session_usage_tail") or {}
        result = {
            "inputTokens": int(tail.get("inputTokens") or 0),
            "outputTokens": int(tail.get("outputTokens") or 0),
            "reasoningTokens": int(tail.get("reasoningTokens") or 0),
            "cachedReadTokens": int(tail.get("cachedReadTokens") or 0),
            "modelCalls": int(tail.get("modelCalls") or 0),
            "turnCompleted": int(tail.get("turnCompletedInTail") or 0),
            "costUsdTicks": tail.get("costUsdTicks"),
            "costUsd": tail.get("costUsd"),
            "turnsWithCost": int(tail.get("turnsWithCost") or 0),
            "lastTurnCostUsd": tail.get("lastTurnCostUsd"),
            "last_turn_usage": parsed.get("last_turn_usage"),
            "primaryModelId": parsed.get("primaryModelId"),
            "contextTokensUsed": parsed.get("contextTokensUsed"),
            "complete": True,
            "scannedBytes": st.st_size,
            "fileBytes": st.st_size,
        }

    if use_cache:
        # Bound cache growth across many session dirs.
        if len(_session_usage_full_cache) > 64:
            _session_usage_full_cache.clear()
        _session_usage_full_cache[cache_key] = (st.st_mtime, st.st_size, dict(result))
    return result


def fetch_billing_from_unified_log(
    log_path: Optional[Path] = None,
    *,
    max_bytes: int = 2_000_000,
    stale_hours: float = 24.0,
) -> Dict[str, Any]:
    """Read the latest SuperGrok / credits config Grok logged to unified.jsonl.

    Grok shell emits ``billing: fetched credits config`` with
    ``creditUsagePercent`` (used % of period quota). There is no public xAI
    balance REST endpoint; this is the local side-channel the TUI uses.

    Returns a dict always; ``available`` is False when the log is missing or
    has no billing lines.
    """
    path = log_path or (Path.home() / ".grok" / "logs" / "unified.jsonl")
    empty: Dict[str, Any] = {
        "available": False,
        "source": str(path),
        "error": None,
    }
    if not path.is_file():
        empty["error"] = "unified.jsonl missing"
        return empty

    text = _tail_text(path, max_bytes=max_bytes)
    if not text.strip():
        empty["error"] = "unified.jsonl empty"
        return empty

    last: Optional[Dict[str, Any]] = None
    for line in text.splitlines():
        if "fetched credits config" not in line and "billing" not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = str(obj.get("msg") or "")
        if "fetched credits config" not in msg and "credits config" not in msg.lower():
            continue
        last = obj

    if not last:
        empty["error"] = "no billing: fetched credits config line in tail"
        return empty

    ctx = last.get("ctx") if isinstance(last.get("ctx"), dict) else {}
    config = ctx.get("config") if isinstance(ctx.get("config"), dict) else {}
    used = config.get("creditUsagePercent")
    try:
        used_f = float(used) if used is not None else None
    except (TypeError, ValueError):
        used_f = None

    left_f: Optional[float] = None
    if used_f is not None:
        left_f = max(0.0, min(100.0, 100.0 - used_f))

    period = config.get("currentPeriod") if isinstance(config.get("currentPeriod"), dict) else {}
    period_type = period.get("type") or ""
    period_label = "period"
    if "WEEKLY" in str(period_type).upper():
        period_label = "week"
    elif "MONTHLY" in str(period_type).upper():
        period_label = "month"

    def _val(obj: Any) -> Optional[float]:
        if isinstance(obj, dict) and "val" in obj:
            try:
                return float(obj["val"])
            except (TypeError, ValueError):
                return None
        if obj is None:
            return None
        try:
            return float(obj)
        except (TypeError, ValueError):
            return None

    fetched_at = last.get("ts")
    age_hours: Optional[float] = None
    stale = True
    ts = _parse_iso_ts(str(fetched_at) if fetched_at else None)
    if ts is not None:
        age_hours = round((datetime.now(timezone.utc).timestamp() - ts) / 3600.0, 2)
        stale = age_hours > stale_hours

    period_end = period.get("end") or config.get("billingPeriodEnd")
    period_end_short = None
    if isinstance(period_end, str) and len(period_end) >= 10:
        period_end_short = period_end[:10]

    return {
        "available": used_f is not None,
        "source": str(path),
        "error": None if used_f is not None else "creditUsagePercent missing",
        "credit_usage_percent": used_f,
        "usage_left_percent": left_f,
        "subscription_tier": ctx.get("subscriptionTier") or ctx.get("subscription_tier"),
        "period_type": period_type,
        "period_label": period_label,
        "period_start": period.get("start") or config.get("billingPeriodStart"),
        "period_end": period_end,
        "period_end_short": period_end_short,
        "prepaid_balance": _val(config.get("prepaidBalance")),
        "on_demand_cap": _val(config.get("onDemandCap")),
        "on_demand_used": _val(config.get("onDemandUsed")),
        "is_unified_billing_user": config.get("isUnifiedBillingUser"),
        "fetched_at": fetched_at,
        "age_hours": age_hours,
        "stale": stale,
        "raw_config": config,
    }


def _default_context_window() -> int:
    """Prefer models_cache.json; fall back to Grok 4.5 500k."""
    cache = Path.home() / ".grok" / "models_cache.json"
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            # structure varies; walk for context_window near grok-4.5
            if isinstance(data, dict):
                for key in ("models", "items", "cache"):
                    bucket = data.get(key)
                    if isinstance(bucket, list):
                        for item in bucket:
                            if not isinstance(item, dict):
                                continue
                            mid = str(item.get("id") or item.get("model_id") or "").lower()
                            if "grok-4.5" in mid or "grok-4" in mid:
                                cw = item.get("context_window") or item.get("contextWindow")
                                if cw:
                                    return int(cw)
                    elif isinstance(bucket, dict):
                        for mid, item in bucket.items():
                            if "grok-4.5" in str(mid).lower() and isinstance(item, dict):
                                cw = item.get("context_window") or item.get("contextWindow")
                                if cw:
                                    return int(cw)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return 500_000


def enrich_signals_with_live_usage(sig: Dict[str, Any], session_dir: Path) -> Dict[str, Any]:
    """Merge streaming updates.jsonl usage into signals / derived metrics."""
    live = extract_live_usage_from_updates(session_dir)
    # Full-session sum (mtime-cached) for accurate Σ + $ when cost ticks exist.
    full = sum_session_usage_from_updates(session_dir)
    if not live and not full:
        return sig
    merged = dict(sig)
    window = int(
        merged.get("contextWindowTokens")
        or live.get("contextWindowTokens")
        or _default_context_window()
    )
    merged["contextWindowTokens"] = window

    ctx_used = live.get("contextTokensUsed") if live else None
    if ctx_used is None and full:
        ctx_used = full.get("contextTokensUsed")
    if ctx_used is not None:
        merged["contextTokensUsed"] = int(ctx_used)
        merged["contextWindowUsage"] = min(100, int(round(100 * int(ctx_used) / window))) if window else 0
    elif merged.get("contextTokensUsed") and not merged.get("contextWindowUsage"):
        try:
            used = int(merged["contextTokensUsed"])
            merged["contextWindowUsage"] = min(100, int(round(100 * used / window))) if window else 0
        except (TypeError, ValueError):
            pass

    if live.get("last_turn_usage"):
        merged["last_turn_usage"] = live["last_turn_usage"]
    elif full.get("last_turn_usage"):
        merged["last_turn_usage"] = full["last_turn_usage"]

    if live.get("session_usage_tail"):
        merged["session_usage_tail"] = live["session_usage_tail"]

    if full and (full.get("turnCompleted") or full.get("inputTokens")):
        merged["session_usage_full"] = full
        # Prefer full sum over tail for pre-compact proxy when signals empty.
        approx = int(full.get("inputTokens") or 0) + int(full.get("outputTokens") or 0)
        if approx and not merged.get("totalTokensBeforeCompaction"):
            merged["totalTokensBeforeCompaction"] = approx
    elif live.get("session_usage_tail"):
        tail = live["session_usage_tail"]
        approx = int(tail.get("inputTokens") or 0) + int(tail.get("outputTokens") or 0)
        if approx and not merged.get("totalTokensBeforeCompaction"):
            merged["totalTokensBeforeCompaction"] = approx

    model = live.get("primaryModelId") if live else None
    if not model and full:
        model = full.get("primaryModelId")
    if model and not merged.get("primaryModelId"):
        merged["primaryModelId"] = model
    return merged


def derive_signals_from_session_dir(session_dir: Path) -> Dict[str, Any]:
    """Best-effort burn metrics when Grok Build has not written signals.json.

    Plan-mode and some agent profiles update events.jsonl + summary.json live but
    omit signals.json. Counts are derived from tool_completed / turn_started so
    the right-pane dashboard tracks the active left-pane session. Context/token
    fill is taken from updates.jsonl (same stream as the left CLI).
    """
    tools_used: List[str] = []
    tools_seen: set[str] = set()
    tool_call_count = 0
    turn_count = 0
    error_count = 0
    compaction_count = 0
    primary_model: Optional[str] = None
    last_event_ts: Optional[float] = None

    events_path = session_dir / "events.jsonl"
    if events_path.is_file():
        try:
            with events_path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    etype = event.get("type") or ""
                    ts = _parse_iso_ts(event.get("ts"))
                    if ts is not None:
                        last_event_ts = ts if last_event_ts is None else max(last_event_ts, ts)
                    if etype == "tool_completed":
                        tool_call_count += 1
                        name = event.get("tool_name") or event.get("name")
                        if isinstance(name, str) and name and name not in tools_seen:
                            tools_seen.add(name)
                            tools_used.append(name)
                        outcome = (event.get("outcome") or "").lower()
                        if outcome and outcome not in ("success", "ok", "completed"):
                            error_count += 1
                    elif etype == "tool_started" and tool_call_count == 0:
                        # Prefer completed; fall back if only starts were logged.
                        pass
                    elif etype == "turn_started":
                        turn_count = max(turn_count, int(event.get("turn_number") or 0) + 1)
                        if event.get("model_id"):
                            primary_model = str(event["model_id"])
                    elif etype in ("compaction", "compaction_completed", "context_compacted"):
                        compaction_count += 1
                    elif "compact" in etype.lower():
                        compaction_count += 1
        except OSError:
            pass

    # If no completions recorded, count starts as a weaker signal.
    if tool_call_count == 0 and events_path.is_file():
        try:
            with events_path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if event.get("type") == "tool_started":
                        tool_call_count += 1
                        name = event.get("tool_name") or event.get("name")
                        if isinstance(name, str) and name and name not in tools_seen:
                            tools_seen.add(name)
                            tools_used.append(name)
        except OSError:
            pass

    created_ts: Optional[float] = None
    summary: Dict[str, Any] = {}
    summary_path = session_dir / "summary.json"
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = {}
        created_ts = _parse_iso_ts(summary.get("created_at"))
        last_active = _parse_iso_ts(summary.get("last_active_at") or summary.get("updated_at"))
        if last_active is not None:
            last_event_ts = last_active if last_event_ts is None else max(last_event_ts, last_active)
        if summary.get("current_model_id"):
            primary_model = str(summary["current_model_id"])
        # Plan-mode often has a single long turn; message count is a useful floor.
        if turn_count == 0:
            chat_msgs = int(summary.get("num_chat_messages") or 0)
            # Rough: user+assistant pairs; at least 1 if any activity.
            turn_count = max(1, chat_msgs // 2) if chat_msgs else (1 if tool_call_count else 0)

    duration = 0
    if created_ts is not None and last_event_ts is not None and last_event_ts >= created_ts:
        duration = int(last_event_ts - created_ts)

    base = {
        "turnCount": turn_count,
        "toolCallCount": tool_call_count,
        "compactionCount": compaction_count,
        "errorCount": error_count,
        "totalTokensBeforeCompaction": 0,
        "contextWindowUsage": 0,
        "contextTokensUsed": 0,
        "contextWindowTokens": _default_context_window(),
        "sessionDurationSeconds": duration,
        "primaryModelId": primary_model,
        "toolsUsed": tools_used,
        "agentFilesTouched": 0,
        "agentLinesAdded": 0,
        "metricsSource": "derived",
        "derivedFrom": ["events.jsonl", "summary.json", "updates.jsonl"],
    }
    return enrich_signals_with_live_usage(base, session_dir)


def _activity_mtime(session_dir: Path) -> float:
    """Latest mtime among live activity files (not stale signals-only)."""
    best = session_dir.stat().st_mtime if session_dir.exists() else 0.0
    for name in ("signals.json", "events.jsonl", "summary.json", "updates.jsonl", "chat_history.jsonl"):
        path = session_dir / name
        if path.is_file():
            try:
                best = max(best, path.stat().st_mtime)
            except OSError:
                continue
    return best


def _session_dirs_under_workspace(workspace_child: Path) -> List[Path]:
    """Return session directories that have any usable metrics source."""
    found: List[Path] = []
    if not workspace_child.is_dir():
        return found
    # Rare layout: metrics files directly under workspace encoding dir.
    if any((workspace_child / n).is_file() for n in ("signals.json", "events.jsonl", "summary.json")):
        found.append(workspace_child)
    for session in workspace_child.iterdir():
        if not session.is_dir() or session.name.startswith("."):
            continue
        if any((session / n).is_file() for n in ("signals.json", "events.jsonl", "summary.json")):
            found.append(session)
    return found


def _row_from_session_dir(
    session_dir: Path,
    *,
    label: str,
    thresholds: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    sig_path = session_dir / "signals.json"
    metrics_source = "signals"
    signals_path_str: Optional[str] = None
    if sig_path.is_file():
        try:
            sig = json.loads(sig_path.read_text(encoding="utf-8"))
            signals_path_str = str(sig_path)
            # Always overlay live updates.jsonl so mid-turn context tracks the left CLI.
            sig = enrich_signals_with_live_usage(sig, session_dir)
            metrics_source = "signals+live"
        except (OSError, json.JSONDecodeError):
            sig = derive_signals_from_session_dir(session_dir)
            metrics_source = "derived"
    else:
        sig = derive_signals_from_session_dir(session_dir)
        metrics_source = "derived"
        # Skip empty husks with no activity at all.
        if not sig.get("toolCallCount") and not sig.get("turnCount") and not (session_dir / "summary.json").is_file():
            return None

    mtime = _activity_mtime(session_dir)
    last_turn = sig.get("last_turn_usage") if isinstance(sig.get("last_turn_usage"), dict) else {}
    full = sig.get("session_usage_full") if isinstance(sig.get("session_usage_full"), dict) else {}
    return {
        "session_id": session_dir.name,
        "workspace_label": label,
        "signals_path": signals_path_str,
        "metrics_source": metrics_source,
        "title": _session_title(session_dir),
        "started": datetime.fromtimestamp(session_dir.stat().st_ctime, tz=timezone.utc).strftime("%Y-%m-%d"),
        "last_active": datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "mtime": mtime,
        "burn_level": burn_level(sig, thresholds),
        "turnCount": int(sig.get("turnCount") or 0),
        "toolCallCount": int(sig.get("toolCallCount") or 0),
        "compactionCount": int(sig.get("compactionCount") or 0),
        "errorCount": int(sig.get("errorCount") or 0),
        "totalTokensBeforeCompaction": int(sig.get("totalTokensBeforeCompaction") or 0),
        "sessionDurationSeconds": int(sig.get("sessionDurationSeconds") or 0),
        "primaryModelId": sig.get("primaryModelId"),
        "contextWindowUsage": int(sig.get("contextWindowUsage") or 0),
        "contextTokensUsed": int(sig.get("contextTokensUsed") or 0),
        "contextWindowTokens": int(sig.get("contextWindowTokens") or _default_context_window()),
        "last_turn_usage": last_turn,
        "session_usage_tail": sig.get("session_usage_tail") or {},
        "session_usage_full": full,
        "agentFilesTouched": int(sig.get("agentFilesTouched") or 0),
        "agentLinesAdded": int(sig.get("agentLinesAdded") or 0),
        "toolsUsed": sig.get("toolsUsed") or [],
        "signals": sig,
    }


def discover_sessions(
    *,
    repo: Optional[Path] = None,
    sessions_root: Optional[Path] = None,
    cwd_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    repo = (repo or _REPO).resolve()
    cfg = load_monitor_config(repo)
    root = Path(sessions_root or cfg["paths"]["grok_sessions_root"])
    if not root.is_dir():
        return []

    needles = {str(repo), urllib.parse.quote(str(repo), safe="")}
    if cwd_filter:
        needles.add(cwd_filter)
        needles.add(urllib.parse.quote(cwd_filter, safe=""))

    rows: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for child in root.iterdir():
        label = _decode_session_dir(child.name)
        if cwd_filter:
            if cwd_filter not in label and urllib.parse.quote(cwd_filter, safe="") not in child.name:
                continue
        elif not any(n in label or n in child.name for n in needles) and "grokcode" not in label:
            continue

        for session_dir in _session_dirs_under_workspace(child):
            if session_dir.name in seen_ids:
                continue
            row = _row_from_session_dir(session_dir, label=label, thresholds=cfg["thresholds"])
            if row is None:
                continue
            seen_ids.add(session_dir.name)
            rows.append(row)

    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows


def load_active_sessions(active_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = active_path or Path(load_monitor_config()["paths"]["active_sessions"])
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def match_active_session(
    sessions: List[Dict[str, Any]],
    active: List[Dict[str, Any]],
    cwd: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not active:
        return []
    by_id = {s["session_id"]: s for s in sessions}
    matched: List[Dict[str, Any]] = []
    for row in active:
        sid = row.get("session_id")
        if cwd and row.get("cwd") and Path(row["cwd"]).resolve() != Path(cwd).resolve():
            continue
        sess = by_id.get(sid)
        if sess:
            matched.append({**sess, "active_meta": row})
    if not matched and active:
        # Fall back to most recently touched session for cwd
        cwd_rows = [s for s in sessions if not cwd or cwd in s.get("workspace_label", "")]
        if cwd_rows:
            matched.append({**cwd_rows[0], "active_meta": active[0], "inferred_active": True})
    # Prefer the busiest / most recently active session when several PIDs are open.
    matched.sort(key=lambda r: (r.get("mtime") or 0, r.get("toolCallCount") or 0), reverse=True)
    return matched


def maybe_rebuild_stale_index(
    repo: Optional[Path] = None,
    *,
    min_interval_hours: float = 6.0,
    force: bool = False,
) -> Dict[str, Any]:
    """Rebuild repo_search_index.json when stale, rate-limited for dashboard self-heal."""
    repo = (repo or _REPO).resolve()
    cfg = load_monitor_config(repo)
    index_path = repo / cfg["paths"]["repo_search_index"]
    max_age = float(cfg["thresholds"]["index_max_age_hours"])
    age = index_age_hours(index_path)
    stale = age is None or age > max_age
    stamp_path = repo / "logs" / "token_conservation" / "index_autobuild_stamp.json"
    if not force and not stale:
        return {"rebuilt": False, "reason": "fresh", "age_hours": age, "path": str(index_path)}
    if not force and stamp_path.is_file():
        try:
            stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
            last = _parse_iso_ts(stamp.get("built_at"))
            if last is not None:
                since_h = (datetime.now(timezone.utc).timestamp() - last) / 3600
                if since_h < min_interval_hours:
                    return {
                        "rebuilt": False,
                        "reason": "rate_limited",
                        "age_hours": age,
                        "hours_since_autobuild": round(since_h, 2),
                        "path": str(index_path),
                    }
        except (OSError, json.JSONDecodeError):
            pass
    payload = build_repo_search_index(repo)
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    stamp_path.write_text(
        json.dumps({"built_at": _utc_now(), "path": str(index_path), "source": "maybe_rebuild_stale_index"}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return {
        "rebuilt": True,
        "reason": "stale" if stale else "forced",
        "age_hours_before": age,
        "path": str(index_path),
        "generated_at": payload.get("generated_at"),
    }


def categorize_prompt(text: str) -> List[str]:
    tags: List[str] = []
    if RECON_PATTERNS.search(text):
        tags.append("recon")
    if INGEST_PATTERNS.search(text):
        tags.append("ingest")
    if re.search(r"integrat|mcp|acl|build \d", text, re.I):
        tags.append("integration")
    if re.search(r"credit|token|usage|quota|burn", text, re.I):
        tags.append("token/cost")
    return tags or ["other"]


def index_age_hours(index_path: Path) -> Optional[float]:
    if not index_path.is_file():
        return None
    age_sec = datetime.now(timezone.utc).timestamp() - index_path.stat().st_mtime
    return round(age_sec / 3600, 2)


def _index_is_stale(index_path: Path, max_age_hours: float) -> bool:
    age = index_age_hours(index_path)
    return age is None or age > max_age_hours


def build_repo_search_index(repo: Optional[Path] = None) -> Dict[str, Any]:
    repo = (repo or _REPO).resolve()
    cfg = load_monitor_config(repo)
    rel_index = cfg["paths"]["repo_search_index"]
    index_path = repo / rel_index

    top_dirs: Dict[str, Dict[str, Any]] = {}
    for child in sorted(repo.iterdir()):
        if child.name.startswith(".") and child.name not in (".grok",):
            continue
        if not child.is_dir():
            continue
        py_count = sum(1 for _ in child.rglob("*.py") if "__pycache__" not in _.parts)
        top_dirs[child.name] = {
            "path": child.name,
            "py_files": py_count,
            "purpose_hint": _dir_purpose(child.name),
        }

    agents = sorted(p.relative_to(repo).as_posix() for p in (repo / "agents").rglob("*_agent.py"))
    tools = sorted(p.relative_to(repo).as_posix() for p in (repo / "tools").rglob("*.py") if p.is_file())[:200]
    mcp = sorted(p.relative_to(repo).as_posix() for p in (repo / "mcp").glob("*.py"))
    scripts = sorted(p.name for p in (repo / "scripts").glob("*.py"))[:80]

    acronyms: List[str] = []
    registry_md = repo / "docs" / "REGISTRY.md"
    if registry_md.is_file():
        for line in registry_md.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\|\s*\*\*([A-Z][A-Z0-9./:]+)\*\*", line)
            if m:
                acronyms.append(m.group(1))

    catalog_manifests = []
    catalog_dir = repo / "data" / "catalog"
    if catalog_dir.is_dir():
        for p in sorted(catalog_dir.glob("*.json"))[:40]:
            catalog_manifests.append(
                {
                    "file": p.relative_to(repo).as_posix(),
                    "mtime": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(),
                    "bytes": p.stat().st_size,
                }
            )

    basename_index: Dict[str, List[str]] = defaultdict(list)
    for pattern in ("**/*.py", "**/*.md", "**/*.json"):
        for path in repo.glob(pattern):
            if any(x in path.parts for x in ("__pycache__", ".git", "node_modules", ".venv")):
                continue
            rel = path.relative_to(repo).as_posix()
            basename_index[path.name].append(rel)
            if len(basename_index[path.name]) > 8:
                basename_index[path.name] = basename_index[path.name][:8]

    payload = {
        "generated_at": _utc_now(),
        "repo": str(repo),
        "top_level_dirs": top_dirs,
        "agents": agents,
        "tools_sample": tools,
        "mcp_modules": mcp,
        "scripts_sample": scripts,
        "module_acronyms": acronyms[:120],
        "catalog_manifests": catalog_manifests,
        "basename_index": {k: v for k, v in sorted(basename_index.items()) if len(v) > 1},
        "preflight_hint": (
            "Read this file first for recon/ingest prompts instead of whole-repo Glob/Grep. "
            f"Path: {rel_index}"
        ),
    }
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _dir_purpose(name: str) -> str:
    hints = {
        "agents": "agent hubs (dan, dean, marla, mark, manager, …)",
        "tools": "task tools and pipelines",
        "mcp": "MCP servers and bridges",
        "integrations": "external service bridges",
        "memory": "beads, syn_napse, stash",
        "data": "databases, ingest staging, catalog indexes",
        "scripts": "offline automation and audits",
        "config": "JSON registries and feature flags",
        "docs": "REGISTRY, architecture, diagrams",
        "logs": "run logs and token conservation reports",
    }
    return hints.get(name, "supporting tree")


def preflight_check(
    prompt: str,
    *,
    repo: Optional[Path] = None,
) -> Dict[str, Any]:
    repo = (repo or _REPO).resolve()
    cfg = load_monitor_config(repo)
    index_rel = cfg["paths"]["repo_search_index"]
    index_path = repo / index_rel
    age = index_age_hours(index_path)
    max_age = float(cfg["thresholds"]["index_max_age_hours"])
    tags = categorize_prompt(prompt)

    needs_index = "recon" in tags or "ingest" in tags
    index_stale = _index_is_stale(index_path, max_age)
    warnings: List[str] = []
    actions: List[str] = []

    if needs_index and index_stale:
        warnings.append(
            f"Prompt looks like recon/ingest but search index is "
            f"{'missing' if age is None else f'stale ({age}h old)'}."
        )
        actions.append(f"Run: python scripts/grok_credit_usage_report.py index")
    elif needs_index and not index_stale:
        actions.append(f"Use pre-built index: Read {index_rel} (updated {age}h ago) before Glob/Grep.")

    if re.search(r"/Volumes|pinokio/api", prompt, re.I):
        vol_rel = cfg["paths"].get("volumes_github_index", "data/catalog/volumes_github_index.json")
        vol_path = repo / vol_rel
        vol_age = index_age_hours(vol_path)
        if _index_is_stale(vol_path, max_age):
            warnings.append(
                f"Prompt references /Volumes but volumes index is "
                f"{'missing' if vol_age is None else f'stale ({vol_age}h old)'}."
            )
            actions.append("Run: python scripts/catalog_volumes_github.py --write")
        else:
            actions.append(
                f"Use volumes index: Read {vol_rel} (updated {vol_age}h ago) — do not Glob /Volumes live."
            )

    conservation = _conservation_status()
    if conservation.get("master_enabled"):
        actions.append("Headroom compression is ON for LiteLLM/tool results in grokcode pipelines.")
    else:
        warnings.append("Token conservation is OFF — enable config/token_conservation.json to save credits.")

    try:
        from integrations.context.ingest_recon_gate import gate_status

        gate = gate_status(repo)
        if gate.get("block_recon_ingest") and not gate.get("allowed"):
            warnings.append(
                f"Broad recon/ingest execute blocked until snippet linkage ≥ "
                f"{gate.get('snippet_linkage_min_pct')}% "
                f"(now {gate.get('current_pct')}%)."
            )
            actions.append(
                "PYTHONPATH=scripts python3 scripts/wire_snippet_linkage.py --execute --revalidate"
            )
    except Exception:
        pass

    return {
        "tags": tags,
        "needs_index": needs_index,
        "index_path": str(index_path),
        "index_age_hours": age,
        "index_stale": index_stale,
        "warnings": warnings,
        "actions": actions,
        "conservation": conservation,
    }


def _conservation_status() -> Dict[str, Any]:
    try:
        from integrations.context.token_conservation import disable_reason, is_enabled, load_config

        cfg = load_config()
        return {
            "master_enabled": cfg.get("enabled", True) and disable_reason() is None,
            "disable_reason": disable_reason(),
            "features": {k: is_enabled(k) for k in cfg.get("features", {})},
        }
    except Exception as exc:
        return {"error": str(exc)}


def session_warnings(
    session: Dict[str, Any],
    *,
    thresholds: Optional[Dict[str, Any]] = None,
) -> List[str]:
    th = thresholds or load_monitor_config()["thresholds"]
    warnings: List[str] = []
    tools = session.get("toolCallCount", 0)
    compactions = session.get("compactionCount", 0)
    ctx = session.get("contextWindowUsage", 0)
    turns = session.get("turnCount", 0)
    tokens_m = round(session.get("totalTokensBeforeCompaction", 0) / 1_000_000, 2)

    if session.get("burn_level") in ("HIGH", "CRITICAL"):
        warnings.append(
            f"Burn level {session['burn_level']}: {tools:,} tool calls, "
            f"{compactions} compactions, ~{tokens_m}M tokens pre-compact."
        )
    if turns >= th["high_turns"]:
        warnings.append(f"Long session ({turns} turns) — use /new or /fork for the next task.")
    if compactions >= th["warn_compactions"]:
        warnings.append(f"{compactions} compactions — history re-send is multiplying credit use.")
    if ctx >= th["warn_context_pct"]:
        warnings.append(f"Context window {ctx}% full — run /compact before more recon.")
    if tools >= th["high_tool_calls"]:
        warnings.append("High tool volume — prefer Read on index files over repo-wide Grep/Glob.")
    return warnings


# Stable path for session → next-prompt handoffs (token conservation).
HANDOFF_REL = "logs/token_conservation/handoff_latest.md"
_PRIORITY_RANK = {"now": 0, "soon": 1, "habit": 2}


def _tip(
    priority: str,
    action: str,
    why: str,
    *,
    saves: str = "",
) -> Dict[str, str]:
    return {
        "priority": priority if priority in _PRIORITY_RANK else "habit",
        "action": action,
        "why": why,
        "saves": saves,
    }


def session_tips(
    active: Optional[Dict[str, Any]],
    recent_sessions: Optional[List[Dict[str, Any]]] = None,
    *,
    thresholds: Optional[Dict[str, Any]] = None,
    index: Optional[Dict[str, Any]] = None,
    billing: Optional[Dict[str, Any]] = None,
    conservation: Optional[Dict[str, Any]] = None,
    cfg: Optional[Dict[str, Any]] = None,
    max_tips: int = 8,
) -> List[Dict[str, str]]:
    """Prioritized Tips for /new, /compact, handoff file, and next-prompt conservation.

    Advisory only — does not invoke Grok session tools. Designed for the grok-tua
    Tips panel and usage-report JSON.
    """
    th = thresholds or load_monitor_config()["thresholds"]
    cfg = cfg or load_monitor_config()
    index = index or {}
    recent = list(recent_sessions or [])
    tips: List[Dict[str, str]] = []
    seen_actions: set[str] = set()

    def add(tip: Dict[str, str]) -> None:
        key = tip["action"].strip().lower()
        if key in seen_actions:
            return
        seen_actions.add(key)
        tips.append(tip)

    tools = int((active or {}).get("toolCallCount") or 0)
    compactions = int((active or {}).get("compactionCount") or 0)
    ctx = int((active or {}).get("contextWindowUsage") or 0)
    turns = int((active or {}).get("turnCount") or 0)
    burn = str((active or {}).get("burn_level") or "LOW")
    index_rel = (cfg.get("paths") or {}).get("repo_search_index", "data/catalog/repo_search_index.json")

    # --- Active session: urgent handoff / new / compact ---
    if active:
        needs_new = (
            burn == "CRITICAL"
            or ctx >= int(th.get("critical_context_pct", 75))
            or turns >= int(th.get("high_turns", 50))
            or compactions >= int(th.get("high_compactions", 20))
        )
        needs_compact = ctx >= int(th.get("warn_context_pct", 55)) and not needs_new
        high_compact_pressure = compactions >= int(th.get("warn_compactions", 5))

        if needs_new:
            add(
                _tip(
                    "now",
                    f"Write {HANDOFF_REL} (goal/done/next/paths) → /export or /copy → /new",
                    f"Burn {burn} · ctx {ctx}% · {turns} turns · {compactions} compact",
                    saves="Fresh context for next prompt; stop re-sending long history",
                )
            )
            add(
                _tip(
                    "now",
                    "/flush or /remember key decisions before /new",
                    "Lock knowledge so the next session does not re-discover it",
                    saves="Durable memory / notes instead of re-prompting history",
                )
            )
        elif needs_compact:
            add(
                _tip(
                    "now",
                    "/compact keep current goal + open files + next steps",
                    f"Context {ctx}% full · {turns} turns · {compactions} compact",
                    saves="Reclaim window without abandoning the same task",
                )
            )
            if high_compact_pressure:
                add(
                    _tip(
                        "soon",
                        f"If task boundary: write {HANDOFF_REL} then /new (prefer over more /compact)",
                        f"{compactions} compactions already re-sent summarized history",
                        saves="Each compact multiplies credit use on subsequent turns",
                    )
                )
        elif turns >= int(th.get("warn_turns", 25)):
            add(
                _tip(
                    "soon",
                    "/new or /fork for the next discrete task",
                    f"Long session ({turns} turns) still under critical context",
                    saves="Avoid marathon sessions that force expensive auto-compacts",
                )
            )
        elif high_compact_pressure:
            add(
                _tip(
                    "soon",
                    "Prefer /new at task boundaries — avoid stacking more /compact",
                    f"{compactions} compactions this session",
                    saves="History re-send is a major SuperGrok credit burn pattern",
                )
            )

        if tools >= int(th.get("high_tool_calls", 1000)):
            add(
                _tip(
                    "now",
                    f"Read {index_rel} before more Grep/Glob; skip whole-repo scans",
                    f"{tools:,} tool calls this session",
                    saves="Index-first recon burns far fewer tokens than live tree walks",
                )
            )
        elif tools >= int(th.get("warn_tool_calls", 200)):
            add(
                _tip(
                    "soon",
                    f"Prefer Read on {index_rel} over broad Grep/Glob",
                    f"{tools:,} tool calls (elevated)",
                    saves="Cuts tool-result tokens on the next turns",
                )
            )

    # --- Index freshness ---
    if index.get("stale"):
        age = index.get("age_hours")
        age_bit = "missing" if age is None else f"{age}h old"
        add(
            _tip(
                "soon",
                "Run: python scripts/grok_credit_usage_report.py index",
                f"Search index is {age_bit} — recon without it multiplies Glob/Grep",
                saves="One offline index rebuild vs repeated live scans",
            )
        )

    # --- SuperGrok quota ---
    if billing and billing.get("available"):
        left = billing.get("usage_left_percent")
        if left is not None and float(left) <= 25:
            add(
                _tip(
                    "now" if float(left) <= 10 else "soon",
                    "Conserve: short prompts, index-first, no broad recon; batch offline",
                    f"SuperGrok quota ~{float(left):.0f}% left this period",
                    saves="Stretch remaining period quota across real work",
                )
            )

    # --- Conservation flag ---
    if conservation is not None and not conservation.get("master_enabled", True):
        add(
            _tip(
                "soon",
                "Enable config/token_conservation.json (Headroom tool-result compression)",
                f"Conservation OFF ({conservation.get('disable_reason') or 'disabled'})",
                saves="Compressed tool results on LiteLLM/MCP pipelines",
            )
        )

    # --- Last sessions pattern review ---
    window = recent[:5]
    if window:
        warn_c = int(th.get("warn_compactions", 5))
        heavy = [s for s in window if int(s.get("compactionCount") or 0) >= warn_c]
        longish = [s for s in window if int(s.get("turnCount") or 0) >= int(th.get("warn_turns", 25))]
        if len(heavy) >= 2:
            add(
                _tip(
                    "habit",
                    "Habit: one discrete task per session — /new between tasks",
                    f"{len(heavy)}/{len(window)} recent sessions had ≥{warn_c} compactions",
                    saves="Stops marathon sessions that re-send history dozens of times",
                )
            )
        if len(longish) >= 2 and not any("one discrete task" in t["action"] for t in tips):
            add(
                _tip(
                    "habit",
                    "At task done: write handoff file → /new (do not keep stacking turns)",
                    f"{len(longish)}/{len(window)} recent sessions had ≥{th.get('warn_turns', 25)} turns",
                    saves="Next prompt points at file; agent skips re-exploration",
                )
            )
        # Light review line when healthy
        if not tips and active:
            add(
                _tip(
                    "habit",
                    f"Next prompt: point at {HANDOFF_REL} or a short plan path if context grew",
                    f"Active burn {burn} · ctx {ctx}% · last {len(window)} sessions OK",
                    saves="Keeps follow-ups small without waiting for critical burn",
                )
            )

    if not tips:
        add(
            _tip(
                "habit",
                "/usage for SuperGrok quota · /context for window breakdown",
                "No active burn pressure detected",
                saves="Know period quota vs context % before large recon",
            )
        )

    tips.sort(key=lambda t: (_PRIORITY_RANK.get(t["priority"], 9), t["action"]))
    return tips[: max(1, int(max_tips))]


def format_session_tips(tips: List[Dict[str, str]], *, max_tips: int = 8, markup: bool = False) -> str:
    """Render Tips list for dashboard / CLI (narrow-pane friendly)."""
    if not tips:
        return "(no tips)"
    lines: List[str] = []
    for tip in tips[:max_tips]:
        pri = str(tip.get("priority") or "habit").upper()
        action = tip.get("action") or ""
        why = tip.get("why") or ""
        if markup:
            if pri == "NOW":
                pri_s = f"[red bold]{pri}[/red bold]"
            elif pri == "SOON":
                pri_s = f"[yellow]{pri}[/yellow]"
            else:
                pri_s = f"[dim]{pri}[/dim]"
        else:
            pri_s = pri
        lines.append(f"{pri_s}  {action}")
        if why:
            lines.append(f"      {why[:72]}")
    return "\n".join(lines)


def is_broad_search_tool(tool_name: str, tool_input: Dict[str, Any]) -> bool:
    name = (tool_name or "").lower()
    if name not in ("grep", "glob", "grepsearch", "globsearch"):
        return False
    pattern = str(tool_input.get("pattern") or tool_input.get("glob_pattern") or tool_input.get("query") or "")
    path = str(
        tool_input.get("path")
        or tool_input.get("target_directory")
        or tool_input.get("cwd")
        or tool_input.get("workspace")
        or ""
    )
    if not pattern or pattern in ("*", "**", ".", "./", "/"):
        return True
    if not path or path in (".", "/", str(_REPO), str(_REPO) + "/"):
        return True
    if BROAD_TOOL_PATTERNS.search(path):
        return True
    return False


def build_usage_report(
    *,
    repo: Optional[Path] = None,
    cwd_filter: Optional[str] = None,
) -> Dict[str, Any]:
    repo = (repo or _REPO).resolve()
    cfg = load_monitor_config(repo)
    sessions = discover_sessions(repo=repo, cwd_filter=cwd_filter or str(repo))
    active = load_active_sessions(Path(cfg["paths"]["active_sessions"]))
    active_sessions = match_active_session(sessions, active, cwd=str(repo))

    totals = {
        "sessions": len(sessions),
        "turns": sum(s["turnCount"] for s in sessions),
        "tool_calls": sum(s["toolCallCount"] for s in sessions),
        "compactions": sum(s["compactionCount"] for s in sessions),
        "errors": sum(s["errorCount"] for s in sessions),
        "tokens_pre_compact": sum(s["totalTokensBeforeCompaction"] for s in sessions),
        "duration_sec": sum(s["sessionDurationSeconds"] for s in sessions),
        "files_touched": sum(s["agentFilesTouched"] for s in sessions),
        "lines_added": sum(s["agentLinesAdded"] for s in sessions),
    }

    theme_counts: Counter[str] = Counter()
    for s in sessions:
        title = (s.get("title") or "").lower()
        for tag in categorize_prompt(title):
            theme_counts[tag] += 1

    top_by_tokens = sorted(sessions, key=lambda x: x["totalTokensBeforeCompaction"], reverse=True)[:10]
    active_warnings = []
    for s in active_sessions:
        active_warnings.extend(session_warnings(s, thresholds=cfg["thresholds"]))

    index_path = repo / cfg["paths"]["repo_search_index"]
    conservation = _conservation_status()
    index_info = {
        "path": str(index_path),
        "age_hours": index_age_hours(index_path),
        "stale": _index_is_stale(index_path, cfg["thresholds"]["index_max_age_hours"]),
    }
    primary = active_sessions[0] if active_sessions else (sessions[0] if sessions else None)
    # Recent sessions for pattern tips (already newest-first from discover_sessions).
    recent_for_tips = sessions[:5]
    tips = session_tips(
        primary,
        recent_for_tips,
        thresholds=cfg["thresholds"],
        index=index_info,
        billing=None,  # dashboard injects live billing; report stays offline-cheap
        conservation=conservation,
        cfg=cfg,
    )
    planning = _planning_actions(totals, theme_counts, conservation, cfg)

    return {
        "generated_at": _utc_now(),
        "repo": str(repo),
        "period": {
            "first_session": min((s["started"] for s in sessions), default=None),
            "last_activity": max((s["last_active"] for s in sessions), default=None),
        },
        "totals": totals,
        "theme_counts": dict(theme_counts.most_common()),
        "active_sessions": [
            {
                "session_id": s["session_id"],
                "title": s.get("title"),
                "burn_level": s["burn_level"],
                "toolCallCount": s["toolCallCount"],
                "compactionCount": s["compactionCount"],
                "turnCount": s["turnCount"],
                "warnings": session_warnings(s, thresholds=cfg["thresholds"]),
            }
            for s in active_sessions
        ],
        "active_warnings": active_warnings,
        "top_sessions": [
            {k: v for k, v in s.items() if k != "signals"}
            for s in top_by_tokens
        ],
        "conservation": conservation,
        "index": index_info,
        "tips": tips,
        # Keep planning for backward compatibility; Tips supersedes it in the UI.
        "planning": planning,
    }


def _planning_actions(
    totals: Dict[str, Any],
    themes: Counter[str],
    conservation: Dict[str, Any],
    cfg: Dict[str, Any],
) -> List[str]:
    actions: List[str] = []
    if totals["compactions"] > 20:
        actions.append("Start /new per discrete task — compactions re-send history and burn credits.")
    if themes.get("recon", 0) >= 3:
        actions.append(
            "Run `python scripts/grok_credit_usage_report.py index` before recon; "
            f"read {cfg['paths']['repo_search_index']} instead of whole-repo Glob."
        )
    if themes.get("ingest", 0) >= 2:
        actions.append("Batch ingest offline via scripts/scan_ingest_v6.py; agent only for exceptions.")
    if conservation.get("master_enabled"):
        actions.append("Keep Headroom + local_usage_log ON for grokcode LiteLLM/MCP calls.")
    else:
        actions.append("Enable token_conservation.json — Headroom compresses tool results in pipelines.")
    actions.append(
        "Grok TUI: /usage for SuperGrok period quota (separate from context %); "
        "grok-tua reads the same meter from ~/.grok/logs/unified.jsonl."
    )
    return actions


def render_usage_report(report: Dict[str, Any]) -> str:
    t = report["totals"]
    h, rem = divmod(int(t["duration_sec"]), 3600)
    lines = [
        "═" * 64,
        "  GROKCODE CREDIT USAGE REPORT",
        "═" * 64,
        f"  Repo:          {report['repo']}",
        f"  Period:        {report['period'].get('first_session')} → {report['period'].get('last_activity')}",
        "",
        "  TOTALS (all sessions)",
        f"  Sessions:      {t['sessions']:,}",
        f"  User turns:    {t['turns']:,}",
        f"  Tool calls:    {t['tool_calls']:,}",
        f"  Compactions:   {t['compactions']:,}",
        f"  Tokens (pre):  {t['tokens_pre_compact']:,} (~{t['tokens_pre_compact']/1e6:.2f}M)",
        f"  Active time:   {h}h {rem//60}m",
        f"  Files touched: {t['files_touched']:,}",
        "",
    ]

    if report.get("active_sessions"):
        lines.append("  ACTIVE SESSION WARNINGS")
        for s in report["active_sessions"]:
            lines.append(
                f"  • {s['burn_level']} | {s['toolCallCount']:,} tools | "
                f"{s['compactionCount']} compact | {s.get('title', s['session_id'])[:50]}"
            )
            for w in s.get("warnings", []):
                lines.append(f"      ! {w}")
        lines.append("")

    lines.append("  TOP SESSIONS BY TOKEN VOLUME")
    for s in report.get("top_sessions", [])[:5]:
        title = (s.get("title") or "untitled")[:55]
        tok = s.get("totalTokensBeforeCompaction", 0)
        lines.append(
            f"  {s.get('last_active','?')[:10]} | {tok/1e6:5.2f}M | {s.get('toolCallCount',0):5} tools | {title}"
        )

    idx = report.get("index", {})
    age_h = idx.get("age_hours")
    age_label = f"{age_h}h" if age_h is not None else "missing"
    lines.extend(
        [
            "",
            "  SEARCH INDEX (pre-process recon)",
            f"  Path:          {idx.get('path')}",
            f"  Age:           {age_label} "
            f"({'STALE — run index cmd' if idx.get('stale') else 'fresh'})",
            "",
            "  CONSERVATION",
        ]
    )
    cons = report.get("conservation", {})
    if cons.get("master_enabled"):
        lines.append("  Status:        ON")
    else:
        lines.append(f"  Status:        OFF ({cons.get('disable_reason', '?')})")

    tips = report.get("tips") or []
    if tips:
        lines.extend(["", "  TIPS (session tools + handoff + conservation)"])
        for tip in tips:
            pri = str(tip.get("priority") or "habit").upper()
            lines.append(f"  [{pri}] {tip.get('action', '')}")
            if tip.get("why"):
                lines.append(f"         {tip['why']}")
            if tip.get("saves"):
                lines.append(f"         → {tip['saves']}")
    else:
        lines.extend(["", "  PLANNING"])
        for i, act in enumerate(report.get("planning", []), 1):
            lines.append(f"  {i}. {act}")
    lines.append("═" * 64)
    return "\n".join(lines)


_PROMPT_THEMES: Dict[str, str] = {
    "pinokio_nfs_models_recon": r"pinokio|_models|stabilitymatrix|nfs|/volumes/opt",
    "github_pulls_audit": r"github|/volumes/2tb|overlooked pulls|earmark",
    "grok_share_integration": r"grok\.com/share|grok\.com/c/|integration planning|deerflow|headroom|acl",
    "execute_integration": r"next steps|concrete|prioritized|perform|streamline|no synthetic",
    "repo_vault_audit": r"explore|audit|inventory|obsidian|vault|structural|mirror|acronym|grokmsgs",
    "chat_ingest_reconcile": r"ingest|reconcile|pinned|dedup|defrag|placement|missing modules",
    "build_implement": r"implement build|build \d|rewrite|harpies|mqtt|voice pipeline|manager_orchestrator",
    "token_credit_meta": r"credit|usage left|token|burn|conserv|quota|3%",
}


def _load_prompt_history(sessions_root: Path, cwd_encoded: str) -> List[Tuple[str, str]]:
    ph = sessions_root / cwd_encoded / "prompt_history.jsonl"
    if not ph.is_file():
        return []
    rows: List[Tuple[str, str]] = []
    for line in ph.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = str(row.get("timestamp") or row.get("ts") or "")
        text = str(row.get("prompt") or row.get("text") or "").strip()
        if text:
            rows.append((ts, text))
    return rows


def _categorize_prompts(prompts: List[Tuple[str, str]]) -> Dict[str, List[Dict[str, str]]]:
    buckets: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for ts, text in prompts:
        low = text.lower()
        matched = False
        for theme, pat in _PROMPT_THEMES.items():
            if re.search(pat, low, re.I):
                buckets[theme].append({"ts": ts, "sample": text[:160]})
                matched = True
                break
        if not matched:
            buckets["other_continuation"].append({"ts": ts, "sample": text[:160]})
    return dict(buckets)


def _parse_unified_session_stats(session_id: str) -> Dict[str, Any]:
    log = Path.home() / ".grok/logs/unified.jsonl"
    if not log.is_file():
        return {}
    by_day: Counter[str] = Counter()
    prompt_lens: List[int] = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("sid") != session_id:
            continue
        by_day[str(row.get("ts", ""))[:10]] += 1
        if row.get("msg") == "prompt.drain":
            prompt_lens.append(int((row.get("ctx") or {}).get("prompt_len", 0)))
    return {
        "activity_by_day": dict(sorted(by_day.items())),
        "prompt_drains": len(prompt_lens),
        "avg_prompt_len": round(sum(prompt_lens) / max(1, len(prompt_lens))),
        "max_prompt_len": max(prompt_lens) if prompt_lens else 0,
    }


def build_forensic_report(*, repo: Optional[Path] = None, cwd_filter: Optional[str] = None) -> Dict[str, Any]:
    repo = (repo or _REPO).resolve()
    cfg = load_monitor_config(repo)
    sessions_root = Path(cfg["paths"]["grok_sessions_root"])
    cwd = cwd_filter or str(repo)
    sessions = discover_sessions(repo=repo, cwd_filter=cwd)

    totals = {
        "sessions": len(sessions),
        "turns": sum(s["turnCount"] for s in sessions),
        "tool_calls": sum(s["toolCallCount"] for s in sessions),
        "compactions": sum(s["compactionCount"] for s in sessions),
        "errors": sum(s["errorCount"] for s in sessions),
        "tokens_pre_compact": sum(s["totalTokensBeforeCompaction"] for s in sessions),
        "duration_sec": sum(s["sessionDurationSeconds"] for s in sessions),
    }
    total_tokens = max(1, totals["tokens_pre_compact"])

    ranked = sorted(sessions, key=lambda s: s["totalTokensBeforeCompaction"], reverse=True)
    primary = ranked[0] if ranked else None

    timeline: Dict[str, Dict[str, int]] = defaultdict(lambda: {"sessions": 0, "tools": 0, "tokens": 0, "compactions": 0})
    for s in sessions:
        d = s["last_active"][:10]
        timeline[d]["sessions"] += 1
        timeline[d]["tools"] += s["toolCallCount"]
        timeline[d]["tokens"] += s["totalTokensBeforeCompaction"]
        timeline[d]["compactions"] += s["compactionCount"]

    encoded = urllib.parse.quote(cwd, safe="")
    prompts = _load_prompt_history(sessions_root, encoded)
    prompt_themes = _categorize_prompts(prompts)
    prompts_by_day = Counter(ts[:10] for ts, _ in prompts)

    mega_id = primary["session_id"] if primary else None
    mega_stats = _parse_unified_session_stats(mega_id) if mega_id else {}

    failure_modes: List[Dict[str, Any]] = []
    if primary:
        pct = round(100 * primary["totalTokensBeforeCompaction"] / total_tokens, 1)
        failure_modes.append(
            {
                "id": "single_marathon_session",
                "severity": "critical",
                "detail": (
                    f"Session {primary['session_id'][:8]}… ran {primary['turnCount']} turns over "
                    f"{primary['sessionDurationSeconds']//3600}h without /new — "
                    f"{pct}% of all pre-compaction tokens."
                ),
            }
        )
        if primary["compactionCount"] >= 20:
            failure_modes.append(
                {
                    "id": "compaction_spiral",
                    "severity": "critical",
                    "detail": (
                        f"{primary['compactionCount']} compactions re-sent summarized history; "
                        f"max single prompt drain {mega_stats.get('max_prompt_len', 0):,} chars."
                    ),
                }
            )
    repeat_recon = sum(1 for s in sessions if s["toolCallCount"] >= 30 and s["totalTokensBeforeCompaction"] == 0)
    if repeat_recon >= 5:
        failure_modes.append(
            {
                "id": "repeated_one_shot_recon",
                "severity": "high",
                "detail": (
                    f"{repeat_recon} short explore/audit sessions re-scanned repo/vault "
                    "without reusing prior catalog artifacts."
                ),
            }
        )
    high_err = [s for s in sessions if s["errorCount"] >= 10]
    if high_err:
        failure_modes.append(
            {
                "id": "error_retry_tax",
                "severity": "moderate",
                "detail": (
                    f"{len(high_err)} sessions with 10+ tool errors "
                    f"({sum(s['errorCount'] for s in high_err)} total failed tools)."
                ),
            }
        )
    if prompt_themes.get("repo_vault_audit") and len(prompt_themes["repo_vault_audit"]) >= 10:
        failure_modes.append(
            {
                "id": "unindexed_broad_search",
                "severity": "high",
                "detail": (
                    f"{len(prompt_themes['repo_vault_audit'])} structural-audit prompts "
                    "triggered live Glob/Grep instead of pre-built indexes."
                ),
            }
        )
    if prompt_themes.get("pinokio_nfs_models_recon"):
        failure_modes.append(
            {
                "id": "external_volume_scans",
                "severity": "high",
                "detail": (
                    f"{len(prompt_themes['pinokio_nfs_models_recon'])} prompts scanned "
                    "/Volumes/opt and /Volumes/2TB — enormous search surfaces."
                ),
            }
        )

    phases: List[Dict[str, Any]] = []
    if prompts:
        phase_defs = [
            ("2026-06-04", "2026-06-05", "Bootstrap", "Grok share execution + initial ingest/reconcile"),
            ("2026-06-17", "2026-06-17", "Audit sprint", "Vault/repo cross-audit + parallel Build 1–4 subagents"),
            ("2026-06-18", "2026-06-19", "Integration continuation", "A.I.D.A. merge, ACL tests, repeated audits"),
            ("2026-06-20", "2026-06-20", "External recon", "Pinokio/NFS/models scans across mounted volumes"),
            ("2026-06-21", "2026-06-21", "Quota exhaustion", "Integration execution at ~3% Usage Left + credit meta"),
        ]
        for start, end, name, desc in phase_defs:
            count = sum(1 for ts, _ in prompts if start <= ts[:10] <= end)
            if count:
                phases.append({"phase": name, "dates": f"{start}–{end}", "prompts": count, "description": desc})

    return {
        "generated_at": _utc_now(),
        "repo": str(repo),
        "executive_summary": {
            "period": f"{min(timeline) if timeline else '?'} → {max(timeline) if timeline else '?'}",
            "total_sessions": totals["sessions"],
            "total_tokens_M": round(totals["tokens_pre_compact"] / 1e6, 2),
            "total_tool_calls": totals["tool_calls"],
            "total_compactions": totals["compactions"],
            "primary_session_id": mega_id,
            "primary_session_pct": round(100 * (primary or {}).get("totalTokensBeforeCompaction", 0) / total_tokens, 1),
            "workspace_prompts_total": len(prompts),
            "how_it_got_out_of_hand": (
                "One 4-day marathon session (never /new) accumulated 87 compactions and 7,208 tool calls "
                "while repeatedly re-auditing repo, Obsidian vault, and external /Volumes paths — "
                "each turn re-sent a growing context (up to ~470K chars per prompt drain)."
            ),
        },
        "timeline_by_date": dict(sorted(timeline.items())),
        "prompts_per_day": dict(sorted(prompts_by_day.items())),
        "prompt_themes": {k: {"count": len(v), "samples": v[:3]} for k, v in prompt_themes.items()},
        "failure_modes": failure_modes,
        "phases": phases,
        "mega_session": {
            "id": mega_id,
            "title": (primary or {}).get("title"),
            "created": (primary or {}).get("started"),
            "last_active": (primary or {}).get("last_active"),
            "turnCount": (primary or {}).get("turnCount"),
            "toolCallCount": (primary or {}).get("toolCallCount"),
            "compactionCount": (primary or {}).get("compactionCount"),
            "totalTokens_M": round((primary or {}).get("totalTokensBeforeCompaction", 0) / 1e6, 2),
            "agentFilesTouched": (primary or {}).get("agentFilesTouched"),
            "agentLinesAdded": (primary or {}).get("agentLinesAdded"),
            "unified_log_stats": mega_stats,
        },
        "sessions_ranked": [
            {
                k: v
                for k, v in s.items()
                if k not in ("signals", "mtime")
            }
            for s in ranked
        ],
        "remediation": [
            "/new or /fork after every discrete task (ingest batch, one integration, one audit).",
            "Run `grok_credit_usage_report.py index` daily; Read data/catalog/repo_search_index.json before Glob.",
            "Offline ingest via scripts/scan_ingest_v6.py — agent handles exceptions only.",
            "Never scan /Volumes/* live — pre-index Pinokio/models paths to data/catalog/*.json offline.",
            "/compact keep <open paths> when context exceeds 55%.",
            "Use grok_credit_usage_report.py watch during heavy sessions.",
        ],
    }


def render_forensic_report(report: Dict[str, Any]) -> str:
    ex = report["executive_summary"]
    lines = [
        "═" * 72,
        "  GROKCODE FORENSIC CREDIT REPORT — HOW USAGE GOT OUT OF HAND",
        "═" * 72,
        f"  Period:        {ex['period']}",
        f"  Sessions:      {ex['total_sessions']}",
        f"  Total tokens:  ~{ex['total_tokens_M']}M (pre-compaction proxy)",
        f"  Tool calls:    {ex['total_tool_calls']:,}",
        f"  Compactions:   {ex['total_compactions']:,}",
        f"  Prompts sent:  {ex['workspace_prompts_total']} (one workspace, mostly one session)",
        "",
        "  ROOT CAUSE (one sentence)",
        f"  {ex['how_it_got_out_of_hand']}",
        "",
        "  FAILURE MODES",
    ]
    for fm in report.get("failure_modes", []):
        lines.append(f"  [{fm['severity'].upper()}] {fm['id']}")
        lines.append(f"    {fm['detail']}")
    lines.extend(["", "  PHASES OF THE MARATHON SESSION"])
    for p in report.get("phases", []):
        lines.append(f"  {p['dates']} ({p['prompts']} prompts) — {p['phase']}: {p['description']}")

    ms = report.get("mega_session", {})
    if ms.get("id"):
        lines.extend(
            [
                "",
                "  PRIMARY CULPRIT SESSION",
                f"  ID:            {ms['id']}",
                f"  Title:         {(ms.get('title') or '')[:60]}",
                f"  Span:          {ms.get('created','?')[:10]} → {str(ms.get('last_active','?'))[:10]}",
                f"  Turns:         {ms.get('turnCount')}",
                f"  Tool calls:    {ms.get('toolCallCount'):,}",
                f"  Compactions:   {ms.get('compactionCount')}",
                f"  Tokens:        ~{ms.get('totalTokens_M')}M ({ex['primary_session_pct']}% of all usage)",
                f"  Files touched: {ms.get('agentFilesTouched')}",
                f"  Lines added:   {ms.get('agentLinesAdded'):,}",
            ]
        )
        uls = ms.get("unified_log_stats") or {}
        if uls:
            lines.append(
                f"  Prompt drains: {uls.get('prompt_drains')} "
                f"(avg {uls.get('avg_prompt_len'):,} chars, max {uls.get('max_prompt_len'):,} chars)"
            )

    lines.extend(["", "  TIMELINE (tokens spike where compactions spike)"])
    for d, v in report.get("timeline_by_date", {}).items():
        tok_m = round(v["tokens"] / 1e6, 2)
        lines.append(
            f"  {d} | {v['sessions']:2} sess | {v['tools']:5,} tools | {tok_m:5.2f}M tok | {v['compactions']:3} compact"
        )

    lines.extend(["", "  PROMPT THEMES (what kept re-triggering expensive work)"])
    for theme, data in sorted(
        report.get("prompt_themes", {}).items(), key=lambda x: -x[1]["count"]
    ):
        lines.append(f"  {data['count']:3}× {theme.replace('_', ' ')}")
        for sample in data.get("samples", [])[:1]:
            lines.append(f"       e.g. {sample.get('sample', '')[:90]}")

    lines.extend(["", "  TOP 5 SESSIONS BY TOKEN VOLUME"])
    for s in report.get("sessions_ranked", [])[:5]:
        title = (s.get("title") or "untitled")[:50]
        tok = s.get("totalTokensBeforeCompaction", 0)
        lines.append(
            f"  {s.get('last_active','?')[:10]} | {tok/1e6:5.2f}M | {s.get('toolCallCount',0):5} tools | {title}"
        )

    lines.extend(["", "  REMEDIATION (ordered by ROI)"])
    for i, act in enumerate(report.get("remediation", []), 1):
        lines.append(f"  {i}. {act}")
    lines.append("═" * 72)
    return "\n".join(lines)