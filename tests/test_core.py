import json
import shutil
from pathlib import Path

import httpx
import pytest

from boorubot.models import Post, PostError
from boorubot.sites.base import SkipPost
from boorubot.sites.danbooru import Danbooru
from boorubot.sites.forms import Paheal

EXAMPLE = Path(__file__).resolve().parents[1] / "queue" / "exemplo-001"


@pytest.fixture
def post_dir(tmp_path):
    folder = tmp_path / "p1"
    shutil.copytree(EXAMPLE, folder)
    return folder


def write_meta(folder: Path, **changes):
    meta = json.loads((folder / "post.json").read_text(encoding="utf-8"))
    meta.update(changes)
    (folder / "post.json").write_text(json.dumps(meta), encoding="utf-8")


def test_load_example():
    post = Post.load(EXAMPLE)
    assert post.image.name == "arte.png"
    assert post.rating == "general"
    assert post.tags_for("danbooru")[-1] == "commentary"
    assert "commentary" not in post.tags_for("gelbooru")


def test_tags_are_normalized(post_dir):
    write_meta(post_dir, tags=["Long Hair ", "1girl", " "])
    assert Post.load(post_dir).tags == ["long_hair", "1girl"]


@pytest.mark.parametrize("changes", [{"rating": "nsfw"}, {"tags": []}])
def test_invalid_meta(post_dir, changes):
    write_meta(post_dir, **changes)
    with pytest.raises(PostError):
        Post.load(post_dir)


def test_paheal_skips_original_characters(post_dir):
    write_meta(post_dir, original_character=True)
    with pytest.raises(SkipPost):
        Paheal().check_rules(Post.load(post_dir))


def test_danbooru_adds_original_tag_and_rating_code(post_dir):
    write_meta(post_dir, original_character=True, rating="explicit", source="https://x.com/a/status/1")
    payload = Danbooru("https://testbooru.donmai.us").post_payload(Post.load(post_dir), 7)
    assert payload["post[rating]"] == "e"
    assert payload["post[tag_string]"].split()[-1] == "original"
    assert payload["post[source]"] == "https://x.com/a/status/1"
    assert payload["upload_media_asset_id"] == "7"


def test_danbooru_full_flow():
    """upload -> poll até completed -> cria post, com a API simulada."""
    calls = []
    polls = iter(["processing", "completed"])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.headers["user-agent"].startswith("boorubot/")
        assert request.headers["authorization"].startswith("Basic ")
        if request.method == "POST" and request.url.path == "/uploads.json":
            assert b'name="upload[files][0]"; filename="arte.png"' in request.content
            return httpx.Response(201, json={"id": 10, "status": "pending"})
        if request.method == "GET" and request.url.path == "/uploads/10.json":
            status = next(polls)
            body = {"id": 10, "status": status}
            if status == "completed":
                body["upload_media_assets"] = [{"id": 55}]
            return httpx.Response(200, json=body)
        if request.method == "POST" and request.url.path == "/posts.json":
            form = dict(httpx.QueryParams(request.content.decode()))
            assert form["upload_media_asset_id"] == "55"
            assert form["post[rating]"] == "g"
            assert form["post[artist_commentary][original_title]"] == "Teste"
            return httpx.Response(201, json={"id": 999})
        return httpx.Response(404)

    site = Danbooru("https://testbooru.donmai.us", "user", "key",
                    transport=httpx.MockTransport(handler), poll_interval=0)
    result = site.publish(Post.load(EXAMPLE), dry_run=False)

    assert result.ok and result.url == "https://testbooru.donmai.us/posts/999"
    assert calls == [("POST", "/uploads.json"), ("GET", "/uploads/10.json"),
                     ("GET", "/uploads/10.json"), ("POST", "/posts.json")]


def test_danbooru_upload_error_is_reported():
    def handler(request):
        if request.url.path == "/uploads.json":
            return httpx.Response(201, json={"id": 1, "status": "error", "error": "duplicate"})
        return httpx.Response(404)

    site = Danbooru("https://t", "u", "k", transport=httpx.MockTransport(handler), poll_interval=0)
    with pytest.raises(RuntimeError, match="duplicate"):
        site.publish(Post.load(EXAMPLE), dry_run=False)


def test_danbooru_dry_run_sends_nothing():
    def handler(request):
        raise AssertionError("dry-run não pode chamar a API")

    site = Danbooru("https://t", transport=httpx.MockTransport(handler))
    result = site.publish(Post.load(EXAMPLE), dry_run=True)
    assert result.dry_run and "post[tag_string]" in result.message
