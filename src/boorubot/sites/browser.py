"""Helpers de Playwright compartilhados pelos sites sem API.

Cada site usa um perfil de navegador persistente em .profiles/<site>: o login (e captcha,
se houver) é feito uma vez por uma pessoa e o bot reaproveita a sessão.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
PROFILES_DIR = ROOT / ".profiles"
RUNS_DIR = ROOT / "runs"

# Como o navegador roda:
#   headless = sem janela nenhuma (o normal no envio real)
#   hidden   = navegador real com a janela FORA da tela (sites que barram headless, ex. rule34.xxx);
#              se aparecer captcha, reveal_window() traz a janela para a pessoa resolver
#   visible  = janela na tela (login, que é sempre feito por uma pessoa)
# Fora da tela em vez de minimizado: janela minimizada pode parar de renderizar e travar o Playwright.
BROWSER_MODES = ("headless", "hidden", "visible")
OFFSCREEN_ARGS = ["--window-position=-32000,-32000"]

CAPTCHA_PATTERNS = re.compile(r"captcha|just a moment|attention required", re.I)
CAPTCHA_FRAMES = ("recaptcha", "hcaptcha", "turnstile", "challenges.cloudflare.com")

DUMP_FORMS_JS = """
() => Array.from(document.forms).map(f => ({
  action: f.getAttribute('action'),
  method: f.method,
  enctype: f.enctype,
  fields: Array.from(f.elements).filter(e => e.name || e.id).map(e => ({
    tag: e.tagName.toLowerCase(),
    type: e.type || null,
    name: e.name || null,
    id: e.id || null,
    required: !!e.required,
    value: (e.type === 'hidden' || e.type === 'radio' || e.type === 'checkbox' || e.type === 'submit') ? e.value : null,
    label: (e.labels && e.labels[0]) ? e.labels[0].innerText.trim().slice(0, 60) : null,
    options: e.tagName === 'SELECT' ? Array.from(e.options).map(o => [o.value, o.text.trim()]) : null,
  })),
}))
"""


def run_dir(site: str) -> Path:
    path = RUNS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{site}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def launch_options(mode: str) -> dict:
    if mode not in BROWSER_MODES:
        raise ValueError(f"modo de navegador desconhecido: {mode}")
    options = {"headless": mode == "headless", "viewport": {"width": 1280, "height": 900}, "locale": "en-US"}
    if mode == "headless":
        # headless com o Chromium completo ("novo headless"). O padrão do Playwright é outro executável
        # (chromium-headless-shell), que NÃO lê os cookies gravados pela janela de login: o site parecia
        # deslogado no teste de sessão e no envio, mesmo com o login feito.
        options["channel"] = "chromium"
    if mode == "hidden":
        options["args"] = list(OFFSCREEN_ARGS)
    return options


@contextmanager
def open_page(site: str, mode: str = "headless"):
    PROFILES_DIR.mkdir(exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILES_DIR / site), **launch_options(mode))
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            yield page
        finally:
            try:
                ctx.close()
            except Exception:
                pass  # a pessoa já fechou a janela (fluxo de login)


def reveal_window(page: Page) -> None:
    """Traz a janela escondida (fora da tela) para a frente, para a pessoa resolver um captcha."""
    cdp = page.context.new_cdp_session(page)
    window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
    # windowState e posição não podem ir na mesma chamada quando a janela não está "normal"
    cdp.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": "normal"}})
    cdp.send("Browser.setWindowBounds", {"windowId": window_id,
                                         "bounds": {"left": 80, "top": 60, "width": 1300, "height": 950}})
    page.bring_to_front()


def captcha_present(page: Page) -> bool:
    if CAPTCHA_PATTERNS.search(page.title() or ""):
        return True
    return any(any(m in (f.url or "") for m in CAPTCHA_FRAMES) for f in page.frames)


def dump_forms(page: Page, out: Path) -> list[dict]:
    forms = page.evaluate(DUMP_FORMS_JS)
    out.write_text(json.dumps(forms, indent=2, ensure_ascii=False), encoding="utf-8")
    return forms


def screenshot(page: Page, out: Path) -> Path:
    page.screenshot(path=str(out), full_page=True)
    return out
