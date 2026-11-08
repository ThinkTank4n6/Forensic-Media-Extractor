"""Only this program's own page may drive this program.

The GUI listens on 127.0.0.1, and a browser lets any website send requests
there. Two things stood in the way of nothing:

- Every POST was read with get_json(force=True), so a request marked
  text/plain -- which a browser sends cross-site without asking the target
  first -- was taken as JSON. A page on any site, open in the examiner's
  browser, could start an extraction of the accepted image and point its
  output at a folder of its choosing.
- Nothing looked at the Host header, so a site that re-points its own name
  at 127.0.0.1 (DNS rebinding) became same-origin and could read case
  media and file listings as well.

The guard closes both, for every route on the app it is installed on:

- The Host must name this machine's loopback address, or a host the person
  running the program allowed. A rebound name is refused, reads included.
- A request that changes anything (POST, PUT, PATCH, DELETE), or that names
  a path on this machine (?path=), must carry the X-FME-Request header. A browser will not add a custom header to a
  cross-site request without asking the server first, and this server never
  says yes, so only this program's own page can send one. An Origin, when
  the browser sends one, must be this server's.
"""
import hmac
import os
import secrets
from typing import Optional
from urllib.parse import urlsplit

from flask import jsonify, request

HEADER = "X-FME-Request"
UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
WILDCARD = frozenset({"", "0.0.0.0", "::"})


def _hostname(host: str) -> str:
    """The name part of a Host header or netloc, without port or brackets."""
    host = (host or "").strip().lower()
    if host.startswith("["):                        # [::1]:5001
        return host[1:host.find("]")] if "]" in host else host[1:]
    if host.count(":") > 1:                         # a bare IPv6 address
        return host
    return host.split(":", 1)[0]


def allowed_hosts(bound: Optional[str] = None) -> set:
    """Loopback, the address the server was bound to, and FME_ALLOWED_HOSTS."""
    names = set(LOOPBACK)
    if bound and _hostname(bound) not in WILDCARD:
        names.add(_hostname(bound))
    for extra in os.environ.get("FME_ALLOWED_HOSTS", "").split(","):
        if extra.strip():
            names.add(_hostname(extra))
    return names


def install(app, bound: Optional[str] = None) -> None:
    """Refuse requests that did not come from this program's own page."""
    app.config["FME_ALLOWED_HOSTS"] = allowed_hosts(bound)

    def refuse(why: str):
        response = jsonify({"ok": False, "error": f"Refused: {why}"})
        response.status_code = 403
        return response

    @app.after_request
    def _evidence_is_inert(response):
        # Recovered files are the suspect's files. Served from this origin
        # with a guessed type and nothing else, an SVG or HTML file opened
        # through its link ran its scripts as this program's own page --
        # with the run controls, the case files and the local disk behind
        # it. The page only ever shows evidence through <img>, <video> and
        # <audio>, which a sandbox policy does not affect; a document
        # opened directly gets no scripts, no requests and no sniffing.
        if request.path in EVIDENCE_PATHS:
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Content-Security-Policy"] = (
                "sandbox; default-src 'none'; img-src 'self' data:; "
                "media-src 'self'; style-src 'unsafe-inline'")
        return response

    @app.after_request
    def _remember_key(response):
        # The key arrives once, in the address printed at start-up; the page
        # then carries it as a cookie. See require_key.
        key = app.config.get("FME_ACCESS_KEY")
        if key and request.args.get("key") and _has_key(key):
            response.set_cookie(KEY_COOKIE, key, httponly=True, samesite="Strict",
                                secure=request.is_secure)
        return response

    @app.before_request
    def _only_our_own_page():
        key = app.config.get("FME_ACCESS_KEY")
        if key and not _has_key(key):
            return refuse("this program is reachable from other machines, so "
                          "it needs the access key printed in its console "
                          "when it started. Open the address it printed.")
        if _hostname(request.host) not in app.config["FME_ALLOWED_HOSTS"]:
            return refuse(f"this program does not answer to the name "
                          f"{_hostname(request.host)!r}. To reach it from "
                          f"another machine, start it with --host set to "
                          f"that name or list it in FME_ALLOWED_HOSTS.")
        # A read that names a path on this machine is guarded like a write.
        # Any website could make the browser send one -- an <img> pointed at
        # /api/content/model?path=/dev/zero tied up a thread hashing for
        # ever, and on Windows a \\host\share path makes the machine
        # connect out and offer the examiner's credentials (v2026.11.317).
        if request.method not in UNSAFE and "path" not in request.args:
            return None
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc.lower() != request.host.lower():
            return refuse("the request came from another website.")
        if request.headers.get(HEADER) != "1":
            return refuse("the request did not come from this program's page.")
        return None


