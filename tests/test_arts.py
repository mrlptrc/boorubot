"""Tela Artes (etapa 13): filtros, busca, estado por site e o Histórico redirecionando para cá."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from boorubot import arts
from boorubot.db import DB
from boorubot.web import app as app_mod
from boorubot.worker import Worker

ALL = ["danbooru", "paheal", "rule34xxx"]


@pytest.fixture
def db(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    monkeypatch.setattr(app_mod, "IMAGES_DIR", images)
    monkeypatch.setattr("boorubot.db.IMAGES_DIR", images)
    return DB(tmp_path / "a.db")


@pytest.fixture
def seeded(db):
    ids = {}
    ids["rascunho"] = db.create_post("r.png", title="bruxinha", tags="1girl witch_hat", rating="", sites=ALL)
    ids["oc"] = db.create_post("o.png", title="oc pipoca", tags="1girl original", rating="general", sites=ALL,
                               original_character=True)
    ids["fila"] = db.create_post("f.png", title="maid", tags="maid", rating="general", sites=ALL)
    db.approve(ids["fila"])
    ids["saiu"] = db.create_post("s.png", title="lumi", tags="commission", rating="general", sites=ALL, image_md5="abc")
    for site in ALL:
        db.add_publication(ids["saiu"], site, "ok", f"https://x/{site}")
    db.update_post(ids["saiu"], status="done", published_at="2026-10-12T18:02:00")
    ids["falhou"] = db.create_post("p.png", title="piquenique", tags="picnic", rating="general", sites=ALL)
    db.add_publication(ids["falhou"], "danbooru", "ok", "https://x/1")
    db.add_publication(ids["falhou"], "paheal", "failed", message="paheal recusou: tag picnic_basket")
    db.update_post(ids["falhou"], status="partial", published_at="2026-10-15T18:02:00")
    ids["repost"] = db.create_post("s2.png", title="lumi de novo", tags="commission", rating="general", sites=ALL,
                                   image_md5="abc", status="done", published_at="2026-10-09T18:00:00")
    ids["apagada"] = db.create_post("x.png", title="apagada", tags="x", sites=ALL)
    db.delete_post(ids["apagada"])
    db.set_session("rule34xxx", False, "pediu login")
    return db, ids


def titles(view):
    return {r["post"]["title"] for r in view["rows"]}


def test_each_filter_shows_only_what_it_should(seeded):
    db, _ = seeded
    assert titles(arts.build(db, "revisar")) == {"bruxinha", "oc pipoca"}
    assert titles(arts.build(db, "fila")) == {"maid"}
    assert titles(arts.build(db, "publicadas")) == {"lumi", "piquenique", "lumi de novo"}
    assert titles(arts.build(db, "falha")) == {"piquenique"}
    everything = arts.build(db)
    assert "apagada" not in titles(everything) and everything["counts"]["todas"] == 6
    assert everything["counts"] == {"todas": 6, "revisar": 2, "fila": 1, "publicadas": 3, "falha": 1}


def test_site_marks_cover_saiu_falhou_fora_and_login(seeded):
    db, ids = seeded
    rows = {r["post"]["id"]: r for r in arts.build(db)["rows"]}
    failed = rows[ids["falhou"]]
    assert failed["marks"]["danbooru"]["mark"] == "saiu" and failed["marks"]["paheal"]["mark"] == "falhou"
    assert failed["situation"] == ("falhou em 1 site", "falhou")
    assert failed["detail"] == "paheal recusou: tag picnic_basket"
    oc = rows[ids["oc"]]
    assert oc["marks"]["paheal"]["mark"] == "fora" and oc["marks"]["danbooru"]["mark"] == "vai"
    # rule34.xxx à mão: a sessão do robô não importa; enviado pelo robô, "pediu login" aparece
    assert oc["marks"]["rule34xxx"]["mark"] == "vai"
    db.set_setting("assisted_rule34xxx", "")
    oc = {r["post"]["id"]: r for r in arts.build(db)["rows"]}[ids["oc"]]
    assert oc["marks"]["rule34xxx"]["mark"] == "login"
    assert rows[ids["rascunho"]]["when"] == "falta o rating" and rows[ids["rascunho"]]["situation"][0] == "precisa de você"
    assert rows[ids["fila"]]["detail"] == "1º da fila"


def test_repost_says_how_many_times(seeded):
    db, ids = seeded
    rows = {r["post"]["id"]: r for r in arts.build(db)["rows"]}
    assert rows[ids["saiu"]]["detail"] == "postada 2 vezes · última em 12/10"


def test_search_by_title_or_tag(seeded):
    db, _ = seeded
    assert titles(arts.build(db, q="PIQUE")) == {"piquenique"}
    assert titles(arts.build(db, q="witch")) == {"bruxinha"}


def test_page_and_history_redirect(seeded):
    db, _ = seeded
    c = TestClient(app_mod.create_app(db, Worker(db), start_worker=False))
    r = c.get("/historico", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/artes?filtro=publicadas"
    page = c.get("/artes?filtro=falha").text
    assert "piquenique" in page and "bruxinha" not in page and 'aria-current="page"' in page
    assert "esperando login" in page and "fica de fora" in page   # legenda
    assert "Histórico" not in page


def test_pagination(db):
    for i in range(35):
        db.create_post(f"{i}.png", title=f"arte {i}", tags="x")
    view = arts.build(db, limit=30)
    assert len(view["rows"]) == 30 and view["more"] and view["total"] == 35
