"""Celular (DS-6) no navegador de verdade: barra de baixo, folhas, editor em uma coluna e nada de rolar para o lado."""

import socket
import threading
import time

import pytest
import uvicorn
from PIL import Image
from playwright.sync_api import sync_playwright

from boorubot.web import app as app_mod
from boorubot.db import DB
from boorubot.worker import Worker

PHONE = {"width": 390, "height": 844}
ROUTES = ["/", "/artes", "/calendario", "/metricas", "/sites", "/ajustes", "/guia"]
TAGS = "1girl solo long_hair smile witch_hat looking_at_viewer star_(symbol) holding_wand"


@pytest.fixture(scope="module")
def panel(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mobile")
    images = tmp / "images"
    images.mkdir()
    Image.new("RGB", (600, 800), (240, 200, 220)).save(images / "a.png")
    mp = pytest.MonkeyPatch()
    mp.setattr(app_mod, "IMAGES_DIR", images)
    mp.setattr("boorubot.db.IMAGES_DIR", images)
    db = DB(tmp / "test.db")
    pid = db.create_post("a.png", title="bruxinha", tags=TAGS, sites=["danbooru", "gelbooru", "paheal"])
    app = app_mod.create_app(db, Worker(db), start_worker=False)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", pid
    server.should_exit = True
    thread.join(5)
    mp.undo()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def open_page(browser, url, viewport=PHONE):
    page = browser.new_page(viewport=viewport)
    page.route("https://fonts.googleapis.com/**", lambda r: r.abort())   # sem rede nos testes
    page.goto(url)
    return page


def focus_stays_in(page, selector, tabs=12):
    for _ in range(tabs):
        page.keyboard.press("Tab")
        inside = page.evaluate("""(sel) => {
            const a = document.activeElement;
            return a === document.body || document.querySelector(sel).contains(a);
        }""", selector)
        assert inside, f"o foco saiu de {selector}"


def test_no_page_scrolls_sideways_on_a_phone(panel, browser):
    base, pid = panel
    for path in ROUTES + [f"/posts/{pid}"]:
        page = open_page(browser, base + path)
        wide = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert wide <= 0, f"{path} rola {wide}px para o lado em 390px"
        page.close()


def test_phone_has_the_bottom_bar_instead_of_the_sidebar(panel, browser):
    base, _ = panel
    page = open_page(browser, base + "/calendario")
    assert not page.is_visible("aside.side") and page.is_visible(".bottom-nav")
    labels = page.locator(".bottom-nav .bb-nav__item span:not(.bb-nav__count):not(.bb-nav__dot)").all_inner_texts()
    assert labels == ["Início", "Artes", "Calendário", "Mais"]
    assert page.get_attribute('.bottom-nav a[href="/calendario"]', "aria-current") == "page"
    for box in page.locator(".bottom-nav a").all():
        assert box.bounding_box()["height"] >= 44
    page.close()
    desk = open_page(browser, base + "/", {"width": 1440, "height": 1024})
    assert desk.is_visible("aside.side") and not desk.is_visible(".bottom-nav")
    desk.close()


def test_more_sheet_opens_traps_focus_and_closes_with_esc(panel, browser):
    base, _ = panel
    page = open_page(browser, base + "/")
    page.click(".bottom-nav [data-dialog-open=more-sheet]")
    assert page.is_visible("#more-sheet")
    assert page.locator("#more-sheet .bb-nav__item").all_inner_texts() == ["Métricas", "Sites", "Ajustes", "Guia"]
    focus_stays_in(page, "#more-sheet")
    page.keyboard.press("Escape")
    assert not page.is_visible("#more-sheet")
    page.close()


def test_plus_opens_new_art_and_search_also_closes_with_esc(panel, browser):
    base, _ = panel
    page = open_page(browser, base + "/artes")
    page.click(".bottom-nav__add")
    assert page.is_visible("#new-art")
    focus_stays_in(page, "#new-art")
    page.keyboard.press("Escape")
    assert not page.is_visible("#new-art")
    page.click(".mobile-top [data-open-search]")
    assert page.is_visible("#search")
    page.keyboard.press("Escape")
    assert not page.is_visible("#search")
    page.close()


def test_editor_on_a_phone_summarizes_tags_and_sites_in_sheets(panel, browser):
    base, pid = panel
    page = open_page(browser, f"{base}/posts/{pid}")
    tags = page.locator("#post-form .ed-tag-list > li")
    assert tags.count() == 8 and sum(t.is_visible() for t in tags.all()) == 6
    assert page.inner_text(".ed-tag-more") == "+ 2"
    page.click(".ed-tag-more")
    assert page.is_visible("#tags-sheet") and sum(t.is_visible() for t in tags.all()) == 8
    page.keyboard.press("Escape")
    assert not page.is_visible("#tags-sheet")
    assert page.locator("#post-form > .ed-fields .tag-field").count() == 1   # o campo volta para o lugar (e para o form)

    assert page.inner_text("[data-sites-summary]").startswith("3 de ")
    assert not page.is_visible("#sites-sheet")
    page.click(".ed-sites-open")
    assert page.is_visible("#sites-sheet")
    focus_stays_in(page, "#sites-sheet")
    page.click('#sites-sheet .bb-site[data-site="gelbooru"] .bb-switch, #sites-sheet .bb-site[data-site="gelbooru"] label')
    assert page.inner_text("[data-sites-summary]").startswith("2 de ")
    page.keyboard.press("Escape")

    test = page.locator(".ed-test")
    assert test.get_attribute("aria-label") == "Testar sem publicar"
    assert test.bounding_box()["width"] <= 48 and page.locator(".ed-test .ed-bar__label").bounding_box()["width"] <= 1
    bar, nav = page.locator(".ed-bar").bounding_box(), page.locator(".bottom-nav").bounding_box()
    assert bar["y"] + bar["height"] <= nav["y"]
    page.close()


def test_desktop_editor_keeps_the_site_list_in_place(panel, browser):
    base, pid = panel
    page = open_page(browser, f"{base}/posts/{pid}", {"width": 1440, "height": 1024})
    assert page.is_visible("#sites-sheet .bb-sites") and not page.is_visible(".ed-sites-open")
    assert not page.is_visible("#sites-sheet .bb-sheet__head") and not page.is_visible(".ed-tag-more")
    assert page.locator(".ed-test .ed-bar__label").bounding_box()["width"] > 40
    page.close()
