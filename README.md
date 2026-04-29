# devbrief

Local Claude Code terminal history browser with optional AI briefs.

## What devbrief does

- Lists local Claude Code sessions scoped to the current project.
- Shows raw session previews without any LLM calls.
- Infers local session outcome: `completed`, `usage_limited`, `blocked`, `incomplete`, `unknown`.
- Shows files touched, commands run, final assistant response, and human requests.
- Provides a keyboard-first split-pane terminal UI for browsing sessions interactively.
- Optionally generates one AI brief for one selected session after showing a token estimate and asking for confirmation.

## Why it exists

Claude Code stores every terminal session as a local JSONL transcript under `~/.claude/projects`, but there is no polished way to browse them. Developers often want to revisit what happened in a previous coding session — what files were touched, what commands were run, how the session ended — without re-reading raw JSON.

A naive solution would auto-summarise every session using an AI model, but that burns tokens silently for every session you open. devbrief solves this properly: raw history browsing is always local and zero-token; AI briefs are optional, manual, and confirmation-gated.

## What devbrief is not

- Not installed inside Claude Code.
- Not a Claude Code plugin.
- Not an automatic AI summariser.
- Not a background agent that spends tokens.
- Not a tool that uploads your history without explicit action.

## Interface preview

These previews are text mockups with anonymised data. devbrief is designed for local Claude Code history, so the README intentionally avoids real screenshots that could expose project names, local paths, or session content.

### List sessions

`devbrief list` gives you a compact index of local Claude Code sessions for the current project.

```console
$ devbrief list
Filtered to: /Users/you/Code/project-alpha

ID        Status       Date        Project        Turns  Title
────────────────────────────────────────────────────────────────────────────────
a1b2c3d4  pending/raw  2026-04-29  project-alpha      1  Review deployment readiness...
e5f6a7b8  pending/raw  2026-04-28  project-alpha      9  Investigate failing integration test...
c9d0e1f2  briefed      2026-04-28  project-alpha      1  Fix generated summary wording...
b3c4d5e6  blocked      2026-04-27  project-alpha      5  Refactor authentication flow...
f7a8b9c0  pending/raw  2026-04-27  project-alpha     12  Prepare release checklist...
```

### Preview a raw session

`devbrief raw <session-id>` shows a local preview of the original session without calling an LLM.

```
$ devbrief raw a1b2c3d4

Claude Code Session a1b2c3d4
Local preview · no LLM

Home project   project-alpha
CWD            /Users/you/Code/project-alpha
Started        2026-04-29 10:01 UTC
Updated        2026-04-29 10:07 UTC
Status         pending/raw
Turns          1

Session Outcome
Status         completed
Completion     complete
Confidence     medium
Reason         The final response included completion language.

1. Human Request
Review deployment readiness for project-alpha, including:
1. Backend environment setup
2. Client release preparation
3. Backend/client alignment
4. Final closeout notes with commit, deployment, config, and blockers

Important context:
- Verify local HEAD
- Verify remote main
- Check deployed SHA
- Confirm clean tracked working tree
- Report untracked files separately
- Run relevant tests
- Do not make destructive changes
- Do not create or modify production resources

[...use --full for complete request]
```

### Browse in the TUI

Running `devbrief` opens the interactive two-pane browser.

