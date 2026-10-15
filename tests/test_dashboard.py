"""Painel do dia: folga da fila, favoritos na semana, alertas e renderização."""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from boorubot import dashboard
from boorubot.dashboard import countdown, covered_days, favorites_by_day, greeting, queue_runway
from boorubot.db import DB
from boorubot.web import app as app_mod
from boorubot.worker import Worker

TODAY = date(2026, 10, 14)


def at(day, hh=12, mm=0):
    return datetime(2026, 10, day, hh, mm)


@pytest.fixture
def db(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    monkeypatch.setattr(app_mod, "IMAGES_DIR", images)
    monkeypatch.setattr("boorubot.db.IMAGES_DIR", images)
    d = DB(tmp_path / "d.db")
    d.set_setting("daily_time", "18:00")
    return d


def approve_n(db, n):
    ids = []
    for i in range(n):
        pid = db.create_post(f"{i}.png", title=f"arte {i}", tags="x", sites=["danbooru", "gelbooru"])
        db.approve(pid)
        ids.append(pid)
    return ids


# --- folga da fila -----------------------------------------------------------------

def test_runway_counts_consecutive_days_from_today():
    days = {TODAY, TODAY + timedelta(1), TODAY + timedelta(2), TODAY + timedelta(4)}
    assert queue_runway(days, TODAY) == 3                    # o buraco no 4º dia corta a sequência
    assert queue_runway(set(), TODAY) == 0
    assert queue_runway({TODAY + timedelta(1)}, TODAY) == 0  # hoje sem post: folga zero


def test_covered_days_any_site():
    from boorubot.scheduler import Cell
    plans = {"danbooru": {TODAY: Cell("published"), TODAY + timedelta(1): Cell("empty")},
             "gelbooru": {TODAY: Cell("empty"), TODAY + timedelta(1): Cell("post"), TODAY + timedelta(2): Cell("rest")}}
    assert covered_days(plans) == {TODAY, TODAY + timedelta(1)}
    assert queue_runway(covered_days(plans), TODAY) == 2


def test_runway_from_real_queue(db):
    approve_n(db, 3)
    built = dashboard.build(db, at(14, 12))
    assert built["runway"] == 3 and built["runway_low"] is False
    assert built["paws"] == [True, True, True, False, False, False, False]
    assert built["missing_for_week"] == 4


def test_low_runway_flag(db):
    approve_n(db, 2)
    assert dashboard.build(db, at(14, 12))["runway_low"] is True


# --- favoritos na semana -------------------------------------------------------------

def snap(pub, when, favs):
    return {"publication_id": pub, "captured_at": when.isoformat(), "fav_count": favs}


def test_week_gain_is_latest_minus_oldest_per_publication():
    snaps = [
        snap(1, at(9, 10), 10), snap(1, at(11, 10), 14), snap(1, at(14, 10), 20),   # +10
        snap(2, at(12, 10), 3), snap(2, at(12, 22), 5), snap(2, at(13, 10), 6),      # +3
    ]
    series = favorites_by_day(snaps, TODAY)
    assert [d for d, _ in series] == [TODAY - timedelta(6 - i) for i in range(7)]
    by_day = dict(series)
    assert sum(by_day.values()) == 13
    assert by_day[date(2026, 10, 11)] == 4 and by_day[date(2026, 10, 14)] == 6
    assert by_day[date(2026, 10, 12)] == 2 and by_day[date(2026, 10, 13)] == 1


def test_single_snapshot_means_no_gain_yet():
    assert sum(g for _, g in favorites_by_day([snap(1, at(14, 10), 50)], TODAY)) == 0


def published_pub(db, when):
    post_id = db.create_post("a.png")
    db.add_publication(post_id, "danbooru", "ok", "https://danbooru.donmai.us/posts/1")
    pub = db.publications(post_id)[0]["id"]
    db._exec("UPDATE publications SET created_at = ? WHERE id = ?", (when.isoformat(timespec="seconds"), pub))
    return pub


def test_week_favorites_old_post_counts_from_oldest_snapshot(db):
    pub = published_pub(db, at(1, 18))                              # antes da janela
    db.add_metric_snapshot(pub, at(1, 10).isoformat(), 5, 5, 0, 2)    # fora da janela
    db.add_metric_snapshot(pub, at(10, 10).isoformat(), 6, 6, 0, 4)
    db.add_metric_snapshot(pub, at(14, 10).isoformat(), 9, 9, 0, 11)
    fav = dashboard.week_favorites(db, at(14, 12))
    assert fav["has_data"] and fav["total"] == 7
    assert len(fav["bars"]) == 7 and fav["bars"][-1]["pct"] == 100


def test_week_favorites_new_post_counts_from_zero(db):
    """Post às 18h, 1ª coleta só no dia seguinte já com 12: esses 12 contam."""
    pub = published_pub(db, at(12, 18))
    db.add_metric_snapshot(pub, at(13, 10).isoformat(), 14, 14, 0, 12)
    db.add_metric_snapshot(pub, at(14, 10).isoformat(), 20, 20, 0, 15)
    fav = dashboard.week_favorites(db, at(14, 12))
    assert fav["total"] == 15
    by_day = {b["day"]: b["gain"] for b in fav["bars"]}
    assert by_day[date(2026, 10, 13)] == 12 and by_day[date(2026, 10, 14)] == 3


def test_new_post_baseline_is_zero_in_pure_function():
    snaps = [snap(1, at(13, 10), 12)]
    assert sum(g for _, g in favorites_by_day(snaps, TODAY, published_at={1: at(12, 18)})) == 12
    assert sum(g for _, g in favorites_by_day(snaps, TODAY, published_at={1: at(1, 18)})) == 0


def test_no_metrics_yet(db):
    fav = dashboard.week_favorites(db, at(14, 12))
    assert fav["has_data"] is False and fav["total"] == 0


# --- alertas -------------------------------------------------------------------------

def test_session_down_is_the_first_alert(db):
    ids = approve_n(db, 1)
    db.set_session("gelbooru", True, "ok")
    db.set_session("rule34xxx", False, "sem sessão")
    db.set_setting("backup_dir", r"C:\bk")
    db.set_setting("last_backup_ok", "0")
    found = dashboard.alerts(db, ["danbooru", "gelbooru", "rule34xxx"], db.get_post(ids[0]))
    assert [a.kind for a in found] == ["session", "backup", "test_mode"]
    assert found[0].site == "rule34xxx" and found[0].label == "rule34.xxx"
    assert found[0].others == 2                     # o próximo post ainda sai no Danbooru e no Gelbooru


def test_session_of_unused_site_is_not_an_alert(db):
    db.set_session("paheal", False, "sem sessão")
    db.set_setting("test_mode", "0")
    assert dashboard.alerts(db, ["danbooru"], None) == []


def test_test_mode_alert_only_when_nothing_worse(db):
    assert [a.kind for a in dashboard.alerts(db, [], None)] == ["test_mode"]


# --- textos ----------------------------------------------------------------------------

@pytest.mark.parametrize("hour, text", [(6, "Bom dia"), (11, "Bom dia"), (12, "Boa tarde"), (17, "Boa tarde"),
                                        (18, "Boa noite"), (2, "Boa noite")])
def test_greeting(hour, text):
    assert greeting(datetime(2026, 10, 14, hour)) == text


@pytest.mark.parametrize("minutes, text", [(-5, "agora"), (0, "agora"), (40, "em 40 min"), (60, "em 1 h"),
                                           (160, "em 2 h 40 min"), (60 * 24, "em 1 dia"), (60 * 72, "em 3 dias")])
def test_countdown(minutes, text):
    assert countdown(timedelta(minutes=minutes)) == text


# --- tela ---------------------------------------------------------------------------------

def client(db):
    return TestClient(app_mod.create_app(db, Worker(db), start_worker=False))


def test_home_empty_state(db):
    page = client(db).get("/").text
    assert "Nada agendado para hoje." in page and "Fila vazia" in page
    assert "As métricas aparecem depois do primeiro post no Danbooru." in page
    assert page.count("vazio</span>") == 7


def test_home_with_queue_and_session_alert(db):
    approve_n(db, 2)
    db.set_session("gelbooru", False, "sem sessão")
    draft = db.create_post("d.png", title="rascunho-x", tags="a b c")
    failed = db.create_post("f.png", title="falhou-x", status="failed")
    db.add_publication(failed, "paheal", "failed", message="caiu")
    page = client(db).get("/").text
    assert "Próximos 7 dias" in page and "1º · prev." in page and "2º · prev." in page
    assert "pediu login de novo" in page and "/sites#gelbooru" in page
    assert "rascunho-x" in page and "3 tags" in page
    assert "falhou no rule34.paheal" in page and "falhou-x" in page


def test_titles_are_escaped(db):
    pid = db.create_post("x.png", title="<script>alert(1)</script>", tags="x", sites=["danbooru"])
    db.approve(pid)
    page = client(db).get("/").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
