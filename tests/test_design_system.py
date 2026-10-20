"""Design system v2: um valor, um lugar. Cor só existe em tokens.css; o resto usa var(--token)."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boorubot.db import DB
from boorubot.web import app as app_mod
from boorubot.worker import Worker

WEB = Path(__file__).resolve().parents[1] / "src" / "boorubot" / "web"
STATIC = WEB / "static"
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
RGB = re.compile(r"\brgba?\(")


def color_literals(text: str) -> list[str]:
    # ids em seletores (#jobs) e âncoras (href="#x") não são cor: só conta hex depois de ":" ou "=" ou dentro de função
    found = [m.group() for m in re.finditer(r"(?<=[:\s(,])" + HEX.pattern, text)]
    return found + RGB.findall(text)


def test_style_css_has_no_hand_written_colors():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert color_literals(css) == []


def test_templates_and_scripts_have_no_hand_written_colors():
    for path in [*WEB.glob("templates/*.html"), *STATIC.glob("*.js")]:
        text = path.read_text(encoding="utf-8")
        text = re.sub(r'href="#[^"]*"', "", text)
        assert color_literals(text) == [], path.name


def test_tokens_are_loaded_before_the_page_styles():
    base = (WEB / "templates" / "base.html").read_text(encoding="utf-8")
    assert base.index("/static/tokens.css") < base.index("/static/style.css")
    assert "Fredoka" not in base and "family=Quicksand:wght@500;600;700" in base


def test_tokens_css_matches_the_design_system_package():
    pkg = Path(__file__).resolve().parents[1] / "docs" / "design-system" / "tokens.css"
    names = lambda t: set(re.findall(r"^\s*(--[\w-]+):", t, re.M))
    assert names((STATIC / "tokens.css").read_text()) == names(pkg.read_text())
    assert "#26704F" in (STATIC / "tokens.css").read_text()   # verde de sucesso que passa AA


# --- DS-2: componentes ---------------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    monkeypatch.setattr(app_mod, "IMAGES_DIR", images)
    monkeypatch.setattr("boorubot.db.IMAGES_DIR", images)
    db = DB(tmp_path / "test.db")
    return TestClient(app_mod.create_app(db, Worker(db), start_worker=False)), db


def test_components_css_uses_only_tokens():
    css = (STATIC / "components.css").read_text(encoding="utf-8")
    assert color_literals(css) == []
    assert "@import" not in css


def test_ui_catalog_renders_every_component(client):
    c, _ = client
    page = c.get("/_ui")
    assert page.status_code == 200
    for cls in ("bb-btn--primary", "bb-btn--secondary", "bb-btn--soft", "bb-btn--danger", "bb-btn--link", "bb-field--error",
                "bb-tag--check", "bb-tag--mine", "bb-tag--suggest", "bb-status--failed", "bb-rating__opt--suggested",
                "bb-toggle", "bb-site--warn", "bb-site--off", "bb-nav__item", "bb-seg", "bb-toast", "bb-mochi", "bb-empty",
                "bb-dialog", "bb-menu__item", "bb-card--float", "bb-kbd"):
        assert cls in page.text, cls


def test_ui_catalog_is_only_for_development(client):
    c, db = client
    db.set_setting("test_mode", "0")
    assert c.get("/_ui").status_code == 404


def test_components_have_accessibility_attributes(client):
    page = client[0].get("/_ui").text
    assert 'role="switch"' in page
    assert 'aria-label="Remover 1girl"' in page
    assert 'aria-current="page"' in page
    assert 'role="radiogroup"' in page and 'type="radio" name="r2" value="general" checked' in page
    # sugestão da IA nunca vem marcada
    assert 'value="sensitive" checked' not in page
    assert 'aria-invalid="true"' in page and 'aria-describedby="f-t3-hint"' in page


def test_icons_are_inline_and_inherit_color(client):
    page = client[0].get("/_ui").text
    assert 'stroke="currentColor"' in page and 'aria-hidden="true" focusable="false"' in page
    with pytest.raises(ValueError):
        app_mod.icon_svg("../segredo")


# --- DS-3: mascotes ------------------------------------------------------------------------------------

PETS = STATIC / "pets"


def test_mochi_expressions_and_the_gang_are_available():
    for name in ("mochi-feliz", "mochi-comemorando", "mochi-pensando", "mochi-dormindo", "mochi-ops",
                 "bear", "bunny", "puppy", "chick", "cat-sleep", "paw"):
        assert (PETS / f"{name}.svg").exists(), name


def test_pets_are_decorative(client):
    page = client[0].get("/_ui").text
    assert '<img class="bb-pet" src="/static/pets/bear.svg" alt=""' in page
    assert "side-pets" not in page   # o trio decorativo do rodapé da sidebar saiu


# --- DS-4: navegação -----------------------------------------------------------------------------------

def test_sidebar_has_the_v2_items_in_order_with_icons(client):
    page = client[0].get("/calendario").text
    side = page[page.index('<aside class="side"'):page.index("</aside>")]
    labels = ["Início", "Artes", "Calendário", "Métricas", "Sites", "Ajustes", "Guia"]
    positions = [side.index(f"<span>{label}</span>") for label in labels]
    assert positions == sorted(positions)
    nav = side[side.index('<nav class="side-nav"'):side.index("</nav>")]
    assert nav.count('class="bb-nav__item"') == 7 and "/static/pets/" not in nav and nav.count("<svg") == 7
    current = side[side.index('aria-current="page"') - 60:side.index('aria-current="page"')]
    assert 'href="/calendario"' in current


def test_sidebar_counts_reviews_and_flags_login(client):
    c, db = client
    db.create_post("a.png", title="a")
    assert 'bb-nav__dot' not in c.get("/").text
    db.set_session("gelbooru", False, "pediu login")
    page = c.get("/").text
    assert 'class="bb-nav__count" aria-label="1 para revisar">1<' in page
    assert 'class="bb-nav__dot"' in page


def test_robot_card_shows_test_mode_and_links_to_settings(client):
    page = client[0].get("/").text
    assert "side-robot--warn" in page and "Modo teste ligado" in page and 'href="/ajustes"' in page


def test_artes_and_ajustes_links_lead_somewhere(client):
    c, _ = client
    assert c.get("/artes").status_code == 200
    assert c.get("/ajustes").status_code == 200


def test_shared_helpers_stay_in_the_stylesheet():
    """.sr-only e [hidden] são usados em todas as telas: não podem sumir quando um bloco de tela é trocado."""
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ".sr-only {" in css and "[hidden] { display: none !important; }" in css and ".page-head {" in css


# --- etapa 16: modais e busca ----------------------------------------------------------------------------

def test_search_is_our_own_dialog_with_groups(client):
    c, db = client
    page = c.get("/").text
    assert 'id="search"' in page and "ninja-keys" not in page and "/static/search.js" in page
    assert 'href="/artes" data-open-search' in page                       # sem JavaScript, leva para Artes
    db.create_post("a.png", title="sereia", tags="mermaid")
    found = c.get("/busca.json").json()["posts"]
    assert found[0]["title"] == "sereia" and found[0]["detail"]


def test_delete_and_publish_dialogs_say_what_happens(client):
    c, db = client
    pid = db.create_post("a.png", title="bruxinha", tags="x", sites=["danbooru", "paheal"])
    page = c.get(f"/posts/{pid}").text
    assert "Apagar &#34;bruxinha&#34;?" in page or "Apagar &quot;bruxinha&quot;?" in page
    assert 'data-confirm-thumb="/images/a.png"' in page and 'data-confirm-ok="Apagar arte"' in page
    assert 'id="publicar-dialog"' in page and "Deixar na fila" in page and "Ela pula a fila" in page


def test_test_mode_strip_turns_it_off(client):
    c, db = client
    r = c.post("/ajustes/desligar-teste", data={"volta": "/calendario"}, follow_redirects=False)
    assert r.headers["location"] == "/calendario" and not db.test_mode
    db.set_setting("test_mode", "1")
    assert c.post("/ajustes/desligar-teste", data={"volta": "//evil.example"}, follow_redirects=False).headers["location"] == "/"
