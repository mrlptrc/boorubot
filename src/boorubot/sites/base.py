from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from boorubot.models import Post


class SkipPost(Exception):
    """O post não deve ir para este site (regra do site, não erro técnico)."""


class SiteUnavailable(Exception):
    """O problema é o SITE, não a arte: sem login, captcha, fora do ar, chave recusada.
    O site inteiro pausa até a sessão voltar; nenhuma arte perde tentativa."""


class AccountRefused(SiteUnavailable):
    """O site recusou o envio por causa da CONTA (ex.: "not enough reputation").
    Nada foi publicado. O site fica bloqueado até uma pessoa clicar em Testar/Entrar:
    o re-teste automático não insiste, para o robô não ficar batendo na conta."""


class ContentRefused(Exception):
    """O site recusou ESTA arte (ex.: paheal "Invalid tag used"). Nada foi publicado e reenviar igual
    daria o mesmo erro: o robô não reenvia sozinho; volta para a fila do site quando ela corrigir."""


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
