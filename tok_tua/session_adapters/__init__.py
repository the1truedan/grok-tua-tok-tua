"""Best-effort session burn adapters per CLI."""

from tok_tua.session_adapters.codex import codex_session_hint
from tok_tua.session_adapters.generic import generic_hint

__all__ = ["codex_session_hint", "generic_hint", "session_hint_for"]


def session_hint_for(cli_id: str) -> dict:
    if cli_id in {"codex"}:
        return codex_session_hint()
    if cli_id in {"grok"}:
        try:
            from tok_tua.session_adapters.grok import grok_session_hint

            return grok_session_hint()
        except Exception as exc:
            return {"available": False, "error": str(exc)}
    return generic_hint(cli_id)
