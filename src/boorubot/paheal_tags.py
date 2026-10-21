"""Tags do paheal: o site só aceita série, personagem e artista (https://rule34.paheal.net/wiki/tagging).

Tag de conteúdo (1girl, roupa, atos, fetiche…) faz o upload ser recusado ("Invalid tag used").
Cada arte guarda uma lista própria para o paheal: montada sozinha a partir das tags da arte
enquanto ela não mexer; se ela editar, vale o que ela escreveu (`paheal_tags_manual`).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable

log = logging.getLogger("boorubot.paheal_tags")

# categorias do Danbooru
ARTIST, COPYRIGHT, CHARACTER = 1, 3, 4
# as únicas tags de conteúdo que o paheal aceita (nome no Danbooru → nome no paheal)
ALLOWED_CONTENT = {
    "animated": "animated", "comic": "comic", "cosplay": "cosplay", "sound": "sound",
    "webm": "webm", "mp4": "mp4", "ai-generated": "AI-generated", "ai_generated": "AI-generated",
    "screenshot_edit": "screenshot_edit", "stereogram": "stereogram", "source_filmmaker": "Source_Filmmaker",
}
QUALIFIER = re.compile(r"_\(([^()]+)\)$")
# qualificadores do Danbooru que não são nome de série
NOT_SERIES = {"artist", "cosplay", "style", "character", "copyright", "series", "cosplayer"}

Lookup = Callable[[list[str]], dict[str, int]]


def _split(tag: str) -> tuple[str, str]:
    """"zero_two_(darling_in_the_franxx)" → ("zero_two", "darling_in_the_franxx")."""
    m = QUALIFIER.search(tag)
    return (tag[:m.start()], m.group(1)) if m else (tag, "")


def build(tags: Iterable[str], categories: dict[str, int], artists: Iterable[str] = ()) -> list[str]:
    """Só artista, série e personagem (sem o parêntese do Danbooru) + as exceções permitidas.
    Sem categoria conhecida, uma tag com parêntese conta como personagem e as tags padrão dela como artista."""
    artists = set(artists)
    out: list[str] = []
    has_subject = False
    for tag in tags:
        cat = categories.get(tag)
        name, qualifier = _split(tag)
        if tag in ALLOWED_CONTENT:
            out.append(ALLOWED_CONTENT[tag])
        elif cat == ARTIST or (cat is None and tag in artists):
            out.append(name if qualifier in NOT_SERIES else tag)
        elif cat == COPYRIGHT:
            out.append(tag)
            has_subject = True
        elif cat == CHARACTER or (cat is None and qualifier and qualifier not in NOT_SERIES):
            out.append(name)
            if qualifier and qualifier not in NOT_SERIES:
                out.append(qualifier)
            has_subject = True
    if not has_subject:
        out.append("tagme")  # regra do paheal: tagme fica até alguém pôr série e personagem
    return list(dict.fromkeys(out))


def normalize(raw: str) -> str:
    items = raw.split(",") if "," in raw else raw.split()
    return " ".join(dict.fromkeys("_".join(t.strip().split()) for t in items if t.strip()))


def refresh(db, post_id: int, lookup: Lookup | None = None) -> str:
    """Remonta as tags do paheal da arte (se ela não editou à mão). `lookup` busca as categorias no
    Danbooru; sem ele, ou se a consulta falhar, usa só o parêntese e as tags padrão. Retorna a lista."""
    row = db.get_post(post_id)
    if not row:
        return ""
    if row["paheal_tags_manual"]:
        return row["paheal_tags"]
    categories: dict[str, int] = {}
    if lookup is not None:
        try:
            categories = lookup(row["tag_list"])
        except Exception as exc:  # sem rede: a heurística ainda separa personagem e artista
            log.info("categorias do Danbooru indisponíveis: %s", exc)
    tags = " ".join(build(row["tag_list"], categories, db.setting("default_tags").split()))
    if tags != row["paheal_tags"]:
        db.update_post(post_id, paheal_tags=tags)
    return tags
