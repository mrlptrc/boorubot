from __future__ import annotations

import os

from dotenv import load_dotenv

from boorubot.sites.browser import ROOT

load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


DANBOORU_URLS = {"test": "https://testbooru.donmai.us", "live": "https://danbooru.donmai.us"}


def build_site(name: str, db=None, test: bool = False):
    """Monta o site. Com `db` (painel), produção e teste usam contas SEPARADAS:
    produção -> Danbooru real + conta real; test=True -> testbooru + conta de teste."""
    from boorubot.sites.danbooru import Danbooru
    from boorubot.sites.forms import FORM_SITES

    if name == "danbooru":
        if db is not None and test:
            return Danbooru(
                base_url=DANBOORU_URLS["test"],
                login=db.setting("danbooru_test_login"),
                api_key=db.setting("danbooru_test_api_key"),
            )
        if db is not None:
            return Danbooru(
                base_url=DANBOORU_URLS["live"],
                login=db.setting("danbooru_login"),
                api_key=db.setting("danbooru_api_key"),
                force_pending=db.setting("danbooru_force_pending") == "1",
            )
        # CLI do técnico: .env (testbooru por padrão)
        return Danbooru(
            base_url=env("DANBOORU_URL", DANBOORU_URLS["test"]),
            login=env("DANBOORU_LOGIN"),
            api_key=env("DANBOORU_API_KEY"),
        )
    if name in FORM_SITES:
        prefix = name.upper().replace(".", "_")
        send_mode = db.setting(f"browser_mode_{name}") if db is not None else None
        return FORM_SITES[name](username=env(f"{prefix}_USER"), password=env(f"{prefix}_PASS"),
                                send_mode=send_mode or None)
    raise SystemExit(f"site desconhecido: {name}")


ALL_SITES = ("danbooru", "gelbooru", "paheal", "rule34us", "rule34xxx")
SITE_LABELS = {
    "danbooru": "Danbooru", "gelbooru": "Gelbooru", "paheal": "rule34.paheal",
    "rule34us": "rule34.us", "rule34xxx": "rule34.xxx",
}
