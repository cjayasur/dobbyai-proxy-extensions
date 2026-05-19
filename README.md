# DobbyAI Proxy Extensions

Token-auth + local-vision routing that turns the open-source
[`1rgs/claude-code-proxy`](https://github.com/1rgs/claude-code-proxy) into a
**fully on-prem** Anthropic-compatible gateway: Claude-style clients (Claude
Code, the [`dobbyai-agentic`](https://github.com/cjayasur/dobbyai-agentic)
agent, anything that speaks the Anthropic Messages API) talking to
self-hosted open-weight models (Qwen / Llama) with **zero external API
egress** and a local, auditable API-key layer.

---

## ⚠️ Attribution & licensing (read first)

This repository contains **only my own original code.** It is *not* a
redistribution of the upstream proxy.

- The base proxy is **[`1rgs/claude-code-proxy`](https://github.com/1rgs/claude-code-proxy)**,
  which wires up [LiteLLM](https://github.com/BerriAI/litellm) for
  Anthropic↔OpenAI translation.
- At the time of writing, **the upstream repo declares no license** (no
  `LICENSE` file, no SPDX) — so all rights are reserved to its author.
- Because of that I deliberately **do not republish their code.** This repo
  ships only files I wrote, plus my `server.py` changes as a **unified diff**
  (not their file). You clone the upstream yourself and apply these on top.

Publish only what is mine, credit the original clearly, let users obtain the
upstream under its own terms.

---

## How the translation works (the architecture)

```
 Anthropic-format client                 this layer                       your model
 (Claude Code / dobbyai-agentic)
        │  POST /v1/messages
        │  x-api-key: dk_…  or  Bearer dk_…
        ▼
   ┌─────────────────────── patched server.py ───────────────────────┐
   │  DobbyAuthMiddleware   →  sha256(dk_) lookup in SQLite           │
   │     (401 if missing / unknown / revoked)                         │
   │  LiteLLM translation   →  Anthropic Messages  ⇄  OpenAI Chat     │
   │     model-name mapping:  *sonnet* → BIG_MODEL                    │
   │                          *haiku*  → SMALL_MODEL                  │
   │     tool_use ⇄ tool_calls translated **both directions**         │
   └──────────────────────────────┬──────────────────────────────────┘
        │  OpenAI /v1/chat/completions
        ▼
   your OpenAI-compatible local model  (vLLM / llama.cpp / mlx_lm serving
   Qwen / Llama)  →  response translated back to Anthropic format
```

The client thinks it's talking to Anthropic. Nothing leaves the perimeter:
auth is a local SQLite lookup, inference is your local model, and the
Anthropic↔OpenAI (including tool-call) translation happens in-process.

---

## What's in this repo (100% mine, MIT-licensed)

| File | What it is |
|---|---|
| `auth_middleware.py` | **`DobbyAuthMiddleware`** — Starlette middleware enforcing `dk_*` tokens. SHA-256-hashed keys checked against a SQLite store; per-key revocation, `last_used_at` + `request_count` tracking. Accepts `Bearer dk_…` or `x-api-key: dk_…`. |
| `vision_server.py` | Standalone **vision-language model server** (loads a VL model; runs where a GPU is). The patched proxy *routes* image requests to it via `VISION_MODEL_BASE` — two-stage VL→Coder. |
| `create_key.py` | Stdlib-only **key management CLI** — `create` / `list` / `revoke`. Mints `dk_` + 32 hex chars, stores only the hash. |
| `server.extensions.patch` | Unified diff of my upstream `server.py` changes (~345 lines): wires the auth middleware (`ENABLE_DOBBY_AUTH`), vision routing, and on-prem model-base config. `git apply` or `patch -p1`. |

None of these exist upstream — they are my additions.

---

## Quickstart (verified end-to-end)

Assumes you have an **OpenAI-compatible model already serving** (vLLM /
llama.cpp / mlx_lm) and reachable — e.g. `http://MODEL_HOST:8013/v1`.

```bash
# 1. Upstream proxy (under its own terms) + drop in my files
git clone https://github.com/1rgs/claude-code-proxy.git
cd claude-code-proxy
cp /path/to/dobbyai-proxy-extensions/auth_middleware.py .
cp /path/to/dobbyai-proxy-extensions/vision_server.py  .
cp /path/to/dobbyai-proxy-extensions/create_key.py     .
git apply /path/to/dobbyai-proxy-extensions/server.extensions.patch
#   (or: patch -p1 < .../server.extensions.patch  — both work)

# 2. Deps (Python ≥3.10; upstream uses these)
python3 -m venv .venv && source .venv/bin/activate
pip install "fastapi[standard]" uvicorn httpx pydantic "litellm>=1.77.7" \
            python-dotenv google-auth google-cloud-aiplatform

# 3. Configure — see "Environment" below; write a .env

# 4. Run  (load .env into the process before server.py imports auth_middleware)
set -a && source .env && set +a
python server.py
```

> **The server is silent on success.** Upstream runs
> `uvicorn(... port=8082, log_level="error")`, so there is **no startup
> banner** — a quiet terminal means it is running. It logs request lines
> (`POST /v1/messages ✓ 200 OK …`), not an "Uvicorn running" line.

### Environment (`.env`)

```bash
PREFERRED_PROVIDER=openai
OPENAI_API_KEY=none                       # local model usually needs no key

# Point at YOUR local model. Set BOTH: server.py reads OPENAI_BASE_URL,
# LiteLLM reads OPENAI_API_BASE. A mismatch = silent wrong-target.
OPENAI_API_BASE=http://MODEL_HOST:8013/v1
OPENAI_BASE_URL=http://MODEL_HOST:8013/v1

# Must match an id your model server actually serves
# (curl http://MODEL_HOST:8013/v1/models). *sonnet*→BIG, *haiku*→SMALL.
BIG_MODEL=your-org/Your-Model-AWQ
SMALL_MODEL=your-org/Your-Model-AWQ

# Auth: false = open (good for first-run sanity); true = enforce dk_ tokens
ENABLE_DOBBY_AUTH=false
DOBBYAI_DB_PATH=./dobbyai.db              # read at import — set before running

# Optional: image support via a separate VL server (blank = text only)
# VISION_MODEL_BASE=http://VL_HOST:8005/v1
# VISION_MODEL=your-org/Your-VL-Model-AWQ
```

### Recommended bring-up: two phases

**Phase 1 — prove translation (auth off).** `ENABLE_DOBBY_AUTH=false`, run,
then:

```bash
curl -s -X POST http://localhost:8082/v1/messages \
  -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' \
  -d '{"model":"claude-sonnet-4-20250514","max_tokens":40,
       "messages":[{"role":"user","content":"one short sentence"}]}'
```
Expect `200` and `"model":"openai/<your model>"` — translation + your model work.

**Phase 2 — enforce auth.** Mint a key, flip the toggle, restart:

```bash
DOBBYAI_DB_PATH=./dobbyai.db python create_key.py create --name laptop   # prints dk_… ONCE
# set ENABLE_DOBBY_AUTH=true in .env, then: set -a && source .env && set +a && python server.py
```
```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8082/v1/messages -d '{}'         # 401
curl -s localhost:8082/v1/messages -H "x-api-key: dk_…" -H 'anthropic-version: 2023-06-01' \
  -H 'content-type: application/json' \
  -d '{"model":"claude-sonnet-4-20250514","max_tokens":20,"messages":[{"role":"user","content":"hi"}]}'  # 200
python create_key.py list      # request_count incremented — the live audit trail
```

---

## Use it from a Claude-style client

Point any Anthropic-Messages client at the proxy with a `dk_` key:

- **[`dobbyai-agentic`](https://github.com/cjayasur/dobbyai-agentic):**
  `AGENT_API_URL=http://localhost:8082/v1/messages`, `AGENT_API_KEY=dk_…`
- **Claude Code:** `ANTHROPIC_BASE_URL=http://localhost:8082`,
  `ANTHROPIC_API_KEY=dk_…`

The client's model name (e.g. `claude-sonnet-4-…`) is remapped to
`BIG_MODEL`/`SMALL_MODEL` by the proxy — clients need no changes.

Together with `dobbyai-agentic` this is a **fully on-prem agentic stack**:
agent + MCP tools → this proxy (local `dk_` auth) → your local model. Suitable
for regulated / sovereign deployments (finance, healthcare, government) where
data cannot reach a third-party API.

---

## Notes & limitations

- **Single key store, local file.** The proxy validates by opening the SQLite
  file directly — co-locate the store with the proxy. This is not a networked
  auth service; scale-out needs a shared/replicated store or a real auth
  broker.
- **`vision_server.py` runs where the GPU is**, not necessarily next to the
  proxy. The proxy only needs `VISION_MODEL_BASE` pointing at it.
- Reference implementation, single-tenant by design.

---

## License

The files in **this repository** (`auth_middleware.py`, `vision_server.py`,
`create_key.py`, `server.extensions.patch`) are © 2026 Charitha Jayasuriya,
released under the **MIT License** (see [LICENSE](LICENSE)).

The MIT grant covers **only** these files. The upstream
`1rgs/claude-code-proxy` is a separate work under its author's own (currently
undeclared) terms — obtain it from its source.
