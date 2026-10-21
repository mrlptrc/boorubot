"""Sites sem API de upload: o bot preenche o formulário web num navegador real.

Os campos do formulário de upload só aparecem logado, então o preenchimento é por
heurística (o campo cujo name contém "tag", "source", "rating"...). Rode
`boorubot discover <site>` depois do login para salvar os campos reais em runs/ e
ajustar aqui se alguma heurística errar.
"""

from __future__ import annotations

import re
import time
from typing import Callable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from boorubot.models import Post
from boorubot.sites.base import AccountRefused, ContentRefused, Result, Site, SiteUnavailable, SkipPost
from boorubot.sites.browser import (
    BROWSER_MODES, captcha_present, dump_forms, open_page, reveal_window, run_dir, screenshot,
)

CAPTCHA_MSG = "o site pediu captcha: entre de novo em Sites → Entrar (e resolva o captcha lá)"


class FormSiteError(RuntimeError):
    pass


class FormSiteDown(FormSiteError, SiteUnavailable):
    """Sem sessão, captcha ou site fora do ar: pausa o site, não conta contra a arte."""


# página depois do envio dizendo que a CONTA não pode enviar (nada foi publicado)
ACCOUNT_REFUSED = re.compile(
    r"not have enough reputation|lacks permission|permission denied|not allowed to (upload|add)", re.I)


class FormSite(Site):
    home_url = ""
    login_url = ""
    upload_url = ""
    # rating do post -> valores aceitos no form, em ordem de preferência
    rating_values: dict[str, list[str]] = {}
    # URL depois do envio que indica post criado
    success_url = re.compile(r"$^")
    # rule34.xxx: o Cloudflare barra navegador invisível (headless vira "hidden")
    needs_headed = False
    # modo do navegador no ENVIO REAL; pode ser trocado por site no painel (settings browser_mode_<site>)
    send_mode = "headless"
    # cookies que o site só grava depois do login: dizem se a pessoa chegou a entrar na conta
    login_cookies: tuple[str, ...] = ("user_id", "pass_hash")
    login_timeout_ms = 10 * 60 * 1000
    captcha_timeout_s = 5 * 60

    def __init__(self, username: str = "", password: str = "", send_mode: str | None = None):
        self.username = username
        self.password = password
        self.send_mode_override = send_mode if send_mode in BROWSER_MODES else None

    def browser_mode(self, purpose: str) -> str:
        """purpose: login | send (envio real) | dry_run | session | discover."""
        if purpose == "login":
            return "visible"  # login e captcha são sempre feitos por uma pessoa, na tela
        mode = (self.send_mode_override or self.send_mode) if purpose == "send" else "headless"
        if self.needs_headed and mode == "headless":
            mode = "hidden"
        return mode

    # --- login -----------------------------------------------------------------

    def login(self, cancelled: Callable[[], bool] = lambda: False) -> Result | None:
        """Abre o navegador visível no login (usuário/senha do .env já preenchidos).
        A pessoa entra, resolve captcha se tiver e FECHA a janela; a sessão fica em .profiles/<site>.
        `cancelled()` verdadeiro (botão Cancelar no painel) fecha a janela e devolve None, sem mexer na sessão."""
        with open_page(self.name, self.browser_mode("login")) as page:
            page.goto(self.login_url)
            # .first: o paheal tem dois formulários de login na mesma página (lateral e centro)
            if self.username and self.password and page.locator("input[name=pass]").count():
                page.locator("input[name=user]").first.fill(self.username)
                page.locator("input[name=pass]").first.fill(self.password)
            # espera em fatias curtas (na thread do navegador) para poder cancelar sem fechar de outra thread
            deadline = time.monotonic() + self.login_timeout_ms / 1000
            while not page.is_closed():
                if cancelled():
                    return None
                if time.monotonic() > deadline:
                    raise TimeoutError("o login passou de 10 minutos sem a janela ser fechada")
                try:
                    page.wait_for_timeout(500)
                except PlaywrightError:  # a janela fechou durante a espera
                    break
        return self.session_check()

    def session_check(self) -> Result:
        """A sessão salva ainda vale? Abre a página de upload e procura o formulário."""
        out = run_dir(self.name)
        with open_page(self.name, self.browser_mode("session")) as page:
            page.goto(self.upload_url)
            captcha = not self._wait_captcha(page, 20)
            ok = self._has_upload_form(page)
            dump_forms(page, out / "upload-forms.json")  # diagnóstico dos campos reais
            shot = screenshot(page, out / "session.png")
            logged = self._logged_in(page)
        return Result(self.name, ok=ok, dry_run=True, message=self._session_message(ok, captcha, logged),
                      artifacts=[shot])

    @staticmethod
    def _account_refused(page: Page) -> str:
        """Frase do site recusando a conta (ou "" se não houver)."""
        try:
            text = page.inner_text("body")
        except Exception:
            return ""
        m = ACCOUNT_REFUSED.search(text)
        if not m:
            return ""
        line = next((ln.strip() for ln in text.splitlines() if m.group(0).lower() in ln.lower()), m.group(0))
        return line[:200]

    def _content_refused(self, page: Page) -> str:
        """O site recusou esta arte (e não a conta)? Frase para a artista entender, ou ""."""
        return ""

    def _logged_in(self, page: Page) -> bool:
        names = {c["name"] for c in page.context.cookies()}
        # aceita prefixo (Shimmie usa <prefixo>_user / <prefixo>_session)
        return all(any(n == want or n.endswith("_" + want) for n in names) for want in self.login_cookies)

    @staticmethod
    def _session_message(ok: bool, captcha: bool, logged: bool) -> str:
        """Diz o motivo real, para a pessoa saber o que fazer."""
        if ok:
            return "sessão ativa"
        if captcha:
            return "o site pediu captcha: clique em Entrar e resolva na janela"
        if not logged:
            return ("o site não registrou o login: clique em Entrar, entre na sua conta e só feche "
                    "a janela depois de ver que está logada")
        return "logada, mas a página de upload não abriu (a conta pode não ter permissão de enviar)"


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
        with open_page(f"{self.name}-check", "headless" if headless else "visible") as page:
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
        with open_page(self.name, self.browser_mode("discover")) as page:
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
        mode = self.browser_mode("dry_run" if dry_run else "send")
        with open_page(self.name, mode) as page:
            try:
                page.goto(self.upload_url)
            except Exception as exc:  # sem internet / site fora do ar: nada foi enviado
                raise FormSiteDown(f"não consegui abrir o site ({type(exc).__name__}: {exc})") from exc
            # challenge do Cloudflare às vezes passa sozinho em alguns segundos
            if not self._wait_captcha(page, 20):
                if dry_run or mode == "headless":
                    # sem janela não há como uma pessoa resolver; nunca tentar contornar
                    shot = screenshot(page, out / "captcha.png")
                    raise FormSiteDown(f"{CAPTCHA_MSG}. print: {shot}")
                if mode == "hidden":
                    reveal_window(page)
                if not self._wait_captcha(page, self.captcha_timeout_s):
                    shot = screenshot(page, out / "captcha.png")
                    raise FormSiteDown(f"captcha não resolvido a tempo na página de upload. print: {shot}")
            if not self._has_upload_form(page):
                shot = screenshot(page, out / "no-form.png")
                raise FormSiteDown(f"sem sessão no site: faça o login (painel: Sites → Entrar). print: {shot}")

            self.fill(page, post)
            filled = screenshot(page, out / "filled.png")
            if dry_run:
                return Result(self.name, ok=True, dry_run=True, message="formulário preenchido, NÃO enviado",
                              artifacts=[filled])

            # Depois do clique, o post PODE ter saído. Se não der para confirmar, o resultado é
            # "incerto": o robô nunca reenvia sozinho (evita duplicata); uma pessoa confere no site.
            try:
                self._submit(page)
                page.wait_for_load_state("domcontentloaded")
                done = screenshot(page, out / "submitted.png")
            except Exception as exc:
                return Result(self.name, ok=False, dry_run=False, artifacts=[filled], info={"uncertain": True},
                              message=f"erro depois do clique em enviar ({type(exc).__name__}: {exc}); confira no site")
            refused = self._account_refused(page)
            if refused:
                raise AccountRefused(f"o site recusou o envio por causa da conta: \"{refused}\". "
                                     f"Nada foi publicado. print: {done}")
            if self.success_url.search(page.url):
                return Result(self.name, ok=True, dry_run=False, message="post criado", url=page.url,
                              artifacts=[filled, done])
            refused = self._content_refused(page)
            if refused:
                raise ContentRefused(f"{refused}. Nada foi publicado. print: {done}")
            return Result(self.name, ok=False, dry_run=False, artifacts=[filled, done], info={"uncertain": True},
                          message=f"enviado, mas não reconheci a página de sucesso ({page.url}); confira no site")

    def fill(self, page: Page, post: Post) -> None:
        form = self._upload_form(page)
        form.locator("input[type=file]").first.set_input_files(str(post.image))
        self._fill_text(form, "tag", " ".join(post.tags_for(self.name)), required=True)
        self._fill_text(form, "source", post.source)
        self._fill_text(form, "title", post.title)
        self._set_rating(form, post.rating)
        # alguns formulários só mostram source/tags (com animação) depois de escolher o arquivo:
        # espera terminar para o print mostrar o que foi preenchido
        page.wait_for_timeout(1000)

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


