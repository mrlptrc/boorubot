"""Ritmo por site: padrão idêntico ao de sempre; atraso, dia sim/dia não e desligado por site."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from boorubot import worker as worker_mod
from boorubot.db import DB
from boorubot.sites.base import Result
from boorubot.web import app as app_mod
from boorubot.worker import Worker, publish_post, publish_site

PNG = (Path(__file__).resolve().parents[1] / "queue" / "exemplo-001" / "arte.png").read_bytes()
SITES = ["danbooru", "gelbooru", "paheal", "rule34us", "rule34xxx"]


class Fake:
    calls: list = []

    def __init__(self, name):
        self.name = name

    def publish(self, post, dry_run=True):
        assert dry_run is False
        Fake.calls.append(self.name)
        return Result(self.name, ok=True, dry_run=False, message="ok", url=f"https://{self.name}/1")


@pytest.fixture
def db(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    (images / "a.png").write_bytes(PNG)
    monkeypatch.setattr(app_mod, "IMAGES_DIR", images)
    monkeypatch.setattr("boorubot.db.IMAGES_DIR", images)
    Fake.calls = []
    monkeypatch.setattr(worker_mod, "build_site", lambda n, db=None, test=False: Fake(n))
    d = DB(tmp_path / "s.db")
    d.set_setting("test_mode", "0")
    d.set_setting("daily_time", "00:00")
    return d


def new_post(db, sites=SITES, **fields):
    return db.create_post("a.png", tags="x", sites=sites, **fields)


def sent(db, post_id):
    return sorted(p["site"] for p in db.publications(post_id) if p["status"] == "ok")


# --- padrão = comportamento de sempre --------------------------------------------------

def test_default_publishes_everywhere_at_once(db):
    post_id = new_post(db)
    assert publish_post(db, post_id) == "done"
    assert sorted(Fake.calls) == sorted(SITES)
    assert db.site_schedule() == []                    # padrão não usa a agenda por site
    assert db.last_published() is not None


def test_default_settings_values(db):
    for site in SITES:
        assert db.site_rhythm(site) == "diario" and db.site_delay(site) == 0 and db.site_follows_queue(site)
    db.set_setting("rhythm_gelbooru", "qualquer")      # valor inválido volta ao padrão
    db.set_setting("delay_gelbooru", "abc")
    assert db.site_rhythm("gelbooru") == "diario" and db.site_delay("gelbooru") == 0


# --- atraso -------------------------------------------------------------------------------

def test_delay_one_day_schedules_the_site(db):
    db.set_setting("delay_gelbooru", "1")
    post_id = new_post(db, ["danbooru", "gelbooru"])
    released_at = datetime.now()
    assert publish_post(db, post_id) == "publishing"   # falta o Gelbooru
    assert Fake.calls == ["danbooru"]
    [row] = db.site_schedule(post_id=post_id)
    assert row["site"] == "gelbooru" and row["status"] == "agendado"
    assert datetime.fromisoformat(row["due_at"]).date() == (released_at + timedelta(days=1)).date()

    assert db.next_site_due("gelbooru", released_at + timedelta(hours=1)) is None
    tomorrow = datetime.fromisoformat(row["due_at"]) + timedelta(minutes=1)
    assert db.next_site_due("gelbooru", tomorrow)["post_id"] == post_id

    lead_before = db.setting("last_published_at")
    assert publish_site(db, post_id, "gelbooru") == "ok"
    assert db.get_post(post_id)["status"] == "done"
    assert db.site_schedule(post_id=post_id)[0]["status"] == "publicado"
    # envio atrasado de um site não conta como "post do dia" da fila principal
    assert db.setting("last_published_at") == lead_before


def test_site_send_does_not_block_the_main_queue(db):
    db.set_setting("delay_gelbooru", "1")
    a, b = new_post(db, ["danbooru", "gelbooru"]), new_post(db, ["danbooru", "gelbooru"])
    db.approve(a)
    db.approve(b)
    publish_post(db, a)
    db.set_setting("last_published_at", (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds"))
    publish_site(db, a, "gelbooru")
    assert db.next_due()["id"] == b                    # a fila principal segue no ritmo dela


def test_all_sites_delayed_still_counts_as_day_zero(db):
    """Sem envio imediato, o dia 0 ainda conta: a fila não solta várias artes no mesmo dia."""
    for site in ("danbooru", "gelbooru"):
        db.set_setting(f"delay_{site}", "1")
    a, b = new_post(db, ["danbooru", "gelbooru"]), new_post(db, ["danbooru", "gelbooru"])
    db.approve(a)
    db.approve(b)
    publish_post(db, a)
    assert Fake.calls == [] and db.last_published() is not None
    assert db.next_due() is None


# --- dia sim, dia não ----------------------------------------------------------------------

def test_every_other_day(db):
    db.set_setting("rhythm_rule34us", "dia_sim_dia_nao")
    a, b = new_post(db, ["rule34us"]), new_post(db, ["rule34us"])
    publish_post(db, a)
    assert Fake.calls == ["rule34us"]                  # nada antes: sai no dia 0
    publish_post(db, b)
    [row] = db.site_schedule(post_id=b)
    assert row["status"] == "agendado"                 # hoje já saiu um: espera
    today = datetime.now().replace(hour=0, minute=30, second=0, microsecond=0)
    assert db.next_site_due("rule34us", today + timedelta(days=1)) is None
    assert db.next_site_due("rule34us", today + timedelta(days=2))["post_id"] == b


# --- desligado -------------------------------------------------------------------------------

def test_site_off_never_receives(db):
    db.set_setting("rhythm_paheal", "desligado")
    post_id = new_post(db, ["danbooru", "paheal"])
    assert publish_post(db, post_id) == "done"
    assert Fake.calls == ["danbooru"]
    assert db.site_schedule(post_id=post_id)[0]["status"] == "pulado"
    assert db.next_site_due("paheal", datetime.now() + timedelta(days=3)) is None
    publish_post(db, post_id)                          # "Tentar de novo" também não manda
    assert Fake.calls == ["danbooru"]


# --- nunca duplicar -------------------------------------------------------------------------

def test_never_duplicates_a_site(db):
    post_id = new_post(db, ["gelbooru"])
    db.add_publication(post_id, "gelbooru", "ok", "https://gelbooru/1")
    db.schedule_site(post_id, "gelbooru", datetime.now() - timedelta(days=1))
    assert publish_site(db, post_id, "gelbooru") == "ok"
    assert Fake.calls == []
    assert db.site_schedule(post_id=post_id)[0]["status"] == "publicado"


def test_manual_retry_of_failed_scheduled_site(db, monkeypatch):
    db.set_setting("delay_gelbooru", "1")
    post_id = new_post(db, ["danbooru", "gelbooru"])
    publish_post(db, post_id)
    db.set_schedule_status(post_id, "gelbooru", "falhou")
    publish_post(db, post_id)                          # "Tentar de novo"
    assert Fake.calls == ["danbooru", "gelbooru"]
    assert db.site_schedule(post_id=post_id)[0]["status"] == "publicado"


# --- PC desligado: atraso não acumula ---------------------------------------------------------

def test_overdue_site_rows_go_one_per_day(db):
    db.set_setting("delay_gelbooru", "1")
    old = datetime.now() - timedelta(days=5)
    ids = [new_post(db, ["gelbooru"]) for _ in range(3)]
    for pid in ids:
        db.schedule_site(pid, "gelbooru", old)
    now = datetime.now()
    first = db.next_site_due("gelbooru", now)
    assert first["post_id"] == ids[0]
    publish_site(db, ids[0], "gelbooru")
    assert db.next_site_due("gelbooru", now + timedelta(minutes=5)) is None  # 1 por dia no site
    tomorrow = now.replace(hour=0, minute=1) + timedelta(days=1)
    tomorrow = max(tomorrow, now + timedelta(hours=12, minutes=1))
    assert db.next_site_due("gelbooru", tomorrow)["post_id"] == ids[1]


# --- agendador ---------------------------------------------------------------------------------

def test_scheduler_one_send_at_a_time(db):
    db.set_setting("delay_gelbooru", "1")
    old = datetime.now() - timedelta(days=2)
    pid = new_post(db, ["gelbooru"])
    db.schedule_site(pid, "gelbooru", old)
    queued = new_post(db, ["danbooru"])
    db.approve(queued)
    w = Worker(db)
    w.tick()
    w.tick()
    assert [j.kind for j in w.jobs.queue] == ["publish"]    # a fila principal primeiro, um por vez
    w.jobs.get()
    w.tick()
    assert [j.kind for j in w.jobs.queue] == ["publish_site"]
    assert w.jobs.queue[0].target == f"{pid}:gelbooru"


def test_test_mode_pauses_site_sends(db):
    db.set_setting("test_mode", "1")
    pid = new_post(db, ["gelbooru"])
    db.schedule_site(pid, "gelbooru", datetime.now() - timedelta(days=1))
    w = Worker(db)
    w.tick()
    assert w.jobs.empty()
    assert publish_site(db, pid, "gelbooru") == "blocked" and Fake.calls == []
