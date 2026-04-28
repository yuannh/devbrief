"""Textual TUI for devbrief — token-safe interactive session browser."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

from rich.markup import escape
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from . import compactor, config as cfg, parser, storage


# ── view model ────────────────────────────────────────────────────────────────

@dataclass
class SessionRow:
    session_id: str
    jsonl_path: Path
    project_name: str
    project_path: str
    started_at: Optional[datetime]
    turns: int
    status: Literal["digested", "pending"]
    title: str
    stored: Optional[storage.Session]
    meta: Optional[parser.SessionMeta]
    updated_at: Optional[datetime] = None
    first_user_request: str = ""
    touched_files_count: int = 0
    outcome_status: str = "unknown"


def load_rows(
    show_all: bool = True,
    project_root: Optional[Path] = None,
) -> list[SessionRow]:
    """Build the merged list of discovered + digested sessions.

    Priority:
    1. Sessions stored in SQLite (pending or digested) are shown first.
    2. JSONL files discovered on disk that are NOT in SQLite are appended.

    When show_all=False and project_root is given, only sessions whose cwd
    matches the current project are included.
    """
    discovered = parser.get_all_sessions()
    stored_map = {s.session_id: s for s in storage.get_all(limit=2000, include_pending=True)}

    rows: list[SessionRow] = []
    seen: set[str] = set()

    for meta in discovered:
        if not show_all and project_root is not None:
            if not parser.session_matches_project(meta, project_root):
                continue

        seen.add(meta.session_id)
        stored = stored_map.get(meta.session_id)

        # Prefer DB status if available; fall back to "pending" for disk-only files.
        if stored:
            status: Literal["digested", "pending"] = (
                "digested" if stored.status == "digested" else "pending"
            )
            title = stored.title or ""
            turns = stored.user_turn_count or meta.user_turn_count
        else:
            status = "pending"
            title = ""
            turns = meta.user_turn_count

        facts = _local_preview_facts(meta.jsonl_path)
        rows.append(SessionRow(
            session_id=meta.session_id,
            jsonl_path=meta.jsonl_path,
            project_name=meta.project_name,
            project_path=meta.project_path,
            started_at=meta.started_at,
            turns=turns,
            status=status,
            title=title,
            stored=stored,
            meta=meta,
            updated_at=stored.updated_at if stored else _file_updated_at(meta.jsonl_path),
            first_user_request=facts["first_user_request"],
            touched_files_count=facts["touched_files_count"],
            outcome_status=facts["outcome_status"],
        ))

    # Also include DB rows whose JSONL file no longer exists on disk (archived).
    for session_id, stored in stored_map.items():
        if session_id in seen:
            continue
        raw = Path(stored.raw_path) if stored.raw_path else None
        if raw is None:
            continue
        if not show_all and project_root is not None:
            try:
                cwd = Path(stored.project_path).resolve()
                root = project_root.resolve()
                if not (
                    cwd == root
                    or str(cwd).startswith(str(root) + "/")
                ):
                    continue
            except Exception:
                continue
        facts = _local_preview_facts(raw)
        rows.append(SessionRow(
            session_id=session_id,
            jsonl_path=raw,
            project_name=stored.project_name,
            project_path=stored.project_path,
            started_at=stored.started_at,
            turns=stored.user_turn_count,
            status="digested" if stored.status == "digested" else "pending",
            title=stored.title or "",
            stored=stored,
            meta=None,
            updated_at=stored.updated_at or _file_updated_at(raw),
            first_user_request=facts["first_user_request"],
            touched_files_count=facts["touched_files_count"],
            outcome_status=facts["outcome_status"],
        ))

    rows.sort(key=lambda r: r.started_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return rows


def _local_preview_facts(jsonl_path: Path) -> dict:
    """Small row-level facts for the free history browser. No LLM calls."""
    if not jsonl_path.exists():
        return {"first_user_request": "", "touched_files_count": 0}
    try:
        raw_messages = parser.extract_raw_messages(jsonl_path)
        preview = compactor.build_history_preview(raw_messages)
        return {
            "first_user_request": preview.get("first_user_request", ""),
            "touched_files_count": preview.get("touched_files_count", 0),
            "outcome_status": compactor.infer_session_outcome(preview, raw_messages).get("status", "unknown"),
        }
    except Exception:
        return {"first_user_request": "", "touched_files_count": 0, "outcome_status": "unknown"}


def _file_updated_at(path: Path) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _render_list_section(title: str, values: list[str], limit: int) -> str:
    shown = [v for v in values if v][:limit]
    if not shown:
        return ""
    lines = "\n".join(f"  • {escape(v)}" for v in shown)
    more = ""
    if len(values) > limit:
        more = f"\n  [dim]…and {len(values) - limit} more[/dim]"
    return f"[bold cyan]{title}[/bold cyan]\n{lines}{more}\n\n"


# ── widgets ───────────────────────────────────────────────────────────────────

class SessionItem(ListItem):
    def __init__(self, row: SessionRow) -> None:
        super().__init__()
        self.row = row

    def compose(self) -> ComposeResult:
        icon = "✓" if self.row.status == "digested" else "○"
        color = "green" if self.row.status == "digested" else "yellow"
        status = "briefed" if self.row.status == "digested" else "pending"
        date_str = (
            self.row.started_at.strftime("%m-%d %H:%M")
            if self.row.started_at else "—"
        )
        proj = escape(self.row.project_name[:20])

        yield Label(
            f"[{color}]{icon} {status:<7}[/{color}] "
            f"{proj:<18} [dim]{date_str} · {self.row.session_id[:8]} · {self.row.outcome_status}[/dim]"
        )

        request = self.row.title if self.row.status == "digested" else self.row.first_user_request
        if not request:
            request = "(no user request preview)"
        short = escape(request[:60] + ("…" if len(request) > 60 else ""))
        files = (
            f" · {self.row.touched_files_count} files"
            if self.row.touched_files_count else ""
        )
        yield Label(f"   [dim]{self.row.turns} turns{files} · {short}[/dim]")


# ── app ───────────────────────────────────────────────────────────────────────

_HINT = (
    "[dim]↑↓/jk navigate    "
    "e estimate    "
    "d/b generate brief    "
    "D re-brief    "
    "v raw/brief    "
    "r refresh    a toggle all    ? help    q quit[/dim]"
)

_LOADING = "[dim]Loading sessions…[/dim]"

_TOKEN_WARNING = (
    "[bold yellow]⚠ AI brief uses Claude tokens.[/bold yellow]\n"
    "[dim]Press [bold]d[/bold] to see estimate + confirm, "
    "[bold]e[/bold] for estimate only.[/dim]"
)


class DevbriefApp(App):
    TITLE = "devbrief"

    CSS = """
    Screen {
        background: $surface;
    }

    #main {
        height: 1fr;
    }

    #session-list {
        width: 42%;
        border-right: solid $primary-darken-2;
    }

    #detail-pane {
        width: 58%;
        padding: 1 2;
    }

    #detail-content {
        width: 100%;
    }

    SessionItem {
        height: auto;
        padding: 0 1;
    }

    SessionItem > Label {
        width: 100%;
    }

    SessionItem:hover {
        background: $boost;
    }

    SessionItem.--highlight {
        background: $accent;
    }
    """

    BINDINGS = [
        Binding("j",      "cursor_down",       "Down",          show=False),
        Binding("k",      "cursor_up",         "Up",            show=False),
        Binding("down",   "cursor_down",       "Down",          show=False),
        Binding("up",     "cursor_up",         "Up",            show=False),
        Binding("e",      "estimate_selected", "Estimate"),
        Binding("d",      "digest_selected",   "Brief"),
        Binding("b",      "digest_selected",   "Brief",        show=False),
        Binding("D",      "force_digest",      "Re-brief",      show=False),
        Binding("v",      "toggle_raw_preview","Raw/Brief"),
        Binding("enter",  "open_detail",       "Open"),
        Binding("y",      "confirm_digest",    "Confirm",       show=False),
        Binding("n",      "cancel_digest",     "Cancel",        show=False),
        Binding("escape", "cancel_or_quit",    "Cancel/Quit",   show=False),
        Binding("?",      "show_help",         "Help"),
        Binding("r",      "refresh_list",      "Refresh"),
        Binding("a",      "toggle_all",        "Toggle all"),
        Binding("q",      "quit",              "Quit"),
    ]

    def __init__(self, show_all: bool = False) -> None:
        super().__init__()
        self._show_all: bool = show_all
        self._project_root: Path = parser.get_project_root()
        self._rows: list[SessionRow] = []
        self._conf = cfg.load()
        self._show_raw_preview: bool = False
        # State for brief confirmation flow
        self._pending_digest_row: Optional[SessionRow] = None
        self._pending_digest_force: bool = False
        self._pending_report: Optional[tuple[list[storage.Session], str]] = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            yield ListView(id="session-list")
            with VerticalScroll(id="detail-pane"):
                yield Static(_LOADING, id="detail-content")
        yield Footer()

    def on_mount(self) -> None:
        self._update_subtitle()
        self._load_sessions_worker()

    # ── background loader ─────────────────────────────────────────────────────

    @work(thread=True, exclusive=True)
    def _load_sessions_worker(self, preserve_index: Optional[int] = None) -> None:
        try:
            rows = load_rows(
                show_all=self._show_all,
                project_root=self._project_root,
            )
            self.call_from_thread(self._populate_list, rows, preserve_index)
        except Exception as exc:
            self.call_from_thread(
                self.query_one("#detail-content", Static).update,
                f"[red]Failed to load sessions:[/red]\n\n{escape(str(exc))}",
            )

    def _populate_list(
        self, rows: list[SessionRow], preserve_index: Optional[int]
    ) -> None:
        self._rows = rows
        lv = self.query_one("#session-list", ListView)
        lv.clear()
        for row in rows:
            lv.append(SessionItem(row))

        if not rows:
            label = "all projects" if self._show_all else self._project_root.name
            self.query_one("#detail-content", Static).update(
                f"[dim]No sessions found for {escape(label)}.[/dim]\n\n{_HINT}"
            )
            return

        target = min(preserve_index or 0, len(rows) - 1)
        lv.index = target
        self._update_detail(rows[target])

    # ── subtitle ──────────────────────────────────────────────────────────────

    def _update_subtitle(self) -> None:
        if self._show_all:
            self.sub_title = "all sessions"
        else:
            self.sub_title = f"{self._project_root.name} sessions"

    # ── session list ──────────────────────────────────────────────────────────

    def _selected_row(self) -> Optional[SessionRow]:
        lv = self.query_one("#session-list", ListView)
        idx = lv.index
        if idx is not None and 0 <= idx < len(self._rows):
            return self._rows[idx]
        return None

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._rows):
            # Cancel any pending confirmation when user navigates away
            if self._pending_digest_row is not None:
                self._pending_digest_row = None
                self._pending_digest_force = False
            self._pending_report = None
            self._update_detail(self._rows[idx])

    def action_cursor_down(self) -> None:
        self.query_one("#session-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#session-list", ListView).action_cursor_up()

    def action_toggle_all(self) -> None:
        self._show_all = not self._show_all
        self._update_subtitle()
        self.query_one("#detail-content", Static).update(_LOADING)
        idx = self.query_one("#session-list", ListView).index
        self._load_sessions_worker(preserve_index=idx)

    def action_refresh_list(self) -> None:
        idx = self.query_one("#session-list", ListView).index
        self.query_one("#detail-content", Static).update(_LOADING)
        self._load_sessions_worker(preserve_index=idx)

    def action_open_detail(self) -> None:
        row = self._selected_row()
        if row:
            self._update_detail(row)

    def action_show_help(self) -> None:
        self.query_one("#detail-content", Static).update(
            "[bold cyan]devbrief TUI help[/bold cyan]\n\n"
            "Raw browsing is local-only and never calls Claude.\n"
            "AI briefs are optional and require explicit confirmation.\n\n"
            "[bold]Navigation[/bold]\n"
            "  ↑/↓ or j/k     Move selection\n"
            "  Enter          Open/focus detail\n"
            "  v              Toggle raw preview / AI brief\n"
            "  r              Refresh sessions\n"
            "  a              Toggle current project / all projects\n"
            "  q              Quit\n\n"
            "[bold]AI brief[/bold]\n"
            "  d or b          Show estimate, then ask before generating brief\n"
            "  y              Confirm pending brief/report action\n"
            "  n or Esc        Cancel pending action\n"
        )

    def action_toggle_raw_preview(self) -> None:
        self._show_raw_preview = not self._show_raw_preview
        row = self._selected_row()
        if row:
            self._update_detail(row)

    # ── detail pane ───────────────────────────────────────────────────────────

    def _update_detail(self, row: SessionRow) -> None:
        self.query_one("#detail-content", Static).update(self._render_row(row))
        self.query_one("#detail-pane", VerticalScroll).scroll_home(animate=False)

    def _render_row(self, row: SessionRow) -> str:
        if (
            row.status == "digested"
            and row.stored
            and row.stored.title
            and not self._show_raw_preview
        ):
            s = row.stored
            date_str = s.started_at.strftime("%Y-%m-%d %H:%M UTC")
            path_line = (
                f"\n[dim]Project path: {escape(s.project_path)}[/dim]"
                if s.project_path else ""
            )
            digested_line = ""
            if s.digested_at:
                digested_line = (
                    f"\n[dim]Briefed: {s.digested_at.strftime('%Y-%m-%d %H:%M UTC')}[/dim]"
                )
            return (
                f"[bold]{escape(s.title)}[/bold]\n"
                f"[dim]{escape(s.project_name)}  ·  {date_str}  ·  briefed[/dim]"
                f"{path_line}{digested_line}\n\n"
                f"[dim]Press [bold]v[/bold] to view raw local history preview.[/dim]\n\n"
                f"[bold cyan]Problem[/bold cyan]\n{escape(s.problem)}\n\n"
                f"[bold cyan]Approach[/bold cyan]\n{escape(s.approach)}\n\n"
                f"[bold cyan]Outcome[/bold cyan]\n{escape(s.outcome)}\n\n"
                f"[bold cyan]Brief[/bold cyan] [dim](AI-generated)[/dim]\n"
                f"{escape(s.summary)}"
            )

        date_str = (
            row.started_at.strftime("%Y-%m-%d %H:%M UTC")
            if row.started_at else "—"
        )
        updated = row.updated_at.strftime("%Y-%m-%d %H:%M UTC") if row.updated_at else "—"
        preview: dict = {}
        outcome: dict = {"status": "unknown", "completion": "unknown", "confidence": "low", "reason": ""}
        preview_error = ""
        if row.jsonl_path.exists():
            try:
                raw_messages = parser.extract_raw_messages(row.jsonl_path)
                preview = compactor.build_history_preview(raw_messages)
                outcome = compactor.infer_session_outcome(preview, raw_messages)
            except Exception as exc:
                preview_error = f"[red](could not read transcript: {escape(str(exc))})[/red]"
        else:
            preview_error = f"[red](JSONL file not found: {escape(str(row.jsonl_path))})[/red]"

        path_line = (
            f"\n[dim]Project path: {escape(row.project_path)}[/dim]"
            if row.project_path else ""
        )
        raw_user_requests = preview.get("user_requests", [])
        user_requests = _render_list_section(
            "User requests",
            raw_user_requests,
            limit=5,
        )
        if not raw_user_requests and preview.get("internal_model_prompts"):
            user_requests = (
                "[yellow]No human user request found. This session appears to be "
                "an internal tool/model run.[/yellow]\n"
                "[dim]Use `devbrief raw SESSION_ID --show-internal` to inspect "
                "internal prompts.[/dim]\n\n"
            )
        final = preview.get("final_assistant_text") or ""
        final_section = (
            f"[bold cyan]Final assistant message[/bold cyan]\n{escape(final)}\n\n"
            if final else ""
        )
        commands = _render_list_section(
            "Commands run",
            preview.get("commands", []),
            limit=8,
        )
        files = _render_list_section(
            "Files read/written/edited",
            preview.get("files", []),
            limit=12,
        )
        tools = _render_list_section(
            "Tool calls",
            preview.get("tools", []),
            limit=20,
        )
        errors = _render_list_section(
            "Error snippets",
            preview.get("errors", []),
            limit=4,
        )
        status = "briefed" if row.status == "digested" else "pending"
        outcome_section = (
            "[bold cyan]Session Outcome[/bold cyan]\n"
            f"Status: {escape(str(outcome.get('status', 'unknown')))}\n"
            f"Completion: {escape(str(outcome.get('completion', 'unknown')))}\n"
            f"Confidence: {escape(str(outcome.get('confidence', 'low')))}\n"
            f"Reason: {escape(str(outcome.get('reason', '')))}\n\n"
        )
        toggle_hint = (
            "[dim]Press [bold]v[/bold] to return to AI brief.[/dim]\n\n"
            if row.status == "digested" else ""
        )
        brief_footer = (
            "[dim]AI brief exists. Press [bold]v[/bold] to show it.[/dim]\n"
            if row.status == "digested"
            else f"[bold yellow]─── No AI brief generated yet ───[/bold yellow]\n{_TOKEN_WARNING}\n"
        )
        return (
            f"[bold]{escape(row.project_name)}  /  {row.session_id[:8]}[/bold]\n"
            f"[dim]Status: {status}  ·  Started: {date_str}  ·  Updated: {updated}  ·  "
            f"{row.turns} turns  ·  {row.touched_files_count} files[/dim]"
            f"{path_line}\n\n"
            f"[bold cyan]Raw History Preview[/bold cyan] [dim](local, free, no LLM)[/dim]\n\n"
            f"{toggle_hint}"
            f"{preview_error + chr(10) + chr(10) if preview_error else ''}"
            f"{outcome_section}"
            f"{user_requests}"
            f"{final_section}"
            f"{commands}"
            f"{files}"
            f"{tools}"
            f"{errors}"
            f"{brief_footer}"
        )

    # ── estimate ──────────────────────────────────────────────────────────────

    def action_estimate_selected(self) -> None:
        row = self._selected_row()
        if not row:
            return
        self._show_estimate(row, confirm_prompt=False)

    def _show_estimate(self, row: SessionRow, confirm_prompt: bool) -> None:
        self.query_one("#detail-content", Static).update(
            f"[bold]{escape(row.project_name)}  /  {row.session_id[:8]}[/bold]\n\n"
            "[dim]Calculating estimate…[/dim]"
        )
        self._estimate_worker(row, confirm_prompt)

    @work(thread=True)
    def _estimate_worker(self, row: SessionRow, confirm_prompt: bool) -> None:
        from . import compactor

        try:
            if not row.jsonl_path.exists():
                raise RuntimeError(f"JSONL file not found: {row.jsonl_path}")
            raw_messages = parser.extract_raw_messages(row.jsonl_path)
            session_meta = {
                "session_id": row.session_id,
                "project_name": row.project_name,
                "cwd": row.project_path,
                "user_turn_count": row.turns,
            }
            _packet, meta = compactor.build_evidence_packet(
                raw_messages, max_chars=16_000, session_meta=session_meta
            )
            self.call_from_thread(
                self._show_estimate_result, row, meta, confirm_prompt, None
            )
        except Exception as exc:
            self.call_from_thread(
                self._show_estimate_result, row, {}, confirm_prompt, str(exc)
            )

    def _show_estimate_result(
        self,
        row: SessionRow,
        meta: dict,
        confirm_prompt: bool,
        error: Optional[str],
    ) -> None:
        header = (
            f"[bold]{escape(row.project_name)}  /  {row.session_id[:8]}[/bold]\n\n"
        )
        if error:
            self.query_one("#detail-content", Static).update(
                header + f"[red]Estimate failed: {escape(error)}[/red]"
            )
            return

        trunc_str = (
            "[yellow]yes — transcript truncated[/yellow]"
            if meta.get("truncated") else "[green]no[/green]"
        )
        excluded = meta.get("excluded_counts", {})
        excl_lines = "\n".join(
            f"    {k:<28} {v}"
            for k, v in excluded.items()
            if v
        )
        excl_section = (
            f"\n[dim]Excluded/compressed:[/dim]\n{excl_lines}" if excl_lines else ""
        )

        body = (
            f"[bold cyan]Token estimate[/bold cyan]\n\n"
            f"  Raw transcript chars   {meta.get('raw_chars', 0):>10,}\n"
            f"  Compact evidence chars {meta.get('compact_chars', 0):>10,}\n"
            f"  Approx input tokens    {meta.get('estimated_tokens', 0):>10,}\n"
            f"  Truncated              {trunc_str}"
            f"{excl_section}\n\n"
        )

        if confirm_prompt:
            confirm_section = (
                f"[bold yellow]⚠ AI brief uses Claude tokens.[/bold yellow]\n"
                f"Press [bold]y[/bold] to generate brief  "
                f"[bold]n[/bold] / [bold]Esc[/bold] to cancel."
            )
        else:
            confirm_section = (
                "[dim]Press [bold]d[/bold] to generate brief (will ask for confirmation).[/dim]"
            )

        self.query_one("#detail-content", Static).update(
            header + body + confirm_section
        )

    # ── digest ────────────────────────────────────────────────────────────────

    def action_digest_selected(self) -> None:
        row = self._selected_row()
        if not row:
            return
        if row.status == "digested":
            self.notify("Already briefed. Press Shift+D to regenerate brief.", timeout=3)
            return
        self._start_digest_flow(row, force=False)

    def action_force_digest(self) -> None:
        row = self._selected_row()
        if not row:
            return
        self._start_digest_flow(row, force=True)

    def _start_digest_flow(self, row: SessionRow, force: bool) -> None:
        """Show estimate + confirmation prompt before spending tokens."""
        self._pending_digest_row = row
        self._pending_digest_force = force
        self._show_estimate(row, confirm_prompt=True)

    def action_confirm_digest(self) -> None:
        """User pressed y — proceed with brief/report generation."""
        if self._pending_report is not None:
            sessions, label = self._pending_report
            self._pending_report = None
            self.query_one("#detail-content", Static).update(
                f"[bold]Report: {label}[/bold]\n\n[yellow]⏳ Generating…[/yellow]"
            )
            self._report_worker(sessions, label)
            return
        if self._pending_digest_row is None:
            return
        row = self._pending_digest_row
        force = self._pending_digest_force
        self._pending_digest_row = None
        self._pending_digest_force = False
        self._kick_digest(row, force)

    def action_cancel_digest(self) -> None:
        """User pressed n — cancel pending brief."""
        if self._pending_report is not None:
            self._pending_report = None
            row = self._selected_row()
            if row:
                self._update_detail(row)
            self.notify("Report cancelled.", timeout=2)
            return
        if self._pending_digest_row is not None:
            self._pending_digest_row = None
            self._pending_digest_force = False
            row = self._selected_row()
            if row:
                self._update_detail(row)
            self.notify("Brief cancelled.", timeout=2)

    def action_cancel_or_quit(self) -> None:
        if self._pending_digest_row is not None:
            self.action_cancel_digest()
        elif self._pending_report is not None:
            self.action_cancel_digest()
        else:
            self.exit()

    def _kick_digest(self, row: SessionRow, force: bool) -> None:
        idx = self.query_one("#session-list", ListView).index
        self.query_one("#detail-content", Static).update(
            f"[bold]{escape(row.project_name)}  /  {row.session_id[:8]}[/bold]\n\n"
            "[yellow]⏳ Generating AI brief… this may take a moment.[/yellow]"
        )
        self._digest_worker(row, force, idx)

    @work(thread=True)
    def _digest_worker(
        self, row: SessionRow, force: bool, idx: Optional[int]
    ) -> None:
        from . import compactor, summarizer as sm

        try:
            if not row.jsonl_path.exists():
                raise RuntimeError(f"JSONL file not found: {row.jsonl_path}")

            raw_messages = parser.extract_raw_messages(row.jsonl_path)
            if not raw_messages:
                raise RuntimeError("No conversation messages found in this session.")

            session_meta = {
                "session_id": row.session_id,
                "project_name": row.project_name,
                "cwd": row.project_path,
                "user_turn_count": row.turns,
            }
            packet, _est = compactor.build_evidence_packet(
                raw_messages, max_chars=16_000, session_meta=session_meta
            )

            data = sm.summarize(packet, language=self._conf.get("language", "en"))

            meta = row.meta
            session = storage.Session(
                session_id=row.session_id,
                project_path=meta.project_path if meta else row.project_path,
                project_name=meta.project_name if meta else row.project_name,
                started_at=(
                    meta.started_at if meta
                    else (row.started_at or datetime.now(tz=timezone.utc))
                ),
                title=data["title"],
                problem=data["problem"],
                approach=data["approach"],
                outcome=data["outcome"],
                summary=data["summary"],
                raw_path=str(row.jsonl_path),
                status="digested",
                user_turn_count=row.turns,
                digested_at=datetime.now(tz=timezone.utc),
            )
            storage.upsert(session)

            row.status = "digested"
            row.title = data["title"]
            row.stored = session

            self.call_from_thread(self._on_digest_done, row, idx, None)
        except Exception as exc:
            self.call_from_thread(self._on_digest_done, row, idx, str(exc))

    def _on_digest_done(
        self, row: SessionRow, idx: Optional[int], error: Optional[str]
    ) -> None:
        if error:
            self.query_one("#detail-content", Static).update(
                f"[bold]{escape(row.project_name)}  /  {row.session_id[:8]}[/bold]\n\n"
            f"[red]❌ Brief generation failed:[/red]\n\n{escape(error)}"
            )
            self.notify(f"Brief failed: {error[:80]}", severity="error", timeout=6)
        else:
            self._load_sessions_worker(preserve_index=idx)
            self.notify(f"✓ {escape(row.title[:60])}", timeout=4)

    # ── reports ───────────────────────────────────────────────────────────────

    def action_report_today(self) -> None:
        today = date.today()
        self._generate_report(today, today, str(today))

    def action_report_week(self) -> None:
        today = date.today()
        start = today - timedelta(days=today.weekday())
        self._generate_report(start, today, f"{start} to {today}")

    def _generate_report(self, start: date, end: date, label: str) -> None:
        self.notify(
            "Reports are disabled. Use devbrief brief SESSION_ID for one-session AI briefs.",
            timeout=4,
        )
        return

        sessions = storage.get_by_date_range(start, end)
        if not sessions:
            self.notify(f"No briefed sessions for {label}", timeout=3)
            return
        self._pending_report = (sessions, label)
        self.query_one("#detail-content", Static).update(
            f"[bold]Report: {label}[/bold]\n\n"
            "[bold yellow]⚠ Report generation uses Claude tokens.[/bold yellow]\n"
            "Press [bold]y[/bold] to generate report  "
            "[bold]n[/bold] / [bold]Esc[/bold] to cancel."
        )

    @work(thread=True)
    def _report_worker(
        self, sessions: list[storage.Session], label: str
    ) -> None:
        from . import summarizer as sm

        try:
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
            text = sm.generate_report(
                session_dicts,
                period=label,
                language=self._conf.get("language", "en"),
            )
            self.call_from_thread(self._show_report, label, text, None)
        except Exception as exc:
            self.call_from_thread(self._show_report, label, "", str(exc))

    def _show_report(
        self, label: str, text: str, error: Optional[str]
    ) -> None:
        if error:
            content = (
                f"[bold]Report: {label}[/bold]\n\n"
                f"[red]Error generating report:[/red]\n{escape(error)}"
            )
        else:
            content = f"[bold]Report: {label}[/bold]\n\n{escape(text)}"
        self.query_one("#detail-content", Static).update(content)
        self.query_one("#detail-pane", VerticalScroll).scroll_home(animate=False)

    # ── quit ──────────────────────────────────────────────────────────────────

    def action_quit(self) -> None:
        self.exit()
