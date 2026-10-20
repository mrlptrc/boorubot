import hashlib
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boorubot import worker as worker_mod
from boorubot.db import DB
from boorubot.sites.base import Result, SkipPost
from boorubot.web import app as app_mod
from boorubot.worker import Worker, publish_post, run_post_test

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


def test_upload_multiple_images_returns_json_and_adds_each_to_suggestion_queue(client, db):
    c, worker = client
    response = c.post("/posts", files=[
        ("image", ("one.png", PNG, "image/png")),
        ("image", ("two.jpg", PNG + b"different", "image/jpeg")),
    ], headers={"accept": "application/json"})

    assert response.status_code == 200
    posts = response.json()["posts"]
    assert [post["title"] for post in posts] == ["one", "two"]
    assert [db.get_post(post["id"])["status"] for post in posts] == ["draft", "draft"]
    assert worker.suggest_position(posts[0]["id"]) == 0
    assert worker.suggest_position(posts[1]["id"]) == 1
    states = c.get("/suggest/status.json", params={"ids": ",".join(str(post["id"]) for post in posts)}).json()
    assert [post["id"] for post in states["posts"]] == [post["id"] for post in posts]
    assert [post["state"] for post in states["posts"]] == ["pendente", "pendente"]


def test_repeated_image_warns_but_can_be_uploaded_as_new(client, db):
    c, _ = client
    first = c.post("/posts", files={"image": ("one.png", PNG, "image/png")},
                   headers={"accept": "application/json"}).json()["posts"][0]

    repeated = c.post("/posts", files={"image": ("copy.png", PNG, "image/png")},
                      headers={"accept": "application/json"}).json()
    assert repeated["posts"] == []
    assert repeated["duplicates"] == [{"name": "copy.png", "existing": {
        "id": first["id"], "published_at": "", "published_label": "", "where": "em revisão",
    }}]

    uploaded_again = c.post("/posts", data={"repetida": "1"},
                            files={"image": ("copy.png", PNG, "image/png")},
                            headers={"accept": "application/json"}).json()
    assert len(uploaded_again["posts"]) == 1
    assert uploaded_again["posts"][0]["id"] != first["id"]


def test_repeated_published_image_shows_publication_date(client, db):
    c, _ = client
    post_id = c.post("/posts", files={"image": ("one.png", PNG, "image/png")},
                     headers={"accept": "application/json"}).json()["posts"][0]["id"]
    db.mark_post_published_at(post_id, datetime(2026, 10, 12, 18, 0))
    response = c.post("/posts", files={"image": ("copy.png", PNG, "image/png")},
                      headers={"accept": "application/json"}).json()
    assert response["duplicates"][0]["existing"]["published_label"] == "12/10"


def test_new_art_window_is_available_on_every_page(client):
    c, _ = client
    for path in ("/", "/historico"):
        page = c.get(path).text
        assert 'id="new-art"' in page
        assert 'src="/static/new-art.js"' in page
        assert 'data-new-art' in page


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
    assert row["status"] == "approved" and row["queue_pos"] is not None
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


def at(day, hh=12, mm=0):
    return datetime(2026, 10, day, hh, mm)


def approve_n(db, n):
    ids = [db.create_post(f"{i}.png", tags="x", sites=["danbooru"]) for i in range(n)]
    for i in ids:
        db.approve(i)
    return ids


def published(db, post_id, when):
    """O que o worker faz ao publicar: tira da fila e marca o horário."""
    db.update_post(post_id, status="done", queue_pos=None)
    db.mark_published(post_id, when)


def test_move_in_queue(db):
    a, b, c = approve_n(db, 3)
    db.move_in_queue(c, "first")
    db.move_in_queue(a, "last")
    assert [p["id"] for p in db.queue()] == [c, b, a]


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

    # o Gelbooru falhou: a arte continua na fila só para ele (tentativa automática depois)
    assert publish_post(db, post_id) == "approved"
    assert {p["site"]: p["status"] for p in db.publications(post_id)} == {
        "danbooru": "ok", "paheal": "skipped", "gelbooru": "failed"}
    assert db.pending_sites(db.get_post(post_id)) == ["gelbooru"]

    outcomes["gelbooru"] = "ok"
    assert publish_post(db, post_id) == "done"
    assert [p["site"] for p in db.publications(post_id)].count("danbooru") == 1  # não reenviou


