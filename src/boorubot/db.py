"""SQLite local: posts da fila, publicações por site e configurações do painel."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from boorubot.models import Post
from boorubot.sites.browser import ROOT

DATA_DIR = ROOT / "data"
IMAGES_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "boorubot.db"

# draft -> approved (agendado) -> publishing -> done | partial | failed | tested (modo teste)
POST_STATUSES = ("draft", "approved", "publishing", "done", "partial", "failed", "tested")

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    image TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    rating TEXT NOT NULL DEFAULT 'general',
    source TEXT NOT NULL DEFAULT '',
    commentary_title TEXT NOT NULL DEFAULT '',
    commentary_description TEXT NOT NULL DEFAULT '',
    original_character INTEGER NOT NULL DEFAULT 0,
    sites TEXT NOT NULL DEFAULT '[]',
    site_tags TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'draft',
    scheduled_for TEXT
);
CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    site TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,          -- ok | dry_run | skipped | failed
    url TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS site_sessions (
    site TEXT PRIMARY KEY,
    checked_at TEXT NOT NULL,
    ok INTEGER NOT NULL,
    message TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "test_mode": "1",       # 1 = nada é enviado de verdade (dry-run)
    "daily_time": "18:00",  # horário do post diário
    "danbooru_env": "test",  # test = testbooru, live = danbooru de verdade
    "danbooru_login": "",
    "danbooru_api_key": "",
    "default_tags": "",      # entram em todo post novo (ex.: nome de artista, commission)
    "default_sites": json.dumps(["danbooru", "gelbooru", "paheal", "rule34us", "rule34xxx"]),
}

_lock = threading.Lock()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        with _lock, self.conn:
            self.conn.executescript(SCHEMA)
            for key, value in DEFAULT_SETTINGS.items():
                self.conn.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (key, value))

    def _exec(self, sql: str, params=()) -> sqlite3.Cursor:
        with _lock, self.conn:
            return self.conn.execute(sql, params)

    def _all(self, sql: str, params=()) -> list[dict]:
        with _lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    # --- settings ----------------------------------------------------------------

    def setting(self, key: str) -> str:
        rows = self._all("SELECT value FROM settings WHERE key = ?", (key,))
        return rows[0]["value"] if rows else DEFAULT_SETTINGS.get(key, "")

    def set_setting(self, key: str, value: str) -> None:
        self._exec("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))

    @property
    def test_mode(self) -> bool:
        return self.setting("test_mode") == "1"

    # --- posts -------------------------------------------------------------------

    def create_post(self, image: str, **fields) -> int:
        cur = self._exec("INSERT INTO posts (created_at, image) VALUES (?, ?)", (now(), image))
        if fields:
            self.update_post(cur.lastrowid, **fields)
        return cur.lastrowid

    def update_post(self, post_id: int, **fields) -> None:
        if "sites" in fields:
            fields["sites"] = json.dumps(list(fields["sites"]))
        if "site_tags" in fields:
            fields["site_tags"] = json.dumps(fields["site_tags"])
        if "original_character" in fields:
            fields["original_character"] = int(bool(fields["original_character"]))
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE posts SET {cols} WHERE id = ?", (*fields.values(), post_id))

    def delete_post(self, post_id: int) -> None:
        self._exec("DELETE FROM posts WHERE id = ?", (post_id,))

    def get_post(self, post_id: int) -> dict | None:
        rows = self._all("SELECT * FROM posts WHERE id = ?", (post_id,))
        return _decode(rows[0]) if rows else None

    def list_posts(self, statuses: tuple[str, ...] | None = None) -> list[dict]:
        if statuses:
            marks = ",".join("?" * len(statuses))
            rows = self._all(f"SELECT * FROM posts WHERE status IN ({marks}) "
                             "ORDER BY COALESCE(scheduled_for, created_at)", statuses)
        else:
            rows = self._all("SELECT * FROM posts ORDER BY created_at DESC")
        return [_decode(r) for r in rows]

    def approve(self, post_id: int, when: datetime | None = None) -> datetime:
        """Agenda no próximo horário diário livre (1 post por dia)."""
        when = when or self.next_free_slot()
        self.update_post(post_id, status="approved", scheduled_for=when.isoformat(timespec="minutes"))
        return when

    def next_free_slot(self, ref: datetime | None = None) -> datetime:
        ref = ref or datetime.now()
        hour, minute = (int(x) for x in self.setting("daily_time").split(":"))
        taken = {r["scheduled_for"][:10] for r in self._all(
            "SELECT scheduled_for FROM posts WHERE status IN ('approved', 'publishing') AND scheduled_for IS NOT NULL")}
        taken |= {r["day"] for r in self._all(
            "SELECT DISTINCT substr(created_at, 1, 10) AS day FROM publications WHERE status = 'ok'")}
        slot = ref.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if slot <= ref:
            slot += timedelta(days=1)
        while slot.date().isoformat() in taken:
            slot += timedelta(days=1)
        return slot

    def due_posts(self, ref: datetime | None = None) -> list[dict]:
        ref = (ref or datetime.now()).isoformat(timespec="minutes")
        return [_decode(r) for r in self._all(
            "SELECT * FROM posts WHERE status = 'approved' AND scheduled_for <= ? ORDER BY scheduled_for", (ref,))]

    # --- publicações -------------------------------------------------------------

    def add_publication(self, post_id: int, site: str, status: str, url: str = "", message: str = "") -> None:
        self._exec("INSERT INTO publications (post_id, site, created_at, status, url, message) "
                   "VALUES (?, ?, ?, ?, ?, ?)", (post_id, site, now(), status, url, message))

    def publications(self, post_id: int | None = None, limit: int = 100) -> list[dict]:
        if post_id is not None:
            return self._all("SELECT * FROM publications WHERE post_id = ? ORDER BY id DESC", (post_id,))
        return self._all("SELECT p.*, posts.title, posts.image FROM publications p "
                         "JOIN posts ON posts.id = p.post_id ORDER BY p.id DESC LIMIT ?", (limit,))

    def published_sites(self, post_id: int) -> set[str]:
        """Sites onde o post já saiu (ou foi pulado por regra) — não reenvia."""
        return {r["site"] for r in self._all(
            "SELECT site FROM publications WHERE post_id = ? AND status IN ('ok', 'skipped')", (post_id,))}

    # --- sessões -----------------------------------------------------------------

    def set_session(self, site: str, ok: bool, message: str) -> None:
        self._exec("INSERT OR REPLACE INTO site_sessions VALUES (?, ?, ?, ?)", (site, now(), int(ok), message))

    def sessions(self) -> dict[str, dict]:
        return {r["site"]: r for r in self._all("SELECT * FROM site_sessions")}


def _decode(row: dict) -> dict:
    row = dict(row)
    row["sites"] = json.loads(row["sites"])
    row["site_tags"] = json.loads(row["site_tags"])
    row["original_character"] = bool(row["original_character"])
    row["tag_list"] = row["tags"].split()
    return row


def to_post(row: dict) -> Post:
    image = IMAGES_DIR / row["image"]
    return Post(
        folder=image.parent,
        image=image,
        tags=row["tags"].split(),
        rating=row["rating"],
        source=row["source"],
        title=row["title"],
        commentary_title=row["commentary_title"],
        commentary_description=row["commentary_description"],
        original_character=row["original_character"],
        sites=row["sites"],
        site_tags=row["site_tags"],
    )
