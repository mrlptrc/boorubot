"""paheal só aceita série, personagem e artista: lista própria por arte, recusa sem reenvio automático."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from boorubot import paheal_tags, worker as worker_mod
from boorubot.db import DB, to_post
from boorubot.models import Post
from boorubot.sites import forms
from boorubot.sites.base import ContentRefused, Result
from boorubot.sites.forms import Paheal, Rule34us
from boorubot.tag_api import DanbooruTagAPI
from boorubot.web import app as app_mod
from boorubot.worker import Worker

EXAMPLE = Path(__file__).resolve().parents[1] / "queue" / "exemplo-001"
# a arte que o paheal recusou de verdade ("Invalid tag used: anal")
REAL_TAGS = ("artista_exemplo zero_two_(darling_in_the_franxx) 1girl breasts penis hetero sex cum anal "
             "green_eyes red_horns doggystyle artist_name").split()
CATEGORIES = {"artista_exemplo": 1, "zero_two_(darling_in_the_franxx)": 4, "darling_in_the_franxx": 3}


def test_only_artist_character_and_series_go_to_paheal():
    assert paheal_tags.build(REAL_TAGS, {**CATEGORIES, "anal": 0, "1girl": 0}) == \
        ["artista_exemplo", "zero_two", "darling_in_the_franxx"]


def test_without_danbooru_the_parenthesis_and_default_tags_still_work():
    # sem rede: parêntese = personagem (e a série vem dele); tag padrão dela = artista
    assert paheal_tags.build(REAL_TAGS, {}, artists=["artista_exemplo"]) == ["artista_exemplo", "zero_two", "darling_in_the_franxx"]


def test_allowed_content_tags_and_tagme():
    assert paheal_tags.build(["artista_exemplo", "animated", "ai-generated", "solo"], {"artista_exemplo": 1}) == \
        ["artista_exemplo", "animated", "AI-generated", "tagme"]
    assert paheal_tags.build(["someone_(artist)"], {"someone_(artist)": 1}) == ["someone", "tagme"]


@pytest.fixture
def db(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    monkeypatch.setattr(app_mod, "IMAGES_DIR", images)
    monkeypatch.setattr("boorubot.db.IMAGES_DIR", images)
    (images / "a.png").write_bytes(b"x")
    d = DB(tmp_path / "test.db")
    d.set_setting("default_tags", "artista_exemplo")
    return d


def tags_json(request):
    names = [v for k, v in request.url.params.multi_items() if k == "search[name_array][]"]
    return httpx.Response(200, json=[{"name": n, "category": CATEGORIES[n]} for n in names if n in CATEGORIES])


@pytest.fixture
def client(db):
    api = DanbooruTagAPI(transport=httpx.MockTransport(tags_json))
    return TestClient(app_mod.create_app(db, Worker(db), start_worker=False, tag_api=api))


def test_editor_fills_paheal_tags_and_she_can_write_her_own(client, db):
    pid = db.create_post("a.png", tags=" ".join(REAL_TAGS), sites=["danbooru", "paheal"])
    page = client.get(f"/posts/{pid}").text
    assert 'name="paheal_tags"' in page and "artista_exemplo zero_two darling_in_the_franxx" in page
    assert db.get_post(pid)["paheal_tags"] == "artista_exemplo zero_two darling_in_the_franxx"

    r = client.patch(f"/posts/{pid}", json={"paheal_tags": "artista_exemplo Zero_Two Darling_in_the_FranXX"}).json()
    assert r["post"]["paheal_tags_manual"] and r["post"]["paheal_tags"] == "artista_exemplo Zero_Two Darling_in_the_FranXX"
    # tags da arte mudam: a lista dela continua
    client.patch(f"/posts/{pid}", json={"tags": "artista_exemplo 1girl"})
    assert db.get_post(pid)["paheal_tags"] == "artista_exemplo Zero_Two Darling_in_the_FranXX"
    # "refazer pela Mochi"
    r = client.patch(f"/posts/{pid}", json={"paheal_auto": True}).json()
    assert not r["post"]["paheal_tags_manual"] and r["post"]["paheal_tags"] == "artista_exemplo tagme"


def test_paheal_receives_only_its_list():
    post = Post.load(EXAMPLE)
    post.only_tags = {"paheal": ["artista_exemplo", "zero_two"]}
    assert post.tags_for("paheal") == ["artista_exemplo", "zero_two"] and post.tags_for("gelbooru") == post.tags


def test_old_art_without_paheal_tags_never_sends_the_full_list(db, monkeypatch):
    db.set_setting("test_mode", "0")
    pid = db.create_post("a.png", tags=" ".join(REAL_TAGS), sites=["paheal"], rating="explicit")
    sent = {}

    class FakePaheal:
        def publish(self, post, dry_run):
            sent["tags"] = post.tags_for("paheal")
            return Result("paheal", ok=True, dry_run=False, url="https://rule34.paheal.net/post/view/1")

    monkeypatch.setattr(worker_mod, "build_site", lambda site, db, **kw: FakePaheal())
    worker_mod.publish_post(db, pid)
    assert sent["tags"] == ["artista_exemplo", "zero_two", "darling_in_the_franxx"]


def test_refused_art_waits_for_her_fix_then_goes_back_to_the_queue(client, db, monkeypatch):
    db.set_setting("test_mode", "0")
    pid = db.create_post("a.png", tags=" ".join(REAL_TAGS), sites=["paheal"], rating="explicit")
    db.approve(pid)

    class Refusing:
        def publish(self, post, dry_run):
            raise ContentRefused('o paheal recusou a tag "anal"')

    monkeypatch.setattr(worker_mod, "build_site", lambda site, db, **kw: Refusing())
    assert worker_mod.publish_post(db, pid) == "partial"
    assert db.pending_sites(db.get_post(pid)) == []          # não reenvia sozinho
    page = client.get(f"/posts/{pid}").text
    assert "O site recusou esta arte" in page and 'recusou a tag "anal"' in page.replace("&#34;", '"')

    client.patch(f"/posts/{pid}", json={"paheal_tags": "artista_exemplo zero_two"})
    row = db.get_post(pid)
    assert row["status"] == "approved" and db.pending_sites(row) == ["paheal"]


@pytest.fixture(scope="module")
def browser_page():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b.new_page()
        b.close()


def serve(page, url, html):
    page.route(url, lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    page.goto(url)


def test_paheal_invalid_tag_page_is_a_refusal(browser_page):
    serve(browser_page, "https://rule34.paheal.net/upload",
          "<div>Invalid tag used: anal, please see /wiki/content%20tag%20attempted</div>")
    msg = Paheal()._content_refused(browser_page)
    assert '"anal"' in msg and "série, personagem e artista" in msg


def test_rule34us_bouncing_to_an_empty_form_is_a_refusal(browser_page):
    serve(browser_page, "https://rule34.us/index.php?r=posts/create",
          '<form><input type="file" name="file"><button>Upload File</button></form>')
    assert "voltou para o formulário vazio" in Rule34us()._content_refused(browser_page)
    serve(browser_page, "https://rule34.us/index.php?r=posts/view&id=5", "<p>post</p>")
    assert Rule34us()._content_refused(browser_page) == ""


def test_publish_raises_content_refused_after_submit(monkeypatch, tmp_path):
    from contextlib import contextmanager

    class Page:
        url = "https://rule34.paheal.net/upload"

        def goto(self, *a, **k):
            pass

        def wait_for_load_state(self, *a):
            pass

        def wait_for_timeout(self, *a):
            pass

    @contextmanager
    def fake_open(site, mode="headless"):
        yield Page()

    monkeypatch.setattr(forms, "open_page", fake_open)
    monkeypatch.setattr(forms, "captcha_present", lambda page: False)
    monkeypatch.setattr(forms, "screenshot", lambda page, out: out)
    monkeypatch.setattr(forms, "run_dir", lambda site: tmp_path)
    monkeypatch.setattr(forms.FormSite, "_has_upload_form", lambda self, page: True)
    monkeypatch.setattr(forms.FormSite, "fill", lambda self, page, post: None)
    monkeypatch.setattr(forms.FormSite, "_submit", lambda self, page: None)
    monkeypatch.setattr(forms.FormSite, "_account_refused", staticmethod(lambda page: ""))
    monkeypatch.setattr(Paheal, "_content_refused", lambda self, page: 'o paheal recusou a tag "anal"')
    post = Post.load(EXAMPLE)
    post.original_character = False
    with pytest.raises(ContentRefused, match="anal"):
        Paheal().publish(post, dry_run=False)
