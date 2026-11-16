"""Page reader and reference check for Let AI Explain, shared by the local server
(let_ai_explain.py) and the hosted site's Vercel functions (api/fetch.py, api/check-url.py).

Standard library only. Every connection goes to an address that was checked to be public,
so a link can't reach this computer, the local network or a cloud metadata service, not even
through a redirect or a DNS answer that changes between the check and the connection.
"""
import http.client
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html import unescape as html_unescape
from html.parser import HTMLParser

PAGE_LIMIT = 6_000_000    # bytes read from a page
CHECK_LIMIT = 1_000_000   # bytes read when checking a reference
PAGE_TIMEOUT = 25         # seconds per request
CHECK_TIMEOUT = 10
TEXT_LIMIT = 60_000       # characters of page text returned
ANY_PORT = None           # the local server reads any port; the hosted functions pass WEB_PORTS
WEB_PORTS = (80, 443)


class ReaderError(Exception):
    """An error the page shows; the local server's AppError extends it."""
    def __init__(self, code, message="", status=400):
        super().__init__(message or code)
        self.code, self.message, self.status = code, message, status
        self.retryable = False

    def json(self):
        return {"error": self.code, "message": self.message, **({"retryable": True} if self.retryable else {})}


# ---------------------------------------------------------------- public addresses only
def public_ip(ip):
    ip = ipaddress.ip_address(str(ip).split("%")[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 is still 127.0.0.1
    return ip.is_global and not ip.is_multicast


def resolve_public(host, port):
    """The address to connect to, if every address the name resolves to is public."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ReaderError("bad_url", "That address couldn't be found.")
    addrs = [info[4][0] for info in infos]
    if not addrs or not all(public_ip(a) for a in addrs):
        raise ReaderError("bad_url", "That link points to a private address, so it can't be read.")
    return addrs[0]


def public_url(url, ports=ANY_PORT):
    """Quick check before fetching: http(s), an allowed port, and a host that resolves to public addresses."""
    try:
        p = urllib.parse.urlparse(url)
        port = p.port
    except ValueError:
        return False
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        return False
    if ports and (port or (443 if p.scheme == "https" else 80)) not in ports:
        return False
    try:
        resolve_public(p.hostname, port or 443)
    except ReaderError:
        return False
    return True


class _PinnedHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.create_connection((resolve_public(self.host, self.port), self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        sock = socket.create_connection((resolve_public(self.host, self.port), self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PinnedHTTP, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedHTTPS, req, context=self._context)


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to http(s) URLs on allowed ports (the connection itself checks the address)."""
    def __init__(self, ports):
        self.ports = ports

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p = urllib.parse.urlparse(newurl)
        try:
            port = p.port or (443 if p.scheme == "https" else 80)
        except ValueError:
            port = -1
        if p.scheme not in ("http", "https") or (self.ports and port not in self.ports):
            raise ReaderError("bad_url", "That link redirected somewhere that can't be read.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def opener(ports=ANY_PORT):
    """A urllib opener for http(s) only, connecting only to checked public addresses."""
    o = urllib.request.OpenerDirector()
    for h in (_PinnedHTTPHandler(), _PinnedHTTPSHandler(), _PublicRedirects(ports),
              urllib.request.HTTPErrorProcessor(), urllib.request.HTTPDefaultErrorHandler()):
        o.add_handler(h)
    return o


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




def http_get(url, limit=PAGE_LIMIT, ports=ANY_PORT):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; Let AI Explain reader)",
        "Accept": "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8,*/*;q=0.3"})
    with opener(ports).open(req, timeout=PAGE_TIMEOUT) as r:
        body = r.read(limit)
        return r.geturl(), r.headers.get("Content-Type", ""), body, r.headers.get_content_charset() or "utf-8"


def read_page(url, ports=ANY_PORT):
    """A page's title, readable text (as light Markdown) and up to 8 images."""
    if not public_url(url, ports):
        raise ReaderError("bad_url", "That link can't be read.")
    candidates = [url]
    m = re.match(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf|html)/([^?#]+?)(?:\.pdf)?/?$", url)
    if m:  # arXiv: the HTML rendering has full text and figures; fall back to the abstract page
        candidates = [f"https://arxiv.org/html/{m.group(1)}", f"https://arxiv.org/abs/{m.group(1)}"]
    last = None
    for cand in candidates:
        try:
            final, ctype, body, charset = http_get(cand, ports=ports)
        except (urllib.error.URLError, OSError) as e:
            last = e
            continue
        if "pdf" in ctype:
            last = ReaderError("unsupported_type", "PDF files can't be read yet. Paste the text instead.")
            continue
        try:
            html = body.decode(charset, "replace")
        except LookupError:
            html = body.decode("utf-8", "replace")
        if "text/plain" in ctype:
            return {"url": final, "title": "", "text": html[:TEXT_LIMIT], "images": []}
        ex = TextExtractor(final)
        ex.feed(html)
        text = ex.text()
        if len(text) < 200:
            last = ReaderError("empty_page", "That page had almost no readable text. It may need a browser to load.")
            continue
        return {"url": final, "title": ex.title.strip(), "text": text[:TEXT_LIMIT], "images": ex.images[:8]}
    if isinstance(last, ReaderError):
        raise last
    raise ReaderError("fetch_failed", f"Couldn't load that page{': ' + str(getattr(last, 'reason', last)) if last else ''}.", 502)


# ---------------------------------------------------------------- reference check
def check_url(url, ports=ANY_PORT):
    """Does a cited web page exist? Returns its status, final URL and <title> (reads at most ~1MB)."""
    if not public_url(url, ports):
        raise ReaderError("bad_url", "That link can't be checked.")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; Let AI Explain reference check)",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"})
    try:
        with opener(ports).open(req, timeout=CHECK_TIMEOUT) as r:
            final, status = r.geturl(), r.status
            ctype = r.headers.get("Content-Type", "")
            charset = r.headers.get_content_charset() or "utf-8"
            body = r.read(CHECK_LIMIT)
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "final_url": e.geturl() or url, "title": ""}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"ok": False, "status": 0, "final_url": url, "title": "",
                "message": str(getattr(e, "reason", e))[:200]}
    title = ""
    m = re.search(rb"<title[^>]*>(.*?)</title>", body, re.I | re.S) if "html" in ctype or not ctype else None
    if m:
        try:
            raw = m.group(1).decode(charset, "replace")
        except LookupError:
            raw = m.group(1).decode("utf-8", "replace")
        title = html_unescape(re.sub(r"\s+", " ", raw)).strip()[:300]
    return {"ok": status == 200, "status": status, "final_url": final, "title": title}
