"""Painel do dia (tela inicial): cálculos sobre o banco, sem HTML.

Tudo aqui só LÊ: fila e previsões vêm do pacer (`db.projected_times`), favoritos vêm de
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

def covered_days(projected: dict[int, datetime], last_published: datetime | None, today: date) -> set[date]:
    """Dias com post: os previstos pela fila + hoje, se o post de hoje já saiu."""
    days = {when.date() for when in projected.values()}
    if last_published and last_published.date() == today:
        days.add(today)
    return days


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
    kind: str           # session | backup | test_mode
    site: str = ""
    label: str = ""
    others: int = 0     # quantos sites do próximo post continuam funcionando


def alerts(db: DB, sites_in_use: list[str], next_post: dict | None) -> list[Alert]:
    """Do mais importante para o menos: sessão caída (trava publicação), backup falhando, modo teste."""
    found: list[Alert] = []
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


def build(db: DB, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    today = now.date()
    queue = db.queue()
    projected = db.projected_times(now)
    by_id = {p["id"]: p for p in queue}

    days = covered_days(projected, db.last_published(), today)
    runway = queue_runway(days, today)

    next_post = queue[0] if queue else None
    next_info = None
    if next_post:
        when = projected[next_post["id"]]
        next_info = {
            "post": next_post, "when": when, "countdown": countdown(when - now),
            "is_today": when.date() == today,
            "when_label": ("hoje" if when.date() == today else day_label(when.date(), today)) + when.strftime(" %H:%M"),
            "sites": [SITE_LABELS[s] for s in next_post["sites"]],
        }

    week = []
    for i in range(7):
        day = today + timedelta(days=i)
        post_id = next((pid for pid, when in projected.items() if when.date() == day), None)
        week.append({
            "day": day, "label": day_label(day, today), "today": i == 0,
            "post": by_id.get(post_id), "when": projected.get(post_id),
            "position": list(projected).index(post_id) + 1 if post_id else None,
        })

    in_use = sorted(set(default_sites(db)) | {s for p in queue for s in p["sites"]}, key=ALL_SITES.index)
    review = [p for p in db.list_posts() if p["status"] in ("draft", "failed", "partial")]
    failed_sites = {p["id"]: [SITE_LABELS[r["site"]] for r in db.publications(p["id"]) if r["status"] == "failed"]
                    for p in review if p["status"] in ("failed", "partial")}

    return {
        "greeting": greeting(now),
        "next": next_info,
        "runway": runway,
        "runway_low": runway < LOW_RUNWAY_DAYS,
        "missing_for_week": max(0, 7 - runway),
        "paws": [today + timedelta(days=i) in days for i in range(7)],
        "favorites": week_favorites(db, now),
        "alert": (alerts(db, in_use, next_post) or [None])[0],
        "week": week,
        "review": review,
        "failed_sites": failed_sites,
        "next_slot": db.projected_next(now),
    }
