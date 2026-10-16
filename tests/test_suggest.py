import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boorubot import worker as worker_mod
from boorubot.db import DB
from boorubot.suggest import run_suggestion
from boorubot.tagger import Suggestion
from boorubot.web import app as app_mod
from boorubot.worker import Worker

PNG = (Path(__file__).resolve().parents[1] / "queue" / "exemplo-001" / "arte.png").read_bytes()


class FakeTagger:
    model_ready = True

    def __init__(self, general=(("long_hair", 0.9), ("smile", 0.5)), characters=(), rating="general"):
        self.result = Suggestion(rating=rating, rating_scores={rating: 0.91},
                                 general=list(general), characters=list(characters))
        self.calls = 0

    def suggest(self, path):
        self.calls += 1
        return self.result


@pytest.fixture
def db(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    monkeypatch.setattr(app_mod, "IMAGES_DIR", images)
    monkeypatch.setattr("boorubot.db.IMAGES_DIR", images)
    monkeypatch.setattr("boorubot.suggest.IMAGES_DIR", images)
    return DB(tmp_path / "test.db")


def upload(c) -> int:
    r = c.post("/posts", files={"image": ("arte.png", PNG, "image/png")}, follow_redirects=False)
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_upload_queues_the_suggestion(db):
    w = Worker(db, tagger=FakeTagger())
    c = TestClient(app_mod.create_app(db, w, start_worker=False))
    post_id = upload(c)
    assert list(w.suggestions.queue) == [post_id]
    assert w.jobs.empty()  # nada na fila de publicação
    status = c.get(f"/posts/{post_id}/suggest/status").json()
    assert status["state"] == "pendente" and status["waiting"] == 0


def test_suggestion_adds_tags_without_removing_existing(db):
    post_id = db.create_post("x.png", tags="artist_name smile")
    assert run_suggestion(db, post_id, FakeTagger()) == "pronta"
    row = db.get_post(post_id)
    assert row["tag_list"] == ["artist_name", "smile", "long_hair"]
    assert row["suggest_state"] == "pronta"


def test_failed_suggestion_is_recorded(db):
    class Broken:
        def suggest(self, path):
            raise OSError("sem internet")

    post_id = db.create_post("x.png", tags="a")
    assert run_suggestion(db, post_id, Broken()) == "falhou"
    row = db.get_post(post_id)
    assert row["suggest_state"] == "falhou" and "sem internet" in row["suggest_error"] and row["tags"] == "a"


def test_suggest_again_requeues(db):
    w = Worker(db, tagger=FakeTagger())
    c = TestClient(app_mod.create_app(db, w, start_worker=False))
    post_id = db.create_post("x.png")
    assert c.post(f"/posts/{post_id}/suggest").json()["state"] == "pendente"
    c.post(f"/posts/{post_id}/suggest")  # clicar de novo não enfileira duas vezes
    assert list(w.suggestions.queue) == [post_id]


def test_interrupted_suggestions_resume_on_start(db):
    post_id = db.create_post("x.png")
    db.set_suggest_state(post_id, "analisando")
    w = Worker(db, tagger=FakeTagger(), scheduler_interval=3600)
    w.start()
    try:
        for _ in range(50):
            if db.get_post(post_id)["suggest_state"] == "pronta":
                break
            time.sleep(0.05)
        assert db.get_post(post_id)["suggest_state"] == "pronta"
    finally:
        w.stop()


def test_publish_never_waits_for_a_suggestion(db, monkeypatch):
    release, published = threading.Event(), threading.Event()

    class SlowTagger(FakeTagger):
        def suggest(self, path):  # primeira vez: baixando 380 MB…
            release.wait(5)
            return super().suggest(path)

    monkeypatch.setattr(worker_mod, "publish_post", lambda db, post_id: published.set())
    w = Worker(db, tagger=SlowTagger(), scheduler_interval=3600)
    post_id = db.create_post("x.png")
    w.start()
    try:
        w.submit_suggest(post_id)
        for _ in range(50):
            if w.suggesting == post_id:
                break
            time.sleep(0.02)
        assert w.suggesting == post_id
        w.submit("publish", post_id)
        assert published.wait(2), "o publish ficou esperando a IA"
        assert db.get_post(post_id)["suggest_state"] == "analisando"
    finally:
        release.set()
        w.stop()
