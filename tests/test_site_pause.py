"""Falha de SITE (login caído, captcha, fora do ar) pausa o site; não gasta tentativas das artes."""

from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from boorubot import worker as worker_mod
from boorubot.db import DB
from boorubot.models import Post
from boorubot.scheduler import site_due
from boorubot.sites import forms
from boorubot.sites.base import Result, SiteUnavailable
from boorubot.sites.danbooru import Danbooru
from boorubot.web import app as app_mod
from boorubot.worker import Worker, publish_site

PNG = (Path(__file__).resolve().parents[1] / "queue" / "exemplo-001" / "arte.png").read_bytes()
EXAMPLE = Path(__file__).resolve().parents[1] / "queue" / "exemplo-001"


class Fake:
    mode = "down"
    calls = 0

    def __init__(self, name):
        self.name = name

    def check_rules(self, post):
        pass

    def publish(self, post, dry_run=True):
        Fake.calls += 1
        if Fake.mode == "down":
            raise SiteUnavailable("sem sessão no site: faça o login")
        return Result(self.name, ok=True, dry_run=False, message="ok", url="https://x/1")


@pytest.fixture
def db(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    (images / "a.png").write_bytes(PNG)
    monkeypatch.setattr(app_mod, "IMAGES_DIR", images)
    monkeypatch.setattr("boorubot.db.IMAGES_DIR", images)
    Fake.mode, Fake.calls = "down", 0
    monkeypatch.setattr(worker_mod, "build_site", lambda n, db=None, test=False: Fake(n))
    d = DB(tmp_path / "p.db")
    d.set_setting("test_mode", "0")
    d.set_setting("daily_time", "00:00")
    return d


def art(db, title):
    pid = db.create_post("a.png", title=title, tags="x", sites=["gelbooru"])
    db.approve(pid)
    return pid


def test_site_down_pauses_the_site_not_the_art(db):
    a, b = art(db, "a"), art(db, "b")
    assert publish_site(db, a, "gelbooru") == "site_down"
    assert db.failures_in_a_row(a, "gelbooru") == 0          # não gasta tentativa da arte
    assert not db.sessions()["gelbooru"]["ok"]                # site fica desconectado
    assert db.get_post(a)["status"] == "approved"             # arte continua na fila
    later = datetime.now() + timedelta(days=2)
    assert site_due(db, "gelbooru", later) is None            # site pausado: não tenta nada
    db.set_session("gelbooru", True, "sessão ativa")          # Entrar / Testar deu certo
    assert site_due(db, "gelbooru", later)["id"] == a         # retoma da MESMA arte


def test_a_night_logged_out_does_not_drop_any_art(db):
    ids = [art(db, t) for t in "abcde"]
    publish_site(db, ids[0], "gelbooru")
    w = Worker(db)
    for _ in range(10):                                       # várias rodadas do agendador
        w.tick()
        assert not any(j.kind == "publish_site" for j in w.jobs.queue)
    assert Fake.calls == 1
    assert all(db.failures_in_a_row(i, "gelbooru") == 0 for i in ids)


def test_paused_site_is_rechecked_every_30_minutes(db):
    art(db, "a")
    db.set_session("gelbooru", False, "sem sessão")
    w = Worker(db)
    w.tick()
    w.tick()
    assert [j.kind for j in w.jobs.queue] == ["session"]      # 1 re-teste, não empilha
    w.jobs.get()
    w.tick()
    assert w.jobs.empty()                                     # só daqui a 30 min
    db.set_setting("recheck_gelbooru", (datetime.now() - timedelta(minutes=31)).isoformat(timespec="seconds"))
    w.tick()
    assert [j.kind for j in w.jobs.queue] == ["session"]


def test_no_recheck_in_test_mode_or_without_pending_art(db):
    db.set_session("gelbooru", False, "sem sessão")
    w = Worker(db)
    w.tick()
    assert w.jobs.empty()                                     # nada na fila do site
    art(db, "a")
    db.set_setting("test_mode", "1")
    w.tick()
    assert w.jobs.empty()


def test_art_failure_still_counts(db, monkeypatch):
    class Broken(Fake):
        def publish(self, post, dry_run=True):
            raise RuntimeError("formulário recusou")

    monkeypatch.setattr(worker_mod, "build_site", lambda n, db=None, test=False: Broken(n))
    a = art(db, "a")
    assert publish_site(db, a, "gelbooru") == "failed"
    assert db.failures_in_a_row(a, "gelbooru") == 1 and db.sessions().get("gelbooru") is None


# --- o que é "falha do site" -------------------------------------------------------------------

class FakePage:
    def goto(self, url):
        pass

    def wait_for_timeout(self, ms):
        pass

    def locator(self, *a, **k):
        class L:
            def count(self):
                return 0
        return L()


@pytest.fixture
def form_env(monkeypatch, tmp_path):
    state = {"captcha": False}

    @contextmanager
    def fake_open(site, mode="headless"):
        yield FakePage()

    monkeypatch.setattr(forms, "open_page", fake_open)
    monkeypatch.setattr(forms, "captcha_present", lambda page: state["captcha"])
    monkeypatch.setattr(forms, "screenshot", lambda page, out: out)
    monkeypatch.setattr(forms, "run_dir", lambda site: tmp_path)
    return state


def test_form_without_session_is_site_down(form_env):
    with pytest.raises(SiteUnavailable, match="sem sessão"):
        forms.Gelbooru().publish(Post.load(EXAMPLE), dry_run=False)


def test_form_captcha_is_site_down(form_env):
    form_env["captcha"] = True
    with pytest.raises(SiteUnavailable, match="captcha"):
        forms.Gelbooru().publish(Post.load(EXAMPLE), dry_run=False)


def test_danbooru_without_key_or_rejected_key_is_site_down():
    with pytest.raises(SiteUnavailable):
        Danbooru("https://t").publish(Post.load(EXAMPLE), dry_run=False)
    refused = Danbooru("https://t", "u", "k", transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(SiteUnavailable):
        refused.publish(Post.load(EXAMPLE), dry_run=False)
    offline = Danbooru("https://t", "u", "k",
                       transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("sem rede"))))
    with pytest.raises(SiteUnavailable):
        offline.publish(Post.load(EXAMPLE), dry_run=False)


