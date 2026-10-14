"""Uma thread executa as tarefas de navegador/API em fila (uma por vez, para não brigar
pelo mesmo perfil de navegador) e outra agenda os posts aprovados no horário."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from boorubot.config import ALL_SITES, build_site
from boorubot.db import DB, to_post
from boorubot.sites.base import SkipPost

log = logging.getLogger("boorubot.worker")


@dataclass
class Job:
    kind: str  # publish | publish_site (produção) | test | login | session | session_test | metrics | backup
    target: int | str
    label: str = ""
    started: float = field(default=0.0)


def _send(db: DB, post, post_id: int, site: str) -> str:
    """Um envio REAL para um site; grava em publications. Retorna ok | skipped | failed."""
    try:
        result = build_site(site, db).publish(post, dry_run=False)
        status = "ok" if result.ok else "failed"
        db.add_publication(post_id, site, status, result.url, result.message)
    except SkipPost as exc:
        status = "skipped"
        db.add_publication(post_id, site, status, message=str(exc))
    except Exception as exc:  # uma falha num site não impede os outros
        status = "failed"
        log.exception("falha publicando post %s em %s", post_id, site)
        db.add_publication(post_id, site, status, message=f"{type(exc).__name__}: {exc}")
    return status


SCHEDULE_STATUS = {"ok": "publicado", "failed": "falhou", "skipped": "pulado"}


def _status_from(results: list[str]) -> str:
    if results and all(s == "failed" for s in results):
        return "failed"
    if "failed" in results:
        return "partial"
    return "done"


def refresh_post_status(db: DB, post_id: int) -> str:
    """Status de um post que tem sites agendados: publicando enquanto falta algum site;
    depois, o último resultado de cada site decide (done | partial | failed)."""
    row = db.get_post(post_id)
    schedule = {r["site"]: r for r in db.site_schedule(post_id=post_id)}
    if any(r["status"] == "agendado" for r in schedule.values()):
        final = "publishing"
    else:
        latest: dict[str, str] = {}
        for pub in reversed(db.publications(post_id)):  # do mais antigo para o mais novo
            latest[pub["site"]] = pub["status"]
        final = _status_from([latest[s] for s in row["sites"] if s in latest])
    db.update_post(post_id, status=final)
    return final


def publish_post(db: DB, post_id: int) -> str:
    """PRODUÇÃO: o "dia 0" de uma arte. Publica agora nos sites que seguem a fila (diário, sem
    atraso: o padrão) e agenda em site_schedule os sites com atraso ou dia sim/dia não.
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
    schedule = {r["site"]: r for r in db.site_schedule(post_id=post_id)}
    now = datetime.now()
    results, scheduled = [], 0
    for name in row["sites"]:
        if name in done:
            continue
        existing = schedule.get(name)
        if existing and existing["status"] in ("agendado", "pulado"):
            continue  # já tem dia próprio no calendário do site (ou o site está desligado)
        rhythm, delay = db.site_rhythm(name), db.site_delay(name)
        if rhythm == "desligado":
            db.schedule_site(post_id, name, now, status="pulado")
            continue
        retry = existing is not None and existing["status"] == "falhou"  # "Tentar de novo" manual
        if not retry and (delay > 0 or (rhythm == "dia_sim_dia_nao" and not db.site_slot_open(name, now))):
            due = db.daily_slot(now + timedelta(days=delay)) if delay else now
            db.schedule_site(post_id, name, due)
            scheduled += 1
            continue
        status = _send(db, post, post_id, name)
        if existing:
            db.set_schedule_status(post_id, name, SCHEDULE_STATUS[status])
        results.append(status)

    if "ok" in results:
        db.mark_published(post_id)  # conta para o ritmo só se saiu de verdade em algum site
    elif scheduled:
        db.mark_released(now)  # nada saiu agora, mas hoje foi o dia 0 desta arte
    if db.site_schedule(post_id=post_id):
        return refresh_post_status(db, post_id)
    final = _status_from(results)  # sem agenda por site: exatamente como sempre foi
    db.update_post(post_id, status=final)
    return final


def publish_site(db: DB, post_id: int, site: str) -> str:
    """PRODUÇÃO: envio agendado de UM site (atraso / dia sim, dia não). Nunca duplica."""
    if db.test_mode:
        return "blocked"
    row = db.get_post(post_id)
    if not row:
        db.set_schedule_status(post_id, site, "pulado")
        return "missing"
    if site in db.published_sites(post_id):
        db.set_schedule_status(post_id, site, "publicado")
        refresh_post_status(db, post_id)
        return "ok"
    status = _send(db, to_post(row), post_id, site)
    db.set_schedule_status(post_id, site, SCHEDULE_STATUS[status])
    if status == "ok":
        db.mark_post_published_at(post_id)  # não conta como "post do dia" da fila principal
    refresh_post_status(db, post_id)
    return status


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
        elif job.kind == "publish_site":
            post_id, site = str(job.target).split(":", 1)
            publish_site(self.db, int(post_id), site)
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
        self._tick_sites()
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
        if self.db.test_mode or self._publishing():
            return
        row = self.db.next_due()
        if row:
            self.db.update_post(row["id"], status="publishing", queue_pos=None)
            self.submit("publish", row["id"], f"publicar #{row['id']} (agendado)")

    def _publishing(self) -> bool:
        """Só 1 envio de produção por vez (fila principal ou agenda de site)."""
        return self._queued("publish") or self._queued("publish_site")

    def _tick_sites(self) -> None:
        """Envios atrasados por site: cada site no próprio ritmo, no máximo 1 por vez no total."""
        if self.db.test_mode or self._publishing():
            return
        for site in ALL_SITES:
            row = self.db.next_site_due(site)
            if row:
                self.submit("publish_site", f"{row['post_id']}:{site}", f"publicar #{row['post_id']} no {site}")
                return

    def _run_scheduler(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("agendador falhou")
            self._stop.wait(self.scheduler_interval)
