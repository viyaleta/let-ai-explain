#!/usr/bin/env python3
"""Let AI Explain: a local explainer that runs on your ChatGPT plan.

Run:  python3 let_ai_explain.py
Then use the page it opens at http://127.0.0.1:1455/

Uses only the Python standard library. If PyJWT with crypto support is
installed (pip install "pyjwt[crypto]"), the ID token signature is also checked.
"""
import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from html import unescape as html_unescape
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_NAME = "Let AI Explain"  # sent as agent_name_hint; only affects new registrations
AUTH_BASE = "https://auth.openai.com"
AUTHORIZE_URL = AUTH_BASE + "/api/accounts/authorize"
TOKEN_URL = AUTH_BASE + "/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
API = "https://api.openai.com/v1"
IMAGE_DATA_URL = re.compile(r"data:image/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+\Z")
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
HOST = "127.0.0.1"
PREFERRED_PORT = 1455
CALLBACK_PATH = "/auth/callback"
USAGE_URL = "https://chatgpt.com/settings/usage"

ROOT = Path(__file__).resolve().parent
CONFIG_ENV = os.environ.get("LET_AI_EXPLAIN_HOME") or os.environ.get("EXPLAINIT_HOME")  # old name still honored
CONFIG_DIR = Path(CONFIG_ENV or Path.home() / ".config" / "let-ai-explain")
LEGACY_CONFIG_DIR = Path.home() / ".config" / "explainit"  # folder used before the rename
HOST_FILE = CONFIG_DIR / "host.json"
CRED_FILE = CONFIG_DIR / "credentials.json"

PENDING = {}            # state -> pending authorization attempt
LOCK = threading.Lock()  # guards credential reads/writes and refresh


