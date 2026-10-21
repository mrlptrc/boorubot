"""Grava o fluxo principal em vídeo. Uso: record.py <base> <pasta_saida>"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

base, out = sys.argv[1], Path(sys.argv[2])
CURSOR = """
addEventListener('DOMContentLoaded', () => {
  const c = document.createElement('div');
  c.style.cssText = 'position:fixed;z-index:99999;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;' +
    'background:rgba(214,51,108,.35);border:2px solid #d6336c;pointer-events:none;transition:transform .12s;left:-50px;top:-50px';
  document.body.appendChild(c);
  const pos = JSON.parse(sessionStorage.getItem('cur') || '[640,400]');
  c.style.left = pos[0] + 'px'; c.style.top = pos[1] + 'px';
  addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px';
    sessionStorage.setItem('cur', JSON.stringify([e.clientX, e.clientY])); }, true);
  addEventListener('mousedown', () => c.style.transform = 'scale(.7)', true);
  addEventListener('mouseup', () => c.style.transform = '', true);
});
"""
W, H = 1280, 800
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": W, "height": H}, record_video_dir=str(out), record_video_size={"width": W, "height": H})
    ctx.add_init_script(CURSOR)
    pg = ctx.new_page()
    at = [640, 400]

    def go(sel, click=True, wait=500):
        el = pg.locator(sel).first
        el.scroll_into_view_if_needed()
        box = el.bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        pg.mouse.move(at[0], at[1]); pg.mouse.move(x, y, steps=22); at[:] = [x, y]
        pg.wait_for_timeout(250)
        if click:
            pg.mouse.down(); pg.wait_for_timeout(90); pg.mouse.up()
        pg.wait_for_timeout(wait)

    pg.goto(base + "/", wait_until="networkidle"); pg.wait_for_timeout(2200)
    go("nav a[href='/artes']", wait=1600)
    go("text=menina das flores", wait=1800)
    pg.mouse.wheel(0, 250); pg.wait_for_timeout(900)
    go(".bb-rating__opt:has(input[value=general])", wait=1200)
    go("button[data-approve]", wait=2200)
    go("nav a[href='/calendario']", wait=2400)
    go("nav a[href='/metricas']", wait=2600)
    ctx.close(); b.close()
print(next(out.glob("*.webm")))
