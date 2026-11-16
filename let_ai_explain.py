#!/usr/bin/env python3
"""Let AI Explain: a local explainer that runs on your ChatGPT plan.

Run:  python3 let_ai_explain.py
Then use the page it opens at http://let-ai-explain.localhost:1455/ (or http://127.0.0.1:1455/)

Uses only the Python standard library. If PyJWT with crypto support is
installed (pip install "pyjwt[crypto]"), the ID token signature is also checked.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from lae_reader import ReaderError, check_url, read_page  # page reader, shared with the hosted site

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
FRIENDLY_HOST = "let-ai-explain.localhost"  # nicer address for the browser; *.localhost is this computer
PREFERRED_PORT = 1455
# Files the page needs besides index.html (the "Install app" manifest, service worker and icons).
STATIC = {"/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
          "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
          "/providers.js": ("providers.js", "text/javascript; charset=utf-8"),
          "/icons/icon-192.png": ("icons/icon-192.png", "image/png"),
          "/icons/icon-512.png": ("icons/icon-512.png", "image/png"),
          "/icons/icon-maskable-512.png": ("icons/icon-maskable-512.png", "image/png")}
PIXEL = bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")
CALLBACK_PATH = "/auth/callback"
USAGE_URL = "https://chatgpt.com/settings/usage"

# Other ways to connect: OpenRouter (OAuth) and your own API key.
OPENROUTER_API = "https://openrouter.ai/api/v1"
OPENROUTER_AUTH = "https://openrouter.ai/auth"
OPENROUTER_CALLBACK = "/auth/openrouter/callback"
OPENROUTER_FREE = "openrouter/free"
APP_URL = "https://github.com/viyaleta/let-ai-explain"  # sent to OpenRouter as HTTP-Referer
ANTHROPIC_API = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
PROVIDERS = {
    "chatgpt": {"name": "ChatGPT", "usage_url": USAGE_URL},
    "openrouter": {"name": "OpenRouter", "usage_url": "https://openrouter.ai/settings/credits"},
    "openai": {"name": "OpenAI", "usage_url": "https://platform.openai.com/usage"},
    "anthropic": {"name": "Anthropic", "usage_url": "https://console.anthropic.com/settings/usage"},
    "gemini": {"name": "Google Gemini", "usage_url": "https://aistudio.google.com/usage"},
}
KEY_PROVIDERS = ("openai", "anthropic", "gemini")  # connected with a pasted API key
API_KEY_SHAPE = re.compile(r"[\x21-\x7e]{8,512}")  # printable, no spaces: safe in a header
# ChatGPT sign-in fields kept at the top level of credentials.json (as before other providers existed).
CHATGPT_FIELDS = {"client_id", "issuer", "subject", "email", "ext_agent_host_id", "id_token",
                  "access_token", "refresh_token", "expires_at", "scopes", "saved_at"}

# Where index.html lives: next to this file, or inside the bundle of the desktop app.
ROOT = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent)
CONFIG_ENV = os.environ.get("LET_AI_EXPLAIN_HOME") or os.environ.get("EXPLAINIT_HOME")  # old name still honored
UNIX_CONFIG_DIR = Path.home() / ".config" / "let-ai-explain"
if sys.platform == "win32":  # Windows keeps app data in %APPDATA%
    DEFAULT_CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "let-ai-explain"
else:
    DEFAULT_CONFIG_DIR = UNIX_CONFIG_DIR
CONFIG_DIR = Path(CONFIG_ENV or DEFAULT_CONFIG_DIR)
LEGACY_CONFIG_DIR = Path.home() / ".config" / "explainit"  # folder used before the rename
HOST_FILE = CONFIG_DIR / "host.json"
CRED_FILE = CONFIG_DIR / "credentials.json"
PREFS_FILE = CONFIG_DIR / "prefs.json"
HISTORY_DIR = CONFIG_DIR / "history"
HISTORY_MAX, HISTORY_DAYS = 100, 30  # keep the newest 100 sessions, none older than 30 days
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
PREFS = {"theme": ("light", "dark", None), "level": ("simple", "standard", "expert")}

PENDING = {}            # state -> pending authorization attempt
DESKTOP = None          # set by desktop.py: opens links, saves and prints for the app window
AUTH_RESULT = {"seq": 0, "signin": None, "reason": None}  # last sign-in outcome, polled by the app window
LOCK = threading.Lock()  # guards credential reads/writes and refresh


# ---------------------------------------------------------------- storage
def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def write_private(path, data):
    """Atomic write with owner-only permissions (folders 700, files 600)."""
    for folder in [CONFIG_DIR] + ([path.parent] if path.parent != CONFIG_DIR else []):
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(folder, 0o700)
        except OSError:
            pass
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2 if path.parent == CONFIG_DIR else None, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def migrate_config():
    """Move an older settings folder over once, so existing sign-ins keep working."""
    if CONFIG_ENV or CONFIG_DIR.exists():
        return
    for old in (UNIX_CONFIG_DIR, LEGACY_CONFIG_DIR):  # ~/.config/let-ai-explain matters on Windows only
        if old == CONFIG_DIR or not old.is_dir():
            continue
        try:
            CONFIG_DIR.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(CONFIG_DIR))
            print(f"Moved your settings from {old} to {CONFIG_DIR}.")
        except OSError as e:
            print(f"Note: couldn't move {old} to {CONFIG_DIR} ({e}). You may need to sign in again.")
        return


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


# ---------------------------------------------------------------- history and preferences
HISTORY_LOCK = threading.Lock()


def history_path(sid):
    if not isinstance(sid, str) or not SESSION_ID.fullmatch(sid):
        raise AppError("bad_id", "That history entry doesn't exist.", 404)
    return HISTORY_DIR / f"{sid}.json"


def history_meta(s):
    return {"id": s.get("id"), "title": str(s.get("title") or "")[:300], "level": s.get("level"),
            "ts": s.get("ts") if isinstance(s.get("ts"), (int, float)) else 0}


def history_list(keep=None):
    """Metadata of saved sessions, newest first, after removing old and extra ones (never `keep`)."""
    with HISTORY_LOCK:
        metas = []
        for f in HISTORY_DIR.glob("*.json") if HISTORY_DIR.is_dir() else []:
            s = read_json(f)
            if s is None:
                continue  # unreadable right now: leave it alone
            if isinstance(s, dict) and s.get("id") == f.stem:
                metas.append(history_meta(s))
            else:
                f.unlink(missing_ok=True)  # not a session of ours
        metas.sort(key=lambda m: m["ts"], reverse=True)
        cutoff = (time.time() - HISTORY_DAYS * 86400) * 1000  # the page's timestamps are in ms
        kept = []
        for m in metas:
            if m["id"] != keep and (m["ts"] < cutoff or len(kept) >= HISTORY_MAX):
                (HISTORY_DIR / f"{m['id']}.json").unlink(missing_ok=True)
            else:
                kept.append(m)
        return kept


def load_prefs():
    p = read_json(PREFS_FILE)
    return {k: v for k, v in (p if isinstance(p, dict) else {}).items() if k in PREFS and v in PREFS[k]}


# ---------------------------------------------------------------- helpers
def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64url_decode(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class AppError(ReaderError):
    """An error the page shows. retryable marks a passing upstream failure (the free router)."""


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


# ---------------------------------------------------------------- connections
def active_provider(cred):
    """Which connection is in use. Files from before other providers existed have no "provider"."""
    if "provider" not in cred:
        return "chatgpt" if cred.get("access_token") or cred.get("refresh_token") else None
    p = cred.get("provider")
    return p if p in PROVIDERS else None


def has_connection(cred, provider):
    if provider == "chatgpt":
        return bool(cred.get("access_token") or cred.get("refresh_token"))
    return bool((cred.get("keys") or {}).get(provider))


def set_active(cred, provider):
    cred["provider"] = provider
    cred["model"] = (cred.get("models") or {}).get(provider) if provider else None


def mask_key(key):
    return "••••" + key[-4:] if key else ""


def api_key(provider):
    cred = load_cred()
    key = (cred.get("keys") or {}).get(provider)
    if not key:
        raise AppError("not_signed_in", f"Connect {PROVIDERS[provider]['name']} first.", 401)
    return key


def key_headers(provider, key):
    if provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    if provider == "gemini":
        return {"x-goog-api-key": key}  # header rather than ?key= so the key never sits in a URL
    h = {"Authorization": "Bearer " + key}
    if provider == "openrouter":
        h.update({"HTTP-Referer": APP_URL, "X-Title": APP_NAME})
    return h


# ---------------------------------------------------------------- provider errors
# OpenRouter failures that pass: its gateway or the routed model was briefly unavailable.
RETRYABLE_STATUS = (502, 503, 504)
IDLE_TIMEOUT = re.compile(r"upstream idle timeout|idle timeout", re.I)


def openrouter_retryable(status, message):
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = None
    return status in RETRYABLE_STATUS or bool(IDLE_TIMEOUT.search(str(message or "")))


def provider_error(provider, status, raw, images=False):
    """Map an HTTP error from any provider onto the page's error codes."""
    err = map_provider_error(provider, status, raw, images)
    if provider == "openrouter" and openrouter_retryable(status, raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw):
        err.retryable = True
    return err


