"""tok-tua voice mode — PTT / talk2ya / V.O.X. + Headroom manager-auto loop."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[1]
_CONFIG = _REPO / "config" / "tok_tua.json"

DEFAULT_GATEWAY = "http://127.0.0.1:8787/v1"
DEFAULT_MODEL = "manager-auto"
VOICE_SYSTEM = (
    "You are a brief voice assistant on this desk. "
    "Answer in one short spoken sentence. No markdown, no lists."
)


def _load_tok_config() -> Dict[str, Any]:
    if not _CONFIG.is_file():
        return {}
    try:
        return json.loads(_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def voice_config() -> Dict[str, Any]:
    cfg = _load_tok_config()
    voice = dict(cfg.get("voice") or {})
    voice.setdefault("enabled", False)
    voice.setdefault("orchestrator", "vox")
    voice.setdefault("io_backend", "talk2ya")
    voice.setdefault("ptt_mode", "software_hotkey")
    voice.setdefault("input_device_match", ["JOUNIVO", "JV-601"])
    voice.setdefault("model", DEFAULT_MODEL)
    voice.setdefault("gateway", (_load_tok_config().get("defaults") or {}).get("gateway", DEFAULT_GATEWAY))
    return voice


def vision_config() -> Dict[str, Any]:
    cfg = _load_tok_config()
    vision = dict(cfg.get("vision") or {})
    vision.setdefault("enabled", False)
    vision.setdefault("backend", "show-u-a")
    vision.setdefault("route", "manager-vision")
    vision.setdefault("opencv", True)
    return vision


def _ensure_gateway_keys() -> None:
    """Load this repo's .env gateway keys into process if unset (same path as tok-tua launch)."""
    try:
        from grok_tua.stack_metrics import ensure_gateway_env

        ensure_gateway_env()
    except Exception:
        env_path = _REPO / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v


