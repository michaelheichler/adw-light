from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from . import session_state

DB_NAME = "principles.sqlite"

@dataclass(frozen=True, slots=True)
class Row:
    source: str
    entry_id: str
    title: str
    text: str


def _cache_path(root: Path | None) -> Path:
    cache_root = Path(root) if root is not None else session_state.plugin_data_home() / "cache"
    return cache_root / DB_NAME


def entry(entry_id: str, *, root: Path | None = None) -> Row | None:
    try:
        db_path = _cache_path(root)
        if not db_path.is_file():
            return None
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
        try:
            found = connection.execute(
                "SELECT source, entry_id, title, text FROM principle WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()
        finally:
            connection.close()
        return Row(*found) if found else None
    except Exception:
        return None
