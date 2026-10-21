"""Tela Artes: tudo o que ela subiu, com a situação de cada arte e o estado dela em cada site.

Uma consulta para cada coisa (posts, publicações, métricas, sessões) e um plano do agendador;
nada de consulta por linha.
"""

from __future__ import annotations

from datetime import datetime

from boorubot.config import ALL_SITES, SITE_LABELS
from boorubot.db import DB

FILTERS = {"todas": "Todas", "revisar": "Para revisar", "fila": "Na fila", "publicadas": "Publicadas",
           "falha": "Com falha"}
PAGE = 30
WEEKDAYS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
SITE_COLUMNS = {"danbooru": "Dan", "gelbooru": "Gel", "paheal": "pah", "rule34us": ".us", "rule34xxx": ".xxx"}
# marca de cada site na linha: saiu | vai (vai sair) | login (esperando login) | falhou | fora (fica de fora)
MARK_TEXT = {"saiu": "saiu", "vai": "vai sair", "login": "esperando login", "falhou": "falhou", "fora": "fica de fora"}


def day_short(when: datetime) -> str:
    """"seg 12/10"."""
    return f"{WEEKDAYS[when.weekday()]} {when:%d/%m}"


def site_marks(post: dict, pubs: list[dict], down: set[str], off: set[str]) -> dict[str, dict]:
    """Estado de cada site para esta arte. `pubs`: publicações desta arte, da mais antiga para a mais nova."""
    latest = {p["site"]: p for p in pubs}
    ok = {p["site"] for p in pubs if p["status"] == "ok"}
    marks = {}
    for site in ALL_SITES:
        last = latest.get(site)
        if site in ok:
            mark, why = "saiu", ""
        elif last and last["status"] in ("failed", "uncertain", "refused"):
            mark, why = "falhou", "conferir no site" if last["status"] == "uncertain" else last["message"]
        elif last and last["status"] == "skipped":
            mark, why = "fora", last["message"]
        elif site not in post["sites"]:
            mark, why = "fora", "não marcado nesta arte"
        elif site == "paheal" and post["original_character"]:
            mark, why = "fora", "não aceita personagem original"
        elif site in off:
            mark, why = "fora", "site desligado no calendário"
        elif post["status"] in ("done",):
            mark, why = "fora", ""
        elif site in down:
            mark, why = "login", "pediu login de novo"
        else:
            mark, why = "vai", ""
        marks[site] = {"mark": mark, "text": MARK_TEXT[mark], "why": why}
    return marks


