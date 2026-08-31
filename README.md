# OtakuChat

A Hermes-inspired TUI chat harness for local Ollama models. Chat-only —
no shell tool is ever exposed to the model. It curates its own memory,
tracks its own performance, self-tunes how hard it "thinks" per model and
per prompt, and compacts its own conversation history to stay inside a
model's context window.

## Run

```bash
uv tool install --force .
otakuchat
```

or during dev:

```bash
uv run otakuchat
```

## Slash commands

- `/help`          — show all commands with descriptions
- `/model [name]`  — list/switch the active Ollama model
- `/memory`        — open the curated memory file in your editor
- `/config`        — open config.ini (model, api url, boost mode) in your editor
- `/new`           — start a fresh session
- `/sessions`      — browse and resume past sessions
- `/add <file>`    — attach a file's contents to context (read-only)
- `/think`         — cycle boost mode: auto / always / off
- `/prompt`        — open a full-screen editor for a long/multi-line prompt
- `/quit`          — exit

Plain input also gets shell-style **Up/Down history recall**, persisted
across sessions in sqlite (`otakuchat/widgets.py:HistoryInput`).

## Design

- `otakuchat/db.py` — sqlite store: sessions, messages, curated memory, perf
  stats, session compaction cache, input history
- `otakuchat/ollama_client.py` — streaming chat, model discovery, and
  `/api/show` capability detection (thinking / tools / vision) against the
  local Ollama HTTP API only — no other providers, by design
- `otakuchat/reasoning.py` — the aggregation/self-boost layer. Two strategies,
  chosen automatically per model:
  - **native thinking**: when Ollama reports a model supports it
    (deepseek-r1, qwen3, gpt-oss, ...), ask for real server-side
    chain-of-thought via `think: true` — one call, streamed separately from
    the answer
  - **draft → critique → refine** (fallback for models with no native
    reasoning mode, e.g. llama3.2, qwen2.5-coder): run the same model
    against itself three times to simulate the same effect

  Also strips stray inline `<think>`/`<reasoning>` tags some model
  chat-templates leak into `content` instead of using the API's `thinking`
  field, so the visible answer is always just the answer. Boosting is
  adaptive (`auto` mode) and self-tunes its complexity threshold from a
  rolling perf/feedback signal per model — corrections from the user nudge
  it to boost more; wasted boosts nudge it to boost less.
- `otakuchat/context.py` — trajectory compaction. Once a session's tracked
  size crosses a token budget, older turns (outside a protected tail window
  that always ends on an assistant turn) get folded into one cached summary
  instead of resending an ever-growing transcript every turn. Recurses if a
  single summarization pass still doesn't fit.
- `otakuchat/memory.py` — periodic, budgeted self-curation of durable facts
  into persistent memory (modeled on Hermes's memory tool, but the model
  never runs a command to do it — the app makes a hidden side-call, then
  validates/writes the result itself)
- `otakuchat/app.py` — Textual TUI. Caches the assembled system prompt and
  only rebuilds it when curated memory actually changes, keeping the prefix
  byte-stable across turns so Ollama can reuse its KV cache instead of
  reprocessing the system+memory block on every single message.

## DNA sources

Built by studying `ispiration/hermes-agent`, `ispiration/oterm`, and
`ispiration/aider` (gitignored local reference clones, not part of this
repo) and porting the parts that fit a chat-only, no-shell, local-Ollama
harness:

- **Hermes**: prompt-cache stability as a first-class constraint, head/tail-
  protected trajectory compression, app-driven self-curating memory.
- **oterm**: Ollama `/api/show` capability detection, native `think`
  streaming support.
- **aider**: inline `<think>` tag stripping for models that leak reasoning
  into `content`, size/budget-aware recursive context compaction ending on
  clean turn boundaries, persistent shell-style input history recall.
