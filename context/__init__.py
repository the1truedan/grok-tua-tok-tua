"""Shared context helpers for grok-tua / tok-tua.

Vendored from the original monorepo's ``integrations/context`` and
``memory`` packages, trimmed to just the pieces grok-tua/tok-tua actually
import:

- ``grok_credit_monitor`` — SuperGrok session discovery + credit/usage
  accounting (session log parsing, billing report formatting).
- ``syn_napse`` — best-effort local audit-log helper used for QQQ mode
  selection logging (optional; failures are swallowed by callers).
"""
