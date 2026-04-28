from datetime import date, timedelta

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from . import storage, summarizer as summ

console = Console()


def print_session_list(sessions: list[storage.Session]) -> None:
    if not sessions:
        console.print("[dim]No sessions found.[/dim]")
        return

    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=4)
    table.add_column("Date", width=11)
    table.add_column("Project", style="cyan", width=18)
    table.add_column("Title", style="bold")
    table.add_column("Outcome", style="dim")

    for i, s in enumerate(sessions, 1):
        table.add_row(
            str(i),
            s.started_at.strftime("%Y-%m-%d"),
            s.project_name[:18],
            s.title[:55] if s.title else "[dim]—[/dim]",
            s.outcome[:60] if s.outcome else "",
        )

    console.print(table)


def print_session_detail(s: storage.Session) -> None:
    date_str = s.started_at.strftime("%Y-%m-%d %H:%M UTC")

    console.print(Panel(f"[bold]{s.title}[/bold]", subtitle=f"{s.project_name} · {date_str}"))

    console.print("\n[bold cyan]Problem[/bold cyan]")
    console.print(s.problem)

    console.print("\n[bold cyan]Approach[/bold cyan]")
    console.print(s.approach)

    console.print("\n[bold cyan]Outcome[/bold cyan]")
    console.print(s.outcome)

    console.print("\n[bold cyan]Summary (non-technical)[/bold cyan]")
    console.print(Panel(s.summary, border_style="green"))


def print_report(text: str, period: str) -> None:
    console.print(Panel(text, title=f"[bold]Dev Report — {period}[/bold]", border_style="blue"))


def get_period_range(period: str, specific_date: str | None = None) -> tuple[date, date]:
    today = date.today()
    if specific_date:
        d = date.fromisoformat(specific_date)
        return d, d
    if period == "today":
        return today, today
    if period == "week":
        start = today - timedelta(days=today.weekday())
        return start, today
    if period == "month":
        start = today.replace(day=1)
        return start, today
    return today, today
