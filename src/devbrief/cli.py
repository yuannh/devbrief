"""devbrief CLI — token-safe Claude Code session tracker."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.prompt import Confirm, Prompt

from . import __version__
from . import config as cfg
from . import parser, reporter, storage, summarizer

console = Console()

# ── main group ────────────────────────────────────────────────────────────────

@click.group(invoke_without_command=True)
@click.version_option()
@click.option(
    "--all", "show_all", is_flag=True, default=False,
    help="Show sessions from all projects (default: current project only).",
)
@click.pass_context
def main(ctx: click.Context, show_all: bool) -> None:
    """devbrief — local Claude Code terminal history browser.

    Run without arguments to open the interactive session browser,
    filtered to the current home project. Use --all to see every session.
    """
    if ctx.invoked_subcommand is None:
        from .tui import DevbriefApp
        DevbriefApp(show_all=show_all).run()


# ── setup ─────────────────────────────────────────────────────────────────────

@main.command()
def setup() -> None:
    """Configure devbrief as a standalone local history browser."""
    import shutil
    console.print("\n[bold cyan]devbrief setup[/bold cyan]\n")
    console.print(
        "devbrief is a standalone local CLI. It reads Claude Code JSONL history "
        "from [dim]~/.claude/projects[/dim] and stores local data in "
        "[dim]~/.local/share/devbrief/sessions.db[/dim].\n"
    )
    console.print(
        "[green]Raw history browsing works without an API key and without Claude tokens.[/green]\n"
        "AI briefs are optional, manual, and confirmation-gated.\n"
    )

    has_cli = shutil.which("claude") is not None
    if has_cli:
        console.print(
            "[green]✓ Claude CLI detected[/green] — optional AI briefs can use it "
            "after explicit confirmation. No API key is required for raw browsing.\n"
        )
    else:
        console.print(
            "[yellow]Claude CLI not found.[/yellow] Raw browsing still works. "
            "You can provide an Anthropic API key only if you want manual AI briefs.\n"
        )

    existing = cfg.load()

    if not has_cli:
        current_key = existing.get("api_key", "")
        masked = (
            f"sk-ant-...{current_key[-6:]}" if len(current_key) > 10 else "(not set)"
        )
        console.print(f"Current API key: [dim]{masked}[/dim]")
        new_key = Prompt.ask(
            "Anthropic API key (leave blank to keep current)",
            default="",
            password=True,
        )
        if new_key.strip():
            existing["api_key"] = new_key.strip()

    console.print("Supported languages: en, zh, ja, fr, de, es, ...")
    lang = Prompt.ask("Output language", default=existing.get("language", "en"))
    existing["language"] = lang.strip()

    cfg.save(existing)
    console.print("\n[green]Config saved.[/green]")

    _offer_hook_setup()


def _offer_hook_setup() -> None:
    console.print("\n[bold]Optional Claude Code hook[/bold]")
    console.print(
        "devbrief can capture lightweight session metadata when Claude Code finishes.\n"
        "This adds a Stop hook to [dim]~/.claude/settings.json[/dim].\n"
        "[green]This hook only records metadata and does not spend tokens.[/green]\n"
        "[dim]It runs: devbrief capture --hook[/dim]\n"
    )

    if not Confirm.ask("Set up automatic capture hook?", default=True):
        console.print(
            "[dim]Skipped. Run 'devbrief capture --hook' manually or "
            "'devbrief setup' again.[/dim]"
        )
        return

    _install_hook()


def _install_hook() -> None:
    settings_path = Path.home() / ".claude" / "settings.json"
    hook_cmd = "devbrief capture --hook"

    try:
        settings: dict = {}
        if settings_path.exists():
            with open(settings_path) as f:
                settings = json.load(f)

        # Replace any old/unsafe devbrief hook with capture-only.
        _remove_devbrief_hooks(settings)
        hooks = settings.setdefault("hooks", {})
        stop_hooks = hooks.setdefault("Stop", [])

        stop_hooks.append({
            "matcher": "",
            "hooks": [{"type": "command", "command": hook_cmd}],
        })

        settings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)

        console.print(f"[green]Hook installed in {settings_path}[/green]")
        console.print(
            "[dim]devbrief will auto-capture session metadata when each "
            "session ends. No LLM calls are made automatically.[/dim]"
        )

    except Exception as e:
        console.print(f"[red]Failed to install hook: {e}[/red]")
        console.print("[dim]Add manually to ~/.claude/settings.json:[/dim]")
        example = (
            '{"hooks": {"Stop": [{"matcher": "", '
            f'"hooks": [{{"type": "command", "command": "{hook_cmd}"}}]'
            "}]}}"
        )
        console.print(f"[dim]{example}[/dim]")


def _remove_devbrief_entries(stop_hooks: list) -> None:
    """Remove any hook entry whose command mentions devbrief."""
    i = 0
    while i < len(stop_hooks):
        entry = stop_hooks[i]
        inner = entry.get("hooks", [])
        inner[:] = [
            h for h in inner
            if "devbrief" not in str(h.get("command", ""))
        ]
        if not inner:
            stop_hooks.pop(i)
        else:
            i += 1


def _remove_devbrief_hooks(settings: dict) -> int:
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        return 0

    removed = 0
    for event_name in list(hooks.keys()):
        entries = hooks.get(event_name)
        if not isinstance(entries, list):
            continue
        before = sum(
            1
            for entry in entries
            if isinstance(entry, dict)
            for hook in entry.get("hooks", [])
            if isinstance(hook, dict) and "devbrief" in str(hook.get("command", ""))
        )
        _remove_devbrief_entries(entries)
        after = sum(
            1
            for entry in entries
            if isinstance(entry, dict)
            for hook in entry.get("hooks", [])
            if isinstance(hook, dict) and "devbrief" in str(hook.get("command", ""))
        )
        removed += before - after
        if not entries:
            hooks.pop(event_name, None)
    if not hooks:
        settings.pop("hooks", None)
    return removed


# ── capture --hook (metadata-only, no LLM) ───────────────────────────────────

@main.command()
@click.option(
    "--hook", is_flag=True, required=True,
    help="Read Claude Code Stop hook JSON from stdin and capture metadata.",
)
def capture(hook: bool) -> None:
    """Capture session metadata from a Claude Code Stop hook (no LLM calls).

    Reads the hook JSON payload from stdin, parses lightweight metadata, and
    stores the session as 'pending' in SQLite.  Never calls the summarizer.

    Add to ~/.claude/settings.json Stop hooks:
        devbrief capture --hook
    """
    _run_capture_hook()


def _run_capture_hook() -> None:
    try:
        raw = sys.stdin.read()
        payload: dict = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    # Resolve JSONL path from payload
    tp = payload.get("transcript_path") or payload.get("transcriptPath", "")
    sid = payload.get("session_id") or payload.get("sessionId", "")

    jsonl_path: Path | None = None
    if tp:
        jsonl_path = Path(tp)
    elif sid:
        jsonl_path = _find_session_file(sid)

    if not jsonl_path or not jsonl_path.exists():
        jsonl_path = _latest_session_file()

    if not jsonl_path or not jsonl_path.exists():
        print("devbrief capture: no session file found", file=sys.stderr)
        sys.exit(0)

    session_id = jsonl_path.stem

    # Parse lightweight metadata only
    meta = parser._read_meta(jsonl_path)
    if not meta:
        print(
            f"devbrief capture: could not read metadata from {jsonl_path}",
            file=sys.stderr,
        )
        sys.exit(0)

    stat = jsonl_path.stat()
    created_at = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat()
    updated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

    storage.upsert_pending(
        session_id=session_id,
        jsonl_path=str(jsonl_path),
        project_name=meta.project_name,
        cwd=meta.project_path,
        created_at=created_at,
        updated_at=updated_at,
        user_turn_count=meta.user_turn_count,
    )

    print(
        f"devbrief: captured {meta.project_name}/{session_id[:8]} "
        f"({meta.user_turn_count} turns) — pending",
        flush=True,
    )


# ── uninstall-hook ────────────────────────────────────────────────────────────

@main.command("uninstall-hook")
def uninstall_hook() -> None:
    """Remove all devbrief Stop hooks from ~/.claude/settings.json."""
    settings_path = Path.home() / ".claude" / "settings.json"

    if not settings_path.exists():
        console.print("[yellow]~/.claude/settings.json not found — nothing to do.[/yellow]")
        return

    try:
        with open(settings_path) as f:
            settings = json.load(f)
    except Exception as e:
        console.print(f"[red]Could not read settings.json: {e}[/red]")
        sys.exit(1)

    removed = _remove_devbrief_hooks(settings)

    if removed == 0:
        console.print("[dim]No devbrief hooks found in ~/.claude/settings.json.[/dim]")
        return

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)

    console.print(
        f"[green]Removed {removed} devbrief hook command(s) from "
        f"{settings_path}[/green]"
    )


# ── install-hook (standalone) ─────────────────────────────────────────────────

@main.command("install-hook")
def install_hook() -> None:
    """Install the Claude Code Stop hook (capture-only, no LLM)."""
    _install_hook()


# ── install-info ──────────────────────────────────────────────────────────────

@main.command("install-info")
def install_info() -> None:
    """Print standalone install and verification instructions."""
    console.print("[bold cyan]devbrief install info[/bold cyan]\n")
    console.print("[bold]Development install[/bold]")
    console.print("  git clone <repo-url>")
    console.print("  cd devbrief")
    console.print("  pip install -e .\n")
    console.print("[bold]Future package install[/bold]")
    console.print("  pip install devbrief\n")
    console.print("[bold]Verify[/bold]")
    console.print("  devbrief doctor")
    console.print("  devbrief list\n")
    console.print("[bold]Optional capture-only hook[/bold]")
    console.print("  devbrief install-hook\n")
    console.print("[bold]Safety[/bold]")
    console.print(
        "  Raw browsing does not require an API key or hook.\n"
        "  The hook runs only 'devbrief capture --hook'.\n"
        "  It does not call Claude, the Anthropic SDK, brief, or digest."
    )


# ── estimate ─────────────────────────────────────────────────────────────────

@main.command()
@click.argument("session_id")
@click.option(
    "--max-chars", default=16_000, show_default=True,
    help="Max chars for the evidence packet.",
)
def estimate(session_id: str, max_chars: int) -> None:
    """Show token estimate for briefing a session — no LLM call.

    Builds the compact evidence packet and prints size statistics.
    """
    from . import compactor

    jsonl_path = _resolve_session_path(session_id)
    if not jsonl_path:
        sys.exit(1)

    raw_messages = parser.extract_raw_messages(jsonl_path)
    if not raw_messages:
        console.print("[yellow]No messages found in session.[/yellow]")
        sys.exit(0)

    db_session = storage.get(jsonl_path.stem)
    session_meta: dict | None = None
    if db_session:
        session_meta = {
            "session_id": db_session.session_id,
            "project_name": db_session.project_name,
            "cwd": db_session.project_path,
            "user_turn_count": db_session.user_turn_count,
        }

    _packet, meta = compactor.build_evidence_packet(
        raw_messages, max_chars=max_chars, session_meta=session_meta
    )

    console.print(f"\n[bold cyan]Token estimate for session {jsonl_path.stem[:8]}[/bold cyan]")
    console.print(f"  Raw transcript chars   {meta['raw_chars']:>10,}")
    console.print(f"  Compact evidence chars {meta['compact_chars']:>10,}")
    console.print(f"  Approx input tokens    {meta['estimated_tokens']:>10,}")
    console.print(f"  Truncated              {'[yellow]yes[/yellow]' if meta['truncated'] else '[green]no[/green]'}")
    console.print()
    excluded = meta["excluded_counts"]
    if any(excluded.values()):
        console.print("  [dim]Excluded/truncated:[/dim]")
        for k, v in excluded.items():
            if v:
                console.print(f"    {k:<28} {v}")
    console.print()
    console.print("[dim]No LLM call made.[/dim]")


# ── brief / digest ────────────────────────────────────────────────────────────

@main.command()
@click.argument("session_id")
@click.option("--hook", is_flag=True, hidden=True,
              help="(Deprecated) use 'devbrief capture --hook' instead.")
@click.option("--force", is_flag=True, help="Regenerate even if already briefed.")
@click.option("--yes", "-y", "skip_confirm", is_flag=True,
              help="Skip confirmation and spend tokens immediately. Use with care.")
@click.option("--max-chars", default=16_000, show_default=True,
              help="Max chars for the evidence packet sent to Claude.")
def digest(
    session_id: str | None,
    hook: bool,
    force: bool,
    skip_confirm: bool,
    max_chars: int,
) -> None:
    """Deprecated alias for 'devbrief brief'."""
    if hook:
        console.print(
            "[yellow]'devbrief digest --hook' is deprecated.[/yellow]\n"
            "Install the new capture-only hook with: devbrief install-hook"
        )
        sys.exit(0)

    console.print(
        "[yellow]devbrief digest is deprecated. Use devbrief brief instead.[/yellow]"
    )
    _brief_command(session_id, force, skip_confirm, max_chars)


@main.command("brief")
@click.argument("session_id")
@click.option("--force", is_flag=True, help="Regenerate even if already briefed.")
@click.option("--yes", "-y", "skip_confirm", is_flag=True,
              help="Skip confirmation and spend tokens immediately. Use with care.")
@click.option("--max-chars", default=16_000, show_default=True,
              help="Max chars for the evidence packet sent to Claude.")
def brief(
    session_id: str,
    force: bool,
    skip_confirm: bool,
    max_chars: int,
) -> None:
    """Generate an optional AI brief for a local history session."""
    _brief_command(session_id, force, skip_confirm, max_chars)


def _brief_command(
    session_id: str,
    force: bool,
    skip_confirm: bool,
    max_chars: int,
) -> None:
    import shutil
    conf = cfg.load()
    if not shutil.which("claude") and not cfg.get_api_key():
        console.print(
            "[red]No API key and claude CLI not found. Run: devbrief setup[/red]"
        )
        sys.exit(1)

    jsonl_path = _resolve_session_path(session_id)
    if not jsonl_path:
        console.print(f"[red]Session not found: {session_id}[/red]")
        sys.exit(1)

    _run_digest(jsonl_path, conf, force, quiet=False, skip_confirm=skip_confirm,
                max_chars=max_chars)


def _run_digest(
    jsonl_path: Path,
    conf: dict,
    force: bool,
    quiet: bool,
    skip_confirm: bool = False,
    max_chars: int = 16_000,
) -> None:
    from . import compactor

    session_id = jsonl_path.stem

    if not force and storage.exists(session_id):
        if not quiet:
            console.print("[dim]Already briefed. Use --force to regenerate.[/dim]")
            s = storage.get(session_id)
            if s:
                reporter.print_session_detail(s)
        return

    meta = parser._read_meta(jsonl_path)
    if not meta:
        if not quiet:
            console.print("[red]Could not read session metadata.[/red]")
        return

    raw_messages = parser.extract_raw_messages(jsonl_path)
    if not raw_messages:
        if not quiet:
            console.print("[yellow]No conversation found in session.[/yellow]")
        return

    session_meta = {
        "session_id": session_id,
        "project_name": meta.project_name,
        "cwd": meta.project_path,
        "user_turn_count": meta.user_turn_count,
    }
    packet, est = compactor.build_evidence_packet(
        raw_messages, max_chars=max_chars, session_meta=session_meta
    )

    if not quiet:
        console.print(
            f"\n[bold]Session:[/bold] {meta.project_name}/{session_id[:8]}"
        )
        console.print(f"  Compact evidence chars : {est['compact_chars']:,}")
        console.print(f"  Approx input tokens    : {est['estimated_tokens']:,}")
        if est["truncated"]:
            console.print("  [yellow]⚠ Transcript was truncated to fit max_chars[/yellow]")
        console.print()

        if not skip_confirm:
            if not Confirm.ask(
                "[bold yellow]Generate brief and spend Claude tokens?[/bold yellow]",
                default=False,
            ):
                console.print("[dim]Aborted.[/dim]")
                return

    if not quiet:
        console.print(f"[dim]Generating brief for {session_id[:8]}…[/dim]")
    else:
        print(
            f"devbrief: briefing {meta.project_name}/{session_id[:8]}…",
            flush=True,
        )

    try:
        data = summarizer.summarize(packet, language=conf.get("language", "en"))
    except Exception as e:
        if not quiet:
            console.print(f"[red]Brief generation failed: {e}[/red]")
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
        status="digested",
        user_turn_count=meta.user_turn_count,
        digested_at=datetime.now(tz=timezone.utc),
    )
    storage.upsert(session)

    if not quiet:
        reporter.print_session_detail(session)
    else:
            print(f"devbrief: briefed — {data['title']}", flush=True)


# ── list ──────────────────────────────────────────────────────────────────────

@main.command("list")
@click.option("--project", "-p", help="Filter by project name.")
@click.option("--current", is_flag=True, help="Show only sessions from current project.")
@click.option("--limit", "-n", default=20, show_default=True, help="Max sessions to show.")
@click.option("--all", "show_all", is_flag=True, default=False,
              help="Show sessions from all home projects.")
@click.option("--briefed", "briefed_only", is_flag=True,
              help="Show only SQLite-stored AI briefs.")
@click.option("--stored", "briefed_only", is_flag=True,
              help="Alias for --briefed.")
def list_sessions(
    project: str | None,
    current: bool,
    limit: int,
    show_all: bool,
    briefed_only: bool,
) -> None:
    """List local Claude Code history sessions."""
    root = None if show_all else parser.get_project_root()
    rows = _build_history_rows(project_root=root, briefed_only=briefed_only)

    if root:
        console.print(f"[dim]Filtered to: {root}[/dim]\n")

    if project:
        rows = [r for r in rows if project.lower() in r["project_name"].lower()]

    reporter.print_history_list(rows[:limit])


# ── view ──────────────────────────────────────────────────────────────────────

@main.command()
@click.argument("session_id")
def view(session_id: str) -> None:
    """View a stored session brief from local SQLite only."""
    matches = storage.find_by_prefix(session_id)

    if not matches:
        console.print(f"[red]Session not found: {session_id}[/red]")
        console.print("[dim]Use 'devbrief list' to see available sessions.[/dim]")
        sys.exit(1)
    if len(matches) > 1:
        console.print(f"[red]Session prefix is ambiguous: {session_id}[/red]")
        console.print("[dim]Matching sessions:[/dim]")
        for s in matches[:20]:
            console.print(
                f"  {s.session_id}  {s.started_at.strftime('%Y-%m-%d %H:%M')}  "
                f"{s.project_name}"
            )
        if len(matches) > 20:
            console.print(f"  [dim]…and {len(matches) - 20} more[/dim]")
        sys.exit(1)

    reporter.print_session_detail(matches[0])


# ── raw ───────────────────────────────────────────────────────────────────────

@main.command()
@click.argument("session_id")
@click.option(
    "--show-internal",
    is_flag=True,
    help="Show internal analyzer/summarizer prompts separately.",
)
@click.option("--full", "show_full", is_flag=True, help="Show longer content and higher limits.")
@click.option("--json", "as_json", is_flag=True, help="Output structured JSON for debugging/export.")
@click.option("--commands", "show_commands", is_flag=True, help="Show commands in default mode.")
@click.option("--errors", "show_errors", is_flag=True, help="Show errors/blockers in default mode.")
@click.option("--tools", "show_tools", is_flag=True, help="Show tool calls in default mode.")
def raw(
    session_id: str,
    show_internal: bool,
    show_full: bool,
    as_json: bool,
    show_commands: bool,
    show_errors: bool,
    show_tools: bool,
) -> None:
    """Print a local raw history preview from the JSONL transcript. No LLM call."""
    from . import compactor

    db_matches = storage.find_by_prefix(session_id)
    if len(db_matches) > 1:
        console.print(f"[red]Session prefix is ambiguous: {session_id}[/red]")
        console.print("[dim]Matching sessions:[/dim]")
        for s in db_matches[:20]:
            console.print(
                f"  {s.session_id}  {s.started_at.strftime('%Y-%m-%d %H:%M')}  "
                f"{s.project_name}"
            )
        if len(db_matches) > 20:
            console.print(f"  [dim]…and {len(db_matches) - 20} more[/dim]")
        sys.exit(1)

    session = db_matches[0] if db_matches else None
    jsonl_path: Path | None = None
    if session and session.raw_path:
        candidate = Path(session.raw_path)
        if candidate.exists():
            jsonl_path = candidate

    if jsonl_path is None:
        jsonl_path = _find_session_file_by_prefix(session_id)

    if not jsonl_path or not jsonl_path.exists():
        console.print(f"[red]JSONL transcript not found for: {session_id}[/red]")
        sys.exit(1)

    raw_messages = parser.extract_raw_messages(jsonl_path)
    preview = compactor.build_history_preview(raw_messages, full=show_full)
    outcome = compactor.infer_session_outcome(preview, raw_messages)

    meta = parser._read_meta(jsonl_path)
    db_status = "briefed" if session and session.status == "digested" else "pending/raw"
    started = (
        meta.started_at.strftime("%Y-%m-%d %H:%M UTC")
        if meta else "unknown"
    )
    updated = datetime.fromtimestamp(
        jsonl_path.stat().st_mtime, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")
    project = meta.project_name if meta else (session.project_name if session else "")
    cwd = meta.project_path if meta else (session.project_path if session else "")
    turn_count = meta.user_turn_count if meta else (session.user_turn_count if session else 0)

    data = _raw_preview_payload(
        session_id=jsonl_path.stem,
        project=project,
        cwd=cwd,
        started=started,
        updated=updated,
        status=db_status,
        turn_count=turn_count,
        preview=preview,
        outcome=outcome,
        show_internal=show_internal,
    )
    if as_json:
        console.print_json(json.dumps(data, ensure_ascii=False))
        return

    _print_raw_reading_view(
        data,
        full=show_full,
        show_internal=show_internal,
        show_commands=show_commands,
        show_errors=show_errors,
        show_tools=show_tools,
    )


def _print_raw_section(title: str, values: list[str], limit: int = 30) -> None:
    shown = [v for v in values if v][:limit]
    if not shown:
        return
    console.print(f"[bold cyan]{title}[/bold cyan]")
    for value in shown:
        console.print(f"  - {value}")
    if len(values) > limit:
        console.print(f"  [dim]…and {len(values) - limit} more[/dim]")
    console.print()


def _raw_preview_payload(
    *,
    session_id: str,
    project: str,
    cwd: str,
    started: str,
    updated: str,
    status: str,
    turn_count: int,
    preview: dict,
    outcome: dict,
    show_internal: bool,
) -> dict:
    home = Path(cwd).resolve() if cwd else None
    commands = [_short_command(c) for c in preview.get("commands", [])]
    files = [
        _display_path(path, home)
        for path in preview.get("files", [])
    ]
    final = preview.get("final_assistant_text", "")
    payload = {
        "session": {
            "short_id": session_id[:8],
            "session_id": session_id,
            "home_project": project,
            "cwd": cwd,
            "started": started,
            "updated": updated,
            "status": status,
            "turn_count": turn_count,
            "note": "Local preview, no LLM",
        },
        "session_outcome": outcome,
        "human_requests": preview.get("user_requests", []),
        "what_happened_locally": _local_happened_summary(
            commands=commands,
            files=files,
            tools=preview.get("tools", []),
            final_response=final,
        ),
        "files_touched_or_inspected": files,
        "commands_run": commands,
        "tool_calls": preview.get("tools", []),
        "errors_or_blockers": _dedupe_texts(preview.get("errors", [])),
        "final_assistant_response": {
            "kind": _final_response_kind(final),
            "text": final,
        },
        "counts": preview.get("counts", {}),
    }
    if show_internal:
        payload["internal_model_prompts"] = preview.get("internal_model_prompts", [])
    return payload


def _print_raw_reading_view(
    data: dict,
    *,
    full: bool,
    show_internal: bool,
    show_commands: bool,
    show_errors: bool,
    show_tools: bool,
) -> None:
    session = data["session"]
    limit = 80_000 if full else 2_500
    file_limit = 100 if full else 10
    item_limit = 100 if full else 20
    error_limit = 30 if full else 10

    console.print(
        f"[bold cyan]Claude Code Session {session['short_id']}[/bold cyan] "
        f"[dim]({session['session_id']})[/dim]"
    )
    console.print("[dim]Local preview, no LLM[/dim]\n")
    console.print(f"[bold]Home project[/bold] {session['home_project']}")
    console.print(f"[bold]CWD[/bold]          {session['cwd']}")
    console.print(f"[bold]Started[/bold]      {session['started']}")
    console.print(f"[bold]Updated[/bold]      {session['updated']}")
    console.print(f"[bold]Status[/bold]       {session['status']}")
    if session.get("turn_count"):
        console.print(f"[bold]Turns[/bold]        {session['turn_count']}")
    console.print()

    _print_outcome_section(data.get("session_outcome", {}))

    requests = data.get("human_requests", [])
    console.print("[bold cyan]1. Human Request[/bold cyan]")
    if requests:
        request_limit = 80_000 if full else 1_000
        for i, request in enumerate(requests, 1):
            prefix = f"[bold]{i}.[/bold] " if len(requests) > 1 else ""
            console.print(prefix + _truncate_request_display(request, request_limit, full=full))
            console.print()
    else:
        console.print(
            "[yellow]No human user request found. This session appears to be "
            "an internal tool/model run.[/yellow]"
        )
        if not show_internal:
            console.print("[dim]Run with --show-internal to inspect internal prompts.[/dim]")
        console.print()

    console.print("[bold cyan]2. What Happened Locally[/bold cyan]")
    console.print(data["what_happened_locally"])
    console.print()

    _print_numbered_raw_section(
        "3. Files Touched / Inspected",
        data.get("files_touched_or_inspected", []),
        limit=file_limit,
    )

    next_section = 4
    if full or show_commands:
        _print_numbered_raw_section(
            f"{next_section}. Commands Run",
            data.get("commands_run", []),
            limit=item_limit,
        )
        next_section += 1

    outcome_status = data.get("session_outcome", {}).get("status")
    should_show_errors = (
        full
        or show_errors
        or outcome_status in {"blocked", "interrupted"}
    )
    if should_show_errors:
        _print_numbered_raw_section(
            f"{next_section}. Errors / Blockers",
            data.get("errors_or_blockers", []),
            limit=3 if not (full or show_errors) else error_limit,
        )
        next_section += 1

    if full or show_tools:
        _print_numbered_raw_section(
            f"{next_section}. Tool Calls",
            data.get("tool_calls", []),
            limit=item_limit,
        )
        next_section += 1

    console.print(f"[bold cyan]{next_section}. Final Assistant Response[/bold cyan]")
    final = data.get("final_assistant_response", {})
    final_text = final.get("text") or ""
    final_limit = 1_000 if not full else limit
    if not final_text:
        console.print("[dim]No final natural-language response found.[/dim]\n")
    elif final.get("kind") == "stopped":
        console.print("[yellow]Session stopped because:[/yellow]")
        console.print(_truncate_display(final_text, final_limit))
        console.print()
    else:
        console.print(_truncate_display(final_text, final_limit))
        console.print()

    if show_internal:
        _print_numbered_raw_section(
            f"{next_section + 1}. Internal Model Prompts",
            data.get("internal_model_prompts", []),
            limit=30 if full else 10,
            text_limit=limit,
        )

    if not full and not (show_commands or show_errors or show_tools):
        console.print(
            "[dim]Use --full to show commands, errors, tool calls, and longer snippets.[/dim]"
        )


def _print_outcome_section(outcome: dict) -> None:
    console.print("[bold cyan]Session Outcome[/bold cyan]")
    console.print(f"[bold]Status[/bold]      {outcome.get('status', 'unknown')}")
    console.print(f"[bold]Completion[/bold]  {outcome.get('completion', 'unknown')}")
    console.print(f"[bold]Confidence[/bold]  {outcome.get('confidence', 'low')}")
    console.print(f"[bold]Reason[/bold]      {outcome.get('reason', '')}")
    signals = outcome.get("signals") or []
    if signals:
        concise = "; ".join(str(s) for s in signals[:5])
        console.print(f"[bold]Signals[/bold]     {concise}")
    console.print()


def _print_numbered_raw_section(
    title: str,
    values: list[str],
    *,
    limit: int,
    text_limit: int = 1_200,
) -> None:
    console.print(f"[bold cyan]{title}[/bold cyan]")
    shown = [v for v in _dedupe_texts(values) if v][:limit]
    if not shown:
        console.print("[dim]None detected.[/dim]\n")
        return
    for value in shown:
        console.print(f"  - {_truncate_display(value, text_limit)}")
    remaining = len(values) - len(shown)
    if remaining > 0:
        console.print(f"  [dim]...and {remaining} more[/dim]")
    console.print()


def _local_happened_summary(
    *,
    commands: list[str],
    files: list[str],
    tools: list[str],
    final_response: str,
) -> str:
    actions: list[str] = []
    lower_commands = " ".join(commands).lower()
    file_text = " ".join(files).lower()

    if any(tool in tools for tool in ("Read", "Glob", "Grep")) or files:
        actions.append("inspected project files")
    if any(tool in tools for tool in ("Edit", "Write", "MultiEdit", "str_replace_editor")):
        actions.append("edited code")
    if "grep" in lower_commands or "rg " in lower_commands:
        actions.append("searched for relevant code paths")
    if "pytest" in lower_commands or " test" in lower_commands:
        actions.append("ran tests")
    if "ssh " in lower_commands or "docker " in lower_commands:
        actions.append("checked remote or container state")
    if "git " in lower_commands:
        actions.append("checked git state")

    focus_terms: list[str] = []
    for token in ("quote", "blockquote", "story", "moment", "evidence", "render", "api", "test"):
        if token in file_text or token in lower_commands:
            focus_terms.append(token)

    if not actions:
        actions.append("reviewed the local session transcript")

    subject = ", ".join(_dedupe_texts(focus_terms[:4]))
    if subject:
        return f"Claude {', '.join(_dedupe_texts(actions))}, focusing on {subject}."
    if final_response:
        return f"Claude {', '.join(_dedupe_texts(actions))} and produced a final response."
    return f"Claude {', '.join(_dedupe_texts(actions))}."


def _short_command(command: str) -> str:
    one_line = " ".join(command.split())
    if len(one_line) <= 220:
        return one_line
    return one_line[:219] + "…"


def _display_path(path: str, home: Path | None) -> str:
    try:
        p = Path(path).resolve()
        if home is not None:
            try:
                return str(p.relative_to(home))
            except ValueError:
                pass
        parts = p.parts
        if len(parts) >= 4:
            return str(Path(*parts[-4:]))
        return str(p)
    except Exception:
        return path


def _final_response_kind(text: str) -> str:
    lower = text.lower()
    if not text:
        return "none"
    if "out of" in lower and "usage" in lower:
        return "stopped"
    if "tool_use_error" in lower or lower.startswith("error"):
        return "stopped"
    return "natural_language"


def _truncate_display(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"…[+{len(text) - limit} chars]"


def _truncate_request_display(text: str, limit: int, *, full: bool) -> str:
    if len(text) <= limit:
        return text
    if full:
        return text[:limit] + f"…[+{len(text) - limit} chars]"
    return text[:limit] + f"\n[...+{len(text) - limit} chars, use --full for complete request]"


def _dedupe_texts(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        clean = str(value).strip()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def _build_history_rows(
    project_root: Path | None = None,
    briefed_only: bool = False,
) -> list[dict]:
    from . import compactor

    stored_map = {
        s.session_id: s
        for s in storage.get_all(limit=10_000, include_pending=True)
    }
    discovered = parser.get_all_sessions()
    rows: list[dict] = []
    seen: set[str] = set()

    for meta in discovered:
        if project_root is not None and not parser.session_matches_project(meta, project_root):
            continue

        stored = stored_map.get(meta.session_id)
        if briefed_only and not (stored and stored.status == "digested"):
            continue

        seen.add(meta.session_id)
        preview = _preview_for_list(meta.jsonl_path, compactor)
        is_briefed = bool(stored and stored.status == "digested")
        title = (
            stored.title
            if is_briefed and stored and stored.title
            else preview.get("first_user_request") or "(no human request)"
        )
        updated = _jsonl_mtime(meta.jsonl_path)
        rows.append({
            "session_id": meta.session_id,
            "status": "briefed" if is_briefed else "pending/raw",
            "date": (meta.started_at or updated).strftime("%Y-%m-%d"),
            "sort_at": updated or meta.started_at,
            "project_name": meta.project_name,
            "turns": stored.user_turn_count if stored and stored.user_turn_count else meta.user_turn_count,
            "title": title,
        })

    for session_id, stored in stored_map.items():
        if session_id in seen:
            continue
        if project_root is not None and not _path_matches_project(stored.project_path, project_root):
            continue
        if briefed_only and stored.status != "digested":
            continue

        jsonl_path = Path(stored.raw_path) if stored.raw_path else None
        preview = _preview_for_list(jsonl_path, compactor) if jsonl_path else {}
        is_briefed = stored.status == "digested"
        title = (
            stored.title
            if is_briefed and stored.title
            else preview.get("first_user_request") or "(no human request)"
        )
        rows.append({
            "session_id": stored.session_id,
            "status": "briefed" if is_briefed else "pending/raw",
            "date": stored.started_at.strftime("%Y-%m-%d"),
            "sort_at": stored.updated_at or stored.started_at,
            "project_name": stored.project_name,
            "turns": stored.user_turn_count,
            "title": title,
        })

    rows.sort(key=lambda r: r["sort_at"], reverse=True)
    return rows


def _preview_for_list(jsonl_path: Path | None, compactor_module) -> dict:
    if not jsonl_path or not jsonl_path.exists():
        return {}
    try:
        return compactor_module.build_history_preview(parser.extract_raw_messages(jsonl_path))
    except Exception:
        return {}


def _jsonl_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _path_matches_project(path: str, project_root: Path) -> bool:
    if not path:
        return False
    try:
        cwd = Path(path).resolve()
        root = project_root.resolve()
        cwd_str = str(cwd)
        root_str = str(root)
        return (
            cwd == root
            or cwd_str.startswith(root_str + "/")
        )
    except Exception:
        return False


# ── report ────────────────────────────────────────────────────────────────────

@main.command()
def report() -> None:
    """Deprecated disabled command.

    Multi-session AI reports are disabled in devbrief's token-safe model.
    Use `devbrief brief SESSION_ID` for one explicit, confirmation-gated AI brief.
    """
    console.print(
        "[yellow]Deprecated disabled command.[/yellow] "
        "Multi-session AI reports are disabled in devbrief's token-safe model. "
        "Use `devbrief brief SESSION_ID` for one explicit, confirmation-gated AI brief."
    )


# ── tui ───────────────────────────────────────────────────────────────────────

@main.command("tui")
@click.option("--all", "show_all", is_flag=True, default=False,
              help="Show sessions from all projects.")
def tui_cmd(show_all: bool) -> None:
    """Open the interactive session browser."""
    from .tui import DevbriefApp
    DevbriefApp(show_all=show_all).run()


# ── doctor ────────────────────────────────────────────────────────────────────

@main.command()
def doctor() -> None:
    """Print diagnostic information for troubleshooting. No LLM calls."""
    import shutil

    cwd = Path.cwd()
    root = parser.get_project_root()
    devbrief_exe = shutil.which("devbrief") or "(not on PATH)"

    console.print("[bold cyan]devbrief doctor[/bold cyan]\n")
    console.print(f"  Version                 {__version__}")
    console.print(f"  Executable              {devbrief_exe}")
    console.print(f"  Python                  {sys.executable}")
    console.print(f"  CWD                     {cwd}")
    console.print(f"  Home project / git root {root}")
    console.print(f"  Config file             {cfg.CONFIG_FILE}")
    console.print(f"  DB file                 {cfg.DB_FILE}")
    console.print(f"  Claude projects dir     {cfg.CLAUDE_PROJECTS_DIR}")

    projects_dir = cfg.CLAUDE_PROJECTS_DIR
    if projects_dir.exists():
        project_dirs = [d for d in projects_dir.iterdir() if d.is_dir()]
        jsonl_files = [f for d in project_dirs for f in d.glob("*.jsonl")]
        console.print(f"  Claude project dirs     {len(project_dirs)}")
        console.print(f"  JSONL files             {len(jsonl_files)}")
    else:
        console.print(f"  [red]Claude projects dir not found: {projects_dir}[/red]")

    console.print()
    console.print("  [dim]Loading session counts…[/dim]")
    all_rows = _build_history_rows(project_root=None, briefed_only=False)
    current_rows = _build_history_rows(project_root=root, briefed_only=False)
    console.print(f"  Current project rows    {len(current_rows)}")
    console.print(f"  All history rows        {len(all_rows)}")
    console.print(f"  DB total rows           {storage.count_all()}")
    console.print(f"  DB briefed rows         {storage.count_digested()}")

    console.print()
    has_claude = shutil.which("claude") is not None
    console.print(
        f"  Claude CLI              "
        + ("[green]✓ found[/green]" if has_claude else "[red]✗ not found[/red]")
    )

    try:
        import textual
        console.print(f"  Textual                 [green]✓ {textual.__version__}[/green]")
    except ImportError as exc:
        console.print(f"  Textual                 [red]✗ {exc}[/red]")

    try:
        from .tui import DevbriefApp  # noqa: F401
        console.print("  TUI import              [green]✓ ok[/green]")
    except Exception as exc:
        console.print(f"  TUI import              [red]✗ {exc}[/red]")

    api_key = cfg.get_api_key()
    console.print(
        "  API key                 "
        + ("[green]set[/green]" if api_key else "[dim]not set[/dim]")
    )

    console.print()
    hook = _devbrief_hook_status()
    if hook["status"] == "capture":
        console.print("  Hook status             [green]capture hook installed[/green]")
    elif hook["status"] == "unsafe":
        console.print("  Hook status             [red]unsafe digest/brief hook installed[/red]")
        console.print(
            "  [yellow]Warning: unsafe devbrief hooks can spend tokens. "
            "Run 'devbrief install-hook' to replace them with capture-only, "
            "or 'devbrief uninstall-hook' to remove them.[/yellow]"
        )
    elif hook["status"] == "mixed":
        console.print("  Hook status             [yellow]capture hook + unsafe hook installed[/yellow]")
        console.print(
            "  [yellow]Warning: unsafe devbrief hooks can spend tokens. "
            "Run 'devbrief install-hook' to replace all devbrief hooks with capture-only.[/yellow]"
        )
    elif hook["status"] == "unreadable":
        console.print(f"  Hook status             [red]could not read settings.json: {hook['error']}[/red]")
    elif hook["status"] == "missing-settings":
        console.print("  Hook status             [dim]not installed (~/.claude/settings.json missing)[/dim]")
    else:
        console.print("  Hook status             [dim]not installed[/dim]")

    if hook.get("commands"):
        console.print("  Hook commands")
        for cmd in hook["commands"]:
            console.print(f"    - {cmd}")

    console.print()


# ── helpers ───────────────────────────────────────────────────────────────────

def _devbrief_hook_status() -> dict:
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return {"status": "missing-settings", "commands": []}

    try:
        with open(settings_path) as f:
            settings = json.load(f)
    except Exception as exc:
        return {"status": "unreadable", "commands": [], "error": str(exc)}

    commands = _devbrief_hook_commands(settings)
    if not commands:
        return {"status": "not-installed", "commands": []}

    capture = [cmd for cmd in commands if _is_capture_hook_command(cmd)]
    unsafe = [cmd for cmd in commands if not _is_capture_hook_command(cmd)]
    if capture and unsafe:
        status = "mixed"
    elif unsafe:
        status = "unsafe"
    else:
        status = "capture"
    return {"status": status, "commands": commands}


def _devbrief_hook_commands(settings: dict) -> list[str]:
    commands: list[str] = []
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        return commands
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for hook in entry.get("hooks", []):
                if not isinstance(hook, dict):
                    continue
                command = str(hook.get("command", ""))
                if "devbrief" in command:
                    commands.append(command)
    return commands


def _is_capture_hook_command(command: str) -> bool:
    stripped = " ".join(command.strip().split())
    return stripped == "devbrief capture --hook"

def _resolve_session_path(session_id: str) -> Path | None:
    """Resolve a (partial) session_id to a JSONL path."""
    # Check SQLite first
    sessions_db = storage.get_all(limit=2000, include_pending=True)
    matches_db = [s for s in sessions_db if s.session_id.startswith(session_id)]
    if matches_db and matches_db[0].raw_path:
        p = Path(matches_db[0].raw_path)
        if p.exists():
            return p

    # Fall back to scanning filesystem
    all_sessions = parser.get_all_sessions()
    matches = [s for s in all_sessions if s.session_id.startswith(session_id)]
    if not matches:
        console.print(f"[red]Session not found: {session_id}[/red]")
        return None
    return matches[0].jsonl_path


def _latest_session_file() -> Path | None:
    all_sessions = parser.get_all_sessions()
    return all_sessions[0].jsonl_path if all_sessions else None


def _find_session_file(session_id: str) -> Path | None:
    if not cfg.CLAUDE_PROJECTS_DIR.exists():
        return None
    for project_dir in cfg.CLAUDE_PROJECTS_DIR.iterdir():
        candidate = project_dir / f"{session_id}.jsonl"
        if candidate.exists():
            return candidate
    return None


def _find_session_file_by_prefix(session_id_prefix: str) -> Path | None:
    if not cfg.CLAUDE_PROJECTS_DIR.exists():
        return None

    matches: list[Path] = []
    for project_dir in cfg.CLAUDE_PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        matches.extend(project_dir.glob(f"{session_id_prefix}*.jsonl"))

    if not matches:
        return None
    if len(matches) > 1:
        console.print(f"[red]Session prefix is ambiguous: {session_id_prefix}[/red]")
        console.print("[dim]Matching transcript files:[/dim]")
        for path in matches[:20]:
            console.print(f"  {path.stem}  {path.parent.name}")
        if len(matches) > 20:
            console.print(f"  [dim]…and {len(matches) - 20} more[/dim]")
        sys.exit(1)
    return matches[0]
