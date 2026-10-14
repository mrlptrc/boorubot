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


@contextmanager
def open_page(site: str, headless: bool = True):
    PROFILES_DIR.mkdir(exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILES_DIR / site),
            headless=headless,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            yield page
        finally:
            try:
                ctx.close()
            except Exception:
                pass  # a pessoa já fechou a janela (fluxo de login)


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
