"""
grok-tua reactive metrics dashboard for grokcode.

Panels:
  - Gateway stack: Headroom → LiteLLM health/spend + Herdr status
  - Local system meters (CPU/RAM/GPU % left of bars; Apple ioreg on Apple Silicon Macs)
  - Active Grok Build session burn + per-turn/session tokens & $ (session files)
  - SuperGrok quota left (from ~/.grok/logs/unified.jsonl billing fetch)
  - Conservation status + search index freshness
  - Git recent commits
  - Tips: /new · /compact · handoff file · next-prompt token conservation

Run:
  python -m grok_tua.dashboard
  python -m grok_tua.dashboard --check
  grok-tua dashboard   # via wrapper
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from context.grok_credit_monitor import (  # noqa: E402
    build_usage_report,
    cost_usd_from_ticks,
    discover_sessions,
    fetch_billing_from_unified_log,
    format_session_tips,
    load_active_sessions,
    load_monitor_config,
    match_active_session,
    maybe_rebuild_stale_index,
    session_tips,
    session_warnings,
)
from grok_tua.stack_metrics import (  # noqa: E402
    fetch_stack_metrics,
    format_stack_metrics,
)

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, ScrollableContainer, Vertical
    from textual.widgets import Footer, Header, ProgressBar, Static

    _HAS_TEXTUAL = True
except ImportError:
    _HAS_TEXTUAL = False


BURN_CLASS = {
    "LOW": "burn-low",
    "MODERATE": "burn-moderate",
    "HIGH": "burn-high",
    "CRITICAL": "burn-critical",
}


def _mac_gpu_stats() -> Dict[str, Any]:
    """Apple Silicon GPU utilization from IOAccelerator (no root / no nvidia-smi)."""
    try:
        import re
        import subprocess

        proc = subprocess.run(
            ["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        text = proc.stdout or ""
        device = re.findall(r'"Device Utilization %"\s*=\s*(\d+)', text)
        renderer = re.findall(r'"Renderer Utilization %"\s*=\s*(\d+)', text)
        mem = re.findall(r'"In use system memory"\s*=\s*(\d+)', text)
        cores = re.findall(r'"gpu-core-count"\s*=\s*(\d+)', text)
        if not device and not renderer:
            return {"available": False}
        util = float(device[0] if device else renderer[0])
        return {
            "available": True,
            "source": "ioreg",
            "utilization_percent": util,
            "memory_used_mb": round(float(mem[0]) / 1024**2, 1) if mem else None,
            "gpu_core_count": int(cores[0]) if cores else None,
        }
    except Exception:
        return {"available": False}


def _collect_gpu() -> Dict[str, Any]:
    """Local GPU: NVIDIA when present, else Apple Silicon IOAccelerator."""
    try:
        from tools.metrics_collector import _gpu

        gpu = _gpu()
        if isinstance(gpu, dict) and gpu.get("available"):
            return gpu
    except Exception:
        pass
    mac = _mac_gpu_stats()
    if mac.get("available"):
        return mac
    return {"available": False}


def _system_stats() -> Dict[str, Any]:
    """CPU/RAM/Disk via psutil when available; GPU always attempted (ioreg on Apple Silicon Macs)."""
    stats: Dict[str, Any] = {}
    try:
        import psutil

        vm = psutil.virtual_memory()
        # Single overall CPU % (not per-core). Short sample so first poll is not 0.
        stats["cpu_pct"] = float(psutil.cpu_percent(interval=0.05))
        stats["mem_pct"] = float(vm.percent)
        stats["mem_used_gb"] = vm.used / (1024**3)
        stats["mem_total_gb"] = vm.total / (1024**3)
        stats["disk_pct"] = float(psutil.disk_usage("/").percent)
    except Exception as exc:
        stats["psutil_error"] = str(exc)
        try:
            stats["load_1m"] = float(os.getloadavg()[0])
        except (OSError, AttributeError):
            pass
    stats["gpu"] = _collect_gpu()
    try:
        host = os.uname().nodename.split(".")[0]
        stats["host"] = host
    except Exception:
        pass
    return stats


def _git_recent(repo: Path, limit: int = 8) -> List[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "log", "--oneline", f"-{limit}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip().splitlines()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ["(no git log)"]


def _fmt_pct(n: Any) -> str:
    try:
        return f"{float(n):4.0f}%"
    except (TypeError, ValueError):
        return "  —%"


def _format_system(stats: Dict[str, Any], *, meters: bool = False) -> str:
    """Plain snapshot text. When meters=True (TUI body), omit CPU/RAM % (shown on bars)."""
    if stats.get("error") and not stats.get("gpu") and stats.get("cpu_pct") is None:
        return f"[red]System stats unavailable: {stats['error']}[/red]"

    lines: List[str] = []
    host = stats.get("host")
    if host:
        lines.append(f"Host  {host}")

    if not meters:
        # Snapshot / --check: one line each, single overall CPU % only.
        if stats.get("cpu_pct") is not None:
            lines.append(f"CPU   {_fmt_pct(stats['cpu_pct']).strip()}")
        elif stats.get("load_1m") is not None:
            lines.append(f"Load  {stats['load_1m']:.2f} (1m)")
        elif stats.get("psutil_error"):
            lines.append(f"CPU   — ({stats['psutil_error'][:40]})")

        if stats.get("mem_pct") is not None:
            mem = f"RAM   {_fmt_pct(stats['mem_pct']).strip()}"
            if stats.get("mem_used_gb") is not None and stats.get("mem_total_gb") is not None:
                mem += f"  ({stats['mem_used_gb']:.1f}/{stats['mem_total_gb']:.1f} GB)"
            lines.append(mem)

    if stats.get("disk_pct") is not None:
        lines.append(f"Disk  {_fmt_pct(stats['disk_pct']).strip()}")

    gpu = stats.get("gpu") if isinstance(stats.get("gpu"), dict) else None
    if gpu and gpu.get("available"):
        util = gpu.get("utilization_percent")
        mem = gpu.get("memory_used_mb")
        cores = gpu.get("gpu_core_count")
        src = gpu.get("source") or "gpu"
        # Normalize source label for Apple Silicon
        if "ioreg" in str(src):
            src = "ioreg"
        bit = f"GPU   {_fmt_pct(util).strip()}" if isinstance(util, (int, float)) else "GPU   n/a"
        extras = []
        if mem is not None:
            extras.append(f"{float(mem):.0f}MB")
        if cores:
            extras.append(f"{cores}c")
        extras.append(str(src))
        if not meters:
            lines.append(bit + ("  · " + " · ".join(extras) if extras else ""))
        else:
            # TUI: GPU % is on the bar; body keeps short extras only.
            lines.append("GPU   " + " · ".join(extras))
    else:
        if not meters:
            lines.append("GPU   — (no telemetry)")
        else:
            lines.append("GPU   — (no telemetry)")

    if not lines:
        return "[dim](no system stats)[/dim]"
    return "\n".join(lines)


def _fmt_int(n: Any) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "0"


def _fmt_usd(n: Any) -> str:
    try:
        return f"${float(n):.4f}"
    except (TypeError, ValueError):
        return "$?"


def _format_session(sess: Dict[str, Any], warnings: List[str], *, markup: bool = True) -> str:
    title = (sess.get("title") or sess.get("session_id", "?"))[:52]
    level = sess.get("burn_level", "LOW")
    ctx_pct = int(sess.get("contextWindowUsage") or 0)
    ctx_used = int(sess.get("contextTokensUsed") or 0)
    ctx_win = int(sess.get("contextWindowTokens") or 500_000)
    sid = str(sess.get("session_id") or "")[:8]
    source = sess.get("metrics_source") or (sess.get("signals") or {}).get("metricsSource") or "signals"
    title_line = f"[bold]{title}[/bold]" if markup else title
    warn_hdr = "[yellow]Warnings[/yellow]" if markup else "Warnings"
    if source == "derived":
        src_note = "derived"
    elif "live" in str(source):
        src_note = "live"
    else:
        src_note = "signals"
    model = sess.get("primaryModelId") or ""
    if model and len(model) > 22:
        model = model[:20] + "…"
    lines = [
        title_line,
        f"Id        {sid}…  [{src_note}]",
        f"Burn      {level}" + (f"  · {model}" if model else ""),
        f"Tools     {_fmt_int(sess.get('toolCallCount', 0))}",
        f"Compact   {sess.get('compactionCount', 0)}",
        f"Turns     {sess.get('turnCount', 0)}",
        f"Context   {ctx_pct}%  {_fmt_int(ctx_used)} / {_fmt_int(ctx_win)}",
    ]
    last = sess.get("last_turn_usage") if isinstance(sess.get("last_turn_usage"), dict) else {}
    if last:
        lines.append(
            "Last turn "
            f"in {_fmt_int(last.get('inputTokens'))}  "
            f"out {_fmt_int(last.get('outputTokens'))}  "
            f"reason {_fmt_int(last.get('reasoningTokens'))}"
        )
        last_cost = cost_usd_from_ticks(
            last.get("costUsdTicks")
            if last.get("costUsdTicks") is not None
            else last.get("total_cost_usd_ticks")
        )
        call_bits = []
        if last.get("modelCalls") is not None:
            call_bits.append(f"calls {last.get('modelCalls')}")
        call_bits.append(f"cache {_fmt_int(last.get('cachedReadTokens'))}")
        if last_cost is not None:
            call_bits.append(_fmt_usd(last_cost))
        if call_bits:
            lines.append(f"          {'  '.join(call_bits)}")

    full = sess.get("session_usage_full") if isinstance(sess.get("session_usage_full"), dict) else {}
    if full and int(full.get("turnCompleted") or 0) >= 1:
        complete = full.get("complete", True)
        tag = "Σ session" if complete else "Σ ~file"
        lines.append(
            f"{tag} "
            f"in {_fmt_int(full.get('inputTokens'))}  "
            f"out {_fmt_int(full.get('outputTokens'))}  "
            f"turns {int(full.get('turnCompleted') or 0)}"
        )
        cost = full.get("costUsd")
        twc = int(full.get("turnsWithCost") or 0)
        nturns = int(full.get("turnCompleted") or 0)
        if cost is not None and twc > 0:
            if twc < nturns:
                lines.append(f"          cost {_fmt_usd(cost)}  ({twc}/{nturns} turns w/ $)")
            else:
                lines.append(f"          cost {_fmt_usd(cost)}")
        elif nturns:
            lines.append("          cost n/a (OAuth / unstamped)")
    else:
        tail = sess.get("session_usage_tail") if isinstance(sess.get("session_usage_tail"), dict) else {}
        if tail and int(tail.get("turnCompletedInTail") or 0) > 1:
            lines.append(
                "Σ tail    "
                f"in {_fmt_int(tail.get('inputTokens'))}  "
                f"out {_fmt_int(tail.get('outputTokens'))}"
            )
            if tail.get("costUsd") is not None:
                lines.append(f"          cost {_fmt_usd(tail.get('costUsd'))}")
    tok_pre = int(sess.get("totalTokensBeforeCompaction") or 0)
    if tok_pre:
        lines.append(f"Tokens~   {_fmt_int(tok_pre)} (pre-compact)")
    if warnings:
        lines.append("")
        lines.append(warn_hdr)
        for w in warnings[:6]:
            lines.append(f"  • {w[:72]}")
    return "\n".join(lines)


def _format_billing(billing: Dict[str, Any], *, markup: bool = True) -> str:
    """SuperGrok / period quota from Grok's unified.jsonl billing fetch."""
    if not billing or not billing.get("available"):
        err = (billing or {}).get("error") or "unavailable"
        note = "run /usage in left TUI"
        if markup:
            return f"[dim]Quota     — ({err})\n          {note}[/dim]"
        return f"Quota     — ({err})\n          {note}"

    left = billing.get("usage_left_percent")
    used = billing.get("credit_usage_percent")
    tier = billing.get("subscription_tier") or "SuperGrok"
    period = billing.get("period_label") or "period"
    end = billing.get("period_end_short") or ""
    age = billing.get("age_hours")
    stale = billing.get("stale")

    left_s = f"{left:.0f}%" if left is not None else "?"
    used_s = f"{used:.0f}%" if used is not None else "?"
    end_bit = f" ends {end}" if end else ""
    age_bit = ""
    if age is not None:
        if age < 1:
            age_bit = f"  · {int(age * 60)}m ago"
        else:
            age_bit = f"  · {age:.1f}h ago"
    stale_bit = "  STALE" if stale else ""

    left_line = f"Quota     ~{left_s} left  (used {used_s})"
    if markup and left is not None:
        if left <= 10:
            left_line = f"[red]{left_line}[/red]"
        elif left <= 25:
            left_line = f"[yellow]{left_line}[/yellow]"
        else:
            left_line = f"[green]{left_line}[/green]"

    prepaid = billing.get("prepaid_balance")
    prepaid_line = ""
    if prepaid is not None and prepaid > 0:
        prepaid_line = f"\nPrepaid   ${prepaid:.2f}"

    return (
        f"{left_line}\n"
        f"          {tier} · {period}{end_bit}{age_bit}{stale_bit}"
        f"{prepaid_line}\n"
        f"          (from Grok billing log · /usage)"
    )


