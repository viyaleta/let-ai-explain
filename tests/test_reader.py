"""Tests for lae_reader (shared page reader) and the hosted site's api/ functions.

    python3 -m unittest discover -s tests -v

Runs offline: host lookups are faked, and "public" pages are served by a local test server.
"""
import importlib.util
import json
import socket
import sys
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import lae_reader as R  # noqa: E402

REAL_GETADDRINFO = socket.getaddrinfo
REAL_PUBLIC_IP = R.public_ip


def fake_dns(table):
    """getaddrinfo that answers from table (host -> list of IPs, or a callable returning one)."""
    def lookup(host, port, *a, **k):
        if host in table:
            ips = table[host]() if callable(table[host]) else table[host]
            return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]
        if host in ("127.0.0.1", "localhost"):
            return REAL_GETADDRINFO(host, port, *a, **k)
        raise socket.gaierror("not in the fake table")
    return lookup


def loopback_is_public(ip):
    """In these tests 127.0.0.1 stands in for a public server; everything else is judged for real."""
    return str(ip) == "127.0.0.1" or REAL_PUBLIC_IP(ip)


class Pages(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/to-private"):
            self.send_response(302)
            self.send_header("Location", "http://internal.test:%d/secret" % self.server.server_port)
            self.end_headers()
            return
        if self.path.startswith("/to-port"):
            self.send_response(302)
            self.send_header("Location", "http://site.test:8080/")
            self.end_headers()
            return
        if self.path.startswith("/big"):
            body = b"<html><head><title>Big</title></head><body>" + b"x" * 3_000_000 + b"</body></html>"
        else:
            body = (b"<html><head><title>A page</title></head><body><h1>Heading</h1><p>"
                    + b"Readable text. " * 40 + b"</p><script>bad()</script></body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the reader stopped at its size cap


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Pages)
        cls.port = cls.srv.server_port
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        dns = {"site.test": ["127.0.0.1"], "internal.test": ["10.0.0.5"], "metadata.test": ["169.254.169.254"],
               "mixed.test": ["93.184.216.34", "192.168.1.1"], "public.test": ["93.184.216.34"]}
        p1 = mock.patch.object(socket, "getaddrinfo", fake_dns(dns))
        p2 = mock.patch.object(R, "public_ip", loopback_is_public)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def url(self, path="/page"):
        return f"http://site.test:{self.port}{path}"

    def test_public_ip(self):
        for ip in ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.0.1", "169.254.169.254", "0.0.0.0",
                   "100.64.0.1", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "224.0.0.1"]:
            self.assertFalse(REAL_PUBLIC_IP(ip), ip)
        for ip in ["93.184.216.34", "1.1.1.1", "2606:4700:4700::1111"]:
            self.assertTrue(REAL_PUBLIC_IP(ip), ip)

    def test_public_url_refusals(self):
        bad = ["http://internal.test/", "http://metadata.test/latest/meta-data/", "http://mixed.test/",
               "file:///etc/passwd", "ftp://public.test/", "http://user:pw@public.test/", "javascript:alert(1)",
               "http://nowhere.test/", "http://[::1]/", "http://10.0.0.1/", "http://public.test:99999/"]
        for u in bad:
            self.assertFalse(R.public_url(u), u)
        self.assertTrue(R.public_url("https://public.test/"))
        self.assertTrue(R.public_url("http://public.test:8080/"))                  # local server: any port
        self.assertFalse(R.public_url("http://public.test:8080/", R.WEB_PORTS))    # hosted: 80 and 443 only
        self.assertTrue(R.public_url("https://public.test:443/", R.WEB_PORTS))

    def test_read_page(self):
        page = R.read_page(self.url())
        self.assertEqual(page["title"], "A page")
        self.assertIn("# Heading", page["text"])
        self.assertNotIn("bad()", page["text"])

    def test_redirect_to_private_address_is_refused(self):
        with self.assertRaises(R.ReaderError) as e:
            R.read_page(self.url("/to-private"))
        self.assertEqual(e.exception.code, "bad_url")
        with self.assertRaises(R.ReaderError):
            R.check_url(self.url("/to-private"))

    def test_redirect_to_other_port_is_refused_when_ports_are_limited(self):
        with self.assertRaises(R.ReaderError):
            R.check_url(self.url("/to-port"), ports=(self.port,))

    def test_dns_rebinding_is_refused(self):
        """The name looks public at the check and private at the connection: the connection is refused."""
        answers = iter([["93.184.216.34"], ["10.0.0.5"]])
        with mock.patch.object(socket, "getaddrinfo", fake_dns({"rebind.test": lambda: next(answers)})):
            with self.assertRaises(R.ReaderError) as e:
                R.check_url(f"http://rebind.test:{self.port}/")
        self.assertEqual(e.exception.code, "bad_url")

    def test_size_caps(self):
        final, ctype, body, cs = R.http_get(self.url("/big"), limit=1000)
        self.assertEqual(len(body), 1000)
        r = R.check_url(self.url("/big"))
        self.assertTrue(r["ok"])
        self.assertEqual(r["title"], "Big")

    def test_check_url_missing_page(self):
        with mock.patch.object(Pages, "do_GET", lambda h: (h.send_response(404), h.end_headers())):
            r = R.check_url(self.url("/gone"))
        self.assertEqual((r["ok"], r["status"]), (False, 404))