```
┌──────────────────────────── devbrief — project-alpha sessions ────────────────────────────┐
│  Sessions                                           │  Selected session                    │
├─────────────────────────────────────────────────────┼──────────────────────────────────────┤
│ ○ pending/raw  project-alpha                        │ project-alpha / a1b2c3d4              │
│ 04-29 10:01 · 1 turn · 10 files                     │ Status: pending/raw                   │
│ Review deployment readiness...                      │ Started: 2026-04-29 10:01 UTC         │
│                                                     │ Updated: 2026-04-29 10:07 UTC         │
│ ✓ briefed      project-alpha                        │ Turns: 1 · Files: 10                  │
│ 04-28 13:08 · 1 turn · 4 files                      │ Project path: /Users/you/Code/project │
│ Fix generated summary wording...                    │                                      │
│                                                     │ Raw History Preview                   │
│ ○ pending/raw  api-service                          │ local · free · no LLM                 │
│ 04-28 11:42 · 8 turns · 12 files                    │                                      │
│ Investigate failing integration test...             │ Problem                              │
│                                                     │ The session captured a release        │
│ ○ blocked      app-core                             │ readiness check and surfaced one      │
│ 04-27 18:20 · 5 turns · 6 files                     │ unresolved configuration issue.       │
│ Refactor authentication flow...                     │                                      │
│                                                     │ Approach                             │
│                                                     │ Verified local state, checked remote  │
│                                                     │ branches, reviewed config files, and  │
│                                                     │ produced a closeout summary.          │
│                                                     │                                      │
│                                                     │ Outcome                              │
│                                                     │ Ready for review, with one blocker    │
│                                                     │ clearly documented.                   │
├─────────────────────────────────────────────────────┴──────────────────────────────────────┤
│ e Estimate   d Brief   v Raw/Brief   ↵ Open   ? Help   r Refresh   a Toggle all   q Quit    │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Optional AI brief

Raw previews are local and free. AI briefs are optional and only generated when explicitly requested.

```console
$ devbrief estimate a1b2c3d4
Session: a1b2c3d4
Estimated input: 8,420 tokens
Estimated output: 700 tokens
No LLM call made.

