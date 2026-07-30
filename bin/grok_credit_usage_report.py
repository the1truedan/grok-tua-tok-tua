#!/usr/bin/env python3
"""
Grok Build credit usage report, live warnings, and pre-search index for grokcode.

Usage:
  python scripts/grok_credit_usage_report.py              # full report
  python scripts/grok_credit_usage_report.py report --json
  python scripts/grok_credit_usage_report.py warn       # exit 1 if active session HIGH/CRITICAL
  python scripts/grok_credit_usage_report.py watch      # poll active session burn
  python scripts/grok_credit_usage_report.py index      # rebuild repo_search_index.json
  python scripts/grok_credit_usage_report.py preflight "explore grokcode structure"
  python scripts/grok_credit_usage_report.py hook       # Grok lifecycle hook (stdin JSON)
  python scripts/grok_credit_usage_report.py forensic   # how credit usage got out of hand
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from context.grok_credit_monitor import (  # noqa: E402
    build_forensic_report,
    build_repo_search_index,
    build_usage_report,
    burn_level,
    discover_sessions,
    is_broad_search_tool,
    load_active_sessions,
    load_monitor_config,
    match_active_session,
    preflight_check,
    render_forensic_report,
    render_usage_report,
    session_warnings,
)


def _save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def cmd_report(args: argparse.Namespace) -> int:
    report = build_usage_report(repo=REPO, cwd_filter=args.cwd)
    cfg = load_monitor_config(REPO)
    out = REPO / cfg["paths"]["usage_report"]
    snap = REPO / cfg["paths"]["burn_snapshot"]
    _save_json(out, report)
    if report.get("active_sessions"):
        _save_json(
            snap,
            {
                "generated_at": report["generated_at"],
                "active_sessions": report["active_sessions"],
            },
        )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_usage_report(report))
        print(f"\nSaved: {out}")
    return 0


def cmd_warn(args: argparse.Namespace) -> int:
    cfg = load_monitor_config(REPO)
    sessions = discover_sessions(repo=REPO, cwd_filter=args.cwd or str(REPO))
    active = load_active_sessions(Path(cfg["paths"]["active_sessions"]))
    matched = match_active_session(sessions, active, cwd=args.cwd or str(REPO))

    if not matched:
        print("No active grokcode session detected.")
        return 0

    worst = "LOW"
    lines: List[str] = []
    for s in matched:
        level = s["burn_level"]
        if level == "CRITICAL" or (level == "HIGH" and worst != "CRITICAL"):
            worst = level
        lines.append(
            f"[{level}] {s.get('title') or s['session_id']}: "
            f"{s['toolCallCount']:,} tools, {s['compactionCount']} compactions, "
            f"{s['turnCount']} turns"
        )
        for w in session_warnings(s, thresholds=cfg["thresholds"]):
            lines.append(f"  ! {w}")

    print("\n".join(lines))
    if worst in ("HIGH", "CRITICAL") and args.strict:
        return 1
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    cfg = load_monitor_config(REPO)
    poll = args.interval or int(cfg["thresholds"]["watch_poll_seconds"])
    cwd = args.cwd or str(REPO)
    seen: Dict[str, int] = {}

    print(f"Watching active sessions for {cwd} every {poll}s (Ctrl+C to stop)")
    try:
        while True:
            sessions = discover_sessions(repo=REPO, cwd_filter=cwd)
            active = load_active_sessions(Path(cfg["paths"]["active_sessions"]))
            matched = match_active_session(sessions, active, cwd=cwd)
            for s in matched:
                sid = s["session_id"]
                tools = s["toolCallCount"]
                prev = seen.get(sid, tools)
                delta = tools - prev
                seen[sid] = tools
                if delta > 0 or s["burn_level"] in ("HIGH", "CRITICAL"):
                    print(
                        f"[{s['burn_level']}] +{delta} tools (total {tools:,}) | "
                        f"compact={s['compactionCount']} ctx={s['contextWindowUsage']}% | "
                        f"{(s.get('title') or sid)[:60]}"
                    )
                    for w in session_warnings(s, thresholds=cfg["thresholds"]):
                        print(f"  ! {w}")
            time.sleep(poll)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    payload = build_repo_search_index(REPO)
    cfg = load_monitor_config(REPO)
    path = REPO / cfg["paths"]["repo_search_index"]
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Index written: {path}")
        print(f"  top-level dirs: {len(payload.get('top_level_dirs', {}))}")
        print(f"  agents: {len(payload.get('agents', []))}")
        print(f"  acronyms: {len(payload.get('module_acronyms', []))}")
        print(f"  catalog manifests: {len(payload.get('catalog_manifests', []))}")
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    prompt = " ".join(args.prompt) if args.prompt else ""
    if not prompt and not sys.stdin.isatty():
        try:
            hook_in = json.load(sys.stdin)
            prompt = str(hook_in.get("prompt") or hook_in.get("userPrompt") or "")
        except json.JSONDecodeError:
            prompt = sys.stdin.read()
    result = preflight_check(prompt, repo=REPO)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Tags: {', '.join(result['tags'])}")
        for w in result.get("warnings", []):
            print(f"WARNING: {w}")
        for a in result.get("actions", []):
            print(f"ACTION:  {a}")
    return 1 if result.get("warnings") and args.strict else 0


def cmd_forensic(args: argparse.Namespace) -> int:
    report = build_forensic_report(repo=REPO, cwd_filter=args.cwd)
    cfg = load_monitor_config(REPO)
    out = REPO / cfg["paths"].get("forensic_report", "logs/token_conservation/grokcode_forensic_credit_report.json")
    _save_json(out, report)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_forensic_report(report))
        print(f"\nSaved: {out}")
    return 0


def cmd_hook() -> int:
    """Grok lifecycle hook — reads JSON event from stdin."""
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    cfg = load_monitor_config(REPO)
    if not cfg.get("features", {}).get("credit_monitor", True):
        return 0

    hook_name = (event.get("hookEventName") or "").lower()
    cwd = event.get("cwd") or event.get("workspaceRoot") or ""
    if "grokcode" not in str(cwd):
        return 0

    messages: List[str] = []

    if hook_name in ("user_prompt_submit", "beforesubmitprompt"):
        prompt = str(event.get("prompt") or event.get("userPrompt") or "")
        pf = preflight_check(prompt, repo=REPO)
        sessions = discover_sessions(repo=REPO, cwd_filter=str(REPO))
        active = load_active_sessions(Path(cfg["paths"]["active_sessions"]))
        for s in match_active_session(sessions, active, cwd=str(REPO)):
            messages.extend(session_warnings(s, thresholds=cfg["thresholds"]))
        messages.extend(pf.get("warnings", []))
        messages.extend(pf.get("actions", []))

    elif hook_name in ("pre_tool_use", "beforetoolexecution"):
        if not cfg.get("features", {}).get("block_broad_grep_glob", False):
            return 0
        tool = event.get("toolName") or ""
        tool_input = event.get("toolInput") or {}
        if is_broad_search_tool(tool, tool_input):
            index_rel = cfg["paths"]["repo_search_index"]
            print(
                json.dumps(
                    {
                        "decision": "deny",
                        "reason": (
                            f"Broad {tool} burns credits. Read {index_rel} first "
                            "(run: python scripts/grok_credit_usage_report.py index)."
                        ),
                    }
                )
            )
            return 0

    elif hook_name in ("session_start", "sessionstart"):
        age = None
        index_path = REPO / cfg["paths"]["repo_search_index"]
        if index_path.is_file():
            from context.grok_credit_monitor import index_age_hours

            age = index_age_hours(index_path)
        if age is None or age > cfg["thresholds"]["index_max_age_hours"]:
            messages.append(
                "Credit monitor: search index stale/missing — "
                "run `python scripts/grok_credit_usage_report.py index` before recon."
            )

    if messages:
        print("\n".join(f"[credit-monitor] {m}" for m in messages), file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Grok credit usage report and warnings")
    parser.add_argument("--cwd", default=str(REPO), help="Workspace filter")
    parser.add_argument("--json", action="store_true", help="JSON output")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("report", help="Full usage report (default)")
    p_warn = sub.add_parser("warn", help="Warn on active session burn")
    p_warn.add_argument("--strict", action="store_true", help="Exit 1 on HIGH/CRITICAL")

    p_watch = sub.add_parser("watch", help="Poll active session tool-call deltas")
    p_watch.add_argument("--interval", type=int, help="Poll seconds")

    sub.add_parser("index", help="Rebuild repo search index")
    p_pf = sub.add_parser("preflight", help="Check prompt vs index/conservation")
    p_pf.add_argument("prompt", nargs="*", help="Prompt text to analyze")
    p_pf.add_argument("--strict", action="store_true", help="Exit 1 on warnings")

    sub.add_parser("hook", help="Grok lifecycle hook entry (stdin JSON)")
    sub.add_parser("forensic", help="Forensic report: how credit usage got out of hand")

    args = parser.parse_args()
    cmd = args.command or "report"

    if cmd == "report":
        return cmd_report(args)
    if cmd == "forensic":
        return cmd_forensic(args)
    if cmd == "warn":
        return cmd_warn(args)
    if cmd == "watch":
        return cmd_watch(args)
    if cmd == "index":
        return cmd_index(args)
    if cmd == "preflight":
        return cmd_preflight(args)
    if cmd == "hook":
        return cmd_hook()
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())