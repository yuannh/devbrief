# Changelog

All notable changes to devbrief are documented here.

## Unreleased — initial release

### Core product

- **Local history listing**: `devbrief list` shows Claude Code sessions for the current project, scoped by Claude Code launch cwd / git root. `--all` shows all projects.
- **Raw preview**: `devbrief raw SESSION_ID` shows a readable local preview of any session with no LLM call. Options: `--full`, `--json`, `--show-internal`, `--commands`, `--errors`, `--tools`.
- **Session outcome detection**: Local heuristics infer session outcome (`completed`, `usage_limited`, `blocked`, `incomplete`, `unknown`) from the JSONL transcript without any LLM call.
- **Token estimate**: `devbrief estimate SESSION_ID` shows compact packet size and approximate token count before briefing, without making any API call.
- **AI brief**: `devbrief brief SESSION_ID` generates an optional AI brief for one session. It shows the token estimate and asks for confirmation before calling Claude. Result is stored in local SQLite.
- **Interactive session browser**: `devbrief` / `devbrief tui` opens a keyboard-first split-pane terminal UI. Left pane: session list with outcome and metadata. Right pane: raw preview or stored AI brief. Full keybinding support including `v` to toggle, `d`/`b` to brief, `a` to toggle project scope, `?` for help.
- **Capture-only hook**: `devbrief capture --hook` is a Claude Code Stop hook entrypoint that captures lightweight session metadata (project, turn count, file path) without calling Claude.
- **Token safety docs**: `docs/token-safety.md` explains the two-layer model, hook policy, and unsafe patterns to avoid.

### Safety

- `devbrief digest SESSION_ID` exists only as a deprecated backwards-compatible alias for `devbrief brief`. It prints a deprecation warning.
- `devbrief report` is a disabled compatibility stub.
- `devbrief install-hook` always replaces old devbrief hook commands with the safe capture hook.
- `devbrief doctor` detects and warns about unsafe hooks.
