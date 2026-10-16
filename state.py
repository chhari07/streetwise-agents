"""Small JSON files that keep agent memory across restarts (a waiting deal, the buyer's area, shops seen).

Files live in ./state/ (git-ignored). STREETWISE_STATE_DIR moves them; test_flow.py uses a temp folder
so tests never touch the live agent's files."""
import json
import os
from pathlib import Path


def state_dir() -> Path:
    d = Path(os.getenv("STREETWISE_STATE_DIR") or Path(__file__).with_name("state"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def load(name: str) -> dict:
    try:
        return json.loads((state_dir() / name).read_text())
    except (OSError, ValueError):
        return {}


def save(name: str, data: dict) -> None:
    path = state_dir() / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)  # a crash mid-write never leaves a half-written file
