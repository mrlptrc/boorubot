"""Página de Métricas: agregações sobre metric_snapshots + publications (só produção).

Hoje só o Danbooru tem score/favoritos coletados (metrics.py). Os outros sites aparecem com
contagem de posts e "sem métrica por enquanto" até ganharem coleta própria.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from boorubot.config import ALL_SITES, SITE_LABELS
from boorubot.dashboard import favorites_by_day
from boorubot.db import DB

PERIODS = {"7": ("7 dias", 7), "30": ("30 dias", 30), "tudo": ("Tudo", None)}
# score "das primeiras 24 h": o snapshot mais perto de +24 h, aceito entre a publicação e +48 h
FIRST_DAY = timedelta(hours=24)
FIRST_DAY_TOLERANCE = timedelta(hours=24)


def period_start(period: str, now: datetime, db: DB) -> date:
    days = PERIODS.get(period, PERIODS["30"])[1]
    if days:
        return now.date() - timedelta(days=days - 1)
    stamps = [p["created_at"] for p in db.publications_with_posts() if p["status"] == "ok"]
    stamps += [s["captured_at"] for s in db.all_metric_snapshots()]
    return min(datetime.fromisoformat(x).date() for x in stamps) if stamps else now.date()


def weekly(series: list[tuple[date, int]]) -> list[dict]:
    """Agrupa o ganho diário em semanas (começando na segunda-feira)."""
    weeks: dict[date, int] = {}
    for day, gain in series:
        start = day - timedelta(days=day.weekday())
        weeks[start] = weeks.get(start, 0) + gain
    peak = max([g for g in weeks.values()] + [1])
    return [{"start": start, "label": f"{start.day}/{start.month}", "gain": gain,
             "pct": round(100 * max(gain, 0) / peak)} for start, gain in sorted(weeks.items())]


def first_day_score(publication: dict, snapshots: list[dict]) -> int | None:
    published = datetime.fromisoformat(publication["created_at"])
    target = published + FIRST_DAY
    best = None
    for snap in snapshots:
        when = datetime.fromisoformat(snap["captured_at"])
        if snap["score"] is None or not (published <= when <= target + FIRST_DAY_TOLERANCE):
            continue
        if best is None or abs(when - target) < abs(datetime.fromisoformat(best["captured_at"]) - target):
            best = snap
    return best["score"] if best else None


def previous_favorites(db: DB, start_day: date, days: int) -> int:
    """Favoritos ganhos no período anterior de mesmo tamanho (para "18 a mais que o mês anterior")."""
    prev_end = start_day - timedelta(days=1)
    prev_start = datetime.combine(prev_end - timedelta(days=days - 1), time.min)
    snaps = [s for s in db.all_metric_snapshots()
             if prev_start <= datetime.fromisoformat(s["captured_at"]) < datetime.combine(start_day, time.min)]
    series = favorites_by_day(snaps, prev_end, days,
                              published_at={p["id"]: datetime.fromisoformat(p["created_at"])
                                            for p in db.publications_with_posts()})
    return sum(g for _, g in series)


def build(db: DB, period: str = "30", now: datetime | None = None) -> dict:
    now = now or datetime.now()
    period = period if period in PERIODS else "30"
    start_day = period_start(period, now, db)
    start = datetime.combine(start_day, time.min)
    days = (now.date() - start_day).days + 1

    pubs = db.publications_with_posts()
    snaps_all = db.all_metric_snapshots()
    snaps_by_pub: dict[int, list[dict]] = {}
    for s in snaps_all:
        snaps_by_pub.setdefault(s["publication_id"], []).append(s)
    in_period = [p for p in pubs if datetime.fromisoformat(p["created_at"]) >= start]

    series = favorites_by_day([s for s in snaps_all if datetime.fromisoformat(s["captured_at"]) >= start],
                              now.date(), days,
                              published_at={p["id"]: datetime.fromisoformat(p["created_at"]) for p in pubs})
    first_scores = [sc for p in in_period if p["status"] == "ok"
                    for sc in [first_day_score(p, snaps_by_pub.get(p["id"], []))] if sc is not None]

    ok_posts = {p["post_id"] for p in in_period if p["status"] == "ok"}
    failed_posts = {p["post_id"] for p in in_period if p["status"] == "failed"} - ok_posts

    # mais amadas: favoritos atuais (último snapshot) de cada arte com publicação no período
    latest = {pub_id: snaps[-1] for pub_id, snaps in snaps_by_pub.items()}
    loved: dict[int, dict] = {}
    for p in in_period:
        snap = latest.get(p["id"])
        if p["status"] != "ok" or not snap or snap["fav_count"] is None:
            continue
        entry = loved.setdefault(p["post_id"], {"post_id": p["post_id"], "title": p["title"], "image": p["image"],
                                                "favs": 0, "score": 0, "site": SITE_LABELS[p["site"]]})
        entry["favs"] += snap["fav_count"] or 0
        entry["score"] += snap["score"] or 0
    top = sorted(loved.values(), key=lambda e: (-e["favs"], -e["score"], e["post_id"]))[:3]

    by_site = []
    for site in ALL_SITES:
        site_pubs = [p for p in in_period if p["site"] == site and p["status"] == "ok"]
        measured = [latest[p["id"]] for p in site_pubs if p["id"] in latest]
        by_site.append({
            "site": site, "label": SITE_LABELS[site], "posts": len(site_pubs), "has_metrics": bool(measured),
            "avg_score": sum(s["score"] or 0 for s in measured) / len(measured) if measured else None,
            "favs": sum(s["fav_count"] or 0 for s in measured) if measured else None,
        })

    return {
        "period": period, "periods": PERIODS, "start": start_day,
        "has_data": bool(snaps_all), "has_publications": bool(pubs),
        "favorites": sum(g for _, g in series),
        # comparação com o período anterior de mesmo tamanho; em "Tudo" não há anterior
        "previous_favorites": previous_favorites(db, start_day, days) if PERIODS[period][1] else None,
        "first_day_score": sum(first_scores) / len(first_scores) if first_scores else None,
        "first_day_count": len(first_scores),
        "posts": len(ok_posts), "failed": len(failed_posts),
        "weeks": weekly(series),
        "top": top,
        "by_site": by_site,
        "last_run": db.setting("metrics_last_run"),
    }
