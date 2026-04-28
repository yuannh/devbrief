# Token Safety

devbrief is built around a strict split between free local history browsing and optional, explicit AI brief generation.

## Why devbrief moved away from automatic summarisation

An earlier version of devbrief attempted to auto-summarise every Claude Code session when the session ended, by hooking into Claude Code's Stop hook and immediately calling an LLM. This turned out to be unsafe:

- Every session — even trivial ones — would silently burn tokens.
- The hook ran without confirmation or review.
- A misconfigured hook could call the API in a loop.
- Sensitive coding session content would be sent to the API without the user explicitly deciding to do so.

devbrief now enforces a strict rule: **the hook is capture-only and must never call Claude**.

## Local-only commands

These commands read local JSONL files or SQLite data only. They never call Claude, the Anthropic SDK, `claude -p`, or `claude --print`:

| Command | What it reads |
|---|---|
| `devbrief` / `devbrief tui` | Local JSONL + SQLite |
| `devbrief list` | Local JSONL + SQLite |
| `devbrief raw SESSION_ID` | Local JSONL |
| `devbrief view SESSION_ID` | SQLite only |
| `devbrief estimate SESSION_ID` | Local JSONL (computes size, no API call) |
| `devbrief doctor` | Local filesystem and config |
| `devbrief capture --hook` | Local JSONL (metadata only) |
| `devbrief install-hook` | Local `~/.claude/settings.json` |
| `devbrief uninstall-hook` | Local `~/.claude/settings.json` |

## Token-consuming commands

`devbrief brief SESSION_ID` is the only primary token-consuming command.

Before calling Claude it:

1. Builds a compact evidence packet from the local JSONL transcript.
2. Truncates and excludes verbose content to stay within `--max-chars`.
3. Prints the packet size and approximate token estimate.
4. Asks for explicit confirmation unless `--yes` is passed.

`--yes` skips the confirmation prompt and spends tokens immediately. Use it only after you have already reviewed the estimate.

`devbrief digest SESSION_ID` is only a deprecated backwards-compatible alias for `brief`.

`devbrief report` is disabled and will not generate anything.

## Compact evidence packet

The evidence packet sent to Claude is not a raw dump of the JSONL transcript. It is built by `compactor.build_evidence_packet` and includes:

- Session metadata (project name, session ID, turn count).
- A filtered subset of assistant messages (skipping tool results, verbose outputs, large files).
- Human user turns.
- A truncated transcript if the full content exceeds `--max-chars`.

This keeps the input well within practical token budgets for most sessions.

## Hook policy

The Claude Code Stop hook must be capture-only:

```bash
devbrief capture --hook
```

`devbrief install-hook` enforces this by:

1. Removing every existing devbrief hook command from `~/.claude/settings.json` (across all event types).
2. Installing only `devbrief capture --hook` as a Stop hook.

Unsafe hook commands that must never be installed:

```bash
devbrief digest --hook
devbrief brief --hook
claude -p
claude --print
```

## How to inspect hooks

```bash
devbrief doctor
python3 -m json.tool ~/.claude/settings.json
```

`devbrief doctor` will classify your hook as `capture`, `unsafe`, `mixed`, `not-installed`, or `unreadable`, and print a warning for any unsafe state.

## How to fix unsafe hooks

Replace all devbrief hooks with the safe capture hook:

```bash
devbrief install-hook
```

Or remove all devbrief hook commands entirely:

```bash
devbrief uninstall-hook
```

## Common unsafe patterns

| Pattern | Risk |
|---|---|
| `devbrief digest --hook` in Stop hook | Calls Claude on every session end, silently burns tokens |
| `devbrief brief --hook` in Stop hook | Same risk |
| `claude -p` or `claude --print` in any hook | Calls Claude CLI in a loop, burns tokens |
| `--yes` flag on brief in a script | Skips confirmation, may spend tokens unexpectedly |