def map_provider_error(provider, status, raw, images=False):
    code, msg = error_from_body(raw, status)
    code = str(code)
    blob = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw))[:4000].lower()
    name = PROVIDERS[provider]["name"]
    if images and status in (400, 404, 415, 422) and re.search(r"image|vision|multimodal|modalit", blob):
        return AppError("images_unsupported", msg or "This model doesn't accept images.", 400)
    if status == 402 or re.search(r"insufficient[_ ](credits|quota|balance)|credit balance is too low|out of credits", blob):
        return AppError("out_of_credits", msg, 402)
    # Google: a key limited to other websites, IP addresses, apps or APIs (API_KEY_*_BLOCKED).
    if provider == "gemini" and re.search(r"api_key_[a-z_]+_blocked|requests from referer", blob):
        return AppError("key_restricted", msg or "Your Google API key is restricted.", 403)
    if status == 401 or re.search(r"api_key_invalid|api key not valid|invalid x-api-key|invalid api key|incorrect api key", blob):
        return AppError("invalid_key", msg or f"{name} didn't accept the key.", 401)
    if status == 403:
        if provider == "openrouter" and re.search(r"moderat|flagged", blob):
            return AppError("blocked", msg, 403)
        return AppError("invalid_key", msg or f"{name} didn't allow this key.", 403)
    if status == 429:
        return AppError("rate_limited", msg, 429)
    if status == 404:
        return AppError("model_not_found", msg, 404)
    if status in (503, 529) or "overloaded" in blob:
        return AppError("provider_busy", msg, 503)
    return AppError(code if code and not code.isdigit() else f"http_{status}", msg, status if status < 500 else 502)


STREAM_ERROR_TYPES = {  # error types that arrive inside a stream (Anthropic names; others by status)
    "overloaded_error": "provider_busy", "rate_limit_error": "rate_limited", "authentication_error": "invalid_key",
    "permission_error": "invalid_key", "not_found_error": "model_not_found", "billing_error": "out_of_credits",
}
STREAM_STATUS = {401: "invalid_key", 403: "invalid_key", 402: "out_of_credits", 404: "model_not_found",
                 429: "rate_limited", 503: "provider_busy", 529: "provider_busy"}


