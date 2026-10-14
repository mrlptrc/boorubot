"""Painel local: fila de artes, sugestão de tags, aprovação/agendamento, sites e histórico."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from urllib.parse import quote
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from boorubot import startup
from boorubot.config import ALL_SITES, SITE_LABELS, build_site
from boorubot.sites.forms import FORM_SITES
from boorubot.db import DB, IMAGES_DIR
from boorubot.models import IMAGE_EXTS, RATINGS
from boorubot.sites.danbooru import CONTRIBUTOR_LEVEL
from boorubot.worker import Worker

HERE = Path(__file__).parent

BROWSER_MODE_LABELS = {
    "headless": "invisível (sem janela)",
    "hidden": "janela escondida (aparece só se pedir captcha)",
    "visible": "janela visível",
}
RATINGS_PT = {"general": "geral (SFW)", "sensitive": "sensível", "questionable": "questionável (nudez)",
              "explicit": "explícito"}
STATUS_LABELS = {
    "draft": "rascunho", "approved": "na fila", "publishing": "publicando",
    "done": "publicado", "partial": "parcial", "failed": "falhou",
    "ok": "publicado", "skipped": "pulado",
    "dry_run": "simulado", "test_upload": "enviado ao testbooru",
}


def normalize_tags(raw: str) -> list[str]:
    """Aceita "long_hair 1girl" (padrão booru) ou "long hair, 1girl" (vírgulas); remove repetidas."""
    items = raw.split(",") if "," in raw else raw.split()
    tags = ("_".join(t.strip().lower().split()) for t in items)
    return list(dict.fromkeys(t for t in tags if t))


def see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def create_app(db: DB | None = None, worker: Worker | None = None, start_worker: bool = True) -> FastAPI:
    db = db or DB()
    worker = worker or Worker(db)
    if start_worker:
        worker.start()

    app = FastAPI(title="boorubot")
    app.state.db, app.state.worker = db, worker
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(
        SITE_LABELS=SITE_LABELS, STATUS_LABELS=STATUS_LABELS, RATINGS=RATINGS, RATINGS_PT=RATINGS_PT,
        ALL_SITES=ALL_SITES, BROWSER_MODE_LABELS=BROWSER_MODE_LABELS,
    )

    def danbooru_mode() -> str:
        """Como o post entra no Danbooru, pelo nível REAL lido da conta (nunca presumido).
        unknown | direct (Contributor, sem moderação) | forced (Contributor pedindo moderação) | moderated"""
        level = db.setting("danbooru_level")
        if not level.isdigit() or not db.sessions().get("danbooru", {}).get("ok"):
            return "unknown"
        if int(level) >= CONTRIBUTOR_LEVEL:
            return "forced" if db.setting("danbooru_force_pending") == "1" else "direct"
        return "moderated"

    def render(request: Request, name: str, **ctx):
        ctx.update(test_mode=db.test_mode, jobs=worker.pending(),
                   danbooru_mode=danbooru_mode(), danbooru_level=db.setting("danbooru_level_string"))
        return templates.TemplateResponse(request, name, ctx)

    def default_sites() -> list[str]:
        try:
            return [s for s in ALL_SITES if s in json.loads(db.setting("default_sites"))]
        except ValueError:
            return list(ALL_SITES)

    def setup_steps() -> list[dict]:
        """Checklist de configuração mostrada na Fila até tudo estar pronto."""
        sessions = db.sessions()
        steps = [{
            "label": f"Conectar {SITE_LABELS[s]}",
            "done": bool(sessions.get(s, {}).get("ok")),
            "href": f"/guia#{s}",
        } for s in default_sites()]
        steps.append({"label": "Testar um post (botão \"Testar este post\")", "done": bool(db.test_runs()),
                      "href": "/guia#teste"})
        steps.append({"label": "Desligar o modo teste para começar a postar", "done": not db.test_mode,
                      "href": "/guia#valendo"})
        return steps

    def post_or_404(post_id: int) -> dict:
        row = db.get_post(post_id)
        if not row:
            raise HTTPException(404, "post não encontrado")
        return row

    # --- fila --------------------------------------------------------------------

    @app.get("/")
    def index(request: Request):
        from boorubot import dashboard

        return render(request, "index.html", d=dashboard.build(db), steps=setup_steps())

    @app.post("/posts")
    async def create_post(image: UploadFile = File(...)):
        ext = Path(image.filename or "").suffix.lower()
        if ext not in IMAGE_EXTS:
            raise HTTPException(400, f"formato não suportado: {ext or '?'} (use {', '.join(IMAGE_EXTS)})")
        filename = f"{uuid.uuid4().hex}{ext}"
        (IMAGES_DIR / filename).write_bytes(await image.read())
        post_id = db.create_post(filename, title=Path(image.filename).stem, sites=default_sites(),
                                 tags=" ".join(normalize_tags(db.setting("default_tags"))))
        return see_other(f"/posts/{post_id}")

    @app.get("/posts/{post_id}")
    def edit_post(request: Request, post_id: int, erro: str = ""):
        row = post_or_404(post_id)
        queue_ids = [p["id"] for p in db.queue()]
        return render(
            request, "post.html", post=row, pubs=db.publications(post_id), tests=db.test_runs(post_id), error=erro,
            metrics=db.latest_metrics(),
            has_test_account=bool(db.setting("danbooru_test_api_key")),
            projected=db.projected_times().get(post_id),
            queue_pos=queue_ids.index(post_id) + 1 if post_id in queue_ids else None, queue_len=len(queue_ids),
            published_today=bool(db.last_published() and db.last_published().date() == datetime.now().date()),
        )

    @app.post("/posts/{post_id}")
    def save_post(
        post_id: int,
        action: str = Form("save"),
        title: str = Form(""),
        tags: str = Form(""),
        rating: str = Form("general"),
        source: str = Form(""),
        commentary_title: str = Form(""),
        commentary_description: str = Form(""),
        original_character: bool = Form(False),
        sites: list[str] = Form([]),
    ):
        row = post_or_404(post_id)
        if action == "delete":
            db.delete_post(post_id)
            (IMAGES_DIR / row["image"]).unlink(missing_ok=True)
            return see_other("/")

        if row["status"] in ("draft", "approved", "failed", "partial"):
            clean_tags = " ".join(normalize_tags(tags))
            db.update_post(
                post_id, title=title.strip(), tags=clean_tags,
                rating=rating if rating in RATINGS else "general", source=source.strip(),
                commentary_title=commentary_title.strip(), commentary_description=commentary_description.strip(),
                original_character=original_character, sites=[s for s in ALL_SITES if s in sites],
            )

        if action == "publish_now" and db.test_mode:
            return see_other(f"/posts/{post_id}?erro=" + quote(
                "modo teste ligado: nada é publicado. Use \"Testar este post\" ou desligue o modo teste em Sites."))
        if action in ("approve", "publish_now", "test"):
            errors = []
            if not db.get_post(post_id)["tags"]:
                errors.append("sem tags")
            if not sites:
                errors.append("nenhum site marcado")
            if errors:
                return see_other(f"/posts/{post_id}?erro=" + quote("não dá para aprovar: " + ", ".join(errors)))
        if action == "approve":
            db.approve(post_id)
        elif action == "publish_now":
            db.update_post(post_id, status="publishing", queue_pos=None)
            worker.submit("publish", post_id, f"publicar #{post_id} (agora)")
        elif action == "test":  # não muda nada no post: só grava em test_runs
            worker.submit("test", post_id, f"testar #{post_id}")
        elif action == "unapprove":
            db.update_post(post_id, status="draft", queue_pos=None)
        elif action in ("first", "last") and row["status"] == "approved":
            db.move_in_queue(post_id, action)
        return see_other(f"/posts/{post_id}")

    @app.post("/posts/{post_id}/suggest")
    def suggest(post_id: int):
        from boorubot.tagger import tagger

        row = post_or_404(post_id)
        s = tagger.suggest(IMAGES_DIR / row["image"])
        return JSONResponse({
            "rating": s.rating,
            "rating_scores": {k: round(v, 3) for k, v in s.rating_scores.items()},
            "tags": s.tags,
            "characters": [t for t, _ in s.characters],
            "scores": {t: round(v, 2) for t, v in s.characters + s.general},
        })

    # --- sites -------------------------------------------------------------------

    @app.get("/sites")
    def sites_page(request: Request, erro: str = ""):
        key, test_key = db.setting("danbooru_api_key"), db.setting("danbooru_test_api_key")
        return render(
            request, "sites.html", sessions=db.sessions(), daily_time=db.setting("daily_time"),
            danbooru_login=db.setting("danbooru_login"),
            danbooru_key_hint=f"••••{key[-4:]}" if key else "",
            danbooru_test_login=db.setting("danbooru_test_login"),
            danbooru_test_key_hint=f"••••{test_key[-4:]}" if test_key else "",
            default_tags=db.setting("default_tags"), default_sites=default_sites(),
            force_pending=db.setting("danbooru_force_pending") == "1",
            startup=startup.status(), error=erro,
            backup_dir=db.setting("backup_dir"), last_backup_at=db.setting("last_backup_at"),
            last_backup_ok=db.setting("last_backup_ok"), last_backup_msg=db.setting("last_backup_msg"),
            send_modes={s: build_site(s, db).browser_mode("send") for s in FORM_SITES},
            default_modes={s: build_site(s).browser_mode("send") for s in FORM_SITES},
        )

    @app.post("/settings/backup")
    def save_backup(backup_dir: str = Form(""), action: str = Form("save")):
        from boorubot.backup import BackupError, validate_dest

        if action == "off" or not backup_dir.strip():
            db.set_setting("backup_dir", "")
            return see_other("/sites#backup")
        try:
            dest = validate_dest(backup_dir)
        except BackupError as exc:
            return see_other("/sites?erro=" + quote(str(exc)) + "#backup")
        db.set_setting("backup_dir", str(dest))
        if action == "now":
            worker.submit("backup", "backup", "backup agora")
        return see_other("/sites#backup")

    @app.post("/settings/browser/{name}")
    def save_browser_mode(name: str, mode: str = Form(...)):
        if name not in FORM_SITES or mode not in BROWSER_MODE_LABELS:
            raise HTTPException(404)
        db.set_setting(f"browser_mode_{name}", mode)
        return see_other(f"/sites#{name}")

    @app.post("/settings/startup")
    def toggle_startup(action: str = Form(...)):
        try:
            if action == "install":
                startup.install()
            elif action == "remove":
                startup.remove()
        except startup.StartupError as exc:
            return see_other("/sites?erro=" + quote(str(exc)) + "#inicio")
        return see_other("/sites#inicio")

    @app.post("/settings/danbooru")
    def save_danbooru(login: str = Form(""), api_key: str = Form(""), force_pending: bool = Form(False)):
        """Conta REAL (produção). A conta de teste fica em /settings/danbooru-test."""
        # a conta pode ter mudado: esquece o nível até o próximo teste
        db.set_setting("danbooru_level", "")
        db.set_setting("danbooru_level_string", "")
        db.set_setting("danbooru_force_pending", "1" if force_pending else "0")
        db.set_setting("danbooru_login", login.strip())
        if api_key.strip():  # vazio = mantém a chave salva
            db.set_setting("danbooru_api_key", api_key.strip())
        worker.submit("session", "danbooru", "testar sessão Danbooru")
        return see_other("/sites#danbooru")

    @app.post("/settings/danbooru-test")
    def save_danbooru_test(login: str = Form(""), api_key: str = Form(""), remove: bool = Form(False)):
        """Conta de TESTE (testbooru): usada só pelo botão "Testar este post"."""
        if remove:
            db.set_setting("danbooru_test_api_key", "")
            db.set_session("danbooru_test", False, "conta de teste removida")
            return see_other("/sites#danbooru-test")
        db.set_setting("danbooru_test_login", login.strip())
        if api_key.strip():
            db.set_setting("danbooru_test_api_key", api_key.strip())
        worker.submit("session_test", "danbooru", "testar conta de teste (testbooru)")
        return see_other("/sites#danbooru-test")

    @app.post("/settings/danbooru/remove")
    def remove_danbooru():
        db.set_setting("danbooru_api_key", "")
        db.set_setting("danbooru_level", "")
        db.set_setting("danbooru_level_string", "")
        db.set_session("danbooru", False, "chave removida do painel")
        return see_other("/sites#danbooru")

    @app.get("/guia")
    def guide(request: Request):
        return render(request, "guide.html", sessions=db.sessions())

    @app.post("/sites/{name}/{kind}")
    def site_action(name: str, kind: str):
        if name == "danbooru-test" and kind == "session":
            worker.submit("session_test", "danbooru", "testar conta de teste (testbooru)")
            return see_other("/sites#danbooru-test")
        if name not in ALL_SITES or kind not in ("login", "session"):
            raise HTTPException(404)
        if kind == "login" and name == "danbooru":
            raise HTTPException(400, "Danbooru conecta por chave de API, não precisa de login no navegador")
        label = f"{'login' if kind == 'login' else 'testar sessão'} {SITE_LABELS[name]}"
        worker.submit(kind, name, label)
        return see_other("/sites")

    @app.post("/settings")
    def save_settings(
        test_mode: bool = Form(False),
        daily_time: str = Form("18:00"),
        default_tags: str = Form(""),
        default_sites: list[str] = Form([]),
    ):
        db.set_setting("test_mode", "1" if test_mode else "0")
        db.set_setting("default_tags", " ".join(normalize_tags(default_tags)))
        db.set_setting("default_sites", json.dumps([s for s in ALL_SITES if s in default_sites]))
        hh, _, mm = daily_time.partition(":")
        if hh.isdigit() and mm.isdigit() and int(hh) < 24 and int(mm) < 60:
            db.set_setting("daily_time", f"{int(hh):02d}:{int(mm):02d}")
        return see_other("/sites")

    # --- histórico ---------------------------------------------------------------

    @app.get("/historico")
    def history(request: Request):
        return render(request, "history.html", pubs=db.publications(limit=200), metrics=db.latest_metrics(),
                      metrics_last_run=db.setting("metrics_last_run"))

    @app.post("/historico/metricas")
    def refresh_metrics():
        worker.submit("metrics", "danbooru", "atualizar métricas")
        return see_other("/historico")

    @app.get("/status.json")
    def status():
        return {"jobs": worker.pending()}

    @app.get("/busca.json")
    def search_index():
        """Artes para a busca Ctrl+K (o filtro roda no navegador)."""
        return {"posts": [{
            "id": p["id"], "title": p["title"] or f"#{p['id']}", "image": p["image"], "tags": p["tags"],
            "status": p["status"], "status_label": STATUS_LABELS.get(p["status"], p["status"]),
        } for p in db.list_posts()]}

    return app
