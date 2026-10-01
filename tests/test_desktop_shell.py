from __future__ import annotations

from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

from app.desktop_shell import _QuietHandler


def test_es_module_assets_are_served_as_javascript() -> None:
    frontend = Path(__file__).resolve().parents[1] / "frontend"
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(frontend)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/js/designer_geometry.mjs") as response:
            assert response.status == 200
            assert response.headers.get_content_type() in {"text/javascript", "application/javascript"}
            assert b"resizeElement" in response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
