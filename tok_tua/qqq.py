"""QQQ mode (Queue Queue Queue) resolution + gating for tok-tua.

See docs/operations/QQQ_CODING_ORCHESTRATION.md and config/qqq_orchestration.json.
QQQ0 = local-only (default) · QQQ1 = paid cloud subscriptions · QQQ3 = free/kitchen-sink cloud.

This module is a structural gate only (mode + data_class), mirroring
tok_tua.routes.assert_model_allowed. It does not scan prompt/message content —
that's the deeper K.A.R.E.N./L.A.D.I.E.S. earmark (item 5 in the QQQ doc),
which is out of scope here since there is no prompt text yet at CLI-launch time.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from tok_tua.routes import get_route

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "qqq_orchestration.json"

KNOWN_MODES = ("QQQ0", "QQQ1", "QQQ3")

_PHI_DATA_CLASSES = frozenset({"phi", "phi-adjacent", "caregiving_dumps"})

_config_cache: dict[str, Any] | None = None


def load_qqq_config(path: Path | None = None) -> dict[str, Any]:
    global _config_cache
    cfg_path = path or DEFAULT_CONFIG
    if _config_cache is not None and path is None:
        return _config_cache
    if not cfg_path.is_file():
        data: dict[str, Any] = {"default_mode": "QQQ0", "modes": {}}
    else:
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"default_mode": "QQQ0", "modes": {}}
    if path is None:
        _config_cache = data
    return data


def _normalize(mode: str | None) -> str | None:
    if not mode:
        return None
    m = str(mode).strip().upper()
    if m in {"0", "1", "3"}:
        m = f"QQQ{m}"
    return m


def current_mode(explicit: str | None = None) -> tuple[str, bool]:
    """Resolve --qqq arg -> TOK_TUA_QQQ env -> config default_mode.

    Returns (mode, was_explicit) — was_explicit is True only when the caller
    passed a value or TOK_TUA_QQQ is set (QQQ1 requires explicit_mode_select).
    """
    cfg = load_qqq_config()
    from_arg = _normalize(explicit)
    if from_arg:
        return from_arg, True
    from_env = _normalize(os.environ.get("TOK_TUA_QQQ"))
    if from_env:
        return from_env, True
    default = _normalize(cfg.get("default_mode")) or "QQQ0"
    return default, False


def assert_qqq_allowed(
    mode: str,
    model_id: str | None = None,
    *,
    data_class: str = "public_code",
    explicit: bool = False,
) -> None:
    """Raise ValueError if this QQQ mode selection is not allowed.

    Structural checks only: mode validity, explicit-select requirement for
    QQQ1, and PHI-adjacent data classes refused for QQQ1/QQQ3 — matching
    config/qqq_orchestration.json's per-mode "requires"/"refuse_if" lists.
    """
    if mode not in KNOWN_MODES:
        raise ValueError(f"unknown QQQ mode {mode!r}; known: {', '.join(KNOWN_MODES)}")

    dc = (data_class or "public_code").strip().lower()

    if mode == "QQQ1" and not explicit:
        raise ValueError(
            "QQQ1 (paid cloud) requires explicit selection — pass --qqq 1 or set TOK_TUA_QQQ=QQQ1"
        )

    if mode in ("QQQ1", "QQQ3") and dc in _PHI_DATA_CLASSES:
        raise ValueError(
            f"QQQ mode {mode} refused for data_class={dc!r} (phi/phi-adjacent/caregiving_dumps "
            "never leave QQQ0 without explicit override + NARC)"
        )

    if model_id:
        route = get_route(model_id)
        if route is not None and mode in ("QQQ1", "QQQ3") and not route.get("cloud") and dc in _PHI_DATA_CLASSES:
            # local-only model under a cloud mode with phi data_class: still refuse (belt+suspenders)
            raise ValueError(f"model {model_id!r} refused under {mode} for data_class={dc!r}")


def log_qqq_selection(
    mode: str,
    *,
    model_id: str | None = None,
    cli_id: str | None = None,
    explicit: bool = False,
    data_class: str = "public_code",
) -> None:
    """Best-effort audit log of a QQQ mode selection (never blocks on failure).

    log_action() prints a status line to stdout — harmless for interactive use,
    but it would corrupt JSON emitted by `tok-tua resolve`/`tok-tua spawn`
    (both call resolve_launch, which calls this). Suppress just this call's
    stdout rather than touching syn_napse's global behavior.
    """
    import contextlib
    import io

    try:
        from context.syn_napse import log_action

        with contextlib.redirect_stdout(io.StringIO()):
            log_action(
                "QQQ_MODE_SELECTED",
                {
                    "mode": mode,
                    "model_id": model_id,
                    "cli_id": cli_id,
                    "explicit": explicit,
                    "data_class": data_class,
                },
                actor="tok-tua",
            )
    except Exception:
        pass


def qqq_mode_label(mode: str) -> str:
    cfg = load_qqq_config()
    entry = (cfg.get("modes") or {}).get(mode) or {}
    label = entry.get("label")
    return f"{mode} ({label})" if label else mode
