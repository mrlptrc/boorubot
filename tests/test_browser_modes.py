"""Navegador discreto: qual modo cada site usa em cada situação, sem abrir navegador."""

from contextlib import contextmanager
from pathlib import Path

import pytest

from boorubot.models import Post
from boorubot.sites import forms
from boorubot.sites.browser import launch_options
from boorubot.sites.forms import FormSiteError, Paheal, Rule34xxx

EXAMPLE = Path(__file__).resolve().parents[1] / "queue" / "exemplo-001"


@pytest.mark.parametrize("site, purpose, expected", [
    (Paheal(), "send", "headless"),
    (Rule34xxx(), "send", "hidden"),        # Cloudflare: navegador real, janela fora da tela
    (Paheal(), "dry_run", "headless"),
    (Rule34xxx(), "dry_run", "hidden"),
    (Rule34xxx(), "session", "hidden"),
    (Paheal(), "session", "headless"),
    (Paheal(), "login", "visible"),       # login sempre na tela
    (Rule34xxx(), "login", "visible"),
])
def test_mode_per_site_and_purpose(site, purpose, expected):
    assert site.browser_mode(purpose) == expected


def test_override_from_settings():
    assert Paheal(send_mode="visible").browser_mode("send") == "visible"
    assert Paheal(send_mode="hidden").browser_mode("send") == "hidden"
    # rule34.xxx nunca vai para headless, mesmo se alguém configurar
    assert Rule34xxx(send_mode="headless").browser_mode("send") == "hidden"
    # valor inválido é ignorado
    assert Paheal(send_mode="qualquer").browser_mode("send") == "headless"
    # override vale só para o envio real
    assert Paheal(send_mode="visible").browser_mode("dry_run") == "headless"


def test_build_site_reads_override(tmp_path):
    from boorubot.config import build_site
    from boorubot.db import DB

    db = DB(tmp_path / "t.db")
    assert build_site("paheal", db).browser_mode("send") == "headless"
    db.set_setting("browser_mode_paheal", "visible")
    assert build_site("paheal", db).browser_mode("send") == "visible"


def test_launch_options():
    assert launch_options("headless")["headless"] is True
    hidden = launch_options("hidden")
    assert hidden["headless"] is False and "--window-position=-32000,-32000" in hidden["args"]
    assert launch_options("visible")["headless"] is False and "args" not in launch_options("visible")
    with pytest.raises(ValueError):
        launch_options("minimizado")


# --- captcha durante o envio ---------------------------------------------------

class FakeLocator:
    def count(self):
        return 0


class FakePage:
    def goto(self, url):
        pass

    def wait_for_timeout(self, ms):
        pass

    def locator(self, *a, **k):
        return FakeLocator()


@pytest.fixture
def fake_browser(monkeypatch, tmp_path):
    state = {"modes": [], "revealed": 0, "captcha_until_reveal": True}

    @contextmanager
    def fake_open(site, mode="headless"):
        state["modes"].append(mode)
        yield FakePage()

    def fake_reveal(page):
        state["revealed"] += 1
        state["captcha_until_reveal"] = False  # a pessoa resolve depois que a janela aparece

    monkeypatch.setattr(forms, "open_page", fake_open)
    monkeypatch.setattr(forms, "reveal_window", fake_reveal)
    monkeypatch.setattr(forms, "captcha_present", lambda page: state["captcha_until_reveal"])
    monkeypatch.setattr(forms, "screenshot", lambda page, out: out)
    monkeypatch.setattr(forms, "run_dir", lambda site: tmp_path)
    return state


def test_captcha_in_headless_send_fails_clearly(fake_browser):
    with pytest.raises(FormSiteError, match="o site pediu captcha: entre de novo em Sites → Entrar"):
        Paheal().publish(Post.load(EXAMPLE), dry_run=False)
    assert fake_browser["modes"] == ["headless"] and fake_browser["revealed"] == 0


