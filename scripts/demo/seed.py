"""Banco de demonstração do boorubot: artes geradas, títulos e tags inventados. Uso: seed.py <pasta>

Nada aqui é de uma pessoa real: as imagens são paisagens desenhadas pelo próprio script."""
import json, math, random, sys
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

import boorubot.db as db_mod

out = Path(sys.argv[1]); images = out / "images"; images.mkdir(parents=True, exist_ok=True)
db_mod.IMAGES_DIR = images
db = db_mod.DB(out / "boorubot.db")
rnd = random.Random(7)
now = datetime.now().replace(second=0, microsecond=0)

PALETTES = [((255, 205, 222), (180, 140, 230)), ((255, 220, 180), (240, 120, 150)), ((200, 230, 255), (150, 130, 230)),
            ((255, 240, 200), (250, 160, 180)), ((210, 250, 230), (120, 170, 220)), ((250, 210, 240), (120, 90, 180))]


def art(name: str, i: int) -> str:
    top, bottom = PALETTES[i % len(PALETTES)]
    w = h = 768
    im = Image.new("RGB", (w, h))
    px = ImageDraw.Draw(im)
    for y in range(h):
        t = y / h
        px.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    d = ImageDraw.Draw(im, "RGBA")
    cx, cy, r = rnd.randint(220, 540), rnd.randint(180, 320), rnd.randint(90, 140)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 170))
    for _ in range(26):
        x, y, s = rnd.randint(0, w), rnd.randint(0, h // 2), rnd.randint(2, 6)
        d.ellipse([x - s, y - s, x + s, y + s], fill=(255, 255, 255, 200))
    for layer in range(3):
        base = h - 220 + layer * 70
        pts = [(0, h)] + [(x, base + 40 * math.sin(x / (90 + 40 * layer) + i + layer)) for x in range(0, w + 16, 16)] + [(w, h)]
        shade = tuple(int(c * (0.85 - layer * 0.15)) for c in bottom)
        d.polygon(pts, fill=shade + (235,))
    im = im.filter(ImageFilter.GaussianBlur(0.6))
    path = images / f"demo-{i:02d}.png"
    im.save(path)
    return path.name


SITES = ["danbooru", "paheal", "rule34xxx"]
db.set_setting("test_mode", "0")
db.set_setting("daily_time", "19:30")
db.set_setting("default_tags", "artista_exemplo")
db.set_setting("danbooru_login", "artista_exemplo")
db.set_setting("danbooru_api_key", "demo")
db.set_setting("backup_dir", str(out / "backup"))
(out / "backup").mkdir(exist_ok=True)
db.set_session("paheal", True, "sessão ativa")
db.set_session("danbooru", True, "chave válida")

TAGS = {
    "pôr do sol": "1girl solo long_hair sunset sky cloud outdoors smile",
    "kitsune": "1girl fox_ears fox_tail kimono autumn_leaves smile solo",
    "chuva de verão": "1girl umbrella rain summer short_hair solo outdoors",
    "bruxinha": "1girl witch_hat cape broom night star_(sky) solo",
    "jardim noturno": "1girl flower garden night moon long_hair solo",
    "gatinha de inverno": "1girl cat_ears scarf snow winter solo smile",
    "café da tarde": "1girl cup coffee indoors window sweater solo",
    "astronauta": "1girl spacesuit space star_(sky) planet solo",
    "sereia no lago": "1girl mermaid lake water long_hair solo",
    "menina das flores": "1girl flower_field dress sun_hat smile solo",
    "dragãozinho": "dragon no_humans cloud sky wings",
}
titles = list(TAGS)
ids = {}
for i, title in enumerate(titles):
    ids[title] = db.create_post(art(title, i), title=title, tags="artista_exemplo " + TAGS[title], rating="general",
                                source=f"https://x.com/artista_exemplo/status/{1000 + i}", sites=SITES)
    db.update_post(ids[title], paheal_tags="artista_exemplo tagme")

# publicadas: uma a cada 3 dias, com favoritos crescendo
favs = {"pôr do sol": 22, "kitsune": 47, "chuva de verão": 33, "bruxinha": 35, "jardim noturno": 61}
for n, title in enumerate(["pôr do sol", "kitsune", "chuva de verão", "bruxinha", "jardim noturno"]):
    pid = ids[title]; when = now.replace(hour=19, minute=30) - timedelta(days=3 * (5 - n))
    db.approve(pid)
    for site in SITES:
        msg = "enviado à mão" if site == "rule34xxx" else ""
        url = {"danbooru": f"https://testbooru.donmai.us/posts/{900 + n}", "paheal": "", "rule34xxx": ""}[site]
        db.add_publication(pid, site, "ok", url, msg)
    db.conn.execute("UPDATE publications SET created_at = ? WHERE post_id = ?", (when.isoformat(timespec="seconds"), pid))
    db.conn.commit()
    db.update_post(pid, status="done", queue_pos=None)
    db.mark_post_published_at(pid, when)
    pub = [p for p in db.publications(pid) if p["site"] == "danbooru"][0]
    for k, frac in enumerate((0.4, 0.75, 1.0)):
        f = int(favs[title] * frac)
        db.add_metric_snapshot(pub["id"], (when + timedelta(days=k + 1)).isoformat(timespec="seconds"),
                               f + 3, f + 4, 1, f)

db.set_setting("last_published_at", when.isoformat(timespec="seconds"))

# fila: aprovadas, uma por dia a partir de hoje
for title in ["gatinha de inverno", "café da tarde", "astronauta", "sereia no lago"]:
    db.approve(ids[title])

# rascunhos: a Mochi já olhou, falta a artista revisar
for title, rating in (("menina das flores", "general"), ("dragãozinho", "general")):
    pid = ids[title]
    tags = TAGS[title].split()
    db.update_post(pid, rating="", rating_suggestion=rating, rating_confidence=0.93,
                   tag_scores={t: round(0.6 + 0.35 * rnd.random(), 2) for t in tags},
                   weak_tags=[["day", 0.31], ["blue_sky", 0.28]], oc_auto=int(title == "dragãozinho"),
                   original_character=title == "dragãozinho")
    db.set_suggest_state(pid, "pronta")
print({t: ids[t] for t in titles})
