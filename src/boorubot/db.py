"""SQLite local: posts da fila, publicações por site e configurações do painel."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from boorubot.models import Post
from boorubot.sites.browser import ROOT

DATA_DIR = ROOT / "data"
IMAGES_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "boorubot.db"

# draft -> approved (na fila, enquanto falta algum site) -> done (todos os sites receberam)
#
# TESTE E PRODUÇÃO NUNCA SE MISTURAM: testar um post grava só em `test_runs` e não muda
# status, fila, published_at nem o ritmo. `publications` guarda apenas envios reais.
#
# Agendamento = uma fila por site (scheduler.py). Post aprovado não tem data: tem posição na
# fila, e cada site pega a próxima arte dele, 1 por dia, >= MIN_GAP entre posts do site, sem
# acumular atraso. Dias fixados no calendário (site_pins) passam na frente.
MIN_GAP = timedelta(hours=12)

# Ritmo por site: diário (padrão), dia sim/dia não ou desligado, e atraso em dias contado a
# partir do 1º site em que a arte saiu.
RHYTHMS = ("diario", "dia_sim_dia_nao", "desligado")
MAX_DELAY_DAYS = 7
POST_STATUSES = ("draft", "approved", "publishing", "done", "partial", "failed", "deleted")
# apagada: some de todas as listas, pode ser desfeita; o worker remove de vez depois deste prazo
PURGE_AFTER = timedelta(hours=24)

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
    published_at TEXT,      -- quando saiu de verdade em pelo menos um site
    approved_in_test INTEGER NOT NULL DEFAULT 0  -- aprovada com o modo teste ligado: precisa confirmar
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
CREATE TABLE IF NOT EXISTS site_schedule (
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    site TEXT NOT NULL,
    due_at TEXT NOT NULL,          -- a partir de quando o site pode receber; o pacer do site decide o dia
    status TEXT NOT NULL DEFAULT 'agendado',  -- agendado | publicado | falhou | pulado
    updated_at TEXT NOT NULL,
    PRIMARY KEY (post_id, site)
);
CREATE TABLE IF NOT EXISTS site_pins (
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    site TEXT NOT NULL,
    day TEXT NOT NULL,             -- dia fixado (arrastado no calendário) para esta arte neste site
    PRIMARY KEY (post_id, site)
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

# colunas que vieram depois da primeira versão (o DB.__init__ adiciona em bancos antigos)
NEW_POST_COLUMNS = (
    ("queue_pos", "REAL"),
    ("published_at", "TEXT"),
    # sugestão da IA: '' (nunca pedida) | pendente | analisando | pronta | falhou
    ("suggest_state", "TEXT NOT NULL DEFAULT ''"),
    ("suggest_error", "TEXT NOT NULL DEFAULT ''"),
    ("tag_scores", "TEXT NOT NULL DEFAULT '{}'"),  # {tag: confiança} das tags que a IA sugeriu
    ("weak_tags", "TEXT NOT NULL DEFAULT '[]'"),   # [[tag, confiança]] entre 0,25 e 0,35: só sugestão
    ("oc_touched", "INTEGER NOT NULL DEFAULT 0"),  # ela mexeu no OC: a IA não marca nem desmarca mais
    ("oc_auto", "INTEGER NOT NULL DEFAULT 0"),     # OC marcado pela IA (nenhum personagem conhecido)
    # rating '' = ninguém escolheu ainda (posts novos); a IA só SUGERE, em colunas próprias
    ("rating_suggestion", "TEXT NOT NULL DEFAULT ''"),
    ("rating_confidence", "REAL"),
    ("deleted_at", "TEXT"),                                  # quando foi apagada (status 'deleted')
    ("status_before_delete", "TEXT NOT NULL DEFAULT ''"),    # para o "desfazer"
    ("image_md5", "TEXT NOT NULL DEFAULT ''"),             # identifica upload repetido sem bloquear repost
)
SUGGEST_STATES = ("pendente", "analisando", "pronta", "falhou")

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
    "metrics_last_run": "",   # última coleta de métricas bem-sucedida
    "metrics_last_attempt": "",  # data da última tentativa: evita repetir a cada 30 s se a API cair
    "backup_dir": "",         # pasta de backup (vazio = backup desligado)
    "backup_last_attempt": "",
    "last_backup_at": "",
    "last_backup_ok": "",
    "last_backup_msg": "",
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
            for col, kind in NEW_POST_COLUMNS:
                if col not in cols:  # banco criado por versão anterior
                    self.conn.execute(f"ALTER TABLE posts ADD COLUMN {col} {kind}")
            # Backfill preguiçoso e idempotente: hash só das imagens antigas que ainda existem.
            rows = self.conn.execute("SELECT id, image FROM posts WHERE image_md5 = '' AND image != ''").fetchall()
            image_root = IMAGES_DIR.resolve()
            for row in rows:
                path = (IMAGES_DIR / row["image"]).resolve()
                if path.parent != image_root:
                    continue
                try:
                    digest = hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()
                except OSError:
                    continue
                self.conn.execute("UPDATE posts SET image_md5 = ? WHERE id = ?", (digest, row["id"]))
            if "approved_in_test" not in cols:
                self.conn.execute("ALTER TABLE posts ADD COLUMN approved_in_test INTEGER NOT NULL DEFAULT 0")
                # banco de antes da trava: com o modo teste ligado, o que está na fila foi aprovado
                # no teste e também precisa de confirmação antes de ir ao ar
                mode = self.conn.execute("SELECT value FROM settings WHERE key = 'test_mode'").fetchone()
                if mode is None or mode[0] == "1":
                    self.conn.execute("UPDATE posts SET approved_in_test = 1 WHERE status = 'approved'")
            # versões antigas consumiam o post no teste ("tested"); teste não muda mais o post
            self.conn.execute("UPDATE posts SET status = 'draft' WHERE status = 'tested'")
            self.conn.execute("DELETE FROM settings WHERE key = 'danbooru_env'")
            # fila por site: "publicando" (sites agendados do modelo antigo) volta para a fila
            self.conn.execute("UPDATE posts SET status = 'approved' WHERE status = 'publishing'")
            for key, value in DEFAULT_SETTINGS.items():
                self.conn.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (key, value))

    def backup_to(self, target: Path) -> None:
        """Cópia consistente do banco (API de backup do SQLite), segura com o painel rodando."""
        out = sqlite3.connect(target)
        try:
            with _lock:
                self.conn.backup(out)
        finally:
            out.close()

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
        for key in ("site_tags", "tag_scores", "weak_tags"):
            if key in fields:
                fields[key] = json.dumps(fields[key])
        if "original_character" in fields:
            fields["original_character"] = int(bool(fields["original_character"]))
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE posts SET {cols} WHERE id = ?", (*fields.values(), post_id))

    def set_suggest_state(self, post_id: int, state: str, error: str = "") -> None:
        self._exec("UPDATE posts SET suggest_state = ?, suggest_error = ? WHERE id = ?", (state, error, post_id))

    def posts_suggesting(self) -> list[int]:
        """Sugestões que não terminaram (o painel fechou no meio): o worker retoma ao iniciar."""
        return [r["id"] for r in self._all(
            "SELECT id FROM posts WHERE suggest_state IN ('pendente', 'analisando') ORDER BY id")]

    def delete_post(self, post_id: int) -> None:
        """Apagar = esconder (dá para desfazer). A remoção de verdade é purge_deleted()."""
        self._exec("UPDATE posts SET status_before_delete = status, status = 'deleted', deleted_at = ? "
                   "WHERE id = ? AND status != 'deleted'", (now(), post_id))

    def restore_post(self, post_id: int) -> bool:
        row = self.get_post(post_id)
        if not row or row["status"] != "deleted":
            return False
        self._exec("UPDATE posts SET status = ?, deleted_at = NULL, status_before_delete = '' WHERE id = ?",
                   (row["status_before_delete"] or "draft", post_id))
        return True

    def purge_deleted(self, when: datetime | None = None) -> list[str]:
        """Remove de vez o que foi apagado há mais de PURGE_AFTER. Devolve as imagens a apagar do disco.
        Arte que já saiu em algum site fica com a linha (escondida): o ritmo de cada site e a proteção
        contra post duplicado contam com esse histórico. Só a imagem vai embora."""
        cutoff = ((when or datetime.now()) - PURGE_AFTER).isoformat(timespec="seconds")
        rows = self._all("SELECT id, image FROM posts WHERE status = 'deleted' AND deleted_at <= ? AND image != ''",
                         (cutoff,))
        images = []
        for r in rows:
            if self._all("SELECT 1 FROM publications WHERE post_id = ? AND status = 'ok' LIMIT 1", (r["id"],)):
                self._exec("UPDATE posts SET image = '' WHERE id = ?", (r["id"],))
            else:
                self._exec("DELETE FROM posts WHERE id = ?", (r["id"],))
            images.append(r["image"])
        return images

    def get_post(self, post_id: int) -> dict | None:
        rows = self._all("SELECT * FROM posts WHERE id = ?", (post_id,))
        return _decode(rows[0]) if rows else None

    def find_image_duplicate(self, image_md5: str) -> dict | None:
        """Encontra uma arte igual ainda ativa ou que já foi publicada, sem impedir novo upload."""
        rows = self._all(
            "SELECT * FROM posts WHERE image_md5 = ? AND (status != 'deleted' OR published_at IS NOT NULL) "
            "ORDER BY published_at IS NULL, created_at DESC, id DESC LIMIT 1", (image_md5,))
        return _decode(rows[0]) if rows else None

    def list_posts(self, statuses: tuple[str, ...] | None = None) -> list[dict]:
        if statuses:
            marks = ",".join("?" * len(statuses))
            rows = self._all(f"SELECT * FROM posts WHERE status IN ({marks}) "
                             "ORDER BY queue_pos IS NULL, queue_pos, created_at DESC", statuses)
        else:  # sem filtro: tudo menos as apagadas
            rows = self._all("SELECT * FROM posts WHERE status != 'deleted' ORDER BY created_at DESC")
        return [_decode(r) for r in rows]

    # --- fila + limitador de ritmo -----------------------------------------------

    def queue(self) -> list[dict]:
        """Fila. Em produção, arte aprovada no modo teste fica DE FORA até alguém confirmar:
        desligar o modo teste nunca faz arte de teste ir ao ar sozinha."""
        guard = "" if self.test_mode else " AND approved_in_test = 0"
        return [_decode(r) for r in self._all(
            f"SELECT * FROM posts WHERE status = 'approved'{guard} ORDER BY queue_pos, id")]

    def approve(self, post_id: int) -> None:
        """Entra no fim da fila (marcada se for aprovada com o modo teste ligado)."""
        last = self._all("SELECT MAX(queue_pos) AS m FROM posts WHERE status = 'approved'")[0]["m"]
        self.update_post(post_id, status="approved", queue_pos=(last or 0) + 1,
                         approved_in_test=int(self.test_mode))

    def needs_confirmation(self) -> list[dict]:
        """Aprovadas no modo teste, esperando alguém confirmar que valem para produção."""
        return [_decode(r) for r in self._all(
            "SELECT * FROM posts WHERE status = 'approved' AND approved_in_test = 1 ORDER BY queue_pos, id")]

    def confirm_for_production(self, post_id: int) -> None:
        self.update_post(post_id, approved_in_test=0)

    def move_in_queue(self, post_id: int, where: str) -> None:
        agg = "MIN" if where == "first" else "MAX"
        edge = self._all(f"SELECT {agg}(queue_pos) AS v FROM posts WHERE status = 'approved'")[0]["v"] or 0
        self.update_post(post_id, queue_pos=edge - 1 if where == "first" else edge + 1)

    def move_to_position(self, post_id: int, position: int) -> None:
        """Põe a arte na posição `position` (1 = a próxima) e renumera a fila 1..N, sem buracos nem repetição.
        O pacer não muda: ele só lê a ordem."""
        ids = [r["id"] for r in self._all("SELECT id FROM posts WHERE status = 'approved' ORDER BY queue_pos, id")]
        if post_id not in ids:
            return
        ids.remove(post_id)
        ids.insert(max(0, min(position - 1, len(ids))), post_id)
        for pos, pid in enumerate(ids, start=1):
            self._exec("UPDATE posts SET queue_pos = ? WHERE id = ?", (float(pos), pid))

    def mark_post_published_at(self, post_id: int, when: datetime | None = None) -> None:
        """Primeira vez que a arte saiu em algum site (referência do atraso por site)."""
        when = when or datetime.now()
        row = self.get_post(post_id)
        if row and not row["published_at"]:
            self.update_post(post_id, published_at=when.isoformat(timespec="seconds"))

    def daily_slot(self, day: datetime) -> datetime:
        """O horário do post diário no dia de `day`."""
        hour, minute = (int(x) for x in self.setting("daily_time").split(":"))
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # --- ritmo por site (o agendador em si está em scheduler.py) ----------------

    def site_rhythm(self, site: str) -> str:
        value = self.setting(f"rhythm_{site}")
        return value if value in RHYTHMS else "diario"

    def site_delay(self, site: str) -> int:
        value = self.setting(f"delay_{site}")
        return min(int(value), MAX_DELAY_DAYS) if value.isdigit() else 0

    def site_day_gap(self, site: str) -> int:
        return 2 if self.site_rhythm(site) == "dia_sim_dia_nao" else 1

    def site_last_published(self, site: str) -> datetime | None:
        value = self._all("SELECT MAX(created_at) AS m FROM publications WHERE site = ? AND status = 'ok'",
                          (site,))[0]["m"]
        return datetime.fromisoformat(value) if value else None

    def site_backoff(self, site: str) -> datetime | None:
        """Depois de uma falha, o site só tenta de novo a partir daqui."""
        value = self.setting(f"backoff_{site}")
        return datetime.fromisoformat(value) if value else None

    def set_site_backoff(self, site: str, until: datetime | None) -> None:
        self.set_setting(f"backoff_{site}", until.isoformat(timespec="seconds") if until else "")

    def pending_sites(self, post: dict) -> list[str]:
        """Sites marcados na arte que ainda não receberam (nem pularam) e não estão desligados."""
        done = self.published_sites(post["id"])
        return [s for s in post["sites"] if s not in done and self.site_rhythm(s) != "desligado"]

    # --- dias fixados no calendário ----------------------------------------------

    def site_pins(self, site: str) -> dict[int, date]:
        return {r["post_id"]: date.fromisoformat(r["day"]) for r in self._all(
            "SELECT post_id, day FROM site_pins WHERE site = ?", (site,))}

    def post_pins(self, post_id: int) -> dict[str, date]:
        return {r["site"]: date.fromisoformat(r["day"]) for r in self._all(
            "SELECT site, day FROM site_pins WHERE post_id = ?", (post_id,))}

    def pin(self, post_id: int, site: str, day: date) -> None:
        """Fixa a arte num dia, naquele site. Se outra arte já estava fixada nesse dia, as duas trocam."""
        current = self.post_pins(post_id).get(site)
        for other_id, other_day in self.site_pins(site).items():
            if other_day == day and other_id != post_id:
                if current:
                    self._exec("UPDATE site_pins SET day = ? WHERE post_id = ? AND site = ?",
                               (current.isoformat(), other_id, site))
                else:
                    self.unpin(other_id, site)
        self._exec("INSERT OR REPLACE INTO site_pins (post_id, site, day) VALUES (?, ?, ?)",
                   (post_id, site, day.isoformat()))

    def unpin(self, post_id: int, site: str) -> None:
        self._exec("DELETE FROM site_pins WHERE post_id = ? AND site = ?", (post_id, site))

    # --- publicações -------------------------------------------------------------

    def add_publication(self, post_id: int, site: str, status: str, url: str = "", message: str = "") -> None:
        self._exec("INSERT INTO publications (post_id, site, created_at, status, url, message) "
                   "VALUES (?, ?, ?, ?, ?, ?)", (post_id, site, now(), status, url, message))

    def publications(self, post_id: int | None = None, limit: int = 100) -> list[dict]:
        if post_id is not None:
            return self._all("SELECT * FROM publications WHERE post_id = ? ORDER BY id DESC", (post_id,))
        return self._all("SELECT p.*, posts.title, posts.image, posts.status AS post_status FROM publications p "
                         "JOIN posts ON posts.id = p.post_id ORDER BY p.id DESC LIMIT ?", (limit,))

    def all_publications(self) -> list[dict]:
        """Todas as publicações, da mais antiga para a mais nova (tela Artes: uma consulta só)."""
        return self._all("SELECT * FROM publications ORDER BY id")

    def published_sites(self, post_id: int) -> set[str]:
        """Sites que NÃO recebem mais esta arte automaticamente: já saiu, foi pulada pela regra,
        ou o último envio ficou incerto (pode ter saído: só uma pessoa decide, para não duplicar)."""
        rows = self._all("SELECT site, status FROM publications WHERE post_id = ? ORDER BY id", (post_id,))
        latest = {r["site"]: r["status"] for r in rows}
        ever = {r["site"] for r in rows if r["status"] in ("ok", "skipped")}
        return ever | {site for site, status in latest.items() if status == "uncertain"}

    def uncertain_sites(self, post_id: int) -> list[str]:
        """Sites em que o último envio desta arte ficou incerto e ninguém conferiu ainda."""
        rows = self._all("SELECT site, status FROM publications WHERE post_id = ? ORDER BY id", (post_id,))
        latest = {r["site"]: r["status"] for r in rows}
        ok = {r["site"] for r in rows if r["status"] == "ok"}
        return [s for s, st in latest.items() if st == "uncertain" and s not in ok]

    def failures_in_a_row(self, post_id: int, site: str) -> int:
        """Falhas seguidas (as mais recentes) desta arte neste site."""
        count = 0
        for r in self._all("SELECT status FROM publications WHERE post_id = ? AND site = ? ORDER BY id DESC",
                           (post_id, site)):
            if r["status"] != "failed":
                break
            count += 1
        return count

    # --- métricas (só produção) ----------------------------------------------------

    def publications_for_metrics(self, site: str) -> list[dict]:
        return self._all("SELECT * FROM publications WHERE site = ? AND status = 'ok' AND url != ''", (site,))

    def add_metric_snapshot(self, publication_id: int, captured_at: str, score, up_score, down_score,
                            fav_count) -> None:
        self._exec("INSERT INTO metric_snapshots (publication_id, captured_at, score, up_score, down_score, "
                   "fav_count) VALUES (?, ?, ?, ?, ?, ?)",
                   (publication_id, captured_at, score, up_score, down_score, fav_count))

    _VISIBLE_SNAPSHOTS = ("SELECT m.* FROM metric_snapshots m JOIN publications p ON p.id = m.publication_id "
                          "JOIN posts ON posts.id = p.post_id WHERE posts.status != 'deleted'")

    def metric_snapshots_since(self, since: datetime) -> list[dict]:
        return self._all(self._VISIBLE_SNAPSHOTS + " AND m.captured_at >= ? ORDER BY m.captured_at, m.id",
                         (since.isoformat(timespec="seconds"),))

    def all_metric_snapshots(self) -> list[dict]:
        return self._all(self._VISIBLE_SNAPSHOTS + " ORDER BY m.captured_at, m.id")

    def publications_with_posts(self) -> list[dict]:
        """Para as métricas: arte apagada não conta."""
        return self._all("SELECT p.*, posts.title, posts.image FROM publications p "
                         "JOIN posts ON posts.id = p.post_id WHERE posts.status != 'deleted' ORDER BY p.id")

    def has_metrics(self) -> bool:
        return bool(self._all("SELECT 1 FROM metric_snapshots LIMIT 1"))

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
    row["tag_scores"] = json.loads(row.get("tag_scores") or "{}")
    row["weak_tags"] = json.loads(row.get("weak_tags") or "[]")
    row["original_character"] = bool(row["original_character"])
    row["approved_in_test"] = bool(row.get("approved_in_test"))
    row["oc_touched"] = bool(row.get("oc_touched"))
    row["oc_auto"] = bool(row.get("oc_auto"))
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
