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
- `/rename [name]` — rename the current session
- `/export <file>` — export the current session's transcript to markdown
- `/add <file>`    — attach a file's contents to context (read-only, secrets redacted)
- `/think`         — cycle boost mode: auto / always / off
- `/pattern [name]`— apply a curated prompt pattern to your next message (Fabric-style); no arg opens a picker, `/pattern off` clears it
- `/prompt`        — open a full-screen editor for a long/multi-line prompt
- `/quit`          — exit

The main chat box is a real multi-line editor (`otakuchat/widgets.py:ChatInput`,
adapted from oterm): **Enter** submits, **Shift+Enter** inserts a newline, it
auto-grows up to 8 lines, and it gets shell-style **Up/Down history recall**
(single-line only — once you're composing multiple lines Up/Down move the
cursor like a normal editor), persisted across sessions in sqlite.

The header shows the active model plus its live capability badges
(🧠 thinking / 🛠️ tools / 👁️ vision), read from Ollama's `/api/show`.

## Design

- `otakuchat/db.py` — sqlite store: sessions, messages, curated memory, perf
  stats, session compaction cache, input history
- `otakuchat/ollama_client.py` — streaming chat, model discovery, and
  `/api/show` capability detection (thinking / tools / vision) against the
  local Ollama HTTP API only — no other providers, by design
- `otakuchat/reasoning.py` — the aggregation/self-boost layer. Two engines,
  chosen automatically per model:
  - **native thinking**: when Ollama reports a model supports it
    (deepseek-r1, qwen3, gpt-oss, ...), ask for real server-side
    chain-of-thought via `think: true` — one call, streamed separately from
    the answer
  - **draft → critique → refine** (fallback for models with no native
    reasoning mode, e.g. llama3.2, qwen2.5-coder): run the same model
    against itself three times to simulate the same effect

  Whichever engine runs, a **reasoning strategy** is auto-picked per prompt
  from `otakuchat/strategies.py` (`data/strategies/` ported from Fabric) —
  chain-of-thought, tree-of-thought, self-refine, least-to-most,
  chain-of-draft, self-consistency, atom-of-thought — and its short
  instruction is folded into the boosted pass as extra system guidance, so
  a code-fix prompt gets self-refine-flavored guidance while a "compare
  these approaches" prompt gets tree-of-thought instead of the app always
  reasoning the same way regardless of what kind of ask it is. Not
  user-selectable by design — the heuristic (`strategies.pick_strategy`)
  reads prompt shape (code hints, design/compare language, multi-step
  phrasing, question density) and just picks.

  Also strips stray inline `<think>`/`<reasoning>` tags some model
  chat-templates leak into `content` instead of using the API's `thinking`
  field, so the visible answer is always just the answer. Boosting is
  adaptive (`auto` mode) and self-tunes its complexity threshold from a
  rolling perf/feedback signal per model — corrections from the user nudge
  it to boost more; wasted boosts nudge it to boost less.
- `otakuchat/patterns.py` — a curated ~26-pattern library (`otakuchat/patterns/`,
  ported from Fabric's `data/patterns/`, which ships 256 — this is a
  hand-picked general-use subset: summarize, extract_wisdom, review_code,
  explain_code, translate, create_git_diff_commit, ...). `/pattern` applies
  one to your very next message only (single-use, consumed at the point the
  turn is built in `OtakuChat.build_messages`) as an extra trailing system
  message, so it never touches the cached/byte-stable base system prompt.
- `otakuchat/context.py` — trajectory compaction. Once a session's tracked
  size crosses a token budget, older turns (outside a protected tail window
  that always ends on an assistant turn) get folded into one cached summary
  instead of resending an ever-growing transcript every turn. Recurses if a
  single summarization pass still doesn't fit.
- `otakuchat/memory.py` — periodic, budgeted self-curation of durable facts
  into persistent memory (modeled on Hermes's memory tool, but the model
  never runs a command to do it — the app makes a hidden side-call, then
  validates/writes the result itself). Below `RELEVANCE_THRESHOLD` (20)
  facts, every fact is always included, unranked, so the system prompt
  stays byte-stable for KV-cache reuse. Above it, `db.relevant_facts`
  (Jaccard token-overlap scoring, adapted from hermes-agent's holographic
  memory provider with its numpy/HRR machinery stripped out) ranks facts
  against the current user message and only the top N make it into the
  prompt — trading cache-prefix stability for relevance once the store is
  big enough that dumping everything in stops making sense.
- `otakuchat/ollama_client.py` / `otakuchat/config.py` GENERATION section —
  Ollama `/api/chat` generation options (temperature, top_p, max_tokens,
  seed), ported from oterm's per-chat parameter modal but flattened into
  `config.ini` (edited via the existing `/config` command) rather than a
  new modal/command. Any field left blank is omitted from the request
  entirely so Ollama falls back to the model's own default instead of the
  app silently guessing one. The internal draft->critique review pass
  deliberately does NOT inherit these — it's an unseen quality check, not
  a visible answer, and shouldn't inherit e.g. a high creative-writing
  temperature meant for the final response.
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
  protected trajectory compression, app-driven self-curating memory,
  secret redaction (trimmed down from `agent/redact.py`) applied to `/add`
  file ingestion and memory curation so a pasted API key never becomes a
  permanent fact or a persisted attachment.
- **oterm**: Ollama `/api/show` capability detection + live badges in the
  header, native `think` streaming support, `/rename` and `/export`
  session commands, the Enter-submits/Shift+Enter-newline auto-growing
  chat box (`ChatInput`, adapted from `PostableTextArea`).
- **aider**: inline `<think>` tag stripping for models that leak reasoning
  into `content`, size/budget-aware recursive context compaction ending on
  clean turn boundaries, persistent shell-style input history recall.
- **Fabric** (danielmiessler/fabric): a curated subset of `data/patterns/`
  (256 → ~26 general-use ones) as `otakuchat/patterns/` behind `/pattern`,
  and all of `data/strategies/` (9 reasoning-strategy JSONs — CoT, ToT,
  self-refine, LTM, CoD, self-consistency, AoT, reflexion, standard) as
  `otakuchat/strategies.py`, auto-picked per prompt inside the existing
  boost layer instead of surfacing as its own command.
- **hermes-agent** (holographic memory plugin, `plugins/memory/holographic/`):
  Jaccard token-overlap relevance scoring for curated facts (`db.relevant_facts`)
  — the numpy-based HRR compositional-retrieval algebra was left out entirely
  (too heavy a dependency for this app's scope), keeping only the
  lightweight keyword-overlap ranking layer that needed no new dependency.
- **oterm** (`app/chat_edit.py`): per-chat Ollama generation parameters
  (temperature/top_p/max_tokens/seed) — ported as a `GENERATION` section in
  `config.ini` rather than oterm's dedicated modal screen, since editing
  config.ini via the existing `/config` command already covers it without
  a new command.
