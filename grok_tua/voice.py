"""grok-tua voice mode — reuses tok-tua talk2ya/V.O.X. + Headroom manager-auto path."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

# Shared implementation lives in tok_tua.voice (same stack: talk2ya + Headroom).
from tok_tua.voice import (  # noqa: F401
    headroom_chat,
    voice_health,
    voice_llm_check,
    voice_oneshot_prompt,
    voice_ptt,
    voice_speak,
    voice_transcribe,
)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="grok-tua voice",
        description="grok-tua voice mode (talk2ya STT/TTS + Headroom manager-auto)",
    )
    sub = parser.add_subparsers(dest="vcmd")
    sub.add_parser("check", help="Health probe")

    p_speak = sub.add_parser("speak", help="TTS")
    p_speak.add_argument("text", nargs="+")
    p_speak.add_argument("--live", action="store_true")

    p_stt = sub.add_parser("transcribe", help="STT a path")
    p_stt.add_argument("audio_path")

    p_llm = sub.add_parser("llm-check", help="Mic→STT→manager-auto→TTS")
    p_llm.add_argument("--seconds", type=float, default=4.0)
    p_llm.add_argument("--model", default="manager-auto")
    p_llm.add_argument("--live", action="store_true", default=True)
    p_llm.add_argument("--no-live", action="store_true")
    p_llm.add_argument("--no-speak", action="store_true")
    p_llm.add_argument("--text", default=None)
    p_llm.add_argument("--audio", default=None)

    p_ptt = sub.add_parser("ptt", help="Timed PTT")
    p_ptt.add_argument("--seconds", type=float, default=5.0)
    p_ptt.add_argument("--ack", action="store_true")

    args = parser.parse_args(argv)
    vcmd = args.vcmd or "check"

    if vcmd == "check":
        h = voice_health()
        h["agent"] = "grok-tua"
        print(json.dumps(h, indent=2, default=str))
        return 0 if h.get("ok") or h.get("talk2ya") else 1

    if vcmd == "speak":
        print(json.dumps(voice_speak(" ".join(args.text), live=args.live), indent=2, default=str))
        return 0

    if vcmd == "transcribe":
        print(json.dumps(voice_transcribe(args.audio_path, live=True), indent=2, default=str))
        return 0

    if vcmd == "ptt":
        print(json.dumps(voice_ptt(seconds=args.seconds, speak_ack=args.ack, handoff="grok-tua"), indent=2, default=str))
        return 0

    if vcmd == "llm-check":
        live = not args.no_live
        result = voice_llm_check(
            seconds=args.seconds,
            model=args.model or "manager-auto",
            live=live,
            speak_reply=not args.no_speak,
            text=args.text,
            audio_path=args.audio,
            agent="grok-tua",
        )
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("ok") else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