def danbooru_with(fail_step, status):
    """Danbooru simulado que responde `status` numa etapa: upload, poll ou post."""
    def handler(request):
        path = request.url.path
        if path == "/uploads.json":
            return httpx.Response(status if fail_step == "upload" else 201, json={"id": 1, "status": "pending"})
        if path == "/uploads/1.json":
            if fail_step == "poll":
                return httpx.Response(status)
            return httpx.Response(200, json={"id": 1, "status": "completed", "upload_media_assets": [{"id": 5}]})
        if path == "/posts.json":
            return httpx.Response(status if fail_step == "post" else 201, json={"id": 9})
        return httpx.Response(404)
    return Danbooru("https://t", "u", "k", transport=httpx.MockTransport(handler), poll_interval=0)


@pytest.mark.parametrize("step", ["upload", "poll", "post"])
@pytest.mark.parametrize("status", [500, 502, 503, 429])
def test_danbooru_outage_or_rate_limit_is_site_down(step, status):
    with pytest.raises(SiteUnavailable):
        danbooru_with(step, status).publish(Post.load(EXAMPLE), dry_run=False)


def test_danbooru_real_post_error_still_counts_against_the_art():
    from boorubot.sites.danbooru import DanbooruError

    with pytest.raises(DanbooruError) as info:
        danbooru_with("post", 422).publish(Post.load(EXAMPLE), dry_run=False)
    assert not isinstance(info.value, SiteUnavailable)
