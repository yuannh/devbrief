# Security

## Local transcript data

devbrief reads Claude Code session history from `~/.claude/projects`. These JSONL transcripts may contain sensitive information including source code, file contents, API responses, and internal tool outputs from your coding sessions.

- Do not upload, share, or commit JSONL transcripts or the local SQLite DB (`~/.local/share/devbrief/sessions.db`).
- Treat transcript data with the same sensitivity as source code.

## Raw browsing is local-only

Commands such as `devbrief list`, `devbrief raw`, `devbrief view`, `devbrief estimate`, `devbrief doctor`, and `devbrief capture --hook` do not call Claude, the Anthropic SDK, or any external service. No data leaves your machine.

## AI brief generation

`devbrief brief SESSION_ID` sends a compact, truncated evidence packet to Claude, but only after:

1. You explicitly run the command.
2. devbrief displays the packet size and approximate token estimate.
3. You confirm the prompt (or pass `--yes` to bypass confirmation).

The evidence packet is a filtered and truncated summary of the session transcript. It is not a raw dump of all session data.

## API keys

- `ANTHROPIC_API_KEY` is only used for AI brief generation.
- API keys are never printed in diagnostics, logs, or doctor output.
- Do not commit API keys, `.env` files, or `~/.config/devbrief/config.toml` to version control.

## Claude Code hooks

The Claude Code hook integration must be capture-only:

```bash
devbrief capture --hook
```

Hooks that call `devbrief brief`, `devbrief digest`, `claude -p`, or `claude --print` are unsafe and will silently spend tokens on every session. `devbrief doctor` will detect and warn about unsafe hooks.

## Reporting security issues

If you find a bug that could cause:

- Unexpected automatic LLM calls or token expenditure
- Unsafe hook installation
- Accidental exposure of API keys or session data
- Any other privacy or security issue

Please report it through the repository issue tracker, or privately if the issue is sensitive.
