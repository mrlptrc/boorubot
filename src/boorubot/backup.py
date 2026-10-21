"""Backup diário para uma pasta escolhida (ex.: uma pasta do OneDrive).

- banco: cópia consistente com a API de backup do SQLite (segura com o painel rodando)
- imagens: cópia incremental (só arquivos novos ou alterados; nada é apagado no destino)
- .profiles/ NÃO entra: tem cookies de login; se perder, basta entrar de novo nos sites
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from boorubot.db import DB, IMAGES_DIR, now

DB_FILE = "boorubot.db"


class BackupError(RuntimeError):
    pass


@dataclass
class BackupResult:
    dest: Path
    images_copied: int
    images_total: int


def validate_dest(raw: str) -> Path:
    dest = Path(raw.strip().strip('"'))
    if not raw.strip() or not dest.is_absolute():
        raise BackupError("use o caminho completo da pasta (ex.: C:\\Users\\voce\\OneDrive\\boorubot-backup)")
    return dest


def run_backup(db: DB, dest: Path, images_dir: Path | None = None) -> BackupResult:
    images_dir = images_dir or IMAGES_DIR
    try:
        dest.mkdir(parents=True, exist_ok=True)
        # grava num temporário e troca no fim: um backup interrompido não estraga o anterior
        tmp = dest / (DB_FILE + ".tmp")
        db.backup_to(tmp)
        os.replace(tmp, dest / DB_FILE)

        copied = total = 0
        if images_dir.exists():
            target_dir = dest / "images"
            target_dir.mkdir(exist_ok=True)
            for src in images_dir.iterdir():
                if not src.is_file():
                    continue
                total += 1
                target = target_dir / src.name
                if not target.exists() or target.stat().st_size != src.stat().st_size:
                    shutil.copy2(src, target)
                    copied += 1
    except OSError as exc:
        raise BackupError(f"não consegui gravar em {dest}: {exc}") from exc
    return BackupResult(dest=dest, images_copied=copied, images_total=total)


def backup_now(db: DB) -> BackupResult | None:
    """Roda o backup configurado e registra o resultado nas settings (mostrado na página Sites)."""
    raw = db.setting("backup_dir")
    if not raw:
        return None
    try:
        result = run_backup(db, validate_dest(raw))
    except BackupError as exc:
        db.set_setting("last_backup_ok", "0")
        db.set_setting("last_backup_msg", str(exc))
        raise
    db.set_setting("last_backup_at", now())
    db.set_setting("last_backup_ok", "1")
    db.set_setting("last_backup_msg", f"banco + {result.images_total} imagens ({result.images_copied} novas)")
    return result
