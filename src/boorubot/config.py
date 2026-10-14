from __future__ import annotations

import os

from dotenv import load_dotenv

from boorubot.sites.browser import ROOT

load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def build_site(name: str):
    from boorubot.sites.danbooru import Danbooru
    from boorubot.sites.forms import FORM_SITES

    if name == "danbooru":
        return Danbooru(
            base_url=env("DANBOORU_URL", "https://testbooru.donmai.us"),
            login=env("DANBOORU_LOGIN"),
            api_key=env("DANBOORU_API_KEY"),
        )
    if name in FORM_SITES:
        prefix = name.upper().replace(".", "_")
        return FORM_SITES[name](username=env(f"{prefix}_USER"), password=env(f"{prefix}_PASS"))
    raise SystemExit(f"site desconhecido: {name}")


ALL_SITES = ("danbooru", "gelbooru", "paheal", "rule34us", "rule34xxx")
