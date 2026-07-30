"""
Ordered Gateway stack version board for grok-tua / tok-tua.

Display order (user-requested):
  Headroom → LiteLLM → Herdr → codex → claude → pi → omp → opencode → openweb-ui
  then other ai-gateway surfaces (turnstone, tau, aider, grok, grafana, …)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from grok_tua.stack_metrics import ensure_gateway_env, _http_text

# Primary board order — gateway health first (narrow stats pane must show these)
PRIMARY_ORDER = (
    "headroom",
    "litellm",
    "grafana",
    "prompt_io",
    "herdr",
    "codex",
    "claude",
    "pi",
    "omp",
    "opencode",
    "openwebui",
)

# Trailing "other" stack
OTHER_ORDER = (
    "turnstone",
    "tau",
    "aider",
    "grok",
    "cursor",
)

_EXTRA_BIN = (
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
    Path.home() / ".local" / "bin",
    Path.home() / ".opencode" / "bin",
)


def _which(name: str) -> Optional[str]:
    found = shutil.which(name)
    if found:
        return found
    for d in _EXTRA_BIN:
        p = d / name
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def _version_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    bad = re.compile(r"^(usage:|warning:|error:|options:|commands:)", re.I)
    for raw in text.strip().splitlines():
        line = raw.strip()
        if not line or bad.match(line):
            continue
        m = re.search(r"\b(\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.]+)?)\b", line)
        if m:
            return m.group(1)
        # "grok 0.2.111 (hash)" style
        m2 = re.search(r"\b([vV]?\d+\.\d+\.\d+)\b", line)
        if m2:
            return m2.group(1).lstrip("vV")
    return None


def probe_cli(binary: str, *, timeout: float = 2.0) -> Dict[str, Any]:
    path = _which(binary)
    if not path:
        return {
            "id": binary,
            "label": binary,
            "kind": "cli",
            "status": "missing",
            "version": None,
            "path": None,
            "summary": "not installed",
        }
    argsets = [[path, "--version"], [path, "version"], [path, "-V"]]
    ver = None
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
            break
    return {
        "id": binary,
        "label": binary,
        "kind": "cli",
        "status": "ok" if ver else "ok_no_ver",
        "version": ver,
        "path": path,
        "summary": ver or "on PATH",
    }


def _http_ok(url: str, *, timeout: float = 2.0) -> Tuple[bool, int, str, str]:
    """Return (ok, http_code, error_or_empty, body_snippet)."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", 200) or 200
            body = ""
            try:
                body = (resp.read(512) or b"").decode("utf-8", errors="replace")
            except Exception:
                body = ""
            return code < 500, code, "", body
    except urllib.error.HTTPError as exc:
        return exc.code < 500, exc.code, str(exc.code), ""
    except Exception as exc:
        return False, 0, str(exc)[:80], ""


def _version_from_http_body(body: str) -> Optional[str]:
    if not body or not body.lstrip().startswith("{"):
        return None
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    ver = data.get("version")
    if ver is None:
        return None
    text = str(ver).strip()
    return text or None


def _is_host_unreachable_base(base: str) -> bool:
    """Docker-compose service hostnames are not resolvable on the Mac host."""
    raw = (base or "").strip().rstrip("/")
    if not raw:
        return True
    try:
        from urllib.parse import urlparse

        host = (urlparse(raw).hostname or "").lower()
    except Exception:
        host = ""
    if not host:
        return True
    if host in {"prompt-io", "grafana", "litellm", "headroom", "host.docker.internal"}:
        return True
    if host.endswith(".internal") or host.endswith(".localdomain"):
        return True
    # bare service-style names (no dots, not localhost/IPs)
    if "." not in host and host not in {"localhost"}:
        return True
    return False


def grafana_base_candidates() -> List[str]:
    """Ordered Grafana bases. A remote LAN host may publish a nonstandard port."""
    nas_host = os.environ.get("NAS_HOST_IP", "")
    gpu_host = os.environ.get("GPU_HOST_IP", "")
    bases: List[str] = []
    for base in (
        os.environ.get("GRAFANA_BASE", "").strip(),
        "http://127.0.0.1:3000",
        f"http://{nas_host}:3002" if nas_host else "",  # some setups map 3002:3000
        f"http://{nas_host}:3000" if nas_host else "",
        f"http://{gpu_host}:3000" if gpu_host else "",
    ):
        if not base:
            continue
        if _is_host_unreachable_base(base):
            continue
        norm = base.rstrip("/")
        if norm not in bases:
            bases.append(norm)
    return bases


