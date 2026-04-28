import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.prompt import Confirm, Prompt

from . import config as cfg
from . import parser, reporter, storage, summarizer

console = Console()


@click.group(invoke_without_command=True)
@click.version_option()
@click.pass_context
def main(ctx: click.Context):
    """devbrief — AI-powered dev session log and report generator.

    Run without arguments to open the interactive session browser.
    """
    if ctx.invoked_subcommand is None:
        from .tui import DevbriefApp
        DevbriefApp().run()


# ── setup ────────────────────────────────────────────────────────────────────

@main.command()
def setup():
    """Interactive setup: language, optional API key, and Claude Code hook."""
    import shutil
    console.print("\n[bold cyan]devbrief setup[/bold cyan]\n")

    has_cli = shutil.which("claude") is not None
    if has_cli:
        console.print("[green]✓ Claude CLI detected[/green] — devbrief will use it directly, no API key needed.\n")
    else:
        console.print("[yellow]Claude CLI not found.[/yellow] You can provide an Anthropic API key instead.\n")

    existing = cfg.load()

    # API key — optional when claude CLI is available
    if not has_cli:
        current_key = existing.get("api_key", "")
        masked = f"sk-ant-...{current_key[-6:]}" if len(current_key) > 10 else "(not set)"
        console.print(f"Current API key: [dim]{masked}[/dim]")
        new_key = Prompt.ask(
            "Anthropic API key (leave blank to keep current)",
            default="",
            password=True,
        )
        if new_key.strip():
            existing["api_key"] = new_key.strip()

    # Language
    console.print("Supported languages: en, zh, ja, fr, de, es, ...")
    lang = Prompt.ask("Output language", default=existing.get("language", "en"))
    existing["language"] = lang.strip()

    cfg.save(existing)
    console.print("\n[green]Config saved.[/green]")

    # Hook setup
    _offer_hook_setup()


def _offer_hook_setup():
    console.print("\n[bold]Auto-capture hook[/bold]")
    console.print(
        "devbrief can auto-capture sessions when Claude Code finishes.\n"
        "This adds a Stop hook to [dim]~/.claude/settings.json[/dim].\n"
    )

    if not Confirm.ask("Set up automatic capture hook?", default=True):
        console.print("[dim]Skipped. You can run 'devbrief digest' manually.[/dim]")
        return

    _install_hook()


def _install_hook():
    settings_path = Path.home() / ".claude" / "settings.json"
    hook_cmd = "devbrief digest --hook"

    try:
        settings = {}
        if settings_path.exists():
            with open(settings_path) as f:
                settings = json.load(f)

        hooks = settings.setdefault("hooks", {})
        stop_hooks = hooks.setdefault("Stop", [])

        # Check if already installed
        for entry in stop_hooks:
            for h in entry.get("hooks", []):
                if h.get("command") == hook_cmd:
                    console.print("[green]Hook already installed.[/green]")
                    return

        stop_hooks.append({
            "matcher": "",
            "hooks": [{"type": "command", "command": hook_cmd}],
        })

        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)

        console.print(f"[green]Hook installed in {settings_path}[/green]")
        console.print("[dim]devbrief will auto-capture each session when it ends.[/dim]")

    except Exception as e:
        console.print(f"[red]Failed to install hook: {e}[/red]")
        console.print("[dim]Add manually to ~/.claude/settings.json:[/dim]")
        example = (
            '{"hooks": {"Stop": [{"matcher": "", '
            f'"hooks": [{{"type": "command", "command": "{hook_cmd}"}}]'
            "}]}}"
        )
        console.print(f"[dim]{example}[/dim]")


# ── digest ───────────────────────────────────────────────────────────────────

