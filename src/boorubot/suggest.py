"""Sugestão da IA (tags + rating) rodando sozinha quando a arte chega.

Roda numa fila própria do worker (nunca na fila de publicação): o primeiro uso baixa ~380 MB e
uma publicação não pode esperar por isso. A sugestão só ACRESCENTA ao post: nunca apaga tag,
nunca escolhe o rating e nunca desfaz o que ela já mexeu.
"""

from __future__ import annotations

import logging

from boorubot.db import DB, IMAGES_DIR

log = logging.getLogger("boorubot.suggest")


def merge_tags(current: list[str], suggested: list[str]) -> list[str]:
    """As tags do post primeiro (na ordem dela), depois as novas da IA, sem repetir."""
    return list(dict.fromkeys([*current, *suggested]))


def apply_suggestion(db: DB, post_id: int, s) -> None:
    """Junta a sugestão ao post. Lê o post de novo aqui: ela pode ter editado durante a análise."""
    row = db.get_post(post_id)
    if not row:
        return
    tags = merge_tags(row["tag_list"], s.tags)
    fields = {}
    if not row["oc_touched"]:
        # nenhum personagem conhecido acima do limite = personagem original (ela pode desmarcar)
        oc = not s.characters
        fields = {"original_character": oc, "oc_auto": int(oc)} if oc or row["oc_auto"] else {}
    db.update_post(
        post_id, tags=" ".join(tags),
        tag_scores={**row["tag_scores"], **s.scores},
        weak_tags=[[t, round(float(v), 3)] for t, v in s.weak if t not in tags],
        **fields,
    )


def oc_fields(row: dict, original_character: bool) -> dict:
    """Ela mudou o OC na tela: a escolha dela vale daqui para frente."""
    if bool(original_character) == row["original_character"]:
        return {}
    return {"oc_touched": 1, "oc_auto": 0}


def mochi_note(row: dict) -> str:
    """O recado da Mochi embaixo da arte (vazio quando não há o que dizer)."""
    if not (row["oc_auto"] and row["original_character"]):
        return ""
    n = len(row["tag_scores"])
    note = f"Achei {n} tag{'s' if n != 1 else ''} e nenhum personagem conhecido, então marquei como personagem original."
    if "paheal" in row["sites"]:
        note += " O paheal não aceita OC e vai ficar de fora."
    return note


def accept_weak(row: dict, tags: list[str]) -> dict:
    """Ela adicionou uma tag de "a IA também viu": a tag passa a ter a confiança da IA e sai das fracas."""
    weak = dict(row["weak_tags"])
    taken = {t: v for t, v in weak.items() if t in tags}
    if not taken:
        return {}
    return {"tag_scores": {**row["tag_scores"], **taken},
            "weak_tags": [[t, v] for t, v in row["weak_tags"] if t not in taken]}


def run_suggestion(db: DB, post_id: int, tagger=None) -> str:
    """Analisa a imagem e grava a sugestão. Retorna o estado final (pronta | falhou | missing)."""
    if tagger is None:
        from boorubot.tagger import tagger
    row = db.get_post(post_id)
    if not row:
        return "missing"
    db.set_suggest_state(post_id, "analisando")
    try:
        suggestion = tagger.suggest(IMAGES_DIR / row["image"])
        apply_suggestion(db, post_id, suggestion)
    except Exception as exc:
        log.exception("sugestão do post %s falhou", post_id)
        db.set_suggest_state(post_id, "falhou", f"{type(exc).__name__}: {exc}"[:300])
        return "falhou"
    db.set_suggest_state(post_id, "pronta")
    return "pronta"
