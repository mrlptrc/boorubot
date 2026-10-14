"""Calendário: previsão semana × site (só leitura; quem publica é o worker).

A fila principal (db.projected_times) dá o "dia 0" de cada arte. Cada site percorre a
mesma ordem no seu ritmo: sites no padrão (diário, sem atraso) saem no dia 0; os demais são
simulados com o pacer do site (horário diário, 12 h de intervalo, 1 por dia ou dia sim/dia não).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from boorubot.config import ALL_SITES, SITE_LABELS
from boorubot.dashboard import WEEKDAYS, day_label
from boorubot.db import DB

RHYTHM_LABELS = {"diario": "1 por dia", "dia_sim_dia_nao": "dia sim, dia não", "desligado": "desligado"}
DELAY_LABELS = {0: "mesmo dia", 1: "1 dia depois"}


def delay_label(delay: int) -> str:
    return DELAY_LABELS.get(delay, f"{delay} dias depois")


def rhythm_label(db: DB, site: str) -> str:
    rhythm, delay = db.site_rhythm(site), db.site_delay(site)
    if rhythm == "desligado":
        return "desligado"
    parts = [RHYTHM_LABELS[rhythm]]
    if delay:
        parts.append(delay_label(delay))
    elif rhythm == "diario":
        parts.append(db.setting("daily_time"))
    if site == "paheal":
        parts.append("pula OC")
    return " · ".join(parts)


def short_title(post: dict) -> str:
    title = post.get("title") or f"#{post['id']}"
    return title if len(title) <= 14 else title[:13].rstrip() + "…"


def site_row(db: DB, site: str, dates: list[date], now: datetime, lead: dict[int, datetime],
             queue: list[dict], session_down: bool) -> dict:
    cells: dict[date, dict] = {d: {"kind": "empty"} for d in dates}
    rhythm, delay = db.site_rhythm(site), db.site_delay(site)
    row = {"site": site, "label": SITE_LABELS[site], "rhythm": rhythm_label(db, site),
           "session_down": session_down, "off": rhythm == "desligado"}
    if row["off"]:
        row["cells"] = [{"kind": "off"} for _ in dates]
        return row

    by_id = {p["id"]: p for p in queue}
    # o que já saiu hoje neste site
    for pub in db.publications(limit=50):
        when = datetime.fromisoformat(pub["created_at"])
        if pub["site"] == site and pub["status"] == "ok" and when.date() == now.date() and now.date() in cells:
            cells[now.date()] = {"kind": "published", "post": db.get_post(pub["post_id"])}

    items: list[tuple[dict, datetime, bool]] = []  # (post, a partir de quando, segue a fila?)
    for sched in db.site_schedule(site=site, status="agendado"):
        post = db.get_post(sched["post_id"])
        if post:
            items.append((post, datetime.fromisoformat(sched["due_at"]), False))
    follows = db.site_follows_queue(site)
    for post in queue:
        if site not in post["sites"]:
            continue
        zero = lead[post["id"]]
        if site == "paheal" and post["original_character"]:
            if zero.date() in cells and cells[zero.date()]["kind"] == "empty":
                cells[zero.date()] = {"kind": "skipped", "post": post}
            continue
        start = zero if not delay else max(zero, db.daily_slot(zero + timedelta(days=delay)))
        items.append((post, start, follows))

    last, cursor, gap = db.site_last_published(site), now, db.site_day_gap(site)
    for post, start, on_lead in items:
        if on_lead:
            when = start  # padrão: sai junto com o dia 0 da fila principal
        else:
            when = db.earliest_after(max(cursor, start), last, gap)
            last = cursor = when
        if when.date() in cells and cells[when.date()]["kind"] in ("empty", "skipped"):
            cells[when.date()] = {"kind": "waiting" if session_down else "post", "post": post, "when": when}

    if gap == 2:  # dia sim, dia não: o dia depois de um envio fica de folga, não "vazio"
        for d in dates:
            before = cells.get(d - timedelta(days=1), {}).get("kind")
            if cells[d]["kind"] == "empty" and before in ("post", "published", "waiting"):
                cells[d] = {"kind": "rest"}
    row["cells"] = [cells[d] for d in dates]
    return row


def build(db: DB, now: datetime | None = None, days: int = 7) -> dict:
    now = now or datetime.now()
    today = now.date()
    dates = [today + timedelta(days=i) for i in range(days)]
    queue = db.queue()
    lead = db.projected_times(now)
    sessions = db.sessions()

    rows = [site_row(db, site, dates, now, lead, queue,
                     session_down=bool(sessions.get(site)) and not sessions[site]["ok"])
            for site in ALL_SITES]

    lead_days = {when.date() for when in lead.values()}
    last = db.last_published()
    if last and last.date() == today:
        lead_days.add(today)
    empty_days = [d for d in dates if d not in lead_days]
    beyond = [p for p in queue if lead[p["id"]].date() > dates[-1]]
    drafts = db.list_posts(("draft",))

    return {
        "dates": [{"date": d, "label": day_label(d, today).lower(), "today": d == today} for d in dates],
        "rows": rows,
        "beyond": beyond,
        "empty_days": [f"{WEEKDAYS[d.weekday()]} {d.day}" for d in empty_days],
        "suggestion": (beyond[0] if beyond else (drafts[0] if drafts else None)),
        "suggestion_is_draft": not beyond and bool(drafts),
    }