def load_function(name):
    path = ROOT / "api" / f"{name}.py"
    spec = importlib.util.spec_from_file_location("api_" + name.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.handler


class FunctionTests(unittest.TestCase):
    """The api/*.py handlers, served the way Vercel calls them (one BaseHTTPRequestHandler per request)."""
    servers = []

    @classmethod
    def serve(cls, name):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), load_function(name))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        cls.servers.append(srv)
        return f"http://127.0.0.1:{srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        for s in cls.servers:
            s.shutdown()

    def get(self, base, path, headers=None, method="GET"):
        h = {"X-Let-AI-Explain": "1", "Sec-Fetch-Site": "same-origin", **(headers or {})}
        req = urllib.request.Request(base + path, headers={k: v for k, v in h.items() if v is not None}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_config(self):
        self.assertEqual(self.get(self.serve("config"), "/api/config"), (200, {"mode": "hosted"}))

    def test_fetch_access_and_validation(self):
        base = self.serve("fetch")
        self.assertEqual(self.get(base, "/api/fetch?url=x", {"X-Let-AI-Explain": None})[0], 403)
        self.assertEqual(self.get(base, "/api/fetch?url=x", {"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.get(base, "/api/fetch?url=x", {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.get(base, "/api/fetch?url=x", method="POST")[0], 405)
        self.assertEqual(self.get(base, "/api/fetch?url=" + "a" * 3000)[1]["error"], "bad_url")
        for target in ["http://127.0.0.1:1455/api/session", "http://169.254.169.254/latest/meta-data/",
                       "http://localhost/", "file:///etc/passwd", "https://example.com:8443/"]:
            status, body = self.get(base, "/api/fetch?url=" + urllib.parse.quote(target, safe=""))
            self.assertEqual((status, body["error"]), (400, "bad_url"), target)

    def test_rate_limit(self):
        base = self.serve("check-url")
        ip = {"x-real-ip": "203.0.113.9"}
        codes = [self.get(base, "/api/check-url?url=http://10.0.0.1/", ip)[0] for _ in range(125)]
        self.assertEqual(codes[:120], [400] * 120)
        self.assertEqual(set(codes[120:]), {429})
        self.assertEqual(self.get(base, "/api/check-url?url=http://10.0.0.1/", {"x-real-ip": "203.0.113.10"})[0], 400)


import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402

if __name__ == "__main__":
    unittest.main()
