# Session handoff template

Copy this file to `docs/handoffs/SESSION_HANDOFF_YYYY-MM-DD_<slug>.md` after a plan turn, or use:

```bash
PYTHONPATH=. python3 -m tok_tua loop --cli pi --model manager-code --name my-task
# default is dry-run; writes a filled template under docs/handoffs/
```

---

## 0. Paste into new chat (first message)

```text
Continue from docs/handoffs/SESSION_HANDOFF_YYYY-MM-DD_<slug>.md
Also: docs/CONTINUITY.md
Routing: prompt → citations → Headroom :8787 → LiteLLM → prefer local GPU workers

DONE:
- …

OPEN:
- …

Sensitive care / PHI: local-only models. No bulk session dumps.
```

```bash
export HEADROOM_BASE="${HEADROOM_BASE:-http://127.0.0.1:8787}"
./bin/tok-tua --cli pi --model manager-code
# SuperGrok path:
./bin/grok-tua
# Herdr scale (optional):
./bin/tok-tua --scale herdr --cli pi --model manager-code
```

---

## 1. Done

| Track | Status | Where |
|-------|--------|--------|
| … | … | … |

---

## 2. Open / next (operator pick)

1. …
2. …

---

## 3. Operator laws

```text
prompt → citations → Headroom :8787 → LiteLLM → prefer GPU host for heavy code
on length/full: compact OR new session + this packet
plan short → HANDOFF+PLAN → local execute
sensitive data → local-only models
```

---

## 4. Session citations (IDs only)

| Surface | ID / path |
|---------|-----------|
| This session | … |
| Related | … |

---

## 5. Dual-pane / scale notes

- Default layout: left coding ~80% · right metrics ~20% (`TOK_TUA_STATS_PCT` / `GROK_TUA_STATS_PCT`)
- Prefer `scale=single` until Herdr multipane is healthy
- Turnstone is optional visibility (`python -m tok_tua turnstone health`), not the handoff SoR
