"""Painel local: fila de artes, sugestão de tags, aprovação/agendamento, sites e histórico."""

from __future__ import annotations

import json
import uuid
from urllib.parse import quote
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from boorubot.config import ALL_SITES
from boorubot.db import DB, IMAGES_DIR
from boorubot.models import IMAGE_EXTS, RATINGS
from boorubot.worker import Worker

HERE = Path(__file__).parent

SITE_LABELS = {
    "danbooru": "Danbooru",
    "gelbooru": "Gelbooru",
    "paheal": "rule34.paheal",
    "rule34us": "rule34.us",
    "rule34xxx": "rule34.xxx",
}
RATINGS_PT = {"general": "geral (SFW)", "sensitive": "sensível", "questionable": "questionável (nudez)",
              "explicit": "explícito"}
STATUS_LABELS = {
    "draft": "rascunho", "approved": "agendado", "publishing": "publicando",
    "done": "publicado", "partial": "parcial", "failed": "falhou", "tested": "testado (não enviado)",
    "ok": "publicado", "dry_run": "teste", "skipped": "pulado",
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
        ALL_SITES=ALL_SITES,
    )

    def render(request: Request, name: str, **ctx):
        ctx.update(test_mode=db.test_mode, jobs=worker.pending(), danbooru_env=db.setting("danbooru_env"))
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
        tested = any(p["status"] in ("tested", "done", "partial") for p in db.list_posts())
        steps.append({"label": "Fazer um post de teste (modo teste ligado)", "done": tested, "href": "/guia#teste"})
        steps.append({"label": "Desligar o modo teste para começar a postar", "done": not db.test_mode,
                      "href": "/guia#valendo"})
        if "danbooru" in default_sites():
            steps.append({"label": "Danbooru no ambiente real (não o de teste)",
                          "done": db.setting("danbooru_env") == "live", "href": "/guia#danbooru"})
        return steps

    def post_or_404(post_id: int) -> dict:
        row = db.get_post(post_id)
        if not row:
            raise HTTPException(404, "post não encontrado")
        return row

    # --- fila --------------------------------------------------------------------

    @app.get("/")
    def index(request: Request):
        return render(
            request, "index.html",
            drafts=db.list_posts(("draft",)),
            scheduled=db.list_posts(("approved", "publishing")),
            recent=[p for p in db.list_posts() if p["status"] in ("done", "partial", "failed", "tested")][:12],
            next_slot=db.next_free_slot(),
            steps=setup_steps(),
        )

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
        return render(request, "post.html", post=post_or_404(post_id), pubs=db.publications(post_id), error=erro)

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

        if row["status"] in ("draft", "approved", "tested", "failed", "partial"):
            clean_tags = " ".join(normalize_tags(tags))
            db.update_post(
                post_id, title=title.strip(), tags=clean_tags,
                rating=rating if rating in RATINGS else "general", source=source.strip(),
                commentary_title=commentary_title.strip(), commentary_description=commentary_description.strip(),
                original_character=original_character, sites=[s for s in ALL_SITES if s in sites],
            )

        if action in ("approve", "publish_now"):
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
            db.update_post(post_id, status="approved")
            worker.submit("publish", post_id, f"publicar #{post_id} (agora)")
        elif action == "unapprove":
            db.update_post(post_id, status="draft", scheduled_for=None)
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
    def sites_page(request: Request):
        key = db.setting("danbooru_api_key")
        return render(
            request, "sites.html", sessions=db.sessions(), daily_time=db.setting("daily_time"),
            danbooru_login=db.setting("danbooru_login"),
            danbooru_key_hint=f"••••{key[-4:]}" if key else "",
            default_tags=db.setting("default_tags"), default_sites=default_sites(),
        )

    @app.post("/settings/danbooru")
    def save_danbooru(login: str = Form(""), api_key: str = Form(""), env: str = Form("test")):
        db.set_setting("danbooru_login", login.strip())
        if api_key.strip():  # vazio = mantém a chave salva
            db.set_setting("danbooru_api_key", api_key.strip())
        db.set_setting("danbooru_env", env if env in ("test", "live") else "test")
        worker.submit("session", "danbooru", "testar sessão Danbooru")
        return see_other("/sites#danbooru")

    @app.post("/settings/danbooru/remove")
    def remove_danbooru():
        db.set_setting("danbooru_api_key", "")
        db.set_session("danbooru", False, "chave removida do painel")
        return see_other("/sites#danbooru")

    @app.get("/guia")
    def guide(request: Request):
        return render(request, "guide.html", sessions=db.sessions())

    @app.post("/sites/{name}/{kind}")
    def site_action(name: str, kind: str):
        if name not in ALL_SITES or kind not in ("login", "session"):
            raise HTTPException(404)
        if kind == "login" and name == "danbooru":
            raise HTTPException(400, "Danbooru usa API key no .env, não precisa de login no navegador")
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
        return render(request, "history.html", pubs=db.publications(limit=200))

    @app.get("/status.json")
    def status():
        return {"jobs": worker.pending()}

    return app