def prompt_io_base_candidates() -> List[str]:
    """Ordered Prompt-I/O bases; skip docker-internal hostnames on the Mac host."""
    nas_host = os.environ.get("NAS_HOST_IP", "")
    bases: List[str] = []
    for base in (
        os.environ.get("PROMPT_IO_BASE", "").strip(),
        "http://127.0.0.1:5050",
        "http://127.0.0.1:8788",
        f"http://{nas_host}:5050" if nas_host else "",
    ):
        if not base:
            continue
        if _is_host_unreachable_base(base):
            continue
        norm = base.rstrip("/")
        if norm not in bases:
            bases.append(norm)
    return bases


def probe_http_service(
    sid: str,
    label: str,
    urls: List[str],
) -> Dict[str, Any]:
    err = "unreachable"
    for url in urls:
        ok, code, err, body = _http_ok(url)
        if ok:
            ver = _version_from_http_body(body)
            summary = f"{ver} · {url}" if ver else f"up · {url}"
            return {
                "id": sid,
                "label": label,
                "kind": "http",
                "status": "ok",
                "version": ver,
                "base": url,
                "summary": summary,
                "http_code": code,
            }
    return {
        "id": sid,
        "label": label,
        "kind": "http",
        "status": "down",
        "version": None,
        "summary": err or "unreachable",
        "bases_tried": urls,
    }