$ devbrief brief a1b2c3d4
Generate AI brief for this session? [y/N]
```

**What this demonstrates:**

- Browse Claude Code history by project
- List sessions without opening raw JSONL files
- Preview raw sessions locally without spending tokens
- Generate AI briefs only when needed
- Avoid exposing private session details in README screenshots

All examples above use anonymised sample data. They are not captured from a real Claude Code session.

## Token Safety Model

| Command | Calls Claude? | Spends tokens? | Notes |
|---|---|---|---|
| `devbrief list` | No | No | Local JSONL + SQLite only |
| `devbrief raw SESSION_ID` | No | No | Local JSONL only |
| `devbrief view SESSION_ID` | No | No | Reads stored brief from SQLite |
| `devbrief estimate SESSION_ID` | No | No | Shows packet size, no API call |
| `devbrief doctor` | No | No | Local diagnostics only |
| `devbrief tui` browsing | No | No | Local display only |
| `devbrief capture --hook` | No | No | Metadata capture, no LLM |
| `devbrief brief SESSION_ID` | **Yes** | **Yes** | Only after showing estimate and asking confirmation |
| `devbrief digest SESSION_ID` | **Yes** | **Yes** | Deprecated alias for brief; avoid using it. |
| `devbrief report` | No | No | Disabled compatibility stub |

`--yes` skips the confirmation prompt on `brief` and spends tokens immediately. Use it only when you have already reviewed the estimate.

The optional Claude Code hook must be capture-only. Unsafe hook patterns to avoid:

```bash
devbrief digest --hook
devbrief brief --hook
claude -p
claude --print
```

## Installation

Development install:

```bash
git clone https://github.com/yuannh/devbrief.git
cd devbrief
pip install -e .
devbrief doctor
```

Future package install:

```bash
pip install devbrief
```

Raw history browsing does not require `devbrief setup`, an API key, or a Claude Code hook.

## Quick Start

```bash
cd /path/to/your/project
devbrief list
devbrief raw SESSION_ID
devbrief estimate SESSION_ID
devbrief brief SESSION_ID
devbrief view SESSION_ID
```

`devbrief list` scopes to the current home project by default. Use `devbrief list --all` to browse across all projects.

## Interactive Session Browser

Open the keyboard-first split-pane terminal UI:

```bash
devbrief
devbrief tui
devbrief --all
devbrief tui --all
```

The left pane shows the session list with ID, status, local outcome, date/time, project, and first human request or stored title. The right pane shows the selected session detail: a stored AI brief if one exists, or a local raw preview otherwise.

Keybindings:

| Key | Action |
|---|---|
| `j` / `k` or `↑` / `↓` | Move selection |
| `Enter` | Open/focus detail |
| `v` | Toggle raw preview / AI brief when both exist |
| `d` or `b` | Generate AI brief (shows estimate and asks confirmation first) |
| `r` | Refresh |
| `a` | Toggle current project / all projects |
| `?` | Help |
| `q` | Quit |

Opening or navigating the session browser never calls Claude. AI brief generation always requires explicit confirmation.

## Optional Claude Code Hook

The hook is optional. It captures lightweight session metadata when Claude Code emits a Stop hook event and does not call Claude, generate briefs, or spend tokens.

Install the safe hook:

```bash
devbrief install-hook
```

Remove all devbrief hook commands:

```bash
devbrief uninstall-hook
```

`devbrief install-hook` removes any old devbrief hook commands and installs only:

```bash
devbrief capture --hook
```

## Commands Reference

| Command | Purpose |
|---|---|
| `devbrief list` | Show current project's local history sessions. |
| `devbrief list --all` | Show sessions from all projects. |
| `devbrief list --briefed` | Show stored AI briefs for the current project. |
| `devbrief raw SESSION_ID` | Show local raw preview. No LLM call. |
| `devbrief raw SESSION_ID --full` | Include commands, errors, tool calls, longer snippets. |
| `devbrief raw SESSION_ID --json` | Output structured JSON. |
| `devbrief raw SESSION_ID --show-internal` | Show internal model prompts alongside human requests. |
| `devbrief estimate SESSION_ID` | Show token estimate for a session. No LLM call. |
| `devbrief view SESSION_ID` | Show a stored AI brief from SQLite. No LLM call. |
| `devbrief brief SESSION_ID` | Generate one AI brief after estimate + confirmation. |
| `devbrief digest SESSION_ID` | Deprecated alias for `brief`. Prints a warning. |
| `devbrief report` | Disabled compatibility stub. |
| `devbrief doctor` | Print diagnostics, paths, dependency status, and hook safety. |
| `devbrief setup` | Optional interactive config and hook prompt. |
| `devbrief install-info` | Print install and verification instructions. |
| `devbrief capture --hook` | Capture-only hook entrypoint. Metadata only, no LLM. |
| `devbrief install-hook` | Install/replace capture-only Claude Code Stop hook. |
| `devbrief uninstall-hook` | Remove all devbrief hook commands. |
| `devbrief tui` | Open the interactive session browser. |

## Storage and Config

| Path | Purpose |
|---|---|
| `~/.claude/projects` | Claude Code session transcripts (read-only) |
| `~/.config/devbrief/config.toml` | devbrief configuration |
| `~/.local/share/devbrief/sessions.db` | Session metadata and stored AI briefs |

Set `ANTHROPIC_API_KEY` only if you want optional manual AI briefs and cannot use the Claude CLI. Raw browsing never needs it.

Do not commit your DB, config, or Claude Code transcripts to version control.

## Privacy

- `list`, `raw`, `view`, `estimate`, and `doctor` are local-only and never send data outside your machine.
- `devbrief brief SESSION_ID` sends a compact, truncated evidence packet to Claude only after you explicitly confirm.
- API keys are never printed in diagnostics or logs.
- The local SQLite DB may contain summaries of your coding sessions. Treat it as sensitive.

## Safety Checklist

Run these to verify hook safety:

```bash
devbrief doctor
python3 -m json.tool ~/.claude/settings.json
```

Safe state:

- No devbrief hook installed, **or**
- Only `devbrief capture --hook`

Unsafe state — replace with a capture-only hook using `devbrief install-hook`, or remove all devbrief hooks using `devbrief uninstall-hook`:

- `devbrief digest --hook`
- `devbrief brief --hook`
- `claude -p` hook
- `claude --print` hook

## Development

```bash
git clone https://github.com/yuannh/devbrief.git
cd devbrief
pip install -e .
python -m compileall src
devbrief doctor
```

## License

MIT
