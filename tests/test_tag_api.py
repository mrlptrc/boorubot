import httpx
from fastapi.testclient import TestClient

from boorubot.db import DB
from boorubot.tag_api import DanbooruTagAPI
from boorubot.web.app import create_app
from boorubot.worker import Worker


def client(tmp_path, handler, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    monkeypatch.setattr("boorubot.web.app.IMAGES_DIR", images)
    monkeypatch.setattr("boorubot.db.IMAGES_DIR", images)
    db = DB(tmp_path / "tags.db")
    api = DanbooruTagAPI(transport=httpx.MockTransport(handler))
    return TestClient(create_app(db, Worker(db), start_worker=False, tag_api=api))


def test_autocomplete_endpoint_returns_danbooru_suggestions(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[{"value": "blue_hair", "category": 0}, {"value": "blue_eyes"}])

    response = client(tmp_path, handler, monkeypatch).get("/danbooru/autocomplete.json?q=blue")
    assert response.json() == {"available": True, "suggestions": [
        {"value": "blue_hair", "category": 0}, {"value": "blue_eyes"},
    ]}
    assert requests[0].url.path == "/autocomplete.json"
    assert requests[0].url.params["search[type]"] == "tag_query"
    assert requests[0].url.params["search[query]"] == "blue"
    assert requests[0].headers["user-agent"].startswith("boorubot/")


def test_tag_check_marks_unknown_tags_and_caches_results(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[{"name": "blue_hair"}])

    c = client(tmp_path, handler, monkeypatch)
    for _ in range(2):
        response = c.post("/danbooru/tags/check", json={"tags": ["blue_hair", "not_a_tag"]})
        assert response.json() == {"available": True, "unknown": ["not_a_tag"]}
    assert len(requests) == 1
    assert set(requests[0].url.params.get_list("search[name_array][]")) == {"blue_hair", "not_a_tag"}
    assert requests[0].url.params["search[hide_empty]"] == "false"


def test_danbooru_outage_does_not_break_tag_check(tmp_path, monkeypatch):
    c = client(tmp_path, lambda request: httpx.Response(503), monkeypatch)
    response = c.post("/danbooru/tags/check", json={"tags": ["blue_hair"]})
    assert response.json() == {"available": False, "unknown": []}
    suggestions = c.get("/danbooru/autocomplete.json?q=blue").json()
    assert suggestions == {"available": False, "suggestions": []}
