"""Trava: arte aprovada com o modo teste ligado nunca vai ao ar sozinha."""

import sqlite3
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from boorubot import dashboard
from boorubot.db import DB
from boorubot.scheduler import site_due
from boorubot.web import app as app_mod
from boorubot.worker import Worker


@pytest.fixture
def db(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    monkeypatch.setattr(app_mod, "IMAGES_DIR", images)
    d = DB(tmp_path / "g.db")
    d.set_setting("daily_time", "00:00")
    return d


def new(db, title="arte"):
    return db.create_post("a.png", title=title, tags="x", sites=["danbooru"])


def test_approved_in_test_mode_is_flagged(db):
    assert db.test_mode
    pid = new(db)
    db.approve(pid)
    assert db.get_post(pid)["approved_in_test"]
    db.set_setting("test_mode", "0")
    other = new(db)
    db.approve(other)
    assert not db.get_post(other)["approved_in_test"]


def test_test_approvals_leave_the_production_queue(db):
    pid = new(db)
    db.approve(pid)                                   # aprovada no modo teste
    assert [p["id"] for p in db.queue()] == [pid]     # no modo teste aparece na prévia
    db.set_setting("test_mode", "0")
    assert db.queue() == [] and site_due(db, "danbooru") is None  # desligou o teste: NÃO vai ao ar
    assert [p["id"] for p in db.needs_confirmation()] == [pid]


def test_scheduler_never_publishes_unconfirmed(db):
    pid = new(db)
    db.approve(pid)
    db.set_setting("test_mode", "0")
    w = Worker(db)
    w.tick()
    assert w.jobs.empty()
    assert db.get_post(pid)["status"] == "approved"


def test_confirm_puts_it_in_the_queue(db):
    pid = new(db)
    db.approve(pid)
    db.set_setting("test_mode", "0")
    db.confirm_for_production(pid)
    assert [p["id"] for p in db.queue()] == [pid] and db.needs_confirmation() == []


def test_back_to_draft_clears_the_flag(db, tmp_path):
    c = TestClient(app_mod.create_app(db, Worker(db), start_worker=False))
    pid = new(db)
    db.approve(pid)
    c.post(f"/posts/{pid}", data={"action": "unapprove", "tags": "x", "sites": ["danbooru"]})
    row = db.get_post(pid)
    assert row["status"] == "draft" and not row["approved_in_test"]


def test_panel_confirm_and_bulk_actions(db):
    w = Worker(db)
    c = TestClient(app_mod.create_app(db, w, start_worker=False))
    a, b = new(db, "kaneki"), new(db, "ichigo")
    db.approve(a)
    db.approve(b)
    db.set_setting("test_mode", "0")
    home = c.get("/").text
    assert "Confirme antes de postar" in home and "kaneki" in home
    page = c.get(f"/posts/{a}").text
    assert "Aprovada no modo teste" in page and 'value="confirm"' in page
    c.post(f"/posts/{a}", data={"action": "confirm", "tags": "x", "sites": ["danbooru"]})
    assert [p["id"] for p in db.queue()] == [a]
    c.post("/fila/teste", data={"action": "draft"})   # "voltar todas para rascunho"
    assert db.get_post(b)["status"] == "draft" and db.needs_confirmation() == []


def test_publish_now_in_production_is_an_explicit_confirmation(db):
    w = Worker(db)
    c = TestClient(app_mod.create_app(db, w, start_worker=False))
    pid = new(db)
    db.approve(pid)
    db.set_setting("test_mode", "0")
    c.post(f"/posts/{pid}", data={"action": "publish_now", "tags": "x", "rating": "general", "sites": ["danbooru"]})
    assert not db.get_post(pid)["approved_in_test"] and [j.kind for j in w.jobs.queue] == ["publish"]


def test_existing_queue_is_protected_on_upgrade(tmp_path):
    """Banco de antes da trava, com modo teste ligado: o que estava na fila fica marcado."""
    path = tmp_path / "old.db"
    old = DB(path)
    pid = old.create_post("a.png", tags="x", sites=["danbooru"])
    old.approve(pid)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE posts DROP COLUMN approved_in_test")  # simula o banco antigo
    conn.commit()
    conn.close()
    upgraded = DB(path)
    assert upgraded.get_post(pid)["approved_in_test"]


def test_dashboard_alert(db):
    pid = new(db)
    db.approve(pid)
    db.set_setting("test_mode", "0")
    kinds = [a.kind for a in dashboard.alerts(db, ["danbooru"], None)]
    assert kinds[0] == "confirm"
