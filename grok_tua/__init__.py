"""grok-tua — Grok Textual User Agent: launch wrapper + gateway stack metrics.

Primary entrypoints:
  - ``grok-tua`` console script / ``bin/grok-tua`` → tmux wrapper (CLI + monitor)
  - ``python -m grok_tua.dashboard`` → Textual / headless metrics panels

Metrics prefer Herdr status + LiteLLM spend/health via Headroom (:8787 → :4000).
"""

__version__ = "0.2.0"
