"""Calendário semana × site: fila por site, arrastar para fixar o dia, navegação."""

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
    monkeypatch.setattr("boorubot.db.IMAGES_DIR", images)
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


def posts(r):
    return [c["post"]["id"] if c.get("post") else None for c in r["cells"]]


def test_rows_follow_each_site_queue(db):
    a, b = approve(db, 2)
    c = approve(db, 1, ["paheal"])[0]
    cal = calendar_view.build(db, NOW)
    assert posts(row(cal, "danbooru"))[:3] == [a, b, None]
    assert posts(row(cal, "paheal"))[:3] == [a, b, c]
    assert cal["dates"][0]["today"] and cal["dates"][0]["label"].startswith("hoje")


def test_delay_and_every_other_day(db):
    db.set_setting("delay_gelbooru", "1")
    db.set_setting("rhythm_rule34us", "dia_sim_dia_nao")
    ids = approve(db, 3)
    cal = calendar_view.build(db, NOW)
    assert posts(row(cal, "gelbooru"))[:4] == [None, ids[0], ids[1], ids[2]]
    r = row(cal, "rule34us")
    assert posts(r)[:5] == [ids[0], None, ids[1], None, ids[2]] and r["cells"][1]["kind"] == "rest"
    assert "1 dia depois" in row(cal, "gelbooru")["rhythm"]


def test_site_off(db):
    db.set_setting("rhythm_rule34xxx", "desligado")
    approve(db, 2)
    r = row(calendar_view.build(db, NOW), "rule34xxx")
    assert r["off"] and {c["kind"] for c in r["cells"]} == {"off"}


def test_paheal_skips_oc_without_losing_the_day(db):
    approve(db, 1, original_character=True)
    other = approve(db, 1)[0]
    assert posts(row(calendar_view.build(db, NOW), "paheal"))[0] == other


def test_session_down_marks_waiting(db):
    approve(db, 1)
    db.set_session("rule34xxx", False, "sem sessão")
    r = row(calendar_view.build(db, NOW), "rule34xxx")
    assert r["session_down"] and r["cells"][0]["kind"] == "waiting"


def test_pinned_flag(db):
    a, b = approve(db, 2, ["danbooru"])
    db.pin(b, "danbooru", NOW.date())
    cells = row(calendar_view.build(db, NOW), "danbooru")["cells"]
    assert cells[0]["post"]["id"] == b and cells[0]["pinned"] and not cells[1]["pinned"]


def test_week_navigation_and_beyond(db):
    ids = approve(db, 9, ["danbooru"])
    cal = calendar_view.build(db, NOW)
    assert [p["id"] for p in cal["beyond"]] == ids[7:]
    nxt = calendar_view.build(db, NOW, week=1)
    assert nxt["dates"][0]["date"] == date(2026, 10, 21)
    assert posts(row(nxt, "danbooru"))[:2] == ids[7:9] and nxt["beyond"] == []


def test_empty_days_suggest_a_draft(db):
    approve(db, 2)
    draft = db.create_post("d.png", title="rascunho bom")
    cal = calendar_view.build(db, NOW)
    assert cal["empty_days"][0] == "sex 16" and cal["suggestion"]["id"] == draft


# --- tela, arrastar e configuração ------------------------------------------------------------

def client(db):
    return TestClient(app_mod.create_app(db, Worker(db), start_worker=False))


def test_calendar_page_renders_draggable_cells(db):
    db.set_setting("delay_gelbooru", "1")
    approve(db, 2)
    db.set_session("rule34xxx", False, "sem sessão")
    page = client(db).get("/calendario").text
    assert "Calendário" in page and "Na fila, depois desta semana" in page and "pediu login" in page
    assert 'draggable="true"' in page and 'data-drop-site="danbooru"' in page
    assert page.count('class="cal-row') == 6                # cabeçalho + 5 sites
    assert "Próxima semana" in page and "Esta semana" in client(db).get("/calendario?semana=1").text


def test_calendar_page_empty(db):
    page = client(db).get("/calendario").text
    assert "Tudo o que está aprovado já tem dia" in page and "vazio" in page


def test_drag_pins_the_day(db):
    a, b = approve(db, 2, ["danbooru"])
    c = client(db)
    day = (datetime.now().date() + timedelta(days=3)).isoformat()
    r = c.post("/calendario/fixar", data={"post_id": a, "site": "danbooru", "day": day})
    assert r.status_code == 200 and r.json()["ok"]
    assert db.post_pins(a)["danbooru"].isoformat() == day
    c.post("/calendario/desafixar", data={"post_id": a, "site": "danbooru"})
    assert db.post_pins(a) == {}


