"""Um post na fila: uma pasta com a imagem + post.json."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

RATINGS = ("general", "sensitive", "questionable", "explicit")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


class PostError(ValueError):
    pass


@dataclass
class Post:
    folder: Path
    image: Path
    tags: list[str]
    rating: str
    source: str = ""
    title: str = ""
    commentary_title: str = ""
    commentary_description: str = ""
    original_character: bool = False
    sites: list[str] = field(default_factory=list)
    site_tags: dict[str, list[str]] = field(default_factory=dict)
    # lista que SUBSTITUI as tags num site (paheal: só série, personagem e artista)
    only_tags: dict[str, list[str]] = field(default_factory=dict)

    def tags_for(self, site: str) -> list[str]:
        """Tags base + extras específicas do site, sem duplicar (ou a lista própria do site, se houver)."""
        if self.only_tags.get(site):
            return list(self.only_tags[site])
        merged = list(self.tags)
        for tag in self.site_tags.get(site, []):
            if tag not in merged:
                merged.append(tag)
        return merged

    @classmethod
    def load(cls, folder: Path) -> Post:
        folder = Path(folder)
        meta_path = folder / "post.json"
        if not meta_path.exists():
            raise PostError(f"{folder}: falta post.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        images = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS)
        if len(images) != 1:
            raise PostError(f"{folder}: esperava 1 imagem, achei {len(images)}")

        tags = [t.strip().lower().replace(" ", "_") for t in meta.get("tags", []) if t.strip()]
        if not tags:
            raise PostError(f"{folder}: 'tags' vazio")

        rating = meta.get("rating", "")
        if rating not in RATINGS:
            raise PostError(f"{folder}: rating '{rating}' inválido, use um de {RATINGS}")

        commentary = meta.get("commentary", {})
        return cls(
            folder=folder,
            image=images[0],
            tags=tags,
            rating=rating,
            source=meta.get("source", ""),
            title=meta.get("title", ""),
            commentary_title=commentary.get("title", ""),
            commentary_description=commentary.get("description", ""),
            original_character=bool(meta.get("original_character", False)),
            sites=list(meta.get("sites", [])),
            site_tags={k: list(v) for k, v in meta.get("site_tags", {}).items()},
        )
