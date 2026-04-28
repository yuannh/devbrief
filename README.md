# devbrief

AI-powered dev session log — captures Claude Code sessions automatically and generates boss-friendly reports.

## What it does

Every time you finish a Claude Code session (in Ghostty, VS Code, or any terminal), `devbrief` automatically:

1. Reads the session transcript
2. Uses Claude to extract: **problem → approach → outcome**
3. Stores it locally
4. Lets you generate a clean, non-technical report for your team or manager

## Install

```bash
pip install devbrief
devbrief setup
```

## Usage

```bash
devbrief list                      # see all captured sessions
devbrief view <session-id>         # details of one session
devbrief digest                    # manually capture latest session
devbrief report --today            # today's report
devbrief report --week             # this week's report
devbrief report --week --copy      # copy to clipboard (macOS)
```

## How auto-capture works

`devbrief setup` installs a Claude Code Stop hook that runs `devbrief digest --hook` every time a session ends. No manual steps needed.

## Config

- API key, language, model: `~/.config/devbrief/config.toml`
- Session database: `~/.local/share/devbrief/sessions.db`

Set `ANTHROPIC_API_KEY` env var to override the stored key.

## Supported languages

Set `language = "zh"` (or any language name) in config for non-English output.

## License

MIT