def _format_credit(report: Dict[str, Any], billing: Optional[Dict[str, Any]] = None) -> str:
    t = report.get("totals", {})
    idx = report.get("index", {})
    cons = report.get("conservation", {})
    age = idx.get("age_hours")
    age_label = f"{age}h" if age is not None else "missing"
    stale = "STALE" if idx.get("stale") else "fresh"
    status = "ON" if cons.get("master_enabled") else f"OFF ({cons.get('disable_reason', '?')})"
    tok_m = t.get("tokens_pre_compact", 0) / 1e6
    # Index = data/catalog/repo_search_index.json (token-conserving layout map), not model pool.
    bill_block = _format_billing(billing or {}, markup=True)
    return (
        f"{bill_block}\n"
        f"Sessions  {t.get('sessions', 0):,}\n"
        f"Tool calls {t.get('tool_calls', 0):,}\n"
        f"Compact   {t.get('compactions', 0):,}\n"
        f"Tokens~   {tok_m:.2f}M (pre-compact)\n"
        f"Conserv   {status}\n"
        f"Index     {age_label} ({stale})\n"
        f"          repo_search_index"
    )


def _format_git(lines: List[str]) -> str:
    return "\n".join(f"  {ln[:70]}" for ln in lines)


def _format_tips(tips: List[Dict[str, Any]], *, markup: bool = False) -> str:
    return format_session_tips(tips, max_tips=8, markup=markup)


