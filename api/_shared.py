"""Shared plumbing for the hosted site's Python functions (Vercel skips files starting with _).

Each function is a BaseHTTPRequestHandler subclass named `handler`, the format Vercel's Python
runtime runs. Only the page on the same site may call them: a request must carry the
X-Let-AI-Explain header (which a browser on another site can't send without a CORS preflight,
and none is ever answered), and a browser's Sec-Fetch-Site / Origin must say same-origin.
"""
import json
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # for lae_reader.py at the project root

from lae_reader import ReaderError, WEB_PORTS  # noqa: E402

MAX_URL = 2048

# Per-IP limits, kept in this function instance's memory: a best-effort brake on scripted use.
# Vercel runs several instances, so the firewall's rate-limit rule on /api/* is the real cap.
_HITS, _LOCK = {}, threading.Lock()


def allow(ip, name, per_minute):
    now = time.monotonic()
    key = (name, ip)
    with _LOCK:
        hits = [t for t in _HITS.get(key, ()) if now - t < 60]
        ok = len(hits) < per_minute
        if ok:
            hits.append(now)
        _HITS[key] = hits
        if len(_HITS) > 10_000:  # forget idle visitors
            for k in [k for k, v in _HITS.items() if not v or now - v[-1] > 60]:
                del _HITS[k]
    return ok


class Handler(BaseHTTPRequestHandler):
    """Subclasses set NAME and PER_MINUTE and implement answer(query)."""
    NAME, PER_MINUTE = "api", 60

    def log_message(self, fmt, *args):  # never log URLs: they're what people read
        pass

    def send_json(self, status, obj, extra=None):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def client_ip(self):
        fwd = self.headers.get("x-real-ip") or (self.headers.get("x-forwarded-for") or "").split(",")[0]
        return fwd.strip() or self.client_address[0]

    def same_site(self):
        if self.headers.get("X-Let-AI-Explain") != "1":
            return False
        site = self.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "none"):
            return False
        origin = self.headers.get("Origin")
        return not origin or urllib.parse.urlparse(origin).netloc == self.headers.get("Host")

    def do_GET(self):
        if not self.same_site():
            return self.send_json(403, {"error": "forbidden", "message": "Only the Let AI Explain page can use this."})
        if self.PER_MINUTE and not allow(self.client_ip(), self.NAME, self.PER_MINUTE):
            return self.send_json(429, {"error": "rate_limited", "message": "Too many requests. Wait a minute and try again."},
                                  {"Retry-After": "60"})
        try:
            self.send_json(200, self.answer(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)))
        except ReaderError as e:
            self.send_json(e.status, e.json())
        except Exception as e:
            self.send_json(502, {"error": "server_error", "message": type(e).__name__})

    def do_POST(self):
        self.send_json(405, {"error": "method_not_allowed"}, {"Allow": "GET"})

    do_PUT = do_DELETE = do_PATCH = do_POST

    def answer(self, query):
        raise NotImplementedError

    @staticmethod
    def url_param(query):
        url = (query.get("url") or [""])[0]
        if not url or len(url) > MAX_URL:
            raise ReaderError("bad_url", "That link can't be read.")
        return url


__all__ = ["Handler", "ReaderError", "WEB_PORTS"]
