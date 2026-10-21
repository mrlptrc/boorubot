"""Prints da demo. Uso: shots.py <base> <saída> nome=/rota[@celular] ..."""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

base, out = sys.argv[1], Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch()
    for arg in sys.argv[3:]:
        name, path = arg.split("=", 1)
        mobile = path.endswith("@celular"); path = path.removesuffix("@celular")
        pg = b.new_page(viewport={"width": 390, "height": 844} if mobile else {"width": 1280, "height": 800},
                        device_scale_factor=2 if mobile else 1.5)
        pg.goto(base + path, wait_until="networkidle"); pg.wait_for_timeout(400)
        pg.screenshot(path=str(out / f"{name}.png"))
        print(name, "ok"); pg.close()
    b.close()