def stream_error(code, message=""):
    if isinstance(code, int) or (isinstance(code, str) and code.isdigit()):
        code = STREAM_STATUS.get(int(code), f"http_{code}")
    code = STREAM_ERROR_TYPES.get(code, code) or "error"
    if re.search(r"insufficient credits|credit balance", str(message), re.I):
        code = "out_of_credits"
    return {"error": code, "message": str(message or "")[:500]}


def http_open(provider, url, headers, payload=None, timeout=30, images=False):
    """Open a request to a provider; HTTP errors become page error codes."""
    req = urllib.request.Request(url, data=payload, method="POST" if payload is not None else "GET",
                                 headers={"Accept": "application/json", **headers,
                                          **({"Content-Type": "application/json"} if payload is not None else {})})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise provider_error(provider, e.code, e.read(), images)
    except urllib.error.URLError as e:
        raise AppError("upstream_unreachable", f"Couldn't reach {PROVIDERS[provider]['name']}: {e.reason}", 502)


def http_json(provider, url, headers, payload=None):
    with http_open(provider, url, headers, payload) as r:
        return json.loads(r.read())


# ---------------------------------------------------------------- model lists
OPENAI_SKIP = re.compile(r"audio|realtime|tts|transcribe|image|search|embedding|moderation|instruct|codex|"
                         r"computer|deep-research|davinci|babbage|gpt-3\.5|-\d{4}-\d{2}-\d{2}$|-\d{4}$")
GEMINI_SKIP = re.compile(r"embedding|tts|image|live|native-audio|robotics|computer-use|aqa|learnlm|gemma|veo|imagen")


def version_of(text, pattern):
    m = re.search(pattern, text)
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def list_models(provider, key):
    """Models the key can use for chat, newest first where the provider says, plus a sensible default."""
    h = key_headers(provider, key)
    if provider == "openai":
        ids = [m["id"] for m in http_json(provider, API + "/models", h).get("data", []) if m.get("id")]
        ids = sorted((i for i in ids if re.match(r"(gpt-|o\d|chatgpt-)", i) and not OPENAI_SKIP.search(i)), reverse=True)
        general = [i for i in ids if re.fullmatch(r"gpt-\d+(\.\d+)?", i)]
        default = max(general, key=lambda i: version_of(i, r"gpt-(\d+(?:\.\d+)?)"), default=None)
        return [{"slug": i, "name": i} for i in ids], default or (ids[0] if ids else None), {}
    if provider == "anthropic":
        data = http_json(provider, ANTHROPIC_API + "/models?limit=1000", h).get("data", [])
        data.sort(key=lambda m: m.get("created_at") or "", reverse=True)
        models = [{"slug": m["id"], "name": m.get("display_name") or m["id"]} for m in data if m.get("id")]
        sonnet = [m["slug"] for m in models if "sonnet" in m["slug"]]
        return models, (sonnet or [m["slug"] for m in models] or [None])[0], {}
    if provider == "gemini":
        found, token = [], ""
        for _ in range(5):
            j = http_json(provider, GEMINI_API + "/models?pageSize=1000" + (f"&pageToken={urllib.parse.quote(token)}" if token else ""), h)
            found += j.get("models", [])
            token = j.get("nextPageToken") or ""
            if not token:
                break
        models = []
        for m in found:
            slug = (m.get("name") or "").removeprefix("models/")
            if "generateContent" in (m.get("supportedGenerationMethods") or []) and "gemini" in slug and not GEMINI_SKIP.search(slug):
                models.append({"slug": slug, "name": m.get("displayName") or slug})
        models.sort(key=lambda m: (version_of(m["slug"], r"gemini-(\d+(?:\.\d+)?)"), m["slug"]), reverse=True)
        flash = [m["slug"] for m in models if re.fullmatch(r"gemini-\d+(\.\d+)?-flash(-preview.*)?", m["slug"])]
        # Newest stable Flash first; a preview only when no stable one is listed.
        flash.sort(key=lambda s: ("preview" not in s, version_of(s, r"gemini-(\d+(?:\.\d+)?)")), reverse=True)
        return models, (flash or [m["slug"] for m in models] or [None])[0], {}
    if provider == "openrouter":
        data = http_json(provider, OPENROUTER_API + "/models", h).get("data", [])
        models = []
        for m in data:
            arch = m.get("architecture") or {}
            if m.get("id") and "text" in (arch.get("output_modalities") or ["text"]):
                models.append({"slug": m["id"], "name": m.get("name") or m["id"],
                               "images": "image" in (arch.get("input_modalities") or [])})
        models.sort(key=lambda m: m["name"].lower())
        if not any(m["slug"] == OPENROUTER_FREE for m in models):
            models.insert(0, {"slug": OPENROUTER_FREE, "name": "Free models (OpenRouter picks)", "images": True})
        credits = openrouter_credits(h)
        if credits is not None and credits <= 0:
            default = OPENROUTER_FREE
        else:
            flash = [m["slug"] for m in models if re.fullmatch(r"google/gemini-\d+(\.\d+)?-flash", m["slug"])]
            default = max(flash, key=lambda s: version_of(s, r"gemini-(\d+(?:\.\d+)?)"), default=OPENROUTER_FREE)
        return models, default, {"credits": credits}
    raise AppError("bad_provider", "Unknown provider.")


def openrouter_credits(headers):
    """Remaining OpenRouter credits in USD, or None if they can't be read."""
    try:
        d = http_json("openrouter", OPENROUTER_API + "/credits", headers).get("data") or {}
        return float(d.get("total_credits") or 0) - float(d.get("total_usage") or 0)
    except (AppError, ValueError, TypeError, OSError):
        pass
    try:
        d = http_json("openrouter", OPENROUTER_API + "/key", headers).get("data") or {}
    except (AppError, ValueError, OSError):
        return None
    if d.get("is_free_tier"):
        return 0.0
    rem = d.get("limit_remaining")
    return float(rem) if isinstance(rem, (int, float)) else None


