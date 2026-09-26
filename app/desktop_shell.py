"""PyWebView window lifecycle."""

from __future__ import annotations

import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import webview
from webview.dom import DOMEventHandler

from app.backend.api import StudioAPI
from app.backend.drop_service import extract_dropped_paths


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_: object) -> None:
        pass

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # WebView can cancel an in-flight local asset request while
            # navigating or shutting down; it is not an application failure.
            pass


def run(*, debug: bool = False) -> None:
    root = Path(__file__).resolve().parents[1]
    frontend_root = root / "frontend"
    frontend = frontend_root / "index.html"
    if not frontend.is_file():
        raise FileNotFoundError(f"Frontend entry point was not found: {frontend}")
    # Serve bundled frontend assets only on loopback. A regular local origin is
    # needed for ES modules; no remote network service is used.
    handler = partial(_QuietHandler, directory=str(frontend_root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    Thread(target=server.serve_forever, name="strip-studio-assets", daemon=True).start()
    frontend_url = f"http://127.0.0.1:{server.server_port}/index.html"
    api = StudioAPI(root=root)
    window = webview.create_window(
        "Strip Print Studio",
        frontend_url,
        js_api=api,
        width=1440,
        height=920,
        min_size=(1024, 680),
        confirm_close=False,
    )
    api.attach_window(window)
    try:
        window.events.closed += server.shutdown
    except Exception:
        pass

    def bind_drop_events() -> None:
        def on_drop(event: object) -> None:
            paths = extract_dropped_paths(event)
            if not paths:
                return
            # JSON serialization keeps filesystem paths as data when passing
            # them into the frontend event; imports still go through the
            # allowlisted API and background job queue.
            payload = json.dumps(paths)
            window.evaluate_js(
                "document.dispatchEvent(new CustomEvent('studio:files-dropped', "
                f"{{ detail: {{ paths: {payload} }} }}));"
            )

        document = window.dom.document
        document.events.dragover += DOMEventHandler(
            lambda _event: None,
            prevent_default=True,
            stop_propagation=True,
            debounce=150,
        )
        document.events.drop += DOMEventHandler(
            on_drop,
            prevent_default=True,
            stop_propagation=True,
        )

    webview.start(
        bind_drop_events,
        debug=debug,
        storage_path=str(api.data_dir / "webview-profile"),
    )


if __name__ == "__main__":
    run(debug=False)
