"""Envio assistido (rule34.xxx): a Cloudflare barra o robô, então o painel prepara e ela envia à mão."""

import pytest
from fastapi.testclient import TestClient

from boorubot import dashboard, worker as worker_mod
from boorubot.db import DB
from boorubot.scheduler import site_due, turn_now
from boorubot.sites.base import Result
from boorubot.web import app as app_mod
from boorubot.worker import Worker

TAGS = "artista_exemplo zero_two_(darling_in_the_franxx) 1girl breasts long_hair pink_hair"


@pytest.fixture
def db(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    monkeypatch.setattr(app_mod, "IMAGES_DIR", images)
    monkeypatch.setattr("boorubot.db.IMAGES_DIR", images)
    (images / "a.png").write_bytes(b"x")
    d = DB(tmp_path / "test.db")
    d.set_setting("test_mode", "0")
    d.set_setting("daily_time", "00:00")  # a vez da arte já chegou hoje
    return d


@pytest.fixture
def client(db):
    return TestClient(app_mod.create_app(db, Worker(db), start_worker=False))


def approved(db, sites=("danbooru", "rule34xxx")):
    pid = db.create_post("a.png", title="zero two", tags=TAGS, sites=list(sites), rating="explicit",
                         source="https://x.com/artista_exemplo/1")
    db.approve(pid)
    return pid


def test_rule34xxx_is_assisted_by_default(db):
    assert db.assisted("rule34xxx") and not db.assisted("paheal") and not db.assisted("danbooru")


def test_robot_never_opens_an_assisted_site(db, monkeypatch):
    pid = approved(db)
    assert site_due(db, "rule34xxx") is None and turn_now(db, "rule34xxx")["id"] == pid
    opened = []

    class Fake:
        def __init__(self, site):
            self.site = site

        def publish(self, post, dry_run):
            opened.append(self.site)
            return Result(self.site, ok=True, dry_run=dry_run, message="ok", url="https://x/1")

    monkeypatch.setattr(worker_mod, "build_site", lambda site, db, **kw: Fake(site))
    worker_mod.publish_post(db, pid)       # "Publicar agora"
    worker_mod.run_post_test(db, pid)      # "Testar sem publicar"
    assert "rule34xxx" not in opened and opened.count("danbooru") == 2
    assert db.pending_sites(db.get_post(pid)) == ["rule34xxx"]   # continua esperando o envio à mão
    assert any(t["site"] == "rule34xxx" and t["status"] == "skipped" for t in db.test_runs(pid))


def test_editor_has_everything_ready_to_copy(client, db):
    pid = approved(db)
    page = client.get(f"/posts/{pid}").text
    assert 'id="manual"' in page and "Enviar à mão" in page
    assert "https://rule34.xxx/index.php?page=post&amp;s=add" in page and 'target="_blank"' in page
    assert f'href="/images/a.png" download="zero_two.png"' in page
    assert "zero_two_(darling_in_the_franxx) 1girl" in page          # tags gerais (não as do paheal)
    assert "<b>Explicit</b>" in page and "https://x.com/artista_exemplo/1" in page


def test_she_marks_it_sent_or_skipped(client, db):
    pid = approved(db)
    db.add_publication(pid, "danbooru", "ok", "https://danbooru.donmai.us/posts/1")
    client.post(f"/posts/{pid}/manual", data={"site": "rule34xxx", "action": "saiu", "url": "https://rule34.xxx/p/9"})
    row = db.get_post(pid)
    assert row["status"] == "done" and db.publications(pid)[0]["url"] == "https://rule34.xxx/p/9"
    assert 'id="manual"' not in client.get(f"/posts/{pid}").text

    other = approved(db)
    client.post(f"/posts/{other}/manual", data={"site": "rule34xxx", "action": "pular"})
    assert "rule34xxx" not in db.pending_sites(db.get_post(other))


def test_nothing_manual_in_test_mode(client, db):
    pid = approved(db)
    db.set_setting("test_mode", "1")
    assert 'id="manual"' not in client.get(f"/posts/{pid}").text
    client.post(f"/posts/{pid}/manual", data={"site": "rule34xxx", "action": "saiu"})
    assert db.pending_sites(db.get_post(pid)) == ["danbooru", "rule34xxx"]


def test_home_tells_her_when_it_is_time(db):
    pid = approved(db, sites=("rule34xxx",))
    found = dashboard.alerts(db, ["rule34xxx"], db.get_post(pid))
    assert found[0].kind == "manual" and found[0].post["id"] == pid and found[0].label == "rule34.xxx"
    db.set_session("rule34xxx", False, "Cloudflare")      # sessão de site assistido não é alerta de login
    assert "session" not in [a.kind for a in dashboard.alerts(db, ["rule34xxx"], db.get_post(pid))]


def test_sites_page_and_turning_it_back_to_the_robot(client, db):
    page = client.get("/sites").text
    assert "envio à mão" in page and "Voltar a enviar pelo robô" in page
    client.post("/sites/rule34xxx/assistido", data={"ligar": "0"})
    assert not db.assisted("rule34xxx")
    assert client.post("/sites/danbooru/assistido", data={"ligar": "1"}).status_code == 404


def test_robot_retry_wait_does_not_delay_her(db):
    from datetime import datetime, timedelta

    pid = approved(db, sites=("rule34xxx",))
    db.set_site_backoff("rule34xxx", datetime.now() + timedelta(hours=1))   # sobra de falha do robô
    assert turn_now(db, "rule34xxx")["id"] == pid


def test_switched_off_site_is_not_a_login_alert(db):
    pid = approved(db, sites=("danbooru", "gelbooru"))
    db.set_session("gelbooru", False, "reputação")
    db.set_setting("rhythm_gelbooru", "desligado")
    assert "session" not in [a.kind for a in dashboard.alerts(db, ["danbooru", "gelbooru"], db.get_post(pid))]
