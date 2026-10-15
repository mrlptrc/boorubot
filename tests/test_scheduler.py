"""Fila por site: cada site anda sozinho na fila, 1 por dia, sem rajada, sem duplicar."""

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from boorubot import worker as worker_mod
from boorubot.db import DB
from boorubot.scheduler import MAX_AUTO_TRIES, next_free_day, plan, site_due
from boorubot.sites.base import Result
from boorubot.web import app as app_mod
from boorubot.worker import Worker, publish_post, publish_site, record_rule_skips

PNG = (Path(__file__).resolve().parents[1] / "queue" / "exemplo-001" / "arte.png").read_bytes()
SITES = ["danbooru", "gelbooru", "paheal", "rule34us", "rule34xxx"]
NOW = datetime(2026, 10, 14, 12, 0)   # quarta, antes do horário (18:00)
TODAY = NOW.date()


class Fake:
    calls: list = []
    outcome: dict = {}

    def __init__(self, name):
        self.name = name

    def check_rules(self, post):
        pass

    def publish(self, post, dry_run=True):
        assert dry_run is False
        Fake.calls.append(self.name)
        how = Fake.outcome.get(self.name, "ok")
        if how == "boom":
            raise RuntimeError("caiu")
        if how == "uncertain":
            return Result(self.name, ok=False, dry_run=False, message="?", info={"uncertain": True})
        return Result(self.name, ok=True, dry_run=False, message="ok", url=f"https://{self.name}/1")


