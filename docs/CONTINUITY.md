# Context pit + multi-CLI continuity (public)

**Status:** canonical for this repo — read before cold-start rediscovery  
**Purpose:** stop agents from rediscovering progress via unbounded filewalk, SSH fan-out, or bulk session dumps

Private monorepos may keep richer handoffs. This public contract is the **pattern** only.

---

## 0. Thirty-second start

```text
1. git status + branch (authoritative for code)
2. This file + latest docs/handoffs/SESSION_HANDOFF_* (if present)
3. Prefer Headroom :8787 → LiteLLM (never raw worker :4000 for normal use)
4. Launch: ./bin/tok-tua --cli pi --model manager-code
   or:     ./bin/grok-tua
5. On context full: compact OR new chat + citation packet — not full-history spill
6. After a plan turn: write a handoff from docs/HANDOFF_TEMPLATE.md before multi-agent handoff
```

Related:

- [HANDOFF_TEMPLATE.md](./HANDOFF_TEMPLATE.md)
- [ORCHESTRATION.md](./ORCHESTRATION.md)

---

## 1. Routing law

```text
Human prompt
  → optional local memory / citation tags (if you use Hippo or similar)
  → Headroom compress (:8787)
  → LiteLLM place (:4000 workers; prefer GPU host for heavy code)
  → CLI / TUI brand (pi · codex · opencode · Claude · Grok · tok-tua · grok-tua)
  → on length / freeze / burn:
       compact OR new session + this pit + handoff packet
```

| Brand / surface | Preferred entry | Notes |
|-----------------|-----------------|-------|
| **tok-tua** | `./bin/tok-tua --cli pi --model manager-code` | PHI / care data: local-only models only |
| **grok-tua** | `./bin/grok-tua` | SuperGrok + stats board; dual-pane target UX |
| **Herdr** | `tok-tua --scale herdr --cli <kind>` | Multi-pane still fragile; prefer single dual-pane first |
| **Turnstone** | `tok-tua --scale turnstone` or `python -m tok_tua turnstone health` | Workstream UI/API — not a full handoff orchestrator |
| **loop** | `python -m tok_tua loop --cli pi --dry-run` | Wait → read → write handoff (prototype) |

---

## 2. Dual-pane layout (scale=single)

Both launchers open **coding left / metrics right**:

| Launcher | Right strip default | Env override |
|----------|---------------------|--------------|
| grok-tua | **20%** | `GROK_TUA_STATS_PCT` |
| tok-tua | **20%** (aligned) | `TOK_TUA_STATS_PCT` |

Larger pane on the left is intentional. Do not “fix” broken multi-pane by spawning unbounded SSH/tmux retries.

---

## 3. Anti-patterns (print in every agent prompt)

**Do:**

```bash
# structure / catalog only when you have a script for it
# exact path from a handoff
ls path/from/handoff/
# single BatchMode SSH with a known command (if remote is intentional)
ssh -o BatchMode=yes -o ConnectTimeout=5 host 'hostname; uptime'
```

**Do not:**

- Unbounded `find` / recursive `rg` across entire NFS / data volumes “for context”
- Multi-host SSH fan-out to “see what’s running”
- Reload multi-megabyte session transcripts into a new model
- Treat a local session browser as source of truth for code (IDs only → handoffs)
- Send care / PHI-class data to free-cloud or paid-cloud façades without explicit consent

---

## 4. Plan → handoff → next agent

```text
plan (short) → write HANDOFF + PLAN (docs/handoffs/) → execute via tok-tua / Herdr
```

1. Keep cloud plan turns short; write the packet before implementing.
2. Next brand loads **handoff path + this file**, not a full prior transcript.
3. Optional: `python -m tok_tua loop --cli pi --model manager-code` (default dry-run) then `--live` when Herdr is up.

---

## 5. What stays out of this public repo

Care modules, medical staging paths, patient-identifying notes, private monorepo milestone dumps, and credential-bearing remotes. Gateway CLIs and the continuity **pattern** live here; full M.A.N.A.G.E.R. agent mesh does not.
