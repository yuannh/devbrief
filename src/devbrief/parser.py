import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config as cfg


@dataclass
class SessionMeta:
    session_id: str
    project_path: str
    project_name: str
    jsonl_path: Path
    started_at: datetime
    user_turn_count: int


@dataclass
class Message:
    role: str
    text: str


def get_all_sessions() -> list[SessionMeta]:
    sessions = []
    projects_dir = cfg.CLAUDE_PROJECTS_DIR

    if not projects_dir.exists():
        return sessions

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl_file in project_dir.glob("*.jsonl"):
            meta = _read_meta(jsonl_file)
            if meta:
                sessions.append(meta)

    sessions.sort(key=lambda s: s.started_at, reverse=True)
    return sessions


def _read_meta(jsonl_path: Path) -> SessionMeta | None:
    session_id = jsonl_path.stem
    cwd = ""
    started_at = datetime.fromtimestamp(jsonl_path.stat().st_ctime, tz=timezone.utc)
    user_turns = 0

    try:
        with open(jsonl_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                t = d.get("type")

                if t == "user":
                    if not cwd and "cwd" in d:
                        cwd = d["cwd"]
                    if "timestamp" in d and user_turns == 0:
                        try:
                            started_at = datetime.fromisoformat(
                                d["timestamp"].replace("Z", "+00:00")
                            )
                        except ValueError:
                            pass
                    content = d.get("message", {}).get("content", "")
                    if isinstance(content, str) and content.strip():
                        user_turns += 1
    except Exception:
        return None

    if user_turns == 0:
        return None

    project_name = Path(cwd).name if cwd else jsonl_path.parent.name
    return SessionMeta(
        session_id=session_id,
        project_path=cwd,
        project_name=project_name,
        jsonl_path=jsonl_path,
        started_at=started_at,
        user_turn_count=user_turns,
    )


def extract_messages(jsonl_path: Path) -> list[Message]:
    messages: list[Message] = []

    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue

            t = d.get("type")

            if t == "user":
                content = d.get("message", {}).get("content", "")
                if isinstance(content, str) and content.strip():
                    messages.append(Message(role="user", text=content.strip()))

            elif t == "assistant":
                content = d.get("message", {}).get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "").strip()
                            if text:
                                messages.append(Message(role="assistant", text=text))
                                break

    return messages


list_sessions = get_all_sessions  # alias used by tui


def format_for_ai(messages: list[Message], max_chars: int = 40000) -> str:
    parts: list[str] = []
    total = 0

    for msg in messages:
        chunk = f"[{msg.role.upper()}]: {msg.text}"
        if total + len(chunk) > max_chars:
            parts.append("[... transcript truncated due to length ...]")
            break
        parts.append(chunk)
        total += len(chunk)

    return "\n\n".join(parts)
