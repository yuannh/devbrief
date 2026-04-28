from datetime import date, timedelta

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from . import storage

console = Console()


def print_session_list(sessions: list[storage.Session]) -> None:
    if not sessions:
        console.print("[dim]No sessions found.[/dim]")
        return

    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=4)
    table.add_column("ID", style="magenta", width=8, no_wrap=True)
    table.add_column("Date", width=11)
    table.add_column("Project", style="cyan", width=14)
    table.add_column("Title", style="bold")

    for i, s in enumerate(sessions, 1):
        table.add_row(
            str(i),
            s.session_id[:8],
            s.started_at.strftime("%Y-%m-%d"),
            s.project_name[:14],
            s.title[:80] if s.title else "[dim](pending)[/dim]",
        )

    console.print(table)


def print_history_list(rows: list[dict]) -> None:
    if not rows:
        console.print("[dim]No sessions found.[/dim]")
        return

    console.print(
        "[bold cyan]"
        f"{'ID':<8}  {'Status':<11}  {'Date':<10}  {'Project':<10}  {'Turns':>5}  Title"
        "[/bold cyan]"
    )
    console.print("[dim]" + "─" * 78 + "[/dim]")
    for row in rows:
        title = " ".join(str(row["title"]).split())
        if len(title) > 24:
            title = title[:23] + "…"
        project = str(row["project_name"])[:10]
        console.print(
            f"{row['session_id'][:8]:<8}  "
            f"{row['status']:<11}  "
            f"{row['date']:<10}  "
            f"{project:<10}  "
            f"{row['turns']:>5}  "
            f"{title}"
        )


def print_session_detail(s: storage.Session) -> None:
    date_str = s.started_at.strftime("%Y-%m-%d %H:%M UTC")
    updated = s.updated_at.strftime("%Y-%m-%d %H:%M UTC") if s.updated_at else ""
    status = "briefed" if s.status == "digested" else s.status

    console.print(Panel(f"[bold]{s.title or '(pending)'}[/bold]", subtitle=f"{s.project_name} · {date_str}"))
    console.print(f"[bold cyan]Session ID[/bold cyan] {s.session_id}")
    console.print(f"[bold cyan]Project[/bold cyan]    {s.project_name}")
    console.print(f"[bold cyan]Created at[/bold cyan] {date_str}")
    if updated:
        console.print(f"[bold cyan]Updated at[/bold cyan] {updated}")
    console.print(f"[bold cyan]Status[/bold cyan]     {status}")

    console.print("\n[bold cyan]Title[/bold cyan]")
    console.print(s.title or "")
    console.print("\n[bold cyan]Problem[/bold cyan]")
    console.print(s.problem)

    console.print("\n[bold cyan]Approach[/bold cyan]")
    console.print(s.approach)

    console.print("\n[bold cyan]Outcome[/bold cyan]")
    console.print(s.outcome)

    console.print("\n[bold cyan]Brief (AI-generated)[/bold cyan]")
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
