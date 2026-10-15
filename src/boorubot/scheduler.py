"""Agendador: uma fila por site.

Cada site anda sozinho na fila (ordem de `db.queue()`): pega a próxima arte aprovada que tem o
site marcado e ainda não saiu nele. Regras de cada site, iguais às de sempre:
  - no máximo 1 post por dia, a partir do horário diário, e >= MIN_GAP (12 h) desde o último;
  - atraso não acumula: PC desligado por dias = 1 post quando voltar, o resto segue 1 por dia;
  - ritmo: diário, dia sim/dia não (day_gap 2) ou desligado;
  - atraso de N dias: a arte só vai para o site N dias depois de sair no primeiro site;
  - dia fixado (arrastado no calendário): a arte sai naquele dia naquele site, à frente da fila;
  - depois de uma falha, o site espera BACKOFF antes de tentar de novo (sem cascata).

`plan()` simula os próximos dias e é usado por TUDO: o worker publica o que `site_due()` diz
(que é o dia de hoje da mesma simulação), e o Início/Calendário mostram a mesma simulação.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from boorubot.config import ALL_SITES, build_site
from boorubot.db import DB, MIN_GAP, to_post
from boorubot.sites.base import SkipPost

BACKOFF = timedelta(hours=1)
# depois de tantas falhas seguidas, a arte sai do automático naquele site até alguém mandar tentar de novo
MAX_AUTO_TRIES = 3


@dataclass
class Cell:
    kind: str                   # post | published | rest | empty | off
    post: dict | None = None
    when: datetime | None = None
    pinned: bool = False


def rule_skips(site: str, post: dict) -> bool:
    """O site recusa a arte pela regra dele (ex.: paheal não aceita só OC)?"""
    try:
        build_site(site).check_rules(to_post(post))
    except SkipPost:
        return True
    return False


def pending_posts(db: DB, site: str, queue: list[dict] | None = None) -> list[dict]:
    """Fila do site: artes aprovadas, na ordem da fila, com o site marcado e que ainda não saíram nele."""
    queue = db.queue() if queue is None else queue
    return [p for p in queue if site in p["sites"] and site not in db.published_sites(p["id"])
            and not rule_skips(site, p) and db.failures_in_a_row(p["id"], site) < MAX_AUTO_TRIES]


def _published_today(db: DB, site: str, today: date) -> Cell | None:
    for pub in db.publications(limit=200):
        when = datetime.fromisoformat(pub["created_at"])
        if pub["site"] == site and pub["status"] == "ok" and when.date() == today:
            return Cell("published", db.get_post(pub["post_id"]), when)
    return None


def simulate_site(db: DB, site: str, now: datetime, days: int, first_day: dict[int, date],
                  queue: list[dict] | None = None) -> dict[date, Cell]:
    """Dia a dia, o que sai no site. `first_day`: quando cada arte saiu (ou vai sair) no 1º site."""
    today = now.date()
    dates = [today + timedelta(days=i) for i in range(days)]
    if db.site_rhythm(site) == "desligado":
        return {d: Cell("off") for d in dates}

    delay, gap = db.site_delay(site), db.site_day_gap(site)
    pending = pending_posts(db, site, queue)
    pins = db.site_pins(site)
    pinned = sorted(((pins[p["id"]], i, p) for i, p in enumerate(pending) if p["id"] in pins),
                    key=lambda x: (x[0], x[1]))
    flow = [p for p in pending if p["id"] not in pins]
    last = db.site_last_published(site)
    backoff = db.site_backoff(site)

    cells: dict[date, Cell] = {}
    done_today = _published_today(db, site, today)
    if done_today:
        cells[today] = done_today
    for d in dates:
        if d in cells:
            continue
        t = db.daily_slot(datetime.combine(d, now.time()))
        if d == today:
            t = max(t, now)
        if backoff and t < backoff:
            t = backoff
        if last and t - last < MIN_GAP:
            t = last + MIN_GAP
        if t.date() != d or (last and last.date() >= d):
            cells[d] = Cell("empty")
            continue

        due_pins = [x for x in pinned if x[0] <= d]
        if due_pins:  # dia fixado manda, inclusive sobre o "dia sim, dia não"
            pinned.remove(due_pins[0])
            post, is_pin = due_pins[0][2], True
        else:
            if gap == 2 and last and (d - last.date()).days < 2:
                cells[d] = Cell("rest")
                continue
            post = next((p for p in flow if not delay or (
                first_day.get(p["id"]) is not None and first_day[p["id"]] + timedelta(days=delay) <= d)), None)
            if post is None:
                cells[d] = Cell("empty")
                continue
            flow.remove(post)
            is_pin = False
        cells[d] = Cell("post", post, t, is_pin)
        last = t
    return cells


def plan(db: DB, now: datetime | None = None, days: int = 14) -> dict[str, dict[date, Cell]]:
    """Simulação de todos os sites. Sites sem atraso primeiro: eles definem o 1º dia de cada arte,
    que é a referência dos sites com atraso."""
    now = now or datetime.now()
    queue = db.queue()
    first_day: dict[int, date] = {p["id"]: datetime.fromisoformat(p["published_at"]).date()
                                  for p in queue if p["published_at"]}
    out: dict[str, dict[date, Cell]] = {}
    for site in sorted(ALL_SITES, key=lambda s: db.site_delay(s)):
        cells = simulate_site(db, site, now, days, first_day, queue)
        if db.site_delay(site) == 0:
            for d, cell in cells.items():
                if cell.kind == "post":
                    pid = cell.post["id"]
                    first_day[pid] = min(first_day.get(pid, d), d)
        out[site] = cells
    return {site: out[site] for site in ALL_SITES}


def site_due(db: DB, site: str, now: datetime | None = None) -> dict | None:
    """O que o worker deve publicar AGORA neste site (no máximo 1), segundo a mesma simulação."""
    now = now or datetime.now()
    first_day = {p["id"]: datetime.fromisoformat(p["published_at"]).date()
                 for p in db.queue() if p["published_at"]}
    cell = simulate_site(db, site, now, 1, first_day).get(now.date())
    return cell.post if cell and cell.kind == "post" and cell.when <= now else None


def next_free_day(db: DB, sites: list[str], now: datetime | None = None, days: int = 60) -> datetime:
    """Quando sairia uma arte aprovada agora (marcada em `sites`): o 1º dia livre em algum deles."""
    now = now or datetime.now()
    plans = plan(db, now, days)
    for i in range(days):
        d = now.date() + timedelta(days=i)
        for site in sites:
            cell = plans[site].get(d)
            if cell and cell.kind == "empty":
                t = db.daily_slot(datetime.combine(d, now.time()))
                return max(t, now) if d == now.date() else t
    return db.daily_slot(datetime.combine(now.date() + timedelta(days=days), now.time()))
