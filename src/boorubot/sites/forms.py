"""Sites sem API de upload: o bot preenche o formulário web num navegador real.

Os campos do formulário de upload só aparecem logado, então o preenchimento é por
heurística (o campo cujo name contém "tag", "source", "rating"...). Rode
`boorubot discover <site>` depois do login para salvar os campos reais em runs/ e
ajustar aqui se alguma heurística errar.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page

from boorubot.models import Post
from boorubot.sites.base import Result, Site, SkipPost
from boorubot.sites.browser import captcha_present, dump_forms, open_page, run_dir, screenshot


class FormSiteError(RuntimeError):
    pass


class FormSite(Site):
    home_url = ""
    login_url = ""
    upload_url = ""
    # rating do post -> valores aceitos no form, em ordem de preferência
    rating_values: dict[str, list[str]] = {}
    # URL depois do envio que indica post criado
    success_url = re.compile(r"$^")
    # rule34.xxx: o Cloudflare barra navegador invisível
    needs_headed = False
    login_timeout_ms = 10 * 60 * 1000
    captcha_timeout_s = 5 * 60

    def __init__(self, username: str = "", password: str = ""):
        self.username = username
        self.password = password

    # --- login -----------------------------------------------------------------

    def login(self) -> Result:
        """Abre o navegador visível no login (usuário/senha do .env já preenchidos).
        A pessoa entra, resolve captcha se tiver e FECHA a janela; a sessão fica em .profiles/<site>."""
        with open_page(self.name, headless=False) as page:
            page.goto(self.login_url)
            if self.username and self.password and page.locator("input[name=pass]").count():
                page.fill("input[name=user]", self.username)
                page.fill("input[name=pass]", self.password)
            page.wait_for_event("close", timeout=self.login_timeout_ms)
        return self.session_check()

    def session_check(self) -> Result:
        """A sessão salva ainda vale? Abre a página de upload e procura o formulário."""
        out = run_dir(self.name)
        with open_page(self.name, headless=not self.needs_headed) as page:
            page.goto(self.upload_url)
            self._wait_captcha(page, 20)
            ok = self._has_upload_form(page)
            shot = screenshot(page, out / "session.png")
        msg = "sessão ativa" if ok else "sem sessão: faça o login de novo"
        return Result(self.name, ok=ok, dry_run=True, message=msg, artifacts=[shot])

    def _wait_captcha(self, page: Page, timeout_s: int) -> bool:
        """Espera o captcha/challenge sumir (sozinho ou resolvido pela pessoa)."""
        for _ in range(timeout_s):
            if not captcha_present(page):
                return True
            page.wait_for_timeout(1000)
        return not captcha_present(page)

    # --- reconhecimento --------------------------------------------------------

    def check(self, headless: bool = True) -> Result:
        """Sem credenciais: a página de login abre? tem formulário? tem captcha?"""
        out = run_dir(self.name)
        with open_page(f"{self.name}-check", headless=headless) as page:
            resp = page.goto(self.login_url, wait_until="domcontentloaded")
            status = resp.status if resp else None
            # challenge do Cloudflare às vezes passa sozinho num navegador real
            for _ in range(20):
                page.wait_for_timeout(1000)
                if not captcha_present(page):
                    break
            login_form = page.locator("form", has=page.locator("input[name=pass]"))
            has_form = login_form.count() > 0
            # widget de captcha pode carregar depois; o rótulo "Captcha" no form já denuncia
            captcha = captcha_present(page) or (has_form and "captcha" in login_form.first.inner_text().lower())
            title = page.title()
            dump_forms(page, out / "login-forms.json")
            shot = screenshot(page, out / "login.png")
        msg = (f"HTTP {status} | título: {title!r} | form de login: {'sim' if has_form else 'NÃO'}"
               f" | captcha: {'SIM (login manual)' if captcha else 'não'}")
        return Result(self.name, ok=has_form, dry_run=True, message=msg,
                      artifacts=[shot, out / "login-forms.json"])

    def discover(self) -> Result:
        """Logado: salva a estrutura do formulário de upload para conferir as heurísticas."""
        out = run_dir(self.name)
        with open_page(self.name, headless=not self.needs_headed) as page:
            page.goto(self.upload_url)
            self._wait_captcha(page, 20)
            forms = dump_forms(page, out / "upload-forms.json")
            shot = screenshot(page, out / "upload.png")
            ok = self._has_upload_form(page)
        msg = f"{len(forms)} formulários salvos" if ok else "sem formulário de upload (não logado?)"
        return Result(self.name, ok=ok, dry_run=True, message=msg, artifacts=[shot, out / "upload-forms.json"])

    # --- publicação ------------------------------------------------------------

    def publish(self, post: Post, dry_run: bool = True) -> Result:
        self.check_rules(post)
        out = run_dir(self.name)
        # envio real roda com navegador visível para a pessoa poder resolver captcha
        with open_page(self.name, headless=dry_run and not self.needs_headed) as page:
            page.goto(self.upload_url)
            if not self._wait_captcha(page, 20 if dry_run else self.captcha_timeout_s):
                shot = screenshot(page, out / "captcha.png")
                raise FormSiteError(f"captcha não resolvido na página de upload. print: {shot}")
            if not self._has_upload_form(page):
                shot = screenshot(page, out / "no-form.png")
                raise FormSiteError(f"sem sessão no site: faça o login (painel: Sites → Entrar). print: {shot}")

            self.fill(page, post)
            filled = screenshot(page, out / "filled.png")
            if dry_run:
                return Result(self.name, ok=True, dry_run=True, message="formulário preenchido, NÃO enviado",
                              artifacts=[filled])

            self._submit(page)
            page.wait_for_load_state("domcontentloaded")
            done = screenshot(page, out / "submitted.png")
            ok = bool(self.success_url.search(page.url))
            msg = "post criado" if ok else f"enviado, mas não reconheci a página de sucesso ({page.url})"
            return Result(self.name, ok=ok, dry_run=False, message=msg, url=page.url if ok else "",
                          artifacts=[filled, done])

    def fill(self, page: Page, post: Post) -> None:
        form = self._upload_form(page)
        form.locator("input[type=file]").first.set_input_files(str(post.image))
        self._fill_text(form, "tag", " ".join(post.tags_for(self.name)), required=True)
        self._fill_text(form, "source", post.source)
        self._fill_text(form, "title", post.title)
        self._set_rating(form, post.rating)

    # --- heurísticas -----------------------------------------------------------

    def _upload_form(self, page: Page):
        return page.locator("form", has=page.locator("input[type=file]")).first

    def _has_upload_form(self, page: Page) -> bool:
        return page.locator("form input[type=file]").count() > 0

    def _fill_text(self, form, key: str, value: str, required: bool = False) -> None:
        field = form.locator(f"textarea[name*={key} i], input[type=text][name*={key} i], input:not([type])[name*={key} i]")
        if field.count() == 0:
            if required:
                raise FormSiteError(f"campo '{key}' não encontrado no formulário")
            return
        if value:
            field.first.fill(value)

    def _set_rating(self, form, rating: str) -> None:
        wanted = [v.lower() for v in self.rating_values.get(rating, [rating])]
        select = form.locator("select[name*=rating i]")
        if select.count():
            options = select.first.locator("option").evaluate_all(
                "os => os.map(o => [o.value, o.text.trim()])")
            for want in wanted:
                for value, text in options:
                    if want in (value.lower(), text.lower()):
                        select.first.select_option(value)
                        return
            raise FormSiteError(f"rating '{rating}' sem opção correspondente em {options}")
        radios = form.locator("input[type=radio][name*=rating i]")
        if radios.count():
            values = radios.evaluate_all("rs => rs.map(r => r.value)")
            for want in wanted:
                for i, value in enumerate(values):
                    if value.lower() == want:
                        radios.nth(i).check()
                        return
            raise FormSiteError(f"rating '{rating}' sem opção correspondente em {values}")
        # site sem campo de rating (ex.: paheal) -> nada a fazer

    def _submit(self, page: Page) -> None:
        form = self._upload_form(page)
        button = form.locator("input[type=submit], button[type=submit], button:not([type])")
        button.first.click()


class Gelbooru(FormSite):
    name = "gelbooru"
    home_url = "https://gelbooru.com/"
    login_url = "https://gelbooru.com/index.php?page=account&s=login&code=00"
    upload_url = "https://gelbooru.com/index.php?page=post&s=add"
    rating_values = {
        "general": ["general", "g", "safe", "s"],
        "sensitive": ["sensitive"],
        "questionable": ["questionable", "q"],
        "explicit": ["explicit", "e"],
    }
    success_url = re.compile(r"page=post&s=view&id=\d+")


class Rule34xxx(Gelbooru):
    """Mesmo engine do Gelbooru 0.2, mas com captcha próprio + Cloudflare na frente."""
    name = "rule34xxx"
    needs_headed = True
    home_url = "https://rule34.xxx/"
    login_url = "https://rule34.xxx/index.php?page=account&s=login&code=00"
    upload_url = "https://rule34.xxx/index.php?page=post&s=add"
    rating_values = {
        "general": ["safe", "s"],
        "sensitive": ["questionable", "q"],
        "questionable": ["questionable", "q"],
        "explicit": ["explicit", "e"],
    }


class Paheal(FormSite):
    """Shimmie2. Campos do upload (código do Shimmie): data0[] / tags / source."""
    name = "paheal"
    home_url = "https://rule34.paheal.net/"
    login_url = "https://rule34.paheal.net/user_admin/login"
    upload_url = "https://rule34.paheal.net/upload"
    success_url = re.compile(r"/post/view/\d+")

    def check_rules(self, post: Post) -> None:
        # Regra 18 do paheal: proibido imagem só com personagem original/OC.
        if post.original_character:
            raise SkipPost("paheal proíbe imagens só com personagem original (regra 18)")


class Rule34us(FormSite):
    name = "rule34us"
    home_url = "https://rule34.us/"
    login_url = "https://rule34.us/index.php?r=account/login"
    # URL do upload ainda não confirmada: sem login o site redireciona. Conferir com `discover`.
    upload_url = "https://rule34.us/index.php?r=posts/upload"
    rating_values = {
        "general": ["safe", "s", "general"],
        "sensitive": ["questionable", "q"],
        "questionable": ["questionable", "q"],
        "explicit": ["explicit", "e"],
    }
    success_url = re.compile(r"r=posts/view&id=\d+")


FORM_SITES: dict[str, type[FormSite]] = {
    cls.name: cls for cls in (Gelbooru, Rule34xxx, Paheal, Rule34us)
}
