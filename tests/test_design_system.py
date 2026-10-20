"""Design system v2: um valor, um lugar. Cor só existe em tokens.css; o resto usa var(--token)."""

import re
from pathlib import Path

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