def build(db: DB, filtro: str = "todas", q: str = "", limit: int = PAGE, now: datetime | None = None) -> dict:
    from boorubot.scheduler import plan

    now = now or datetime.now()
    filtro = filtro if filtro in FILTERS else "todas"
    posts = db.list_posts()
    pubs_by_post: dict[int, list[dict]] = {}
    for p in db.all_publications():
        pubs_by_post.setdefault(p["post_id"], []).append(p)
    metrics = db.latest_metrics()
    sessions = db.sessions()
    down = {s for s, sess in sessions.items() if not sess["ok"]}
    off = {s for s in ALL_SITES if db.site_rhythm(s) == "desligado"}
    queue_ids = [p["id"] for p in db.queue()]
    next_when: dict[int, datetime] = {}
    for cells in plan(db, now, 60).values():
        for cell in cells.values():
            if cell.kind == "post" and cell.when:
                pid = cell.post["id"]
                next_when[pid] = min(next_when.get(pid, cell.when), cell.when)
    # repost: a mesma imagem (md5) publicada mais de uma vez
    published_by_md5: dict[str, list[str]] = {}
    for p in posts:
        if p["image_md5"] and p["published_at"]:
            published_by_md5.setdefault(p["image_md5"], []).append(p["published_at"])

    rows = []
    for post in posts:
        pubs = pubs_by_post.get(post["id"], [])
        marks = site_marks(post, pubs, down, off)
        failed = [s for s, m in marks.items() if m["mark"] == "falhou"]
        favs = [metrics[p["id"]]["fav_count"] for p in pubs
                if p["id"] in metrics and metrics[p["id"]]["fav_count"] is not None]
        published = datetime.fromisoformat(post["published_at"]) if post["published_at"] else None
        when = next_when.get(post["id"])
        tags = post["tags"].split()
        detail, detail_tone = f"{len(tags)} tag{'s' if len(tags) != 1 else ''}", ""
        if post["original_character"]:
            detail += " · personagem original"
        if post["status"] == "draft":
            missing = "faltam as tags" if not tags else "falta o rating" if not post["rating"] else ""
            if post["suggest_state"] in ("pendente", "analisando"):
                detail = "a Mochi está olhando"
            situation = ("precisa de você", "alerta") if missing else ("para revisar", "rascunho")
            when_text, when_tone = (missing, "warn") if missing else ("falta aprovar", "")
            group = "revisar"
        elif post["status"] in ("approved", "publishing"):
            today = when is not None and when.date() == now.date()
            situation = ("sai hoje", "agendado") if today else ("na fila", "agendado")
            when_text = (f"hoje {when:%H:%M}" if today else f"{day_short(when)} · {when:%H:%M}") if when else "sem dia ainda"
            when_tone = "strong" if today else ""
            if post["id"] in queue_ids:
                detail = f"{queue_ids.index(post['id']) + 1}º da fila"
            group = "fila"
        else:
            group = "publicadas"
            situation = ("publicada", "publicado")
            when_text, when_tone = (f"saiu {day_short(published)}" if published else "não saiu"), ""
        if failed and post["status"] != "draft":
            n = len(failed)
            situation = (f"falhou em {n} site{'s' if n != 1 else ''}", "falhou")
            last_fail = next((p for p in reversed(pubs) if p["status"] == "failed" and p["message"]), None)
            if last_fail:
                detail, detail_tone = last_fail["message"], "bad"
        times = published_by_md5.get(post["image_md5"], [])
        if len(times) > 1 and post["published_at"]:
            last = datetime.fromisoformat(max(times))
            detail = f"postada {len(times)} vezes · última em {last:%d/%m}"
        rows.append({
            "post": post, "marks": marks, "situation": situation, "when": when_text, "when_tone": when_tone,
            "detail": detail, "detail_tone": detail_tone, "favs": sum(favs) if favs else None,
            "group": group, "failed": bool(failed and post["status"] != "draft"),
            "sort": ({"revisar": 0, "fila": 2, "publicadas": 3}[group] - (2 if failed and group != "revisar" else 0),
                     -(published.timestamp()) if published else 0,
                     (when or now).timestamp() if group == "fila" else 0, -post["id"]),
        })

    def matches(r: dict) -> bool:
        if not q:
            return True
        needle = q.strip().lower()
        return needle in r["post"]["title"].lower() or any(needle in t for t in r["post"]["tags"].lower().split())

    found = [r for r in rows if matches(r)]
    counts = {
        "todas": len(found),
        "revisar": sum(r["group"] == "revisar" for r in found),
        "fila": sum(r["group"] == "fila" for r in found),
        "publicadas": sum(r["group"] == "publicadas" for r in found),
        "falha": sum(r["failed"] for r in found),
    }
    if filtro == "falha":
        shown = [r for r in found if r["failed"]]
    elif filtro != "todas":
        shown = [r for r in found if r["group"] == filtro]
    else:
        shown = found
    shown.sort(key=lambda r: r["sort"])
    top = max((r["favs"] or 0 for r in shown), default=0)
    return {
        "filtro": filtro, "q": q, "filters": FILTERS, "counts": counts, "total": len(shown),
        "rows": shown[:limit], "limit": limit, "more": len(shown) > limit, "top_favs": top,
        "columns": SITE_COLUMNS, "marks": MARK_TEXT,
    }
