# OtakuChat

A chat-only TUI harness for local Ollama models that curates its own
context instead of trusting a raw, ever-growing transcript. No shell tool
is ever exposed to the model. It can answer, contribute (indirectly,
through hidden app-side side-calls) candidates to its own curated stores,
and — the one deliberate exception, see "FaaS hands" below — request a
call to a function YOU wrote, always gated behind an explicit Yes/No
confirmation. Around all that, the app tracks its own performance,
self-tunes how hard it "thinks" per model and per prompt, and compacts its
own conversation history to stay inside a model's context window.

## Run

Straight from GitHub, no clone needed:

```bash
uv tool install --force git+https://github.com/wolfercarter-cpu/otaku-chat
otakuchat
```

`uv` isn't required — this is a standard `pyproject.toml` package
(hatchling build backend, one dependency), so `pipx` works the same way:

```bash
pipx install git+https://github.com/wolfercarter-cpu/otaku-chat
otakuchat
```

Or from a local clone:

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
- `/menu`          — pick a command from a list instead of typing one
- `/model [name]`  — list/switch the active Ollama model
- `/memory`        — open the curated memory file in the built-in Slate editor
- `/facts`         — open the curated topic→URL bookmark file (from web search grounding) in the built-in Slate editor
- `/snippets`      — open the curated code-snippet library in the built-in Slate editor
- `/vault`         — browse, remove, seed, or wipe imported vault content (fuzzy-searchable list)
- `/import [url]`  — import a git repo (.git), .zip, or single file into the vault; no arg opens a URL prompt
- `/functions`     — browse and manually fire user-defined functions ("FaaS hands" — see below); every call needs your Yes/No, no exceptions
- `/config`        — open config.ini (model, api url, boost mode) in the built-in Slate editor
- `/new`           — start a fresh session
- `/sessions`      — browse and resume past sessions
- `/rename [name]` — rename the current session; no arg opens a text prompt pre-filled with the current title
- `/export [file]` — export the current session's transcript to markdown; no arg opens a text prompt pre-filled with a default filename
- `/add [file]`    — attach a file's contents to context (read-only, secrets redacted); no arg opens a file browser; image files (.png/.jpg/.jpeg/.gif/.webp/.bmp) are attached as a vision message instead
- `/think`         — cycle boost mode: auto / always / off
- `/pattern [name]`— apply a curated prompt pattern to your next message (Fabric-style); no arg opens a picker, `/pattern off` clears it
- `/prompt`        — open a full-screen editor for a long/multi-line prompt
- `/quit`          — exit

The main chat box is a real multi-line editor (`otakuchat/widgets.py:ChatInput`):
**Enter** submits, **Shift+Enter** inserts a newline, it auto-grows up to 8
lines, and it gets shell-style **Up/Down history recall** (single-line
only — once you're composing multiple lines Up/Down move the cursor like
a normal editor), persisted across sessions in sqlite.

The header shows the active model plus its live capability badges
(🧠 thinking / 🛠️ tools / 👁️ vision), read from Ollama's `/api/show`.

`/memory`, `/facts`, `/snippets`, and `/config` all open the same in-app
Slate editor (`otakuchat/editor.py`, `otakuchat/autocomplete.py`) —
ported from Otakumafia's gmag file-manager project — instead of shelling
out to `$EDITOR` and suspending the TUI. Syntax-highlighted (language
auto-detected from the file's extension), auto-closing brackets/quotes,
a "leap" search box (Ctrl+L, Tab completes a word straight from the open
document, Enter jumps to it), and a fuzzy word-completion dropdown fed by
every unique word already in the document. Ctrl+S saves, Esc closes; for
`/config` specifically, closing it also reloads the live model/API URL
without restarting the app, same as the old subprocess flow did.

## Self-curation

OtakuChat keeps three stores that the model never writes to directly —
the app always makes the hidden call, validates the result, and writes it
itself. Each is mirrored to a plain markdown file so it stays reviewable
and hand-editable:

- **Memory** (`MEMORY.md`, `/memory`) — durable facts about you and the
  assistant (preferences, corrections, identity/environment details),
  curated periodically from recent turns.
- **Facts** (`FACTS.md`, `/facts`) — a topic → URL bookmark file. No
  extra LLM call needed: whenever a web search actually returns results
  (see below), the query becomes the topic and the top URLs get filed
  away, deduped.
- **Snippets** (`SNIPPETS.md`, `/snippets`) — a code-snippet library,
  curated the same way memory is. Re-saving an existing title updates it
  in place instead of piling up near-duplicates.

All three share one retrieval mechanism (`db._rank_by_relevance`, Jaccard
token overlap) but tune it differently on purpose: memory falls back to
the most recent facts when nothing scores above zero, because it's meant
to always be present-ish and small enough that a stable prefix matters
more than precision. Facts and snippets never fall back — an unrelated
query surfaces nothing rather than an arbitrary bookmark or snippet, so
neither store gets replayed into a turn it has nothing to do with just
because it isn't empty.

**Web search** feeds facts and grounds answers directly: set
`brave_api_key` under `[SEARCH]` in `config.ini` (`/config`) to a Brave
Web Search API key and every turn automatically gets grounded with fresh
web results for your latest message — no key means it's a complete no-op,
zero network calls. It's not a model-invoked tool (this app exposes no
tools to the model at all); the app itself always runs the search and
folds the results into a hidden system message for that turn, the same
way `/pattern` injects its instruction. A failed search (bad key, network
down, rate limit) just means the turn proceeds ungrounded rather than
breaking the chat.

