"""OpenRouter sign-in on the local server: state lookup, the reason on the done page, and log redaction."""
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

os.environ["LET_AI_EXPLAIN_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import let_ai_explain as app  # noqa: E402


class Stub:
    folders = []

    def open_url(self, url):
        pass

    def open_folder(self, path):
        self.folders.append(path)


class OpenRouterSignin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.DESKTOP = Stub()  # desktop flow: the browser tab ends on /auth/done
        cls.srv = app.Server((app.HOST, 0), app.Handler)
        cls.port = cls.srv.server_port
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        app.DESKTOP = None

    def setUp(self):
        app.PENDING.clear()
        self.real_exchange = app.openrouter_exchange

    def tearDown(self):
        app.openrouter_exchange = self.real_exchange

    def get(self, path, host="127.0.0.1"):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers={"Host": f"{host}:{self.port}"})
        with urllib.request.urlopen(req) as r:
            return r.geturl(), r.read().decode()

    def start(self):
        opener = urllib.request.build_opener(type("NoRedirect", (urllib.request.HTTPRedirectHandler,), {
            "redirect_request": lambda *a, **k: None}))
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/auth/openrouter/start", headers={"Host": f"127.0.0.1:{self.port}"})
        try:
            opener.open(req)
        except urllib.error.HTTPError as e:
            return e.headers["Location"]

    def test_callback_url_uses_this_port(self):
        self.assertIn(f"localhost%3A{self.port}%2Fauth%2Fopenrouter%2Fcallback", self.start())

    def test_no_state_uses_newest_attempt(self):
        self.start()
        app.openrouter_exchange = lambda code, verifier: {"key": "sk-or-v1-" + "0" * 64}
        url, body = self.get("/auth/openrouter/callback?code=abc")
        self.assertIn("signin=ok", url)
        self.assertIn("You're signed in", body)

    def test_wrong_state_shows_reason(self):
        self.start()
        url, body = self.get("/auth/openrouter/callback?code=abc&state=nope")
        self.assertIn("reason=expired", url)
        self.assertIn("doesn&#x27;t match any of 1 started", body)

    def test_unreachable_reason_is_shown_escaped(self):
        self.start()

        def fail(code, verifier):
            raise app.AppError("upstream_unreachable", "Couldn't reach OpenRouter: <certificate verify failed>", 502)
        app.openrouter_exchange = fail
        _, body = self.get("/auth/openrouter/callback?code=abc")
        self.assertIn("Reason: upstream_unreachable", body)
        self.assertIn("&lt;certificate verify failed&gt;", body)
        self.assertNotIn("<certificate", body)

    def test_failed_page_can_open_the_log_folder(self):
        self.start()
        _, body = self.get("/auth/openrouter/callback?code=abc&state=nope")
        self.assertIn('id="logs">Open log folder</button>', body)
        # the button's call, as the page (same origin) makes it
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/desktop/logs", data=b"{}", method="POST", headers={
            "Host": f"127.0.0.1:{self.port}", "Origin": f"http://127.0.0.1:{self.port}",
            "X-Let-AI-Explain": "1", "Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 200)
        self.assertEqual(Stub.folders[-1], app.LOG_DIR)

    def test_done_page_ignores_reason_in_the_link(self):
        _, body = self.get("/auth/done?signin=error&reason=<script>")
        self.assertNotIn("<script>", body)

    def test_redact(self):
        text = app.redact('{"key":"sk-or-v1-abcdef0123456789abcdef","code":"secretcode"} sk-ant-api03-abcdefghijkl')
        self.assertNotIn("abcdef0123456789", text)
        self.assertNotIn("secretcode", text)
        self.assertNotIn("abcdefghijkl", text)


if __name__ == "__main__":
    unittest.main()
