"""boorubot — linha de comando.

  boorubot check [site...]                 reconhecimento sem credenciais
  boorubot login <site>                    login manual no navegador (salva a sessão)
  boorubot discover <site>                 salva os campos do formulário de upload (logado)
  boorubot post <pasta> [--site X] [--send]  dry-run por padrão; --send publica de verdade
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from boorubot.config import ALL_SITES, build_site
from boorubot.models import Post
from boorubot.sites.base import Result, SkipPost


def show(result: Result) -> None:
    mark = "OK  " if result.ok else "FALHA"
    mode = "dry-run" if result.dry_run else "ENVIADO"
    print(f"[{mark}] {result.site} ({mode}): {result.message}")
    if result.url:
        print(f"        {result.url}")
    for path in result.artifacts:
        print(f"        -> {path}")


def cmd_check(args) -> int:
    failures = 0
    for name in args.sites or ALL_SITES:
        site = build_site(name)
        try:
            if name == "danbooru":
                print(f"[....] danbooru: {site.base_url}")
                me = site.whoami()
                level = me.get("level_string", "?")
                print(f"[OK  ] danbooru: API respondeu, usuário={me.get('name', 'anônimo')} nível={level}")
            else:
                result = site.check(headless=not args.headed)
                show(result)
                failures += not result.ok
        except Exception as exc:  # reconhecimento: reporta e segue para o próximo site
            failures += 1
            print(f"[FALHA] {name}: {type(exc).__name__}: {exc}")
    return 1 if failures else 0


def cmd_login(args) -> int:
    show(build_site(args.site).login())
    return 0


def cmd_discover(args) -> int:
    show(build_site(args.site).discover())
    return 0


def cmd_post(args) -> int:
    post = Post.load(Path(args.folder))
    sites = args.site or post.sites or list(ALL_SITES)
    dry_run = not args.send
    if not dry_run:
        print(f"ENVIO REAL para: {', '.join(sites)}")
        if input("confirma? digite 'sim': ").strip().lower() != "sim":
            print("cancelado")
            return 1
    failures = 0
    for name in sites:
        try:
            show(build_site(name).publish(post, dry_run=dry_run))
        except SkipPost as exc:
            print(f"[PULA] {name}: {exc}")
        except Exception as exc:
            failures += 1
            print(f"[FALHA] {name}: {type(exc).__name__}: {exc}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="boorubot")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="reconhecimento sem credenciais")
    p.add_argument("sites", nargs="*", help=f"padrão: todos ({', '.join(ALL_SITES)})")
    p.add_argument("--headed", action="store_true", help="mostra o navegador")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("login", help="login manual, salva sessão em .profiles/")
    p.add_argument("site", choices=[s for s in ALL_SITES if s != "danbooru"])
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("discover", help="salva campos do form de upload (precisa estar logado)")
    p.add_argument("site", choices=[s for s in ALL_SITES if s != "danbooru"])
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("post", help="publica uma pasta da fila (dry-run por padrão)")
    p.add_argument("folder")
    p.add_argument("--site", action="append", choices=ALL_SITES)
    p.add_argument("--send", action="store_true", help="envia de verdade")
    p.set_defaults(func=cmd_post)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