def test_danbooru_credentials_saved_in_panel(client, db):
    c, w = client
    r = c.post("/settings/danbooru", data={"login": " artista ", "api_key": "abc123xyz"}, follow_redirects=False)
    assert r.status_code == 303
    assert db.setting("danbooru_login") == "artista"
    assert [j.kind for j in w.jobs.queue] == ["session"]  # já agenda o teste
    # chave em branco mantém a salva
    c.post("/settings/danbooru", data={"login": "artista", "api_key": ""})
    assert db.setting("danbooru_api_key") == "abc123xyz"
    page = c.get("/sites").text
    assert "abc123xyz" not in page and "••••3xyz" in page


def test_danbooru_test_and_live_accounts_never_mix(db):
    """Produção usa SEMPRE o Danbooru real com a conta real; teste usa SEMPRE o testbooru."""
    from boorubot.config import build_site

    db.set_setting("danbooru_login", "artista")
    db.set_setting("danbooru_api_key", "real")
    db.set_setting("danbooru_test_login", "tecnico")
    db.set_setting("danbooru_test_api_key", "teste")
    live, test = build_site("danbooru", db), build_site("danbooru", db, test=True)
    assert (live.base_url, live.login, live.api_key) == ("https://danbooru.donmai.us", "artista", "real")
    assert (test.base_url, test.login, test.api_key) == ("https://testbooru.donmai.us", "tecnico", "teste")


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
    for site in ("danbooru", "gelbooru", "paheal", "rule34us", "rule34xxx"):
        db.set_session(site, True, "ok")
    db.set_setting("test_mode", "0")
    post_id = db.create_post("a.png")
    db.add_test_run(post_id, "danbooru", "dry_run")
    assert "Configuração inicial" not in c.get("/").text


