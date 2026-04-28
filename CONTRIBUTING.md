# Contributing

Thanks for helping improve devbrief.

## Core rules

The token-safety model is the foundation of the project. Every contribution must preserve it:

1. **No automatic LLM calls.** Raw browsing must always be local-only. `list`, `raw`, `view`, `estimate`, `doctor`, and the hook must never call Claude.
2. **Hook must stay capture-only.** The Claude Code hook command must remain `devbrief capture --hook`. It must never call `devbrief brief`, `devbrief digest`, `claude -p`, `claude --print`, or the Anthropic SDK.
3. **Token-consuming commands must be explicit and confirmation-gated.** `devbrief brief SESSION_ID` is the only command that may call Claude. It must always show an estimate and ask for confirmation before making any API call. `--yes` is acceptable as an explicit opt-in but must not be the default.
4. **Avoid committing local data.** Do not commit personal Claude Code JSONL transcripts, the local SQLite DB, `.env` files, API keys, or `~/.config/devbrief/config.toml`.

## Safe test commands

Before opening a PR, run:

```bash
python -m compileall src
devbrief --help
devbrief doctor
devbrief list
devbrief raw SESSION_ID   # use a known local session ID
devbrief estimate SESSION_ID
devbrief brief --help
devbrief tui --help
```

Do not run:

- `devbrief brief SESSION_ID`
- `devbrief digest SESSION_ID`
- `claude -p`
- `claude --print`
- Any command that calls the Anthropic SDK

## Code style

- Python 3.11+.
- All CLI commands use Click. Output uses Rich.
- The TUI uses Textual.
- Storage is SQLite via the `storage` module. Prefer `upsert` and `find_by_prefix`.
- Local raw preview and outcome detection live in `compactor.py`. Keep them free of LLM calls.

## What to check if you touch specific areas

| Area | What to verify |
|---|---|
| `cli.py` — `list` / `raw` / `view` | No LLM call, project scoping works |
| `cli.py` — `brief` / `digest` | Estimate printed, confirmation required |
| `cli.py` — `capture` | No LLM call, no summarizer import |
| `cli.py` — `install-hook` | Only installs `devbrief capture --hook` |
| `tui.py` | No LLM call on load/navigate, brief only on explicit key press with confirmation |
| `compactor.py` | No LLM calls, all logic is deterministic |
| `storage.py` | Migration is safe and does not break existing data |
