from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from boorubot.models import Post


class SkipPost(Exception):
    """O post não deve ir para este site (regra do site, não erro técnico)."""


@dataclass
class Result:
    site: str
    ok: bool
    dry_run: bool
    message: str
    url: str = ""
    artifacts: list[Path] = field(default_factory=list)
    info: dict = field(default_factory=dict)  # dados extras do site (ex.: nível da conta)


class Site:
    name: str = ""

    def check_rules(self, post: Post) -> None:
        """Levanta SkipPost se o post quebra uma regra do site."""

    def publish(self, post: Post, dry_run: bool = True) -> Result:
        raise NotImplementedError