@pytest.mark.parametrize("level, force, expected", [
    ("", "0", "para saber se vai direto ao ar"),
    ("20", "0", "vai para a fila de moderação (até 3 dias)"),
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


def test_failed_everywhere_does_not_use_the_day(db, monkeypatch):
    (app_mod.IMAGES_DIR / "a.png").write_bytes(PNG)
    monkeypatch.setattr(worker_mod, "build_site", lambda n, db=None: FakeSite(n, "boom"))
    db.set_setting("test_mode", "0")
    post_id = db.create_post("a.png", tags="x", sites=["danbooru"])
    assert publish_post(db, post_id) == "failed"
    assert db.setting("last_published_at") == ""     # nada saiu: o dia continua livre


def test_real_publish_marks_the_day(db, monkeypatch):
    (app_mod.IMAGES_DIR / "a.png").write_bytes(PNG)
    monkeypatch.setattr(worker_mod, "build_site", lambda n, db=None: FakeSite(n, "ok"))
    db.set_setting("test_mode", "0")
    post_id = db.create_post("a.png", tags="x", sites=["danbooru"])
    publish_post(db, post_id)
    assert db.get_post(post_id)["published_at"] and db.site_last_published("danbooru")


def test_scheduler_submits_only_one(db):
    db.set_setting("daily_time", "00:00")
    db.set_setting("test_mode", "0")
    approve_n(db, 3)
    w = Worker(db)
    w.tick()
    w.tick()  # job anterior ainda na fila: não empilha outro
    assert [j.kind for j in w.jobs.queue] == ["publish_site"]


def test_home_shows_queue_positions(client, db):
    c, _ = client
    ids = [upload(c) for _ in range(2)]
    for i in ids:
        c.post(f"/posts/{i}", data={"action": "approve", "tags": "x", "rating": "general", "sites": ["danbooru"]})
    home = c.get("/").text
    assert "1º na fila" in home and "2º na fila" in home
    assert "1º de 2" in c.get(f"/posts/{ids[0]}").text
    c.post(f"/posts/{ids[1]}", data={"action": "first", "tags": "x", "sites": ["danbooru"]})
    assert [p["id"] for p in db.queue()] == [ids[1], ids[0]]


def test_test_mode_pauses_the_queue(db):
    db.set_setting("daily_time", "00:00")
    approve_n(db, 3)
    w = Worker(db)
    w.tick()
    assert w.jobs.empty() and len(db.queue()) == 3


# --- teste e produção nunca se misturam ------------------------------------------

class Recorder(FakeSite):
    calls = []

    def __init__(self, name, test):
        super().__init__(name, "ok")
        self.test = test

    def publish(self, post, dry_run=True):
        Recorder.calls.append((self.name, self.test, dry_run))
        return super().publish(post, dry_run)


@pytest.fixture
def recorder(monkeypatch):
    Recorder.calls = []
    monkeypatch.setattr(worker_mod, "build_site", lambda n, db=None, test=False: Recorder(n, test))
    return Recorder


def test_testing_a_queued_post_changes_nothing(db, recorder):
    (app_mod.IMAGES_DIR / "a.png").write_bytes(PNG)
    post_id = db.create_post("a.png", tags="x", sites=["danbooru", "gelbooru"])
    db.approve(post_id)
    before = db.get_post(post_id)
    run_post_test(db, post_id)
    after = db.get_post(post_id)
    assert (after["status"], after["queue_pos"], after["published_at"]) == ("approved", before["queue_pos"], None)
    assert db.publications(post_id) == []                     # histórico real intocado
    assert db.setting("last_published_at") == ""              # ritmo intocado
    assert {r["site"] for r in db.test_runs(post_id)} == {"danbooru", "gelbooru"}
    assert all(test for _, test, _ in recorder.calls)         # só sites em modo teste


def test_test_post_uploads_to_testbooru_only_with_test_account(db, recorder):
    (app_mod.IMAGES_DIR / "a.png").write_bytes(PNG)
    post_id = db.create_post("a.png", tags="x", sites=["danbooru", "gelbooru"])
    run_post_test(db, post_id)
    assert ("danbooru", True, True) in recorder.calls          # sem conta de teste: só simula
    recorder.calls.clear()
    db.set_setting("danbooru_test_api_key", "k")
    run_post_test(db, post_id)
    assert ("danbooru", True, False) in recorder.calls         # envio real, mas no testbooru
    assert ("gelbooru", True, True) in recorder.calls          # forms: sempre simulação no teste


def test_production_refuses_to_run_in_test_mode(db, recorder):
    (app_mod.IMAGES_DIR / "a.png").write_bytes(PNG)
    post_id = db.create_post("a.png", tags="x", sites=["danbooru"])
    db.approve(post_id)
    assert db.test_mode
    assert publish_post(db, post_id) == "blocked"
    assert recorder.calls == [] and db.get_post(post_id)["status"] == "approved"


def test_production_always_uses_live_sites(db, recorder):
    (app_mod.IMAGES_DIR / "a.png").write_bytes(PNG)
    db.set_setting("test_mode", "0")
    db.set_setting("danbooru_test_api_key", "k")
    post_id = db.create_post("a.png", tags="x", sites=["danbooru"])
    publish_post(db, post_id)
    assert recorder.calls == [("danbooru", False, False)]
    assert db.test_runs(post_id) == []


def test_panel_test_button_and_publish_guard(client, db):
    c, w = client
    post_id = upload(c)
    c.post(f"/posts/{post_id}", data={"action": "approve", "tags": "x", "rating": "general", "sites": ["danbooru"]})
    r = c.post(f"/posts/{post_id}", data={"action": "publish_now", "tags": "x", "rating": "general", "sites": ["danbooru"]},
               follow_redirects=False)
    assert "erro=" in r.headers["location"] and w.jobs.empty()   # modo teste: produção bloqueada
    c.post(f"/posts/{post_id}", data={"action": "test", "tags": "x", "rating": "general", "sites": ["danbooru"]})
    assert [j.kind for j in w.jobs.queue] == ["test"]
    assert db.get_post(post_id)["status"] == "approved"


def test_history_shows_only_production(client, db):
    c, _ = client
    post_id = db.create_post("a.png", title="arte-x")
    db.add_test_run(post_id, "danbooru", "dry_run", message="simulado-123")
    assert "simulado-123" not in c.get("/historico").text
    assert "simulado-123" in c.get(f"/posts/{post_id}").text   # aparece só na seção de testes do post


def test_sites_page_toggles_startup(client, monkeypatch, tmp_path):
    from boorubot import startup

    folder = tmp_path / "Startup"
    uv = tmp_path / "uv.exe"
    uv.write_text("")
    monkeypatch.setattr(startup, "startup_dir", lambda: folder)
    monkeypatch.setenv("UV", str(uv))
    c, _ = client
    assert 'data-setting="startup">' in c.get("/ajustes").text          # interruptor desligado
    c.post("/settings/startup", data={"action": "install"})
    assert (folder / startup.SCRIPT_NAME).exists()
    assert 'value="true" checked data-setting="startup"' in c.get("/ajustes").text
    c.post("/settings/startup", data={"action": "remove"})
    assert not (folder / startup.SCRIPT_NAME).exists()


def test_browser_mode_setting_from_sites_page(client, db):
    c, _ = client
    page = c.get("/sites").text
    assert "invisível (sem janela)" in page and "janela escondida" in page
    c.post("/settings/browser/gelbooru", data={"mode": "visible"})
    assert db.setting("browser_mode_gelbooru") == "visible"
    assert c.post("/settings/browser/danbooru", data={"mode": "visible"}).status_code == 404
    assert c.post("/settings/browser/gelbooru", data={"mode": "xyz"}).status_code == 404


def test_guide_explains_sleep_and_vps(client):
    c, _ = client
    guide = c.get("/guia").text
    assert 'id="pc-acordado"' in guide and "suspensão" in guide and "VPS" in guide
    assert "/guia#pc-acordado" in c.get("/ajustes").text


def test_search_index_lists_posts_for_ctrl_k(client, db):
    c, _ = client
    post_id = upload(c)
    c.post(f"/posts/{post_id}", data={"action": "save", "title": "sereia", "tags": "1girl mermaid", "sites": ["danbooru"]})
    found = {p["id"]: p for p in c.get("/busca.json").json()["posts"]}
    assert found[post_id]["title"] == "sereia" and "mermaid" in found[post_id]["tags"]
    assert found[post_id]["status_label"] == "rascunho"


def test_dangerous_buttons_ask_for_confirmation(client, db):
    c, _ = client
    post_id = upload(c)
    page = c.get(f"/posts/{post_id}").text
    # apagar não pergunta mais: tem "desfazer" logo depois (etapa 9)
    assert 'value="delete"' in page and "return confirm(" not in page


def test_new_post_has_no_rating_and_cannot_be_approved_without_one(client, db):
    c, _ = client
    post_id = upload(c)
    assert db.get_post(post_id)["rating"] == ""
    for action in ("approve", "test"):
        r = c.post(f"/posts/{post_id}", data={"action": action, "tags": "x", "sites": ["danbooru"]},
                   follow_redirects=False)
        assert "rating" in r.headers["location"]
    assert db.get_post(post_id)["status"] == "draft" and c.app.state.worker.jobs.empty()
    c.post(f"/posts/{post_id}", data={"action": "approve", "tags": "x", "rating": "sensitive", "sites": ["danbooru"]})
    assert db.get_post(post_id)["status"] == "approved"


def test_existing_posts_keep_their_rating_and_receive_image_hash(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "old.db"
    images = tmp_path / "images"
    images.mkdir()
    (images / "a.png").write_bytes(PNG)
    monkeypatch.setattr("boorubot.db.IMAGES_DIR", images)
    old = sqlite3.connect(path)  # banco de antes da v2: sem as colunas novas
    old.executescript("""CREATE TABLE posts (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, image TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '', rating TEXT NOT NULL DEFAULT 'general',
        source TEXT NOT NULL DEFAULT '', commentary_title TEXT NOT NULL DEFAULT '',
        commentary_description TEXT NOT NULL DEFAULT '', original_character INTEGER NOT NULL DEFAULT 0,
        sites TEXT NOT NULL DEFAULT '[]', site_tags TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'draft',
        scheduled_for TEXT);
        INSERT INTO posts (created_at, image, tags, rating) VALUES ('2026-10-01T10:00:00', 'a.png', 'x', 'explicit');
        INSERT INTO posts (created_at, image, tags) VALUES ('2026-10-01T11:00:00', 'b.png', 'x');""")
    old.commit()
    old.close()
    db = DB(path)
    DB(path)  # a migração roda de novo sem erro
    assert [p["rating"] for p in sorted(db.list_posts(), key=lambda p: p["id"])] == ["explicit", "general"]
    assert db.get_post(1)["rating_suggestion"] == "" and db.get_post(1)["tag_scores"] == {}
    assert db.get_post(1)["image_md5"] == hashlib.md5(PNG, usedforsecurity=False).hexdigest()


def test_suggestion_only_suggests_the_rating(db):
    from boorubot.suggest import run_suggestion
    from boorubot.tagger import Suggestion

    class T:
        def suggest(self, path):
            return Suggestion("questionable", {"questionable": 0.91, "general": 0.05}, [("smile", 0.8)], [])

    post_id = db.create_post("x.png", rating="")
    run_suggestion(db, post_id, T())
    row = db.get_post(post_id)
    assert row["rating"] == "" and row["rating_suggestion"] == "questionable" and row["rating_confidence"] == 0.91


def test_partial_save_changes_only_the_sent_field(client, db):
    c, _ = client
    post_id = db.create_post("x.png", title="A", tags="a b", rating="general", sites=["danbooru"])
    r = c.patch(f"/posts/{post_id}", json={"tags": "a b, long hair"})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["saved_at"]
    row = db.get_post(post_id)
    assert row["tags"] == "a_b long_hair"  # mesma normalização do form
    assert (row["title"], row["rating"], row["sites"]) == ("A", "general", ["danbooru"])
    c.patch(f"/posts/{post_id}", json={"sites": ["paheal", "nada"], "rating": "explicit"})
    row = db.get_post(post_id)
    assert row["sites"] == ["paheal"] and row["rating"] == "explicit" and row["tags"] == "a_b long_hair"
    assert c.patch(f"/posts/{post_id}", json={"rating": "inventado"}).json()["post"]["rating"] == ""


def test_published_post_cannot_be_edited(client, db):
    c, _ = client
    post_id = db.create_post("x.png", title="A", status="done")
    r = c.patch(f"/posts/{post_id}", json={"title": "B"})
    assert r.status_code == 409 and db.get_post(post_id)["title"] == "A"
    assert c.patch("/posts/999", json={"title": "B"}).status_code == 404


def test_editor_has_no_save_button(client, db):
    c, _ = client
    page = c.get(f"/posts/{upload(c)}").text
    assert 'value="save"' not in page and "data-save-status" in page


def test_editor_layout_lists_every_site_with_its_state(client, db):
    c, _ = client
    post_id = upload(c)
    db.set_session("gelbooru", False, "login caiu")
    db.update_post(post_id, original_character=True)
    page = c.get(f"/posts/{post_id}").text
    assert page.count('<li class="bb-site') == 5 and 'role="switch"' in page
    assert 'formaction="/sites/gelbooru/login"' in page and "pediu login de novo" in page
    assert "fica de fora: não aceita personagem original" in page
    # sem rating ainda: a barra diz o que falta e quando sai depois de aprovar
    assert "falta escolher o rating" in page and "depois sai" in page
    assert "Aprovar e agendar" in page and "Mais ações" in page


def test_replace_image_keeps_fields_and_asks_ai_again(client, db):
    c, w = client
    post_id = upload(c)
    w.suggestions.queue.clear()
    db.update_post(post_id, tags="a b")
    old = db.get_post(post_id)["image"]
    r = c.post(f"/posts/{post_id}/imagem", files={"image": ("nova.png", PNG, "image/png")}, follow_redirects=False)
    row = db.get_post(post_id)
    assert r.status_code == 303 and row["image"] != old and row["tags"] == "a b"
    assert not (app_mod.IMAGES_DIR / old).exists() and list(w.suggestions.queue) == [post_id]


def test_latest_test_result_shows_on_each_site_row(client, db):
    c, _ = client
    post_id = upload(c)
    db.add_test_run(post_id, "gelbooru", "failed", message="campo de tags não encontrado")
    db.add_test_run(post_id, "danbooru", "failed", message="antigo")
    db.add_test_run(post_id, "danbooru", "dry_run", message="simulado")
    page = c.get(f"/posts/{post_id}").text
    row = lambda site: page.split(f'data-site="{site}"')[1].split("</li>")[0]
    assert "teste falhou às" in row("gelbooru") and "campo de tags não encontrado" in row("gelbooru")
    assert "teste ok às" in row("danbooru") and "antigo" not in row("danbooru")  # só o mais recente
    assert "teste" not in row("paheal").replace("site-row-text", "")


def test_approve_goes_to_the_oldest_remaining_draft(client, db):
    c, _ = client
    first, second, third = upload(c), upload(c), upload(c)
    db.update_post(third, created_at="2026-01-01T00:00:00")  # o mais antigo vem primeiro
    r = c.post(f"/posts/{second}", data={"action": "approve", "tags": "x", "rating": "general",
                                          "sites": ["danbooru"]}, follow_redirects=False)
    target, _, query = r.headers["location"].partition("?")
    assert target == f"/posts/{third}" and "Faltam%202%20para%20revisar" in query
    assert "Faltam 2 para revisar" in c.get(r.headers["location"]).text
    for pid in (third, first):
        r = c.post(f"/posts/{pid}", data={"action": "approve", "tags": "x", "rating": "general",
                                           "sites": ["danbooru"]}, follow_redirects=False)
    assert r.headers["location"].startswith("/?aviso=")  # sem rascunhos: volta para o Início


def test_delete_hides_everywhere_and_undo_restores(client, db):
    from datetime import timedelta

    from boorubot import calendar_view, dashboard, insights

    c, _ = client
    post_id = upload(c)
    db.update_post(post_id, tags="x", rating="general")
    db.approve(post_id)
    db.add_publication(post_id, "danbooru", "ok", "https://danbooru.donmai.us/posts/1")
    pub_id = db.publications(post_id)[0]["id"]
    db.add_metric_snapshot(pub_id, datetime.now().isoformat(timespec="seconds"), 5, 5, 0, 3)
    r = c.post(f"/posts/{post_id}", data={"action": "delete"}, follow_redirects=False)
    assert r.headers["location"] == f"/?aviso=Arte%20apagada&desfazer={post_id}"
    home = c.get(r.headers["location"]).text
    assert f'action="/posts/{post_id}/desfazer"' in home
    assert post_id not in [p["id"] for p in db.list_posts()] and post_id not in [p["id"] for p in db.queue()]
    assert post_id not in [p["id"] for p in c.get("/busca.json").json()["posts"]]
    assert "danbooru.donmai.us/posts/1" not in c.get("/historico").text
    assert insights.build(db, "tudo")["posts"] == 0 and not db.all_metric_snapshots()
    assert dashboard.week_favorites(db, datetime.now())["total"] == 0
    other = upload(c)
    db.delete_post(other)
    assert calendar_view.build(db)["suggestion"] is None  # rascunho apagado não é sugerido no calendário
    assert all(p["id"] != other for p in dashboard.build(db)["review"])
    # o ritmo do site continua vendo o envio de hoje (não deixa sair outro post no mesmo dia)
    assert db.site_last_published("danbooru") is not None

    c.post(f"/posts/{post_id}/desfazer")
    row = db.get_post(post_id)
    assert row["status"] == "approved" and row["deleted_at"] is None
    assert post_id in [p["id"] for p in db.queue()]


def test_purge_only_after_24h(client, db):
    from datetime import timedelta

    c, w = client
    draft, published = upload(c), upload(c)
    db.add_publication(published, "danbooru", "ok")
    images = {pid: db.get_post(pid)["image"] for pid in (draft, published)}
    for pid in (draft, published):
        c.post(f"/posts/{pid}", data={"action": "delete"})
    assert w.purge() == 0 and db.get_post(draft)  # antes do prazo nada some
    assert (app_mod.IMAGES_DIR / images[draft]).exists()
    assert w.purge(datetime.now() + timedelta(hours=25)) == 2
    assert db.get_post(draft) is None and not (app_mod.IMAGES_DIR / images[draft]).exists()
    # a que já saiu fica escondida (o ritmo do site conta com ela), sem a imagem
    kept = db.get_post(published)
    assert kept["status"] == "deleted" and kept["image"] == "" and db.publications(published)
    assert not (app_mod.IMAGES_DIR / images[published]).exists()
    assert c.post(f"/posts/{draft}/desfazer", follow_redirects=False).headers["location"].startswith("/?aviso=")


def test_deleted_post_is_never_sent(db, monkeypatch):
    post_id = db.create_post("x.png", tags="x", sites=["danbooru"], status="approved")
    db.set_setting("test_mode", "0")
    db.delete_post(post_id)
    called = []
    monkeypatch.setattr(worker_mod, "build_site", lambda *a, **k: called.append(a))
    assert worker_mod.publish_site(db, post_id, "danbooru") == "missing"
    assert publish_post(db, post_id) == "missing" and not called
