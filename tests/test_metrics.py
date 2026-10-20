"""Métricas do Danbooru: só produção, em lote, com a API simulada."""

from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from boorubot import metrics
from boorubot.db import DB
from boorubot.metrics import collect_danbooru_metrics, danbooru_post_id
from boorubot.web import app as app_mod
from boorubot.worker import Worker


@pytest.fixture
def db(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    monkeypatch.setattr(app_mod, "IMAGES_DIR", images)
    return DB(tmp_path / "m.db")


def live_client(handler):
    return httpx.Client(base_url="https://danbooru.donmai.us", headers={"User-Agent": "boorubot/0.1 (post-automation)"},
                        transport=httpx.MockTransport(handler))


def test_post_id_only_from_live_danbooru():
    assert danbooru_post_id("https://danbooru.donmai.us/posts/123") == 123
    assert danbooru_post_id("https://testbooru.donmai.us/posts/123") is None
    assert danbooru_post_id("https://gelbooru.com/index.php?page=post&s=view&id=5") is None
    assert danbooru_post_id("") is None


def test_collects_only_real_danbooru_publications(db):
    post_id = db.create_post("a.png")
    db.add_publication(post_id, "danbooru", "ok", "https://danbooru.donmai.us/posts/11")
    db.add_publication(post_id, "danbooru", "ok", "https://danbooru.donmai.us/posts/22")
    db.add_publication(post_id, "danbooru", "failed", message="caiu")
    db.add_publication(post_id, "gelbooru", "ok", "https://gelbooru.com/index.php?page=post&s=view&id=9")
    db.add_publication(post_id, "danbooru", "ok", "https://testbooru.donmai.us/posts/33")  # defensivo
    db.add_test_run(post_id, "danbooru", "test_upload", "https://testbooru.donmai.us/posts/44")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=[
            {"id": 11, "score": 17, "up_score": 18, "down_score": -1, "fav_count": 16},
            {"id": 22, "score": 3, "up_score": 3, "down_score": 0, "fav_count": 2},
        ])

    assert collect_danbooru_metrics(db, live_client(handler)) == 2
    assert len(calls) == 1
    req = calls[0]
    assert req.url.host == "danbooru.donmai.us" and req.url.path == "/posts.json"
    assert req.url.params["tags"] == "id:11,22 status:any"
    assert req.headers["user-agent"].startswith("boorubot/")
    assert "authorization" not in req.headers  # dado público: sem conta nenhuma

    latest = db.latest_metrics()
    by_score = sorted((m["score"], m["fav_count"]) for m in latest.values())
    assert by_score == [(3, 2), (17, 16)]
    assert db.setting("metrics_last_run")


def test_batches_of_100(db):
    post_id = db.create_post("a.png")
    for i in range(1, 151):
        db.add_publication(post_id, "danbooru", "ok", f"https://danbooru.donmai.us/posts/{i}")
    sizes = []

    def handler(request):
        ids = request.url.params["tags"].split()[0][3:].split(",")
        sizes.append(len(ids))
        return httpx.Response(200, json=[{"id": int(i), "score": 1, "up_score": 1, "down_score": 0, "fav_count": 0}
                                         for i in ids])

    assert collect_danbooru_metrics(db, live_client(handler)) == 150
    assert sizes == [100, 50]


def test_snapshots_accumulate_and_latest_wins(db):
    post_id = db.create_post("a.png")
    db.add_publication(post_id, "danbooru", "ok", "https://danbooru.donmai.us/posts/7")
    for score in (1, 5):
        collect_danbooru_metrics(db, live_client(
            lambda r, s=score: httpx.Response(200, json=[{"id": 7, "score": s, "up_score": s, "down_score": 0,
                                                          "fav_count": s}])))
    pub_id = db.publications(post_id)[0]["id"]
    assert db.latest_metrics()[pub_id]["score"] == 5
    assert db._all("SELECT COUNT(*) AS n FROM metric_snapshots")[0]["n"] == 2


def test_no_publications_makes_no_request(db):
    assert collect_danbooru_metrics(db, live_client(lambda r: pytest.fail("não devia chamar a API"))) == 0


def test_api_error_propagates(db):
    post_id = db.create_post("a.png")
    db.add_publication(post_id, "danbooru", "ok", "https://danbooru.donmai.us/posts/7")
    with pytest.raises(httpx.HTTPStatusError):
        collect_danbooru_metrics(db, live_client(lambda r: httpx.Response(500)))
    assert db.setting("metrics_last_run") == ""  # falhou: tenta de novo no próximo ciclo


def test_scheduler_runs_metrics_once_a_day(db):
    post_id = db.create_post("a.png")
    w = Worker(db)
    w.tick()
    assert w.jobs.empty()  # sem publicação do Danbooru: nada a coletar
    db.add_publication(post_id, "danbooru", "ok", "https://danbooru.donmai.us/posts/7")
    w.tick()
    w.tick()
    assert [j.kind for j in w.jobs.queue] == ["metrics"]  # não empilha
    w.jobs.get()
    db.set_setting("metrics_last_run", datetime.now().isoformat(timespec="seconds"))
    w.tick()
    assert w.jobs.empty()  # já rodou hoje


def test_history_shows_metrics_and_refresh_button(db):
    w = Worker(db)
    c = TestClient(app_mod.create_app(db, w, start_worker=False))
    post_id = db.create_post("a.png", title="arte")
    db.add_publication(post_id, "danbooru", "ok", "https://danbooru.donmai.us/posts/7")
    pub_id = db.publications(post_id)[0]["id"]
    db.add_metric_snapshot(pub_id, "2026-10-14T10:00:00", 42, 43, -1, 17)
    page = c.get("/historico").text
    assert "<b>42</b> · ♥ 17" in page
    assert "<b>42</b> · 17 favoritos" in c.get(f"/posts/{post_id}").text
    c.post("/metricas/atualizar")
    assert [j.kind for j in w.jobs.queue] == ["metrics"]


def test_metrics_module_never_points_to_testbooru():
    assert "testbooru" not in metrics.LIVE_POST_URL.pattern
