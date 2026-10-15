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


def build(db: DB, now: datetime | None = None, week: int = 0, days: int = 7) -> dict:
    now = now or datetime.now()
    today = now.date()
    week = max(0, min(week, MAX_WEEKS))
    start = today + timedelta(days=7 * week)
    dates = [start + timedelta(days=i) for i in range(days)]
    plans = plan(db, now, 7 * week + days)
    sessions = db.sessions()

    rows = []
    for site in ALL_SITES:
        down = bool(sessions.get(site)) and not sessions[site]["ok"]
        cells = []
        for d in dates:
            cell = plans[site].get(d)
            kind = cell.kind if cell else "empty"
            if kind == "post" and down:
                kind = "waiting"
            cells.append({"kind": kind, "post": cell.post if cell else None, "pinned": bool(cell and cell.pinned),
                          "day": d.isoformat(), "today": d == today})
        rows.append({"site": site, "label": SITE_LABELS[site], "rhythm": rhythm_label(db, site),
                     "session_down": down, "off": db.site_rhythm(site) == "desligado", "cells": cells})

    scheduled = {c.post["id"] for cells in plans.values() for d, c in cells.items()
                 if c.kind == "post" and d <= dates[-1]}
    beyond = [p for p in db.queue() if p["id"] not in scheduled]
    busy_days = {d for cells in plans.values() for d, c in cells.items() if c.kind in ("post", "published")}
    empty_days = [d for d in dates if d not in busy_days]
    drafts = db.list_posts(("draft",))

    return {
        "week": week, "max_weeks": MAX_WEEKS,
        "range_label": f"{dates[0].day}/{dates[0].month} a {dates[-1].day}/{dates[-1].month}",
        "dates": [{"date": d, "iso": d.isoformat(), "label": day_label(d, today).lower(), "today": d == today}
                  for d in dates],
        "rows": rows,
        "beyond": beyond,
        "empty_days": [f"{WEEKDAYS[d.weekday()]} {d.day}" for d in empty_days],
        "suggestion": drafts[0] if drafts else None,
    }
