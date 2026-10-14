"""Métricas de produção do Danbooru: score e favoritos de cada post publicado.

Lê só publicações REAIS (`publications`, status ok) e só do Danbooru real: nunca a conta
de teste nem o testbooru. Busca em lote (`id:1,2,3`, até 100 por request), sem login,
porque score e favoritos são públicos.
"""

from __future__ import annotations

import re

import httpx

from boorubot.config import DANBOORU_URLS
from boorubot.db import DB, now
from boorubot.sites.danbooru import USER_AGENT

BATCH = 100
LIVE_POST_URL = re.compile(r"^https://danbooru\.donmai\.us/posts/(\d+)")


def danbooru_post_id(url: str) -> int | None:
    found = LIVE_POST_URL.match(url or "")
    return int(found.group(1)) if found else None


def collect_danbooru_metrics(db: DB, client: httpx.Client | None = None) -> int:
    """Grava um snapshot por publicação. Retorna quantos snapshots foram gravados."""
    by_post: dict[int, list[int]] = {}
    for pub in db.publications_for_metrics("danbooru"):
        post_id = danbooru_post_id(pub["url"])
        if post_id is not None:  # url de outro host (ex.: testbooru) é ignorada
            by_post.setdefault(post_id, []).append(pub["id"])
    if not by_post:
        db.set_setting("metrics_last_run", now())
        return 0

    client = client or httpx.Client(base_url=DANBOORU_URLS["live"], headers={"User-Agent": USER_AGENT},
                                    timeout=30)
    saved, ids = 0, sorted(by_post)
    for start in range(0, len(ids), BATCH):
        chunk = ids[start:start + BATCH]
        # status:any inclui posts que saíram da busca padrão (ex.: deletados), para não sumirem do histórico
        r = client.get("/posts.json", params={"tags": "id:" + ",".join(map(str, chunk)) + " status:any",
                                              "limit": len(chunk)})
        r.raise_for_status()
        captured = now()
        for post in r.json():
            for pub_id in by_post.get(post.get("id"), []):
                db.add_metric_snapshot(pub_id, captured, post.get("score"), post.get("up_score"),
                                       post.get("down_score"), post.get("fav_count"))
                saved += 1
    db.set_setting("metrics_last_run", now())
    return saved