class Gelbooru02(FormSite):
    """Motor Gelbooru 0.2 (page=post&s=add). O Gelbooru em si saiu do projeto: ele copia sozinho os
    posts do Danbooru. Fica a base do rule34.xxx, que usa o mesmo motor."""
    success_url = re.compile(r"page=post&s=view&id=\d+")


class Rule34xxx(Gelbooru02):
    """Mesmo engine do Gelbooru 0.2, mas com captcha próprio + Cloudflare na frente."""
    name = "rule34xxx"
    needs_headed = True
    send_mode = "hidden"
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
    login_cookies = ("user", "session")  # Shimmie: shm_user / shm_session
    success_url = re.compile(r"/post/view/\d+")

    def _content_refused(self, page: Page) -> str:
        try:
            text = page.inner_text("body")
        except Exception:
            return ""
        m = re.search(r"Invalid tag used:\s*([^\s,]+)", text)
        if m:
            return (f"o paheal recusou a tag \"{m.group(1)}\": ele só aceita série, personagem e artista. "
                    f"Corrija as tags do paheal no editor")
        return ""

    def check_rules(self, post: Post) -> None:
        # Regra 18 do paheal: proibido imagem só com personagem original/OC.
        if post.original_character:
            raise SkipPost("paheal proíbe imagens só com personagem original (regra 18)")


FORM_SITES: dict[str, type[FormSite]] = {
    cls.name: cls for cls in (Rule34xxx, Paheal)
}
