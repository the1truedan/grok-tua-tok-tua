"""tok-tua — Token Textual User Agent: multi-CLI launch + gateway metrics.

Primary entrypoints:
  - ``bin/tok-tua`` / PATH install → tmux wrapper (CLI + metrics)
  - ``python -m tok_tua.dashboard`` → Textual / headless metrics
  - ``python -m tok_tua`` → CLI (stack / spawn / providers)

Default model route: ``manager-auto`` via Headroom → orchestrator (GPU-host preferred).
Specialized SuperGrok path remains ``grok-tua``.
"""

__version__ = "0.1.0"
