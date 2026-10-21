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


def test_scores_persist_and_weak_tags_stay_out(db, tmp_path):
    tagger = FakeTagger(general=[("long_hair", 0.92), ("smile", 0.41)])
    tagger.result.weak = [("blush", 0.3), ("long_hair", 0.28)]
    post_id = db.create_post("x.png", tags="artist_name")
    run_suggestion(db, post_id, tagger)
    again = DB(tmp_path / "test.db").get_post(post_id)  # "recarregar": outra conexão com o mesmo banco
    assert again["tag_scores"] == {"long_hair": 0.92, "smile": 0.41}
    assert again["weak_tags"] == [["blush", 0.3]]  # o que já virou tag não aparece como fraca
    assert "blush" not in again["tag_list"]


def test_accepting_a_weak_tag_keeps_its_score(db):
    w = Worker(db, tagger=FakeTagger())
    c = TestClient(app_mod.create_app(db, w, start_worker=False))
    post_id = db.create_post("x.png", tags="a", weak_tags=[["blush", 0.3], ["hat", 0.27]])
    c.post(f"/posts/{post_id}", data={"action": "save", "tags": "a blush", "sites": ["danbooru"]})
    row = db.get_post(post_id)
    assert row["tag_scores"] == {"blush": 0.3} and row["weak_tags"] == [["hat", 0.27]]
    page = c.get(f"/posts/{post_id}").text
    assert "data-tag-field" in page and "hat" in page


def test_tagger_splits_weak_suggestions():
    from boorubot.tagger import Tagger

    class Session:
        def get_inputs(self):
            return [type("I", (), {"name": "x", "shape": [1, 4, 4, 3]})()]

        def run(self, _, inputs):
            import numpy as np
            return [np.array([[0.9, 0.5, 0.3, 0.2, 0.8, 0.95]])]

    t = Tagger()
    t._session = Session()
    t._labels = [("general", "9"), ("smile", "0"), ("blush", "0"), ("hat", "0"), ("some_char", "4"),
                 ("other_char", "4")]
    t._prepare = lambda path: None
    s = t.suggest(Path("x.png"))
    assert [g for g, _ in s.general] == ["smile"] and [w for w, _ in s.weak] == ["blush"]
    assert [c for c, _ in s.characters] == ["other_char"]  # personagem abaixo de 0,85 não entra


def test_no_character_marks_oc_and_mochi_explains(db):
    w = Worker(db, tagger=FakeTagger())
    c = TestClient(app_mod.create_app(db, w, start_worker=False))
    post_id = db.create_post("x.png", sites=["danbooru", "paheal"])
    run_suggestion(db, post_id, FakeTagger(characters=[]))
    row = db.get_post(post_id)
    assert row["original_character"] and row["oc_auto"]
    note = c.get(f"/posts/{post_id}/suggest/status").json()["mochi"]
    assert "nenhum personagem conhecido" in note and "paheal" in note


def test_character_found_does_not_mark_oc(db):
    post_id = db.create_post("x.png")
    run_suggestion(db, post_id, FakeTagger(characters=[("hatsune_miku", 0.97)]))
    assert not db.get_post(post_id)["original_character"]


def test_unchecked_oc_is_not_marked_again(db):
    w = Worker(db, tagger=FakeTagger())
    c = TestClient(app_mod.create_app(db, w, start_worker=False))
    post_id = db.create_post("x.png", tags="a")
    run_suggestion(db, post_id, FakeTagger())
    assert db.get_post(post_id)["original_character"]
    # ela desmarca (o checkbox desmarcado não vai no form)
    c.post(f"/posts/{post_id}", data={"action": "save", "tags": "a", "sites": ["danbooru"]})
    run_suggestion(db, post_id, FakeTagger())  # "sugerir de novo"
    row = db.get_post(post_id)
    assert not row["original_character"] and row["oc_touched"] and not row["oc_auto"]


def test_mochi_says_what_she_found_even_without_oc(db):
    post_id = db.create_post("x.png", sites=["danbooru"])
    run_suggestion(db, post_id, FakeTagger(characters=[("hatsune_miku", 0.97)]))
    from boorubot.suggest import mochi_note
    note = mochi_note(db.get_post(post_id))
    assert note.startswith("Achei ") and "personagem original" not in note
