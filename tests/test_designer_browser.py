from __future__ import annotations

import importlib.util
import json
import tempfile
import threading
import time
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.backend.api import StudioAPI

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
CHROME_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
)
CHROME = next((path for path in CHROME_CANDIDATES if path.is_file()), None)


@unittest.skipUnless(importlib.util.find_spec("playwright") and CHROME, "Playwright and Chrome/Edge are required for browser interaction checks")
class DesignerBrowserInteractionTests(unittest.TestCase):
    def setUp(self) -> None:
        from PIL import Image

        self.temporary = tempfile.TemporaryDirectory()
        self.work = Path(self.temporary.name)
        self.source = self.work / "test-photo.png"
        photo = Image.new("RGB", (900, 1200))
        for y in range(photo.height):
            for x in range(photo.width):
                photo.putpixel((x, y), ((x * 255 // 900), (y * 255 // 1200), 180))
        photo.save(self.source)
        self.api = StudioAPI(ROOT, self.work / "app-data")
        # Browser startup and interaction tests should not race the product's
        # delayed recovery write. Recovery-specific tests create snapshots
        # explicitly with project_service.autosave().
        self.api.project_service.autosave_delay = 3600
        result = self.api.invoke("images.import_paths", {"paths": [str(self.source)]})
        self.assertTrue(result["ok"], result)
        job_id = result["data"].get("jobId")
        if job_id:
            result = self._wait_job(job_id)
        image_id = result["data"]["images"][0]["id"]
        generated = self.api.invoke("strips.generate", {"slotsPerStrip": 4, "incomplete": "empty"})
        self.assertTrue(generated["ok"], generated)
        added = self.api.invoke("design.add_element", {"type": "shape", "x": 180, "y": 600, "width": 220, "height": 150,
            "fill": "#3878e8", "borderColor": "#c4242b", "borderWidth": 8, "borderRadius": 32})
        self.assertTrue(added["ok"], added)
        self.shape_id = added["data"]["element"]["id"]
        self.strip_id = self.api._project["strips"][0]["id"]
        self.export_path = self.work / "designer-export.pdf"
        self.api.project_service.clear_recovery()

        api = self.api
        export_path = self.export_path
        self.api_calls: list[str] = []
        self.api_requests: list[dict] = []
        api_call_log = self.api_calls
        api_request_log = self.api_requests

        class Handler(SimpleHTTPRequestHandler):
            extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript"}
            call_log = api_call_log
            request_log = api_request_log

            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(FRONTEND), **kwargs)

            def log_message(self, format, *args):
                return

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                method = payload.get("method", "")
                self.call_log.append(method)
                self.request_log.append(payload)
                params = payload.get("params") or {}
                if method == "print.export" and not params.get("path"):
                    params["path"] = str(export_path)
                result = api.invoke(method, params)
                body = json.dumps(result).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/index.html"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)
        self.api.close()
        self.temporary.cleanup()

    def _wait_job(self, job_id: str) -> dict:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = self.api.invoke("jobs.get", {"jobId": job_id})
            self.assertTrue(result["ok"], result)
            job = result["data"]
            if job["status"] == "completed":
                return {"ok": True, "data": job["result"]}
            if job["status"] in {"failed", "cancelled"}:
                self.fail(f"Background job failed: {job}")
            time.sleep(0.03)
        self.fail("Image import timed out")

    def test_designer_pointer_and_layers_interactions_in_light_and_dark_viewports(self) -> None:
        from playwright.sync_api import sync_playwright

        bridge_script = """
          window.pywebview = {api: {invoke: async (method, params = {}) => {
            const response = await fetch('/bridge', {method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({method, params})});
            return response.json();
          }}};
        """
        errors: list[str] = []
        console_logs: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True, args=["--no-sandbox"])
            try:
                page = browser.new_page(viewport={"width": 1366, "height": 768}, device_scale_factor=1)
                page.add_init_script(bridge_script)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: console_logs.append(f"{message.type}: {message.text}"))
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto(self.url, wait_until="load")
                page.wait_for_function("document.body.classList.contains('is-booted')", timeout=15000)
                page.wait_for_selector('[data-selection-chip]', state="attached")
                for width, height, theme in ((1366, 768, "light"), (1366, 768, "dark"), (1920, 1080, "light"), (1920, 1080, "dark")):
                    page.set_viewport_size({"width": width, "height": height})
                    page.evaluate("""mode => { const node = document.querySelector('[data-action="theme-set"]'); node.value = mode; node.dispatchEvent(new Event('change', {bubbles: true})); }""", theme)
                    page.wait_for_function("mode => document.documentElement.dataset.theme === mode", arg=theme)
                    page.locator('[data-nav="designer"]').click()
                    shape_row = page.locator(f'[data-layer-id="{self.shape_id}"]')
                    try:
                        page.wait_for_function("id => [...(document.querySelector('[data-bind=design-element-list]')?.options || [])].some(option => option.value === id)", arg=self.shape_id, timeout=8000)
                    except Exception as error:
                        diagnostic = page.evaluate("""async () => ({bridge:!!window.pywebview?.api,options:[...(document.querySelector('[data-bind=design-element-list]')?.options||[])].map(x=>x.value),status:document.querySelector('[data-bind=app-status]')?.textContent,view:document.body.dataset.activeView,project:document.querySelector('[data-bind=project-name]')?.textContent,snapshot:window.__studio_design_snapshot,backend:await window.pywebview.api.invoke('app.get_state',{})})""")
                        self.fail(f"Designer layer did not appear; seeded={ [x['id'] for x in self.api._project['design']['elements'] ] }, diagnostic={diagnostic}, pageErrors={errors}, console={console_logs}: {error}")
                    shape_row.wait_for(state="visible", timeout=8000)
                    page.evaluate("""() => {
                      window.__selectionEvents = [];
                      window.__stateEvents = [];
                      document.addEventListener('studio:element-selected', event => window.__selectionEvents.push(event.detail?.id ?? null));
                      document.addEventListener('studio:state-change', event => window.__stateEvents.push(event.detail));
                    }""")
                    shape_row.click()
                    page.wait_for_function("id => document.querySelector('[data-bind=design-element-list]').value === id", arg=self.shape_id)
                    self.assertEqual(page.locator('[data-selection-chip]').get_attribute("data-status"), "selected")
                    self.assertEqual(page.evaluate("window.__selectionEvents.at(-1)"), self.shape_id)
                    state_event = page.evaluate("window.__stateEvents.at(-1)")
                    self.assertIsInstance(state_event.get("canUndo"), bool)
                    self.assertIsInstance(state_event.get("canRedo"), bool)
                    history = self.api.invoke("app.get_state", {})["data"]["history"]
                    self.assertEqual(state_event["canUndo"], history["canUndo"])
                    self.assertEqual(state_event["canRedo"], history["canRedo"])
                    self.assertEqual(page.locator('[data-action="design-undo"]').is_disabled(), not history["canUndo"])
                    self.assertEqual(page.locator('[data-action="design-redo"]').is_disabled(), not history["canRedo"])
                    page.wait_for_function("id => document.querySelector(`[data-layer-id=\"${id}\"]`)?.classList.contains('is-selected')", arg=self.shape_id)
                    page.wait_for_function("document.querySelector('[data-inspector-target]')?.textContent.length > 0")
                    self.assertEqual(page.locator('[data-inspector-target]').inner_text(), page.locator('[data-selection-label]').inner_text())
                    self.assertEqual(page.locator(f'[data-layer-id="{self.shape_id}"]').get_attribute("class").find("is-selected") >= 0, True)
                    page.evaluate("""() => { window.__pointerTrace=[]; for (const name of ['pointerdown','pointermove','pointerup','pointercancel','gotpointercapture','lostpointercapture']) document.addEventListener(name,e=>window.__pointerTrace.push({name,x:e.clientX,y:e.clientY,id:e.pointerId,target:e.target.tagName}),true); }""")
                    page.locator('[data-action="design-snap"]').evaluate("node => { node.checked = false; node.dispatchEvent(new Event('change', {bubbles:true})); }")
                    page.wait_for_function("!document.querySelector('[data-action=design-snap]').checked")
                    page.locator('[data-vaction="zoom-fit"]').click()

                    directions = {"nw": (-9, -9), "n": (0, -9), "ne": (9, -9), "w": (-9, 0),
                                  "e": (9, 0), "sw": (-9, 9), "s": (0, 9), "se": (9, 9)}
                    for direction, (dx, dy) in directions.items():
                        before = page.evaluate("""id => { const e = window.__studio_design_snapshot.elements.find(item => item.id === id); return {x:e.x,y:e.y,width:e.width,height:e.height}; }""", self.shape_id)
                        handle = page.locator(f'#designer-overlay .overlay-handle[data-dir="{direction}"]')
                        handle.wait_for(state="attached")
                        point = page.evaluate("""dir => { const node=document.querySelector(`#designer-overlay .overlay-handle[data-dir="${dir}"]`),r=node.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2}; }""", direction)
                        self.assertIsNotNone(point)
                        page.mouse.move(point["x"], point["y"])
                        page.mouse.down()
                        page.mouse.move(point["x"] + dx, point["y"] + dy, steps=3)
                        page.mouse.up()
                        try:
                            page.wait_for_function("""arg => { const e = window.__studio_design_snapshot.elements.find(item => item.id === arg.id); return Math.abs(e.width-arg.width)>1 || Math.abs(e.height-arg.height)>1 || Math.abs(e.x-arg.x)>1 || Math.abs(e.y-arg.y)>1; }""", arg={"id": self.shape_id, **before}, timeout=3000)
                        except Exception as error:
                            diagnostic = page.evaluate("""id => {const c=document.querySelector('[data-bind=designer-canvas]'),r=c.getBoundingClientRect(),root=c.parentElement,e=window.__studio_design_snapshot.elements.find(item=>item.id===id),h=document.querySelector('#designer-overlay .overlay-handle');return {shape:e,canvas:r.toJSON(),root:root.getBoundingClientRect().toJSON(),handle:h?.dataset.dir,handleRect:h?.getBoundingClientRect().toJSON(),debug:window.__designerPointerDebug,trace:window.__pointerTrace.slice(-12)}}""", self.shape_id)
                            self.fail(f"Resize {direction} at {point} produced no geometry delta; before={before}, diagnostic={diagnostic}, calls={self.api_calls[-10:]}, console={console_logs}: {error}")
                        after = page.evaluate("""id => { const e = window.__studio_design_snapshot.elements.find(item => item.id === id); return {x:e.x,y:e.y,width:e.width,height:e.height}; }""", self.shape_id)
                        page.locator('[data-action="design-undo"]').click()
                        page.wait_for_function("""arg => { const e = window.__studio_design_snapshot.elements.find(item => item.id === arg.id); return Math.abs(e.width-arg.width)<.02 && Math.abs(e.height-arg.height)<.02 && Math.abs(e.x-arg.x)<.02 && Math.abs(e.y-arg.y)<.02; }""", arg={"id": self.shape_id, **before})
                        page.locator('[data-action="design-redo"]').click()
                        page.wait_for_function("""arg => { const e = window.__studio_design_snapshot.elements.find(item => item.id === arg.id); return Math.abs(e.width-arg.width)<.02 && Math.abs(e.height-arg.height)<.02 && Math.abs(e.x-arg.x)<.02 && Math.abs(e.y-arg.y)<.02; }""", arg={"id": self.shape_id, **after})
                        page.locator('[data-action="design-undo"]').click()
                        page.wait_for_function("""arg => { const e = window.__studio_design_snapshot.elements.find(item => item.id === arg.id); return Math.abs(e.width-arg.width)<.02 && Math.abs(e.height-arg.height)<.02 && Math.abs(e.x-arg.x)<.02 && Math.abs(e.y-arg.y)<.02; }""", arg={"id": self.shape_id, **before})

                    page.locator('[data-action="design-aspect-lock"]').check()
                    aspect_before = page.evaluate("""id => { const e=window.__studio_design_snapshot.elements.find(item=>item.id===id); return e.width/e.height; }""", self.shape_id)
                    point = page.evaluate("""id => {const c=document.querySelector('[data-bind=designer-canvas]'),r=c.getBoundingClientRect(),e=window.__studio_design_snapshot.elements.find(item=>item.id===id),a=window.__studio_design_snapshot.artboard;return {x:r.left+(e.x+e.width)/a.width*r.width,y:r.top+(e.y+e.height)/a.height*r.height};}""", self.shape_id)
                    page.mouse.move(point["x"] - 3, point["y"] - 3); page.mouse.down(); page.mouse.move(point["x"] + 13, point["y"], steps=3); page.mouse.up()
                    page.wait_for_timeout(100)
                    aspect_after = page.evaluate("""id => { const e=window.__studio_design_snapshot.elements.find(item=>item.id===id); return e.width/e.height; }""", self.shape_id)
                    self.assertAlmostEqual(aspect_before, aspect_after, places=3)

                    page.locator('[data-action="element-lock"]').click()
                    page.wait_for_function("id => document.querySelector(`[data-layer-id=\"${id}\"]`)?.classList.contains('is-locked')", arg=self.shape_id)
                    self.assertEqual(page.locator('[data-selection-chip]').get_attribute("data-status"), "locked")
                    locked_state = page.evaluate("""id => { const e=window.__studio_design_snapshot.elements.find(item=>item.id===id); return {x:e.x,y:e.y,width:e.width,height:e.height}; }""", self.shape_id)
                    bounds = page.evaluate("""id => { const c=document.querySelector('[data-bind=designer-canvas]'),r=c.getBoundingClientRect(),e=window.__studio_design_snapshot.elements.find(item=>item.id===id),a=window.__studio_design_snapshot.artboard; return {x:r.left+e.x/a.width*r.width,y:r.top+e.y/a.height*r.height}; }""", self.shape_id)
                    page.mouse.click(bounds["x"], bounds["y"])
                    locked_after = page.evaluate("""id => { const e=window.__studio_design_snapshot.elements.find(item=>item.id===id); return {x:e.x,y:e.y,width:e.width,height:e.height}; }""", self.shape_id)
                    self.assertEqual(locked_state, locked_after)
                    page.locator(f'[data-layer-id="{self.shape_id}"]').click()
                    self.assertEqual(page.evaluate("document.querySelector('[data-bind=design-element-list]').value"), self.shape_id)
                    self.assertEqual(page.locator('#designer-overlay .overlay-handle').count(), 0)
                    page.locator('[data-action="element-lock"]').click()
                    try:
                        page.wait_for_function("id => !document.querySelector(`[data-layer-id=\"${id}\"]`)?.classList.contains('is-locked')", arg=self.shape_id, timeout=4000)
                    except Exception as error:
                        diagnostic = page.evaluate("""id => ({selected:document.querySelector('[data-bind=design-element-list]').value,locked:window.__studio_design_snapshot.elements.find(item=>item.id===id).locked,button:document.querySelector('[data-action=element-lock]')?.outerHTML,inspectorDisabled:document.querySelector('[data-panel=design-properties]')?.disabled,status:document.querySelector('[data-bind=app-status]')?.textContent})""", self.shape_id)
                        self.fail(f"Unlock interaction did not complete: {diagnostic}, calls={self.api_calls[-10:]}, requests={self.api_requests[-4:]}, console={console_logs}: {error}")

                    # Find a genuinely empty artboard patch and verify selection clears across mirrors.
                    page.locator('[data-vaction="zoom-fit"]').click()
                    blank = page.evaluate("""() => {const d=window.__studio_design_snapshot,a=d.artboard,c=document.querySelector('[data-bind=designer-canvas]'),r=c.getBoundingClientRect(); for(let y=40;y<a.height-40;y+=20) for(let x=40;x<a.width-40;x+=20) if(!d.elements.some(e=>e.type!=='background'&&e.visible!==false&&x>=e.x&&x<=e.x+e.width&&y>=e.y&&y<=e.y+e.height)) return {x:r.left+x/a.width*r.width,y:r.top+y/a.height*r.height}; return null;}""")
                    self.assertIsNotNone(blank, "test project should contain a true blank artboard area")
                    page.mouse.click(blank["x"], blank["y"])
                    page.wait_for_function("document.querySelector('[data-bind=design-element-list]').value === ''")
                    self.assertEqual(page.locator('[data-selection-chip]').get_attribute("data-status"), "none")
                    self.assertEqual(page.locator('[data-inspector-target]').inner_text(), "Nothing selected")
                    self.assertFalse(page.locator('.layer-row.is-selected').count())
                    self.assertIsNone(page.evaluate("window.__selectionEvents.at(-1)"))
                    self.assertIsNone(page.evaluate("window.__stateEvents.at(-1).selectedElementId"))

                    # Exercise current-rectangle mapping with the stage scrolled at 50% and 150%.
                    shape_row = page.locator(f'[data-layer-id="{self.shape_id}"]'); shape_row.click()
                    page.locator('[data-vaction="zoom-out"]').click(click_count=5)
                    self.assertEqual(page.locator('[data-bind="zoom-level"]').inner_text(), "50%")
                    stage = page.locator('[data-panel="designer-canvas-container"]')
                    stage.evaluate("node => { node.scrollLeft = 0; node.scrollTop = 0; }")
                    box = page.evaluate("""id => { const c=document.querySelector('[data-bind=designer-canvas]'),r=c.getBoundingClientRect(),e=window.__studio_design_snapshot.elements.find(item=>item.id===id),a=window.__studio_design_snapshot.artboard; return {x:r.left+(e.x+e.width/2)/a.width*r.width,y:r.top+(e.y+e.height/2)/a.height*r.height}; }""", self.shape_id)
                    geometry_before_50 = page.evaluate("""id => {const e=window.__studio_design_snapshot.elements.find(item=>item.id===id);return {x:e.x,y:e.y,width:e.width,height:e.height};}""", self.shape_id)
                    before_move = geometry_before_50["x"]
                    page.mouse.move(box["x"], box["y"]); page.mouse.down(); page.mouse.move(box["x"] + 12, box["y"] + 8, steps=3); page.mouse.up()
                    page.wait_for_function("arg => window.__studio_design_snapshot.elements.find(item=>item.id===arg.id).x !== arg.x", arg={"id": self.shape_id, "x": before_move})
                    geometry_after_50_move = page.evaluate("""id => {const e=window.__studio_design_snapshot.elements.find(item=>item.id===id);return {x:e.x,y:e.y,width:e.width,height:e.height};}""", self.shape_id)
                    point = page.evaluate("""id => {const c=document.querySelector('[data-bind=designer-canvas]'),r=c.getBoundingClientRect(),e=window.__studio_design_snapshot.elements.find(item=>item.id===id),a=window.__studio_design_snapshot.artboard;return {x:r.left+(e.x+e.width)/a.width*r.width,y:r.top+(e.y+e.height/2)/a.height*r.height};}""", self.shape_id)
                    size_before = page.evaluate("""id => window.__studio_design_snapshot.elements.find(item=>item.id===id).width""", self.shape_id)
                    page.mouse.move(point["x"] - 2, point["y"]); page.mouse.down(); page.mouse.move(point["x"] + 10, point["y"], steps=3); page.mouse.up()
                    page.wait_for_function("arg => window.__studio_design_snapshot.elements.find(item=>item.id===arg.id).width !== arg.width", arg={"id": self.shape_id, "width": size_before})
                    page.locator('[data-action="design-undo"]').click()
                    page.wait_for_function("""arg => {const e=window.__studio_design_snapshot.elements.find(item=>item.id===arg.id);return Math.abs(e.x-arg.x)<.02&&Math.abs(e.y-arg.y)<.02&&Math.abs(e.width-arg.width)<.02&&Math.abs(e.height-arg.height)<.02;}""", arg={"id": self.shape_id, **geometry_after_50_move})
                    page.locator('[data-action="design-undo"]').click()
                    page.wait_for_function("""arg => {const e=window.__studio_design_snapshot.elements.find(item=>item.id===arg.id);return Math.abs(e.x-arg.x)<.02&&Math.abs(e.y-arg.y)<.02&&Math.abs(e.width-arg.width)<.02&&Math.abs(e.height-arg.height)<.02;}""", arg={"id": self.shape_id, **geometry_before_50})

                    page.locator('[data-vaction="zoom-fit"]').click()
                    page.locator('[data-vaction="zoom-in"]').click(click_count=5)
                    self.assertEqual(page.locator('[data-bind="zoom-level"]').inner_text(), "150%")
                    stage.evaluate("node => { node.scrollLeft = Math.max(0, Math.min(180, node.scrollWidth-node.clientWidth)); node.scrollTop = Math.max(0, Math.min(180, node.scrollHeight-node.clientHeight)); }")
                    point = page.evaluate("""id => {const c=document.querySelector('[data-bind=designer-canvas]'),r=c.getBoundingClientRect(),e=window.__studio_design_snapshot.elements.find(item=>item.id===id),a=window.__studio_design_snapshot.artboard;return {x:r.left+(e.x+e.width/2)/a.width*r.width,y:r.top+(e.y+e.height/2)/a.height*r.height};}""", self.shape_id)
                    before_150_move = page.evaluate("""id => window.__studio_design_snapshot.elements.find(item=>item.id===id).x""", self.shape_id)
                    page.mouse.move(point["x"], point["y"]); page.mouse.down(); page.mouse.move(point["x"] + 8, point["y"] + 6, steps=3); page.mouse.up()
                    page.wait_for_function("arg => window.__studio_design_snapshot.elements.find(item=>item.id===arg.id).x !== arg.x", arg={"id": self.shape_id, "x": before_150_move})
                    point = page.evaluate("""async () => {await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); const h=document.querySelector('#designer-overlay .overlay-handle[data-dir=se]'),r=h.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2};}""")
                    size_before = page.evaluate("""id => window.__studio_design_snapshot.elements.find(item=>item.id===id).width""", self.shape_id)
                    page.mouse.move(point["x"], point["y"]); page.mouse.down(); page.mouse.move(point["x"] + 10, point["y"] + 8, steps=3); page.mouse.up()
                    try:
                        page.wait_for_function("arg => window.__studio_design_snapshot.elements.find(item=>item.id===arg.id).width !== arg.width", arg={"id": self.shape_id, "width": size_before}, timeout=3000)
                    except Exception as error:
                        diagnostic = page.evaluate("""p => ({trace:window.__pointerTrace.slice(-12),hit:document.elementFromPoint(p.x,p.y)?.tagName,selected:document.querySelector('[data-bind=design-element-list]').value,shape:window.__studio_design_snapshot.elements.find(e=>e.id===document.querySelector('[data-bind=design-element-list]').value),canvas:document.querySelector('[data-bind=designer-canvas]').getBoundingClientRect().toJSON(),root:document.querySelector('[data-bind=designer-canvas]').parentElement.getBoundingClientRect().toJSON(),handle:document.querySelector('#designer-overlay .overlay-handle[data-dir=se]')?.getBoundingClientRect().toJSON()})""", point)
                        self.fail(f"150% scrolled resize failed at handle {point}: {diagnostic}, calls={self.api_calls[-8:]}, console={console_logs}: {error}")

                    # Cover crop, contain free-space panning, and the actual PDF bridge all use current structured state.
                    page.locator('[data-vaction="zoom-fit"]').click()
                    page.locator(f'[data-layer-id="photo-1"]').click()
                    frame_before_alt = page.evaluate("""() => {const e=window.__studio_design_snapshot.elements.find(item=>item.id==='photo-1');return {x:e.x,y:e.y};}""")
                    crop_before_alt = self.api._project["strips"][0]["slots"][0]["crop"].copy()
                    alt_point = page.evaluate("""() => {const c=document.querySelector('[data-bind=designer-canvas]'),r=c.getBoundingClientRect(),e=window.__studio_design_snapshot.elements.find(item=>item.id==='photo-1'),a=window.__studio_design_snapshot.artboard;return {x:r.left+(e.x+e.width/2)/a.width*r.width,y:r.top+(e.y+e.height/2)/a.height*r.height};}""")
                    page.keyboard.down("Alt"); page.mouse.move(alt_point["x"], alt_point["y"]); page.mouse.down(); page.mouse.move(alt_point["x"] + 9, alt_point["y"] + 5, steps=3); page.mouse.up(); page.keyboard.up("Alt")
                    frame_after_alt = page.evaluate("""() => {const e=window.__studio_design_snapshot.elements.find(item=>item.id==='photo-1');return {x:e.x,y:e.y};}""")
                    self.assertEqual(frame_before_alt, frame_after_alt)
                    self.assertEqual(self.api._project["strips"][0]["slots"][0]["crop"].get("offsetX", 0), crop_before_alt.get("offsetX", 0))
                    self.assertTrue(page.locator('[data-action="frame-unlock"]').is_visible())
                    page.locator('[data-action="frame-unlock"]').click()
                    page.wait_for_function("!window.__studio_design_snapshot.elements.find(e=>e.id==='photo-1').locked")
                    # Original mode now matches this portrait source, so Cover
                    # has no free vertical pan axis. Select a fixed square
                    # frame before exercising intentional Cover cropping.
                    page.locator('details.inspector-advanced').evaluate("node => { node.open = true; }")
                    page.locator('[data-bind="image-ratio"]').select_option("1:1")
                    page.wait_for_function("window.__studio_design_snapshot.elements.find(e=>e.id==='photo-1').aspectRatio === '1:1'")
                    page.locator('[data-action="frame-unlock"]').click()
                    page.wait_for_function("window.__studio_design_snapshot.elements.find(e=>e.id==='photo-1').locked")
                    page.locator('[data-action="crop-fill"]').click()
                    crop_box = page.evaluate("""() => {const c=document.querySelector('[data-bind=designer-canvas]'),r=c.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height*.12};}""")
                    before_crop = self.api._project["strips"][0]["slots"][0]["crop"].copy()
                    page.mouse.move(crop_box["x"], crop_box["y"]); page.mouse.down(); page.mouse.move(crop_box["x"], crop_box["y"] + 14, steps=3); page.mouse.up()
                    page.wait_for_function("""() => { const r=window.__studio_design_snapshot; return true; }""")
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline and self.api._project["strips"][0]["slots"][0]["crop"].get("offsetY", 0) == before_crop.get("offsetY", 0):
                        time.sleep(0.03)
                    cover_crop = self.api._project["strips"][0]["slots"][0]["crop"]
                    self.assertNotEqual(cover_crop.get("offsetY", 0), before_crop.get("offsetY", 0))
                    page.locator('[data-action="crop-reset"]').click()
                    page.locator('[data-action="crop-fit"]').click()
                    page.wait_for_function("""() => document.querySelector('[data-bind=crop-mode]').value==='contain'""")
                    self.assertEqual(self.api._project["strips"][0]["slots"][0]["crop"]["mode"], "contain")
                    page.locator('[data-bind="crop-zoom"]').evaluate("node => { node.value='2'; node.dispatchEvent(new Event('input',{bubbles:true})); }")
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline and self.api._project["strips"][0]["slots"][0]["crop"].get("zoom") != 2:
                        time.sleep(0.03)
                    page.wait_for_function("document.querySelector('[data-bind=crop-zoom]').value === '2'")
                    contain_before = self.api._project["strips"][0]["slots"][0]["crop"].copy()
                    page.mouse.move(crop_box["x"], crop_box["y"]); page.mouse.down(); page.mouse.move(crop_box["x"] + 12, crop_box["y"] + 8, steps=3); page.mouse.up()
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline and self.api._project["strips"][0]["slots"][0]["crop"].get("offsetX", 0) == contain_before.get("offsetX", 0):
                        time.sleep(0.03)
                    crop_diagnostic = page.evaluate("""() => ({selected:document.querySelector('[data-bind=design-element-list]').value,mode:document.querySelector('[data-bind=crop-mode]').value,zoom:document.querySelector('[data-bind=crop-zoom]').value,trace:window.__pointerTrace.slice(-10),design:window.__studio_design_snapshot.elements.find(e=>e.id===document.querySelector('[data-bind=design-element-list]').value)})""")
                    self.assertNotEqual(self.api._project["strips"][0]["slots"][0]["crop"].get("offsetX", 0), contain_before.get("offsetX", 0),
                        f"contain drag did not alter offsets: before={contain_before}, after={self.api._project['strips'][0]['slots'][0]['crop']}, detail={crop_diagnostic}, calls={self.api_calls[-10:]}")

                    # Compare one rendered photo pixel against the shared strip asset used by preview/PDF.
                    from PIL import Image
                    from app.backend.strip_renderer import render_strips
                    render_path = Path(render_strips(self.api._project["strips"], self.api._project["images"],
                        self.api._project["design"], self.api._project["printSettings"], self.work / "designer-parity-cache")[0]["renderPath"])
                    frame_info = page.evaluate("""() => {const d=window.__studio_design_snapshot,e=d.elements.find(item=>item.id==='photo-1');return {frame:e,artboard:d.artboard};}""")
                    frame = frame_info["frame"]
                    artboard = frame_info["artboard"]
                    sample_points = [(frame["x"] + frame["width"] * x, frame["y"] + frame["height"] * y)
                                     for y in (0.25, 0.5, 0.75) for x in (0.25, 0.5, 0.75)]
                    page.wait_for_function("""() => {const d=window.__studio_design_snapshot,e=d.elements.find(item=>item.id==='photo-1'),c=document.querySelector('[data-bind=designer-canvas]'),p=c.getContext('2d').getImageData(Math.round((e.x+e.width/2)*c.width/d.artboard.width),Math.round((e.y+e.height/2)*c.height/d.artboard.height),1,1).data;return p[2]>120&&p[1]>20;}""")
                    canvas_pixels = page.evaluate("""points => {const c=document.querySelector('[data-bind=designer-canvas]'),ctx=c.getContext('2d'),a=window.__studio_design_snapshot.artboard;return points.map(([x,y])=>[...ctx.getImageData(Math.round(x*c.width/a.width),Math.round(y*c.height/a.height),1,1).data].slice(0,3));}""", sample_points)
                    shape_border = page.evaluate("""id => {const c=document.querySelector('[data-bind=designer-canvas]'),ctx=c.getContext('2d'),e=window.__studio_design_snapshot.elements.find(item=>item.id===id),s=Math.min(c.width/window.__studio_design_snapshot.artboard.width,c.height/window.__studio_design_snapshot.artboard.height),pixel=(x,y)=>[...ctx.getImageData(Math.round(x*s),Math.round(y*s),1,1).data].slice(0,3);return {edge:pixel(e.x+e.width/2,e.y+1),corner:pixel(e.x+2,e.y+2),x:e.x,y:e.y,width:e.width,height:e.height};}""", self.shape_id)
                    with Image.open(render_path) as rendered:
                        raster = rendered.convert("RGB")
                        strip_pixels = [raster.getpixel((round(raster.width * x / artboard["width"]), round(raster.height * y / artboard["height"]))) for x, y in sample_points]
                        scale_x = raster.width / artboard["width"]
                        border_edge = raster.getpixel((round((shape_border["x"] + shape_border["width"] / 2) * scale_x), round((shape_border["y"] + 1) * scale_x)))
                        border_corner = raster.getpixel((round((shape_border["x"] + 2) * scale_x), round((shape_border["y"] + 2) * scale_x)))
                        raster.close()
                    differences = [max(abs(canvas[channel] - strip[channel]) for channel in range(3)) for canvas, strip in zip(canvas_pixels, strip_pixels)]
                    self.assertLessEqual(max(differences), 32,
                        f"Designer canvas and shared strip render differ at contain image samples: canvas={canvas_pixels}, strip={strip_pixels}")
                    border_ink = (196, 36, 43)
                    self.assertLessEqual(max(abs(shape_border["edge"][i] - border_ink[i]) for i in range(3)), 24)
                    self.assertGreater(max(abs(shape_border["corner"][i] - border_ink[i]) for i in range(3)), 40)
                    self.assertLessEqual(max(abs(border_edge[i] - border_ink[i]) for i in range(3)), 28)
                    self.assertGreater(max(abs(border_corner[i] - border_ink[i]) for i in range(3)), 40)

                    page.locator('[data-nav="print"]').click()
                    page.wait_for_function("document.body.dataset.activeView === 'print'")
                    export_button = page.locator('[data-action="export-pdf"]')
                    export_button.evaluate("node => node.click()")
                    page.wait_for_function("document.querySelector('[data-bind=export-status]')?.textContent.includes('designer-export.pdf') || document.querySelector('[data-bind=export-status]')?.textContent.includes('PDF export complete')", timeout=20000)
                    self.assertTrue(self.export_path.is_file())
                    self.assertTrue(self.export_path.read_bytes().startswith(b"%PDF"))
                    self.assertEqual(page.locator('[data-theme]').first.get_attribute("data-theme"), theme)
                page.close()
            finally:
                browser.close()
        self.assertEqual(errors, [])

    def test_drag_keeps_the_selected_layer_when_pattern_overlaps_it(self) -> None:
        from playwright.sync_api import sync_playwright

        pattern = self.api.invoke("design.add_element", {
            "type": "pattern", "x": 430, "y": 520, "width": 220, "height": 150,
            "patternType": "dots", "label": "Overlapping pattern",
        })
        self.assertTrue(pattern["ok"], pattern)
        pattern_id = pattern["data"]["element"]["id"]
        bridge_script = """
          window.pywebview = {api: {invoke: async (method, params = {}) => {
            const response = await fetch('/bridge', {method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({method, params})});
            return response.json();
          }}};
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True, args=["--no-sandbox"])
            try:
                page = browser.new_page(viewport={"width": 1366, "height": 768}, device_scale_factor=1)
                page.add_init_script(bridge_script)
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto(self.url, wait_until="load")
                page.wait_for_function("document.body.classList.contains('is-booted')", timeout=15000)
                page.locator('[data-nav="designer"]').click()
                shape_row = page.locator(f'[data-layer-id="{self.shape_id}"]')
                shape_row.wait_for(state="visible", timeout=8000)
                page.evaluate("""() => {
                  window.__selectionStateTrace = [];
                  document.addEventListener('studio:state-change', event => window.__selectionStateTrace.push(event.detail?.selectedElementId ?? null));
                }""")
                shape_row.click()
                self.assertEqual(page.locator('[data-bind="design-element-list"]').input_value(), self.shape_id)
                self.assertEqual(page.evaluate("window.__selectionStateTrace.at(-1)"), self.shape_id)
                page.evaluate("""() => {
                  window.__overlapPointerTrace = [];
                  for (const name of ['pointerdown', 'pointermove', 'pointerup', 'gotpointercapture', 'lostpointercapture'])
                    document.addEventListener(name, event => window.__overlapPointerTrace.push({name, x:event.clientX, y:event.clientY, target:event.target.tagName}), true);
                }""")
                active_design = self.api._design_for_strip(self.api._project, self.api._project["strips"][0]["id"])
                before = active_design["elements"]
                shape_before = next(item for item in before if item["id"] == self.shape_id)
                pattern_before = next(item for item in before if item["id"] == pattern_id)
                point = page.evaluate("""id => {
                  const d=window.__studio_design_snapshot, e=d.elements.find(item=>item.id===id);
                  const r=document.querySelector('[data-bind=designer-canvas]').getBoundingClientRect();
                  return {x:r.left+(e.x+e.width/2)/d.artboard.width*r.width,
                    y:r.top+(e.y+e.height/2)/d.artboard.height*r.height};
                }""", self.shape_id)
                page.mouse.move(point["x"], point["y"])
                page.mouse.down()
                page.mouse.move(point["x"] + 12, point["y"] + 8, steps=3)
                page.mouse.up()
                try:
                    page.wait_for_function("""({shapeId,x}) => {
                      const e=window.__studio_design_snapshot?.elements.find(item=>item.id===shapeId);
                      return e && Math.abs(e.x-x)>1;
                    }""", arg={"shapeId": self.shape_id, "x": shape_before["x"]}, timeout=8000)
                except Exception as error:
                    diagnostic = page.evaluate("""({shapeId,patternId}) => ({
                      selected:document.querySelector('[data-bind=design-element-list]')?.value,
                      shape:window.__studio_design_snapshot?.elements.find(item=>item.id===shapeId),
                      pattern:window.__studio_design_snapshot?.elements.find(item=>item.id===patternId),
                      canvas:document.querySelector('[data-bind=designer-canvas]')?.getBoundingClientRect().toJSON(),
                      trace:window.__overlapPointerTrace,
                      status:document.querySelector('[data-bind=app-status]')?.textContent,
                    })""", {"shapeId": self.shape_id, "patternId": pattern_id})
                    self.fail(f"Selected overlapping element did not move: {diagnostic}: {error}")
                after = self.api._design_for_strip(self.api._project, self.api._project["strips"][0]["id"])["elements"]
                shape_after = next(item for item in after if item["id"] == self.shape_id)
                pattern_after = next(item for item in after if item["id"] == pattern_id)
                self.assertNotEqual(shape_after["x"], shape_before["x"])
                self.assertEqual(pattern_after["x"], pattern_before["x"])
            finally:
                browser.close()

    def test_text_element_hooks_render_in_canvas_strip_and_visible_pdf_export(self) -> None:
        from PIL import Image
        from playwright.sync_api import sync_playwright
        from app.backend.strip_renderer import render_strips

        bridge_script = """
          window.pywebview = {api: {invoke: async (method, params = {}) => {
            const response = await fetch('/bridge', {method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({method, params})});
            return response.json();
          }}};
        """
        errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True, args=["--no-sandbox"])
            try:
                page = browser.new_page(viewport={"width": 1366, "height": 768}, device_scale_factor=1)
                page.add_init_script(bridge_script)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto(self.url, wait_until="load")
                page.wait_for_function("document.body.classList.contains('is-booted')", timeout=15000)
                page.locator('[data-nav="designer"]').click()
                page.locator('[data-vaction="add-menu"]').click()
                page.locator('[data-action="element-add"][data-element-type="text"]').click()
                page.wait_for_function("() => window.__studio_design_snapshot?.elements?.some(item => item.type === 'text')")
                element_id = page.evaluate("() => document.querySelector('[data-bind=design-element-list]').value")
                page.locator('[data-bind="text-content"]').fill("Foundation Week")
                page.locator('[data-bind="text-content"]').dispatch_event('change')
                page.wait_for_function("id => window.__studio_design_snapshot?.elements?.find(item=>item.id===id)?.text === 'Foundation Week'", arg=element_id)
                text_state = page.evaluate("id => window.__studio_design_snapshot.elements.find(item=>item.id===id)", element_id)
                self.assertEqual(text_state["fontFamily"], "Arial")
                self.assertTrue(text_state["syncKey"])
                self.assertTrue(page.evaluate("window.__studio_capabilities.design.applyToAll"))

                page.locator('[data-text-align="left"]').click()
                page.wait_for_function("id => window.__studio_design_snapshot?.elements?.find(item=>item.id===id)?.textAlign === 'left'", arg=element_id)

                canvas_bounds = page.evaluate("""id => {
                  const c=document.querySelector('[data-bind=designer-canvas]'),ctx=c.getContext('2d'),d=window.__studio_design_snapshot,e=d.elements.find(item=>item.id===id);
                  const s=Math.min(c.width/d.artboard.width,c.height/d.artboard.height),points=[];
                  for(let y=Math.floor(e.y*s);y<Math.min(c.height,(e.y+e.height)*s);y++) for(let x=Math.floor(e.x*s);x<Math.min(c.width,(e.x+e.width)*s);x++) {
                    const p=ctx.getImageData(x,y,1,1).data;if(p[0]<80&&p[1]<80&&p[2]<80) points.push([x/s,y/s]);
                  }
                  return points.length?{left:Math.min(...points.map(p=>p[0])),top:Math.min(...points.map(p=>p[1])),right:Math.max(...points.map(p=>p[0])),bottom:Math.max(...points.map(p=>p[1]))}:null;
                }""", element_id)
                self.assertIsNotNone(canvas_bounds)

                rendered = render_strips(self.api._project["strips"], self.api._project["images"],
                    self.api._project["design"], self.api._project["printSettings"], self.work / "text-parity-cache")[0]
                with Image.open(rendered["renderPath"]) as image:
                    rgb = image.convert("RGB")
                    design = self.api._design_for_strip(self.api._project, self.strip_id)
                    element = next(item for item in design["elements"] if item["id"] == element_id)
                    sx, sy = rgb.width / design["artboard"]["width"], rgb.height / design["artboard"]["height"]
                    x0, y0 = round(element["x"] * sx), round(element["y"] * sy)
                    x1, y1 = round((element["x"] + element["width"]) * sx), round((element["y"] + element["height"]) * sy)
                    pixels = [(x / sx, y / sy) for y in range(y0, y1) for x in range(x0, x1)
                              if max(rgb.getpixel((x, y))) < 80]
                    self.assertGreater(len(pixels), 5)
                    raster_bounds = {"left": min(p[0] for p in pixels), "top": min(p[1] for p in pixels),
                                     "right": max(p[0] for p in pixels), "bottom": max(p[1] for p in pixels)}
                    rgb.close()
                for edge in ("left", "top", "right", "bottom"):
                    self.assertLessEqual(abs(canvas_bounds[edge] - raster_bounds[edge]), 24, (canvas_bounds, raster_bounds))

                page.locator('[data-nav="preview"]').click()
                page.wait_for_function("document.body.dataset.activeView === 'preview'")
                page.locator('[data-bind="pdf-preview"]').wait_for(state="visible", timeout=15000)
                page.locator('[data-action="export-pdf"]').click()
                page.wait_for_function("document.querySelector('[data-bind=export-status]')?.textContent.includes('designer-export.pdf')", timeout=20000)
                self.assertTrue(self.export_path.is_file())
                self.assertTrue(self.export_path.read_bytes().startswith(b"%PDF"))
            finally:
                browser.close()
        self.assertEqual(errors, [])

    def test_text_property_edits_have_one_update_and_one_undo_step(self) -> None:
        from playwright.sync_api import sync_playwright

        saved = self.api.invoke("project.save_as", {"path": str(self.work / "dirty-chip.stripproj")})
        self.assertTrue(saved["ok"], saved)

        bridge_script = """
          window.pywebview = {api: {invoke: async (method, params = {}) => {
            const response = await fetch('/bridge', {method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({method, params})});
            return response.json();
          }}};
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True, args=["--no-sandbox"])
            try:
                page = browser.new_page(viewport={"width": 1366, "height": 768})
                page.add_init_script(bridge_script)
                page.add_init_script("""window.__dirtyStateEvents = [];
                  document.addEventListener('studio:state-change', event => {
                    const dirty = event.detail?.project?.isDirty;
                    if (typeof dirty === 'boolean') window.__dirtyStateEvents.push(dirty);
                  });""")
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto(self.url, wait_until="load")
                page.wait_for_function("document.body.classList.contains('is-booted')", timeout=15000)
                page.wait_for_function("window.__dirtyStateEvents?.includes(false)")
                self.assertEqual(page.locator('[data-project-state]').inner_text(), "Saved")
                page.locator('[data-nav="designer"]').click()
                page.locator('[data-vaction="add-menu"]').click()
                page.locator('[data-action="element-add"][data-element-type="text"]').click()
                page.wait_for_function("() => window.__studio_design_snapshot?.elements?.some(item => item.type === 'text')")
                page.wait_for_function("window.__dirtyStateEvents?.includes(true)")
                self.assertEqual(page.locator('[data-project-state]').inner_text(), "Unsaved changes")
                element_id = page.evaluate("() => document.querySelector('[data-bind=design-element-list]').value")

                edits = (
                    ("text-content", "text", "One logical edit"),
                    ("text-font-size", "fontSize", "72"),
                    ("text-color", "fill", "#345678"),
                    ("text-align", "textAlign", "left"),
                )
                for binding, property_name, value in edits:
                    with self.subTest(binding=binding):
                        before_depth = self.api._history.status()["undoDepth"]
                        self.api_calls.clear()
                        page.locator(f'[data-bind="{binding}"]').evaluate(
                            "(node, value) => { node.value = value; node.dispatchEvent(new Event('change', {bubbles: true})); }",
                            value,
                        )
                        expected = float(value) if property_name == "fontSize" else value
                        page.wait_for_function("spec => { const item = window.__studio_design_snapshot?.elements?.find(e => e.id === spec.id); return item && item[spec.property] === spec.value; }",
                            arg={"id": element_id, "property": property_name, "value": expected})
                        page.wait_for_timeout(100)
                        self.assertEqual(self.api_calls.count("design.update"), 1, binding)
                        self.assertEqual(self.api._history.status()["undoDepth"], before_depth + 1, binding)

                page.locator('[data-action="design-undo"]').click()
                page.wait_for_function("id => window.__studio_design_snapshot?.elements?.find(e => e.id === id)?.textAlign === 'center'", arg=element_id)
                self.assertEqual(self.api_calls.count("design.undo"), 1)
                page.locator('[data-action="design-redo"]').click()
                page.wait_for_function("id => window.__studio_design_snapshot?.elements?.find(e => e.id === id)?.textAlign === 'left'", arg=element_id)
                self.assertEqual(self.api_calls.count("design.redo"), 1)

                canvas = page.locator('[data-bind="designer-canvas"]')
                point = page.evaluate("""id => {const c=document.querySelector('[data-bind=designer-canvas]'),e=window.__studio_design_snapshot.elements.find(item=>item.id===id),a=window.__studio_design_snapshot.artboard;return {x:(e.x+e.width/2)/a.width*c.clientWidth,y:(e.y+e.height/2)/a.height*c.clientHeight};}""", element_id)
                original_text = "One logical edit"
                before_depth = self.api._history.status()["undoDepth"]
                self.api_calls.clear()
                canvas.dblclick(position=point)
                dialog_input = page.locator('[data-dialog-input]')
                dialog_input.wait_for(state="visible")
                self.assertEqual(dialog_input.input_value(), original_text)
                page.locator('[data-dialog-action="cancel"]').click()
                page.wait_for_function("document.querySelector('[data-dialog-root]').hidden")
                self.assertEqual(self.api_calls.count("design.update"), 0)
                self.assertEqual(self.api._history.status()["undoDepth"], before_depth)

                canvas.dblclick(position=point)
                dialog_input.wait_for(state="visible")
                page.locator('[data-dialog-action="primary"]').click()
                page.wait_for_function("document.querySelector('[data-dialog-root]').hidden")
                self.assertEqual(self.api_calls.count("design.update"), 0)
                self.assertEqual(self.api._history.status()["undoDepth"], before_depth)

                canvas.dblclick(position=point)
                dialog_input.wait_for(state="visible")
                dialog_input.fill("Edited from in-app dialog")
                dialog_input.press("Enter")
                page.wait_for_function("arg => window.__studio_design_snapshot?.elements?.find(e => e.id === arg.id)?.text === arg.text", arg={"id": element_id, "text": "Edited from in-app dialog"})
                self.assertEqual(self.api_calls.count("design.update"), 1)
                self.assertEqual(self.api._history.status()["undoDepth"], before_depth + 1)
                page.locator('[data-action="design-undo"]').click()
                page.wait_for_function("arg => window.__studio_design_snapshot?.elements?.find(e => e.id === arg.id)?.text === arg.text", arg={"id": element_id, "text": original_text})
                page.locator('[data-action="design-redo"]').click()
                page.wait_for_function("arg => window.__studio_design_snapshot?.elements?.find(e => e.id === arg.id)?.text === arg.text", arg={"id": element_id, "text": "Edited from in-app dialog"})
            finally:
                browser.close()

    def test_async_dialog_queue_and_template_name_flows(self) -> None:
        from playwright.sync_api import sync_playwright

        bridge_script = """
          window.pywebview = {api: {invoke: async (method, params = {}) => {
            const response = await fetch('/bridge', {method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({method, params})});
            return response.json();
          }}};
        """
        native_dialogs: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True, args=["--no-sandbox"])
            try:
                page = browser.new_page(viewport={"width": 1366, "height": 768})
                page.add_init_script(bridge_script)
                page.on("dialog", lambda dialog: (native_dialogs.append(dialog.type), dialog.dismiss()))
                page.goto(self.url, wait_until="load")
                page.wait_for_function("document.body.classList.contains('is-booted')", timeout=15000)
                page.locator('[data-nav="designer"]').click()

                page.evaluate("""() => {
                  window.__dialogResults = [];
                  window.__studio_dialog.request({kind:'confirmation',title:'Queue first',message:'First request'}).then(value=>window.__dialogResults.push(value));
                  window.__studio_dialog.request({kind:'confirmation',title:'Queue second',message:'Second request'}).then(value=>window.__dialogResults.push(value));
                }""")
                page.wait_for_function("document.querySelector('[data-dialog-title]')?.textContent === 'Queue first'")
                self.assertEqual(page.locator('[data-dialog-root] [role="dialog"]').count(), 1)
                page.locator('[data-dialog-action="primary"]').click()
                page.wait_for_function("document.querySelector('[data-dialog-title]')?.textContent === 'Queue second'")
                page.locator('[data-dialog-action="cancel"]').click()
                page.wait_for_function("window.__dialogResults?.length === 2")
                self.assertEqual(page.evaluate("window.__dialogResults"), [
                    {"confirmed": True, "value": None, "action": "primary"},
                    {"confirmed": False, "value": None, "action": "cancel"},
                ])

                self.api_calls.clear()
                page.locator('[data-nav="templates"]').click()
                page.wait_for_function("document.body.dataset.activeView === 'templates'")
                page.locator('[data-action="template-save-as"]').click()
                dialog_input = page.locator('[data-dialog-input]')
                dialog_input.wait_for(state="visible")
                dialog_input.fill("   ")
                page.locator('[data-dialog-action="primary"]').click()
                page.locator('[data-dialog-error]').wait_for(state="visible")
                self.assertNotIn("templates.save_as", self.api_calls)
                dialog_input.fill("Dialog Template")
                dialog_input.press("Enter")
                page.wait_for_function("document.querySelector('[data-dialog-root]').hidden")
                page.wait_for_function("window.__studio_state_mirror?.() && document.querySelector('[data-bind=app-status]')?.textContent === 'Ready'")
                self.assertEqual(self.api_calls.count("templates.save_as"), 1)
                self.assertTrue(any(item.get("name") == "Dialog Template" for item in self.api.template_service.list_templates()))
                page.locator('[data-mirror-options="template-list"] .template-chip', has_text="Dialog Template").last.click()

                self.api_calls.clear()
                page.locator('[data-action="template-duplicate"]').click()
                dialog_input.wait_for(state="visible")
                dialog_input.fill("Cancelled Duplicate")
                page.locator('[data-dialog-action="cancel"]').click()
                page.wait_for_function("document.querySelector('[data-dialog-root]').hidden")
                self.assertNotIn("templates.duplicate", self.api_calls)

                page.locator('[data-action="template-duplicate"]').click()
                dialog_input.wait_for(state="visible")
                dialog_input.fill("Dialog Copy")
                dialog_input.press("Enter")
                page.wait_for_function("document.querySelector('[data-dialog-root]').hidden")
                page.wait_for_function("document.querySelector('[data-bind=app-status]')?.textContent === 'Ready'")
                self.assertEqual(self.api_calls.count("templates.duplicate"), 1)
                self.assertTrue(any(item.get("name") == "Dialog Copy" for item in self.api.template_service.list_templates()))
                page.locator('[data-mirror-options="template-list"] .template-chip', has_text="Dialog Copy").last.click()

                page.locator('[data-action="template-rename"]').click()
                dialog_input.wait_for(state="visible")
                self.assertEqual(dialog_input.input_value(), "Dialog Copy")
                page.wait_for_function("node => document.activeElement === node && node.selectionStart === 0 && node.selectionEnd === node.value.length", arg=dialog_input.element_handle())
                self.assertEqual(dialog_input.evaluate("node => [node.selectionStart, node.selectionEnd]"), [0, len("Dialog Copy")])
                dialog_input.fill("Renamed Copy")
                dialog_input.press("Enter")
                page.wait_for_function("document.querySelector('[data-dialog-root]').hidden")
                page.wait_for_function("document.querySelector('[data-bind=app-status]')?.textContent === 'Ready'")
                self.assertEqual(self.api_calls.count("templates.rename"), 1)
                self.assertTrue(any(item.get("name") == "Renamed Copy" for item in self.api.template_service.list_templates()))

                self.api_calls.clear()
                page.locator('[data-action="project-new"]').click()
                page.wait_for_function("document.querySelector('[data-dialog-title]')?.textContent === 'New project'")
                self.assertIn("Unsaved changes", page.locator('[data-dialog-text]').inner_text())
                page.locator('[data-dialog-action="cancel"]').click()
                page.wait_for_function("document.querySelector('[data-dialog-root]').hidden")
                self.assertNotIn("project.new", self.api_calls)
                page.locator('[data-action="project-new"]').click()
                page.wait_for_function("document.querySelector('[data-dialog-title]')?.textContent === 'New project'")
                page.locator('[data-dialog-action="primary"]').click()
                page.wait_for_function("document.querySelector('[data-dialog-root]').hidden")
                page.wait_for_function("document.querySelector('[data-bind=app-status]')?.textContent === 'Ready'")
                self.assertEqual(self.api_calls.count("project.new"), 1)
                self.assertFalse(self.api.invoke("app.get_state", {})["data"]["project"]["isDirty"])
                self.assertEqual(native_dialogs, [])
            finally:
                browser.close()

    def test_recovery_dialog_requires_restore_or_start_new_without_browser_fallback(self) -> None:
        from playwright.sync_api import sync_playwright

        bridge_script = """
          window.pywebview = {api: {invoke: async (method, params = {}) => {
            const response = await fetch('/bridge', {method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({method, params})});
            return response.json();
          }}};
        """
        self.api._project["name"] = "Recovery choice fixture"
        self.api.project_service.autosave(self.api._project)
        native_dialogs: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True, args=["--no-sandbox"])
            try:
                page = browser.new_page(viewport={"width": 1366, "height": 768})
                page.add_init_script(bridge_script)
                page.on("dialog", lambda dialog: (native_dialogs.append(dialog.type), dialog.dismiss()))
                page.goto(self.url, wait_until="load")
                page.locator('[data-dialog-root]').wait_for(state="visible", timeout=15000)
                self.assertEqual(page.locator('[data-dialog-title]').inner_text(), "Restore previous session?")
                self.assertEqual(page.locator('[data-dialog-action="primary"]').inner_text(), "Restore Session")
                self.assertEqual(page.locator('[data-dialog-action="secondary"]').inner_text(), "Start New")
                self.assertTrue(page.locator('[data-dialog-action="cancel"]').is_hidden())
                page.keyboard.press("Escape")
                page.locator('[data-dialog-backdrop]').click(position={"x": 4, "y": 4})
                self.assertTrue(page.locator('[data-dialog-root]').is_visible())
                self.assertNotIn("session.restore", self.api_calls)
                self.assertNotIn("session.start_new", self.api_calls)
                page.locator('[data-dialog-action="secondary"]').click()
                page.wait_for_function("document.querySelector('[data-dialog-root]').hidden")
                page.wait_for_function("document.querySelector('[data-bind=project-name]')?.textContent === 'Untitled Project'")
                self.assertEqual(self.api_calls.count("session.start_new"), 1)
                self.assertFalse(self.api.invoke("app.get_state", {})["data"]["project"]["isDirty"])

                self.api._project["name"] = "Restored after decision"
                self.api.project_service.autosave(self.api._project)
                page.reload(wait_until="load")
                page.locator('[data-dialog-root]').wait_for(state="visible", timeout=15000)
                page.locator('[data-dialog-action="primary"]').click()
                page.wait_for_function("document.querySelector('[data-dialog-root]').hidden")
                page.wait_for_function("document.querySelector('[data-bind=project-name]')?.textContent === 'Restored after decision'")
                self.assertEqual(self.api_calls.count("session.restore"), 1)
                self.assertTrue(self.api.invoke("app.get_state", {})["data"]["project"]["isDirty"])
                self.assertEqual(native_dialogs, [])
            finally:
                browser.close()

    def test_print_settings_remain_synced_after_visible_form_edits(self) -> None:
        from playwright.sync_api import sync_playwright

        seeded = self.api.invoke("print.update_settings", {"settings": {"stripsPerPage": 6, "cutGuides": False}})
        self.assertTrue(seeded["ok"], seeded)
        bridge_script = """
          window.pywebview = {api: {invoke: async (method, params = {}) => {
            const response = await fetch('/bridge', {method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({method, params})});
            return response.json();
          }}};
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True, args=["--no-sandbox"])
            try:
                page = browser.new_page(viewport={"width": 1366, "height": 768}, device_scale_factor=1)
                page.add_init_script(bridge_script)
                page.goto(self.url, wait_until="load")
                page.wait_for_function("document.body.classList.contains('is-booted')", timeout=15000)
                page.locator('[data-nav="print"]').click()
                count = page.locator('[data-bind="strips-per-page"]')
                count.fill("5")
                count.press("Tab")
                page.wait_for_function("async () => (await window.pywebview.api.invoke('print.get_settings', {})).data.stripsPerPage === 5")
                page.locator('[data-bind="cut-guides"]').click()
                page.wait_for_function("async () => { const s=(await window.pywebview.api.invoke('print.get_settings', {})).data; return s.stripsPerPage === 5 && s.cutGuides === true; }")
                self.assertEqual(count.input_value(), "5")
                self.assertTrue(page.locator('[data-bind="cut-guides"]').is_checked())
                layout = self.api.invoke("print.get_layout", {})["data"]
                self.assertTrue(layout["fit"]["valid"])
                self.assertEqual(layout["settings"]["stripsPerPage"], 5)
                self.assertTrue(layout["settings"]["cutGuides"])
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
