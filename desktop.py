#!/usr/bin/env python3
"""Let AI Explain as a desktop app: the same local server, shown in a native window.

Run from source:  pip install "pywebview>=6.2"  then  python3 desktop.py
The packaged apps (see packaging/) run this file.
"""
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import let_ai_explain as app

# The app logs to logs/let-ai-explain.log in its data folder (rotated at 1 MB).
app.setup_logging(to_file=True)


class _ToLog:
    """A packaged app has no console (on macOS its output goes nowhere), so stray prints and
    tracebacks go to the log instead."""
    def __init__(self):
        self.buf = ""

    def write(self, text):
        self.buf += text
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                app.log.info("%s", line.rstrip())
        return len(text)

    def flush(self):
        pass


if getattr(sys, "frozen", False) or sys.stdout is None or sys.stderr is None:
    sys.stdout = sys.stderr = _ToLog()

import webview  # noqa: E402  (pywebview)

TITLE = "Let AI Explain"
# The window's own storage is per address (origin), so the port must be the same every run:
# 1455 like the browser version, then 1456-1465 in order. Never a random port.
PORTS = [app.PREFERRED_PORT] + list(range(1456, 1466))


def bind_server():
    for port in PORTS:
        try:
            return app.Server((app.HOST, port), app.Handler)
        except OSError:
            continue
    return None


PORTS_BUSY = """<!doctype html><meta charset="utf-8"><body style="font:15px/1.5 system-ui,sans-serif;padding:2rem;max-width:30rem">
<h2 style="margin-top:0">Let AI Explain can't start</h2>
<p>Ports 1455 to 1465 on this computer are all in use, often by other copies of Let AI Explain.
Quit them (or restart your computer) and open the app again.</p></body>"""


class Desktop:
    """What the page asks for through /api/desktop/*: things a web view can't do by itself."""

    def __init__(self):
        self.window = None

    def open_url(self, url):
        webbrowser.open(url)

    def open_folder(self, path):
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606 (our own data folder)
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def save_html(self, name, html):
        name = re.sub(r'[\\/:*?"<>|]+', "-", name).strip() or "explanation.html"
        result = self.window.create_file_dialog(webview.FileDialog.SAVE, save_filename=name)
        if not result:
            return None  # cancelled
        path = Path(result if isinstance(result, str) else result[0])
        if path.suffix.lower() not in (".html", ".htm"):
            path = path.with_name(path.name + ".html")
        path.write_text(html, encoding="utf-8")
        return str(path)

    def print_html(self, html, title):
        """Open the export in the default browser, which prints and saves PDFs well."""
        folder = app.CONFIG_DIR / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.html"):  # tidy up earlier print files
            try:
                if time.time() - old.stat().st_mtime > 86400:
                    old.unlink()
            except OSError:
                pass
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "explanation"
        path = folder / f"{slug}.html"
        auto_print = ("<script>addEventListener('load',()=>setTimeout(()=>print(),900))</script>")
        path.write_text(html.replace("</body>", auto_print + "</body>", 1), encoding="utf-8")
        webbrowser.open(path.as_uri())


def main():
    if not (app.ROOT / "index.html").exists():
        sys.exit("index.html is missing from the app.")
    app.migrate_config()
    server = bind_server()
    if server is None:
        webview.create_window(TITLE, html=PORTS_BUSY, width=520, height=260)
        webview.start()
        return
    desktop = Desktop()
    app.DESKTOP = desktop
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://{app.HOST}:{server.server_port}/"
    app.log.info("%s %s desktop app serving %s (packaged: %s; certificates: %s)", TITLE, app.VERSION or "(dev)", url,
                 bool(getattr(sys, "frozen", False)), app.CERTIFICATES)

    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True  # target=_blank links go to the browser
    desktop.window = webview.create_window(TITLE, url, width=1280, height=800, resizable=True,
                                           min_size=(720, 520), text_select=True)
    storage = app.CONFIG_DIR / "webview"  # the window's own data (cache, cookies), owner-only like the rest
    storage.mkdir(parents=True, exist_ok=True)
    for folder in (app.CONFIG_DIR, storage):
        try:
            os.chmod(folder, 0o700)
        except OSError:
            pass
    try:
        # Not private: History lives in the window's IndexedDB and must survive restarts.
        webview.start(private_mode=False, storage_path=str(storage))
    finally:  # the window was closed: stop the server too
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
