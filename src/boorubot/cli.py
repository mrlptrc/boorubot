"""boorubot — linha de comando.

  boorubot check [site...]                 reconhecimento sem credenciais
  boorubot login <site>                    login manual no navegador (salva a sessão)
  boorubot discover <site>                 salva os campos do formulário de upload (logado)
  boorubot post <pasta> [--site X] [--send]  dry-run por padrão; --send publica de verdade
  boorubot panel                           painel web local (http://127.0.0.1:8765)
  boorubot startup install|remove|status   abrir o painel escondido quando o Windows inicia
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
    print(f"[{args.site}] faça o login no navegador (resolva o captcha se tiver) e FECHE a janela ao terminar")
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


def setup_logging() -> Path:
    """Com a janela oculta (início automático) não há console: o log vai para arquivo, com rotação."""
    import logging
    from logging.handlers import RotatingFileHandler

    from boorubot.sites.browser import RUNS_DIR

    RUNS_DIR.mkdir(exist_ok=True)
    log_path = RUNS_DIR / "panel.log"
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    file_handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root = logging.getLogger()
    root.handlers[:] = [file_handler, console]
    root.setLevel(logging.INFO)
    # o painel consulta /status.json a cada 3 s: o log de acesso só poluiria o arquivo
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    return log_path


def panel_running(host: str, port: int) -> bool:
    """Já existe um painel boorubot respondendo nessa porta?"""
    import httpx

    probe = "127.0.0.1" if host in ("0.0.0.0", "") else host
    try:
        r = httpx.get(f"http://{probe}:{port}/status.json", timeout=1.5)
        return r.status_code == 200 and "jobs" in r.json()
    except (httpx.HTTPError, ValueError):
        return False


def port_in_use(host: str, port: int) -> bool:
    import socket

    probe = "127.0.0.1" if host in ("0.0.0.0", "") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex((probe, port)) == 0


def cmd_panel(args) -> int:
    import logging
    import threading
    import webbrowser

    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}"
    # instância única: dois painéis brigariam pelo mesmo banco e pelos perfis de navegador
    if panel_running(args.host, args.port):
        print(f"o painel já está rodando em {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    if port_in_use(args.host, args.port):
        print(f"a porta {args.port} está ocupada por outro programa; use --port para escolher outra")
        return 1

    import uvicorn

    from boorubot.web.app import create_app

    log_path = setup_logging()
    logging.getLogger("boorubot").info("painel iniciando em %s (log: %s)", url, log_path)
    print(f"painel em {url}  (Ctrl+C para parar; log em {log_path})")
    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, (url,)).start()
    uvicorn.run(create_app(), host=args.host, port=args.port, log_config=None)
    return 0


def cmd_startup(args) -> int:
    from boorubot import startup

    try:
        if args.action == "install":
            path = startup.install(port=args.port)
            print(f"ligado: o painel abre escondido quando o Windows iniciar ({path})")
        elif args.action == "remove":
            print("desligado" if startup.remove() else "já estava desligado")
        else:
            st = startup.status()
            if not st.supported:
                print("início automático só funciona no Windows")
            elif st.installed:
                print(f"ligado ({st.path})")
                print(f"  uv: {st.uv}")
            else:
                print("desligado")
    except startup.StartupError as exc:
        print(f"erro: {exc}")
        return 1
    return 0


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

    p = sub.add_parser("panel", help="painel web local")
    p.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 para abrir para a rede local")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_panel)

    p = sub.add_parser("startup", help="abrir o painel escondido quando o Windows inicia")
    p.add_argument("action", choices=["install", "remove", "status"])
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_startup)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
