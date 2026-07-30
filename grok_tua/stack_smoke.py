"""
Launch-time smoke test + optional quick heal for grok-tua core stack surfaces.

Probes (in order): Headroom, LiteLLM, Grafana, Prompt-I/O.
When --heal is set and a probe fails:
  1. Restart known local Docker containers if they exist.
  2. Optionally restart containers on a remote LAN host over SSH (BatchMode)
     when local stays down.
  3. Never does a full `docker compose up` unless GROK_TUA_SMOKE_COMPOSE=1
     (too heavy/surprising for a launch wrapper).

Env:
  GROK_TUA_NO_SMOKE=1          skip entirely (wrapper honors this)
  GROK_TUA_SMOKE_HEAL=0        probe-only (wrapper default is heal on)
  GROK_TUA_SMOKE_COMPOSE=1     allow `docker compose up -d <svc>` in AI_GATEWAY_DIR
  GROK_TUA_SMOKE_NAS_HOST=0    disable remote-host SSH restarts
  AI_GATEWAY_DIR               dir with your gateway's docker-compose.yml (no default)
  NAS_HOST_IP                  LAN gateway host to probe as a fallback (no default)
  NAS_HOST_SSH                 SSH target for remote restarts, e.g. user@<nas-host-ip>
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from grok_tua.stack_versions import (
    grafana_base_candidates,
    prompt_io_base_candidates,
)

# (service_id, label, local containers, remote-host containers, compose service names)
CORE_SERVICES: Tuple[Tuple[str, str, Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]], ...] = (
    (
        "headroom",
        "Headroom",
        ("headroom-proxy", "headroom"),
        ("nas-host-headroom",),
        ("headroom",),
    ),
    (
        "litellm",
        "LiteLLM",
        ("litellm-proxy", "litellm"),
        ("nas-host-litellm",),
        ("litellm",),
    ),
    (
        "grafana",
        "Grafana",
        ("grafana", "nas-host-grafana"),
        ("nas-host-grafana",),
        (),  # local compose has no grafana; remote host only
    ),
    (
        "prompt_io",
        "Prompt-I/O",
        ("prompt-io", "prompt_io"),
        (),
        ("prompt-io",),  # profile security
    ),
)


@dataclass
class ProbeResult:
    id: str
    label: str
    ok: bool
    url: str = ""
    detail: str = ""
    healed: bool = False
    heal_actions: List[str] = field(default_factory=list)


def _http_ok(url: str, timeout: float = 2.0) -> Tuple[bool, int, str]:
    try:
        req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json,*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = int(getattr(resp, "status", 200) or 200)
            body = resp.read(256).decode("utf-8", errors="replace")
            return True, code, body[:120]
    except urllib.error.HTTPError as exc:
        # Some health endpoints return 401/302 while still "up"
        if exc.code in (200, 204, 301, 302, 401, 403):
            return True, int(exc.code), f"http {exc.code}"
        return False, int(exc.code), f"http {exc.code}"
    except Exception as exc:
        return False, 0, str(exc)[:120]


def _probe_urls(urls: Sequence[str], timeout: float) -> Tuple[bool, str, str]:
    last_err = "no candidates"
    for url in urls:
        ok, code, detail = _http_ok(url, timeout=timeout)
        if ok:
            return True, url, detail or f"http {code}"
        last_err = f"{url}: {detail}"
    return False, "", last_err


def probe_headroom(timeout: float = 2.0) -> ProbeResult:
    base = (os.environ.get("HEADROOM_BASE") or "http://127.0.0.1:8787").rstrip("/")
    urls = [f"{base}/readyz", f"{base}/"]
    # Also try a configured LAN fallback host so smoke can report it even if
    # env points local (set NAS_HOST_IP to enable; skipped otherwise).
    nas_host = os.environ.get("NAS_HOST_IP", "")
    if nas_host:
        nas_base = f"http://{nas_host}:8787"
        if not any(nas_base in u for u in urls):
            urls.extend([f"{nas_base}/readyz", f"{nas_base}/"])
    ok, url, detail = _probe_urls(urls, timeout)
    return ProbeResult("headroom", "Headroom", ok, url=url, detail=detail)


def probe_litellm(timeout: float = 2.0) -> ProbeResult:
    base = (os.environ.get("LITELLM_BASE") or "http://127.0.0.1:4000").rstrip("/")
    urls = [f"{base}/health/liveliness", f"{base}/health", f"{base}/"]
    nas_host = os.environ.get("NAS_HOST_IP", "")
    if nas_host:
        nas_base = f"http://{nas_host}:4000"
        if not any(nas_base in u for u in urls):
            urls.extend([f"{nas_base}/health/liveliness", f"{nas_base}/health"])
    ok, url, detail = _probe_urls(urls, timeout)
    return ProbeResult("litellm", "LiteLLM", ok, url=url, detail=detail)


def probe_grafana(timeout: float = 2.0) -> ProbeResult:
    urls: List[str] = []
    for base in grafana_base_candidates():
        urls.extend([f"{base}/api/health", f"{base}/"])
    ok, url, detail = _probe_urls(urls, timeout)
    return ProbeResult("grafana", "Grafana", ok, url=url, detail=detail)


def probe_prompt_io(timeout: float = 2.0) -> ProbeResult:
    urls: List[str] = []
    for base in prompt_io_base_candidates():
        urls.extend([f"{base}/health", f"{base}/api/health", f"{base}/"])
    ok, url, detail = _probe_urls(urls, timeout)
    return ProbeResult("prompt_io", "Prompt-I/O", ok, url=url, detail=detail)


def _get_prober(sid: str):
    """Look up probers at call time so tests can patch the functions."""
    return {
        "headroom": probe_headroom,
        "litellm": probe_litellm,
        "grafana": probe_grafana,
        "prompt_io": probe_prompt_io,
    }.get(sid)


def _run(
    cmd: Sequence[str],
    *,
    timeout: float = 45.0,
    cwd: Optional[Path] = None,
) -> Tuple[int, str]:
    try:
        proc = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=str(cwd) if cwd else None,
        )
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return proc.returncode, out[:400]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)[:200]


def _docker_bin() -> Optional[str]:
    return shutil.which("docker")


def _container_exists(name: str) -> bool:
    docker = _docker_bin()
    if not docker:
        return False
    code, _ = _run([docker, "inspect", name], timeout=8)
    return code == 0


def _container_running(name: str) -> bool:
    docker = _docker_bin()
    if not docker:
        return False
    code, out = _run(
        [docker, "inspect", "-f", "{{.State.Running}}", name],
        timeout=8,
    )
    return code == 0 and out.strip().lower() == "true"


def _restart_local_container(name: str) -> Tuple[bool, str]:
    docker = _docker_bin()
    if not docker:
        return False, "docker not on PATH"
    if not _container_exists(name):
        return False, f"container {name} not found"
    code, out = _run([docker, "restart", name], timeout=60)
    if code == 0:
        return True, f"docker restart {name}"
    return False, f"restart {name} failed: {out}"


def _compose_up(service: str, *, force_recreate: bool = False) -> Tuple[bool, str]:
    """
    Bring a compose service up. force_recreate is used when the container is
    healthy inside Docker but host ports are not bound (Docker Desktop proxy glitch).
    Enabled via GROK_TUA_SMOKE_COMPOSE=1, or automatically for force_recreate when
    GROK_TUA_SMOKE_RECREATE=1 (default on for that narrow case).
    """
    allow = os.environ.get("GROK_TUA_SMOKE_COMPOSE", "0") in ("1", "true", "yes")
    allow_recreate = os.environ.get("GROK_TUA_SMOKE_RECREATE", "1") in ("1", "true", "yes")
    if force_recreate and not allow and not allow_recreate:
        return False, "recreate disabled (set GROK_TUA_SMOKE_RECREATE=1 or SMOKE_COMPOSE=1)"
    if not force_recreate and not allow:
        return False, "compose up disabled (set GROK_TUA_SMOKE_COMPOSE=1)"
    docker = _docker_bin()
    if not docker:
        return False, "docker not on PATH"
    gateway_dir = os.environ.get("AI_GATEWAY_DIR")
    if not gateway_dir:
        return False, "AI_GATEWAY_DIR not set (dir with your gateway's docker-compose.yml)"
    gateway = Path(gateway_dir)
    if not gateway.is_dir():
        return False, f"AI_GATEWAY_DIR missing: {gateway}"
    # prompt-io lives under profile security
    if service == "prompt-io":
        cmd = [docker, "compose", "--profile", "security", "up", "-d"]
    else:
        cmd = [docker, "compose", "up", "-d"]
    if force_recreate:
        cmd.append("--force-recreate")
    cmd.append(service)
    code, out = _run(cmd, timeout=180, cwd=gateway)
    action = "compose recreate" if force_recreate else "compose up -d"
    if code == 0:
        return True, f"{action} {service}"
    return False, f"{action} {service} failed: {out}"


def _restart_nas_host_containers(names: Sequence[str]) -> Tuple[bool, str]:
    if os.environ.get("GROK_TUA_SMOKE_NAS_HOST", "1") in ("0", "false", "no"):
        return False, "remote-host heal disabled"
    if not names:
        return False, "no remote-host containers mapped"
    target = os.environ.get("NAS_HOST_SSH", "")
    if not target:
        return False, "NAS_HOST_SSH not set (e.g. user@<nas-host-ip>)"
    ssh = shutil.which("ssh")
    if not ssh:
        return False, "ssh not on PATH"
    # Restart each name if it exists; ignore missing so one miss doesn't abort.
    script = " && ".join(
        f"docker inspect {n} >/dev/null 2>&1 && docker restart {n} || true" for n in names
    )
    code, out = _run(
        [
            ssh,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=6",
            "-o",
            "StrictHostKeyChecking=accept-new",
            target,
            script,
        ],
        timeout=90,
    )
    if code == 0:
        return True, f"remote-host restart {','.join(names)}"
    return False, f"remote-host restart failed: {out}"


def _service_meta(sid: str) -> Optional[Tuple[str, str, Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]]:
    for row in CORE_SERVICES:
        if row[0] == sid:
            return row
    return None


def heal_service(sid: str, *, timeout: float = 2.0) -> ProbeResult:
    """Restart known containers for a down service, then re-probe."""
    meta = _service_meta(sid)
    prober = _get_prober(sid)
    if not meta or not prober:
        return ProbeResult(sid, sid, False, detail="unknown service")

    _sid, label, local_names, nas_host_names, compose_svcs = meta
    actions: List[str] = []

    # Prefer restarting a running local container (port-map may recover after restart)
    restarted = False
    had_local = any(_container_exists(n) for n in local_names)
    for name in local_names:
        if _container_exists(name):
            ok, msg = _restart_local_container(name)
            actions.append(msg)
            restarted = restarted or ok
            if ok:
                break

    # Optional compose up when no container exists
    if not restarted and not had_local and compose_svcs:
        for svc in compose_svcs:
            ok, msg = _compose_up(svc)
            actions.append(msg)
            if ok:
                restarted = True
                break

    # Wait briefly for health to flip
    if restarted:
        time.sleep(2.5)

    result = prober(timeout=timeout)

    # Docker Desktop glitch: container healthy, HostConfig has ports, but
    # nothing listens on the host. Force-recreate the compose service once.
    if not result.ok and had_local and compose_svcs:
        for svc in compose_svcs:
            ok, msg = _compose_up(svc, force_recreate=True)
            actions.append(msg)
            if ok:
                time.sleep(3.0)
                result = prober(timeout=timeout)
                restarted = True
                break

    # Remote-host fallback for headroom/litellm/grafana when local still down
    if not result.ok and nas_host_names:
        ok, msg = _restart_nas_host_containers(nas_host_names)
        actions.append(msg)
        if ok:
            time.sleep(2.5)
            result = prober(timeout=timeout)
            restarted = True

    result.healed = bool(actions) and result.ok
    result.heal_actions = actions
    if actions and not result.ok:
        result.detail = (result.detail or "still down") + " after: " + "; ".join(actions)
    elif not actions and not result.ok:
        result.detail = (result.detail or "down") + " · no container found to restart"
    return result


def run_smoke(
    *,
    heal: bool = False,
    timeout: float = 2.0,
    services: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    wanted = list(services) if services else [s[0] for s in CORE_SERVICES]
    results: List[ProbeResult] = []
    for sid in wanted:
        prober = _get_prober(sid)
        if not prober:
            continue
        first = prober(timeout=timeout)
        if first.ok or not heal:
            results.append(first)
            continue
        results.append(heal_service(sid, timeout=timeout))

    down = [r.id for r in results if not r.ok]
    lines = []
    for r in results:
        badge = "OK" if r.ok else "DOWN"
        extra = f"  {r.url}" if r.url else ""
        heal_note = ""
        if r.heal_actions:
            heal_note = f"  [{'; '.join(r.heal_actions)}]"
            if r.healed:
                heal_note = f"  [healed: {'; '.join(r.heal_actions)}]"
        lines.append(f"  {r.label:<10} {badge}{extra}  {r.detail}{heal_note}".rstrip())

    report = {
        "schema": "grok_tua.stack_smoke.v1",
        "heal": heal,
        "ok": not down,
        "services_down": down,
        "results": [
            {
                "id": r.id,
                "label": r.label,
                "ok": r.ok,
                "url": r.url,
                "detail": r.detail,
                "healed": r.healed,
                "heal_actions": r.heal_actions,
            }
            for r in results
        ],
        "lines": lines,
    }
    return report


def format_smoke_report(report: Dict[str, Any]) -> str:
    header = "grok-tua stack smoke" + (" + heal" if report.get("heal") else "")
    status = "all clear" if report.get("ok") else f"down: {', '.join(report.get('services_down') or [])}"
    body = "\n".join(report.get("lines") or [])
    return f"[grok-tua] {header} — {status}\n{body}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="grok-tua core stack smoke test")
    parser.add_argument(
        "--heal",
        action="store_true",
        help="Restart known Docker containers when probes fail",
    )
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    parser.add_argument(
        "--services",
        default="",
        help="Comma list: headroom,litellm,grafana,prompt_io (default all)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    svcs = [s.strip() for s in args.services.split(",") if s.strip()] or None
    report = run_smoke(heal=args.heal, timeout=args.timeout, services=svcs)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_smoke_report(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
