# DobbyAI Proxy Extensions

Token-auth + local-vision routing that turns the open-source
[`1rgs/claude-code-proxy`](https://github.com/1rgs/claude-code-proxy) into a
**fully on-prem** Anthropic-compatible gateway — Claude-style clients talking
to self-hosted open-weight models (Qwen / Llama) with **zero external API
egress**.

---

## ⚠️ Attribution & licensing (read first)

This repository contains **only my own original code.** It is *not* a
redistribution of the upstream proxy.

- The base proxy is **[`1rgs/claude-code-proxy`](https://github.com/1rgs/claude-code-proxy)**
  ("Run Claude Code on OpenAI models"), which wires up
  [LiteLLM](https://github.com/BerriAI/litellm) for Anthropic↔OpenAI
  translation.
- At the time of writing, **the upstream repo declares no license**
  (no `LICENSE` file, no SPDX). That means it is *not* MIT/open — all rights
  are reserved to its author.
- Because of that, I deliberately **do not republish their code here.** This
  repo ships only the files I wrote, plus my `server.py` changes as a
  **unified diff** (not their file). To run the full system you clone the
  upstream proxy yourself and apply these on top.

This is a conscious licensing-diligence choice: publish only what is mine,
credit the original clearly, and let users obtain the upstream directly under
its own terms.

---

## What's in this repo (100% mine, MIT-licensed)

| File | What it is |
|---|---|
| `auth_middleware.py` | **`DobbyAuthMiddleware`** — Starlette middleware enforcing `dk_*` API tokens. SHA-256 hashed keys checked against a SQLite key store; per-key revocation, last-used + request-count tracking. Bearer *or* `x-api-key`. |
| `vision_server.py` | Local **vision-routing** server — sends image requests to a separate self-hosted vision-language model while text stays on the local coder model (two-stage VL→Coder pipeline). |
| `server.extensions.patch` | Unified diff of my changes to upstream `server.py` (≈345 changed lines): wiring the auth middleware, vision routing, and on-prem model-base configuration. Apply with `patch` / `git apply`. |

None of these files exist in the upstream repo — they are my additions.

---

## Why this matters (the design point)

Most "use Claude Code with a local model" setups still phone home for
something — auth, telemetry, or a fallback. This combination closes that gap:

- **Auth stays local** — `dk_*` tokens validated against a local SQLite store,
  no external identity provider, no callout.
- **Inference stays local** — text to a local coder model, vision to a local
  VL model, via the patched proxy.
- **Zero external egress** — nothing leaves the network perimeter. Suitable for
  regulated / sovereign deployments (finance, healthcare, government) where
  customer data cannot reach a third-party API.

The companion agent that runs against this proxy is
[`cjayasur/dobbyai-agentic`](https://github.com/cjayasur/dobbyai-agentic) —
together they form a fully on-prem agentic stack.

---

## How to use

```bash
# 1. Get the upstream proxy directly from its author (under its own terms)
git clone https://github.com/1rgs/claude-code-proxy.git
cd claude-code-proxy

# 2. Drop in my extension files
cp /path/to/this-repo/auth_middleware.py .
cp /path/to/this-repo/vision_server.py .

# 3. Apply my server.py changes
git apply /path/to/this-repo/server.extensions.patch
#   (or: patch -p1 < server.extensions.patch)

# 4. Configure (see .env.example) and run per the upstream README
```

### Environment

```
DOBBYAI_DB_PATH=./dobbyai.db          # SQLite store of hashed dk_* keys
VISION_MODEL_BASE=http://your-vision-host:8005/v1   # local VL model endpoint
# plus the upstream proxy's own env (OPENAI_BASE_URL pointed at your local model, etc.)
```

The `dobbyai.db` schema is a single `api_keys` table
(`key_hash`, `revoked`, `last_used_at`, `request_count`, …) — create and
populate it with your own hashed tokens.

---

## License

The files in **this repository** (`auth_middleware.py`, `vision_server.py`,
`server.extensions.patch`) are © 2026 Charitha Jayasuriya, released under the
**MIT License** (see [LICENSE](LICENSE)).

The MIT grant covers **only** these files. The upstream
`1rgs/claude-code-proxy` is a separate work under its author's own (currently
undeclared) terms — obtain it from its source.
