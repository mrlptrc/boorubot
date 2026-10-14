"""Uma thread executa as tarefas de navegador/API em fila (uma por vez, para não brigar
pelo mesmo perfil de navegador) e outra agenda os posts aprovados no horário."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from boorubot.config import build_site
from boorubot.db import DB, to_post
from boorubot.sites.base import SkipPost

log = logging.getLogger("boorubot.worker")


@dataclass
class Job:
    kind: str  # publish (produção) | test | login | session | session_test | metrics | backup
    target: int | str
    label: str = ""
    started: float = field(default=0.0)


def publish_post(db: DB, post_id: int) -> str:
    """PRODUÇÃO: publica de verdade nos sites pendentes do post. Retorna o status final.
    Recusa rodar com o modo teste ligado: produção e teste nunca se misturam."""
    row = db.get_post(post_id)
    if not row:
        return "missing"
    if db.test_mode:
        log.warning("post %s: produção bloqueada com o modo teste ligado", post_id)
        return "blocked"
    db.update_post(post_id, status="publishing", queue_pos=None)
    post = to_post(row)
    done = db.published_sites(post_id)
    results = []
    for name in row["sites"]:
        if name in done:
            continue
        try:
            result = build_site(name, db).publish(post, dry_run=False)
            status = "ok" if result.ok else "failed"
            db.add_publication(post_id, name, status, result.url, result.message)
        except SkipPost as exc:
            status = "skipped"
            db.add_publication(post_id, name, status, message=str(exc))
        except Exception as exc:  # uma falha num site não impede os outros
            status = "failed"
            log.exception("falha publicando post %s em %s", post_id, name)
            db.add_publication(post_id, name, status, message=f"{type(exc).__name__}: {exc}")
        results.append(status)

    if "ok" in results:
        db.mark_published(post_id)  # conta para o ritmo só se saiu de verdade em algum site
    if results and all(s == "failed" for s in results):
        final = "failed"
    elif "failed" in results:
        final = "partial"
    else:
        final = "done"
    db.update_post(post_id, status=final)
    return final


def run_post_test(db: DB, post_id: int) -> list[str]:
    """TESTE: simula o post em cada site marcado. Grava só em test_runs; NÃO muda status,
    posição na fila, published_at nem o ritmo. Danbooru só envia de verdade se houver conta
    de teste (testbooru); os sites de formulário apenas preenchem, sem enviar."""
    row = db.get_post(post_id)
    if not row:
        return []
    post = to_post(row)
    has_test_account = bool(db.setting("danbooru_test_api_key"))
    results = []
    for name in row["sites"]:
        upload = name == "danbooru" and has_test_account
        try:
            result = build_site(name, db, test=True).publish(post, dry_run=not upload)
            status = ("test_upload" if upload else "dry_run") if result.ok else "failed"
            db.add_test_run(post_id, name, status, result.url, result.message)
        except SkipPost as exc:
            status = "skipped"
            db.add_test_run(post_id, name, status, message=str(exc))
        except Exception as exc:
            status = "failed"
            log.exception("falha testando post %s em %s", post_id, name)
            db.add_test_run(post_id, name, status, message=f"{type(exc).__name__}: {exc}")
        results.append(status)
    return results


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
        elif job.kind == "test":
            run_post_test(self.db, int(job.target))
        elif job.kind == "metrics":
            from boorubot.metrics import collect_danbooru_metrics

            log.info("métricas: %s snapshots gravados", collect_danbooru_metrics(self.db))
        elif job.kind == "backup":
            from boorubot.backup import backup_now

            result = backup_now(self.db)
            if result:
                log.info("backup em %s: %s imagens novas", result.dest, result.images_copied)
        elif job.kind == "session_test":  # conta de teste do Danbooru (testbooru)
            try:
                result = build_site("danbooru", self.db, test=True).session_check()
                self.db.set_session("danbooru_test", result.ok, result.message)
            except Exception as exc:
                self.db.set_session("danbooru_test", False, f"{type(exc).__name__}: {exc}")
        elif job.kind in ("login", "session"):
            site = build_site(str(job.target), self.db)
            try:
                result = site.login() if job.kind == "login" else site.session_check()
                self.db.set_session(site.name, result.ok, result.message)
                if site.name == "danbooru":
                    # nível real da conta: decide se o post vai direto ao ar ou para moderação
                    self.db.set_setting("danbooru_level", str(result.info.get("level", "")))
                    self.db.set_setting("danbooru_level_string", result.info.get("level_string", ""))
            except Exception as exc:
                self.db.set_session(site.name, False, f"{type(exc).__name__}: {exc}")

    def tick(self) -> None:
        self._tick_publish()
        self._tick_daily()

    def _queued(self, kind: str) -> bool:
        return any(j.kind == kind for j in list(self.jobs.queue)) or (
            self.current is not None and self.current.kind == kind)

    def _tick_daily(self) -> None:
        """Manutenção 1x por dia (não publica nada; roda também com o modo teste ligado).
        Marca a TENTATIVA do dia: se a API cair ou a pasta sumir, não repete a cada 30 s."""
        today = datetime.now().date().isoformat()
        if (self.db.setting("metrics_last_attempt") != today and not self._queued("metrics")
                and self.db.publications_for_metrics("danbooru")):
            self.db.set_setting("metrics_last_attempt", today)
            self.submit("metrics", "danbooru", "atualizar métricas (diário)")
        if (self.db.setting("backup_dir") and self.db.setting("backup_last_attempt") != today
                and not self._queued("backup")):
            self.db.set_setting("backup_last_attempt", today)
            self.submit("backup", "backup", "backup diário")

    def _tick_publish(self) -> None:
        """No máximo 1 publicação por vez, liberada pelo pacer.
        Em modo teste a fila fica pausada (testes não contam no ritmo e esvaziariam a fila)."""
        if self.db.test_mode:
            return
        busy = any(j.kind == "publish" for j in list(self.jobs.queue))
        busy = busy or (self.current is not None and self.current.kind == "publish")
        if busy:
            return
        row = self.db.next_due()
        if row:
            self.db.update_post(row["id"], status="publishing", queue_pos=None)
            self.submit("publish", row["id"], f"publicar #{row['id']} (agendado)")

    def _run_scheduler(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("agendador falhou")
            self._stop.wait(self.scheduler_interval)
