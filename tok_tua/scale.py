"""Scale modes: single tmux, herdr multipane, turnstone surface."""

from __future__ import annotations

import json
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
    pane_id: str | None = None,
) -> list[str]:
    """Build a Herdr 0.7 agent-start argv (no shell).

    Herdr owns pane creation (`herdr pane split`) and requires a canonical
    `--kind` plus an existing `--pane`; older tok-tua code passed `--cwd` to
    `agent start`, which Herdr rejects. The caller supplies the pane created
    with the requested cwd.
    """
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,48}$", name):
        raise ValueError(f"invalid agent name {name!r}")
    short = plan.get("short_command") or plan.get("effective_cli") or "codex"
    if plan.get("kind") == "url":
        raise ValueError("herdr scale does not start URL surfaces; use turnstone scale")
    cmd_argv = _argv_from_short_command(str(short))
    effective_cli = str(plan.get("effective_cli") or plan.get("cli") or cmd_argv[0])
    kind = effective_cli
    supported = {"pi", "claude", "codex", "gemini", "cursor", "devin", "agy", "cline", "omp", "mastracode", "opencode", "copilot", "kimi", "kiro", "droid", "amp", "grok", "hermes", "kilo", "qodercli", "maki"}
    if kind not in supported:
        raise ValueError(f"Herdr does not support CLI kind {kind!r}")
    if not pane_id:
        pane_id = "<pane-id>"
    argv = ["herdr", "agent", "start", name, "--kind", kind, "--pane", pane_id, "--timeout", "30000"]
    # Herdr supplies the canonical executable. Pass only provider/model args.
    extra = cmd_argv[1:]
    if kind == "opencode" and not extra and plan.get("model"):
        extra = ["--model", f"ai-gateway/{plan['model']}"]
    argv.append("--")
    argv.extend(extra)
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
    # Dry-run must work in CI without the CLI binary on PATH.
    plan = resolve_launch(cli, model, require_available=not dry_run)
    # Keep coding-primary split congruent with tok-tua/grok-tua dual-pane (~80% left).
    # Herdr --ratio is the fraction for the *new* pane; 0.20 ≈ right strip.
    split_argv = [
        "herdr",
        "pane",
        "split",
        "--current",
        "--direction",
        "right",
        "--ratio",
        "0.20",
        "--no-focus",
    ]
    if cwd:
        split_argv.extend(["--cwd", cwd])
    if dry_run:
        pane_id = "<pane-id>"
    else:
        split = subprocess.run(split_argv, capture_output=True, text=True, timeout=15, check=False)
        try:
            payload = json.loads(split.stdout)
            pane_id = payload["result"]["pane"]["pane_id"]
        except (ValueError, KeyError, TypeError) as exc:
            return {
                "ok": False,
                "argv": split_argv,
                "error": f"Herdr pane split failed: {exc}",
                "stdout": split.stdout[-2000:],
                "stderr": split.stderr[-2000:],
                "plan": _public_plan(plan),
            }
    argv = build_herdr_start_argv(name, plan, cwd=cwd, pane_id=pane_id)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "argv": argv,
            "pane_split": list(split_argv),
            "plan": _public_plan(plan),
        }
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
