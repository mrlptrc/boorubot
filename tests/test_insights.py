"""Página de Métricas: agregações, período e renderização (só produção)."""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from boorubot import insights
from boorubot.db import DB
from boorubot.web import app as app_mod
from boorubot.worker import Worker

NOW = datetime(2026, 10, 14, 20, 0)


@pytest.fixture
def db(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    monkeypatch.setattr(app_mod, "IMAGES_DIR", images)
    return DB(tmp_path / "i.db")


def publish(db, title, site, when, status="ok"):
    """Publicação real com created_at controlado (o banco usa now() por padrão)."""
    post_id = db.create_post(f"{title}.png", title=title)
    db.add_publication(post_id, site, status, f"https://danbooru.donmai.us/posts/{post_id}" if status == "ok" else "")
    pub_id = db.publications(post_id)[0]["id"]
    db._exec("UPDATE publications SET created_at = ? WHERE id = ?", (when.isoformat(timespec="seconds"), pub_id))
    return post_id, pub_id


def snap(db, pub_id, when, score, favs):
    db.add_metric_snapshot(pub_id, when.isoformat(timespec="seconds"), score, score, 0, favs)


@pytest.fixture
def seeded(db):
    _, a = publish(db, "sereia", "danbooru", NOW - timedelta(days=20))
    snap(db, a, NOW - timedelta(days=19), 8, 5)        # ~24 h depois: score 8
    snap(db, a, NOW - timedelta(days=10), 20, 15)
    snap(db, a, NOW - timedelta(days=1), 31, 28)
    _, b = publish(db, "maid", "danbooru", NOW - timedelta(days=3))
    snap(db, b, NOW - timedelta(days=2, hours=1), 4, 2)  # ~23 h depois: score 4
    snap(db, b, NOW - timedelta(hours=2), 9, 7)
    publish(db, "fada", "gelbooru", NOW - timedelta(days=2))
    publish(db, "falha", "paheal", NOW - timedelta(days=1), status="failed")
    return db


def test_thirty_days(seeded):
    m = insights.build(seeded, "30", NOW)
    # sereia: 5 -> 28 (+23) ; maid: 2 -> 7 (+5)
    assert m["favorites"] == 28
    assert m["first_day_score"] == pytest.approx(6.0) and m["first_day_count"] == 2
    assert m["posts"] == 3 and m["failed"] == 1
    assert [t["title"] for t in m["top"]] == ["sereia", "maid"]
    assert m["top"][0]["favs"] == 28 and m["top"][0]["score"] == 31


def test_seven_days_window(seeded):
    m = insights.build(seeded, "7", NOW)
    assert m["start"] == date(2026, 10, 8)
    # sereia na janela só tem o snapshot de ontem (base = ele mesmo, +0); maid +5
    assert m["favorites"] == 5
    assert m["posts"] == 2                                # sereia foi publicada antes da janela
    assert m["first_day_count"] == 1                      # só a maid foi publicada na janela


def test_all_time_starts_at_first_activity(seeded):
    m = insights.build(seeded, "tudo", NOW)
    assert m["start"] == (NOW - timedelta(days=20)).date()
    assert m["favorites"] == 28


def test_invalid_period_falls_back_to_30(seeded):
    assert insights.build(seeded, "999", NOW)["period"] == "30"


def test_weekly_buckets_start_on_monday():
    series = [(date(2026, 10, 11), 3), (date(2026, 10, 12), 4), (date(2026, 10, 14), 5)]  # dom, seg, qua
    weeks = insights.weekly(series)
    assert [(w["label"], w["gain"]) for w in weeks] == [("5/10", 3), ("12/10", 9)]
    assert weeks[-1]["pct"] == 100


def test_first_day_score_ignores_late_snapshots():
    pub = {"created_at": "2026-10-01T18:00:00"}
    late = [{"captured_at": "2026-10-05T18:00:00", "score": 40}]
    assert insights.first_day_score(pub, late) is None
    close = late + [{"captured_at": "2026-10-02T17:00:00", "score": 7}, {"captured_at": "2026-10-03T12:00:00", "score": 9}]
    assert insights.first_day_score(pub, close) == 7


def test_by_site_marks_sites_without_metrics(seeded):
    rows = {r["site"]: r for r in insights.build(seeded, "30", NOW)["by_site"]}
    assert rows["danbooru"]["has_metrics"] and rows["danbooru"]["favs"] == 35
    assert rows["gelbooru"]["posts"] == 1 and not rows["gelbooru"]["has_metrics"]
    assert rows["paheal"]["posts"] == 0 and not rows["paheal"]["has_metrics"]


def test_never_reads_test_runs(seeded):
    post_id = seeded.create_post("t.png", title="so-teste")
    seeded.add_test_run(post_id, "danbooru", "test_upload", "https://testbooru.donmai.us/posts/9")
    m = insights.build(seeded, "tudo", NOW)
    assert m["posts"] == 3 and "so-teste" not in [t["title"] for t in m["top"]]


# --- página ---------------------------------------------------------------------------

def client(db):
    w = Worker(db)
    return TestClient(app_mod.create_app(db, w, start_worker=False)), w


def test_page_renders_without_data(db):
    c, _ = client(db)
    page = c.get("/metricas").text
    assert "Métricas" in page and "As métricas aparecem depois do primeiro post no Danbooru" in page
    assert page.count("sem métrica por enquanto") == 5


def test_page_with_data_and_period_links(seeded):
    c, _ = client(seeded)
    page = c.get("/metricas?periodo=tudo").text
    assert 'href="/metricas?periodo=7"' in page and 'aria-current="page"' in page
    assert "Favoritos por semana" in page and 'role="img"' in page and "semana de" in page
    assert "sereia" in page and "sem métrica por enquanto" in page
    assert "6,0" in page                                   # score médio nas 24 h, com vírgula


def test_refresh_button_lives_on_metrics_page(seeded):
    c, w = client(seeded)
    assert 'action="/metricas/atualizar"' in c.get("/metricas").text
    assert "/metricas/atualizar" not in c.get("/historico").text
    c.post("/metricas/atualizar")
    assert [j.kind for j in w.jobs.queue] == ["metrics"]
