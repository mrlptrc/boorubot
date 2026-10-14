"""Calendário semana × site: projeção por site e tela."""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from boorubot import calendar_view
from boorubot.db import DB
from boorubot.web import app as app_mod
from boorubot.worker import Worker

NOW = datetime(2026, 10, 14, 12, 0)   # quarta
SITES = ["danbooru", "gelbooru", "paheal", "rule34us", "rule34xxx"]


@pytest.fixture
def db(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    monkeypatch.setattr(app_mod, "IMAGES_DIR", images)
    d = DB(tmp_path / "c.db")
    d.set_setting("daily_time", "18:00")
    return d


def approve(db, n, sites=SITES, **fields):
    ids = []
    for i in range(n):
        pid = db.create_post(f"{i}.png", title=f"arte {i}", tags="x", sites=sites, **fields)
        db.approve(pid)
        ids.append(pid)
    return ids


def row(cal, site):
    return next(r for r in cal["rows"] if r["site"] == site)


def kinds(r):
    return [c["kind"] for c in r["cells"]]


def posts(r):
    return [c["post"]["id"] if c.get("post") else None for c in r["cells"]]


def test_default_every_site_follows_the_queue(db):
    ids = approve(db, 3)
    cal = calendar_view.build(db, NOW)
    lead = db.projected_times(NOW)
    expected = [None] * 7
    for pid, when in lead.items():
        expected[(when.date() - NOW.date()).days] = pid
    for site in SITES:
        assert posts(row(cal, site)) == expected       # padrão: igual à fila principal em todos os sites
    assert expected[:3] == ids
    assert cal["dates"][0]["today"] and cal["dates"][0]["label"].startswith("hoje")


def test_delay_one_day(db):
    db.set_setting("delay_gelbooru", "1")
    ids = approve(db, 3)
    cal = calendar_view.build(db, NOW)
    assert posts(row(cal, "danbooru"))[:4] == [ids[0], ids[1], ids[2], None]
    assert posts(row(cal, "gelbooru"))[:4] == [None, ids[0], ids[1], ids[2]]
    assert "1 dia depois" in row(cal, "gelbooru")["rhythm"]


def test_every_other_day(db):
    db.set_setting("rhythm_rule34us", "dia_sim_dia_nao")
    ids = approve(db, 3)
    r = row(calendar_view.build(db, NOW), "rule34us")
    assert posts(r)[:5] == [ids[0], None, ids[1], None, ids[2]]
    assert kinds(r)[1] == "rest" and kinds(r)[3] == "rest"
    assert r["rhythm"].startswith("dia sim, dia não")


def test_site_off(db):
    db.set_setting("rhythm_rule34xxx", "desligado")
    approve(db, 2)
    r = row(calendar_view.build(db, NOW), "rule34xxx")
    assert r["off"] and set(kinds(r)) == {"off"} and r["rhythm"] == "desligado"


def test_paheal_skips_original_characters(db):
    oc = approve(db, 1, original_character=True)[0]
    other = approve(db, 1)[0]
    r = row(calendar_view.build(db, NOW), "paheal")
    assert kinds(r)[0] == "skipped" and posts(r)[0] == oc
    assert posts(r)[1] == other


def test_session_down_marks_waiting(db):
    approve(db, 1)
    db.set_session("rule34xxx", False, "sem sessão")
    r = row(calendar_view.build(db, NOW), "rule34xxx")
    assert r["session_down"] and kinds(r)[0] == "waiting"


def test_pending_site_schedule_shows_up(db):
    db.set_setting("delay_gelbooru", "1")
    pid = db.create_post("x.png", title="ontem", tags="x", sites=["danbooru", "gelbooru"], status="publishing")
    db.schedule_site(pid, "gelbooru", datetime(2026, 10, 14, 18, 0))
    r = row(calendar_view.build(db, NOW), "gelbooru")
    assert posts(r)[0] == pid


def test_beyond_week_and_empty_days(db):
    ids = approve(db, 9)
    cal = calendar_view.build(db, NOW)
    assert [p["id"] for p in cal["beyond"]] == ids[7:]
    assert cal["empty_days"] == []


def test_empty_days_suggest_a_draft(db):
    approve(db, 2)
    draft = db.create_post("d.png", title="rascunho bom")
    cal = calendar_view.build(db, NOW)
    assert cal["empty_days"][0] == "sex 16" and len(cal["empty_days"]) == 5
    assert cal["suggestion"]["id"] == draft and cal["suggestion_is_draft"]


# --- tela e configuração ------------------------------------------------------------------

def client(db):
    return TestClient(app_mod.create_app(db, Worker(db), start_worker=False))


def test_calendar_page_renders(db):
    db.set_setting("delay_gelbooru", "1")
    approve(db, 2)
    db.set_session("rule34xxx", False, "sem sessão")
    page = client(db).get("/calendario").text
    assert "Calendário" in page and "Na fila, sem dia ainda" in page
    assert "1 dia depois" in page and "pedir login" in page and "esperando" in page
    assert page.count('class="cal-row"') == 6               # cabeçalho + 5 sites


def test_calendar_page_empty(db):
    page = client(db).get("/calendario").text
    assert "Tudo o que está aprovado já tem dia nesta semana." in page and "vazio" in page


def test_rhythm_settings_form(db):
    c = client(db)
    assert 'id="ritmo"' in c.get("/sites").text
    c.post("/settings/ritmo", data={"rhythm_rule34us": "dia_sim_dia_nao", "delay_gelbooru": "1",
                                    "rhythm_paheal": "hackeado", "delay_danbooru": "99"})
    assert db.site_rhythm("rule34us") == "dia_sim_dia_nao" and db.site_delay("gelbooru") == 1
    assert db.site_rhythm("paheal") == "diario" and db.site_delay("danbooru") == 0
    assert db.site_rhythm("gelbooru") == "diario"           # campo ausente volta ao padrão