# ---------------------------------------------------------------- storage
def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def write_private(path, data):
    """Atomic write with owner-only permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def migrate_config():
    """Move the pre-rename config folder over once, so existing sign-ins keep working."""
    if CONFIG_ENV or CONFIG_DIR.exists() or not LEGACY_CONFIG_DIR.is_dir():
        return
    try:
        CONFIG_DIR.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(LEGACY_CONFIG_DIR), str(CONFIG_DIR))
        print(f"Moved your settings from {LEGACY_CONFIG_DIR} to {CONFIG_DIR}.")
    except OSError as e:
        print(f"Note: couldn't move {LEGACY_CONFIG_DIR} to {CONFIG_DIR} ({e}). You may need to sign in again.")


def host_id():
    data = read_json(HOST_FILE) or {}
    if not data.get("ext_agent_host_id"):
        data = {"ext_agent_host_id": "urn:uuid:" + str(uuid.uuid4())}
        write_private(HOST_FILE, data)
    return data["ext_agent_host_id"]


def load_cred():
    return read_json(CRED_FILE) or {}


def save_cred(cred):
    write_private(CRED_FILE, cred)


# ---------------------------------------------------------------- helpers
def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64url_decode(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class AppError(Exception):
    def __init__(self, code, message="", status=400):
        super().__init__(message or code)
        self.code, self.message, self.status = code, message, status


def post_form(url, fields):
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode(), method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        code, msg = error_from_body(e.read(), e.code)
        raise AppError(code, msg, e.code)


def error_from_body(raw, status):
    """Pull a code and message out of OAuth, Responses or admission error bodies."""
    try:
        j = json.loads(raw)
    except ValueError:
        return f"http_{status}", raw[:300].decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)[:300]
    if isinstance(j.get("error"), dict):
        e = j["error"]
        return e.get("code") or e.get("type") or f"http_{status}", e.get("message", "")
    if isinstance(j.get("error"), str):
        return j["error"], j.get("error_description", "")
    if "detail" in j:
        return f"http_{status}", str(j["detail"])
    return f"http_{status}", ""


# ---------------------------------------------------------------- ID token
def validate_id_token(id_token, client_id, nonce):
    try:
        _, payload, _ = id_token.split(".")
        claims = json.loads(b64url_decode(payload))
    except Exception:
        raise AppError("invalid_id_token", "ChatGPT did not return a readable identity.")
    if str(claims.get("iss", "")).rstrip("/") != AUTH_BASE:
        raise AppError("invalid_id_token", "Unexpected token issuer.")
    aud = claims.get("aud")
    if client_id not in (aud if isinstance(aud, list) else [aud]):
        raise AppError("invalid_id_token", "Token audience does not match this app.")
    if float(claims.get("exp", 0)) < time.time() - 60:
        raise AppError("invalid_id_token", "Identity token expired.")
    if claims.get("nonce") != nonce:
        raise AppError("invalid_id_token", "Sign-in nonce did not match.")
    verify_signature(id_token, client_id)
    return claims


def verify_signature(id_token, client_id):
    """Check the JWT signature when PyJWT is available. The token always arrives
    straight from OpenAI's token endpoint over TLS, which OIDC accepts for the
    code flow; the signature check is extra assurance."""
    try:
        import jwt  # PyJWT
    except ImportError:
        return
    try:
        with urllib.request.urlopen(AUTH_BASE + "/.well-known/openid-configuration", timeout=10) as r:
            jwks_uri = json.loads(r.read())["jwks_uri"]
        key = jwt.PyJWKClient(jwks_uri, timeout=10).get_signing_key_from_jwt(id_token).key
    except Exception as e:  # key server unreachable: keep the TLS-based assurance, don't block sign-in
        print(f"Note: couldn't fetch OpenAI's signing keys ({e}); skipped the extra signature check.")
        return
    try:
        jwt.decode(id_token, key, algorithms=["RS256", "ES256"], audience=client_id,
                   options={"verify_iss": False})
    except jwt.PyJWTError as e:
        raise AppError("invalid_id_token", f"Identity signature check failed: {e}")


# ---------------------------------------------------------------- tokens
UNUSABLE_REFRESH = {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
                    "refresh_token_invalidated", "refresh_token_reused"}


def clear_tokens(cred):
    """Drop tokens but keep the registration (issued client ID) for the next sign-in."""
    for k in ("access_token", "refresh_token", "expires_at", "id_token", "scopes"):
        cred.pop(k, None)
    save_cred(cred)


def access_token(force_refresh=False):
    with LOCK:
        cred = load_cred()
        if not cred.get("access_token") and not cred.get("refresh_token"):
            raise AppError("not_signed_in", "Continue with ChatGPT to sign in.", 401)
        if PLAN_SCOPE not in cred.get("scopes", []):
            raise AppError("plan_usage_not_enabled", "ChatGPT plan usage isn't enabled for this app.", 403)
        if not force_refresh and cred.get("access_token") and cred.get("expires_at", 0) - time.time() > 120:
            return cred["access_token"]
        if not cred.get("refresh_token"):
            clear_tokens(cred)
            raise AppError("session_expired", "Your ChatGPT session ended. Sign in again.", 401)
        try:
            data = post_form(TOKEN_URL, {"grant_type": "refresh_token", "client_id": cred["client_id"],
                                         "refresh_token": cred["refresh_token"], "resource": RESOURCE})
        except AppError as e:
            if e.code in UNUSABLE_REFRESH:
                clear_tokens(cred)
                raise AppError("session_expired", "Your ChatGPT session ended. Sign in again.", 401)
            raise
        cred["access_token"] = data["access_token"]
        if data.get("refresh_token"):
            cred["refresh_token"] = data["refresh_token"]  # rotating
        if data.get("id_token"):
            cred["id_token"] = data["id_token"]
        if data.get("scope"):
            cred["scopes"] = data["scope"].split()
        cred["expires_at"] = time.time() + int(data.get("expires_in", 3600))
        cred["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        save_cred(cred)
        return cred["access_token"]


# ---------------------------------------------------------------- page reader
class TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "aside", "form", "button",
            "iframe", "template", "select"}
    BLOCK = {"p", "div", "section", "article", "br", "tr", "blockquote", "pre", "figcaption",
             "dd", "dt", "table", "ul", "ol", "figure", "main"}

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.parts, self.images = base, [], []
        self.skip = self.math = 0
        self.title, self.in_title, self.caption = "", False, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.math:
            self.math += tag == "math"
            return
        if tag in self.SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "title":
            self.in_title = True
        elif tag == "math":
            alt = (a.get("alttext") or "").strip()
            if alt:
                self.parts.append(f"\n$${alt}$$\n" if a.get("display") == "block" else f" ${alt}$ ")
            self.math = 1
        elif tag in ("h1", "h2", "h3", "h4"):
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "img" and len(self.images) < 10:
            src = urllib.parse.urljoin(self.base, a.get("src") or "")
            alt = (a.get("alt") or "").strip()
            w = a.get("width") or ""
            small = w.isdigit() and int(w) < 120
            if src.startswith("https://") and not small and not re.search(r"logo|icon|sprite|avatar|badge|pixel", src, re.I):
                self.images.append({"src": src, "caption": alt[:160]})

    def handle_endtag(self, tag):
        if self.math:
            self.math -= tag == "math"
            return
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag == "title":
            self.in_title = False
        elif tag in self.BLOCK or tag in ("h1", "h2", "h3", "h4", "li"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self.math or self.skip:
            return
        if self.in_title:
            self.title += data
        else:
            self.parts.append(data)

    def text(self):
        raw = "".join(self.parts)
        lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in raw.splitlines()]
        out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
        return out


def public_url(url):
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def http_get(url, limit=6_000_000):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; Let AI Explain local reader)",
        "Accept": "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8,*/*;q=0.3"})
    with urllib.request.urlopen(req, timeout=25) as r:
        body = r.read(limit)
        return r.geturl(), r.headers.get("Content-Type", ""), body, r.headers.get_content_charset() or "utf-8"


def read_page(url):
    if not public_url(url):
        raise AppError("bad_url", "That link can't be read.")
    candidates = [url]
    m = re.match(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf|html)/([^?#]+?)(?:\.pdf)?/?$", url)
    if m:  # arXiv: the HTML rendering has full text and figures; fall back to the abstract page
        candidates = [f"https://arxiv.org/html/{m.group(1)}", f"https://arxiv.org/abs/{m.group(1)}"]
    last = None
    for cand in candidates:
        try:
            final, ctype, body, charset = http_get(cand)
        except (urllib.error.URLError, OSError) as e:
            last = e
            continue
        if not public_url(final):
            raise AppError("bad_url", "That link redirected somewhere that can't be read.")
        if "pdf" in ctype:
            last = AppError("unsupported_type", "PDF files can't be read yet. Paste the text instead.")
            continue
        html = body.decode(charset, "replace")
        if "text/plain" in ctype:
            return {"url": final, "title": "", "text": html[:60000], "images": []}
        ex = TextExtractor(final)
        ex.feed(html)
        text = ex.text()
        if len(text) < 200:
            last = AppError("empty_page", "That page had almost no readable text. It may need a browser to load.")
            continue
        return {"url": final, "title": ex.title.strip(), "text": text[:60000], "images": ex.images[:8]}
    if isinstance(last, AppError):
        raise last
    raise AppError("fetch_failed", f"Couldn't load that page{': ' + str(getattr(last, 'reason', last)) if last else ''}.", 502)


class PublicRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only while they stay on public hosts."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public_url(newurl):
            raise AppError("bad_url", "That link redirected somewhere that can't be checked.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


CHECK_OPENER = urllib.request.build_opener(PublicRedirects)


def check_url(url):
    """Does a cited web page exist? Returns its status, final URL and <title> (reads at most ~1MB)."""
    if not public_url(url):
        raise AppError("bad_url", "That link can't be checked.")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; Let AI Explain reference check)",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"})
    try:
        with CHECK_OPENER.open(req, timeout=10) as r:
            final, status = r.geturl(), r.status
            ctype = r.headers.get("Content-Type", "")
            charset = r.headers.get_content_charset() or "utf-8"
            body = r.read(1_000_000)
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "final_url": e.geturl() or url, "title": ""}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"ok": False, "status": 0, "final_url": url, "title": "",
                "message": str(getattr(e, "reason", e))[:200]}
    if not public_url(final):
        raise AppError("bad_url", "That link redirected somewhere that can't be checked.")
    title = ""
    m = re.search(rb"<title[^>]*>(.*?)</title>", body, re.I | re.S) if "html" in ctype or not ctype else None
    if m:
        try:
            raw = m.group(1).decode(charset, "replace")
        except LookupError:
            raw = m.group(1).decode("utf-8", "replace")
        title = html_unescape(re.sub(r"\s+", " ", raw)).strip()[:300]
    return {"ok": status == 200, "status": status, "final_url": final, "title": title}


# ---------------------------------------------------------------- HTTP server
class Handler(BaseHTTPRequestHandler):
    server_version = "let-ai-explain"

    def log_message(self, fmt, *args):  # quiet; never log query strings (they carry auth codes)
        pass

    # ---- plumbing
    @property
    def origin(self):
        return f"http://{HOST}:{self.server.server_port}"

    def send_json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def host_ok(self):
        return self.headers.get("Host") == f"{HOST}:{self.server.server_port}"

    def api_ok(self):
        """Block other websites from driving this server (CSRF / DNS rebinding)."""
        if not self.host_ok() or self.headers.get("X-Let-AI-Explain") != "1":
            return False
        origin = self.headers.get("Origin")
        return origin in (None, self.origin)

    def read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2_000_000:
            raise AppError("too_large", "Request too large.", 413)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise AppError("bad_json", "Malformed request.", 400)

    # ---- routing
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if not self.host_ok():
            if (self.headers.get("Host") or "").startswith("localhost:"):
                return self.redirect(self.origin + self.path)
            return self.send_json(403, {"error": "forbidden"})
        try:
            if url.path in ("/", "/index.html"):
                return self.serve_index()
            if url.path == "/auth/start":
                return self.auth_start(urllib.parse.parse_qs(url.query))
            if url.path == CALLBACK_PATH:
                return self.auth_callback(urllib.parse.parse_qs(url.query))
            if not self.api_ok():
                return self.send_json(403, {"error": "forbidden"})
            if url.path == "/api/session":
                return self.api_session()
            if url.path == "/api/models":
                return self.api_models()
            if url.path == "/api/fetch":
                target = (urllib.parse.parse_qs(url.query).get("url") or [""])[0]
                return self.send_json(200, read_page(target))
            if url.path == "/api/check-url":
                target = (urllib.parse.parse_qs(url.query).get("url") or [""])[0]
                return self.send_json(200, check_url(target))
            self.send_json(404, {"error": "not_found"})
        except AppError as e:
            self.send_json(e.status, {"error": e.code, "message": e.message})
        except Exception as e:
            self.send_json(502, {"error": "server_error", "message": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self.api_ok():
            return self.send_json(403, {"error": "forbidden"})
        try:
            if path == "/api/respond":
                return self.api_respond(self.read_body())
            if path == "/api/signout":
                with LOCK:
                    clear_tokens(load_cred())
                return self.send_json(200, {"ok": True})
            self.send_json(404, {"error": "not_found"})
        except AppError as e:
            self.send_json(e.status, {"error": e.code, "message": e.message})
        except Exception as e:
            self.send_json(502, {"error": "server_error", "message": f"{type(e).__name__}: {e}"})

    def serve_index(self):
        try:
            body = (ROOT / "index.html").read_bytes()
        except OSError:
            return self.send_json(500, {"error": "index.html is missing next to let_ai_explain.py"})
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- sign-in
    def auth_start(self, q):
        cred = load_cred()
        verifier = b64url(secrets.token_bytes(48))
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        redirect_uri = self.origin + CALLBACK_PATH
        params = {
            "response_type": "code", "redirect_uri": redirect_uri, "scope": SCOPES, "resource": RESOURCE,
            "state": state, "nonce": nonce, "code_challenge_method": "S256",
            "code_challenge": b64url(hashlib.sha256(verifier.encode()).digest()),
            "ext_agent_host_id": host_id(),
        }
        if cred.get("client_id"):  # returning: reuse the issued client
            params["client_id"] = cred["client_id"]
            if cred.get("id_token"):
                params["id_token_hint"] = cred["id_token"]
            if cred.get("email"):
                params["login_hint"] = cred["email"]
            if (q.get("reconsent") or [""])[0] == "1":
                params["prompt"] = "consent"  # user chose to enable plan usage after declining
        else:  # first-time dynamic registration
            params["client_id"] = "dynamic_agent_client"
            params["agent_name_hint"] = APP_NAME
        now = time.time()
        for s in [s for s, p in PENDING.items() if now - p["created"] > 900]:
            PENDING.pop(s, None)
        PENDING[state] = {"verifier": verifier, "nonce": nonce, "client_id": params["client_id"],
                          "redirect_uri": redirect_uri, "created": now}
        self.redirect(AUTHORIZE_URL + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote))

    def auth_callback(self, q):
        get = lambda k: (q.get(k) or [""])[0]
        pending = PENDING.pop(get("state"), None)
        if not pending:
            return self.redirect("/?signin=error&reason=expired")
        if get("error"):
            return self.redirect("/?signin=denied" if get("error") == "access_denied"
                                 else "/?signin=error&reason=" + urllib.parse.quote(get("error")))
        try:
            with LOCK:
                cred = load_cred()
                cb_client = get("client_id")
                if pending["client_id"] == "dynamic_agent_client":
                    if not cb_client:
                        raise AppError("registration_incomplete", "ChatGPT didn't return an app registration.")
                    client_id = cb_client
                    # Keep the issued client ID even if the code exchange fails below.
                    cred = {"client_id": client_id}
                    save_cred(cred)
                else:
                    client_id = pending["client_id"]
                    if cb_client and cb_client != client_id:
                        raise AppError("client_mismatch", "ChatGPT returned a different app registration.")
                data = post_form(TOKEN_URL, {
                    "grant_type": "authorization_code", "client_id": client_id, "code": get("code"),
                    "code_verifier": pending["verifier"], "redirect_uri": pending["redirect_uri"],
                    "resource": RESOURCE})
                if not isinstance(data.get("id_token"), str):
                    raise AppError("invalid_id_token", "ChatGPT did not return a verifiable identity.")
                claims = validate_id_token(data["id_token"], client_id, pending["nonce"])
                if cred.get("subject") and cred["subject"] != claims.get("sub"):
                    raise AppError("account_mismatch", "That's a different ChatGPT account than this connection.")
                scopes = (data.get("scope") or get("scope") or "").split()
                cred.update({
                    "client_id": client_id, "issuer": AUTH_BASE, "subject": claims.get("sub"),
                    "email": claims.get("email") or cred.get("email"), "ext_agent_host_id": host_id(),
                    "id_token": data["id_token"], "access_token": data.get("access_token"),
                    "refresh_token": data.get("refresh_token") or cred.get("refresh_token"),
                    "expires_at": time.time() + int(data.get("expires_in", 3600)), "scopes": scopes,
                    "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                })
                save_cred(cred)
            self.redirect("/?signin=ok" if PLAN_SCOPE in scopes else "/?signin=noplan")
        except AppError as e:
            self.redirect("/?signin=error&reason=" + urllib.parse.quote(e.code))
        except Exception as e:
            print(f"Sign-in failed: {type(e).__name__}: {e}")
            self.redirect("/?signin=error&reason=" + urllib.parse.quote(type(e).__name__))

    # ---- API
    def api_session(self):
        cred = load_cred()
        connected = bool(cred.get("access_token") or cred.get("refresh_token"))
        self.send_json(200, {"connected": connected, "email": cred.get("email") if connected else None,
                             "sharing": connected and PLAN_SCOPE in cred.get("scopes", []),
                             "usage_url": USAGE_URL})

    def api_models(self):
        for attempt in (0, 1):
            token = access_token(force_refresh=attempt == 1)
            req = urllib.request.Request(API + "/models", headers={"Authorization": "Bearer " + token})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    j = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    continue
                code, msg = error_from_body(e.read(), e.code)
                raise AppError(code, msg, e.code)
        if isinstance(j.get("models"), list):
            models = [{"slug": m.get("slug"), "name": m.get("display_name") or m.get("slug")}
                      for m in j["models"] if m.get("visibility", "list") == "list" and m.get("slug")]
        else:
            models = [{"slug": m["id"], "name": m["id"]} for m in j.get("data", []) if m.get("id")]
        self.send_json(200, {"models": models})

    def emit(self, obj):
        self.wfile.write((json.dumps(obj) + "\n").encode())
        self.wfile.flush()

    def api_respond(self, body):
        model = (body.get("model") or "").strip()
        if not model:
            raise AppError("no_model", "Choose a model first.")
        content = body.get("input") or ""
        # Optional small images (the image-search check): inline data URLs only, so OpenAI never fetches a URL.
        images = body.get("images") or []
        if images:
            if not isinstance(images, list) or len(images) > 6 or not all(
                    isinstance(u, str) and len(u) < 400_000 and IMAGE_DATA_URL.match(u) for u in images):
                raise AppError("bad_images", "Images must be up to 6 small PNG, JPEG, GIF or WebP data URLs.", 400)
            content = [{"type": "input_text", "text": str(content)}] + [
                {"type": "input_image", "image_url": u, "detail": "low"} for u in images]
        payload = json.dumps({
            "model": model, "instructions": body.get("instructions") or "",
            "input": [{"role": "user", "content": content}],
            "store": False, "stream": True,
        }).encode()
        upstream = None
        for attempt in (0, 1):
            token = access_token(force_refresh=attempt == 1)
            req = urllib.request.Request(API + "/responses", data=payload, method="POST", headers={
                "Authorization": "Bearer " + token, "Content-Type": "application/json",
                "Accept": "text/event-stream"})
            try:
                upstream = urllib.request.urlopen(req, timeout=300)
                break
            except urllib.error.HTTPError as e:
                raw = e.read()
                if e.code == 401 and attempt == 0:
                    continue
                code, msg = error_from_body(raw, e.code)
                raise AppError(code, msg, e.code if e.code < 500 else 502)
            except urllib.error.URLError as e:
                raise AppError("upstream_unreachable", f"Couldn't reach OpenAI: {e.reason}", 502)

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        completed = False
        try:
            with upstream:
                for raw in upstream:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        ev = json.loads(data)
                    except ValueError:
                        continue
                    t = ev.get("type")
                    if t == "response.output_text.delta":
                        self.emit({"delta": ev.get("delta", "")})
                    elif t == "response.completed":
                        completed = True
                        self.emit({"done": True})
                        break
                    elif t == "response.failed":
                        err = (ev.get("response") or {}).get("error") or {}
                        return self.emit({"error": err.get("code") or "response_failed", "message": err.get("message", "")})
                    elif t == "response.incomplete":
                        det = (ev.get("response") or {}).get("incomplete_details") or {}
                        return self.emit({"error": "incomplete", "message": det.get("reason", "")})
                    elif t == "error":
                        err = ev.get("error") if isinstance(ev.get("error"), dict) else ev
                        return self.emit({"error": err.get("code") or "error", "message": err.get("message", "")})
            if not completed:
                self.emit({"error": "interrupted"})
        except (BrokenPipeError, ConnectionResetError):
            pass  # the page stopped listening (Stop button or reload)
        except Exception:
            try:
                self.emit({"error": "interrupted"})
            except OSError:
                pass


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def main():
    if not (ROOT / "index.html").exists():
        sys.exit("index.html must sit next to let_ai_explain.py")
    migrate_config()
    try:
        server = Server((HOST, PREFERRED_PORT), Handler)
    except OSError:
        server = Server((HOST, 0), Handler)  # any free port works for the loopback callback
    url = f"http://{HOST}:{server.server_port}/"
    print(f"{APP_NAME} is running at {url}")
    print(f"Credentials are stored in {CONFIG_DIR} (owner-only). Press Ctrl+C to stop.")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
