"""Danbooru via API oficial.

Fluxo (conferido no código do Danbooru, uploads_controller/posts_controller):
  1. POST /uploads.json        upload[files][0]=<arquivo>
  2. GET  /uploads/{id}.json   até status == completed  -> upload_media_assets[0].id
  3. POST /posts.json          upload_media_asset_id + post[tag_string|rating|source|artist_commentary]

O Cloudflare bloqueia User-Agent de navegador sem JS; com um UA próprio a API responde normal.
Teste antes em https://testbooru.donmai.us (pedido da própria documentação).
"""

from __future__ import annotations

import mimetypes
import time

import httpx

from boorubot.models import Post
from boorubot.sites.base import Result, Site, SiteUnavailable

USER_AGENT = "boorubot/0.1 (post-automation)"
NO_KEY = "sem usuário/chave de API: preencha em Sites → Danbooru (ou DANBOORU_LOGIN/DANBOORU_API_KEY no .env)"
# Nível >= Contributor: post aprovado na hora, sem fila de moderação (User::Levels no Danbooru).
CONTRIBUTOR_LEVEL = 35
RATING_CODES = {"general": "g", "sensitive": "s", "questionable": "q", "explicit": "e"}


class DanbooruError(RuntimeError):
    pass


class DanbooruDown(DanbooruError, SiteUnavailable):
    """Sem chave, chave recusada ou Danbooru fora do ar: pausa o site, não conta contra a arte."""


class Danbooru(Site):
    name = "danbooru"

    def __init__(
        self,
        base_url: str,
        login: str = "",
        api_key: str = "",
        transport: httpx.BaseTransport | None = None,
        poll_interval: float = 2.0,
        poll_timeout: float = 120.0,
        force_pending: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        # Contributor pode pedir para o post passar pela moderação mesmo assim
        self.force_pending = force_pending
        self.login = login
        self.api_key = api_key
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.client = httpx.Client(
            base_url=self.base_url,
            auth=(login, api_key) if login and api_key else None,
            headers={"User-Agent": USER_AGENT},
            timeout=60,
            transport=transport,
        )

    def tags_for(self, post: Post) -> list[str]:
        tags = post.tags_for(self.name)
        if post.original_character and "original" not in tags:
            tags.append("original")
        return tags

    def post_payload(self, post: Post, upload_media_asset_id: int) -> dict[str, str]:
        payload = {
            "upload_media_asset_id": str(upload_media_asset_id),
            "post[tag_string]": " ".join(self.tags_for(post)),
            "post[rating]": RATING_CODES[post.rating],
            "post[source]": post.source,
        }
        if self.force_pending:
            payload["post[is_pending]"] = "1"
        if post.commentary_title or post.commentary_description:
            payload["post[artist_commentary][original_title]"] = post.commentary_title
            payload["post[artist_commentary][original_description]"] = post.commentary_description
        return payload

    def whoami(self) -> dict:
        """Confere as credenciais. Sem login, o Danbooru devolve o usuário anônimo."""
        r = self.client.get("/profile.json")
        r.raise_for_status()
        return r.json()

    def session_check(self) -> Result:
        if not (self.login and self.api_key):
            return Result(self.name, ok=False, dry_run=True, message=NO_KEY)
        try:
            me = self.whoami()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                return Result(self.name, ok=False, dry_run=True, message="usuário ou chave de API inválidos")
            raise
        if me.get("name", "").lower() != self.login.lower():
            return Result(self.name, ok=False, dry_run=True, message="usuário ou chave de API inválidos")
        if me.get("is_banned"):
            return Result(self.name, ok=False, dry_run=True, message="a conta está banida no Danbooru")
        level = int(me.get("level") or 0)
        info = {
            "level": level,
            "level_string": me.get("level_string") or "?",
            "contributor": level >= CONTRIBUTOR_LEVEL,
            "uploads": me.get("post_upload_count"),
        }
        where = "teste (testbooru)" if "testbooru" in self.base_url else "real"
        msg = f"conectado como {me.get('name')} ({info['level_string']}), ambiente {where}"
        return Result(self.name, ok=True, dry_run=True, message=msg, info=info)

    def publish(self, post: Post, dry_run: bool = True) -> Result:
        if dry_run:
            payload = self.post_payload(post, upload_media_asset_id=0)
            lines = [f"{k} = {v}" for k, v in payload.items() if k != "upload_media_asset_id"]
            return Result(
                self.name, ok=True, dry_run=True,
                message=f"enviaria {post.image.name} para {self.base_url}\n  " + "\n  ".join(lines),
            )
        if not (self.login and self.api_key):
            raise DanbooruDown(NO_KEY)

        try:
            asset_id = self._upload_file(post)
        except httpx.TransportError as exc:
            raise DanbooruDown(f"Danbooru fora do ar ou sem internet ({type(exc).__name__}: {exc})") from exc
        except DanbooruError as exc:
            if any(f"({code})" in str(exc) for code in (401, 403)):
                raise DanbooruDown(f"chave de API recusada: confira em Sites → Danbooru ({exc})") from exc
            raise
        r = self.client.post("/posts.json", data=self.post_payload(post, asset_id))
        if r.status_code >= 400:
            raise DanbooruError(f"criar post falhou ({r.status_code}): {r.text[:300]}")
        post_id = r.json()["id"]
        return Result(self.name, ok=True, dry_run=False, message=f"post #{post_id} criado",
                      url=f"{self.base_url}/posts/{post_id}")

    def _upload_file(self, post: Post) -> int:
        mime = mimetypes.guess_type(post.image.name)[0] or "application/octet-stream"
        with post.image.open("rb") as fh:
            r = self.client.post("/uploads.json", files={"upload[files][0]": (post.image.name, fh, mime)})
        if r.status_code >= 400:
            raise DanbooruError(f"upload falhou ({r.status_code}): {r.text[:300]}")
        upload = r.json()

        deadline = time.monotonic() + self.poll_timeout
        while upload.get("status") not in ("completed", "error"):
            if time.monotonic() > deadline:
                raise DanbooruError(f"upload #{upload['id']} não terminou em {self.poll_timeout}s")
            time.sleep(self.poll_interval)
            r = self.client.get(f"/uploads/{upload['id']}.json", params={"include": "upload_media_assets"})
            r.raise_for_status()
            upload = r.json()

        if upload["status"] == "error":
            raise DanbooruError(f"upload #{upload['id']} deu erro: {upload.get('error')}")
        if "upload_media_assets" not in upload:
            r = self.client.get(f"/uploads/{upload['id']}.json", params={"include": "upload_media_assets"})
            r.raise_for_status()
            upload = r.json()
        assets = upload.get("upload_media_assets") or []
        if not assets:
            raise DanbooruError(f"upload #{upload['id']} sem media asset")
        return assets[0]["id"]