# ---------------------------------------------------------------- stream parsers
# Each takes the decoded JSON "data:" events of one stream and yields the page's
# ndjson events: {"delta": text}, then {"done": true} or {"error": code, "message": ...}.
# OpenRouter also yields progress: {"wait": true} while queued, {"reasoning": n} (a count of
# reasoning chunks; their text isn't sent) and {"model": slug} for the model that answers.
# A stream that ends without either is reported as "interrupted" by the caller.
def sse_events(lines):
    for raw in lines:
        line = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw).strip()
        if line.startswith(":"):
            yield {"_comment": line[1:].strip()}  # ": OPENROUTER PROCESSING" keep-alives; other parsers ignore it
            continue
        if not line.startswith("data:"):
            continue  # "event:" names, blank separators
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            ev = json.loads(data)
        except ValueError:
            continue
        if isinstance(ev, dict):
            yield ev


def parse_openai(events):
    model = None
    for ev in events:
        t = ev.get("type")
        m = (ev.get("response") or {}).get("model") if isinstance(ev.get("response"), dict) else None
        if isinstance(m, str) and m and m != model:
            model = m
            yield {"model": model}  # the model that actually answers
        if t == "response.output_text.delta":
            yield {"delta": ev.get("delta", "")}
        elif t == "response.completed":
            yield {"done": True}
            return
        elif t == "response.failed":
            err = (ev.get("response") or {}).get("error") or {}
            yield {"error": err.get("code") or "response_failed", "message": err.get("message", "")}
            return
        elif t == "response.incomplete":
            det = (ev.get("response") or {}).get("incomplete_details") or {}
            yield {"error": "incomplete", "message": det.get("reason", "")}
            return
        elif t == "error":
            err = ev.get("error") if isinstance(ev.get("error"), dict) else ev
            yield {"error": err.get("code") or "error", "message": err.get("message", "")}
            return


def finish(reason, ok, cut):
    if reason is None:
        return []
    if reason in cut:
        return [{"error": "incomplete", "message": "max_output_tokens"}]
    if reason in ok:
        return [{"done": True}]
    return [{"error": "blocked", "message": str(reason)}]


def midstream_error(e):
    """An error OpenRouter sends inside the stream (the HTTP status was already 200)."""
    err = stream_error(e.get("code") or "error", e.get("message", ""))
    if err["error"] == "error" or err["error"].startswith("http_5"):
        err["error"] = "model_error"  # the chosen model failed partway; the page words it plainly
    if openrouter_retryable(e.get("code"), e.get("message", "")):
        err["retryable"] = True
    return err


def parse_openrouter(events):
    reason, model, reasoning = None, None, 0
    for ev in events:
        if "_comment" in ev:
            yield {"wait": True}
            continue
        if isinstance(ev.get("error"), dict):
            yield midstream_error(ev["error"])
            return
        if isinstance(ev.get("model"), str) and ev["model"] and ev["model"] != model:
            model = ev["model"]
            yield {"model": model}
        choices = ev.get("choices") or []
        if not choices:
            continue
        ch = choices[0]
        delta = ch.get("delta") or {}
        if (isinstance(delta.get("reasoning"), str) and delta["reasoning"]) or delta.get("reasoning_details"):
            reasoning += 1
            yield {"reasoning": reasoning}
        text = delta.get("content")
        if isinstance(text, str) and text:
            yield {"delta": text}
        if ch.get("finish_reason") == "error" or isinstance(ch.get("error"), dict):
            yield midstream_error(ch.get("error") or ev.get("error") or {})
            return
        reason = ch.get("finish_reason") or reason
    yield from finish(reason, ok=("stop", "tool_calls", "function_call"), cut=("length",))


def parse_anthropic(events):
    stop = None
    for ev in events:
        t = ev.get("type")
        if t == "message_start" and isinstance((ev.get("message") or {}).get("model"), str):
            yield {"model": ev["message"]["model"]}
        if t == "content_block_delta":
            d = ev.get("delta") or {}
            if d.get("type") == "text_delta" and d.get("text"):
                yield {"delta": d["text"]}
        elif t == "message_delta":
            stop = (ev.get("delta") or {}).get("stop_reason") or stop
        elif t == "message_stop":
            yield from finish(stop or "end_turn", ok=("end_turn", "stop_sequence", "pause_turn"),
                              cut=("max_tokens", "model_context_window_exceeded"))
            return
        elif t == "error":
            e = ev.get("error") or {}
            yield stream_error(e.get("type") or "error", e.get("message", ""))
            return


def parse_gemini(events):
    reason, model = None, None
    for ev in events:
        if isinstance(ev.get("modelVersion"), str) and ev["modelVersion"] and ev["modelVersion"] != model:
            model = ev["modelVersion"]
            yield {"model": model}
        if isinstance(ev.get("error"), dict):
            e = ev["error"]
            yield stream_error(e.get("code") or e.get("status"), e.get("message", ""))
            return
        block = (ev.get("promptFeedback") or {}).get("blockReason")
        if block:
            yield {"error": "blocked", "message": block}
            return
        cands = ev.get("candidates") or []
        if not cands:
            continue
        for part in (cands[0].get("content") or {}).get("parts") or []:
            if part.get("thought"):
                continue  # thinking summaries aren't part of the answer
            if isinstance(part.get("text"), str) and part["text"]:
                yield {"delta": part["text"]}
        reason = cands[0].get("finishReason") or reason
    yield from finish(reason, ok=("STOP", "FINISH_REASON_UNSPECIFIED"), cut=("MAX_TOKENS",))


PARSERS = {"chatgpt": parse_openai, "openai": parse_openai, "openrouter": parse_openrouter,
           "anthropic": parse_anthropic, "gemini": parse_gemini}