@main.command()
@click.argument("session_id", required=False)
@click.option("--hook", is_flag=True, hidden=True, help="Called from Claude Code Stop hook (reads stdin).")
@click.option("--force", is_flag=True, help="Re-summarize even if already captured.")
def digest(session_id: str | None, hook: bool, force: bool):
    """Summarize a session and store it.

    When called without arguments, digests the most recent session.
    Pass SESSION_ID to digest a specific session.
    """
    import shutil
    conf = cfg.load()
    if not shutil.which("claude") and not cfg.get_api_key():
        console.print("[red]No API key and claude CLI not found. Run: devbrief setup[/red]")
        sys.exit(1)

    jsonl_path: Path | None = None

    if hook:
        # Called by Claude Code Stop hook — JSON payload on stdin
        try:
            payload = json.loads(sys.stdin.read())
            # Claude Code uses snake_case or camelCase depending on version
            tp = payload.get("transcript_path") or payload.get("transcriptPath", "")
            sid = payload.get("session_id") or payload.get("sessionId", "")
            if tp:
                jsonl_path = Path(tp)
            elif sid:
                # Derive path from session_id by scanning projects dir
                jsonl_path = _find_session_file(sid)
        except Exception:
            pass

        if not jsonl_path or not jsonl_path.exists():
            # Fallback: most recent session file
            jsonl_path = _latest_session_file()

        if not jsonl_path:
            sys.exit(0)

        # Always overwrite in hook mode: Stop fires after every AI turn,
        # so the last firing captures the complete session.
        _run_digest(jsonl_path, conf, force=True, quiet=True)
        return

    if session_id:
        # Find session by ID or partial ID
        all_sessions = parser.get_all_sessions()
        matches = [s for s in all_sessions if s.session_id.startswith(session_id)]
        if not matches:
            console.print(f"[red]Session not found: {session_id}[/red]")
            sys.exit(1)
        meta = matches[0]
        jsonl_path = meta.jsonl_path
    else:
        jsonl_path = _latest_session_file()
        if not jsonl_path:
            console.print("[red]No sessions found.[/red]")
            sys.exit(1)

    _run_digest(jsonl_path, conf, force, quiet=False)


def _run_digest(jsonl_path: Path, conf: dict, force: bool, quiet: bool) -> None:
    session_id = jsonl_path.stem

    if not force and storage.exists(session_id):
        if not quiet:
            console.print(f"[dim]Already captured. Use --force to re-summarize.[/dim]")
            s = storage.get(session_id)
            if s:
                reporter.print_session_detail(s)
        return

    meta = parser._read_meta(jsonl_path)
    if not meta:
        if not quiet:
            console.print("[red]Could not read session metadata.[/red]")
        return

    if not quiet:
        console.print(f"[dim]Analyzing session {session_id[:8]}...[/dim]")
    else:
        print(f"devbrief: capturing {meta.project_name}/{session_id[:8]}...", flush=True)

    messages = parser.extract_messages(jsonl_path)
    if not messages:
        if not quiet:
            console.print("[yellow]No conversation found in session.[/yellow]")
        return

    transcript = parser.format_for_ai(messages)

    try:
        data = summarizer.summarize(transcript, language=conf.get("language", "en"))
    except Exception as e:
        if not quiet:
            console.print(f"[red]Summarization failed: {e}[/red]")
        else:
            print(f"devbrief: error — {e}", flush=True)
        return

    session = storage.Session(
        session_id=session_id,
        project_path=meta.project_path,
        project_name=meta.project_name,
        started_at=meta.started_at,
        title=data["title"],
        problem=data["problem"],
        approach=data["approach"],
        outcome=data["outcome"],
        summary=data["summary"],
        raw_path=str(jsonl_path),
    )
    storage.upsert(session)

    if not quiet:
        reporter.print_session_detail(session)
    else:
        print(f"devbrief: captured — {data['title']}", flush=True)


def _latest_session_file() -> Path | None:
    all_sessions = parser.get_all_sessions()
    return all_sessions[0].jsonl_path if all_sessions else None