Search grounding is more than a snippet list: once Brave returns
candidate URLs, `otakuchat/extract.py` fetches and reads the top few
(`[EXTRACT] top_n` in `config.ini`, default 2) and folds their actual
page text into the same hidden system message — the model reasons over
real page content, not a two-line description. Extraction has two tiers:

- **stdlib tier** (always on, zero new dependencies): `urllib` + a small
  `html.parser.HTMLParser` subclass strips script/style/nav/footer/aside
  and returns clean paragraph text. Handles the overwhelming majority of
  docs/blogs/wikis/news pages.
- **browser tier** (opt-in): for JS-rendered pages the stdlib tier can't
  get real text out of, a headless-Chromium retry via Playwright kicks in
  — but only when both `EXTRACT.use_browser_fallback = true` in
  `config.ini` **and** the `browser` extra is installed
  (`uv sync --extra browser` / `pip install otakuchat[browser]`).
  `playwright` is never imported at all otherwise — same lazy-check
  pattern aider's `Scraper.has_playwright()` uses, ported and trimmed to
  the read-only path (no pandoc, no CLI entrypoint).

Every extracted page is redacted (`redact.py`) before it can enter a
system prompt or the cache, and cached in sqlite for
`[EXTRACT] cache_ttl_hours` (default 24h) so a repeat question about the
same page doesn't refetch it. A failed extraction for one URL degrades
that result back to title+snippet rather than dropping it or breaking the
turn — same best-effort posture as the search call itself.