# ---------------------------------------------------------------- request bodies
def split_data_url(u):
    head, data = u.split(",", 1)
    return head[5:].split(";")[0], data  # "image/png", base64 payload


REASONING_EFFORTS = ("minimal", "low", "medium", "high")


def build_request(provider, model, instructions, text, images, max_tokens=16000, effort=None):
    """URL path pieces and JSON body for one streamed answer in the provider's own format."""
    if provider in ("chatgpt", "openai"):
        content = text
        if images:
            content = [{"type": "input_text", "text": text}] + [
                {"type": "input_image", "image_url": u, "detail": "low"} for u in images]
        return API + "/responses", {"model": model, "instructions": instructions,
                                    "input": [{"role": "user", "content": content}],
                                    "store": False, "stream": True}
    if provider == "openrouter":
        content = text
        if images:
            content = [{"type": "text", "text": text}] + [
                {"type": "image_url", "image_url": {"url": u, "detail": "low"}} for u in images]
        messages = ([{"role": "system", "content": instructions}] if instructions else []) + [
            {"role": "user", "content": content}]
        body = {"model": model, "messages": messages, "stream": True}
        if effort in REASONING_EFFORTS:
            body["reasoning"] = {"effort": effort}  # models without reasoning ignore it
        return OPENROUTER_API + "/chat/completions", body
    if provider == "anthropic":
        content = [{"type": "image", "source": {"type": "base64", "media_type": mt, "data": d}}
                   for mt, d in map(split_data_url, images)] + [{"type": "text", "text": text}]
        body = {"model": model, "max_tokens": max_tokens, "stream": True,
                "messages": [{"role": "user", "content": content}]}
        if instructions:
            body["system"] = instructions
        return ANTHROPIC_API + "/messages", body
    if provider == "gemini":
        parts = [{"text": text}] + [{"inline_data": {"mime_type": mt, "data": d}}
                                    for mt, d in map(split_data_url, images)]
        body = {"contents": [{"role": "user", "parts": parts}]}
        if instructions:
            body["systemInstruction"] = {"parts": [{"text": instructions}]}
        return f"{GEMINI_API}/models/{urllib.parse.quote(model, safe='')}:streamGenerateContent?alt=sse", body
    raise AppError("bad_provider", "Unknown provider.")


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
        port = self.server.server_port
        return self.headers.get("Host") in (f"{HOST}:{port}", f"{FRIENDLY_HOST}:{port}")

    @property
    def friendly(self):
        return self.headers.get("Host") == f"{FRIENDLY_HOST}:{self.server.server_port}"

    def api_ok(self):
        """Block other websites from driving this server (CSRF / DNS rebinding)."""
        if not self.host_ok() or self.headers.get("X-Let-AI-Explain") != "1":
            return False
        origin = self.headers.get("Origin")
        return origin in (None, "http://" + self.headers.get("Host"))

    def read_body(self, limit=2_000_000):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
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
            if url.path in STATIC:
                return self.serve_static(*STATIC[url.path])
            if url.path == "/hello.gif":  # lets the page check that this browser reaches the friendly address
                if DESKTOP is not None:
                    return self.send_json(404, {"error": "not_found"})  # the app window stays on 127.0.0.1
                return self.serve_bytes(PIXEL, "image/gif")
            if url.path == "/auth/start":
                return self.auth_start(urllib.parse.parse_qs(url.query))
            if url.path == CALLBACK_PATH:
                return self.auth_callback(urllib.parse.parse_qs(url.query))
            if url.path == "/auth/done":
                return self.auth_done(urllib.parse.parse_qs(url.query))
            if url.path == "/auth/openrouter/start":
                return self.openrouter_start()
            if url.path == OPENROUTER_CALLBACK:
                return self.openrouter_callback(urllib.parse.parse_qs(url.query))
            if not self.api_ok():
                return self.send_json(403, {"error": "forbidden"})
            if url.path == "/api/config":  # the page runs in local mode only when this says so
                return self.send_json(200, {"mode": "local"})
            if url.path == "/api/session":
                return self.api_session()
            if url.path == "/api/history":
                return self.send_json(200, {"sessions": history_list()})
            if url.path.startswith("/api/history/"):
                s = read_json(history_path(url.path[len("/api/history/"):]))
                if not isinstance(s, dict):
                    raise AppError("not_found", "That history entry doesn't exist.", 404)
                return self.send_json(200, s)
            if url.path == "/api/prefs":
                return self.send_json(200, load_prefs())
            if url.path == "/api/models":
                return self.api_models()
            if url.path == "/api/fetch":
                target = (urllib.parse.parse_qs(url.query).get("url") or [""])[0]
                return self.send_json(200, read_page(target))
            if url.path == "/api/check-url":
                target = (urllib.parse.parse_qs(url.query).get("url") or [""])[0]
                return self.send_json(200, check_url(target))
            self.send_json(404, {"error": "not_found"})
        except ReaderError as e:
            self.send_json(e.status, e.json())
        except Exception as e:
            self.send_json(502, {"error": "server_error", "message": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self.api_ok():
            return self.send_json(403, {"error": "forbidden"})
        try:
            if path == "/api/respond":
                return self.api_respond(self.read_body())
            if path in ("/api/disconnect", "/api/signout"):
                return self.api_disconnect()
            if path == "/api/connect/key":
                return self.api_connect_key(self.read_body())
            if path == "/api/connect/use":
                return self.api_connect_use(self.read_body())
            if path == "/api/model":
                return self.api_model(self.read_body())
            if path.startswith("/api/desktop/") and DESKTOP is not None:
                return self.api_desktop(path.rsplit("/", 1)[1], self.read_body())
            self.send_json(404, {"error": "not_found"})
        except ReaderError as e:
            self.send_json(e.status, e.json())
        except Exception as e:
            self.send_json(502, {"error": "server_error", "message": f"{type(e).__name__}: {e}"})

    def do_PUT(self):
        path = urllib.parse.urlparse(self.path).path
        if not self.api_ok():
            return self.send_json(403, {"error": "forbidden"})
        try:
            if path.startswith("/api/history/"):
                sid = path[len("/api/history/"):]
                target = history_path(sid)
                body = self.read_body(limit=20_000_000)  # long explanations with charts can be big
                if not isinstance(body, dict) or body.get("id") != sid:
                    raise AppError("bad_session", "The session doesn't match its id.", 400)
                with HISTORY_LOCK:
                    write_private(target, body)
                return self.send_json(200, {"sessions": history_list(keep=sid)})
            if path == "/api/prefs":
                body = self.read_body()
                with LOCK:
                    prefs = load_prefs()
                    for k, v in (body.items() if isinstance(body, dict) else []):
                        if k in PREFS and v in PREFS[k]:
                            prefs[k] = v
                    write_private(PREFS_FILE, prefs)
                return self.send_json(200, prefs)
            self.send_json(404, {"error": "not_found"})
        except ReaderError as e:
            self.send_json(e.status, e.json())
        except Exception as e:
            self.send_json(500, {"error": "server_error", "message": f"{type(e).__name__}: {e}"})

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        if not self.api_ok():
            return self.send_json(403, {"error": "forbidden"})
        try:
            if path == "/api/history":
                with HISTORY_LOCK:
                    for f in HISTORY_DIR.glob("*.json") if HISTORY_DIR.is_dir() else []:
                        f.unlink(missing_ok=True)
                return self.send_json(200, {"sessions": []})
            if path.startswith("/api/history/"):
                with HISTORY_LOCK:
                    history_path(path[len("/api/history/"):]).unlink(missing_ok=True)
                return self.send_json(200, {"sessions": history_list()})
            self.send_json(404, {"error": "not_found"})
        except ReaderError as e:
            self.send_json(e.status, e.json())
        except Exception as e:
            self.send_json(500, {"error": "server_error", "message": f"{type(e).__name__}: {e}"})

    def serve_index(self):
        try:
            body = (ROOT / "index.html").read_bytes()
        except OSError:
            return self.send_json(500, {"error": "index.html is missing next to let_ai_explain.py"})
        self.serve_bytes(body, "text/html; charset=utf-8")

    def serve_static(self, name, ctype):
        try:
            body = (ROOT / name).read_bytes()
        except OSError:
            return self.send_json(404, {"error": "not_found"})
        self.serve_bytes(body, ctype, "no-cache")

    def serve_bytes(self, body, ctype, cache="no-store"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- sign-in
    def auth_finish(self, location, pending=None):
        """End a sign-in. Callbacks always arrive on 127.0.0.1; the browser goes back to the address
        the sign-in started from (let-ai-explain.localhost when it used that). In the desktop app the
        flow ran in the system browser, so that tab gets a short "go back to the app" page and the
        app window picks the result up from /api/session."""
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(location).query))
        with LOCK:
            AUTH_RESULT.update(seq=AUTH_RESULT["seq"] + 1, signin=q.get("signin"), reason=q.get("reason"))
        if DESKTOP is None:
            if (pending or {}).get("friendly"):
                location = f"http://{FRIENDLY_HOST}:{self.server.server_port}{location}"
            return self.redirect(location)
        self.redirect("/auth/done?" + urllib.parse.urlencode({k: v for k, v in q.items() if k in ("signin", "reason")}))

    def auth_done(self, q):
        signin = (q.get("signin") or [""])[0]
        title, text = {
            "ok": ("You're signed in", "Go back to the Let AI Explain window. You can close this tab."),
            "noplan": ("Plan usage wasn't allowed", "Go back to the Let AI Explain window to allow it or connect another way."),
            "denied": ("Sign-in was cancelled", "Go back to the Let AI Explain window to try again."),
        }.get(signin, ("Sign-in didn't finish", "Go back to the Let AI Explain window to try again."))
        body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · Let AI Explain</title><style>
:root{{color-scheme:light dark}}body{{margin:0;min-height:100vh;display:grid;place-items:center;font:16px/1.5 system-ui,sans-serif;background:#F7F8FA;color:#14171C}}
@media (prefers-color-scheme:dark){{body{{background:#15181E;color:#E6E9EF}}}}main{{max-width:26rem;padding:2rem 16px;text-align:center}}
h1{{font-size:1.4rem;margin:0 0 .5rem}}p{{margin:0;opacity:.75}}</style></head>
<body><main><h1>{title}</h1><p>{text}</p></main></body></html>""".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
                          "redirect_uri": redirect_uri, "created": now, "friendly": self.friendly}
        self.redirect(AUTHORIZE_URL + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote))

    def auth_callback(self, q):
        get = lambda k: (q.get(k) or [""])[0]
        pending = PENDING.pop(get("state"), None)
        if not pending:
            return self.auth_finish("/?signin=error&reason=expired", pending)
        if get("error"):
            return self.auth_finish("/?signin=denied" if get("error") == "access_denied"
                                 else "/?signin=error&reason=" + urllib.parse.quote(get("error")), pending)
        try:
            with LOCK:
                cred = load_cred()
                cb_client = get("client_id")
                if pending["client_id"] == "dynamic_agent_client":
                    if not cb_client:
                        raise AppError("registration_incomplete", "ChatGPT didn't return an app registration.")
                    client_id = cb_client
                    # Keep the issued client ID even if the code exchange fails below.
                    cred = {k: v for k, v in cred.items() if k not in CHATGPT_FIELDS}  # keep other connections
                    cred["client_id"] = client_id
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
                set_active(cred, "chatgpt")
                save_cred(cred)
            self.auth_finish("/?signin=ok" if PLAN_SCOPE in scopes else "/?signin=noplan", pending)
        except AppError as e:
            self.auth_finish("/?signin=error&reason=" + urllib.parse.quote(e.code), pending)
        except Exception as e:
            print(f"Sign-in failed: {type(e).__name__}: {e}")
            self.auth_finish("/?signin=error&reason=" + urllib.parse.quote(type(e).__name__), pending)

    # ---- OpenRouter sign-in (OAuth PKCE; the key it returns is stored here, never sent to the page)
    def openrouter_start(self):
        verifier = b64url(secrets.token_bytes(48))
        state = secrets.token_urlsafe(24)
        now = time.time()
        for s in [s for s, p in PENDING.items() if now - p["created"] > 900]:
            PENDING.pop(s, None)
        PENDING[state] = {"kind": "openrouter", "verifier": verifier, "created": now, "friendly": self.friendly}
        # OpenRouter accepts http://localhost on any port; the request is sent on to 127.0.0.1 by do_GET.
        params = {"callback_url": f"http://localhost:{self.server.server_port}{OPENROUTER_CALLBACK}",
                  "code_challenge": b64url(hashlib.sha256(verifier.encode()).digest()),
                  "code_challenge_method": "S256", "state": state, "key_label": APP_NAME}
        self.redirect(OPENROUTER_AUTH + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote))

    def openrouter_callback(self, q):
        get = lambda k: (q.get(k) or [""])[0]
        pending = PENDING.pop(get("state"), None) if get("state") else None
        if pending is None and not get("state"):
            # No state echoed back: use the newest OpenRouter attempt. PKCE still binds the code to our verifier.
            mine = sorted((p for p in PENDING.items() if p[1].get("kind") == "openrouter"), key=lambda p: p[1]["created"])
            if mine:
                pending = PENDING.pop(mine[-1][0])
        if not pending or pending.get("kind") != "openrouter" or time.time() - pending["created"] > 900:
            return self.auth_finish("/?signin=error&reason=expired", pending)
        if get("error") or not get("code"):
            return self.auth_finish("/?signin=denied", pending)
        try:
            data = http_json("openrouter", OPENROUTER_API + "/auth/keys", {}, json.dumps({
                "code": get("code"), "code_verifier": pending["verifier"], "code_challenge_method": "S256"}).encode())
            key = data.get("key")
            if not isinstance(key, str) or not API_KEY_SHAPE.fullmatch(key):
                raise AppError("no_key", "OpenRouter didn't return a key.")
            with LOCK:
                cred = load_cred()
                cred.setdefault("keys", {})["openrouter"] = key
                set_active(cred, "openrouter")
                save_cred(cred)
            self.auth_finish("/?signin=ok", pending)
        except AppError as e:
            self.auth_finish("/?signin=error&reason=" + urllib.parse.quote(e.code), pending)
        except Exception as e:
            print(f"OpenRouter sign-in failed: {type(e).__name__}: {e}")
            self.auth_finish("/?signin=error&reason=" + urllib.parse.quote(type(e).__name__), pending)

    # ---- API
    def api_session(self):
        cred = load_cred()
        provider = active_provider(cred)
        connected = bool(provider) and has_connection(cred, provider)
        keys = cred.get("keys") or {}
        sharing = PLAN_SCOPE in cred.get("scopes", []) if provider == "chatgpt" else connected
        saved = [{"provider": p, "name": PROVIDERS[p]["name"],
                  "key_hint": mask_key(keys.get(p)) if p in KEY_PROVIDERS else "",
                  "email": cred.get("email") if p == "chatgpt" else None}
                 for p in PROVIDERS if p != provider and has_connection(cred, p)]
        self.send_json(200, {
            "connected": connected, "provider": provider if connected else None,
            "name": PROVIDERS[provider]["name"] if connected else None,
            "model": (cred.get("models") or {}).get(provider) if connected else None,
            "key_hint": mask_key(keys.get(provider)) if connected and provider in KEY_PROVIDERS else "",
            "email": cred.get("email") if connected and provider == "chatgpt" else None,
            "status": "signed_out" if not connected else "ready" if sharing else "noplan",
            "sharing": connected and sharing,
            "usage_url": PROVIDERS[provider]["usage_url"] if connected else USAGE_URL,
            "saved": saved, "desktop": DESKTOP is not None, "auth": dict(AUTH_RESULT)})

    def current(self):
        cred = load_cred()
        provider = active_provider(cred)
        if not provider or not has_connection(cred, provider):
            raise AppError("not_signed_in", "Connect an AI first.", 401)
        return provider

    def api_models(self):
        provider = self.current()
        if provider != "chatgpt":
            models, default, extra = list_models(provider, api_key(provider))
            return self.send_json(200, {"models": models, "default": default, **extra})
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
        self.send_json(200, {"models": models, "default": None})

    def api_connect_key(self, body):
        provider, key = body.get("provider"), str(body.get("key") or "").strip()
        if provider not in KEY_PROVIDERS:
            raise AppError("bad_provider", "Choose OpenAI, Anthropic or Google Gemini.")
        if not API_KEY_SHAPE.fullmatch(key):
            raise AppError("invalid_key", "That doesn't look like an API key.", 400)
        try:
            models, default, _ = list_models(provider, key)  # the key works if it can list models
        except AppError as e:
            if e.code == "key_restricted":
                raise
            if e.code in ("invalid_key", "model_not_found") or e.status in (400, 401, 403):
                raise AppError("invalid_key", f"{PROVIDERS[provider]['name']} didn't accept that key.", 400)
            raise
        if not models:
            raise AppError("no_models", f"That key works, but it has no chat models available.", 400)
        with LOCK:
            cred = load_cred()
            cred.setdefault("keys", {})[provider] = key
            set_active(cred, provider)
            save_cred(cred)
        self.send_json(200, {"ok": True, "provider": provider, "key_hint": mask_key(key)})

    def api_connect_use(self, body):
        provider = body.get("provider")
        with LOCK:
            cred = load_cred()
            if provider not in PROVIDERS or not has_connection(cred, provider):
                raise AppError("not_signed_in", "That connection isn't saved anymore.", 400)
            set_active(cred, provider)
            save_cred(cred)
        self.send_json(200, {"ok": True})

    def api_disconnect(self):
        """Forget the active connection's credentials; other saved connections stay."""
        with LOCK:
            cred = load_cred()
            provider = active_provider(cred)
            if provider == "chatgpt":
                clear_tokens(cred)  # keeps the ChatGPT registration for the next sign-in
            elif provider:
                (cred.get("keys") or {}).pop(provider, None)
            set_active(cred, None)
            save_cred(cred)
        self.send_json(200, {"ok": True})

    def api_model(self, body):
        model = str(body.get("model") or "").strip()
        if not model or len(model) > 200:
            raise AppError("no_model", "Choose a model first.")
        with LOCK:
            cred = load_cred()
            provider = active_provider(cred)
            if not provider:
                raise AppError("not_signed_in", "Connect an AI first.", 401)
            cred.setdefault("models", {})[provider] = model
            cred["model"] = model
            save_cred(cred)
        self.send_json(200, {"ok": True})

    def api_desktop(self, action, body):
        """Things the app window can't do itself: open the system browser, save a file, print."""
        if action == "open":
            url = str(body.get("url") or "")
            if url.startswith("/auth/"):  # sign-in runs in the browser, then returns to this server
                url = self.origin + url
            elif not re.match(r"https?://", url, re.I):
                raise AppError("bad_url", "Only web links can be opened.")
            DESKTOP.open_url(url)
            return self.send_json(200, {"ok": True})
        if action == "save":
            path = DESKTOP.save_html(str(body.get("name") or "explanation.html"), str(body.get("html") or ""))
            return self.send_json(200, {"saved": path})
        if action == "print":
            DESKTOP.print_html(str(body.get("html") or ""), str(body.get("title") or "Explanation"))
            return self.send_json(200, {"ok": True})
        raise AppError("not_found", "Unknown action.", 404)

    def emit(self, obj):
        self.wfile.write((json.dumps(obj) + "\n").encode())
        self.wfile.flush()

    def open_answer(self, provider, model, instructions, text, images, effort=None):
        """Start the provider's stream; returns the open response."""
        if provider == "chatgpt":
            url, body = build_request(provider, model, instructions, text, images)
            payload = json.dumps(body).encode()
            for attempt in (0, 1):
                token = access_token(force_refresh=attempt == 1)
                req = urllib.request.Request(url, data=payload, method="POST", headers={
                    "Authorization": "Bearer " + token, "Content-Type": "application/json",
                    "Accept": "text/event-stream"})
                try:
                    return urllib.request.urlopen(req, timeout=300)
                except urllib.error.HTTPError as e:
                    raw = e.read()
                    if e.code == 401 and attempt == 0:
                        continue
                    code, msg = error_from_body(raw, e.code)
                    raise AppError(code, msg, e.code if e.code < 500 else 502)
                except urllib.error.URLError as e:
                    raise AppError("upstream_unreachable", f"Couldn't reach OpenAI: {e.reason}", 502)
        headers = {**key_headers(provider, api_key(provider)), "Accept": "text/event-stream"}
        # Some older Claude models allow fewer output tokens; step down if the first size is refused.
        for max_tokens in (16000, 8192, 4096):
            url, body = build_request(provider, model, instructions, text, images, max_tokens, effort)
            try:
                return http_open(provider, url, headers, json.dumps(body).encode(), timeout=300, images=bool(images))
            except AppError as e:
                if not (provider == "anthropic" and e.status == 400 and "max_tokens" in e.message and max_tokens > 4096):
                    raise

    def api_respond(self, body):
        provider = self.current()
        model = (body.get("model") or "").strip()
        if not model:
            raise AppError("no_model", "Choose a model first.")
        text = str(body.get("input") or "")
        # Optional small images (the image-search check): inline data URLs only, so no provider fetches a URL.
        images = body.get("images") or []
        if images and (not isinstance(images, list) or len(images) > 6 or not all(
                isinstance(u, str) and len(u) < 400_000 and IMAGE_DATA_URL.match(u) for u in images)):
            raise AppError("bad_images", "Images must be up to 6 small PNG, JPEG, GIF or WebP data URLs.", 400)
        effort = body.get("reasoning") if body.get("reasoning") in REASONING_EFFORTS else None
        upstream = self.open_answer(provider, model, body.get("instructions") or "", text, images, effort)

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if images:
            self.emit({"images": len(images)})  # lets the page know the images were sent, not dropped
        ended = False
        try:
            with upstream:
                for ev in PARSERS[provider](sse_events(upstream)):
                    self.emit(ev)
                    if "done" in ev or "error" in ev:
                        ended = True
                        break
            if not ended:
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
    # Restarting must get port 1455 back even while the OS still holds the last connections
    # (TIME_WAIT). On Windows SO_REUSEADDR would let other programs share the port, so not there.
    allow_reuse_address = sys.platform != "win32"


def main():
    if not (ROOT / "index.html").exists():
        sys.exit("index.html must sit next to let_ai_explain.py")
    migrate_config()
    try:
        server = Server((HOST, PREFERRED_PORT), Handler)
    except OSError:
        server = Server((HOST, 0), Handler)  # any free port works for the loopback callback
    url = f"http://{HOST}:{server.server_port}/"  # the page moves itself to the friendly address if this browser can reach it
    print(f"{APP_NAME} is running at http://{FRIENDLY_HOST}:{server.server_port}/")
    print(f"  (if your browser can't open that, use {url})")
    print(f"Credentials are stored in {CONFIG_DIR} (owner-only). Press Ctrl+C to stop.")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
