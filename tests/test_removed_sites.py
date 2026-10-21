"""Gelbooru e rule34.us saíram do projeto: a migração limpa as artes e o histórico só desses dois."""

import json

from boorubot.config import ALL_SITES, SITE_LABELS
from boorubot.db import DB
from boorubot.sites.forms import FORM_SITES


def test_only_three_sites_left():
    assert ALL_SITES == ("danbooru", "paheal", "rule34xxx") and set(SITE_LABELS) == set(ALL_SITES)
    assert set(FORM_SITES) == {"paheal", "rule34xxx"}


def test_old_database_is_cleaned_and_the_rest_is_kept(tmp_path):
    path = tmp_path / "old.db"
    db = DB(path)
    pid = db.create_post("a.png", title="zero two", tags="x", sites=["danbooru", "paheal", "rule34xxx"])
    # banco de antes da mudança: os dois sites ainda aparecem em tudo
    with db.conn:
        db.conn.execute("UPDATE posts SET sites = ? WHERE id = ?",
                        (json.dumps(["danbooru", "gelbooru", "paheal", "rule34us", "rule34xxx"]), pid))
        db.conn.execute("UPDATE settings SET value = ? WHERE key = 'default_sites'",
                        (json.dumps(["danbooru", "gelbooru", "paheal", "rule34us", "rule34xxx"]),))
    for site, status in (("danbooru", "ok"), ("gelbooru", "site_down"), ("paheal", "ok"), ("rule34us", "refused")):
        db.add_publication(pid, site, status, message=site)
    db.add_test_run(pid, "gelbooru", "failed", message="x")
    db.set_session("rule34us", True, "sessão ativa")
    db.set_session("paheal", True, "sessão ativa")
    for key in ("rhythm_gelbooru", "blocked_gelbooru", "backoff_rule34us", "rhythm_paheal"):
        db.set_setting(key, "1")

    for _ in range(2):  # idempotente: abrir de novo não muda nada
        db = DB(path)
        assert db.get_post(pid)["sites"] == ["danbooru", "paheal", "rule34xxx"]
        assert json.loads(db.setting("default_sites")) == ["danbooru", "paheal", "rule34xxx"]
        assert [(p["site"], p["status"]) for p in reversed(db.publications(pid))] == [("danbooru", "ok"), ("paheal", "ok")]
        assert db.test_runs(pid) == [] and set(db.sessions()) == {"paheal"}
        assert db.setting("rhythm_paheal") == "1"
        assert all(db.conn.execute("SELECT 1 FROM settings WHERE key = ?", (k,)).fetchone() is None
                   for k in ("rhythm_gelbooru", "blocked_gelbooru", "backoff_rule34us"))
