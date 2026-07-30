"""Scale modes: single tmux, herdr multipane, turnstone surface."""

from __future__ import annotations

import re
import shlex
import subprocess
from typing import Any

from tok_tua.providers import resolve_launch

# Allowlisted bare commands for herdr agent start (first token of short_command).
_SAFE_BIN = re.compile(r"^[a-zA-Z0-9_./+-]+$")

SCALES = ("single", "herdr", "turnstone")


def normalize_scale(scale: str | None) -> str:
    s = (scale or "single").strip().lower()
    if s not in SCALES:
        raise ValueError(f"unknown scale {scale!r}; known: {', '.join(SCALES)}")
    return s


def herdr_status() -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["herdr", "status"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return {
            "ok": proc.returncode == 0,
            "running": "running" in out.lower(),
            "preview": "\n".join(out.splitlines()[:15]),
        }
    except FileNotFoundError:
        return {"ok": False, "running": False, "error": "herdr not on PATH"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "running": False, "error": str(exc)}


def herdr_agent_list() -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["herdr", "agent", "list"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "output": (proc.stdout or proc.stderr or "").strip(),
        }
    except FileNotFoundError:
        return {"ok": False, "error": "herdr not on PATH"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}


def _argv_from_short_command(short: str) -> list[str]:
    """Parse short CLI template into argv; reject empty / unsafe first token."""
    parts = shlex.split(short)
    if not parts:
        raise ValueError("empty command")
    if not _SAFE_BIN.match(parts[0]):
        raise ValueError(f"refusing unsafe binary token: {parts[0]!r}")
    # Disallow shell metacharacters already handled by shlex; block env= injection chains
    for p in parts:
        if any(c in p for c in (";", "|", "&", "`", "\n", "$(", "${")):
            raise ValueError(f"refusing unsafe argv token: {p!r}")
    return parts


def build_herdr_start_argv(
    name: str,
    plan: dict[str, Any],
    *,
    cwd: str | None = None,
) -> list[str]:
    """Allowlisted herdr agent start argv (no shell)."""
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,48}$", name):
        raise ValueError(f"invalid agent name {name!r}")
    short = plan.get("short_command") or plan.get("effective_cli") or "codex"
    if plan.get("kind") == "url":
        raise ValueError("herdr scale does not start URL surfaces; use turnstone scale")
    cmd_argv = _argv_from_short_command(str(short))
    argv = ["herdr", "agent", "start", name]
    if cwd:
        argv.extend(["--cwd", cwd])
    # Pass gateway env via repeated --env (herdr supports --env KEY=VALUE)
    if plan.get("gateway"):
        # OPENAI_* set by wrapper env when herdr inherits; still pass model hints if needed
        pass
    argv.append("--")
    argv.extend(cmd_argv)
    return argv


def spawn_herdr_agent(
    name: str,
    *,
    cli: str | None = None,
    model: str | None = None,
    cwd: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Start a herdr agent pane with an allowlisted CLI plan."""
    plan = resolve_launch(cli, model, require_available=True)
    argv = build_herdr_start_argv(name, plan, cwd=cwd)
    if dry_run:
        return {"ok": True, "dry_run": True, "argv": argv, "plan": _public_plan(plan)}
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "argv": argv,
            "plan": _public_plan(plan),
            "stdout": (proc.stdout or "").strip()[:2000],
            "stderr": (proc.stderr or "").strip()[:2000],
            "returncode": proc.returncode,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "argv": argv, "error": str(exc), "plan": _public_plan(plan)}


def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "cli": plan.get("cli"),
        "effective_cli": plan.get("effective_cli"),
        "model": plan.get("model"),
        "cloud": plan.get("cloud"),
        "short_command": plan.get("short_command"),
        "kind": plan.get("kind"),
    }
