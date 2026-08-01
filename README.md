# grok-tua / tok-tua

Two small launchers that open a coding CLI next to a live status pane (gateway
health, rough spend, system meters). They talk to a local LLM gateway
(Headroom → LiteLLM) so you can see whether the stack is alive before you
burn a session.

They live in **one repo on purpose**: each imports a bit of the other. Split
them and both break.

## What each one is for

**`grok-tua`** — for the Grok / SuperGrok Build CLI. Left pane: `grok`. Right
pane: a compact dashboard (gateway, quota burn, CPU/RAM/GPU, git). On start it
can poke local Docker (and optionally a remote host) if something looks down.

**`tok-tua`** — same idea for *other* coding CLIs (Claude Code, Codex, Cursor,
aider, OpenCode, and friends — see `tok_tua/providers.py`). Extra knobs:

- **QQQ** (`--qqq 0|1|3`) — prefer local vs paid cloud vs free cloud, with a
  hard “don’t send sensitive care data to the wrong place” rule.
- **Scale** (`--scale single|herdr|turnstone`) — one tmux pair, a multi-agent
  layout, or a web UI hook.
- **Session adapters** — light read of recent CLI session files for the panel.

Shared helpers live under `context/`, plus credit tally helpers for the usual
vendor dashboards (OpenRouter, Gemini, OpenAI, Claude, xAI).

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