@pytest.fixture
def db(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    (images / "a.png").write_bytes(PNG)
    monkeypatch.setattr(app_mod, "IMAGES_DIR", images)
    monkeypatch.setattr("boorubot.db.IMAGES_DIR", images)
    Fake.calls, Fake.outcome = [], {}
    monkeypatch.setattr(worker_mod, "build_site", lambda n, db=None, test=False: Fake(n))
    d = DB(tmp_path / "s.db")
    d.set_setting("test_mode", "0")
    d.set_setting("daily_time", "18:00")
    return d


def art(db, title, sites=SITES, **fields):
    pid = db.create_post("a.png", title=title, tags="x", sites=sites, **fields)
    db.approve(pid)
    return pid


def sent_at(db, post_id, site, when):
    db.add_publication(post_id, site, "ok", f"https://{site}/1")
    db._exec("UPDATE publications SET created_at = ? WHERE id = (SELECT MAX(id) FROM publications)",
             (when.isoformat(timespec="seconds"),))


def days_of(plans, site, n=7):
    return [(c.post["title"] if c.post else c.kind) for d, c in sorted(plans[site].items())][:n]


# --- fila por site ----------------------------------------------------------------------------

def test_all_sites_marked_same_as_one_daily_queue(db):
    for t in ("a", "b", "c"):
        art(db, t)
    plans = plan(db, NOW, 5)
    for site in SITES:
        assert days_of(plans, site, 5) == ["a", "b", "c", "empty", "empty"]
    assert plans["danbooru"][TODAY].when == datetime(2026, 10, 14, 18, 0)


def test_different_art_same_day_on_different_sites(db):
    """O caso pedido: segunda o Ichigo no Danbooru/Gelbooru e outra arte no paheal."""
    art(db, "ichigo", ["danbooru", "gelbooru"])
    art(db, "kaneki", ["paheal"])
    plans = plan(db, NOW, 2)
    assert days_of(plans, "danbooru", 1) == ["ichigo"]
    assert days_of(plans, "gelbooru", 1) == ["ichigo"]
    assert days_of(plans, "paheal", 1) == ["kaneki"]


def test_before_daily_time_nothing_is_due(db):
    art(db, "a")
    assert site_due(db, "danbooru", NOW) is None
    assert site_due(db, "danbooru", NOW.replace(hour=18, minute=1))["title"] == "a"


def test_pc_off_for_days_one_per_site_per_day(db):
    """PC desligado vários dias: ao voltar, 1 por site; o resto segue 1 por dia (sem rajada)."""
    old = art(db, "velha")
    sent_at(db, old, "danbooru", datetime(2026, 10, 10, 18, 0))
    a, b = art(db, "a", ["danbooru"]), art(db, "b", ["danbooru"])
    back = datetime(2026, 10, 14, 19, 0)
    assert site_due(db, "danbooru", back)["id"] == a
    sent_at(db, a, "danbooru", back)
    assert site_due(db, "danbooru", back + timedelta(hours=3)) is None       # já saiu hoje
    assert site_due(db, "danbooru", datetime(2026, 10, 15, 18, 0))["id"] == b


def test_min_gap_after_late_post(db):
    """Post às 23h; no dia seguinte (horário 08:00) espera completar 12 h."""
    db.set_setting("daily_time", "08:00")
    a, b = art(db, "a", ["danbooru"]), art(db, "b", ["danbooru"])
    sent_at(db, a, "danbooru", datetime(2026, 10, 14, 23, 0))
    assert site_due(db, "danbooru", datetime(2026, 10, 15, 8, 0)) is None
    assert site_due(db, "danbooru", datetime(2026, 10, 15, 11, 0))["id"] == b


def test_publish_now_counts_as_the_sites_post_of_the_day(db):
    a = art(db, "a", ["danbooru"])
    b = art(db, "b", ["danbooru"])
    publish_post(db, a)                                   # "Publicar agora"
    assert db.get_post(a)["status"] == "done"
    today = datetime.now()
    plans = plan(db, today, 3)
    assert plans["danbooru"][today.date()].kind == "published"
    nxt = next(c for d, c in sorted(plans["danbooru"].items()) if c.kind == "post")
    assert nxt.post["id"] == b and nxt.when.date() > today.date()


def test_never_duplicates(db):
    pid = art(db, "a", ["gelbooru"])
    sent_at(db, pid, "gelbooru", NOW - timedelta(days=2))
    assert publish_site(db, pid, "gelbooru") == "ok" and Fake.calls == []
    assert site_due(db, "gelbooru", NOW.replace(hour=19)) is None


def test_status_done_only_when_every_site_received(db):
    pid = art(db, "a", ["danbooru", "gelbooru"])
    assert publish_site(db, pid, "danbooru") == "ok"
    assert db.get_post(pid)["status"] == "approved"      # falta o Gelbooru
    assert publish_site(db, pid, "gelbooru") == "ok"
    assert db.get_post(pid)["status"] == "done"


# --- ritmo por site -----------------------------------------------------------------------------

def test_delay_counts_from_first_site(db):
    db.set_setting("delay_gelbooru", "1")
    art(db, "a", ["danbooru", "gelbooru"])
    art(db, "b", ["danbooru", "gelbooru"])
    plans = plan(db, NOW, 4)
    assert days_of(plans, "danbooru", 3) == ["a", "b", "empty"]
    assert days_of(plans, "gelbooru", 3) == ["empty", "a", "b"]


def test_every_other_day(db):
    db.set_setting("rhythm_rule34us", "dia_sim_dia_nao")
    for t in ("a", "b", "c"):
        art(db, t, ["rule34us"])
    assert days_of(plan(db, NOW, 5), "rule34us", 5) == ["a", "rest", "b", "rest", "c"]


def test_site_off(db):
    db.set_setting("rhythm_paheal", "desligado")
    pid = art(db, "a", ["danbooru", "paheal"])
    assert days_of(plan(db, NOW, 2), "paheal", 2) == ["off", "off"]
    publish_post(db, pid)
    assert Fake.calls == ["danbooru"] and db.get_post(pid)["status"] == "done"


def test_rule_skip_does_not_use_the_day(db):
    oc = art(db, "oc", ["paheal"], original_character=True)
    art(db, "fanart", ["paheal"])
    assert days_of(plan(db, NOW, 1), "paheal", 1) == ["fanart"]
    record_rule_skips(db)
    assert db.get_post(oc)["status"] == "done" and Fake.calls == []


# --- dia fixado (arrastar no calendário) ----------------------------------------------------------

def test_pin_moves_an_art_to_a_day(db):
    a, b, c = art(db, "a", ["danbooru"]), art(db, "b", ["danbooru"]), art(db, "c", ["danbooru"])
    db.pin(c, "danbooru", TODAY)
    assert days_of(plan(db, NOW, 3), "danbooru", 3) == ["c", "a", "b"]
    db.pin(a, "danbooru", TODAY + timedelta(days=2))
    assert days_of(plan(db, NOW, 3), "danbooru", 3) == ["c", "b", "a"]


def test_pin_on_taken_day_swaps(db):
    a, b = art(db, "a", ["danbooru"]), art(db, "b", ["danbooru"])
    db.pin(a, "danbooru", TODAY)
    db.pin(b, "danbooru", TODAY + timedelta(days=1))
    db.pin(b, "danbooru", TODAY)                         # arrasta b para o dia de a
    assert db.post_pins(a)["danbooru"] == TODAY + timedelta(days=1)
    assert days_of(plan(db, NOW, 2), "danbooru", 2) == ["b", "a"]


def test_pin_is_per_site(db):
    a, b = art(db, "a", ["danbooru", "gelbooru"]), art(db, "b", ["danbooru", "gelbooru"])
    db.pin(b, "gelbooru", TODAY)
    plans = plan(db, NOW, 2)
    assert days_of(plans, "danbooru", 2) == ["a", "b"]
    assert days_of(plans, "gelbooru", 2) == ["b", "a"]


def test_overdue_pin_still_one_per_day(db):
    a, b = art(db, "a", ["danbooru"]), art(db, "b", ["danbooru"])
    db.pin(a, "danbooru", TODAY - timedelta(days=3))     # PC desligado no dia fixado
    db.pin(b, "danbooru", TODAY - timedelta(days=2))
    assert days_of(plan(db, NOW, 2), "danbooru", 2) == ["a", "b"]


def test_unpin(db):
    a, b = art(db, "a", ["danbooru"]), art(db, "b", ["danbooru"])
    db.pin(b, "danbooru", TODAY)
    db.unpin(b, "danbooru")
    assert days_of(plan(db, NOW, 2), "danbooru", 2) == ["a", "b"]


# --- falha, envio incerto e limite de tentativas ----------------------------------------------------

def test_failure_waits_and_retries_the_same_art(db):
    Fake.outcome = {"danbooru": "boom"}
    a, b = art(db, "a", ["danbooru"]), art(db, "b", ["danbooru"])
    assert publish_site(db, a, "danbooru") == "failed"
    assert site_due(db, "danbooru") is None              # espera (sem cascata pela fila)
    backoff = db.site_backoff("danbooru")
    later = max(backoff, datetime.combine(backoff.date(), datetime.min.time()).replace(hour=18)) + timedelta(minutes=1)
    assert site_due(db, "danbooru", later)["id"] == a    # a MESMA arte, não pula para b


def test_uncertain_send_is_never_resent_automatically(db):
    Fake.outcome = {"gelbooru": "uncertain"}
    pid = art(db, "a", ["danbooru", "gelbooru"])
    publish_site(db, pid, "danbooru")
    assert publish_site(db, pid, "gelbooru") == "uncertain"
    assert db.uncertain_sites(pid) == ["gelbooru"]
    assert db.get_post(pid)["status"] == "partial"       # sai da fila e vai para "Para revisar"
    assert site_due(db, "gelbooru", datetime.now() + timedelta(days=3)) is None
    publish_post(db, pid)                                # nem "Tentar de novo" reenvia o incerto
    assert Fake.calls.count("gelbooru") == 1


def test_retry_cap(db):
    Fake.outcome = {"danbooru": "boom"}
    a, b = art(db, "a", ["danbooru"]), art(db, "b", ["danbooru"])
    for _ in range(MAX_AUTO_TRIES):
        db.set_site_backoff("danbooru", None)
        publish_site(db, a, "danbooru")
    db.set_site_backoff("danbooru", None)
    assert db.failures_in_a_row(a, "danbooru") == MAX_AUTO_TRIES
    later = datetime.now().replace(hour=23, minute=59)
    assert site_due(db, "danbooru", later)["id"] == b    # a fila do site segue sem a arte travada


# --- agendador ---------------------------------------------------------------------------------------

def test_scheduler_one_send_at_a_time_and_test_mode(db):
    db.set_setting("daily_time", "00:00")
    art(db, "a", ["danbooru", "gelbooru"])
    w = Worker(db)
    w.tick()
    w.tick()
    assert [j.kind for j in w.jobs.queue] == ["publish_site"]
    db.set_setting("test_mode", "1")
    w2 = Worker(db)
    w2.tick()
    assert w2.jobs.empty()
    assert publish_site(db, 1, "danbooru") == "blocked"


def test_next_free_day(db):
    art(db, "a", ["danbooru"])
    assert next_free_day(db, ["danbooru"], NOW).date() == date(2026, 10, 15)


# --- conferência manual de envio incerto --------------------------------------------------------

def test_manual_check_buttons(db):
    from fastapi.testclient import TestClient

    Fake.outcome = {"gelbooru": "uncertain", "paheal": "uncertain"}
    pid = art(db, "a", ["gelbooru", "paheal"])
    publish_post(db, pid)
    c = TestClient(app_mod.create_app(db, Worker(db), start_worker=False))
    page = c.get(f"/posts/{pid}").text
    assert "Confira no site" in page and "Saiu sim" in page
    assert "conferir no" in c.get("/").text

    c.post(f"/posts/{pid}/conferir", data={"site": "gelbooru", "action": "saiu", "url": "https://gelbooru.com/x?id=9"})
    assert "gelbooru" not in db.uncertain_sites(pid)
    assert any(p["site"] == "gelbooru" and p["status"] == "ok" and p["url"] for p in db.publications(pid))

    c.post(f"/posts/{pid}/conferir", data={"site": "paheal", "action": "nao_saiu"})
    assert db.uncertain_sites(pid) == [] and db.pending_sites(db.get_post(pid)) == ["paheal"]
    assert db.get_post(pid)["status"] == "approved"          # volta para a fila do paheal
    assert db.failures_in_a_row(pid, "paheal") == 0          # "não saiu" não conta para o limite