def headroom_chat(
    user_text: str,
    *,
    model: Optional[str] = None,
    gateway: Optional[str] = None,
    system: Optional[str] = None,
    max_tokens: int = 80,
    temperature: float = 0.2,
    timeout_sec: float = 90.0,
) -> Dict[str, Any]:
    """OpenAI-compatible chat via Headroom → LiteLLM (local/manager-auto path)."""
    _ensure_gateway_keys()
    cfg = voice_config()
    defaults = _load_tok_config().get("defaults") or {}
    gw = (gateway or cfg.get("gateway") or defaults.get("gateway") or DEFAULT_GATEWAY).rstrip("/")
    if not gw.endswith("/v1"):
        gw = gw + "/v1" if not gw.endswith("v1") else gw
    model_id = model or cfg.get("model") or defaults.get("model") or DEFAULT_MODEL
    key = (
        os.environ.get("LITELLM_MASTER_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    if not key:
        return {
            "ok": False,
            "error": "missing LITELLM_MASTER_KEY / OPENAI_API_KEY (set in this repo's .env)",
            "model": model_id,
            "gateway": gw,
        }

    url = f"{gw}/chat/completions"
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system or VOICE_SYSTEM},
            {"role": "user", "content": user_text},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")[:400]
        return {"ok": False, "error": f"HTTP {exc.code}: {err_body}", "model": model_id, "gateway": gw}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300], "model": model_id, "gateway": gw}

    content = ""
    try:
        content = str((body.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
    except Exception:
        content = ""
    return {
        "ok": bool(content),
        "text": content,
        "model": body.get("model") or model_id,
        "requested_model": model_id,
        "gateway": gw,
        "usage": body.get("usage"),
        "id": body.get("id"),
    }


def voice_health() -> Dict[str, Any]:
    """Probe talk2ya + V.O.X. readiness for tok-tua voice mode."""
    from integrations.talk2ya.talk2ya_adapter import health as talk2ya_health

    t2y = talk2ya_health()
    vox_ok = False
    try:
        from agents.vox.vox_agent import VoxAgent

        vox_ok = VoxAgent is not None
    except Exception as exc:
        return {
            "ok": False,
            "voice": voice_config(),
            "vision": vision_config(),
            "talk2ya": t2y,
            "vox": {"available": False, "error": str(exc)[:200]},
        }

    _ensure_gateway_keys()
    key_ok = bool(os.environ.get("LITELLM_MASTER_KEY") or os.environ.get("OPENAI_API_KEY"))
    gw = (voice_config().get("gateway") or DEFAULT_GATEWAY).rstrip("/")
    headroom = {"gateway": gw, "key_present": key_ok, "ready": False}
    try:
        base = gw[:-3] if gw.endswith("/v1") else gw
        req = urllib.request.Request(f"{base}/health", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            headroom["ready"] = resp.status < 400
            headroom["status_code"] = resp.status
    except Exception as exc:
        headroom["error"] = str(exc)[:120]

    return {
        "ok": bool(t2y.get("ready")) and vox_ok,
        "voice": voice_config(),
        "vision": vision_config(),
        "talk2ya": t2y,
        "vox": {"available": True, "orchestrator": "V.O.X."},
        "headroom": headroom,
        "model": voice_config().get("model") or DEFAULT_MODEL,
        "hint": "python -m tok_tua voice check | voice llm-check --seconds 4 --live | voice ptt --seconds 5",
    }


def voice_speak(text: str, *, live: bool = False) -> Dict[str, Any]:
    from agents.vox.vox_agent import VoxAgent

    return VoxAgent().text_to_speech(text, live=live)


def voice_transcribe(audio_path: str, *, live: bool = True) -> Dict[str, Any]:
    from agents.vox.vox_agent import VoxAgent

    return VoxAgent().speech_to_text(audio_path, live=live)


def voice_ptt(
    *,
    seconds: Optional[float] = None,
    live: bool = True,
    speak_ack: bool = False,
    handoff: str = "tok-tua",
) -> Dict[str, Any]:
    """Timed software PTT via V.O.X. → talk2ya (JOUNIVO preferred device)."""
    from agents.vox.vox_agent import VoxAgent

    return VoxAgent().ptt_turn(
        seconds=seconds,
        live=live,
        speak_ack=speak_ack,
        target=handoff,
    )


def voice_oneshot_prompt(transcript: str) -> Dict[str, Any]:
    """
    Build a one-shot coding prompt package for Headroom / CLI (no auto-spawn).

    Callers can feed this into their preferred CLI; PHI gates stay with resolve_launch.
    """
    text = (transcript or "").strip()
    return {
        "ok": bool(text),
        "mode": "oneshot",
        "prompt": text,
        "suggested_cli": (_load_tok_config().get("defaults") or {}).get("cli", "codex"),
        "suggested_model": (_load_tok_config().get("defaults") or {}).get("model", "manager-auto"),
        "note": "Use resolve_launch + spawn or paste into active tok-tua session",
    }


def voice_llm_check(
    *,
    seconds: float = 4.0,
    model: Optional[str] = None,
    live: bool = True,
    speak_reply: bool = True,
    text: Optional[str] = None,
    audio_path: Optional[str] = None,
    agent: str = "tok-tua",
) -> Dict[str, Any]:
    """
    Simple voice path test:
      mic (or text/audio) → STT → Headroom manager-auto → TTS reply.

    Local LLM path is Headroom :8787 → LiteLLM (manager-auto resolves to worker).
    """
    from agents.vox.vox_agent import VoxAgent

    out: Dict[str, Any] = {
        "ok": False,
        "agent": agent,
        "action": "voice_llm_check",
        "model": model or voice_config().get("model") or DEFAULT_MODEL,
        "stages": {},
    }
    vox = VoxAgent()
    transcript = (text or "").strip()

    if not transcript and audio_path:
        stt = vox.speech_to_text(audio_path, live=live)
        out["stages"]["stt"] = stt
        transcript = str(stt.get("transcript") or "")
    elif not transcript:
        from integrations.talk2ya.talk2ya_adapter import record_timed_burst, transcribe

        if live:
            cap = record_timed_burst(seconds)
            out["stages"]["capture"] = cap
            if cap.get("status") != "ok":
                out["error"] = "capture_failed"
                out["status"] = "error"
                return out
            stt = transcribe(str(cap["path"]), live=True)
            out["stages"]["stt"] = stt
            transcript = str(stt.get("text") or "")
        else:
            out["stages"]["capture"] = {"status": "dry_run", "seconds": seconds}
            transcript = "Voice path check dry run"

    out["transcript"] = transcript
    if not transcript:
        out["error"] = "empty_transcript"
        out["status"] = "empty"
        # still speak a hint
        if speak_reply and live:
            out["stages"]["tts"] = vox.text_to_speech(
                "I did not catch that on the JOUNIVO mic. Try again.",
                live=True,
            )
        return out

    chat = headroom_chat(transcript, model=out["model"])
    out["stages"]["llm"] = chat
    reply = str(chat.get("text") or "").strip()
    out["reply"] = reply
    out["resolved_model"] = chat.get("model")

    if speak_reply and reply:
        out["stages"]["tts"] = vox.text_to_speech(reply, live=live)
    elif speak_reply and not reply and live:
        out["stages"]["tts"] = vox.text_to_speech(
            "Headroom did not return a reply. Check manager-auto via LiteLLM.",
            live=True,
        )

    out["ok"] = bool(chat.get("ok") and transcript)
    out["status"] = "ok" if out["ok"] else "error"
    if not chat.get("ok"):
        out["error"] = chat.get("error")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="tok-tua voice", description="tok-tua voice mode (V.O.X. + talk2ya)")
    sub = parser.add_subparsers(dest="vcmd")

    sub.add_parser("check", help="Health probe")

    p_speak = sub.add_parser("speak", help="TTS via V.O.X./talk2ya")
    p_speak.add_argument("text", nargs="+")
    p_speak.add_argument("--live", action="store_true")

    p_stt = sub.add_parser("transcribe", help="STT a wav/path")
    p_stt.add_argument("audio_path")
    p_stt.add_argument("--live", action="store_true", default=True)

    p_ptt = sub.add_parser("ptt", help="Timed software PTT turn")
    p_ptt.add_argument("--seconds", type=float, default=None)
    p_ptt.add_argument("--live", action="store_true", default=True)
    p_ptt.add_argument("--no-live", action="store_true", help="Dry-run capture/STT where possible")
    p_ptt.add_argument("--ack", action="store_true", help="Speak routing ack")
    p_ptt.add_argument("--handoff", default="tok-tua")

    p_prompt = sub.add_parser("prompt", help="Package transcript as oneshot prompt JSON")
    p_prompt.add_argument("text", nargs="+")

    p_llm = sub.add_parser(
        "llm-check",
        help="Mic→STT→Headroom manager-auto→TTS (simple voice path test)",
    )
    p_llm.add_argument("--seconds", type=float, default=4.0)
    p_llm.add_argument("--model", default=None, help="default manager-auto")
    p_llm.add_argument("--live", action="store_true", default=True)
    p_llm.add_argument("--no-live", action="store_true")
    p_llm.add_argument("--no-speak", action="store_true", help="Skip TTS reply")
    p_llm.add_argument("--text", default=None, help="Skip mic; send this text to LLM")
    p_llm.add_argument("--audio", default=None, help="Transcribe this file instead of mic")

    args = parser.parse_args(argv)
    vcmd = args.vcmd or "check"

    if vcmd == "check":
        h = voice_health()
        print(json.dumps(h, indent=2, default=str))
        return 0 if h.get("ok") or h.get("talk2ya") else 1

    if vcmd == "speak":
        text = " ".join(args.text)
        print(json.dumps(voice_speak(text, live=args.live), indent=2, default=str))
        return 0

    if vcmd == "transcribe":
        print(json.dumps(voice_transcribe(args.audio_path, live=args.live), indent=2, default=str))
        return 0

    if vcmd == "ptt":
        live = not args.no_live
        print(
            json.dumps(
                voice_ptt(
                    seconds=args.seconds,
                    live=live,
                    speak_ack=args.ack,
                    handoff=args.handoff,
                ),
                indent=2,
                default=str,
            )
        )
        return 0

    if vcmd == "prompt":
        print(json.dumps(voice_oneshot_prompt(" ".join(args.text)), indent=2))
        return 0

    if vcmd == "llm-check":
        live = not args.no_live
        result = voice_llm_check(
            seconds=args.seconds,
            model=args.model,
            live=live,
            speak_reply=not args.no_speak,
            text=args.text,
            audio_path=args.audio,
            agent="tok-tua",
        )
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("ok") else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
