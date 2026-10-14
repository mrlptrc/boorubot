from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boorubot import worker as worker_mod
from boorubot.db import DB
from boorubot.sites.base import Result, SkipPost
from boorubot.web import app as app_mod
from boorubot.worker import Worker, publish_post

PNG = (Path(__file__).resolve().parents[1] / "queue" / "exemplo-001" / "arte.png").read_bytes()


@pytest.fixture
def db(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    monkeypatch.setattr(app_mod, "IMAGES_DIR", images)
    monkeypatch.setattr("boorubot.db.IMAGES_DIR", images)
    return DB(tmp_path / "test.db")


@pytest.fixture
def client(db):
    w = Worker(db)
    return TestClient(app_mod.create_app(db, w, start_worker=False)), w


def upload(c) -> int:
    r = c.post("/posts", files={"image": ("minha arte.png", PNG, "image/png")}, follow_redirects=False)
    assert r.status_code == 303
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_upload_creates_draft_with_all_sites(client, db):
    c, _ = client
    post_id = upload(c)
    row = db.get_post(post_id)
    assert row["status"] == "draft" and row["title"] == "minha arte"
    assert row["sites"] == ["danbooru", "gelbooru", "paheal", "rule34us", "rule34xxx"]
    assert c.get(f"/posts/{post_id}").status_code == 200
    assert c.get("/").status_code == 200


def test_rejects_non_image(client):
    c, _ = client
    r = c.post("/posts", files={"image": ("x.txt", b"oi", "text/plain")})
    assert r.status_code == 400


def test_save_normalizes_tags_and_approve_schedules(client, db):
    c, _ = client
    post_id = upload(c)
    r = c.post(f"/posts/{post_id}", data={
        "action": "approve", "title": "A", "tags": "Long Hair, 1girl, 1girl,  commission", "rating": "explicit",
        "sites": ["danbooru", "paheal"], "original_character": "true",
    }, follow_redirects=False)
    assert r.status_code == 303
    row = db.get_post(post_id)
    assert row["tags"] == "long_hair 1girl commission"
    assert row["status"] == "approved" and row["scheduled_for"]
    assert row["sites"] == ["danbooru", "paheal"] and row["original_character"]


def test_tags_with_spaces_inside_commas(client, db):
    c, _ = client
    post_id = upload(c)
    c.post(f"/posts/{post_id}", data={"action": "save", "tags": "long_hair, 1girl", "sites": ["danbooru"]})
    assert db.get_post(post_id)["tags"] == "long_hair 1girl"


def test_approve_without_tags_shows_error(client, db):
    c, _ = client
    post_id = upload(c)
    r = c.post(f"/posts/{post_id}", data={"action": "approve", "tags": "", "sites": ["danbooru"]},
               follow_redirects=False)
    assert "erro=" in r.headers["location"]
    assert db.get_post(post_id)["status"] == "draft"


def test_one_post_per_day(db):
    db.set_setting("daily_time", "18:00")
    ref = datetime(2026, 10, 14, 12, 0)
    a = db.create_post("a.png", tags="x", sites=["danbooru"])
    b = db.create_post("b.png", tags="x", sites=["danbooru"])
    assert db.approve(a, db.next_free_slot(ref)) == datetime(2026, 10, 14, 18, 0)
    assert db.approve(b, db.next_free_slot(ref)) == datetime(2026, 10, 15, 18, 0)
    assert [p["id"] for p in db.due_posts(datetime(2026, 10, 14, 18, 1))] == [a]


class FakeSite:
    def __init__(self, name, outcome):
        self.name, self.outcome = name, outcome

    def publish(self, post, dry_run=True):
        if self.outcome == "skip":
            raise SkipPost("regra")
        if self.outcome == "boom":
            raise RuntimeError("caiu")
        return Result(self.name, ok=True, dry_run=dry_run, message="ok", url="" if dry_run else f"https://{self.name}/1")


def test_publish_records_each_site_and_retries_only_failures(db, monkeypatch):
    (app_mod.IMAGES_DIR / "a.png").write_bytes(PNG)
    outcomes = {"danbooru": "ok", "paheal": "skip", "gelbooru": "boom"}
    monkeypatch.setattr(worker_mod, "build_site", lambda n, db=None: FakeSite(n, outcomes[n]))
    db.set_setting("test_mode", "0")
    post_id = db.create_post("a.png", tags="x", sites=["danbooru", "paheal", "gelbooru"])

    assert publish_post(db, post_id) == "partial"
    assert {p["site"]: p["status"] for p in db.publications(post_id)} == {
        "danbooru": "ok", "paheal": "skipped", "gelbooru": "failed"}

    outcomes["gelbooru"] = "ok"
    assert publish_post(db, post_id) == "done"
    assert [p["site"] for p in db.publications(post_id)].count("danbooru") == 1  # não reenviou


def test_test_mode_never_sends(db, monkeypatch):
    (app_mod.IMAGES_DIR / "a.png").write_bytes(PNG)
    seen = []

    class Spy(FakeSite):
        def publish(self, post, dry_run=True):
            seen.append(dry_run)
            return super().publish(post, dry_run)

    monkeypatch.setattr(worker_mod, "build_site", lambda n, db=None: Spy(n, "ok"))
    post_id = db.create_post("a.png", tags="x", sites=["danbooru", "gelbooru"])
    assert db.test_mode
    assert publish_post(db, post_id) == "tested"
    assert seen == [True, True]


def test_test_mode_still_reports_failures(db, monkeypatch):
    (app_mod.IMAGES_DIR / "a.png").write_bytes(PNG)
    monkeypatch.setattr(worker_mod, "build_site", lambda n, db=None: FakeSite(n, "boom" if n == "gelbooru" else "ok"))
    post_id = db.create_post("a.png", tags="x", sites=["danbooru", "gelbooru"])
    assert publish_post(db, post_id) == "partial"


def test_danbooru_credentials_saved_in_panel(client, db):
    c, w = client
    r = c.post("/settings/danbooru", data={"login": " artista ", "api_key": "abc123xyz", "env": "live"},
               follow_redirects=False)
    assert r.status_code == 303
    assert db.setting("danbooru_login") == "artista" and db.setting("danbooru_env") == "live"
    assert [j.kind for j in w.jobs.queue] == ["session"]  # já agenda o teste
    # chave em branco mantém a salva
    c.post("/settings/danbooru", data={"login": "artista", "api_key": "", "env": "live"})
    assert db.setting("danbooru_api_key") == "abc123xyz"
    page = c.get("/sites").text
    assert "abc123xyz" not in page and "••••3xyz" in page

    from boorubot.config import build_site
    site = build_site("danbooru", db)
    assert site.base_url == "https://danbooru.donmai.us" and site.login == "artista"


def test_defaults_prefill_new_posts(client, db):
    c, _ = client
    c.post("/settings", data={"test_mode": "true", "daily_time": "09:30",
                              "default_tags": "Nome Artista, commission", "default_sites": ["danbooru", "gelbooru"]})
    post_id = upload(c)
    row = db.get_post(post_id)
    assert row["tags"] == "nome_artista commission"
    assert row["sites"] == ["danbooru", "gelbooru"]
    assert db.setting("daily_time") == "09:30"


def test_guide_and_checklist(client, db):
    c, _ = client
    assert "api_keys/new" in c.get("/guia").text
    home = c.get("/").text
    assert "Configuração inicial" in home and "Conectar Danbooru" in home
    for s in ("danbooru", "gelbooru", "paheal", "rule34us", "rule34xxx"):
        db.set_session(s, True, "ok")
    db.set_setting("test_mode", "0")
    db.set_setting("danbooru_env", "live")
    db.create_post("a.png", status="done")
    assert "Configuração inicial" not in c.get("/").text


@pytest.mark.parametrize("level, force, expected", [
    ("", "0", "para saber se o post vai direto ao ar"),
    ("20", "0", "vai para a fila de moderação, com até 3 dias"),
    ("35", "0", "vai ao ar na hora"),
    ("35", "1", "você escolheu isso em Sites"),
])
def test_danbooru_notice_follows_real_level(client, db, level, force, expected):
    c, _ = client
    post_id = upload(c)
    db.set_session("danbooru", bool(level), "x")
    db.set_setting("danbooru_level", level)
    db.set_setting("danbooru_level_string", "Contributor" if level == "35" else "Member")
    db.set_setting("danbooru_force_pending", force)
    assert expected in c.get(f"/posts/{post_id}").text


def test_changing_danbooru_account_forgets_level(client, db):
    c, _ = client
    db.set_setting("danbooru_level", "35")
    c.post("/settings/danbooru", data={"login": "outra", "api_key": "k", "env": "live"})
    assert db.setting("danbooru_level") == ""
