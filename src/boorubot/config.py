from __future__ import annotations

import os

from dotenv import load_dotenv

from boorubot.sites.browser import ROOT

load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


DANBOORU_URLS = {"test": "https://testbooru.donmai.us", "live": "https://danbooru.donmai.us"}


def build_site(name: str, db=None):
    """Monta o site. Com `db` (painel), as credenciais do painel têm prioridade sobre o .env."""
    from boorubot.sites.danbooru import Danbooru
    from boorubot.sites.forms import FORM_SITES

    if name == "danbooru":
        if db is not None and db.setting("danbooru_api_key"):
            return Danbooru(
                base_url=DANBOORU_URLS.get(db.setting("danbooru_env"), DANBOORU_URLS["test"]),
                login=db.setting("danbooru_login"),
                api_key=db.setting("danbooru_api_key"),
            )
        return Danbooru(
            base_url=env("DANBOORU_URL", DANBOORU_URLS["test"]),
            login=env("DANBOORU_LOGIN"),
            api_key=env("DANBOORU_API_KEY"),
        )
    if name in FORM_SITES:
        prefix = name.upper().replace(".", "_")
        return FORM_SITES[name](username=env(f"{prefix}_USER"), password=env(f"{prefix}_PASS"))
    raise SystemExit(f"site desconhecido: {name}")


ALL_SITES = ("danbooru", "gelbooru", "paheal", "rule34us", "rule34xxx")