def _compute_tips(
    *,
    primary: Optional[Dict[str, Any]],
    sessions: List[Dict[str, Any]],
    report: Dict[str, Any],
    billing: Optional[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> List[Dict[str, str]]:
    features = cfg.get("features") or {}
    if features.get("session_tips") is False:
        # Fall back to legacy planning strings as habit tips.
        return [
            {"priority": "habit", "action": a, "why": "", "saves": ""}
            for a in (report.get("planning") or [])[:6]
        ]
    return session_tips(
        primary,
        sessions[:5],
        thresholds=cfg.get("thresholds"),
        index=report.get("index") or {},
        billing=billing,
        conservation=report.get("conservation"),
        cfg=cfg,
    )


def fetch_dashboard_state(repo: Path) -> Dict[str, Any]:
    """Collect all panel data (usable without Textual)."""
    cfg = load_monitor_config(repo)
    cwd = str(repo)
    sessions = discover_sessions(repo=repo, cwd_filter=cwd)
    active = load_active_sessions(Path(cfg["paths"]["active_sessions"]))
    matched = match_active_session(sessions, active, cwd=cwd)
    report = build_usage_report(repo=repo, cwd_filter=cwd)
    billing = fetch_billing_from_unified_log()
    stack = fetch_stack_metrics(billing=billing)

    primary: Optional[Dict[str, Any]] = matched[0] if matched else (sessions[0] if sessions else None)
    warnings: List[str] = []
    if primary:
        warnings = session_warnings(primary, thresholds=cfg["thresholds"])

    tips = _compute_tips(
        primary=primary,
        sessions=sessions,
        report=report,
        billing=billing,
        cfg=cfg,
    )

    return {
        "repo": cwd,
        "system": _system_stats(),
        "session": primary,
        "session_warnings": warnings,
        "credit": report,
        "billing": billing,
        "stack": stack,
        "git": _git_recent(repo),
        "tips": tips,
        "planning": report.get("planning", []),
        "poll_seconds": int(cfg["thresholds"].get("watch_poll_seconds", 30)),
    }


def render_snapshot(state: Dict[str, Any]) -> str:
    """Plain-text snapshot for --check mode."""
    stack_text = format_stack_metrics(
        state.get("stack") or {},
        markup=False,
        session=state.get("session"),
        billing=state.get("billing"),
    )
    lines = [
        "═" * 64,
        "  GROK-TUA DASHBOARD SNAPSHOT",
        "═" * 64,
        f"  Repo: {state['repo']}",
        "",
        "  GATEWAY · CLI VERSIONS · CLOUD CREDITS",
        stack_text,
        "",
        "  SYSTEM",
        _format_system(state["system"]).replace("[red]", "").replace("[/red]", ""),
        "",
    ]
    if state.get("session"):
        lines.extend(
            [
                "  ACTIVE SESSION (Grok Build files)",
                _format_session(state["session"], state["session_warnings"], markup=False),
                "",
            ]
        )
    else:
        lines.extend(["  ACTIVE SESSION", "  (none detected)", ""])
    bill_plain = _format_billing(state.get("billing") or {}, markup=False)
    # Strip markup leftovers from conservation block helper
    credit_text = _format_credit(state["credit"], state.get("billing"))
    for tag in ("[red]", "[/red]", "[yellow]", "[/yellow]", "[green]", "[/green]", "[dim]", "[/dim]"):
        credit_text = credit_text.replace(tag, "")
    tips_text = _format_tips(state.get("tips") or [], markup=False)
    lines.extend(
        [
            "  SUPERGROK QUOTA + CONSERVATION",
            credit_text if credit_text else bill_plain,
            "",
            "  GIT (recent)",
            _format_git(state["git"]),
            "",
            "  TIPS (session tools · handoff · conservation)",
            tips_text,
            "═" * 64,
        ]
    )
    return "\n".join(lines)


if _HAS_TEXTUAL:

    class GrokTUADashboard(App):
        """Reactive dashboard: gateway stack + local Grok Build session burn."""

        TITLE = "grok-tua"
        SUB_TITLE = "Headroom → LiteLLM · Herdr · session burn · tips"

        # Single-column stack suits the narrow tmux stats pane (~20% width).
        CSS = """
        Screen {
            background: $surface;
        }
        #sidebar {
            width: 100%;
            height: 1fr;
        }
        .panel-title {
            text-style: bold;
            color: $accent;
            height: 1;
            margin-bottom: 0;
        }
        .panel {
            border: solid $primary-darken-2;
            padding: 0 1;
            width: 100%;
            height: auto;
            min-height: 3;
            margin-bottom: 0;
        }
        #stack-panel {
            min-height: 10;
        }
        #system-panel {
            min-height: 6;
        }
        #session-panel {
            min-height: 8;
        }
        #credit-panel {
            min-height: 6;
        }
        #git-panel {
            min-height: 4;
            max-height: 8;
        }
        #tips-panel {
            min-height: 5;
        }
        .burn-low { color: $success; }
        .burn-moderate { color: $warning; }
        .burn-high { color: #e67e22; }
        .burn-critical { color: $error; }
        .meter-row {
            height: 1;
            width: 100%;
            margin-top: 0;
        }
        .meter-key {
            width: 4;
            height: 1;
            color: $text-muted;
            text-style: bold;
        }
        .meter-pct {
            width: 5;
            height: 1;
            color: $text;
            text-align: right;
            margin-right: 1;
        }
        .meter-row ProgressBar {
            width: 1fr;
            height: 1;
            margin-top: 0;
        }
        ProgressBar {
            margin-top: 0;
            height: 1;
        }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("r", "refresh", "Refresh"),
            Binding("i", "index_hint", "Index hint"),
        ]

        def __init__(self, repo: Path, *, poll_seconds: int = 30) -> None:
            super().__init__()
            self.repo = repo.resolve()
            self.poll_seconds = poll_seconds
            self._state: Dict[str, Any] = {}
            self._index_rebuild_attempted = False

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            with ScrollableContainer(id="sidebar"):
                with Vertical(id="stack-panel", classes="panel"):
                    yield Static("Gateway · CLIs · Credits", classes="panel-title")
                    yield Static("(loading…)", id="stack-body")
                with Vertical(id="system-panel", classes="panel"):
                    yield Static("System", classes="panel-title")
                    # Percents live on the left of each meter (single overall CPU %).
                    with Horizontal(classes="meter-row"):
                        yield Static("CPU", classes="meter-key")
                        yield Static("  —%", id="cpu-pct", classes="meter-pct")
                        yield ProgressBar(total=100, show_eta=False, id="cpu-bar")
                    with Horizontal(classes="meter-row"):
                        yield Static("RAM", classes="meter-key")
                        yield Static("  —%", id="mem-pct", classes="meter-pct")
                        yield ProgressBar(total=100, show_eta=False, id="mem-bar")
                    with Horizontal(classes="meter-row"):
                        yield Static("GPU", classes="meter-key")
                        yield Static("  —%", id="gpu-pct", classes="meter-pct")
                        yield ProgressBar(total=100, show_eta=False, id="gpu-bar")
                    yield Static("", id="system-body")
                with Vertical(id="session-panel", classes="panel"):
                    yield Static("Active Session", classes="panel-title")
                    yield Static("(loading…)", id="session-body")
                    with Horizontal(classes="meter-row"):
                        yield Static("Ctx", classes="meter-key")
                        yield Static("  —%", id="ctx-pct", classes="meter-pct")
                        yield ProgressBar(total=100, show_eta=False, id="ctx-bar")
                with Vertical(id="credit-panel", classes="panel"):
                    yield Static("Quota · Conservation", classes="panel-title")
                    yield Static("", id="credit-body")
                with Vertical(id="git-panel", classes="panel"):
                    yield Static("Git", classes="panel-title")
                    yield Static("", id="git-body")
                with Vertical(id="tips-panel", classes="panel"):
                    yield Static("Tips", classes="panel-title")
                    yield Static("", id="tips-body")
            yield Footer()

        def on_mount(self) -> None:
            self.set_interval(2.0, self._poll_fast)
            # Stack spend/health between full credit reports (session $ + LiteLLM).
            self.set_interval(10.0, self._poll_stack)
            self.set_interval(float(self.poll_seconds), self._poll_full)
            self._refresh()

        def action_refresh(self) -> None:
            self._refresh()

        def action_index_hint(self) -> None:
            self.notify(
                "Index = data/catalog/repo_search_index.json · "
                "python scripts/grok_credit_usage_report.py index",
                title="Refresh search index",
                timeout=8,
            )

        def _poll_fast(self) -> None:
            try:
                cfg = load_monitor_config(self.repo)
                cwd = str(self.repo)
                sessions = discover_sessions(repo=self.repo, cwd_filter=cwd)
                active = load_active_sessions(Path(cfg["paths"]["active_sessions"]))
                matched = match_active_session(sessions, active, cwd=cwd)
                # Most recently active among matched (match already sorts by mtime).
                primary = matched[0] if matched else (sessions[0] if sessions else None)
                warnings = (
                    session_warnings(primary, thresholds=cfg["thresholds"]) if primary else []
                )
                self._state["system"] = _system_stats()
                self._state["session"] = primary
                self._state["sessions_recent"] = sessions[:5]
                self._state["session_warnings"] = warnings
                # Billing log is cheap to tail; refresh with session so quota tracks.
                self._state["billing"] = fetch_billing_from_unified_log()
                # Recompute tips with live session + billing (report may be stale until full poll).
                report = self._state.get("credit") or {}
                self._state["tips"] = _compute_tips(
                    primary=primary,
                    sessions=sessions,
                    report=report if report else {"index": {}, "conservation": {}, "planning": []},
                    billing=self._state.get("billing"),
                    cfg=cfg,
                )
                self._apply_fast()
                # Refresh quota block when billing/session change without waiting full poll.
                if self._state.get("credit"):
                    self._apply_credit_panel()
                # Session $ on stack panel moves with active session without full report.
                if self._state.get("stack"):
                    self._apply_stack_panel()
            except Exception as exc:
                self.notify(f"Fast poll error: {exc}", severity="error")

        def _poll_stack(self) -> None:
            """Refresh LiteLLM spend + path health + cloud credits more often."""
            try:
                billing = fetch_billing_from_unified_log()
                self._state["billing"] = billing
                self._state["stack"] = fetch_stack_metrics(billing=billing)
                self._apply_stack_panel()
            except Exception as exc:
                self.notify(f"Stack poll error: {exc}", severity="error")

        def _poll_full(self) -> None:
            try:
                report = build_usage_report(repo=self.repo, cwd_filter=str(self.repo))
                idx = report.get("index") or {}
                if idx.get("stale") and not self._index_rebuild_attempted:
                    self._index_rebuild_attempted = True
                    try:
                        result = maybe_rebuild_stale_index(self.repo, min_interval_hours=6.0)
                        if result.get("rebuilt"):
                            self.notify("Repo search index rebuilt (was STALE)", timeout=5)
                            report = build_usage_report(repo=self.repo, cwd_filter=str(self.repo))
                        elif result.get("reason") == "rate_limited":
                            pass  # silent; LaunchAgent or manual index still available
                    except Exception as rebuild_exc:
                        self.notify(f"Index self-heal failed: {rebuild_exc}", severity="warning")
                self._state["credit"] = report
                billing = fetch_billing_from_unified_log()
                self._state["billing"] = billing
                self._state["stack"] = fetch_stack_metrics(billing=billing)
                self._state["git"] = _git_recent(self.repo)
                self._state["planning"] = report.get("planning", [])
                cfg = load_monitor_config(self.repo)
                self._state["tips"] = _compute_tips(
                    primary=self._state.get("session"),
                    sessions=self._state.get("sessions_recent")
                    or report.get("top_sessions")
                    or [],
                    report=report,
                    billing=self._state.get("billing"),
                    cfg=cfg,
                )
                self._apply_full()
            except Exception as exc:
                self.notify(f"Full poll error: {exc}", severity="error")

        def _refresh(self) -> None:
            self._state = fetch_dashboard_state(self.repo)
            self._apply_fast()
            self._apply_full()

        def _apply_fast(self) -> None:
            sys_stats = self._state.get("system", {})
            # Body: host / disk / GPU extras only — CPU/RAM/GPU % live on left of meters.
            self.query_one("#system-body", Static).update(
                _format_system(sys_stats, meters=True)
            )

            cpu = sys_stats.get("cpu_pct")
            if cpu is not None:
                self.query_one("#cpu-pct", Static).update(_fmt_pct(cpu))
                self.query_one("#cpu-bar", ProgressBar).update(progress=min(int(float(cpu)), 100))
            else:
                self.query_one("#cpu-pct", Static).update("  —%")
                self.query_one("#cpu-bar", ProgressBar).update(progress=0)

            mem = sys_stats.get("mem_pct")
            if mem is not None:
                self.query_one("#mem-pct", Static).update(_fmt_pct(mem))
                self.query_one("#mem-bar", ProgressBar).update(progress=min(int(float(mem)), 100))
            else:
                self.query_one("#mem-pct", Static).update("  —%")
                self.query_one("#mem-bar", ProgressBar).update(progress=0)

            gpu = sys_stats.get("gpu") if isinstance(sys_stats.get("gpu"), dict) else {}
            gutil = gpu.get("utilization_percent") if gpu.get("available") else None
            if isinstance(gutil, (int, float)):
                self.query_one("#gpu-pct", Static).update(_fmt_pct(gutil))
                self.query_one("#gpu-bar", ProgressBar).update(progress=min(int(gutil), 100))
            else:
                self.query_one("#gpu-pct", Static).update("  —%")
                self.query_one("#gpu-bar", ProgressBar).update(progress=0)

            sess = self._state.get("session")
            body = self.query_one("#session-body", Static)
            if sess:
                level = sess.get("burn_level", "LOW")
                body.update(_format_session(sess, self._state.get("session_warnings", [])))
                body.remove_class(*BURN_CLASS.values())
                body.add_class(BURN_CLASS.get(level, "burn-low"))
                ctx = int(sess.get("contextWindowUsage") or 0)
                self.query_one("#ctx-pct", Static).update(_fmt_pct(ctx))
                self.query_one("#ctx-bar", ProgressBar).update(progress=min(ctx, 100))
            else:
                body.update("[dim]No active grokcode session[/dim]")
                self.query_one("#ctx-pct", Static).update("  —%")
                self.query_one("#ctx-bar", ProgressBar).update(progress=0)

            self.query_one("#tips-body", Static).update(
                _format_tips(self._state.get("tips") or [], markup=True)
            )

        def _apply_credit_panel(self) -> None:
            credit = self._state.get("credit") or {}
            billing = self._state.get("billing") or {}
            # Always show quota block even before full report is ready.
            if credit:
                body = _format_credit(credit, billing)
            else:
                body = _format_billing(billing, markup=True)
            self.query_one("#credit-body", Static).update(body)

        def _apply_stack_panel(self) -> None:
            stack = self._state.get("stack") or {}
            self.query_one("#stack-body", Static).update(
                format_stack_metrics(
                    stack,
                    markup=True,
                    session=self._state.get("session"),
                    billing=self._state.get("billing"),
                )
            )

        def _apply_full(self) -> None:
            self._apply_stack_panel()
            self._apply_credit_panel()
            self.query_one("#git-body", Static).update(_format_git(self._state.get("git", [])))
            self.query_one("#tips-body", Static).update(
                _format_tips(self._state.get("tips") or [], markup=True)
            )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="grok-tua credit-awareness dashboard")
    parser.add_argument("--repo", type=Path, default=_REPO, help="grokcode repo root")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Print snapshot and exit (no TUI; verifies wiring)",
    )
    parser.add_argument(
        "--poll",
        type=int,
        default=None,
        help="Full-report poll interval seconds (default from token_conservation.json)",
    )
    args = parser.parse_args(argv)
    repo = args.repo.resolve()

    state = fetch_dashboard_state(repo)
    if args.check:
        print(render_snapshot(state))
        return 0

    if not _HAS_TEXTUAL:
        print("textual is required: uv pip install textual", file=sys.stderr)
        print(render_snapshot(state))
        return 1

    poll = args.poll or state.get("poll_seconds", 30)
    GrokTUADashboard(repo, poll_seconds=poll).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())