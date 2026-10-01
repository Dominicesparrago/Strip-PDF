from __future__ import annotations

import importlib.util
import json
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

from app.backend.api import StudioAPI


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


@unittest.skipUnless(importlib.util.find_spec("playwright") and EDGE.is_file(), "Playwright and Edge are required")
class ThumbnailBrowserTests(unittest.TestCase):
    def test_four_real_photos_render_and_failed_card_retries(self) -> None:
        from playwright.sync_api import sync_playwright

        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            api = StudioAPI(ROOT, work / "app-data")
            api.project_service.autosave_delay = 3600
            try:
                dimensions = [(1920, 1280), (4000, 3000), (1080, 1920), (3000, 2000)]
                sources = []
                for index, size in enumerate(dimensions, 1):
                    source = work / f"photo-{index}.jpg"
                    Image.new("RGB", size, (30 * index, 50, 80)).save(source)
                    sources.append(str(source))
                imported = api._do_import(sources)
                self.assertEqual(len(imported["images"]), 4)
                first_id = imported["images"][0]["id"]
                api_calls = []

                class Handler(SimpleHTTPRequestHandler):
                    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript"}
                    fail_once_id: str | None = None

                    def __init__(self, *args, **kwargs):
                        super().__init__(*args, directory=str(FRONTEND), **kwargs)

                    def log_message(self, *_args):
                        return

                    def do_POST(self):
                        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                        method, params = payload.get("method"), payload.get("params") or {}
                        api_calls.append(method)
                        if method == "images.get_thumbnail" and params.get("imageId") == self.fail_once_id and not params.get("retry"):
                            Handler.fail_once_id = None
                            response = {"ok": True, "data": {"imageId": first_id, "dataUrl": None}}
                        else:
                            response = api.invoke(method, params)
                        body = json.dumps(response).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)

                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with sync_playwright() as playwright:
                        browser = playwright.chromium.launch(executable_path=str(EDGE), headless=True, args=["--no-sandbox"])
                        try:
                            page = browser.new_page(viewport={"width": 1366, "height": 768})
                            errors = []
                            page.on("pageerror", lambda error: errors.append((error.stack or str(error))[:1200]))
                            page.add_init_script("""window.pywebview = {api: {invoke: async (method, params = {}) => {
                              const response = await fetch('/bridge', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({method, params})});
                              return response.json();
                            }}};""")
                            page.goto(f"http://127.0.0.1:{server.server_port}/index.html", wait_until="load")
                            page.wait_for_function("document.querySelectorAll('.image-grid .image-item').length === 4")
                            page.locator('[data-nav="images"]').click()
                            try:
                                page.wait_for_function("""() => [...document.querySelectorAll('.image-grid .image-item')].every(card =>
                                  card.dataset.thumbnailState === 'ready' && card.querySelector('img')?.naturalWidth > 0)""", timeout=8000)
                            except Exception as error:
                                cards = page.evaluate("""() => [...document.querySelectorAll('.image-grid .image-item')].map(card => ({
                                  id: card.dataset.imageId, state: card.dataset.thumbnailState,
                                  imgState: card.querySelector('img')?.dataset.thumbnailState,
                                  complete: card.querySelector('img')?.complete,
                                  width: card.querySelector('img')?.naturalWidth,
                                  hasSrc: !!card.querySelector('img')?.getAttribute('src'),
                                  rect: card.getBoundingClientRect().toJSON()
                                }))""")
                                view = page.evaluate("""() => ({active: document.body.dataset.activeView,
                                  section: document.querySelector('[data-view=images]')?.className,
                                  observer: typeof IntersectionObserver})""")
                                self.fail(f"Thumbnail cards did not become ready: {cards}; view={view}; calls={api_calls}; page errors={errors}; status={page.locator('[data-bind=app-status]').inner_text()}; {error}")
                            self.assertEqual(errors[:2], [])

                            Handler.fail_once_id = first_id
                            calls_before_reload = len(api_calls)
                            page.reload(wait_until="load")
                            page.locator('[data-nav="images"]').click()
                            try:
                                page.wait_for_function("id => document.querySelector(`.image-grid .image-item[data-image-id='${id}']`)?.dataset.thumbnailState === 'failed'", arg=first_id, timeout=8000)
                            except Exception as error:
                                card_state = page.evaluate("""id => {
                                  const card = document.querySelector(`.image-grid .image-item[data-image-id='${id}']`);
                                  return {state: card?.dataset.thumbnailState, imgState: card?.querySelector('img')?.dataset.thumbnailState,
                                    width: card?.querySelector('img')?.naturalWidth, hasSrc: !!card?.querySelector('img')?.src,
                                    cards: [...document.querySelectorAll('.image-grid .image-item')].map(node => ({id: node.dataset.imageId, state: node.dataset.thumbnailState}))};
                                }""", first_id)
                                self.fail(f"Forced failure was not shown: {card_state}; calls={api_calls[calls_before_reload:]}; failId={Handler.fail_once_id}; errors={errors}; {error}")
                            card = page.locator(f'.image-grid .image-item[data-image-id="{first_id}"]')
                            self.assertIn("Thumbnail failed", card.inner_text())
                            card.locator('[data-action="image-thumbnail-retry"]').click()
                            page.wait_for_function("""id => {
                              const card = document.querySelector(`.image-grid .image-item[data-image-id='${id}']`);
                              return card?.dataset.thumbnailState === 'ready' && card.querySelector('img')?.naturalWidth > 0;
                            }""", arg=first_id, timeout=20000)
                            self.assertEqual(errors[:2], [])
                        finally:
                            browser.close()
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)
            finally:
                api.close()


if __name__ == "__main__":
    unittest.main()
