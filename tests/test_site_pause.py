"""Falha de SITE (login caído, captcha, fora do ar) pausa o site; não gasta tentativas das artes."""

from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
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
    d.set_setting("assisted_rule34xxx", "")  # aqui o rule34.xxx é enviado pelo robô
    return d


def art(db, title):
    pid = db.create_post("a.png", title=title, tags="x", sites=["paheal"])
    db.approve(pid)
    return pid


def test_site_down_pauses_the_site_not_the_art(db):
    a, b = art(db, "a"), art(db, "b")
    assert publish_site(db, a, "paheal") == "site_down"
    assert db.failures_in_a_row(a, "paheal") == 0          # não gasta tentativa da arte
    assert not db.sessions()["paheal"]["ok"]                # site fica desconectado
    assert db.get_post(a)["status"] == "approved"             # arte continua na fila
    later = datetime.now() + timedelta(days=2)
    assert site_due(db, "paheal", later) is None            # site pausado: não tenta nada
    db.set_session("paheal", True, "sessão ativa")          # Entrar / Testar deu certo
    assert site_due(db, "paheal", later)["id"] == a         # retoma da MESMA arte


def test_a_night_logged_out_does_not_drop_any_art(db):
    ids = [art(db, t) for t in "abcde"]
    publish_site(db, ids[0], "paheal")
    w = Worker(db)
    for _ in range(10):                                       # várias rodadas do agendador
        w.tick()
        assert not any(j.kind == "publish_site" for j in w.jobs.queue)
    assert Fake.calls == 1
    assert all(db.failures_in_a_row(i, "paheal") == 0 for i in ids)


def test_paused_site_is_rechecked_every_30_minutes(db):
    art(db, "a")
    db.set_session("paheal", False, "sem sessão")
    w = Worker(db)
    w.tick()
    w.tick()
    assert [j.kind for j in w.jobs.queue] == ["session"]      # 1 re-teste, não empilha
    w.jobs.get()
    w.tick()
    assert w.jobs.empty()                                     # só daqui a 30 min
    db.set_setting("recheck_paheal", (datetime.now() - timedelta(minutes=31)).isoformat(timespec="seconds"))
    w.tick()
    assert [j.kind for j in w.jobs.queue] == ["session"]


def test_no_recheck_in_test_mode_or_without_pending_art(db):
    db.set_session("paheal", False, "sem sessão")
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
    assert publish_site(db, a, "paheal") == "failed"
    assert db.failures_in_a_row(a, "paheal") == 1 and db.sessions().get("paheal") is None


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
        forms.Paheal().publish(Post.load(EXAMPLE), dry_run=False)


def test_form_captcha_is_site_down(form_env):
    form_env["captcha"] = True
    with pytest.raises(SiteUnavailable, match="captcha"):
        forms.Paheal().publish(Post.load(EXAMPLE), dry_run=False)


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


# --- teste de sessão: motivo real na mensagem ------------------------------------------------------

def test_session_message_says_why():
    msg = forms.FormSite._session_message
    assert msg(True, False, True) == "sessão ativa"
    assert "captcha" in msg(False, True, False)
    assert "não registrou o login" in msg(False, False, False)
    assert "permissão" in msg(False, False, True)


def test_login_cookie_detection_accepts_prefix():
    class Ctx:
        def __init__(self, names):
            self.names = names

        def cookies(self):
            return [{"name": n} for n in self.names]

    class Pg:
        def __init__(self, names):
            self.context = Ctx(names)

    assert forms.Paheal()._logged_in(Pg(["shm_user", "shm_session", "_ga"]))
    assert not forms.Paheal()._logged_in(Pg(["shm_accepted_terms", "_ga"]))
    assert forms.Rule34xxx()._logged_in(Pg(["user_id", "pass_hash"]))
    assert not forms.Rule34xxx()._logged_in(Pg(["cf_clearance"]))


# --- conta recusada pelo site (ex.: conta sem reputação) ---------------------------------------

def test_account_refused_blocks_site_until_a_person_tests(db):
    from boorubot.sites.base import AccountRefused

    class Refused(Fake):
        def publish(self, post, dry_run=True):
            Fake.calls += 1
            raise AccountRefused("o site recusou o envio por causa da conta")

    worker_mod.build_site = lambda n, db=None, test=False: Refused(n)
    a = art(db, "a")
    assert publish_site(db, a, "paheal") == "site_down"
    assert db.failures_in_a_row(a, "paheal") == 0 and db.get_post(a)["status"] == "approved"
    assert db.setting("blocked_paheal") == "1"
    db.set_setting("recheck_paheal", (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds"))
    w = Worker(db)
    w.tick()
    assert w.jobs.empty()                                     # não re-testa sozinho
    client = TestClient(app_mod.create_app(db, w, start_worker=False))
    client.post("/sites/paheal/session")                    # a pessoa clicou em Testar
    assert db.setting("blocked_paheal") == "" and [j.kind for j in w.jobs.queue] == ["session"]


def test_refusal_text_is_recognized():
    class Pg:
        def __init__(self, text):
            self.text = text

        def inner_text(self, sel):
            return self.text

    gel = "Your account does not have enough reputation to add images to the site.\n\nPlease try again later"
    assert "enough reputation" in forms.FormSite._account_refused(Pg(gel))
    assert forms.FormSite._account_refused(Pg("Post added! view it here")) == ""