def fetch_stack_versions(
    *,
    headroom: Optional[Dict[str, Any]] = None,
    litellm: Optional[Dict[str, Any]] = None,
    herdr: Optional[Dict[str, Any]] = None,
    turnstone: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build ordered component board. Gateway dicts optional (avoids re-fetch)."""
    ensure_gateway_env()
    hr = headroom or {}
    ll = litellm or {}
    her = herdr or {}
    ts = turnstone or {}

    headroom_base = (hr.get("base") or os.environ.get("HEADROOM_BASE") or "http://127.0.0.1:8787").rstrip("/")
    litellm_base = (ll.get("base") or os.environ.get("LITELLM_BASE") or "http://127.0.0.1:4000").rstrip("/")

    components: Dict[str, Dict[str, Any]] = {}

    # Headroom
    if hr:
        components["headroom"] = {
            "id": "headroom",
            "label": "Headroom",
            "kind": "gateway",
            "status": "ok" if hr.get("ready") else "down",
            "version": None,
            "summary": f"{'OK' if hr.get('ready') else 'DOWN'} · models={hr.get('model_count', 0)} · {headroom_base}",
            "base": headroom_base,
        }
    else:
        body, code = _http_text(f"{headroom_base}/readyz")
        ok = code == 200
        components["headroom"] = {
            "id": "headroom",
            "label": "Headroom",
            "kind": "gateway",
            "status": "ok" if ok else "down",
            "summary": f"{'OK' if ok else 'DOWN'} · {headroom_base}",
            "base": headroom_base,
        }

    # LiteLLM
    if ll:
        components["litellm"] = {
            "id": "litellm",
            "label": "LiteLLM",
            "kind": "gateway",
            "status": "ok" if ll.get("alive") else "down",
            "version": None,
            "summary": f"{'OK' if ll.get('alive') else 'DOWN'} · models={ll.get('model_count', 0)} · {litellm_base}",
            "base": litellm_base,
        }
    else:
        body, code = _http_text(f"{litellm_base}/health/liveliness")
        ok = code == 200 and "alive" in (body or "").lower()
        components["litellm"] = {
            "id": "litellm",
            "label": "LiteLLM",
            "kind": "gateway",
            "status": "ok" if ok else "down",
            "summary": f"{'OK' if ok else 'DOWN'} · {litellm_base}",
            "base": litellm_base,
        }

    # Herdr
    if her:
        components["herdr"] = {
            "id": "herdr",
            "label": "Herdr",
            "kind": "cli",
            "status": "ok" if her.get("running") else ("missing" if not her.get("available") else "down"),
            "version": her.get("version") or None,
            "summary": (
                f"OK · {her.get('version') or ''}".strip()
                if her.get("running")
                else (her.get("error") or "down")
            ),
        }
    else:
        components["herdr"] = probe_cli("herdr")
        components["herdr"]["label"] = "Herdr"

    # CLIs
    for name, label in (
        ("codex", "codex"),
        ("claude", "claude"),
        ("pi", "pi"),
        ("omp", "omp"),
        ("opencode", "opencode"),
        ("tau", "tau"),
        ("aider", "aider"),
        ("grok", "grok"),
        ("cursor", "cursor"),
    ):
        row = probe_cli(name)
        row["label"] = label
        components[name] = row

    # Open WebUI (local + optional LAN host)
    owui_urls = [
        os.environ.get("OPENWEBUI_BASE", "http://127.0.0.1:8080").rstrip("/") + "/",
        "http://127.0.0.1:3001/",
    ]
    _nas_host = os.environ.get("NAS_HOST_IP", "")
    if _nas_host:
        owui_urls.insert(1, f"http://{_nas_host}:3001/")
    components["openwebui"] = probe_http_service("openwebui", "openweb-ui", owui_urls)

    # Turnstone
    if ts:
        components["turnstone"] = {
            "id": "turnstone",
            "label": "Turnstone",
            "kind": "http",
            "status": "ok" if ts.get("ready") else "down",
            "version": ts.get("version") or None,
            "summary": (
                f"OK · {ts.get('base', '')} · {ts.get('version') or ''}".strip()
                if ts.get("ready")
                else (ts.get("error") or "down")
            ),
            "base": ts.get("base"),
        }
    else:
        tb = os.environ.get("TURNSTONE_BASE", "http://127.0.0.1:8090").rstrip("/")
        components["turnstone"] = probe_http_service(
            "turnstone", "Turnstone", [f"{tb}/openapi.json", f"{tb}/"]
        )

    grafana_urls: List[str] = []
    for base in grafana_base_candidates():
        grafana_urls.extend([f"{base}/api/health", f"{base}/"])
    components["grafana"] = probe_http_service("grafana", "Grafana", grafana_urls)

    prompt_urls: List[str] = []
    for base in prompt_io_base_candidates():
        prompt_urls.extend([f"{base}/health", f"{base}/api/health", f"{base}/"])
    components["prompt_io"] = probe_http_service("prompt_io", "Prompt-I/O", prompt_urls)

    ordered: List[Dict[str, Any]] = []
    for sid in PRIMARY_ORDER:
        if sid in components:
            ordered.append(components[sid])
    for sid in OTHER_ORDER:
        if sid in components:
            ordered.append(components[sid])

    return {
        "primary": [c for c in ordered if c["id"] in PRIMARY_ORDER],
        "other": [c for c in ordered if c["id"] in OTHER_ORDER],
        "all": ordered,
    }


def format_stack_versions(
    board: Dict[str, Any],
    *,
    markup: bool = True,
    compact: bool = False,
) -> str:
    lines: List[str] = []

    def fmt(row: Dict[str, Any]) -> str:
        label = (row.get("label") or row.get("id") or "?").ljust(10)
        st = row.get("status") or "?"
        ver = row.get("version")
        summary = row.get("summary") or ""
        if ver and ver not in summary:
            summary = f"{ver} · {summary}" if summary else str(ver)
        path = row.get("path")
        if path and path not in summary:
            summary = f"{summary}  {path}" if summary else path
        if markup:
            if st in ("ok", "ok_no_ver"):
                badge = "[green]OK[/green]"
            elif st == "missing":
                badge = "[dim]—[/dim]"
            else:
                badge = "[red]DOWN[/red]"
        else:
            badge = "OK" if st in ("ok", "ok_no_ver") else ("—" if st == "missing" else "DOWN")
        if compact:
            return f"{label} {badge}  {ver or (summary[:40] if summary else '')}".rstrip()
        return f"{label} {badge}  {summary}".rstrip()

    lines.append("Stack     version / health board")
    for row in board.get("primary") or []:
        lines.append(f"  {fmt(row)}")
    other = board.get("other") or []
    if other:
        lines.append("  ── other ──")
        for row in other:
            lines.append(f"  {fmt(row)}")
    return "\n".join(lines)
