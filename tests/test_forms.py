"""Heurísticas de preenchimento contra cópias locais dos formulários (sem rede)."""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from boorubot.models import Post
from boorubot.sites.forms import FormSiteError, Paheal, Rule34xxx

EXAMPLE = Path(__file__).resolve().parents[1] / "queue" / "exemplo-001"

# Formato do motor Gelbooru 0.2 (page=post&s=add), o mesmo do rule34.xxx
GELBOORU_FORM = """
<form id="search"><input name="tags" type="text"></form>
<form method="post" enctype="multipart/form-data" action="index.php?page=post&s=add">
  <input type="file" name="upload">
  <input type="text" name="source">
  <input type="text" name="title">
  <textarea name="tags"></textarea>
  <input type="radio" name="rating" value="e">
  <input type="radio" name="rating" value="q">
  <input type="radio" name="rating" value="s">
  <input type="submit" name="submit" value="Upload">
</form>
"""

# Formato do Shimmie2 (paheal), sem campo de rating
SHIMMIE_FORM = """
<form method="post" enctype="multipart/form-data" action="/upload">
  <input type="hidden" name="auth_token" value="abc">
  <input type="file" id="data0" name="data0[]">
  <input type="text" name="tags" class="autocomplete_tags" required>
  <input type="text" name="source">
  <input type="submit" value="Post">
</form>
"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def fill(browser, html, site, post):
    page = browser.new_page()
    page.set_content(html)
    site.fill(page, post)
    return page


def test_gelbooru02_form_rule34xxx(browser):
    post = Post.load(EXAMPLE)
    page = fill(browser, GELBOORU_FORM, Rule34xxx(), post)
    upload = page.locator("form[action*='s=add']")
    assert upload.locator("textarea[name=tags]").input_value() == " ".join(post.tags)
    assert upload.locator("input[name=title]").input_value() == post.title
    assert upload.locator("input[name=rating][value=s]").is_checked()  # general -> safe
    assert page.locator("input[type=file]").evaluate("e => e.files[0].name") == "arte.png"
    # não mexe no campo de busca do topo, que também se chama "tags"
    assert page.locator("form#search input[name=tags]").input_value() == ""


def test_rating_without_matching_option_fails_loudly(browser):
    post = Post.load(EXAMPLE)
    post.rating = "general"
    only_eq = GELBOORU_FORM.replace('<input type="radio" name="rating" value="s">', "")
    with pytest.raises(FormSiteError, match="sem opção"):
        fill(browser, only_eq, Rule34xxx(), post)  # sem "safe": não chuta outro rating


def test_shimmie_form(browser):
    post = Post.load(EXAMPLE)
    page = fill(browser, SHIMMIE_FORM, Paheal(), post)
    assert page.locator("input[name=tags]").input_value() == " ".join(post.tags)
    assert page.locator("#data0").evaluate("e => e.files.length") == 1
