# Orchestration map (tok-tua / grok-tua)

## Gateway path

```text
CLI (pi, codex, opencode, claude, omp, …)
    │  OPENAI_BASE_URL=http://127.0.0.1:8787/v1
    ▼
Headroom :8787          # context conservation
    ▼
LiteLLM :4000           # route + spend
    └── workers (local GPU preferred for coding)
```

**Do not** point normal CLIs at raw `:4000` (skips Headroom).

Defaults are loopback. Optional LAN hosts go in `.env` (see `.env.example`) — never commit secrets.

### Common model aliases (when your gateway defines them)

| Alias | Use |
|-------|-----|
| `manager-auto` | Gateway picks placement |
| `manager-code` | Coding / tools |
| `manager-plan` / `manager-reason` | Plan / reason workers |
| `manager-phi-local` (if present) | Local-only sensitive data |

Cloud façades (`manager-openrouter-free`, paid `*-paid` models) are opt-in and refused for care/PHI-class work.

---

## Scale modes

| Scale | What happens | When |
|-------|--------------|------|
| **single** (default) | tmux: CLI left + metrics right (~80/20) | Everyday coding |
| **herdr** | Spawn allowlisted agent via Herdr, then attach Herdr TUI | Multi-agent terminal mesh |
| **turnstone** | Open Turnstone UI + metrics; REST via `python -m tok_tua turnstone` | Browser workstreams |

```bash
./bin/tok-tua --cli pi --model manager-code
./bin/tok-tua --scale herdr --cli pi --model manager-code
./bin/tok-tua --scale turnstone
PYTHONPATH=. python3 -m tok_tua spawn --cli pi --dry-run
PYTHONPATH=. python3 -m tok_tua loop --cli pi --model manager-code   # dry-run default
PYTHONPATH=. python3 -m tok_tua turnstone health
```

### Herdr kinds (upstream)

`pi`, `claude`, `codex`, `gemini`, `cursor`, `opencode`, `omp`, `grok`, and others supported by your Herdr install (`herdr agent start --kind`).

Herdr can:

- `agent start` / `prompt` / `wait --until idle|done|blocked`
- `agent read` / `pane read` / `wait-output`

tok-tua wires **spawn** and a thin **loop** (wait → compact read → handoff file). There is no Deck “dropdown” yet — use CLI flags or Herdr’s own TUI.

### Turnstone

Turnstone is a **workstream REST + UI** plane (health, models, workstreams, send, history). It does **not** auto-watch tmux panes or write handoff files. Use it for human-visible multi-thread work; use Herdr wait + `docs/handoffs/` for agent continuity.

---

## Continuity

See [CONTINUITY.md](./CONTINUITY.md) and [HANDOFF_TEMPLATE.md](./HANDOFF_TEMPLATE.md).

---

## Hard-stops (deferred)

Context-fill hard-stop hooks (55%/75%, turn caps, forced writeout) are intentional follow-on work. Until then: compact early, new session + handoff packet, prefer local execute after short plan turns.
