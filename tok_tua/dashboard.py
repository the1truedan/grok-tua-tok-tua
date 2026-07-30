"""tok-tua metrics dashboard (headless snapshot + optional Textual)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tok_tua.providers import (  # noqa: E402
    defaults,
    enrich_provider_versions,
    resolve_launch,
)
from tok_tua.session_adapters import session_hint_for  # noqa: E402
from tok_tua.stack_metrics import fetch_stack_metrics, format_stack_metrics  # noqa: E402

try:
    from textual.app import App, ComposeResult
    from textual.containers import ScrollableContainer, Vertical
    from textual.widgets import Footer, Header, Static

    _HAS_TEXTUAL = True
except ImportError:
    _HAS_TEXTUAL = False


def format_wrap_facades(providers: List[Dict[str, Any]]) -> str:
    """Launch-registry façades not already covered by the Stack version board.

    codex/claude/pi/omp/opencode/tau/aider/grok/cursor/turnstone/herdr all
    appear (with status + version + path) in the Stack panel's version board —
    listing them again here would just duplicate that panel. Only the wrap
    façades (openrouter-wrap, gemini-wrap) are unique to the launch registry.
    """
    wraps = [p for p in providers if p.get("kind") == "wrap"]
    if not wraps:
        return "  (none configured)"
    lines = []
    for r in wraps:
        status = r.get("status") or "?"
        host = r.get("host_cli") or "?"
        path = r.get("path") or r.get("error") or ""
        lines.append(f"  {r['id']:<18} {status:<7} host={host:<6} {path}")
    return "\n".join(lines)


def fetch_dashboard_state(
    repo: Path | None = None,
    *,
    cli: str | None = None,
    model: str | None = None,
    qqq: str | None = None,
) -> Dict[str, Any]:
    repo = (repo or _REPO).resolve()
    d = defaults()
    cli = cli or os.environ.get("TOK_TUA_CLI") or d.get("cli")
    model = model or os.environ.get("TOK_TUA_MODEL") or d.get("model")
    billing = None
    try:
        from context.grok_credit_monitor import fetch_billing_from_unified_log

        billing = fetch_billing_from_unified_log()
    except Exception:
        billing = None
    stack = fetch_stack_metrics(billing=billing)
    providers = enrich_provider_versions(with_versions=True)
    plan: Dict[str, Any] | None = None
    plan_error: str | None = None
    try:
        plan = resolve_launch(cli, model, qqq_mode=qqq, require_available=False)
    except ValueError as exc:
        plan_error = str(exc)
    hint = session_hint_for(plan.get("effective_cli") or cli or "codex") if plan else {}
    from tok_tua.qqq import current_mode, qqq_mode_label

    qqq_mode = plan.get("qqq_mode") if plan else current_mode(qqq)[0]
    return {
        "repo": str(repo),
        "cli": cli,
        "model": model,
        "plan": plan,
        "plan_error": plan_error,
        "stack": stack,
        "billing": billing,
        "providers": providers,
        "session_hint": hint,
        "qqq_mode": qqq_mode,
        "qqq_label": qqq_mode_label(qqq_mode),
        "poll_seconds": 30,
    }


def render_snapshot(state: Dict[str, Any]) -> str:
    stack_text = format_stack_metrics(
        state.get("stack") or {},
        markup=False,
        billing=state.get("billing"),
    )
    plan = state.get("plan") or {}
    hint = state.get("session_hint") or {}
    lines = [
        "═" * 64,
        "  TOK-TUA DASHBOARD SNAPSHOT",
        "═" * 64,
        f"  Repo:   {state.get('repo')}",
        f"  CLI:    {state.get('cli')} → {plan.get('effective_cli') or '?'}",
        f"  Model:  {state.get('model')}  cloud={plan.get('cloud')}",
        f"  Cmd:    {plan.get('short_command') or state.get('plan_error') or '—'}",
        f"  QQQ:    {state.get('qqq_label') or state.get('qqq_mode') or 'QQQ0'}",
        "",
        "  GATEWAY · STACK (CLI versions + paths) · CLOUD CREDITS",
        stack_text,
        "",
        "  WRAP FAÇADES (not in Stack board)",
        format_wrap_facades(state.get("providers") or []),
        "",
    ]
    if hint.get("available"):
        lines.extend(
            [
                "  SESSION HINT",
                f"  {hint.get('cli')}: {hint.get('thread_name') or hint.get('title') or hint.get('id') or ''}",
                f"  {hint.get('updated_at') or hint.get('path') or ''}",
                "",
            ]
        )
    elif hint.get("note") or hint.get("error"):
        lines.extend(
            [
                "  SESSION HINT",
                f"  {hint.get('note') or hint.get('error')}",
                "",
            ]
        )
    lines.extend(
        [
            "  TIPS",
            "  tok-tua --cli codex --model manager-auto",
            "  tok-tua --cli openrouter-wrap   # free cloud façade (public only)",
            "  tok-tua --scale herdr | turnstone",
            "  tok-tua spawn --cli omp --model manager-openrouter-free",
            "  turnstone-cli health | models",
            "  PHI: use manager-phi-local; never cloud wraps",
            "═" * 64,
        ]
    )
    return "\n".join(lines)


def render_launch_banner(state: Dict[str, Any]) -> str:
    """Compact metrics printed before tmux attach."""
    stack = state.get("stack") or {}
    plan = state.get("plan") or {}
    hr = stack.get("headroom") or {}
    ll = stack.get("litellm") or {}
    her = stack.get("herdr") or {}
    ts = stack.get("turnstone") or {}
    hint = state.get("session_hint") or {}
    lines = [
        f"[tok-tua] model={state.get('model')}  cli={plan.get('cli') or state.get('cli')} "
        f"→ {plan.get('effective_cli')}  cloud={plan.get('cloud')}",
        f"[tok-tua] path={stack.get('path')}  headroom={'ok' if hr.get('ready') else 'down'} "
        f"litellm={'ok' if ll.get('alive') else 'down'}  "
        f"herdr={'ok' if her.get('running') else 'n/a'}  "
        f"turnstone={'ok' if ts.get('ready') else 'down'}",
        f"[tok-tua] cmd={plan.get('short_command') or state.get('plan_error') or '—'}",
        f"[tok-tua] qqq={state.get('qqq_label') or state.get('qqq_mode') or 'QQQ0'}",
    ]
    credits = stack.get("credits") or {}
    for row in (credits.get("rows") or [])[:4]:
        if row.get("available") or row.get("status") == "ok":
            lines.append(f"[tok-tua] credit {row.get('label')}: {row.get('summary')}")
    if plan.get("cloud"):
        lines.append("[tok-tua] WARN: cloud model — public code only; no PHI")
    if hint.get("available"):
        lines.append(
            f"[tok-tua] session: {hint.get('thread_name') or hint.get('title') or hint.get('id')}"
        )
    return "\n".join(lines)


if _HAS_TEXTUAL:

    class TokTUADashboard(App):
        TITLE = "tok-tua"
        SUB_TITLE = "Gateway · CLI versions · cloud credits · multi-CLI"

        CSS = """
        Screen { background: $surface; }
        #body { height: 1fr; padding: 0 1; }
        .panel-title { text-style: bold; color: $accent; height: 1; }
        """

        def __init__(self, repo: Path, state: Dict[str, Any], poll_seconds: int = 30) -> None:
            super().__init__()
            self.repo = repo
            self._state = state
            self.poll_seconds = poll_seconds

        def compose(self) -> ComposeResult:
            yield Header()
            yield ScrollableContainer(
                Vertical(
                    Static("GATEWAY · STACK (CLI versions + paths) · CREDITS", classes="panel-title"),
                    Static(id="stack-body"),
                    Static("LAUNCH", classes="panel-title"),
                    Static(id="launch-body"),
                    Static("WRAP FAÇADES (not in Stack board)", classes="panel-title"),
                    Static(id="prov-body"),
                    Static("TIPS", classes="panel-title"),
                    Static(
                        "tok-tua --cli codex\ntok-tua spawn --cli omp\nturnstone-cli health\n"
                        "curl -s localhost:8765/api/stack/stats | head",
                        id="tips-body",
                    ),
                    id="body",
                )
            )
            yield Footer()

        def on_mount(self) -> None:
            self._apply()
            self.set_interval(self.poll_seconds, self._refresh)

        def _refresh(self) -> None:
            self._state = fetch_dashboard_state(
                self.repo,
                cli=self._state.get("cli"),
                model=self._state.get("model"),
                qqq=self._state.get("qqq_mode"),
            )
            self._apply()

        def _apply(self) -> None:
            st = self._state
            plan = st.get("plan") or {}
            self.query_one("#stack-body", Static).update(
                format_stack_metrics(
                    st.get("stack") or {},
                    markup=True,
                    billing=st.get("billing"),
                )
            )
            launch = (
                f"cli={st.get('cli')} → {plan.get('effective_cli')}\n"
                f"model={st.get('model')} cloud={plan.get('cloud')}\n"
                f"{plan.get('short_command') or st.get('plan_error') or ''}"
            )
            self.query_one("#launch-body", Static).update(launch)
            self.query_one("#prov-body", Static).update(
                format_wrap_facades(st.get("providers") or [])
            )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="tok-tua metrics dashboard")
    parser.add_argument("--repo", type=Path, default=_REPO)
    parser.add_argument("--check", action="store_true", help="Print snapshot and exit")
    parser.add_argument("--cli", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--qqq", default=None, help="QQQ mode: 0|1|3 (see config/qqq_orchestration.json)")
    parser.add_argument("--poll", type=int, default=None)
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    state = fetch_dashboard_state(repo, cli=args.cli, model=args.model, qqq=args.qqq)
    if args.check:
        print(render_snapshot(state))
        return 0
    if not _HAS_TEXTUAL:
        print("textual required: uv pip install textual", file=sys.stderr)
        print(render_snapshot(state))
        return 1
    poll = args.poll or state.get("poll_seconds", 30)
    TokTUADashboard(repo, state, poll_seconds=poll).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
