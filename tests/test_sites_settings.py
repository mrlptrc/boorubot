"""Etapa 14: Sites só com as contas (painel lateral, esperando login) e Ajustes que salva sozinho."""

import pytest
from fastapi.testclient import TestClient

from boorubot.db import DB
from boorubot.web import app as app_mod
from boorubot.worker import Worker


@pytest.fixture
def env(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    monkeypatch.setattr(app_mod, "IMAGES_DIR", images)
    monkeypatch.setattr("boorubot.db.IMAGES_DIR", images)
    db = DB(tmp_path / "s.db")
    w = Worker(db)
    return TestClient(app_mod.create_app(db, w, start_worker=False)), db, w, tmp_path


def test_each_setting_saves_by_itself_and_persists(env):
    c, db, _, tmp = env
    r = c.patch("/ajustes", json={"daily_time": "9:5"})
    assert r.status_code == 200 and r.json()["saved_at"] and db.setting("daily_time") == "09:05"
    assert db.setting("default_tags") == ""                      # só o campo enviado muda
    c.patch("/ajustes", json={"default_tags": ["Nome Artista", "commission"]})
    assert db.setting("default_tags") == "nome_artista commission"
    c.patch("/ajustes", json={"default_sites": ["gelbooru", "hackeado", "danbooru"]})
    assert db.setting("default_sites") == '["danbooru", "gelbooru"]'
    c.patch("/ajustes", json={"test_mode": False})
    assert not db.test_mode
    page = c.get("/ajustes").text
    assert 'value="09:05"' in page and "nome_artista commission" in page


def test_invalid_values_are_refused_with_the_reason(env):
    c, db, _, _ = env
    r = c.patch("/ajustes", json={"daily_time": "25:00"})
    assert r.status_code == 422 and "horário" in r.json()["error"] and db.setting("daily_time") == "18:00"
    r = c.patch("/ajustes", json={"backup_on": True, "backup_dir": "relativo"})
    assert r.status_code == 422 and db.setting("backup_dir") == ""


def test_backup_toggle_remembers_the_folder(env):
    c, db, w, tmp = env
    dest = str(tmp / "bk")
    c.patch("/ajustes", json={"backup_on": True, "backup_dir": dest})
    assert db.setting("backup_dir") == dest
    c.patch("/ajustes", json={"backup_on": False})
    assert db.setting("backup_dir") == ""
    c.patch("/ajustes", json={"backup_on": True})                # religar volta para a mesma pasta
    assert db.setting("backup_dir") == dest
    c.post("/ajustes/backup-agora")
    assert [j.kind for j in w.jobs.queue] == ["backup"]


def test_sites_page_has_only_the_accounts(env):
    c, _, _, _ = env
    page = c.get("/sites").text
    for gone in ('name="daily_time"', 'name="default_tags"', 'name="backup_dir"', 'name="rhythm_', 'name="test_mode"'):
        assert gone not in page, gone
    assert page.count('class="bb-drawer"') == 5 and "Salvar e testar" in page
    assert "/ajustes" in page and "location.replace" in page     # links antigos (#backup, #config, #ritmo)


def test_waiting_for_login_and_cancel(env):
    c, db, w, _ = env
    c.post("/sites/rule34xxx/login")
    page = c.get("/sites").text
    assert "Faça login na janela que abriu. Eu espero aqui." in page and "nunca mexe no captcha" in page
    rows = {r["site"]: r for r in c.get("/sites/status.json").json()["rows"]}
    assert rows["rule34xxx"]["state"] == "login"
    c.post("/sites/rule34xxx/cancelar")
    assert w.logins() == [] and "Eu espero aqui" not in c.get("/sites").text


def test_waiting_state_goes_away_when_the_session_is_ok(env):
    c, db, w, _ = env
    w.submit("login", "gelbooru", "login Gelbooru")
    assert c.get("/sites/status.json").json()["rows"][1]["state"] == "login"
    w.jobs.queue.clear()                                          # o worker terminou o login
    db.set_session("gelbooru", True, "logado")
    assert c.get("/sites/status.json").json()["rows"][1]["state"] == "ok"


def test_drawer_opens_from_the_url_without_javascript(env):
    c, db, _, _ = env
    db.set_setting("danbooru_api_key", "abcdefa1f9")
    page = c.get("/sites?painel=danbooru").text
    assert 'id="painel-danbooru" aria-labelledby="painel-danbooru-t" open' in page
    assert "salva (••••a1f9) · deixe vazio para manter" in page and "abcdefa1f9" not in page