def _find_session_file(session_id: str) -> Path | None:
    from devbrief import config as cfg
    for project_dir in cfg.CLAUDE_PROJECTS_DIR.iterdir():
        candidate = project_dir / f"{session_id}.jsonl"
        if candidate.exists():
            return candidate
    return None


# ── list ─────────────────────────────────────────────────────────────────────

@main.command("list")
@click.option("--project", "-p", help="Filter by project name.")
@click.option("--limit", "-n", default=20, show_default=True, help="Max sessions to show.")
def list_sessions(project: str | None, limit: int):
    """List captured dev sessions."""
    sessions = storage.get_all(limit=limit)

    if project:
        sessions = [s for s in sessions if project.lower() in s.project_name.lower()]

    reporter.print_session_list(sessions)


# ── view ─────────────────────────────────────────────────────────────────────

@main.command()
@click.argument("session_id")
def view(session_id: str):
    """View details of a captured session."""
    sessions = storage.get_all(limit=500)
    matches = [s for s in sessions if s.session_id.startswith(session_id)]

    if not matches:
        console.print(f"[red]Session not found: {session_id}[/red]")
        console.print("[dim]Use 'devbrief list' to see available sessions.[/dim]")
        sys.exit(1)

    reporter.print_session_detail(matches[0])


# ── report ───────────────────────────────────────────────────────────────────

@main.command()
@click.option("--today", "period", flag_value="today", help="Report for today.")
@click.option("--week", "period", flag_value="week", help="Report for this week.")
@click.option("--month", "period", flag_value="month", help="Report for this month.")
@click.option("--date", "specific_date", metavar="YYYY-MM-DD", help="Report for a specific date.")
@click.option("--copy", is_flag=True, help="Copy report to clipboard.")
def report(period: str | None, specific_date: str | None, copy: bool):
    """Generate a boss-friendly progress report.

    Examples:
      devbrief report --today
      devbrief report --week
      devbrief report --date 2025-04-20
    """
    if not period and not specific_date:
        period = "today"

    import shutil
    conf = cfg.load()
    if not shutil.which("claude") and not cfg.get_api_key():
        console.print("[red]No API key and claude CLI not found. Run: devbrief setup[/red]")
        sys.exit(1)

    start, end = reporter.get_period_range(period or "", specific_date)
    sessions = storage.get_by_date_range(start, end)

    if not sessions:
        console.print(f"[yellow]No sessions captured for {start} – {end}.[/yellow]")
        console.print("[dim]Run 'devbrief digest' to capture sessions first.[/dim]")
        return

    console.print(f"[dim]Generating report for {len(sessions)} session(s)...[/dim]")

    session_dicts = [
        {
            "project_name": s.project_name,
            "date": s.started_at.strftime("%Y-%m-%d"),
            "title": s.title,
            "problem": s.problem,
            "outcome": s.outcome,
            "summary": s.summary,
        }
        for s in sessions
    ]

    period_label = f"{start}" if start == end else f"{start} to {end}"

    try:
        text = summarizer.generate_report(
            session_dicts, period=period_label, language=conf.get("language", "en")
        )
    except Exception as e:
        console.print(f"[red]Report generation failed: {e}[/red]")
        sys.exit(1)

    reporter.print_report(text, period_label)

    if copy:
        try:
            import subprocess
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
            console.print("[green]Copied to clipboard.[/green]")
        except Exception:
            console.print("[yellow]--copy only works on macOS.[/yellow]")


# ── tui ──────────────────────────────────────────────────────────────────────

@main.command("tui")
def tui_cmd():
    """Open the interactive session browser (Yazi-style)."""
    from .tui import DevbriefApp
    DevbriefApp().run()


# ── hook-install (standalone) ─────────────────────────────────────────────────

@main.command("install-hook")
def install_hook():
    """Install the Claude Code Stop hook (auto-capture on session end)."""
    _install_hook()
