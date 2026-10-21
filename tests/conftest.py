import httpx
import pytest

from boorubot.tag_api import DanbooruTagAPI


@pytest.fixture(autouse=True)
def no_danbooru_from_tests(monkeypatch):
    """O editor consulta categorias de tag no Danbooru; nos testes, só com transporte falso (sem rede)."""
    real_get = DanbooruTagAPI._get

    def guarded(self, *args, **kwargs):
        if self.transport is None:
            raise httpx.ConnectError("sem rede nos testes")
        return real_get(self, *args, **kwargs)

    monkeypatch.setattr(DanbooruTagAPI, "_get", guarded)
