# grok-tua / tok-tua

Two companion terminal launch-wrappers for coding-CLI sessions, built around a
shared local LLM gateway ("Headroom" → LiteLLM). Both give you a tmux session
with your coding CLI on one side and a live metrics panel (gateway health,
spend, CLI versions, credit/quota tallies) on the other.

They ship in one repo because they **cross-import each other**:
`grok_tua/voice.py` imports from `tok_tua.voice`, and `tok_tua/stack_metrics.py`
imports from `grok_tua.stack_metrics`. Splitting them would break both.

## What each tool does

**`grok-tua`** — launch wrapper specifically for xAI's Grok/SuperGrok Build
CLI. Opens a two-pane tmux session: `grok` on the left, a Textual metrics
dashboard on the right showing gateway health, SuperGrok session burn/quota,
system meters (CPU/RAM/GPU), and git status. On launch it smoke-tests your
local gateway stack and can optionally restart known Docker containers or a
remote host's containers over SSH if things are down.

**`tok-tua`** — a more general multi-CLI launcher ("Token Textual User
Agent"). Same tmux + metrics-dashboard idea, but works with any of several
coding CLIs (Codex, Claude Code, Cursor, aider, OpenCode, pi, omp, tau, or
Grok itself — see `tok_tua/providers.py` for the full registry) routed
through the shared gateway using a `manager-*` model route (see
`tok_tua/routes.py`). It also implements:
- **QQQ mode** (`--qqq 0|1|3`): a coarse routing gate between local-only,
  paid-cloud, and free-cloud model tiers, with a PHI/data-class refusal rule
  (`tok_tua/qqq.py`, `config/qqq_orchestration.json`).
- **Scale modes** (`--scale single|herdr|turnstone`): single tmux pane pair,
  a multi-agent "herdr" launcher, or opening a "Turnstone" web UI
  (`tok_tua/scale.py`, `tok_tua/turnstone_client.py`).
- **Session adapters** for per-CLI session-file introspection (Codex, Grok,
  generic) used to show recent session context in the dashboard.

Both tools pull shared plumbing from `context/` (vendored from the original
monorepo's `integrations/context` and `memory` packages — see below) and
`grok_tua/cloud_credits.py` / `grok_tua/stats_board.py` for multi-vendor
credit/quota tallying (OpenRouter, Gemini, OpenAI, Claude, xAI).

## Layout

```
bin/grok-tua                    launch wrapper (bash) for the Grok/SuperGrok CLI
bin/tok-tua                     launch wrapper (bash) for any registered coding CLI
bin/grok_credit_usage_report.py standalone CLI: usage/credit report, watch loop, session index
grok_tua/                       grok-tua's Python package (dashboard, metrics, voice, credits)
tok_tua/                        tok-tua's Python package (providers, routes, qqq, scale, dashboard)
context/                        shared helpers both packages import (see below)
config/                         JSON config templates (qqq_orchestration.json, tok_tua.json)
.env.example                    copy to .env and fill in for your setup
```

### `context/` package

Trimmed and vendored out of the original monorepo's `integrations/context`
and `memory` packages — only the two leaf modules that grok-tua/tok-tua
actually import, each with zero further external dependencies:

- **`context/grok_credit_monitor.py`** — SuperGrok session discovery, credit
  usage accounting, and the JSON/forensic report formatters used by both the
  dashboards and `bin/grok_credit_usage_report.py`. (Originally
  `integrations/context/grok_credit_monitor.py`.)
- **`context/syn_napse.py`** — a tiny best-effort local audit-log helper,
  used by `tok_tua/qqq.py` to log QQQ-mode selections. Optional: if this
  import fails for any reason, the caller silently skips logging.
  (Originally `memory/syn_napse.py`.)

## What's *not* included

`grok_tua/voice.py` and `tok_tua/voice.py` implement an optional voice mode
(`grok-tua voice …` / `tok-tua voice`) that layers on top of a much larger,
separate personal voice-assistant stack: a "talk2ya" PTT/STT/TTS adapter, a
"V.O.X." orchestrator agent, and further TTS backends (kokoro, chatterbox)
plus an ASR pipeline — none of which are part of this repo. Those imports
are all *lazy* (inside function bodies, not at module import time), so
importing `grok_tua`/`tok_tua` and running the dashboards/routing/QQQ/scale
features works with zero effect from this. Only if you actually invoke
`voice` will you hit a plain `ImportError` for the missing stack — there's
no hidden dependency on it otherwise.

## Install / run

Requires Python 3.10+, `tmux`, and a running OpenAI-compatible gateway
(Headroom → LiteLLM, or point `HEADROOM_BASE`/`LITELLM_BASE` at your own).
`textual` and `psutil` are optional but recommended (`pip install textual
psutil`) for the full dashboard; without them the tools fall back to a
plain `--check` text loop.

```sh
git clone <this-repo>
cd grok-tua-tok-tua
cp .env.example .env      # fill in LITELLM_MASTER_KEY / NAS_HOST_IP etc. as needed
./bin/tok-tua              # launch: codex + metrics dashboard
./bin/tok-tua --cli claude --qqq 0
./bin/grok-tua              # launch: grok + metrics dashboard
```

Every gateway URL defaults to `127.0.0.1` — nothing points at anyone else's
machine out of the box. `.env.example` documents the optional LAN-fallback
variables (`NAS_HOST_IP`, `GPU_HOST_IP`, `NAS_HOST_SSH`, …) if you run your
gateway on a second machine on your own network.

Both wrappers also work invoked directly as Python modules, e.g.:

```sh
PYTHONPATH=. python3 -m tok_tua stack
PYTHONPATH=. python3 -m tok_tua providers
PYTHONPATH=. python3 -m grok_tua.dashboard --check
```

## License

MIT — see `LICENSE`.
