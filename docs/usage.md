# Usage

## Installation

```bash
git clone <repo-url>
cd devbrief
pip install -e .
devbrief doctor
```

Raw history browsing works immediately after install. No API key, no hook, and no `devbrief setup` are required for local browsing.

## Browsing the current project's history

Navigate to any project and run:

```bash
cd /path/to/your/project
devbrief list
```

`devbrief list` automatically scopes to the current home project (the git root or Claude Code launch cwd). Use `--all` to see sessions across all projects:

```bash
devbrief list --all
devbrief list --briefed       # only sessions with stored AI briefs
devbrief list --briefed --all  # briefed sessions across all projects
```

## Viewing raw previews

Raw preview is local-only and never uses tokens:

```bash
devbrief raw SESSION_ID
```

The default view shows:

- Session metadata (project, cwd, times, turn count).
- **Session Outcome**: a locally inferred status such as `completed`, `usage_limited`, `blocked`, `incomplete`, or `unknown`.
- Human requests (truncated).
- A short "what happened locally" summary.
- Files touched or inspected.
- Final assistant response.

Useful options:

```bash
devbrief raw SESSION_ID --full           # longer content, commands, errors, tool calls
devbrief raw SESSION_ID --json           # structured JSON output for scripting/export
devbrief raw SESSION_ID --show-internal  # reveal internal model prompts (usually hidden)
devbrief raw SESSION_ID --commands       # show commands in default mode
devbrief raw SESSION_ID --errors         # show errors in default mode
devbrief raw SESSION_ID --tools          # show tool calls in default mode
```

## Understanding session outcome

The session outcome is inferred locally from the transcript with no LLM call:

| Status | Meaning |
|---|---|
| `completed` | Session finished naturally with a final response |
| `usage_limited` | Claude Code stopped because the usage limit was reached |
| `blocked` | Session stopped due to an error or tool failure |
| `incomplete` | Session ended without a clear final response |
| `unknown` | Outcome could not be determined from the transcript |

Confidence and reasoning signals are shown alongside the status.

## Checking the token estimate before briefing

Before generating an AI brief, check how many tokens it would consume:

```bash
devbrief estimate SESSION_ID
```

This shows raw transcript chars, compact evidence packet chars, approximate input tokens, and whether the transcript was truncated. No API call is made.

## Generating a brief safely

AI briefs are optional, manual, and confirmation-gated:

```bash
devbrief brief SESSION_ID
```

devbrief will:

1. Build a compact evidence packet.
2. Print the token estimate.
3. Ask for explicit confirmation before calling Claude.
4. Store the result in the local SQLite database.

To view a stored brief later without re-generating it:

```bash
devbrief view SESSION_ID
```

`devbrief digest SESSION_ID` is a deprecated alias that works the same way but prints a warning.

## Using the interactive session browser

Open the keyboard-first split-pane terminal UI:

```bash
devbrief
devbrief tui
devbrief --all       # all projects
devbrief tui --all
```

The left pane shows the session list. The right pane shows the selected session:

- If the session has a stored AI brief, the brief is shown by default.
- If the session has no brief yet, a local raw preview is shown instead.

Key bindings:

| Key | Action |
|---|---|
| `j` / `k` or `↑` / `↓` | Move selection |
| `Enter` | Open/focus detail pane |
| `v` | Toggle between raw preview and AI brief |
| `d` or `b` | Generate AI brief (shows estimate and asks confirmation) |
| `r` | Refresh session list |
| `a` | Toggle current project / all projects |
| `?` | Show help |
| `q` | Quit |

Opening and navigating the browser never calls Claude.

## Installing or uninstalling the hook

The optional hook captures metadata only and does not call Claude:

```bash
devbrief install-hook
```

This installs `devbrief capture --hook` as a Claude Code Stop hook. It records lightweight session metadata (project name, turn count, file path) when each Claude Code session ends.

Remove all devbrief hook commands:

```bash
devbrief uninstall-hook
```

## Troubleshooting common issues

**`devbrief list` shows no sessions for the current project**

- Run `devbrief doctor` to confirm that `~/.claude/projects` exists and contains JSONL files.
- Make sure you are running `devbrief list` from inside the project directory (or a subdirectory of it).
- Run `devbrief list --all` to see if sessions appear under a different project name.

**`devbrief raw` says "JSONL transcript not found"**

- The session may only exist in SQLite (e.g., the JSONL file was deleted). Run `devbrief view SESSION_ID` instead.
- Check that the session ID prefix is unambiguous. Use the full ID from `devbrief list` output.

**`devbrief doctor` shows an unsafe hook**

- Run `devbrief install-hook` to replace all devbrief hooks with the safe capture-only hook.
- Or run `devbrief uninstall-hook` to remove all devbrief hooks entirely.
- Then run `python3 -m json.tool ~/.claude/settings.json` to confirm the change.

**`devbrief brief` says "No API key and claude CLI not found"**

- Raw browsing works without any API key or Claude CLI.
- AI briefs require either a Claude CLI in PATH or `ANTHROPIC_API_KEY` set in the environment or config.
- Run `devbrief setup` to configure an API key if needed.