KEY_COOKIE = "fme_key"

# Routes that hand back bytes recovered from evidence.
EVIDENCE_PATHS = frozenset({"/api/file", "/api/thumb", "/api/preview",
                            "/api/frame", "/api/download", "/api/media/download"})


def _has_key(key: str) -> bool:
    offered = (request.args.get("key") or request.headers.get("X-FME-Key")
               or request.cookies.get(KEY_COOKIE) or "")
    return hmac.compare_digest(offered.encode(), key.encode())


def require_key(app) -> str:
    """Demand a key on every request, for a GUI other machines can reach.

    Listening on more than this machine's loopback address made the GUI --
    the evidence it shows, the runs it starts -- available to anyone who
    could reach the port, with no sign-in at all. Bound like that, it now
    makes a fresh random key at start-up and answers nothing without it.
    The address printed in the console carries it once; the reply sets it
    as a cookie (HttpOnly, SameSite=Strict), so the page then works as
    usual. Returns the key.
    """
    key = secrets.token_urlsafe(24)
    app.config["FME_ACCESS_KEY"] = key
    return key


def allow(app, host: str) -> None:
    """Accept one more host name, as --host does for the address it binds."""
    if _hostname(host) not in WILDCARD:
        app.config.setdefault("FME_ALLOWED_HOSTS", set(LOOPBACK)).add(_hostname(host))


def new_nonce() -> str:
    """A fresh value for the page's one script tag, new on every load."""
    return secrets.token_urlsafe(18)


def page_policy(nonce: str) -> str:
    """The Content-Security-Policy for the interface page itself.

    The page draws file names, EXIF fields and paths recovered from evidence
    with innerHTML. One unescaped quote in a camera's Make tag was enough to
    run script with the run controls and the local disk behind it. With this
    policy only the page's own script tag (it carries the nonce) runs: an
    injected <script>, onerror= or javascript: URL does not.

    style-src keeps 'unsafe-inline' because the page and app.js set style=""
    attributes throughout; a nonce cannot cover attributes. Styles cannot run
    script, so that is the strictest setting the page works under. img-src
    and media-src allow data: and blob: for decoded video frames, the logo
    preview and downloads made in the page. worker-src allows blob: for the
    case map's MapLibre worker.
    """
    return ("default-src 'self'; "
            f"script-src 'nonce-{nonce}'; "
            "script-src-attr 'none'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "media-src 'self' data: blob:; "
            "connect-src 'self'; "
            # The case map's MapLibre runs its tile decoding in a worker it
            # makes from a blob: of its own code. Only script already running
            # under the nonce can make one.
            "worker-src 'self' blob:; "
            "font-src 'self' data:; "
            "object-src 'none'; "
            "base-uri 'none'; "
            "form-action 'self'; "
            "frame-ancestors 'none'")


def contained(base: str, path: str) -> bool:
    """Is `path` inside the folder `base`? Both absolute.

    os.path.commonpath raises ValueError for paths on different Windows
    drives, and a request naming "D:\\..." under a case on C: answered with a
    500 and a traceback instead of being refused like any other path that
    leaves the case.
    """
    try:
        return os.path.commonpath([base, path]) == base
    except ValueError:
        return False
