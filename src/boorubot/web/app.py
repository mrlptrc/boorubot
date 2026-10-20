"""Painel local: fila de artes, sugestão de tags, aprovação/agendamento, sites e histórico."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from boorubot import startup
from boorubot.config import ALL_SITES, SITE_LABELS, build_site
from boorubot.sites.forms import FORM_SITES
from boorubot.db import DB, IMAGES_DIR
from boorubot.models import IMAGE_EXTS, RATINGS
from boorubot.sites.danbooru import CONTRIBUTOR_LEVEL
from boorubot.tag_api import DanbooruTagAPI
from boorubot.worker import Worker

HERE = Path(__file__).parent

BROWSER_MODE_LABELS = {
    "headless": "invisível (sem janela)",
    "hidden": "janela escondida (aparece só se pedir captcha)",
    "visible": "janela visível",
}
RATINGS_PT = {"general": "geral (SFW)", "sensitive": "sensível", "questionable": "questionável (nudez)",
              "explicit": "explícito"}
RATING_SHORT = {"general": "geral", "sensitive": "sensível", "questionable": "questionável", "explicit": "explícito"}
STATUS_LABELS = {
    "draft": "rascunho", "approved": "na fila", "publishing": "publicando",
    "done": "publicado", "partial": "parcial", "failed": "falhou",
    "ok": "publicado", "skipped": "pulado",
    "uncertain": "conferir no site", "not_published": "não saiu (conferido)",
    "site_down": "site pausado (login/captcha/fora do ar)",
    "dry_run": "simulado", "test_upload": "enviado ao testbooru",
    "deleted": "apagada",
}


def normalize_tags(raw: str) -> list[str]:
    """Aceita "long_hair 1girl" (padrão booru) ou "long hair, 1girl" (vírgulas); remove repetidas."""
    items = raw.split(",") if "," in raw else raw.split()
    tags = ("_".join(t.strip().lower().split()) for t in items)
    return list(dict.fromkeys(t for t in tags if t))


EDITABLE = ("draft", "approved", "failed", "partial")
TEXT_FIELDS = ("title", "source", "commentary_title", "commentary_description")


def clean_post_fields(row: dict, data: dict) -> dict:
    """Valida e normaliza só os campos presentes em `data` (form inteiro ou salvamento parcial)."""
    from boorubot.suggest import accept_weak, oc_fields

    out = {k: str(data[k]).strip() for k in TEXT_FIELDS if k in data}
    if "tags" in data:
        tag_list = normalize_tags(str(data["tags"]))
        out.update(tags=" ".join(tag_list), **accept_weak(row, tag_list))
    if "rating" in data:
        out["rating"] = data["rating"] if data["rating"] in RATINGS else ""
    if "original_character" in data:
        oc = data["original_character"] in (True, "true", "1", 1, "on")
        out.update(original_character=oc, **oc_fields(row, oc))
    if "sites" in data:
        sites = data["sites"] if isinstance(data["sites"], list) else [data["sites"]]
        out["sites"] = [s for s in ALL_SITES if s in sites]
    return out


WEEKDAYS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


def when_label(when: datetime | None) -> str:
    """"seg 19/10 às 18:00"."""
    return f"{WEEKDAYS[when.weekday()]} {when:%d/%m} às {when:%H:%M}" if when else ""


def short_when(when: datetime | None, now: datetime | None = None) -> str:
    """"hoje 18:02", "ontem 18:02" ou "seg 12/10"."""
    if not when:
        return ""
    now = now or datetime.now()
    days = (now.date() - when.date()).days
    if days == 0:
        return f"hoje {when:%H:%M}"
    if days == 1:
        return f"ontem {when:%H:%M}"
    return f"{WEEKDAYS[when.weekday()]} {when:%d/%m}"


def image_info(filename: str) -> str:
    """"PNG · 1200 × 1600 · 840 KB" (vazio se a imagem sumiu do disco)."""
    from PIL import Image

    path = IMAGES_DIR / filename
    try:
        with Image.open(path) as im:
            w, h = im.size
            kind = im.format or path.suffix.lstrip(".").upper()
        return f"{kind} · {w} × {h} · {max(1, round(path.stat().st_size / 1024))} KB"
    except OSError:
        return ""


ICONS_DIR = HERE / "static" / "icons"


@lru_cache(maxsize=None)
def icon_svg(name: str, size: int = 20) -> Markup:
    """SVG do ícone inline (herda currentColor); decorativo: o rótulo vem do texto ao lado ou do aria-label."""
    if not name.replace("-", "").isalnum():
        raise ValueError(f"ícone inválido: {name}")
    svg = (ICONS_DIR / f"{name}.svg").read_text(encoding="utf-8")
    svg = svg.replace('width="20" height="20"', f'width="{size}" height="{size}" aria-hidden="true" focusable="false"', 1)
    return Markup(svg)


def dict_without(d: dict, *keys: str) -> dict:
    return {k: v for k, v in (d or {}).items() if k not in keys}


def see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def create_app(db: DB | None = None, worker: Worker | None = None, start_worker: bool = True,
               tag_api: DanbooruTagAPI | None = None) -> FastAPI:
    db = db or DB()
    worker = worker or Worker(db)
    tag_api = tag_api or DanbooruTagAPI()
    if start_worker:
        worker.start()

    app = FastAPI(title="boorubot")
    app.state.db, app.state.worker, app.state.tag_api = db, worker, tag_api
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(
        SITE_LABELS=SITE_LABELS, STATUS_LABELS=STATUS_LABELS, RATINGS=RATINGS, RATINGS_PT=RATINGS_PT,
        ALL_SITES=ALL_SITES, BROWSER_MODE_LABELS=BROWSER_MODE_LABELS,
    )
    templates.env.globals["icon_svg"] = icon_svg
    templates.env.filters["quando"] = when_label
    templates.env.filters["dict_without"] = dict_without

    def danbooru_mode() -> str:
        """Como o post entra no Danbooru, pelo nível REAL lido da conta (nunca presumido).
        unknown | direct (Contributor, sem moderação) | forced (Contributor pedindo moderação) | moderated"""
        level = db.setting("danbooru_level")
        if not level.isdigit() or not db.sessions().get("danbooru", {}).get("ok"):
            return "unknown"
        if int(level) >= CONTRIBUTOR_LEVEL:
            return "forced" if db.setting("danbooru_force_pending") == "1" else "direct"
        return "moderated"

    def shell_state() -> dict:
        """Dados da casca (menu): contagem "para revisar", ponto de login em Sites e o card de estado do robô."""
        review = len(db.list_posts(("draft", "failed", "partial")))
        login_down = any(not s.get("ok") for s in db.sessions().values())
        last = db.setting("last_published_at")
        last_label = short_when(datetime.fromisoformat(last)) if last else ""
        if db.test_mode:
            robot = {"tone": "warn", "title": "Modo teste ligado", "text": "a fila está pausada", "href": "/ajustes"}
        elif login_down:
            robot = {"tone": "warn", "title": "Um site pediu login", "text": "os outros continuam na fila", "href": "/sites"}
        else:
            robot = {"tone": "ok", "title": "Ligado e na fila",
                     "text": f"último post {last_label}" if last_label else "nenhum post ainda", "href": "/calendario"}
        return {"review": review, "login_down": login_down, "robot": robot}

    def render(request: Request, name: str, **ctx):
        ctx.update(test_mode=db.test_mode, jobs=worker.pending(), shell=shell_state(),
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

    def approval_errors(row: dict) -> list[str]:
        """O que falta para aprovar, testar ou publicar (mesma lista na tela e no backend)."""
        errors = []
        if not row["tags"]:
            errors.append("sem tags")
        if row["rating"] not in RATINGS:
            errors.append("escolha o rating")
        if not row["sites"]:
            errors.append("nenhum site marcado")
        return errors

    def after_approve_url() -> str:
        """Aprovou: vai direto para o próximo rascunho (o mais antigo); sem rascunhos, volta ao Início."""
        drafts = sorted(db.list_posts(("draft",)), key=lambda p: (p["created_at"], p["id"]))
        if not drafts:
            return "/?aviso=" + quote("Aprovada! Não falta nenhuma arte para revisar.")
        left = len(drafts)
        return f"/posts/{drafts[0]['id']}?aviso=" + quote(f"Aprovada! Falta{'m' if left > 1 else ''} {left} para revisar.")

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

    def save_upload(name: str, data: bytes, image_md5: str = "") -> dict:
        """Uma arte nova: grava a imagem, cria o rascunho e põe na fila da IA."""
        ext = Path(name).suffix.lower()
        if ext not in IMAGE_EXTS:
            raise ValueError(f"formato não suportado: {ext or '?'} (use {', '.join(IMAGE_EXTS)})")
        filename = f"{uuid.uuid4().hex}{ext}"
        (IMAGES_DIR / filename).write_bytes(data)
        image_md5 = image_md5 or hashlib.md5(data, usedforsecurity=False).hexdigest()
        # rating vazio = "não escolhido": nunca sai um rating que ninguém conferiu
        post_id = db.create_post(filename, title=Path(name).stem, sites=default_sites(), rating="",
                                 tags=" ".join(normalize_tags(db.setting("default_tags"))), image_md5=image_md5)
        worker.submit_suggest(post_id)  # fila própria da IA: não atrasa nenhum envio
        return {"id": post_id, "title": Path(name).stem, "image": filename}

    @app.post("/posts")
    async def create_post(request: Request, image: list[UploadFile] = File(...), repetida: str = Form("")):
        """Uma ou várias artes. A janela "Novas artes" pede JSON; o form sem JavaScript recebe um redirect."""
        wants_json = "application/json" in request.headers.get("accept", "")
        created, errors, duplicates = [], [], []
        for upload in image:
            name = upload.filename or ""
            try:
                data = await upload.read()
                ext = Path(name).suffix.lower()
                if ext not in IMAGE_EXTS:
                    raise ValueError(f"formato não suportado: {ext or '?'} (use {', '.join(IMAGE_EXTS)})")
                image_md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
                existing = db.find_image_duplicate(image_md5) if wants_json and repetida != "1" else None
                if existing:
                    published_at = existing["published_at"] or ""
                    if published_at:
                        published_label = datetime.fromisoformat(published_at).strftime("%d/%m")
                        where = "publicada"
                    else:
                        published_label = ""
                        where = {"draft": "em revisão", "approved": "na fila", "failed": "com falha",
                                 "partial": "com falha"}.get(existing["status"], "já existe")
                    duplicates.append({"name": name, "existing": {
                        "id": existing["id"], "published_at": published_at,
                        "published_label": published_label, "where": where,
                    }})
                else:
                    created.append(save_upload(name, data, image_md5))
            except ValueError as exc:
                errors.append({"name": name, "error": str(exc)})
        if wants_json:
            return JSONResponse({"posts": created, "errors": errors, "duplicates": duplicates},
                                status_code=200 if created or duplicates or not errors else 400)
        if not created:
            raise HTTPException(400, errors[0]["error"] if errors else "nenhum arquivo")
        return see_other(f"/posts/{created[0]['id']}" if len(created) == 1 else "/#revisar")

    @app.post("/posts/{post_id}/imagem")
    async def replace_image(post_id: int, image: UploadFile = File(...)):
        """Trocar imagem: mantém tudo que ela escreveu e pede para a IA olhar de novo."""
        row = post_or_404(post_id)
        ext = Path(image.filename or "").suffix.lower()
        if row["status"] not in EDITABLE:
            return see_other(f"/posts/{post_id}?erro=" + quote("esta arte não pode mais ser editada"))
        if ext not in IMAGE_EXTS:
            return see_other(f"/posts/{post_id}?erro=" + quote(f"formato não suportado: {ext or '?'}"))
        filename = f"{uuid.uuid4().hex}{ext}"
        (IMAGES_DIR / filename).write_bytes(await image.read())
        db.update_post(post_id, image=filename)
        (IMAGES_DIR / row["image"]).unlink(missing_ok=True)
        worker.submit_suggest(post_id)
        return see_other(f"/posts/{post_id}")

    @app.get("/posts/{post_id}")
    def edit_post(request: Request, post_id: int, erro: str = ""):
        from boorubot.scheduler import plan
        from boorubot.suggest import mochi_note

        row = post_or_404(post_id)
        queue_ids = [p["id"] for p in db.queue()]
        today = datetime.now().date()
        plans = plan(db, days=60) if post_id in queue_ids else {}
        pins, done = db.post_pins(post_id), db.published_sites(post_id)
        site_days = []
        for site in row["sites"]:
            when = next((c.when for c in plans.get(site, {}).values() if c.kind == "post" and c.post["id"] == post_id),
                        None)
            site_days.append({"site": site, "label": SITE_LABELS[site], "when": when, "pinned": pins.get(site),
                              "done": site in done, "off": db.site_rhythm(site) == "desligado"})
        planned = [s["when"] for s in site_days if s["when"]]
        sessions = db.sessions()
        last_test = {}
        for t in db.test_runs(post_id):  # do mais novo para o mais antigo: fica o último de cada site
            last_test.setdefault(t["site"], t)
        days_by_site = {s["site"]: s for s in site_days}
        site_rows = [{
            "site": s, "label": SITE_LABELS[s], "checked": s in row["sites"],
            "off": db.site_rhythm(s) == "desligado", "done": s in done,
            "when": days_by_site.get(s, {}).get("when"), "pinned": pins.get(s),
            # None = nunca testado; False = sessão caiu (pediu login de novo)
            "session_ok": bool(sessions[s]["ok"]) if s in sessions else None,
            "test": last_test.get(s),
        } for s in ALL_SITES]
        approve_when = None
        if row["status"] == "draft" and row["sites"]:
            from boorubot.scheduler import next_free_day

            approve_when = next_free_day(db, row["sites"])
        return render(
            request, "post.html", post=row, site_rows=site_rows, approve_when=approve_when,
            image_info=image_info(row["image"]), pubs=db.publications(post_id), tests=db.test_runs(post_id), error=erro,
            default_tags=normalize_tags(db.setting("default_tags")), mochi=mochi_note(row),
            approval_errors=approval_errors(row),
            metrics=db.latest_metrics(),
            has_test_account=bool(db.setting("danbooru_test_api_key")),
            projected=min(planned) if planned else None, site_days=site_days, today=today.isoformat(),
            uncertain=[(s, SITE_LABELS[s]) for s in db.uncertain_sites(post_id)],
            queue_pos=queue_ids.index(post_id) + 1 if post_id in queue_ids else None, queue_len=len(queue_ids),
            published_today=any(db.site_last_published(s) and db.site_last_published(s).date() == today
                                for s in row["sites"]),
        )

    def pin_post(post_id: int, site: str, day: str, add_site: bool = False) -> str | None:
        """Fixa a arte num dia, num site. Devolve um erro em texto ou None."""
        from datetime import date as _date

        row = db.get_post(post_id)
        if not row or row["status"] != "approved":
            return "só dá para fixar o dia de uma arte aprovada (na fila)"
        if site not in ALL_SITES or db.site_rhythm(site) == "desligado":
            return "site desligado ou desconhecido"
        try:
            when = _date.fromisoformat(day)
        except ValueError:
            return "data inválida"
        if when < datetime.now().date():
            return "não dá para fixar num dia que já passou"
        if site in db.published_sites(post_id):
            return "essa arte já saiu nesse site"
        if site not in row["sites"]:
            if not add_site:
                return "essa arte não está marcada para esse site"
            db.update_post(post_id, sites=[s for s in ALL_SITES if s in row["sites"] or s == site])
        db.pin(post_id, site, when)
        return None

    @app.post("/posts/{post_id}/desfazer")
    def undo_delete(post_id: int):
        if not db.restore_post(post_id):
            return see_other("/?aviso=" + quote("Não deu para desfazer: a arte já foi removida de vez."))
        return see_other(f"/posts/{post_id}?aviso=" + quote("Arte de volta."))

    @app.post("/posts/{post_id}/conferir")
    def check_uncertain(post_id: int, site: str = Form(...), action: str = Form(...), url: str = Form("")):
        """Envio incerto: uma pessoa olhou no site e diz se saiu. Só ela decide reenviar (sem duplicata)."""
        from boorubot.worker import refresh_post_status

        row = post_or_404(post_id)
        if site not in db.uncertain_sites(post_id):
            return see_other(f"/posts/{post_id}")
        if action == "saiu":
            link = url.strip() if url.strip().startswith("http") else ""
            db.add_publication(post_id, site, "ok", link, "conferido: saiu no site")
            db.mark_post_published_at(post_id)
        elif action == "nao_saiu":
            # não conta como falha: volta para a fila daquele site
            db.add_publication(post_id, site, "not_published", message="conferido: não saiu; volta para a fila")
            if row["status"] != "approved":
                db.update_post(post_id, status="approved")
        refresh_post_status(db, post_id)
        return see_other(f"/posts/{post_id}#conferir")

    @app.post("/posts/{post_id}/dia")
    def post_pin(post_id: int, site: str = Form(...), day: str = Form(""), action: str = Form("pin")):
        if action == "unpin":
            db.unpin(post_id, site)
            return see_other(f"/posts/{post_id}#dias")
        error = pin_post(post_id, site, day)
        return see_other(f"/posts/{post_id}" + (f"?erro={quote(error)}" if error else "") + "#dias")

    @app.post("/calendario/fixar")
    def calendar_pin(post_id: int = Form(...), site: str = Form(...), day: str = Form(...),
                     add_site: bool = Form(False)):
        error = pin_post(post_id, site, day, add_site)
        return JSONResponse({"ok": error is None, "error": error}, status_code=200 if error is None else 400)

    @app.post("/calendario/desafixar")
    def calendar_unpin(post_id: int = Form(...), site: str = Form(...), semana: int = Form(0)):
        db.unpin(post_id, site)
        return see_other(f"/calendario?semana={semana}")

    @app.post("/posts/{post_id}")
    def save_post(
        post_id: int,
        action: str = Form("save"),
        title: str = Form(""),
        tags: str = Form(""),
        rating: str = Form(""),
        source: str = Form(""),
        commentary_title: str = Form(""),
        commentary_description: str = Form(""),
        original_character: bool = Form(False),
        sites: list[str] = Form([]),
    ):
        row = post_or_404(post_id)
        if action == "delete":  # esconde; o worker remove de vez depois de 24 h
            db.delete_post(post_id)
            return see_other(f"/?aviso={quote('Arte apagada')}&desfazer={post_id}")

        if row["status"] in EDITABLE:
            db.update_post(post_id, **clean_post_fields(row, {
                "title": title, "tags": tags, "rating": rating, "source": source,
                "commentary_title": commentary_title, "commentary_description": commentary_description,
                "original_character": original_character, "sites": sites,
            }))

        if action == "publish_now" and db.test_mode:
            return see_other(f"/posts/{post_id}?erro=" + quote(
                "modo teste ligado: nada é publicado. Use \"Testar este post\" ou desligue o modo teste em Sites."))
        if action in ("approve", "publish_now", "test"):
            errors = approval_errors(db.get_post(post_id))
            if errors:
                return see_other(f"/posts/{post_id}?erro=" + quote("não dá para aprovar: " + ", ".join(errors)))
        if action == "approve":
            db.approve(post_id)
            return see_other(after_approve_url())
        elif action == "publish_now":  # clique explícito em produção vale como confirmação
            db.update_post(post_id, approved_in_test=0)
            worker.submit("publish", post_id, f"publicar #{post_id} (agora)")
        elif action == "test":  # não muda nada no post: só grava em test_runs
            worker.submit("test", post_id, f"testar #{post_id}")
        elif action == "unapprove":
            db.update_post(post_id, status="draft", queue_pos=None, approved_in_test=0)
        elif action == "confirm" and row["status"] == "approved":
            db.confirm_for_production(post_id)
        elif action in ("first", "last") and row["status"] == "approved":
            db.move_in_queue(post_id, action)
        return see_other(f"/posts/{post_id}")

    @app.patch("/posts/{post_id}")
    async def save_fields(post_id: int, request: Request):
        """Salvamento parcial (o editor salva sozinho): recebe só os campos que mudaram, em JSON."""
        row = post_or_404(post_id)
        if row["status"] not in EDITABLE:
            return JSONResponse({"ok": False, "error": "esta arte não pode mais ser editada"}, status_code=409)
        try:
            data = await request.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return JSONResponse({"ok": False, "error": "corpo inválido"}, status_code=400)
        fields = clean_post_fields(row, data)
        if fields:
            db.update_post(post_id, **fields)
        row = db.get_post(post_id)
        return {"ok": True, "saved_at": datetime.now().strftime("%H:%M"), "approval_errors": approval_errors(row),
                "post": {k: row[k] for k in ("title", "tags", "rating", "sites", "original_character", "source")}}

    @app.post("/fila/teste")
    def confirm_all(action: str = Form(...)):
        """Artes aprovadas no modo teste: confirmar todas para produção ou voltar todas para rascunho."""
        for post in db.needs_confirmation():
            if action == "confirm":
                db.confirm_for_production(post["id"])
            elif action == "draft":
                db.update_post(post["id"], status="draft", queue_pos=None, approved_in_test=0)
        return see_other("/")

    def suggest_status(row: dict) -> dict:
        from boorubot.suggest import mochi_note
        from boorubot.tagger import tagger

        return {
            "id": row["id"], "state": row["suggest_state"], "error": row["suggest_error"],
            "waiting": worker.suggest_position(row["id"]),
            "model_ready": (worker.tagger or tagger).model_ready,
            "tags": row["tag_list"], "scores": row["tag_scores"], "weak": row["weak_tags"],
            "original_character": row["original_character"], "mochi": mochi_note(row),
            "rating_suggestion": row["rating_suggestion"], "rating_confidence": row["rating_confidence"],
            "rating_label": RATING_SHORT.get(row["rating_suggestion"], ""),
        }

    @app.post("/posts/{post_id}/suggest")
    def suggest(post_id: int):
        """Sugerir de novo: entra na fila da IA (a análise roda no worker, não na requisição)."""
        post_or_404(post_id)
        worker.submit_suggest(post_id)
        return suggest_status(db.get_post(post_id))

    @app.get("/suggest/status.json")
    def suggest_states(ids: str = ""):
        """Janela "Novas artes": estado da IA para várias artes de uma vez."""
        from boorubot.tagger import tagger

        rows = [db.get_post(int(i)) for i in ids.split(",") if i.strip().isdigit()]
        return {"model_ready": (worker.tagger or tagger).model_ready,
                "posts": [suggest_status(r) for r in rows if r]}

    @app.get("/posts/{post_id}/suggest/status")
    def suggest_state(post_id: int):
        return suggest_status(post_or_404(post_id))

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
            rhythms={s: db.site_rhythm(s) for s in ALL_SITES}, delays={s: db.site_delay(s) for s in ALL_SITES},
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

    @app.get("/_ui")
    def ui_catalog(request: Request):
        """Catálogo vivo dos componentes (só para desenvolvimento: modo teste ou BOORUBOT_DEBUG=1)."""
        if not (db.test_mode or os.environ.get("BOORUBOT_DEBUG") == "1"):
            raise HTTPException(404)
        return render(request, "ui_catalog.html")

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
        db.set_setting(f"blocked_{name}", "")  # uma pessoa pediu: libera site bloqueado por recusa da conta
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

    # Artes e Ajustes ganham tela própria nas etapas 13 e 14 do editor v2; até lá o menu leva ao lugar de hoje
    @app.get("/artes")
    def arts_page():
        return see_other("/historico")

    @app.get("/ajustes")
    def settings_page():
        return see_other("/sites#config")

    @app.get("/historico")
    def history(request: Request):
        pubs = [p for p in db.publications(limit=200) if p["post_status"] != "deleted"]
        return render(request, "history.html", pubs=pubs, metrics=db.latest_metrics(),
                      metrics_last_run=db.setting("metrics_last_run"))

    @app.get("/calendario")
    def calendar_page(request: Request, semana: int = 0, erro: str = ""):
        from boorubot import calendar_view

        return render(request, "calendar.html", c=calendar_view.build(db, week=semana), error=erro)

    @app.post("/settings/ritmo")
    async def save_rhythm(request: Request):
        """Ritmo e atraso por site. Só muda o que vem a seguir: o que já saiu fica como está."""
        from boorubot.db import MAX_DELAY_DAYS, RHYTHMS

        form = await request.form()
        for site in ALL_SITES:
            rhythm = str(form.get(f"rhythm_{site}", "diario"))
            delay = str(form.get(f"delay_{site}", "0"))
            db.set_setting(f"rhythm_{site}", rhythm if rhythm in RHYTHMS else "diario")
            db.set_setting(f"delay_{site}", delay if delay.isdigit() and int(delay) <= MAX_DELAY_DAYS else "0")
        return see_other("/sites#ritmo")

    @app.get("/metricas")
    def metrics_page(request: Request, periodo: str = "30"):
        from boorubot import insights

        return render(request, "metrics.html", m=insights.build(db, periodo))

    @app.post("/metricas/atualizar")
    def refresh_metrics():
        worker.submit("metrics", "danbooru", "atualizar métricas")
        return see_other("/metricas")

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

    @app.get("/danbooru/autocomplete.json")
    def danbooru_autocomplete(q: str = ""):
        """Proxy pequeno para o autocomplete público; sem credenciais também funciona."""
        q = q.strip()[:80]
        if len(q) < 2:
            return {"available": True, "suggestions": []}
        try:
            suggestions = tag_api.autocomplete(q, db.setting("danbooru_login"), db.setting("danbooru_api_key"))
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {"available": False, "suggestions": []}
        return {"available": True, "suggestions": suggestions}

    @app.post("/danbooru/tags/check")
    async def check_danbooru_tags(request: Request):
        """Confere grafia em lote. Erro de rede é informativo e nunca bloqueia o editor."""
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"available": False, "unknown": []}, status_code=400)
        tags = body.get("tags") if isinstance(body, dict) else None
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            return JSONResponse({"available": False, "unknown": []}, status_code=400)
        try:
            unknown = tag_api.unknown(tags[:200], db.setting("danbooru_login"), db.setting("danbooru_api_key"))
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {"available": False, "unknown": []}
        return {"available": True, "unknown": unknown}

    return app
