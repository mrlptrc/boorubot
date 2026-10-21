"""Calendário: semana × site, a partir da mesma simulação que o worker usa (scheduler.plan).

Cada linha é a fila daquele site. Arrastar uma arte para outro dia fixa a arte nesse dia nesse
site (db.pin); as outras artes do site se reorganizam em volta.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from boorubot.config import ALL_SITES, SITE_LABELS
from boorubot.dashboard import WEEKDAYS, day_label
from boorubot.db import DB
from boorubot.scheduler import plan

RHYTHM_LABELS = {"diario": "1 por dia", "dia_sim_dia_nao": "dia sim, dia não", "desligado": "desligado"}
MAX_WEEKS = 8


def delay_label(delay: int) -> str:
    return {0: "mesmo dia", 1: "1 dia depois"}.get(delay, f"{delay} dias depois")


def rhythm_label(db: DB, site: str) -> str:
    rhythm, delay = db.site_rhythm(site), db.site_delay(site)
    if rhythm == "desligado":
        return "desligado"
    parts = [RHYTHM_LABELS[rhythm]]
    parts.append(delay_label(delay) if delay else db.setting("daily_time"))
    if site == "paheal":
        parts.append("pula OC")
    return " · ".join(parts)


MONTHS = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
          "novembro", "dezembro")


def range_label(first, last) -> str:
    """"16 a 22 de outubro" ou "28 de outubro a 3 de novembro"."""
    if first.month == last.month:
        return f"{first.day} a {last.day} de {MONTHS[last.month - 1]}"
    return f"{first.day} de {MONTHS[first.month - 1]} a {last.day} de {MONTHS[last.month - 1]}"


def build(db: DB, now: datetime | None = None, week: int = 0, days: int = 7) -> dict:
    """Semana `week` (0 = a partir de hoje; negativas = o que já saiu; positivas = a previsão)."""
    now = now or datetime.now()
    today = now.date()
    week = max(-MAX_WEEKS, min(week, MAX_WEEKS))
    start = today + timedelta(days=7 * week)
    dates = [start + timedelta(days=i) for i in range(days)]
    horizon = (dates[-1] - today).days + 1
    plans = plan(db, now, horizon) if horizon > 0 else {s: {} for s in ALL_SITES}
    sessions = db.sessions()
    queue = db.queue()
    position = {p["id"]: i + 1 for i, p in enumerate(queue)}
    # dias passados: o que saiu de verdade em cada site (publications.ok)
    posts = {p["id"]: p for p in db.list_posts()}
    went_out: dict[tuple[str, object], dict] = {}
    if dates[0] < today:
        for pub in db.all_publications():
            when = datetime.fromisoformat(pub["created_at"]).date()
            if pub["status"] == "ok" and dates[0] <= when < today and pub["post_id"] in posts:
                went_out[(pub["site"], when)] = posts[pub["post_id"]]

    rows = []
    for site in ALL_SITES:
        down = bool(sessions.get(site)) and not sessions[site]["ok"]
        cells, waiting_seen = [], False
        for d in dates:
            if d < today:
                post = went_out.get((site, d))
                cells.append({"kind": "published" if post else "past", "post": post, "pinned": False,
                              "day": d.isoformat(), "today": False, "past": True})
                continue
            cell = plans[site].get(d)
            kind = cell.kind if cell else "empty"
            if kind == "post" and down:
                # o primeiro envio espera o login; os seguintes dependem dele
                kind = "blocked" if waiting_seen else "waiting"
                waiting_seen = True
            cells.append({"kind": kind, "post": cell.post if cell else None, "pinned": bool(cell and cell.pinned),
                          "day": d.isoformat(), "today": d == today, "past": False})
        # destino do arrasto: a posição da arte daquele dia; num dia sem arte, a da próxima arte da linha
        after = len(queue) + 1
        for c in reversed(cells):
            if c["post"] and c["post"]["id"] in position:
                after = position[c["post"]["id"]]
            c["position"] = after
        rows.append({"site": site, "label": SITE_LABELS[site], "rhythm": rhythm_label(db, site),
                     "rhythm_key": db.site_rhythm(site), "delay": db.site_delay(site),
                     "session_down": down, "off": db.site_rhythm(site) == "desligado", "cells": cells})

    scheduled = {c.post["id"] for cells in plans.values() for d, c in cells.items()
                 if c.kind == "post" and d <= dates[-1]}
    beyond = [p for p in queue if p["id"] not in scheduled]
    busy_days = {d for cells in plans.values() for d, c in cells.items() if c.kind in ("post", "published")}
    empty_days = [d for d in dates if d >= today and d not in busy_days]
    drafts = db.list_posts(("draft",))

    return {
        "week": week, "max_weeks": MAX_WEEKS,
        "range_label": range_label(dates[0], dates[-1]),
        "dates": [{"date": d, "iso": d.isoformat(), "label": day_label(d, today).lower(), "today": d == today,
                   "weekday": WEEKDAYS[d.weekday()], "day": d.day} for d in dates],
        "rows": rows,
        "beyond": beyond,
        "empty_days": [f"{WEEKDAYS[d.weekday()]} {d.day}" for d in empty_days],
        "suggestion": drafts[0] if drafts else None,
        "position": position,
    }
