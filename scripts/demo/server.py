"""Painel de demonstração: sem worker (nada é publicado) e sem rede. Uso: server.py <pasta> <porta>"""
import sys
from pathlib import Path

import httpx
import uvicorn

import boorubot.db as db_mod
from boorubot.tag_api import DanbooruTagAPI
from boorubot.web import app as app_mod
from boorubot.worker import Worker


def offline(self, *a, **k):
    raise httpx.ConnectError("demo sem rede")


DanbooruTagAPI._get = offline
data = Path(sys.argv[1]); port = int(sys.argv[2])
db_mod.IMAGES_DIR = app_mod.IMAGES_DIR = data / "images"
db = db_mod.DB(data / "boorubot.db")
uvicorn.run(app_mod.create_app(db, Worker(db), start_worker=False), host="127.0.0.1", port=port, log_level="warning")
