"""Uma thread executa as tarefas de navegador/API em fila (uma por vez, para não brigar
pelo mesmo perfil de navegador), outra agenda os posts aprovados no horário e uma terceira,
separada, roda a sugestão da IA (um publish nunca espera uma análise)."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from boorubot import paheal_tags
from boorubot.config import ALL_SITES, build_site
from boorubot.db import DB, to_post
from boorubot.scheduler import BACKOFF, RECHECK_EVERY, pending_posts, rule_skips, site_due
from boorubot.sites.base import AccountRefused, ContentRefused, SiteUnavailable, SkipPost

log = logging.getLogger("boorubot.worker")


@dataclass
class Job:
    kind: str  # publish | publish_site (produção) | test | login | session | session_test | metrics | backup
    target: int | str
    label: str = ""
    started: float = field(default=0.0)


def _ready_post(db: DB, row: dict):
    """Arte sem tags do paheal (antiga, ou a página não abriu desde então): monta agora, sem rede,
    para o paheal nunca receber a lista inteira, que ele recusa."""
    if not row["paheal_tags"] and not row["paheal_tags_manual"]:
        paheal_tags.refresh(db, row["id"])
        row = db.get_post(row["id"])
    return to_post(row)


def _send(db: DB, post, post_id: int, site: str) -> str:
    """Um envio REAL para um site; grava em publications. Retorna ok | skipped | refused | failed | uncertain."""
    try:
        result = build_site(site, db).publish(post, dry_run=False)
        status = "ok" if result.ok else ("uncertain" if result.info.get("uncertain") else "failed")
        db.add_publication(post_id, site, status, result.url, result.message)
    except SkipPost as exc:
        status = "skipped"
        db.add_publication(post_id, site, status, message=str(exc))
    except ContentRefused as exc:
        # o site recusou esta arte: não reenvia igual; volta para a fila do site quando ela corrigir
        status = "refused"
        db.add_publication(post_id, site, status, message=str(exc))
        log.warning("%s recusou o post %s: %s", site, post_id, exc)
    except SiteUnavailable as exc:
        # o problema é o site: pausa o site inteiro (sessão = desconectado) e não gasta tentativa da arte
        status = "site_down"
        db.add_publication(post_id, site, status, message=str(exc))
        db.set_session(site, False, str(exc)[:300])
        if isinstance(exc, AccountRefused):
            db.set_setting(f"blocked_{site}", "1")  # só uma pessoa libera (Testar/Entrar)
        log.warning("site %s pausado: %s", site, exc)
    except Exception as exc:  # uma falha num site não impede os outros
        status = "failed"
        log.exception("falha publicando post %s em %s", post_id, site)
        db.add_publication(post_id, site, status, message=f"{type(exc).__name__}: {exc}")
    return status


def _after_send(db: DB, post_id: int, site: str, status: str) -> None:
    if status == "ok":
        db.mark_post_published_at(post_id)  # 1º site: referência do atraso dos outros
        db.set_site_backoff(site, None)
    elif status == "failed":
        # sem cascata: o site espera um pouco e tenta a MESMA arte de novo (não pula para a próxima)
        db.set_site_backoff(site, datetime.now() + BACKOFF)


def refresh_post_status(db: DB, post_id: int) -> str:
    """Na fila enquanto falta algum site; publicado quando todos receberam (ou pularam pela regra)."""
    row = db.get_post(post_id)
    if not row or row["status"] not in ("approved", "partial", "failed", "publishing"):
        return row["status"] if row else "missing"
    if db.pending_sites(row):
        final = "approved"
    else:  # nada mais automático: incerto ou recusado vira "parcial" e aparece em "Para revisar"
        final = "partial" if db.uncertain_sites(post_id) or db.refused_sites(post_id) else "done"
    if final != row["status"]:
        db.update_post(post_id, status=final, queue_pos=row["queue_pos"] if final == "approved" else None)
    return final


def publish_post(db: DB, post_id: int) -> str:
    """PRODUÇÃO, "Publicar agora": manda a arte para todos os sites pendentes dela, já.
    Conta como o post do dia nesses sites (a fila de cada um segue amanhã).
    Recusa rodar com o modo teste ligado: produção e teste nunca se misturam."""
    row = db.get_post(post_id)
    if not row or row["status"] == "deleted":
        return "missing"
    if db.test_mode:
        log.warning("post %s: produção bloqueada com o modo teste ligado", post_id)
        return "blocked"
    post = _ready_post(db, row)
    results = []
    for site in db.pending_sites(row):
        status = _send(db, post, post_id, site)
        _after_send(db, post_id, site, status)
        results.append(status)
    if row["status"] in ("partial", "failed", "draft", "publishing"):
        db.update_post(post_id, status="approved")  # "Tentar de novo": volta para a fila do site
    final = refresh_post_status(db, post_id)
    if final == "approved" and results and all(r == "failed" for r in results):
        return "failed"
    return final


def publish_site(db: DB, post_id: int, site: str) -> str:
    """PRODUÇÃO: o envio do dia de UM site (o worker chama quando scheduler.site_due manda). Nunca duplica."""
    if db.test_mode:
        return "blocked"
    row = db.get_post(post_id)
    if not row or row["status"] == "deleted":  # apagada depois de entrar na fila de envio
        return "missing"
    if site in db.published_sites(post_id):
        refresh_post_status(db, post_id)
        return "ok"
    status = _send(db, _ready_post(db, row), post_id, site)
    _after_send(db, post_id, site, status)
    refresh_post_status(db, post_id)
    return status


def record_rule_skips(db: DB) -> None:
    """Arte que o site recusa pela regra (ex.: só OC no paheal) é marcada como pulada na hora,
    sem gastar o dia do site e sem tentativa de envio."""
    for post in db.queue():
        for site in db.pending_sites(post):
            if rule_skips(site, post):
                db.add_publication(post["id"], site, "skipped", message="regra do site (pulado)")
        refresh_post_status(db, post["id"])


def run_post_test(db: DB, post_id: int) -> list[str]:
    """TESTE: simula o post em cada site marcado. Grava só em test_runs; NÃO muda status,
    posição na fila, published_at nem o ritmo. Danbooru só envia de verdade se houver conta
    de teste (testbooru); os sites de formulário apenas preenchem, sem enviar."""
    row = db.get_post(post_id)
    if not row or row["status"] == "deleted":
        return []
    post = _ready_post(db, row)
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
    def __init__(self, db: DB, scheduler_interval: float = 30, tagger=None):
        self.db = db
        self.jobs: queue.Queue[Job] = queue.Queue()
        self.current: Job | None = None
        self.scheduler_interval = scheduler_interval
        self.tagger = tagger  # None = o WD Tagger de verdade (carregado só na 1ª análise)
        # fila só da IA: fora de `jobs` e de pending() (a página não recarrega a cada análise)
        self.suggestions: queue.Queue[int] = queue.Queue()
        self.suggesting: int | None = None
        self._stop = threading.Event()
        self._cancel_login = threading.Event()  # botão Cancelar do "esperando login" em Sites

    def start(self) -> None:
        for post_id in self.db.posts_suggesting():  # painel fechou no meio de uma análise
            self.suggestions.put(post_id)
        threading.Thread(target=self._run_jobs, name="jobs", daemon=True).start()
        threading.Thread(target=self._run_scheduler, name="scheduler", daemon=True).start()
        threading.Thread(target=self._run_suggestions, name="suggest", daemon=True).start()

    def submit_suggest(self, post_id: int) -> None:
        if post_id == self.suggesting or post_id in list(self.suggestions.queue):
            return
        self.db.set_suggest_state(post_id, "pendente")
        self.suggestions.put(post_id)

    def suggest_position(self, post_id: int) -> int | None:
        """Quantas análises estão na frente desta (0 = é a próxima); None = não está esperando."""
        waiting = list(self.suggestions.queue)
        if post_id not in waiting:
            return None
        return waiting.index(post_id) + (1 if self.suggesting is not None else 0)

    def _run_suggestions(self) -> None:
        from boorubot.suggest import run_suggestion

        while not self._stop.is_set():
            try:
                post_id = self.suggestions.get(timeout=1)
            except queue.Empty:
                continue
            self.suggesting = post_id
            try:
                run_suggestion(self.db, post_id, self.tagger)
            except Exception:
                log.exception("sugestão %s falhou", post_id)
            finally:
                self.suggesting = None

    def stop(self) -> None:
        self._stop.set()

    def submit(self, kind: str, target: int | str, label: str = "") -> None:
        self.jobs.put(Job(kind, target, label or f"{kind} {target}"))

    def logins(self) -> list[str]:
        """Sites com login esperando a pessoa (na fila ou com a janela aberta)."""
        jobs = ([self.current] if self.current else []) + list(self.jobs.queue)
        return [str(j.target) for j in jobs if j and j.kind == "login"]

    def cancel_login(self, site: str) -> None:
        """Tira da fila o login do site; se a janela já está aberta, pede para fechar."""
        with self.jobs.mutex:
            kept = [j for j in self.jobs.queue if not (j.kind == "login" and j.target == site)]
            self.jobs.queue.clear()
            self.jobs.queue.extend(kept)
        current = self.current
        if current and current.kind == "login" and current.target == site:
            self._cancel_login.set()

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
            self._cancel_login.clear()
            try:
                result = site.login(self._cancel_login.is_set) if job.kind == "login" else site.session_check()
                if result is None:  # login cancelado: a sessão fica como estava
                    return
                self.db.set_session(site.name, result.ok, result.message)
                if site.name == "danbooru":
                    # nível real da conta: decide se o post vai direto ao ar ou para moderação
                    self.db.set_setting("danbooru_level", str(result.info.get("level", "")))
                    self.db.set_setting("danbooru_level_string", result.info.get("level_string", ""))
            except Exception as exc:
                self.db.set_session(site.name, False, f"{type(exc).__name__}: {exc}")

    def tick(self) -> None:
        self._tick_sites()
        self._tick_recheck()
        self._tick_daily()

    def _queued(self, kind: str) -> bool:
        return any(j.kind == kind for j in list(self.jobs.queue)) or (
            self.current is not None and self.current.kind == kind)

    def _tick_recheck(self) -> None:
        """Site pausado (desconectado) com arte esperando: roda o "Testar" a cada 30 min.
        Se a sessão voltou (login feito, internet de volta), a fila do site continua sozinha."""
        if self.db.test_mode or self._queued("session") or self._queued("login"):
            return
        now = datetime.now()
        for site, sess in self.db.sessions().items():
            if site not in ALL_SITES or sess["ok"] or not pending_posts(self.db, site):
                continue
            if self.db.setting(f"blocked_{site}") == "1":
                continue  # conta recusada pelo site: re-testar sozinho não resolve e pode piorar
            last = self.db.setting(f"recheck_{site}")
            if last and now - datetime.fromisoformat(last) < RECHECK_EVERY:
                continue
            self.db.set_setting(f"recheck_{site}", now.isoformat(timespec="seconds"))
            self.submit("session", site, f"testar sessão {site} (site pausado)")
            return

    def _tick_daily(self) -> None:
        """Manutenção 1x por dia (não publica nada; roda também com o modo teste ligado).
        Marca a TENTATIVA do dia: se a API cair ou a pasta sumir, não repete a cada 30 s."""
        today = datetime.now().date().isoformat()
        if (self.db.setting("metrics_last_attempt") != today and not self._queued("metrics")
                and self.db.publications_for_metrics("danbooru")):
            self.db.set_setting("metrics_last_attempt", today)
            self.submit("metrics", "danbooru", "atualizar métricas (diário)")
        if self.db.setting("purge_last_run") != today:
            self.db.set_setting("purge_last_run", today)
            self.purge()
        if (self.db.setting("backup_dir") and self.db.setting("backup_last_attempt") != today
                and not self._queued("backup")):
            self.db.set_setting("backup_last_attempt", today)
            self.submit("backup", "backup", "backup diário")

    def purge(self, when: datetime | None = None) -> int:
        """Limpeza diária: artes apagadas há mais de 24 h saem de vez (linha + imagem)."""
        from boorubot.db import IMAGES_DIR

        images = self.db.purge_deleted(when)
        for name in images:
            (IMAGES_DIR / name).unlink(missing_ok=True)
        if images:
            log.info("limpeza: %s artes apagadas removidas de vez", len(images))
        return len(images)

    def _publishing(self) -> bool:
        """Só 1 envio de produção por vez."""
        return self._queued("publish") or self._queued("publish_site")

    def _tick_sites(self) -> None:
        """Fila por site: cada site publica a próxima arte dele quando o ritmo do site deixa.
        No máximo 1 envio por vez no total. Modo teste pausa tudo."""
        if self.db.test_mode or self._publishing():
            return
        record_rule_skips(self.db)
        for site in ALL_SITES:
            post = site_due(self.db, site)
            if post:
                self.submit("publish_site", f"{post['id']}:{site}", f"publicar #{post['id']} no {site}")
                return

    def _run_scheduler(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("agendador falhou")
            self._stop.wait(self.scheduler_interval)