def test_captcha_in_hidden_send_reveals_window_and_waits(fake_browser):
    site = Rule34xxx()
    site.captcha_timeout_s = 3
    # depois do captcha resolvido não há formulário (sem sessão no fake): o importante é o reveal
    with pytest.raises(FormSiteError, match="sem sessão"):
        site.publish(Post.load(EXAMPLE), dry_run=False)
    assert fake_browser["modes"] == ["hidden"] and fake_browser["revealed"] == 1


def test_captcha_never_solved_times_out(fake_browser, monkeypatch):
    monkeypatch.setattr(forms, "reveal_window", lambda page: None)  # ninguém resolve
    site = Rule34xxx()
    site.captcha_timeout_s = 2
    with pytest.raises(FormSiteError, match="não resolvido a tempo"):
        site.publish(Post.load(EXAMPLE), dry_run=False)


def test_captcha_in_dry_run_never_reveals(fake_browser):
    with pytest.raises(FormSiteError, match="pediu captcha"):
        Rule34xxx().publish(Post.load(EXAMPLE), dry_run=True)
    assert fake_browser["revealed"] == 0


def test_headless_uses_the_full_chromium():
    """O headless-shell não lê os cookies que o Chromium completo (janela do login) grava."""
    assert launch_options("headless").get("channel") == "chromium"


def test_login_cookies_visible_to_headless_sends(tmp_path, monkeypatch):
    """Regressão: logar no site pela janela e o teste de sessão (invisível) não ver o login."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from boorubot.sites import browser

    class SetsCookie(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Set-Cookie", "user_id=42; Max-Age=31536000; Path=/")
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<p>logado</p>")

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), SetsCookie)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(browser, "PROFILES_DIR", tmp_path)
    url = f"http://127.0.0.1:{server.server_port}/"
    try:
        with browser.open_page("site", "hidden") as page:        # mesmo navegador da janela de login
            page.goto(url)
        with browser.open_page("site", "headless") as page:      # como o teste de sessão e o envio
            names = [c["name"] for c in page.context.cookies(url)]
    finally:
        server.shutdown()
    assert names == ["user_id"]


# --- depois do clique em enviar: sucesso só com certeza --------------------------------

@pytest.fixture
def submit_page(monkeypatch, tmp_path):
    """Página falsa já no formulário (sem captcha); a URL depois do envio é controlada."""
    state = {"url": "", "raise": None}

    class Page(FakePage):
        @property
        def url(self):
            return state["url"]

        def wait_for_load_state(self, *a):
            pass

    @contextmanager
    def fake_open(site, mode="headless"):
        yield Page()

    def fake_submit(self, page):
        if state["raise"]:
            raise state["raise"]

    monkeypatch.setattr(forms, "open_page", fake_open)
    monkeypatch.setattr(forms, "captcha_present", lambda page: False)
    monkeypatch.setattr(forms, "screenshot", lambda page, out: out)
    monkeypatch.setattr(forms, "run_dir", lambda site: tmp_path)
    monkeypatch.setattr(forms.FormSite, "_has_upload_form", lambda self, page: True)
    monkeypatch.setattr(forms.FormSite, "fill", lambda self, page, post: None)
    monkeypatch.setattr(forms.FormSite, "_submit", fake_submit)
    return state


def test_success_page_recognized(submit_page):
    submit_page["url"] = "https://rule34.paheal.net/post/view/123"
    r = Paheal().publish(Post.load(EXAMPLE), dry_run=False)
    assert r.ok and not r.info.get("uncertain") and r.url.endswith("/view/123")


def test_unknown_page_after_submit_is_uncertain(submit_page):
    submit_page["url"] = "https://rule34.paheal.net/upload"
    r = Paheal().publish(Post.load(EXAMPLE), dry_run=False)
    assert not r.ok and r.info["uncertain"] and "confira no site" in r.message


def test_error_after_submit_is_uncertain(submit_page):
    submit_page["raise"] = TimeoutError("demorou")
    r = Paheal().publish(Post.load(EXAMPLE), dry_run=False)
    assert not r.ok and r.info["uncertain"]
