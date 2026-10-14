"""Uma thread executa as tarefas de navegador/API em fila (uma por vez, para não brigar
pelo mesmo perfil de navegador) e outra agenda os posts aprovados no horário."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field

from boorubot.config import build_site
from boorubot.db import DB, to_post
from boorubot.sites.base import SkipPost

log = logging.getLogger("boorubot.worker")


@dataclass
class Job:
    kind: str  # publish | login | session
    target: int | str
    label: str = ""
    started: float = field(default=0.0)


def publish_post(db: DB, post_id: int) -> str:
    """Publica nos sites pendentes do post. Retorna o status final do post."""
    row = db.get_post(post_id)
    if not row:
        return "missing"
    dry_run = db.test_mode
    db.update_post(post_id, status="publishing")
    post = to_post(row)
    done = db.published_sites(post_id)
    results = []
    for name in row["sites"]:
        if name in done:
            continue
        try:
            result = build_site(name).publish(post, dry_run=dry_run)
            status = "dry_run" if result.dry_run else ("ok" if result.ok else "failed")
            db.add_publication(post_id, name, status, result.url, result.message)
        except SkipPost as exc:
            status = "skipped"
            db.add_publication(post_id, name, status, message=str(exc))
        except Exception as exc:  # uma falha num site não impede os outros
            status = "failed"
            log.exception("falha publicando post %s em %s", post_id, name)
            db.add_publication(post_id, name, status, message=f"{type(exc).__name__}: {exc}")
        results.append(status)

    if results and all(s == "failed" for s in results):
        final = "failed"
    elif "failed" in results:
        final = "partial"
    else:
        final = "tested" if dry_run else "done"
    db.update_post(post_id, status=final)
    return final


class Worker:
    def __init__(self, db: DB, scheduler_interval: float = 30):
        self.db = db
        self.jobs: queue.Queue[Job] = queue.Queue()
        self.current: Job | None = None
        self.scheduler_interval = scheduler_interval
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run_jobs, name="jobs", daemon=True).start()
        threading.Thread(target=self._run_scheduler, name="scheduler", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def submit(self, kind: str, target: int | str, label: str = "") -> None:
        self.jobs.put(Job(kind, target, label or f"{kind} {target}"))

    def pending(self) -> list[str]:
        items = [j.label for j in list(self.jobs.queue)]
        return ([f"{self.current.label} (rodando)"] if self.current else []) + items

    def _run_jobs(self) -> None:
        while not self._stop.is_set():
            try:
                job = self.jobs.get(timeout=1)
            except queue.Empty:
                continue
            self.current = job
            job.started = time.time()
            try:
                self._handle(job)
            except Exception:
                log.exception("job %s falhou", job.label)
            finally:
                self.current = None

    def _handle(self, job: Job) -> None:
        if job.kind == "publish":
            publish_post(self.db, int(job.target))
        elif job.kind in ("login", "session"):
            site = build_site(str(job.target))
            try:
                result = site.login() if job.kind == "login" else site.session_check()
                self.db.set_session(site.name, result.ok, result.message)
            except Exception as exc:
                self.db.set_session(site.name, False, f"{type(exc).__name__}: {exc}")

    def _run_scheduler(self) -> None:
        while not self._stop.is_set():
            queued = {j.target for j in list(self.jobs.queue) if j.kind == "publish"}
            for row in self.db.due_posts():
                if row["id"] not in queued:
                    self.submit("publish", row["id"], f"publicar #{row['id']} (agendado)")
            self._stop.wait(self.scheduler_interval)
