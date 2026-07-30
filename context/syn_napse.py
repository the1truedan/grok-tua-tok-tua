"""
memory/syn_napse.py

syn:N.A.P.S.E. — Immutable Audit & Metrics Layer

Every important action, decision, and external effect is logged here.
This is the "single source of truth" for compliance, grants (ACL), and self-review (J.E.S.U.S.).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

LOG_DIR = Path("logs/syn_napse")
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _today_log_path() -> Path:
    date_str = datetime.date.today().isoformat()
    return LOG_DIR / f"{date_str}.jsonl"


def new_flow_id() -> str:
    """UUID for correlating multi-hop ingest / LLM / compress events."""
    return str(uuid.uuid4())


def file_sha256(path: str | Path, *, max_bytes: int = 64 * 1024 * 1024) -> Optional[str]:
    """Content hash for data-in / data-out trail (capped for huge files)."""
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    total = 0
    try:
        with open(p, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    h.update(chunk[: max(0, max_bytes - (total - len(chunk)))])
                    h.update(b"\n#truncated")
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def log_action(
    action_type: str,
    payload: Dict[str, Any],
    actor: str = "M.A.N.A.G.E.R.",
    *,
    flow_id: Optional[str] = None,
    data_in: Optional[Dict[str, Any]] = None,
    data_out: Optional[Dict[str, Any]] = None,
    metrics: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Append an immutable audit record.

    Optional trail fields (backward-compatible):
      flow_id, data_in, data_out, metrics, session_id
    """
    record: Dict[str, Any] = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "actor": actor,
        "type": action_type,
        "payload": payload,
    }
    if flow_id:
        record["flow_id"] = flow_id
    if data_in is not None:
        record["data_in"] = data_in
    if data_out is not None:
        record["data_out"] = data_out
    if metrics is not None:
        record["metrics"] = metrics
    if session_id:
        record["session_id"] = session_id

    # Promote common keys from payload when callers only pass payload
    if not flow_id and isinstance(payload, dict) and payload.get("flow_id"):
        record["flow_id"] = payload["flow_id"]
    if not session_id and isinstance(payload, dict) and payload.get("session_id"):
        record["session_id"] = payload["session_id"]

    with open(_today_log_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
    print(f"📜 syn:N.A.P.S.E. logged: {action_type}")
    return record


def log_trail(
    action_type: str,
    *,
    actor: str = "M.A.N.A.G.E.R.",
    flow_id: Optional[str] = None,
    data_in: Optional[Dict[str, Any]] = None,
    data_out: Optional[Dict[str, Any]] = None,
    metrics: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Explicit data-in / data-out trail entry for ingest and gateway hops."""
    return log_action(
        action_type,
        payload or {},
        actor=actor,
        flow_id=flow_id or new_flow_id(),
        data_in=data_in,
        data_out=data_out,
        metrics=metrics,
        session_id=session_id,
    )


def data_in_from_path(
    path: str | Path,
    *,
    kind: str = "file",
    mime: Optional[str] = None,
) -> Dict[str, Any]:
    p = Path(path)
    info: Dict[str, Any] = {
        "kind": kind,
        "path": str(p),
        "mime": mime,
        "sha256": file_sha256(p) if p.is_file() else None,
    }
    if p.is_file():
        try:
            info["bytes"] = p.stat().st_size
        except OSError:
            pass
    return info


def data_out_briefs(
    *,
    caregiver_path: Optional[str] = None,
    caregivee_path: Optional[str] = None,
    summary_chars: int = 0,
    audiences: Optional[list] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "kind": "dual_briefs",
        "audiences": audiences or ["caregiver", "caregivee"],
        "summary_chars": summary_chars,
    }
    if caregiver_path:
        out["caregiver_path"] = caregiver_path
        out["caregiver_sha256"] = file_sha256(caregiver_path)
    if caregivee_path:
        out["caregivee_path"] = caregivee_path
        out["caregivee_sha256"] = file_sha256(caregivee_path)
    return out


def get_recent_audit(limit: int = 50) -> list[Dict[str, Any]]:
    """Read recent audit entries (most recent first)."""
    path = _today_log_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    entries = [json.loads(line) for line in lines[-limit:]]
    entries.reverse()
    return entries


def get_audit_by_flow_id(flow_id: str, limit: int = 200) -> list[Dict[str, Any]]:
    """Collect today's trail records sharing a flow_id (oldest first)."""
    path = _today_log_path()
    if not path.exists() or not flow_id:
        return []
    matches = []
    for line in path.read_text(encoding="utf-8").strip().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("flow_id") == flow_id or (
            isinstance(rec.get("payload"), dict) and rec["payload"].get("flow_id") == flow_id
        ):
            matches.append(rec)
            if len(matches) >= limit:
                break
    return matches
