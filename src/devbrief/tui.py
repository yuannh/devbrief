from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static
from textual import work

from . import config as cfg, parser, storage


# ── view model ────────────────────────────────────────────────────────────────

@dataclass
class SessionRow:
    session_id: str
    jsonl_path: Path
    project_name: str
    started_at: datetime | None
    turns: int
    status: Literal["digested", "pending"]
    title: str
    stored: storage.Session | None
    meta: parser.SessionMeta | None


def load_rows() -> list[SessionRow]:
    discovered = parser.get_all_sessions()
    digested_map = {s.session_id: s for s in storage.get_all(limit=2000)}
    rows: list[SessionRow] = []
    for meta in discovered:
        stored = digested_map.get(meta.session_id)
        rows.append(SessionRow(
            session_id=meta.session_id,
            jsonl_path=meta.jsonl_path,
            project_name=meta.project_name,
            started_at=meta.started_at,
            turns=meta.user_turn_count,
            status="digested" if stored else "pending",
            title=stored.title if stored else "",
            stored=stored,
            meta=meta,
        ))
    return rows


# ── widgets ───────────────────────────────────────────────────────────────────

class SessionItem(ListItem):
    def __init__(self, row: SessionRow) -> None:
        super().__init__()
        self.row = row

    def compose(self) -> ComposeResult:
        icon = "✓" if self.row.status == "digested" else "○"
        color = "green" if self.row.status == "digested" else "yellow"
        date_str = self.row.started_at.strftime("%m-%d %H:%M") if self.row.started_at else "—"
        proj = escape(self.row.project_name[:20])

        yield Label(f"[{color}]{icon}[/{color}]  {proj:<20}  [dim]{date_str}[/dim]")

        if self.row.status == "digested" and self.row.title:
            short = escape(self.row.title[:46] + ("…" if len(self.row.title) > 46 else ""))
            yield Label(f"   [dim italic]{short}[/dim italic]")
        else:
            yield Label(f"   [dim]{self.row.session_id[:8]}  ·  {self.row.turns} turns[/dim]")


# ── app ───────────────────────────────────────────────────────────────────────

