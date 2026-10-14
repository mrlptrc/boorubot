"""Início automático com o Windows.

Grava um .vbs na pasta Inicializar do usuário. O .vbs roda o painel com a janela oculta
(WScript.Shell.Run ..., 0), então nada aparece na tela quando o PC liga. O caminho do `uv`
é resolvido e gravado na hora do install: no logon o PATH pode não ter o `uv` ainda.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from boorubot.sites.browser import ROOT

SCRIPT_NAME = "boorubot-painel.vbs"


class StartupError(RuntimeError):
    pass


@dataclass
class StartupStatus:
    supported: bool
    installed: bool
    path: Path | None
    uv: str = ""


def startup_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if os.name != "nt" or not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def resolve_uv() -> str:
    """Caminho absoluto do uv. Dentro de `uv run`, o próprio uv exporta UV com o seu caminho."""
    candidate = os.environ.get("UV") or shutil.which("uv")
    if not candidate or not Path(candidate).exists():
        raise StartupError("não achei o `uv`; instale o uv ou rode este comando com `uv run boorubot ...`")
    return str(Path(candidate).resolve())


def _vbs_quote(text: str) -> str:
    # em VBScript, aspas dentro de string são duplicadas
    return '"' + text.replace('"', '""') + '"'


def vbs_content(uv: str, project_dir: Path, port: int = 8765) -> str:
    command = f'"{uv}" run boorubot panel --no-browser --port {port}'
    return "\r\n".join([
        "' boorubot: abre o painel escondido quando o Windows inicia.",
        "' Gerado por `boorubot startup install`; para desligar: `boorubot startup remove`.",
        'Set sh = CreateObject("WScript.Shell")',
        f"sh.CurrentDirectory = {_vbs_quote(str(project_dir))}",
        # 0 = janela oculta; False = não espera o painel terminar
        f"sh.Run {_vbs_quote(command)}, 0, False",
        "",
    ])


def install(folder: Path | None = None, uv: str | None = None, project_dir: Path = ROOT,
            port: int = 8765) -> Path:
    folder = folder or startup_dir()
    if folder is None:
        raise StartupError("início automático só funciona no Windows")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / SCRIPT_NAME
    # utf-16 com BOM: o Windows Script Host lê caminhos com acento sem estragar
    target.write_text(vbs_content(uv or resolve_uv(), project_dir, port), encoding="utf-16", newline="")
    return target


def remove(folder: Path | None = None) -> bool:
    folder = folder or startup_dir()
    if folder is None:
        return False
    target = folder / SCRIPT_NAME
    if target.exists():
        target.unlink()
        return True
    return False


def status(folder: Path | None = None) -> StartupStatus:
    folder = folder or startup_dir()
    if folder is None:
        return StartupStatus(supported=False, installed=False, path=None)
    target = folder / SCRIPT_NAME
    if not target.exists():
        return StartupStatus(supported=True, installed=False, path=target)
    text = target.read_text(encoding="utf-16")
    found = re.search(r'sh\.Run """(.*?)""', text)
    uv = found.group(1).replace('""', '"') if found else ""
    return StartupStatus(supported=True, installed=True, path=target, uv=uv)
