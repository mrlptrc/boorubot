"""Backup diário: banco consistente + imagens incrementais, nunca os perfis de navegador."""

import sqlite3
import sys
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from boorubot.backup import BackupError, backup_now, run_backup, validate_dest
from boorubot.db import DB
from boorubot.web import app as app_mod
from boorubot.worker import Worker


@pytest.fixture
def env(tmp_path, monkeypatch):
    images = tmp_path / "data" / "images"
    images.mkdir(parents=True)
    monkeypatch.setattr(app_mod, "IMAGES_DIR", images)
    monkeypatch.setattr("boorubot.backup.IMAGES_DIR", images)
    (tmp_path / ".profiles" / "paheal").mkdir(parents=True)
    (tmp_path / ".profiles" / "paheal" / "Cookies").write_text("segredo")
    db = DB(tmp_path / "data" / "boorubot.db")
    return db, images, tmp_path


def test_backup_copies_consistent_db_and_images(env):
    db, images, tmp = env
    db.create_post("a.png", title="arte 1")
    db.create_post("b.png", title="arte 2")
    (images / "a.png").write_bytes(b"A" * 10)
    (images / "b.png").write_bytes(b"B" * 20)
    dest = tmp / "OneDrive" / "backup"

    result = run_backup(db, dest)
    assert (result.images_copied, result.images_total) == (2, 2)
    copy = sqlite3.connect(dest / "boorubot.db")
    assert copy.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 2
    copy.close()
    # só banco e imagens: nada de .profiles (cookies), nada de temporário
    assert sorted(p.name for p in dest.iterdir()) == ["boorubot.db", "images"]


def test_images_are_incremental(env):
    db, images, tmp = env
    (images / "a.png").write_bytes(b"A")
    dest = tmp / "bk"
    assert run_backup(db, dest).images_copied == 1
    assert run_backup(db, dest).images_copied == 0          # nada novo
    (images / "b.png").write_bytes(b"B")
    (images / "a.png").write_bytes(b"AA")                    # alterada
    assert run_backup(db, dest).images_copied == 2
    (images / "b.png").unlink()
    run_backup(db, dest)
    assert (dest / "images" / "b.png").exists()              # backup nunca apaga


def test_relative_or_empty_path_is_rejected():
    for bad in ("", "   ", "backup", r".\pasta"):
        with pytest.raises(BackupError):
            validate_dest(bad)
    if sys.platform == "win32":
        assert validate_dest(r'"C:\Users\x\OneDrive\bk"').is_absolute()
    else:
        assert validate_dest('"/home/x/OneDrive/bk"').is_absolute()


def test_backup_now_records_status(env):
    db, images, tmp = env
    assert backup_now(db) is None                            # desligado
    db.set_setting("backup_dir", str(tmp / "bk"))
    backup_now(db)
    assert db.setting("last_backup_ok") == "1" and db.setting("last_backup_at")
    assert "imagens" in db.setting("last_backup_msg")


def test_backup_failure_is_recorded(env, monkeypatch):
    db, images, tmp = env
    blocker = tmp / "arquivo"
    blocker.write_text("não é pasta")
    db.set_setting("backup_dir", str(blocker / "sub"))
    with pytest.raises(BackupError):
        backup_now(db)
    assert db.setting("last_backup_ok") == "0" and "não consegui gravar" in db.setting("last_backup_msg")


def test_scheduler_backs_up_once_a_day_even_if_it_fails(env):
    db, images, tmp = env
    w = Worker(db)
    w.tick()
    assert w.jobs.empty()                                    # sem pasta: backup desligado
    db.set_setting("backup_dir", str(tmp / "bk"))
    w.tick()
    w.tick()
    assert [j.kind for j in w.jobs.queue] == ["backup"]
    w.jobs.get()                                             # mesmo que falhe, não repete hoje
    w.tick()
    assert w.jobs.empty()


def test_metrics_failure_does_not_retry_every_tick(env):
    db, images, tmp = env
    post_id = db.create_post("a.png")
    db.add_publication(post_id, "danbooru", "ok", "https://danbooru.donmai.us/posts/7")
    w = Worker(db)
    w.tick()
    w.jobs.get()                                             # job "falhou": metrics_last_run vazio
    w.tick()
    assert w.jobs.empty()
    assert db.setting("metrics_last_attempt") == datetime.now().date().isoformat()


def test_sites_page_backup_settings(env):
    db, images, tmp = env
    w = Worker(db)
    c = TestClient(app_mod.create_app(db, w, start_worker=False))
    assert "Backup diário" in c.get("/ajustes").text and "Backup diário" not in c.get("/sites").text
    r = c.post("/settings/backup", data={"backup_dir": "relativo"}, follow_redirects=False)
    assert "erro=" in r.headers["location"] and db.setting("backup_dir") == ""
    dest = str(tmp / "bk")
    c.post("/settings/backup", data={"backup_dir": dest, "action": "now"})
    assert db.setting("backup_dir") == dest and [j.kind for j in w.jobs.queue] == ["backup"]
    c.post("/settings/backup", data={"backup_dir": dest, "action": "off"})
    assert db.setting("backup_dir") == ""