class DevbriefApp(App):
    TITLE = "devbrief"
    SUB_TITLE = "dev session log"

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
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("d", "digest_selected", "Digest"),
        Binding("D", "force_digest", "Re-digest", show=False),
        Binding("r", "report_today", "Today"),
        Binding("w", "report_week", "Week"),
        Binding("R", "refresh_list", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[SessionRow] = []
        self._conf = cfg.load()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            yield ListView(id="session-list")
            with VerticalScroll(id="detail-pane"):
                yield Static(
                    "[dim]Select a session to view details.[/dim]\n\n"
                    "[dim]↑↓ / jk  navigate    d  digest    r  today report"
                    "    w  week report    R  refresh    q  quit[/dim]",
                    id="detail-content",
                )
        yield Footer()

    def on_mount(self) -> None:
        self._load_sessions()

    # ── session list ──────────────────────────────────────────────────────────

    def _load_sessions(self, preserve_index: int | None = None) -> None:
        self._rows = load_rows()
        lv = self.query_one("#session-list", ListView)
        lv.clear()
        for row in self._rows:
            lv.append(SessionItem(row))

        if not self._rows:
            return

        target = min(preserve_index or 0, len(self._rows) - 1)
        lv.index = target
        self._update_detail(self._rows[target])

    def _selected_row(self) -> SessionRow | None:
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
            self._update_detail(self._rows[idx])

    def action_cursor_down(self) -> None:
        self.query_one("#session-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#session-list", ListView).action_cursor_up()

    # ── detail pane ──────────────────────────────────────────────────────────

    def _update_detail(self, row: SessionRow) -> None:
        self.query_one("#detail-content", Static).update(self._render_row(row))
        self.query_one("#detail-pane", VerticalScroll).scroll_home(animate=False)

    def _render_row(self, row: SessionRow) -> str:
        if row.status == "digested" and row.stored:
            s = row.stored
            date_str = s.started_at.strftime("%Y-%m-%d %H:%M UTC")
            return (
                f"[bold]{escape(s.title)}[/bold]\n"
                f"[dim]{escape(s.project_name)}  ·  {date_str}[/dim]\n\n"
                f"[bold cyan]Problem[/bold cyan]\n{escape(s.problem)}\n\n"
                f"[bold cyan]Approach[/bold cyan]\n{escape(s.approach)}\n\n"
                f"[bold cyan]Outcome[/bold cyan]\n{escape(s.outcome)}\n\n"
                f"[bold cyan]Summary[/bold cyan] [dim](non-technical)[/dim]\n{escape(s.summary)}"
            )

        date_str = row.started_at.strftime("%Y-%m-%d %H:%M UTC") if row.started_at else "—"
        preview_parts: list[str] = []
        try:
            msgs = parser.extract_messages(row.jsonl_path)
            for msg in msgs[:4]:
                label = "[bold]You[/bold]" if msg.role == "user" else "[bold green]Claude[/bold green]"
                body = escape(msg.text[:280] + ("…" if len(msg.text) > 280 else ""))
                preview_parts.append(f"{label}: {body}")
        except Exception as exc:
            preview_parts.append(f"[red](could not read transcript: {escape(str(exc))})[/red]")

        preview = "\n\n".join(preview_parts) or "[dim]Empty session.[/dim]"
        return (
            f"[bold]{escape(row.project_name)}  /  {row.session_id[:8]}[/bold]\n"
            f"[dim]{date_str}  ·  {row.turns} turns[/dim]\n\n"
            f"[bold cyan]Transcript Preview[/bold cyan]\n\n"
            f"{preview}\n\n"
            f"[dim]─── Not digested. Press [bold]d[/bold] to summarize. ───[/dim]"
        )

    # ── digest ────────────────────────────────────────────────────────────────

    def action_digest_selected(self) -> None:
        row = self._selected_row()
        if not row:
            return
        if row.status == "digested":
            self.notify("Already digested. Press Shift+D to re-digest.", timeout=3)
            return
        self._kick_digest(row, force=False)

    def action_force_digest(self) -> None:
        row = self._selected_row()
        if not row:
            return
        self._kick_digest(row, force=True)

    def _kick_digest(self, row: SessionRow, force: bool) -> None:
        idx = self.query_one("#session-list", ListView).index
        self.query_one("#detail-content", Static).update(
            f"[bold]{escape(row.project_name)}  /  {row.session_id[:8]}[/bold]\n\n"
            "[yellow]⏳ Digesting session… this may take a moment.[/yellow]"
        )
        self._digest_worker(row, force, idx)

    @work(thread=True)
    def _digest_worker(self, row: SessionRow, force: bool, idx: int | None) -> None:
        from . import summarizer as sm

        try:
            messages = parser.extract_messages(row.jsonl_path)
            if not messages:
                raise RuntimeError("No conversation messages found in this session.")

            transcript = parser.format_for_ai(messages)
            data = sm.summarize(transcript, language=self._conf.get("language", "en"))

            meta = row.meta
            session = storage.Session(
                session_id=row.session_id,
                project_path=meta.project_path if meta else "",
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
            )
            storage.upsert(session)

            row.status = "digested"
            row.title = data["title"]
            row.stored = session

            self.call_from_thread(self._on_digest_done, row, idx, None)
        except Exception as exc:
            self.call_from_thread(self._on_digest_done, row, idx, str(exc))

    def _on_digest_done(
        self, row: SessionRow, idx: int | None, error: str | None
    ) -> None:
        if error:
            self.query_one("#detail-content", Static).update(
                f"[bold]{escape(row.project_name)}  /  {row.session_id[:8]}[/bold]\n\n"
                f"[red]❌ Digest failed:[/red]\n\n{escape(error)}"
            )
            self.notify(f"Digest failed: {error[:80]}", severity="error", timeout=6)
        else:
            self._load_sessions(preserve_index=idx)
            self.notify(f"✓ {escape(row.title[:60])}", timeout=4)

    # ── refresh ───────────────────────────────────────────────────────────────

    def action_refresh_list(self) -> None:
        idx = self.query_one("#session-list", ListView).index
        self._load_sessions(preserve_index=idx)
        self.notify("Refreshed", timeout=2)

    # ── reports ───────────────────────────────────────────────────────────────

    def action_report_today(self) -> None:
        today = date.today()
        self._generate_report(today, today, str(today))

    def action_report_week(self) -> None:
        today = date.today()
        start = today - timedelta(days=today.weekday())
        self._generate_report(start, today, f"{start} to {today}")

    def _generate_report(self, start: date, end: date, label: str) -> None:
        sessions = storage.get_by_date_range(start, end)
        if not sessions:
            self.notify(f"No digested sessions for {label}", timeout=3)
            return
        self.query_one("#detail-content", Static).update(
            f"[bold]Report: {label}[/bold]\n\n[yellow]⏳ Generating…[/yellow]"
        )
        self._report_worker(sessions, label)

    @work(thread=True)
    def _report_worker(self, sessions: list[storage.Session], label: str) -> None:
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

    def _show_report(self, label: str, text: str, error: str | None) -> None:
        if error:
            content = (
                f"[bold]Report: {label}[/bold]\n\n"
                f"[red]Error generating report:[/red]\n{escape(error)}"
            )
        else:
            content = f"[bold]Report: {label}[/bold]\n\n{escape(text)}"
        self.query_one("#detail-content", Static).update(content)
        self.query_one("#detail-pane", VerticalScroll).scroll_home(animate=False)
