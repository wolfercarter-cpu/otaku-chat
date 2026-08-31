# OtakuChat

A Hermes-inspired TUI chat harness for local Ollama models. Chat-only —
no shell tool is ever exposed to the model. It curates its own memory,
tracks its own performance, and self-tunes how hard it "thinks" per model
and per prompt.

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

- `/model [name]`   — list/switch the active Ollama model
- `/memory`         — open the curated memory file in your editor
- `/new`            — start a fresh session
- `/sessions`       — browse and resume past sessions
- `/add <file>`     — attach a file's contents to context (read-only)
- `/think`          — cycle boost mode: auto / always / off
- `/quit`           — exit

## Design

- `otakuchat/db.py` — sqlite store: sessions, messages, curated memory, perf stats
- `otakuchat/ollama_client.py` — streaming chat + model discovery against the local Ollama HTTP API
- `otakuchat/reasoning.py` — adaptive draft -> self-critique -> refine booster that self-tunes
  from a rolling performance/feedback signal (per model), so cheap local models get smarter
  on the prompts that need it without slowing down every single turn
- `otakuchat/memory.py` — periodic, budgeted self-curation of durable facts into persistent memory
  (modeled on Hermes memory tool, but the model never runs a command to do it — the app does the write)
- `otakuchat/app.py` — Textual TUI
