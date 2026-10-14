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

# draft -> approved (na fila) -> publishing -> done | partial | failed
#
# TESTE E PRODUÇÃO NUNCA SE MISTURAM: testar um post grava só em `test_runs` e não muda
# status, fila, published_at nem o ritmo. `publications` guarda apenas envios reais.
#
# Agendamento = fila + limitador de ritmo. Post aprovado não tem data: tem posição na fila.
# O "pacer" (next_due) libera o primeiro da fila quando: já passou do horário diário, ainda
# não saiu post hoje e o último saiu há >= MIN_GAP. Atraso (PC desligado) nunca acumula:
# ao voltar sai 1 post e o resto segue 1 por dia. As datas na tela são só previsões.
MIN_GAP = timedelta(hours=12)
POST_STATUSES = ("draft", "approved", "publishing", "done", "partial", "failed")

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
    scheduled_for TEXT,     -- legado (agendamento por data fixa); não é mais usado
    queue_pos REAL,         -- posição na fila (só posts aprovados)
    published_at TEXT       -- quando saiu de verdade em pelo menos um site
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
CREATE TABLE IF NOT EXISTS test_runs (
    id INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    site TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,          -- dry_run (simulado) | test_upload (enviado ao testbooru) | skipped | failed
    url TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS metric_snapshots (
    id INTEGER PRIMARY KEY,
    publication_id INTEGER NOT NULL REFERENCES publications(id) ON DELETE CASCADE,
    captured_at TEXT NOT NULL,
    score INTEGER,
    up_score INTEGER,
    down_score INTEGER,
    fav_count INTEGER
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
    "danbooru_login": "",           # conta REAL (produção, danbooru.donmai.us)
    "danbooru_api_key": "",
    "danbooru_test_login": "",      # conta de TESTE (testbooru), opcional, só para testes
    "danbooru_test_api_key": "",
    "danbooru_level": "",          # preenchido pelo teste de conexão (nível real da conta)
    "danbooru_level_string": "",
    "danbooru_force_pending": "0",  # 1 = mesmo Contributor, manda para a fila de moderação
    "last_published_at": "",  # último post real: base do limitador de ritmo
    "metrics_last_run": "",   # última coleta de métricas (1x por dia)
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
            cols = {r[1] for r in self.conn.execute("PRAGMA table_info(posts)")}
            for col, kind in (("queue_pos", "REAL"), ("published_at", "TEXT")):
                if col not in cols:  # banco criado por versão anterior
                    self.conn.execute(f"ALTER TABLE posts ADD COLUMN {col} {kind}")
            # versões antigas consumiam o post no teste ("tested"); teste não muda mais o post
            self.conn.execute("UPDATE posts SET status = 'draft' WHERE status = 'tested'")
            self.conn.execute("DELETE FROM settings WHERE key = 'danbooru_env'")
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
                             "ORDER BY queue_pos IS NULL, queue_pos, created_at DESC", statuses)
        else:
            rows = self._all("SELECT * FROM posts ORDER BY created_at DESC")
        return [_decode(r) for r in rows]

    # --- fila + limitador de ritmo -----------------------------------------------

    def queue(self) -> list[dict]:
        return [_decode(r) for r in self._all(
            "SELECT * FROM posts WHERE status = 'approved' ORDER BY queue_pos, id")]

    def approve(self, post_id: int) -> None:
        """Entra no fim da fila."""
        last = self._all("SELECT MAX(queue_pos) AS m FROM posts WHERE status = 'approved'")[0]["m"]
        self.update_post(post_id, status="approved", queue_pos=(last or 0) + 1)

    def move_in_queue(self, post_id: int, where: str) -> None:
        agg = "MIN" if where == "first" else "MAX"
        edge = self._all(f"SELECT {agg}(queue_pos) AS v FROM posts WHERE status = 'approved'")[0]["v"] or 0
        self.update_post(post_id, queue_pos=edge - 1 if where == "first" else edge + 1)

    def last_published(self) -> datetime | None:
        value = self.setting("last_published_at")
        return datetime.fromisoformat(value) if value else None

    def mark_published(self, post_id: int, when: datetime | None = None) -> None:
        when = when or datetime.now()
        row = self.get_post(post_id)
        if row and not row["published_at"]:
            self.update_post(post_id, published_at=when.isoformat(timespec="seconds"))
        last = self.last_published()
        if last is None or when > last:
            self.set_setting("last_published_at", when.isoformat(timespec="seconds"))

    def _daily_slot(self, day: datetime) -> datetime:
        hour, minute = (int(x) for x in self.setting("daily_time").split(":"))
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def _earliest_after(self, ref: datetime, last: datetime | None) -> datetime:
        """Menor instante >= ref em que o pacer libera um post:
        depois do horário do dia, nenhum post nesse dia e >= MIN_GAP desde o último."""
        day = ref.date()
        while True:
            slot = self._daily_slot(datetime.combine(day, ref.time()))
            if last and last.date() >= day:
                day += timedelta(days=1)
                continue
            start = max(ref, slot)
            if last:
                start = max(start, last + MIN_GAP)
            if start.date() != day:  # o intervalo mínimo empurrou para outro dia
                day = start.date()
                ref = max(ref, start)
                continue
            return start

    def slot_open(self, ref: datetime | None = None) -> bool:
        ref = ref or datetime.now()
        return self._earliest_after(ref, self.last_published()) <= ref

    def next_due(self, ref: datetime | None = None) -> dict | None:
        """O pacer: devolve no máximo UM post, e só se o ritmo permitir agora."""
        queue = self.queue()
        return queue[0] if queue and self.slot_open(ref) else None

    def projected_times(self, ref: datetime | None = None) -> dict[int, datetime]:
        """Previsão de quando cada post da fila sai, mantendo o ritmo de 1 por dia."""
        ref = ref or datetime.now()
        last, out = self.last_published(), {}
        cursor = ref
        for row in self.queue():
            when = self._earliest_after(cursor, last)
            out[row["id"]] = when
            last, cursor = when, when
        return out

    def projected_next(self, ref: datetime | None = None) -> datetime:
        """Quando sairia um post aprovado agora (entrando no fim da fila)."""
        ref = ref or datetime.now()
        times = self.projected_times(ref)
        if not times:
            return self._earliest_after(ref, self.last_published())
        last = max(times.values())
        return self._earliest_after(last, last)

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

    # --- métricas (só produção) ----------------------------------------------------

    def publications_for_metrics(self, site: str) -> list[dict]:
        return self._all("SELECT * FROM publications WHERE site = ? AND status = 'ok' AND url != ''", (site,))

    def add_metric_snapshot(self, publication_id: int, captured_at: str, score, up_score, down_score,
                            fav_count) -> None:
        self._exec("INSERT INTO metric_snapshots (publication_id, captured_at, score, up_score, down_score, "
                   "fav_count) VALUES (?, ?, ?, ?, ?, ?)",
                   (publication_id, captured_at, score, up_score, down_score, fav_count))

    def latest_metrics(self) -> dict[int, dict]:
        rows = self._all("SELECT m.* FROM metric_snapshots m JOIN (SELECT publication_id, MAX(id) AS id "
                         "FROM metric_snapshots GROUP BY publication_id) last ON last.id = m.id")
        return {r["publication_id"]: r for r in rows}

    # --- testes (separados da produção) -------------------------------------------

    def add_test_run(self, post_id: int, site: str, status: str, url: str = "", message: str = "") -> None:
        self._exec("INSERT INTO test_runs (post_id, site, created_at, status, url, message) "
                   "VALUES (?, ?, ?, ?, ?, ?)", (post_id, site, now(), status, url, message))

    def test_runs(self, post_id: int | None = None) -> list[dict]:
        if post_id is not None:
            return self._all("SELECT * FROM test_runs WHERE post_id = ? ORDER BY id DESC", (post_id,))
        return self._all("SELECT * FROM test_runs ORDER BY id DESC")

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
