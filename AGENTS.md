# Agent start (public extract)

Read before editing or launching multi-CLI sessions:

1. **[docs/CONTINUITY.md](docs/CONTINUITY.md)** — context pit law  
2. **[docs/HANDOFF_TEMPLATE.md](docs/HANDOFF_TEMPLATE.md)** — paste-first packet  
3. **[docs/ORCHESTRATION.md](docs/ORCHESTRATION.md)** — Headroom / Herdr / Turnstone  

## Quick launch

```bash
./bin/tok-tua --cli pi --model manager-code
./bin/grok-tua
PYTHONPATH=. python3 -m tok_tua loop --cli pi --model manager-code
```

## Rules

- Dual-pane: **left coding larger (~80%)**, right metrics (~20%). Override with `TOK_TUA_STATS_PCT` / `GROK_TUA_STATS_PCT`.
- Prefer Headroom `:8787` → LiteLLM; do not bypass to raw `:4000` for normal work.
- After a plan turn, write `docs/handoffs/SESSION_HANDOFF_*.md` before handing off to another brand.
- No unbounded filesystem walks or SSH fan-out for “context.”
- Session browsers: copy **IDs into handoffs** only — no bulk transcript dumps.
- Sensitive care data: local-only models; never free-cloud façades.

This repo is the **gateway CLI + metrics** public surface. Full care-agent modules live in the private monorepo.
