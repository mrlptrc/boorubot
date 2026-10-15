"""Painel do dia (tela inicial): cálculos sobre o banco, sem HTML.

Tudo aqui só LÊ: fila e previsões vêm da simulação por site (`scheduler.plan`), favoritos vêm de
`metric_snapshots` (produção). Nada mexe em fila, status ou ritmo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from boorubot.config import ALL_SITES, SITE_LABELS
from boorubot.db import DB

WEEKDAYS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
LOW_RUNWAY_DAYS = 3


def greeting(now: datetime) -> str:
    if 5 <= now.hour < 12:
        return "Bom dia"
    if 12 <= now.hour < 18:
        return "Boa tarde"
    return "Boa noite"


def day_label(day: date, today: date) -> str:
    short = f"{WEEKDAYS[day.weekday()]} {day.day}"
    return f"Hoje · {short}" if day == today else short


def countdown(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes <= 0:
        return "agora"
    if minutes < 60:
        return f"em {minutes} min"
    if minutes < 24 * 60:
        h, m = divmod(minutes, 60)
        return f"em {h} h {m} min" if m else f"em {h} h"
    days = round(minutes / (24 * 60))
    return f"em {days} dia" + ("s" if days > 1 else "")


# --- folga da fila ---------------------------------------------------------------

def covered_days(plans: dict[str, dict[date, "Cell"]]) -> set[date]:
    """Dias com post em algum site (previsto ou que já saiu hoje)."""
    return {d for cells in plans.values() for d, cell in cells.items() if cell.kind in ("post", "published")}


def queue_runway(days: set[date], today: date) -> int:
    """Quantos dias seguidos, a partir de hoje, têm post."""
    count, day = 0, today
    while day in days:
        count += 1
        day += timedelta(days=1)
    return count


# --- favoritos na semana -----------------------------------------------------------

def favorites_by_day(snapshots: list[dict], today: date, days: int = 7,
                     published_at: dict[int, datetime] | None = None) -> list[tuple[date, int]]:
    """Ganho de favoritos por dia nos últimos `days` dias (hoje incluído).

    Por publicação: diferença entre o snapshot mais recente e a base, distribuída pelos dias
    (o valor de cada dia é o último snapshot daquele dia). A base é:
      - ZERO, se a publicação saiu dentro da janela: a coleta é 1x por dia, então os favoritos das
        primeiras horas (quando o post mais ganha) só aparecem no 1º snapshot e precisam contar;
      - o snapshot mais antigo da janela, se a publicação é de antes da janela.
    `snapshots`: linhas com publication_id, captured_at (iso) e fav_count, já filtradas pela janela.
    `published_at`: quando cada publicação saiu (publications.created_at).
    """
    window = [today - timedelta(days=days - 1 - i) for i in range(days)]
    window_start = datetime.combine(window[0], time.min)
    published_at = published_at or {}
    gains = dict.fromkeys(window, 0)
    by_pub: dict[int, list[tuple[datetime, int]]] = {}
    for row in snapshots:
        if row["fav_count"] is None:
            continue
        by_pub.setdefault(row["publication_id"], []).append(
            (datetime.fromisoformat(row["captured_at"]), int(row["fav_count"])))
    for pub_id, snaps in by_pub.items():
        snaps.sort()
        end_of_day = {}
        for when, favs in snaps:
            end_of_day[when.date()] = favs  # ordenado: o último do dia prevalece
        released = published_at.get(pub_id)
        previous = 0 if released and released >= window_start else snaps[0][1]
        for day in window:
            if day in end_of_day:
                gains[day] += end_of_day[day] - previous
                previous = end_of_day[day]
    return [(day, gains[day]) for day in window]


def publication_times(db: DB) -> dict[int, datetime]:
    return {p["id"]: datetime.fromisoformat(p["created_at"]) for p in db.publications_with_posts()}


def week_favorites(db: DB, now: datetime) -> dict:
    today = now.date()
    start = datetime.combine(today - timedelta(days=6), time.min)
    series = favorites_by_day(db.metric_snapshots_since(start), today, published_at=publication_times(db))
    peak = max([g for _, g in series] + [1])
    return {
        "has_data": db.has_metrics(),
        "total": sum(g for _, g in series),
        "bars": [{"day": d, "gain": g, "pct": round(100 * max(g, 0) / peak)} for d, g in series],
        "aria": "favoritos por dia, últimos 7 dias: " + ", ".join(f"{d.day}/{d.month} {g:+d}" for d, g in series),
    }


# --- alertas -------------------------------------------------------------------------

@dataclass
class Alert:
    kind: str           # confirm | session | backup | test_mode
    site: str = ""
    label: str = ""
    others: int = 0     # quantos sites do próximo post continuam funcionando


def alerts(db: DB, sites_in_use: list[str], next_post: dict | None) -> list[Alert]:
    """Do mais importante para o menos: sessão caída (trava publicação), backup falhando, modo teste."""
    found: list[Alert] = []
    if not db.test_mode and db.needs_confirmation():
        found.append(Alert("confirm", others=len(db.needs_confirmation())))
    sessions = db.sessions()
    for site in ALL_SITES:
        sess = sessions.get(site)
        if site in sites_in_use and sess and not sess["ok"]:
            others = len([s for s in (next_post or {}).get("sites", []) if s != site
                          and (sessions.get(s) or {}).get("ok", 1)])
            found.append(Alert("session", site, SITE_LABELS[site], others))
    if db.setting("backup_dir") and db.setting("last_backup_ok") == "0":
        found.append(Alert("backup"))
    if db.test_mode:
        found.append(Alert("test_mode"))
    return found


# --- montagem da tela ----------------------------------------------------------------

def default_sites(db: DB) -> list[str]:
    try:
        return [s for s in ALL_SITES if s in json.loads(db.setting("default_sites"))]
    except ValueError:
        return list(ALL_SITES)


def failed_pending_sites(db: DB, post: dict) -> list[str]:
    """Sites em que a última tentativa desta arte falhou e que ainda estão pendentes."""
    latest: dict[str, str] = {}
    for pub in reversed(db.publications(post["id"])):  # do mais antigo para o mais novo
        latest[pub["site"]] = pub["status"]
    pending = set(db.pending_sites(post))
    return [SITE_LABELS[s] for s in post["sites"] if latest.get(s) == "failed" and s in pending]


def build(db: DB, now: datetime | None = None) -> dict:
    from boorubot.scheduler import next_free_day, plan

    now = now or datetime.now()
    today = now.date()
    queue = db.queue()
    position = {p["id"]: i + 1 for i, p in enumerate(queue)}
    plans = plan(db, now, 14)

    days = covered_days(plans)
    runway = queue_runway(days, today)

    # cada envio previsto: (quando, site, arte); fila por site = artes diferentes no mesmo dia
    events = sorted(((c.when, site, c.post) for site, cells in plans.items() for c in cells.values()
                     if c.kind == "post"), key=lambda e: (e[0], ALL_SITES.index(e[1])))

    next_info = None
    if events:
        when, _, post = events[0]
        sites = [SITE_LABELS[s] for w, s, p in events if p["id"] == post["id"] and w.date() == when.date()]
        next_info = {
            "post": post, "when": when, "countdown": countdown(when - now), "is_today": when.date() == today,
            "when_label": ("hoje" if when.date() == today else day_label(when.date(), today)) + when.strftime(" %H:%M"),
            "sites": sites,
        }

    week = []
    for i in range(7):
        day = today + timedelta(days=i)
        posts, seen = [], set()
        for when, site, post in events:
            if when.date() == day and post["id"] not in seen:
                seen.add(post["id"])
                posts.append((post, when))
        main = posts[0] if posts else (None, None)
        week.append({
            "day": day, "label": day_label(day, today), "today": i == 0,
            "post": main[0], "when": main[1], "extra": [p for p, _ in posts[1:]],
            "position": position.get(main[0]["id"]) if main[0] else None,
        })
    today_posts = {p["id"]: p for w, s, p in events if w.date() == today}
    today_sites = len({s for w, s, p in events if w.date() == today})

    in_use = sorted(set(default_sites(db)) | {s for p in queue for s in p["sites"]}, key=ALL_SITES.index)
    review = [p for p in db.list_posts() if p["status"] in ("draft", "failed", "partial")]
    review += [p for p in queue if failed_pending_sites(db, p) or db.uncertain_sites(p["id"])]
    failed_sites = {p["id"]: failed_pending_sites(db, p) or
                    [SITE_LABELS[r["site"]] for r in db.publications(p["id"]) if r["status"] == "failed"]
                    for p in review if p["status"] != "draft"}
    to_check = {p["id"]: [SITE_LABELS[s] for s in db.uncertain_sites(p["id"])] for p in review}

    return {
        "greeting": greeting(now),
        "next": next_info,
        "today_posts": list(today_posts.values()),
        "today_sites": today_sites,
        "runway": runway,
        "runway_low": runway < LOW_RUNWAY_DAYS,
        "missing_for_week": max(0, 7 - runway),
        "paws": [today + timedelta(days=i) in days for i in range(7)],
        "favorites": week_favorites(db, now),
        "alert": (alerts(db, in_use, queue[0] if queue else None) or [None])[0],
        "week": week,
        "review": review,
        "failed_sites": failed_sites,
        "to_check": to_check,
        "next_slot": next_free_day(db, default_sites(db), now),
        "to_confirm": [] if db.test_mode else db.needs_confirmation(),
    }