def test_drag_rejects_bad_moves(db):
    a = approve(db, 1, ["danbooru"])[0]
    draft = db.create_post("d.png", tags="x", sites=["danbooru"])
    c = client(db)
    past = (datetime.now().date() - timedelta(days=1)).isoformat()
    future = (datetime.now().date() + timedelta(days=1)).isoformat()
    assert c.post("/calendario/fixar", data={"post_id": a, "site": "danbooru", "day": past}).status_code == 400
    assert c.post("/calendario/fixar", data={"post_id": draft, "site": "danbooru", "day": future}).status_code == 400
    assert c.post("/calendario/fixar", data={"post_id": a, "site": "gelbooru", "day": future}).status_code == 400
    db.set_setting("rhythm_paheal", "desligado")
    assert c.post("/calendario/fixar", data={"post_id": a, "site": "paheal", "day": future,
                                             "add_site": "true"}).status_code == 400


def test_drag_from_queue_box_adds_the_site(db):
    a = approve(db, 1, ["danbooru"])[0]
    future = (datetime.now().date() + timedelta(days=2)).isoformat()
    r = client(db).post("/calendario/fixar", data={"post_id": a, "site": "gelbooru", "day": future, "add_site": "true"})
    assert r.status_code == 200
    assert db.get_post(a)["sites"] == ["danbooru", "gelbooru"] and "gelbooru" in db.post_pins(a)


def test_post_page_day_per_site(db):
    a = approve(db, 1, ["danbooru", "gelbooru"])[0]
    c = client(db)
    assert "Dia por site" in c.get(f"/posts/{a}").text
    future = (datetime.now().date() + timedelta(days=4)).isoformat()
    c.post(f"/posts/{a}/dia", data={"site": "gelbooru", "day": future, "action": "pin"})
    assert db.post_pins(a)["gelbooru"].isoformat() == future
    c.post(f"/posts/{a}/dia", data={"site": "gelbooru", "action": "unpin"})
    assert db.post_pins(a) == {}


def test_rhythm_settings_form(db):
    c = client(db)
    assert 'id="ritmo"' not in c.get("/sites").text          # o ritmo saiu de Sites e foi para o Calendário
    c.post("/settings/ritmo", data={"rhythm_rule34us": "dia_sim_dia_nao", "delay_gelbooru": "1",
                                    "rhythm_paheal": "hackeado", "delay_danbooru": "99"})
    assert db.site_rhythm("rule34us") == "dia_sim_dia_nao" and db.site_delay("gelbooru") == 1
    assert db.site_rhythm("paheal") == "diario" and db.site_delay("danbooru") == 0


# --- etapa 15: ordem da fila pelo calendário, ritmo no menu, semanas passadas ----------------------

def test_move_to_position_reorders_without_gaps_or_repeats(db):
    a, b, c = approve(db, 3)
    db.move_to_position(c, 1)
    assert [p["id"] for p in db.queue()] == [c, a, b]
    db.move_to_position(c, 99)                              # além do fim: vai para o fim
    assert [p["id"] for p in db.queue()] == [a, b, c]
    positions = [p["queue_pos"] for p in db.queue()]
    assert positions == [1.0, 2.0, 3.0]


def test_drag_to_another_day_moves_in_the_queue(db):
    a, b, c = approve(db, 3)
    r = client(db).post("/calendario/mover", data={"post_id": c, "posicao": 1}, headers={"accept": "application/json"})
    assert r.json() == {"ok": True} and db.queue()[0]["id"] == c
    draft = db.create_post("d.png", title="rascunho", tags="x", sites=["danbooru"])
    r = client(db).post("/calendario/mover", data={"post_id": draft, "posicao": 1}, headers={"accept": "application/json"})
    assert r.status_code == 400 and db.get_post(draft)["queue_pos"] is None


def test_rhythm_saved_from_the_calendar_shows_in_the_grid(db):
    approve(db, 2)
    client(db).post("/calendario/ritmo", data={"site": "paheal", "rhythm": "dia_sim_dia_nao", "delay": 2})
    assert db.site_rhythm("paheal") == "dia_sim_dia_nao" and db.site_delay("paheal") == 2
    page = client(db).get("/calendario").text
    assert "dia sim, dia não · 2 dias depois" in page
    client(db).post("/calendario/ritmo", data={"site": "paheal", "rhythm": "hackeado", "delay": 99})
    assert db.site_rhythm("paheal") == "diario" and db.site_delay("paheal") == 0


def test_days_after_a_login_wait_depend_on_it(db):
    approve(db, 3)
    db.set_session("gelbooru", False, "sem sessão")
    row = next(r for r in calendar_view.build(db, NOW)["rows"] if r["site"] == "gelbooru")
    kinds = [c["kind"] for c in row["cells"] if c["kind"] in ("waiting", "blocked")]
    assert kinds[0] == "waiting" and set(kinds[1:]) == {"blocked"}


def test_past_week_shows_what_went_out(db):
    (pid,) = approve(db, 1)
    db.add_publication(pid, "danbooru", "ok", "https://x/1")
    when = NOW - timedelta(days=3)
    db._exec("UPDATE publications SET created_at = ?", (when.isoformat(),))
    view = calendar_view.build(db, NOW, week=-1)
    dan = next(r for r in view["rows"] if r["site"] == "danbooru")
    assert [c["kind"] for c in dan["cells"]].count("published") == 1