Extraction across the `top_n` results runs **concurrently**
(`concurrent.futures.ThreadPoolExecutor`, stdlib — no new dependency,
matches the app's existing worker-thread model) rather than one URL at a
time, so N results cost roughly one fetch's wall time instead of N times
that.

Optionally, each extracted excerpt can also be **summarized** by the
active model before grounding a turn (`[EXTRACT] use_summarize = true` in
`config.ini`, off by default) — one extra fast, non-streaming Ollama call
per result, condensing it down to what's relevant to your actual question
(`[EXTRACT] summarize_max_chars`, default 600). This trades one more
model round-trip for a smaller, less noisy excerpt; summarize calls also
run concurrently across results, and any failure just falls back to the
raw excerpt rather than losing content.

## Design

**Self-curation subsystem** — memory, facts, snippets, and the web search
that feeds facts, all built on one shared foundation:

- `otakuchat/db.py` — sqlite store: sessions, messages, curated memory,
  topic→URL bookmarks, code snippets, extracted-page-text cache, perf
  stats, session compaction cache, input history. `_rank_by_relevance` is
  the one Jaccard token-overlap ranker behind all three relevance-gated
  stores.
- `otakuchat/curation.py` — the side-call/JSON-extraction plumbing shared
  by memory's and snippets' periodic self-curation passes (build a
  transcript, ask the model, pull a JSON array out of whatever it said),
  so each store module only owns what's actually specific to it.
- `otakuchat/threats.py` — a lightweight prompt-injection/exfiltration
  pattern scanner (trimmed down from Hermes-agent's `threat_patterns.py`
  to the patterns actually relevant here — no shell/skills/C2 surface to
  defend, just classic instruction-override injection, system-prompt
  leak attempts, and secret-exfil phrasing). Every candidate fact, topic
  link, and snippet is redacted *then* scanned before it's written —
  redaction runs first so a real leaked secret gets masked-and-kept
  instead of the whole entry being rejected outright, since a masked
  secret never matches an injection pattern but a genuine payload still
  does. A flagged entry is silently dropped, never partially stored: a
  curated fact/snippet/bookmark replays into every future system prompt
  forever, so nothing enters that store on anything less than a clean
  scan. Not a security boundary against a determined attacker (same
  caveat as `redact.py`) — a best-effort net so a poisoned conversation
  or malicious search result can't quietly become permanent, always-
  injected context.
- `otakuchat/fileio.py` — `locked_atomic_write()`, shared by every mirror
  write in this subsystem (`MEMORY.md`, `FACTS.md`, `SNIPPETS.md`,
  `config.ini`, and Slate's own saves to any of those paths). A sidecar
  `.lock` file (fcntl/msvcrt) serializes concurrent writers — the
  periodic self-curation pass runs on a background thread
  (`app.py`'s `@work(thread=True)`) and can otherwise land at the same
  moment as a hand-edit through the Slate editor — and the write itself
  goes to a temp file + `os.replace()` so a crash mid-write can't leave a
  torn file on disk. Adapted from Hermes-agent's `memory_tool.py` file-
  locking pattern.
- `otakuchat/memory.py` — periodic, budgeted self-curation of durable
  facts. Below `RELEVANCE_THRESHOLD` (20) facts, every fact is always
  included, unranked, so the system prompt stays byte-stable for KV-cache
  reuse. Above it, ranking by relevance to the current message takes
  over — trading cache-prefix stability for relevance once the store is
  big enough that dumping everything in stops making sense. The prune
  limit (`[MEMORY] max_facts_stored`, default 200) is a config key, not
  the hardcoded literal it used to be.
- `otakuchat/facts.py` — topic→URL curation fed directly by web search
  results, strictly relevance-gated retrieval (see "Self-curation"
  above). A search result's description is the least-trusted text in
  this app (raw third-party web content) — scanned before it's ever
  filed away as a bookmark.
- `otakuchat/snippets.py` — self-curating code-snippet library, same
  hidden-side-call pattern as `memory.py`, strictly relevance-gated
  retrieval
- `otakuchat/vault.py` — a RAG-style import directory ported from
  Otakumafia's hutuio project's smart git/zip/file downloader
  (`inspiration/hutuio/importer.py`), extended with a chunked-retrieval
  layer on top so anything dropped into the vault auto-grounds relevant
  turns the same way memory/facts/snippets do. Two directories:
  `vault/` (the dump — text/code/markdown you import via `/import` or
  drop in by hand) and `seed/` (a whitelist — anything copied there via
  "Add to Seed" survives `/vault`'s Wipe button, which otherwise clears
  `vault/` completely). Text-ish extensions get chunked, redacted
  (`redact_secrets`), and indexed for retrieval; binaries are kept on
  disk (so a cloned repo's assets still exist) but skipped from
  retrieval since there's nothing useful to embed. `/import` accepts a
  `.git` URL (shells out to `git clone --depth 1`), a `.zip` URL
  (extracted in-memory, zip-slip guarded), or any other URL (saved as a
  single file) — same three-way dispatch as hutuio's importer.
- `otakuchat/vault_ui.py` — `/vault`'s manager screen: the same
  search-as-you-type fuzzy list pattern as `pickers.py`, plus
  Remove/Add to Seed/Wipe Vault/Reindex/Import URL buttons and `r`/`w`
  key shortcuts. `VaultImportPrompt` is the small modal `/import` (no
  arg) pops for a URL.
- `otakuchat/search.py` — optional Brave Web Search client, wired into
  `OtakuChat.maybe_web_search` (`app.py`); inert unless `config.ini`'s
  `[SEARCH] brave_api_key` is set
- `otakuchat/extract.py` — turns search's title+snippet results into real
  page text: stdlib `urllib` + `HTMLParser` tier always on, opt-in
  headless-Chromium (Playwright) tier for JS-heavy pages, sqlite-cached,
  redacted before it can enter a system prompt

**Everything else:**

- `otakuchat/editor.py` + `otakuchat/autocomplete.py` — Slate, the in-app
  editor for `/memory`, `/facts`, `/snippets`, `/config` (ported from
  Otakumafia's gmag project). Syntax highlighting, auto-closing
  brackets/quotes, a leap-search input, and a document-word-completion
  dropdown, all inside the same Textual process — replaces shelling out
  to `$EDITOR` + `App.suspend()`.
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
  from `otakuchat/strategies.py` — chain-of-thought, tree-of-thought,
  self-refine, least-to-most, chain-of-draft, self-consistency,
  atom-of-thought — and its short instruction is folded into the boosted
  pass as extra system guidance, so a code-fix prompt gets
  self-refine-flavored guidance while a "compare these approaches" prompt
  gets tree-of-thought instead of the app always reasoning the same way
  regardless of what kind of ask it is. Not user-selectable by design —
  the heuristic (`strategies.pick_strategy`) reads prompt shape (code
  hints, design/compare language, multi-step phrasing, question density)
  and just picks.

  Also strips stray inline `<think>`/`<reasoning>` tags some model
  chat-templates leak into `content` instead of using the API's `thinking`
  field, so the visible answer is always just the answer. Boosting is
  adaptive (`auto` mode) and self-tunes its complexity threshold from a
  rolling perf/feedback signal per model — corrections from the user nudge
  it to boost more; wasted boosts nudge it to boost less.
- `otakuchat/patterns.py` — a curated ~26-pattern library
  (`otakuchat/patterns/`, a hand-picked general-use subset: summarize,
  extract_wisdom, review_code, explain_code, translate,
  create_git_diff_commit, ...). `/pattern` applies one to your very next
  message only (single-use, consumed at the point the turn is built in
  `OtakuChat.build_messages`) as an extra trailing system message, so it
  never touches the cached/byte-stable base system prompt.
- `otakuchat/context.py` — trajectory compaction. Once a session's tracked
  size crosses a token budget, older turns (outside a protected tail window
  that always ends on an assistant turn) get folded into one cached summary
  instead of resending an ever-growing transcript every turn. Recurses if a
  single summarization pass still doesn't fit.
- `otakuchat/ollama_client.py` / `otakuchat/config.py` GENERATION section —
  Ollama `/api/chat` generation options (temperature, top_p, max_tokens,
  seed), flattened into `config.ini` (edited via the existing `/config`
  command) rather than a dedicated modal/command. Any field left blank is
  omitted from the request entirely so Ollama falls back to the model's
  own default instead of the app silently guessing one. The internal
  draft→critique review pass deliberately does NOT inherit these — it's
  an unseen quality check, not a visible answer, and shouldn't inherit
  e.g. a high creative-writing temperature meant for the final response.
- `otakuchat/app.py` — Textual TUI. Caches the assembled system prompt and
  only rebuilds it when curated memory actually changes, keeping the prefix
  byte-stable across turns so Ollama can reuse its KV cache instead of
  reprocessing the system+memory block on every single message.

## Tests

```bash
uv run pytest
```

106 tests, all fully isolated from your real `~/.config/otakuchat` and
`~/.local/share/otakuchat` (see `tests/conftest.py`'s `isolated_env`
fixture, which redirects every on-disk path into a throwaway `tmp_path`
before each test) — safe to run repeatedly without touching real
sessions, config, or curated stores. No live Ollama server or network
access required: `test_ollama_client.py`/`test_search.py` mock
`urllib.request.urlopen` directly.

Covers, module for module: the strict relevance-gating in `db.py`
(facts/snippets never leak into an unrelated turn) and its stopword list;
`patterns.get_pattern()`'s path-traversal guard; every numeric
`config.get_*()` falling back to its documented default instead of
raising on a hand-edited `config.ini`; `ollama_client.py`/`search.py`
wrapping a mid-response disconnect into a clean error instead of leaking
a raw exception; the `ChatInput`/`HistoryInput` typing and resize
regressions; and full end-to-end flows through the real `OtakuChat` app
via Textual's `run_test()` pilot harness (`/menu`, `/rename`, `/export`,
`/sessions` including the delete-confirm flow, `/pattern`).

## Prior art

Several design choices here were informed by studying `hermes-agent`
(prompt-cache stability, self-curating memory, secret redaction),
`oterm` (Ollama capability badges, the auto-growing chat box, per-chat
generation parameters), `aider` (think-tag stripping, budget-aware
context compaction), and Fabric (`danielmiessler/fabric`)'s pattern and
reasoning-strategy libraries, trimmed to a curated subset behind
`/pattern`. None of those projects are dependencies or vendored code —
just prior art that shaped specific mechanisms above.
