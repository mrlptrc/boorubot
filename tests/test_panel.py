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


def test_queue_order_and_projection(db):
    db.set_setting("daily_time", "18:00")
    a, b, c = approve_n(db, 3)
    assert [p["id"] for p in db.queue()] == [a, b, c]
    proj = db.projected_times(at(14, 12))
    assert proj == {a: at(14, 18), b: at(15, 18), c: at(16, 18)}


def test_pc_off_for_days_publishes_only_one(db):
    """Painel fechado 3 dias: ao voltar sai 1 post, o resto segue 1 por dia."""
    db.set_setting("daily_time", "18:00")
    a, b, c = approve_n(db, 3)
    db.set_setting("last_published_at", at(11, 18).isoformat())
    assert db.next_due(at(14, 19))["id"] == a
    published(db, a, at(14, 19))
    assert db.next_due(at(14, 23)) is None          # já saiu um hoje
    assert db.next_due(at(15, 17, 59)) is None      # antes do horário
    assert db.next_due(at(15, 18))["id"] == b


def test_before_daily_time_waits(db):
    db.set_setting("daily_time", "18:00")
    approve_n(db, 1)
    assert db.next_due(at(14, 9)) is None


def test_publish_now_counts_as_todays_post(db):
    db.set_setting("daily_time", "18:00")
    a, b = approve_n(db, 2)
    published(db, a, at(14, 10))                     # "Publicar agora" de manhã
    assert db.next_due(at(14, 18)) is None           # o agendado de hoje vai para amanhã
    assert db.projected_times(at(14, 18, 30))[b] == at(15, 18)
    assert db.next_due(at(15, 18))["id"] == b


def test_min_gap_after_late_post(db):
    """Post às 23h; no dia seguinte espera completar 12h desde o último."""
    db.set_setting("daily_time", "08:00")
    a, b = approve_n(db, 2)
    published(db, a, at(14, 23))
    assert db.next_due(at(15, 8)) is None
    assert db.next_due(at(15, 11))["id"] == b
    assert db.projected_times(at(15, 8))[b] == at(15, 11)


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

    assert publish_post(db, post_id) == "partial"
    assert {p["site"]: p["status"] for p in db.publications(post_id)} == {
        "danbooru": "ok", "paheal": "skipped", "gelbooru": "failed"}

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
    assert db.get_post(post_id)["published_at"] and db.setting("last_published_at")


def test_scheduler_submits_only_one(db):
    db.set_setting("daily_time", "00:00")
    db.set_setting("test_mode", "0")
    approve_n(db, 3)
    w = Worker(db)
    w.tick()
    w.tick()  # job anterior ainda na fila: não empilha outro
    assert [j.kind for j in w.jobs.queue] == ["publish"]
    assert len(db.queue()) == 2


def test_home_shows_queue_positions(client, db):
    c, _ = client
    ids = [upload(c) for _ in range(2)]
    for i in ids:
        c.post(f"/posts/{i}", data={"action": "approve", "tags": "x", "sites": ["danbooru"]})
    home = c.get("/").text
    assert "1º · prev." in home and "2º · prev." in home
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
    c.post(f"/posts/{post_id}", data={"action": "approve", "tags": "x", "sites": ["danbooru"]})
    r = c.post(f"/posts/{post_id}", data={"action": "publish_now", "tags": "x", "sites": ["danbooru"]},
               follow_redirects=False)
    assert "erro=" in r.headers["location"] and w.jobs.empty()   # modo teste: produção bloqueada
    c.post(f"/posts/{post_id}", data={"action": "test", "tags": "x", "sites": ["danbooru"]})
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
    assert "desligado" in c.get("/sites").text
    c.post("/settings/startup", data={"action": "install"})
    assert (folder / startup.SCRIPT_NAME).exists()
    assert "ligado</span>" in c.get("/sites").text
    c.post("/settings/startup", data={"action": "remove"})
    assert not (folder / startup.SCRIPT_NAME).exists()
