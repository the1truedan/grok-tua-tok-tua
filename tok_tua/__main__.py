"""python -m tok_tua — stack / providers / spawn / dashboard."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def main(argv: Optional[List[str]] = None) -> int:
    argv_list = list(argv) if argv is not None else sys.argv[1:]
    # Dispatch before argparse so flags like --cli reach loop.main.
    if argv_list and argv_list[0] == "loop":
        from tok_tua.loop import main as loop_main

        return loop_main(argv_list[1:])

    parser = argparse.ArgumentParser(prog="tok-tua", description="Token Textual User Agent")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("stack", help="Gateway stack snapshot")
    sub.add_parser("providers", help="List CLI providers + PATH status")

    p_dash = sub.add_parser("dashboard", help="Metrics dashboard")
    p_dash.add_argument("--check", action="store_true")
    p_dash.add_argument("--cli", default=None)
    p_dash.add_argument("--model", default=None)
    p_dash.add_argument("--qqq", default=None, help="QQQ mode: 0|1|3 (see config/qqq_orchestration.json)")

    p_resolve = sub.add_parser("resolve", help="Resolve launch plan JSON")
    p_resolve.add_argument("--cli", default=None)
    p_resolve.add_argument("--model", default=None)
    p_resolve.add_argument("--qqq", default=None, help="QQQ mode: 0|1|3 (see config/qqq_orchestration.json)")

    p_spawn = sub.add_parser("spawn", help="Spawn herdr agent (allowlisted)")
    p_spawn.add_argument("--cli", default=None)
    p_spawn.add_argument("--model", default=None)
    p_spawn.add_argument("--name", default="tok-spawn")
    p_spawn.add_argument("--cwd", default=None)
    p_spawn.add_argument("--dry-run", action="store_true")

    sub.add_parser(
        "loop",
        help="Herdr wait-loop + handoff writeout (default dry-run; use --live)",
    )

    p_ts = sub.add_parser("turnstone", help="Turnstone REST (pass-through)")
    p_ts.add_argument("ts_args", nargs=argparse.REMAINDER)

    p_voice = sub.add_parser("voice", help="Voice mode (V.O.X. + talk2ya PTT/TTS)")
    p_voice.add_argument(
        "voice_args",
        nargs=argparse.REMAINDER,
        help="check | speak TEXT | transcribe PATH | ptt | prompt TEXT",
    )

    args = parser.parse_args(argv_list)
    cmd = args.cmd or "stack"

    if cmd == "stack":
        from tok_tua.dashboard import fetch_dashboard_state, render_launch_banner

        state = fetch_dashboard_state(_REPO)
        print(render_launch_banner(state))
        from tok_tua.stack_metrics import format_stack_metrics

        print(format_stack_metrics(state["stack"], markup=False))
        return 0

    if cmd == "providers":
        from tok_tua.providers import format_provider_table, list_providers

        print(format_provider_table(list_providers()))
        return 0

    if cmd == "dashboard":
        from tok_tua.dashboard import main as dash_main

        dash_argv = []
        if args.check:
            dash_argv.append("--check")
        if args.cli:
            dash_argv.extend(["--cli", args.cli])
        if args.model:
            dash_argv.extend(["--model", args.model])
        if args.qqq:
            dash_argv.extend(["--qqq", args.qqq])
        return dash_main(dash_argv)

    if cmd == "resolve":
        from tok_tua.providers import resolve_launch

        plan = resolve_launch(args.cli, args.model, qqq_mode=args.qqq, require_available=False)
        # Redact long prelude from default print
        public = {k: v for k, v in plan.items() if k not in {"command", "prelude", "provider"}}
        public["path"] = plan.get("path")
        public["short_command"] = plan.get("short_command")
        print(json.dumps(public, indent=2, default=str))
        return 0

    if cmd == "spawn":
        from tok_tua.scale import spawn_herdr_agent

        result = spawn_herdr_agent(
            args.name,
            cli=args.cli,
            model=args.model,
            cwd=args.cwd or str(_REPO),
            dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("ok") else 1

    if cmd == "loop":
        from tok_tua.loop import main as loop_main

        return loop_main([])

    if cmd == "turnstone":
        from tok_tua.turnstone_client import main as ts_main

        ts_args = list(args.ts_args or [])
        if ts_args and ts_args[0] == "--":
            ts_args = ts_args[1:]
        if not ts_args:
            ts_args = ["health"]
        return ts_main(ts_args)

    if cmd == "voice":
        from tok_tua.voice import main as voice_main

        v_args = list(args.voice_args or [])
        if v_args and v_args[0] == "--":
            v_args = v_args[1:]
        # allow: tok-tua voice --check → check
        if v_args == ["--check"] or v_args == ["-c"]:
            v_args = ["check"]
        if not v_args:
            v_args = ["check"]
        return voice_main(v_args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
