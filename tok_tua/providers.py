"""CLI / surface registry for tok-tua."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from tok_tua.qqq import assert_qqq_allowed, current_mode, log_qqq_selection
from tok_tua.routes import DEFAULT_ROUTE, assert_model_allowed, get_route

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "tok_tua.json"

# Soft-fail providers: listed even if binary missing (until account/install).
SOFT_FAIL = frozenset({"claude", "gemini-wrap", "openrouter-wrap"})

PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "codex",
        "label": "Codex CLI",
        "kind": "cli",
        "binary": "codex",
        "template": "codex",
        "gateway": True,
        "default_for_wrap": True,
    },
    {
        "id": "cursor",
        "label": "Cursor agent",
        "kind": "cli",
        "binary": "cursor",
        "template": "cursor agent",
        "gateway": True,
        "probe": "cursor_agent",
        "notes": "Uses `cursor agent`, not IDE `cursor .`",
    },
    {
        "id": "claude",
        "label": "Claude Code",
        "kind": "cli",
        "binary": "claude",
        "template": "claude",
        "gateway": True,
        "gateway_protocol": "anthropic",
        "soft": True,
        "notes": (
            "Headroom Anthropic gateway (ANTHROPIC_BASE_URL=:8787) for token monitoring. "
            "Set TOK_TUA_CLAUDE_MODE=native for claude.ai subscription without Headroom metering. "
            "Official Claude Code LLM-gateway path; no OAuth usage scrape."
        ),
    },
    {
        "id": "pi",
        "label": "pi agent",
        "kind": "cli",
        "binary": "pi",
        "template": "pi --provider ai-gateway --model {model}",
        "gateway": True,
    },
    {
        "id": "omp",
        "label": "OMP (Oh My Pi)",
        "kind": "cli",
        "binary": "omp",
        "template": "omp --model ai-gateway/{model}",
        "gateway": True,
    },
    {
        "id": "opencode",
        "label": "OpenCode",
        "kind": "cli",
        "binary": "opencode",
        "template": "opencode",
        "gateway": True,
        "notes": "Model from ~/.config/opencode after apply_manager_clients",
    },
    {
        "id": "tau",
        "label": "Tau harness",
        "kind": "cli",
        "binary": "tau",
        "template": "tau",
        "gateway": True,
    },
    {
        "id": "aider",
        "label": "Aider",
        "kind": "cli",
        "binary": "aider",
        "template": "aider --model openai/{model}",
        "gateway": True,
    },
    {
        "id": "grok",
        "label": "Grok Build (or grok-tua)",
        "kind": "cli",
        "binary": "grok",
        "template": "grok",
        "gateway": False,
        "supergrok": True,
        "notes": "Prefer bin/grok-tua for full SuperGrok burn metrics",
    },
    {
        "id": "openrouter-wrap",
        "label": "OpenRouter free wrap",
        "kind": "wrap",
        "binary": None,
        "force_model": "manager-openrouter-free",
        "host_cli": "omp",
        "gateway": True,
        "cloud": True,
        "notes": "Façade: host CLI with manager-openrouter-free (public only)",
    },
    {
        "id": "gemini-wrap",
        "label": "Gemini paid wrap",
        "kind": "wrap",
        "binary": None,
        "force_model": "manager-gemini-paid",
        "host_cli": "omp",
        "gateway": True,
        "cloud": True,
        "soft": True,
        "notes": "Façade: host CLI with manager-gemini-paid (consent)",
    },
    {
        "id": "turnstone",
        "label": "Turnstone UI (GPU host)",
        "kind": "url",
        "binary": None,
        "url_key": "turnstone_base",
        "gateway": True,
        "notes": "Chat/workstream surface; use turnstone-cli for REST",
    },
    {
        "id": "herdr",
        "label": "Herdr multiplexer",
        "kind": "cli",
        "binary": "herdr",
        "template": "herdr",
        "gateway": True,
        "notes": "Agent workspace manager; scale=herdr uses agent start",
    },
]

KNOWN_PROVIDERS = {p["id"] for p in PROVIDERS}


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or Path(os.environ.get("TOK_TUA_CONFIG", DEFAULT_CONFIG))
    if not cfg_path.is_file():
        return {}
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def defaults() -> dict[str, Any]:
    cfg = load_config()
    d = dict(cfg.get("defaults") or {})
    d.setdefault("model", DEFAULT_ROUTE)
    d.setdefault("cli", "codex")
    d.setdefault("scale", "single")
    d.setdefault("gateway", "http://127.0.0.1:8787/v1")
    d.setdefault("turnstone_base", "http://127.0.0.1:8090")
    d.setdefault("wrap_host_cli", "omp")
    return d


# Extra dirs when PATH is LaunchAgent-thin (Maintenance Deck).
_EXTRA_BIN_DIRS = (
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
    Path.home() / ".local" / "bin",
    Path.home() / ".opencode" / "bin",
)


def _claude_desktop_binary() -> str | None:
    """Newest Claude Code binary bundled with Claude Desktop (macOS app support)."""
    base = Path.home() / "Library" / "Application Support" / "Claude" / "claude-code"
    if not base.is_dir():
        return None
    try:
        versions = sorted(
            (p for p in base.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )
    except OSError:
        return None
    for verdir in versions:
        cand = verdir / "claude.app" / "Contents" / "MacOS" / "claude"
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def _which(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for directory in _EXTRA_BIN_DIRS:
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    if name == "claude":
        return _claude_desktop_binary()
    return None


def _cursor_agent_available() -> bool:
    """True if `cursor` exists; avoid invoking `cursor agent` (may network-install)."""
    return _which("cursor") is not None


def probe_provider(provider: dict[str, Any]) -> dict[str, Any]:
    """Return availability for one provider definition."""
    row = dict(provider)
    kind = row.get("kind")
    soft = bool(row.get("soft") or row["id"] in SOFT_FAIL)

    if kind == "wrap":
        host_id = row.get("host_cli") or defaults().get("wrap_host_cli") or "omp"
        host = next((p for p in PROVIDERS if p["id"] == host_id), None)
        host_bin = (host or {}).get("binary")
        path = _which(host_bin) if host_bin else None
        row["available"] = bool(path)
        row["path"] = path
        row["host_cli"] = host_id
        row["status"] = "ready" if path else ("soft" if soft else "missing")
        row["error"] = None if path else f"wrap host CLI {host_id!r} not on PATH"
        return row

    if kind == "url":
        row["available"] = True
        row["path"] = row.get("url") or defaults().get(row.get("url_key") or "", "")
        row["status"] = "ready"
        row["error"] = None
        return row

    binary = row.get("binary")
    if row.get("probe") == "cursor_agent":
        ok = _cursor_agent_available()
        row["available"] = ok
        row["path"] = _which("cursor")
        row["status"] = "ready" if ok else ("soft" if soft else "missing")
        row["error"] = None if ok else "cursor not on PATH"
        return row

    if not binary:
        row["available"] = soft
        row["path"] = None
        row["status"] = "soft" if soft else "missing"
        row["error"] = "no binary configured"
        return row

    path = _which(binary)
    # grok may also be `agent` (Grok Build TUI)
    if not path and row["id"] == "grok":
        path = _which("agent")
        if path:
            row["template"] = "agent"
            row["binary"] = "agent"
    row["available"] = bool(path)
    row["path"] = path
    if path:
        row["status"] = "ready"
        row["error"] = None
    else:
        row["status"] = "soft" if soft else "missing"
        row["error"] = f"{binary} not on PATH"
    return row


def _version_from_text(text: str) -> str | None:
    if not text:
        return None
    bad = re.compile(r"^(usage:|warning:|error:|options:|commands:)", re.I)
    for raw in text.strip().splitlines():
        line = raw.strip()
        if not line or bad.match(line):
            continue
        if "not in the list of known options" in line.lower():
            continue
        # Prefer first semver-ish token on a non-usage line
        m = re.search(r"\b(\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.]+)?)\b", line)
        if m:
            return m.group(1)
    return None


def probe_cli_version(binary_path: str | None, *, timeout: float = 2.5) -> str | None:
    """Best-effort ``--version`` / ``version`` for a CLI binary path."""
    if not binary_path:
        return None
    name = Path(binary_path).name
    # Electron wrappers (cursor) treat -V poorly; prefer --version only first.
    argsets: list[list[str]]
    if name in {"cursor", "code"}:
        argsets = [[binary_path, "--version"], [binary_path, "-v"]]
    elif name in {"tau", "omp", "aider"}:
        argsets = [[binary_path, "--version"], [binary_path, "version"]]
    else:
        argsets = [[binary_path, "--version"], [binary_path, "version"], [binary_path, "-V"]]
    for args in argsets:
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        ver = _version_from_text(text)
        if ver:
            return ver
    return None


def enrich_provider_versions(
    rows: list[dict[str, Any]] | None = None,
    *,
    with_versions: bool = True,
) -> list[dict[str, Any]]:
    """List providers with optional version strings (missing → version null)."""
    base = rows if rows is not None else list_providers()
    out: list[dict[str, Any]] = []
    for row in base:
        item = dict(row)
        if not with_versions:
            item.setdefault("version", None)
            out.append(item)
            continue
        if item.get("kind") == "url":
            item["version"] = "url"
        elif item.get("kind") == "wrap":
            # version of host CLI
            host_path = item.get("path")
            item["version"] = probe_cli_version(host_path) if host_path else None
            item["version_note"] = f"host {item.get('host_cli') or 'cli'}"
        elif not item.get("available") or not item.get("path"):
            item["version"] = None
        else:
            item["version"] = probe_cli_version(item.get("path"))
        out.append(item)
    return out


def provider_board(*, with_versions: bool = True) -> dict[str, Any]:
    """Aggregate for Deck footer / tok-tua status."""
    rows = enrich_provider_versions(with_versions=with_versions)
    ready = sum(1 for r in rows if r.get("status") == "ready" or (r.get("available") and r.get("status") != "missing"))
    missing = sum(1 for r in rows if r.get("status") == "missing" or (not r.get("available") and r.get("status") != "soft"))
    soft = sum(1 for r in rows if r.get("status") == "soft")
    return {
        "tools_total": len(rows),
        "tools_available": ready,
        "tools_missing": missing,
        "tools_soft": soft,
        "providers": rows,
        "hint": "python -m tok_tua providers · tok-tua --cli <id>",
    }


def list_providers(*, available_only: bool = False) -> list[dict[str, Any]]:
    rows = [probe_provider(p) for p in PROVIDERS]
    if available_only:
        return [r for r in rows if r.get("available") or r.get("status") == "soft"]
    return rows


def get_provider(cli_id: str) -> dict[str, Any] | None:
    for p in PROVIDERS:
        if p["id"] == cli_id:
            return probe_provider(p)
    return None


def shell_prelude(*, gateway: str | None = None) -> str:
    """Env prelude so OpenAI-compatible CLIs hit Headroom."""
    d = defaults()
    gw = (gateway or d.get("gateway") or "http://127.0.0.1:8787/v1").rstrip("/")
    return (
        f'set -a && [ -f "{ROOT}/.env" ] && . "{ROOT}/.env"; set +a; '
        f'export OPENAI_BASE_URL={gw} OPENAI_API_BASE={gw} '
        f'OPENAI_API_KEY="${{LITELLM_MASTER_KEY:-$OPENAI_API_KEY}}" '
        f'LITELLM_BASE_URL={gw} LITELLM_API_KEY="${{LITELLM_MASTER_KEY:-}}" '
        f'HEADROOM_BASE={gw.rsplit("/v1", 1)[0]}'
    )


def shell_prelude_anthropic(*, headroom_base: str | None = None) -> str:
    """Env prelude so Claude Code hits Headroom Anthropic /v1/messages (token monitoring).

    Official Claude Code LLM-gateway path (ANTHROPIC_BASE_URL + gateway credential).
    Does not scrape claude.ai OAuth usage; metrics come from Headroom/LiteLLM.
    """
    d = defaults()
    hr = (
        headroom_base
        or os.environ.get("HEADROOM_BASE")
        or d.get("headroom_base")
        or "http://127.0.0.1:8787"
    ).rstrip("/")
    # Strip accidental /v1 suffix — Claude appends /v1/messages itself.
    if hr.endswith("/v1"):
        hr = hr[: -len("/v1")]
    return (
        f'set -a && [ -f "{ROOT}/.env" ] && . "{ROOT}/.env"; set +a; '
        f'export HEADROOM_BASE={hr} '
        f'ANTHROPIC_BASE_URL={hr} '
        f'ANTHROPIC_AUTH_TOKEN="${{LITELLM_MASTER_KEY:-${{ANTHROPIC_AUTH_TOKEN:-}}}}" '
        f'ANTHROPIC_API_KEY="${{LITELLM_MASTER_KEY:-${{ANTHROPIC_API_KEY:-}}}}" '
        f'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="${{CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC:-1}}"'
    )


def claude_mode() -> str:
    """gateway (default, Headroom metered) or native (claude.ai subscription, unmetered by Headroom)."""
    mode = (os.environ.get("TOK_TUA_CLAUDE_MODE") or "gateway").strip().lower()
    if mode in {"native", "subscription", "oauth"}:
        return "native"
    return "gateway"


def resolve_launch(
    cli_id: str | None = None,
    model_id: str | None = None,
    *,
    data_class: str = "public_code",
    qqq_mode: str | None = None,
    require_available: bool = True,
) -> dict[str, Any]:
    """Resolve CLI + model into a launch plan (command, env notes, cloud flags)."""
    d = defaults()
    cli_id = cli_id or os.environ.get("TOK_TUA_CLI") or d.get("cli") or "codex"
    model_id = model_id or os.environ.get("TOK_TUA_MODEL") or d.get("model") or DEFAULT_ROUTE
    data_class = os.environ.get("TOK_TUA_DATA_CLASS") or data_class

    qqq_resolved, qqq_explicit = current_mode(qqq_mode)
    assert_qqq_allowed(qqq_resolved, model_id, data_class=data_class, explicit=qqq_explicit)
    log_qqq_selection(qqq_resolved, model_id=model_id, cli_id=cli_id, explicit=qqq_explicit, data_class=data_class)

    provider = get_provider(cli_id)
    if provider is None:
        known = ", ".join(sorted(KNOWN_PROVIDERS))
        raise ValueError(f"unknown cli {cli_id!r}; known: {known}")

    # Wrap façades force a cloud model
    if provider.get("kind") == "wrap":
        model_id = str(provider.get("force_model") or model_id)
        host_id = provider.get("host_cli") or d.get("wrap_host_cli") or "omp"
        host = get_provider(str(host_id))
        if host is None or not host.get("available"):
            # fall back to codex/pi if wrap host missing
            for fallback in ("omp", "pi", "codex", "opencode"):
                cand = get_provider(fallback)
                if cand and cand.get("available"):
                    host = cand
                    host_id = fallback
                    break
        if host is None:
            raise ValueError(f"wrap {cli_id!r}: no host CLI available")
        effective = host
        wrap_id = cli_id
    else:
        effective = provider
        wrap_id = None
        host_id = None

    assert_model_allowed(model_id, data_class=data_class)
    route = get_route(model_id) or {}

    if require_available and not effective.get("available") and not effective.get("soft"):
        raise ValueError(
            f"cli {effective.get('id')!r} not available: {effective.get('error') or 'missing'}"
        )

    template = effective.get("template") or effective.get("id") or "codex"
    short = template.format(model=model_id)
    # Prefer absolute binary path so tmux panes never hit wrong PATH peers
    # (e.g. Debian fdclone once owned /usr/bin/fd; bare `pi` must stay earendil).
    bin_path = effective.get("path")
    if bin_path and isinstance(bin_path, str) and bin_path.startswith("/"):
        first, _, rest = short.partition(" ")
        if first and "/" not in first and first == (effective.get("binary") or first):
            short = f"{bin_path} {rest}".rstrip() if rest else bin_path

    protocol = str(effective.get("gateway_protocol") or "openai")
    mode = claude_mode() if effective.get("id") == "claude" else "gateway"
    # Native Claude: no Headroom env (subscription path; Headroom cannot meter it).
    if effective.get("id") == "claude" and mode == "native":
        use_gateway = False
        prelude = ""
        protocol = "native"
    else:
        use_gateway = bool(effective.get("gateway", True)) and not effective.get("supergrok")
        if protocol == "anthropic":
            prelude = shell_prelude_anthropic()
        else:
            prelude = shell_prelude()

    if effective.get("kind") == "url":
        url = defaults().get(effective.get("url_key") or "turnstone_base") or provider.get("path")
        return {
            "cli": wrap_id or cli_id,
            "effective_cli": effective["id"],
            "kind": "url",
            "url": url,
            "model": model_id,
            "route": route,
            "cloud": bool(route.get("cloud") or provider.get("cloud")),
            "gateway": use_gateway,
            "short_command": f"open {url}",
            "command": f"open {url}",
            "provider": effective,
            "wrap": wrap_id,
            "qqq_mode": qqq_resolved,
        }

    command = f"{prelude} && {short}" if use_gateway and prelude else short
    return {
        "cli": wrap_id or cli_id,
        "effective_cli": effective["id"],
        "kind": "cli",
        "model": model_id,
        "route": route,
        "cloud": bool(route.get("cloud") or provider.get("cloud")),
        "gateway": use_gateway,
        "gateway_protocol": protocol,
        "claude_mode": mode if effective.get("id") == "claude" else None,
        "short_command": short,
        "command": command,
        "prelude": prelude if use_gateway else "",
        "provider": effective,
        "wrap": wrap_id,
        "host_cli": host_id,
        "supergrok": bool(effective.get("supergrok")),
        "available": bool(effective.get("available")),
        "path": effective.get("path"),
        "status": effective.get("status"),
        "error": effective.get("error"),
        "qqq_mode": qqq_resolved,
    }


def format_provider_table(rows: list[dict[str, Any]] | None = None) -> str:
    if rows is None:
        rows = enrich_provider_versions(with_versions=True)
    elif rows and all(r.get("version") is None for r in rows if r.get("available")):
        # Callers sometimes pass list_providers() without versions — enrich in place.
        rows = enrich_provider_versions(rows, with_versions=True)
    lines = ["id                 status    version      path/binary", "-" * 72]
    for r in rows:
        path = r.get("path") or r.get("error") or ""
        ver = r.get("version")
        if ver is None and not r.get("available") and r.get("status") != "soft":
            ver = "NOT INSTALLED"
        ver_s = str(ver or "—")[:12]
        lines.append(f"{r['id']:<18} {str(r.get('status') or '?'):<9} {ver_s:<12} {path}")
    return "\n".join(lines)
