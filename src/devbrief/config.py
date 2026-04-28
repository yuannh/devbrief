import os
import tomllib
import tomli_w
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "devbrief"
CONFIG_FILE = CONFIG_DIR / "config.toml"
DATA_DIR = Path.home() / ".local" / "share" / "devbrief"
DB_FILE = DATA_DIR / "sessions.db"
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

_DEFAULTS: dict = {
    "api_key": "",
    "language": "en",
    "model": "claude-sonnet-4-6",
}


def load() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "rb") as f:
            stored = tomllib.load(f)
        return {**_DEFAULTS, **stored}
    return dict(_DEFAULTS)


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "wb") as f:
        tomli_w.dump(cfg, f)


def get_api_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY") or load().get("api_key", "")
