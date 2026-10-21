"""Contraste dos pares de cor usados no painel (DS-7), com os valores lidos de tokens.css.
Texto: 4,5:1. Ícone, anel de foco e ponto de status: 3:1. Regras e notas em docs/design-system/BRAND-BOOK.md."""

import re
from pathlib import Path

import pytest

TOKENS = Path(__file__).resolve().parents[1] / "src" / "boorubot" / "web" / "static" / "tokens.css"
COLORS = dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})\s*;", TOKENS.read_text(encoding="utf-8")))

TEXT = [
    # texto principal e secundário
    *[("ink", bg) for bg in ("paper", "surface", "blush", "lilac-100", "ok-bg", "warn-bg", "danger-bg")],
    *[("ink-soft", bg) for bg in ("paper", "surface")],
    # links e texto rosa; item atual da barra de baixo
    *[("primary-deep", bg) for bg in ("paper", "surface", "blush")],
    ("primary-strong", "surface"),
    # botões e toast
    ("on-primary", "primary-strong"), ("on-primary", "primary-pressed"), ("on-primary", "danger"),
    ("on-toast", "toast"), ("toast-action", "toast"),
    # status e avisos
    ("ok", "ok-bg"), ("ok", "surface"), ("warn", "warn-bg"), ("warn", "surface"),
    ("danger", "danger-bg"), ("danger", "surface"),
    ("lilac-700", "lilac-100"), ("lilac-700", "surface"),
]
NON_TEXT = [
    ("primary-strong", "surface"), ("primary-strong", "paper"),   # anel de foco
    ("lilac-500", "surface"), ("ink-soft", "surface"),            # ícones e pontos de tag
    ("ink-faint", "surface"),                                     # trilha do toggle desligado
    ("primary", "surface"),                                       # toggle ligado
]


def luminance(hex_color: str) -> float:
    rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(fg: str, bg: str) -> float:
    a, b = sorted((luminance(COLORS[fg]), luminance(COLORS[bg])), reverse=True)
    return (a + 0.05) / (b + 0.05)


@pytest.mark.parametrize("fg,bg", TEXT)
def test_text_pairs_pass_aa(fg, bg):
    assert ratio(fg, bg) >= 4.5, f"{fg} sobre {bg}: {ratio(fg, bg):.2f}:1"


@pytest.mark.parametrize("fg,bg", NON_TEXT)
def test_icons_and_focus_pass_3_to_1(fg, bg):
    assert ratio(fg, bg) >= 3, f"{fg} sobre {bg}: {ratio(fg, bg):.2f}:1"


def test_placeholder_color_is_never_text():
    # ink-faint não passa 4,5:1: só placeholder, ícone inativo e trilha do toggle
    assert ratio("ink-faint", "surface") < 4.5
    css = (TOKENS.parent / "style.css").read_text(encoding="utf-8") + (TOKENS.parent / "components.css").read_text(encoding="utf-8")
    for rule in re.findall(r"([^{}]+)\{[^}]*color:\s*var\(--ink-faint\)", css):
        assert re.search(r"placeholder|svg|icon|toggle|switch|::before|::after|__dot", rule), rule.strip()
