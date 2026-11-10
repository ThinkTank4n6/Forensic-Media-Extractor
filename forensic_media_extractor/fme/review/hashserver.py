"""
hashserver.py -- one hash store, shared across a unit.

A store on a file share does not work. SQLite takes file locks that network
file systems implement badly or not at all, and two examiners contributing
at the same time across SMB is how a database gets corrupted. The fix is the
usual one: one machine owns the file, everyone else talks to it.

This is that machine. It serves lookups, source listings and contributions
over HTTP, holds the store itself locally, and writes down who asked for
what.

Three things it is deliberately strict about, because of what the store
contains -- hashes of child abuse material, and the gradings colleagues
assigned to them:

**Everyone is named.** There is no anonymous access and no shared password.
Each person gets their own token, and every lookup and contribution is
recorded against them, with the time and the address it came from. A store
that cannot say who queried it is a store nobody should put this data in.

**Rights are separate from access.** Reading, contributing and
administering are three different permissions. An examiner who should check
hashes against the store does not thereby get to add to it, and nobody gets
to import a national set by accident.

**It refuses to be careless.** It binds to localhost unless told otherwise,
it warns loudly when run without TLS on a real interface, and it never
returns a hash the caller did not already have: a lookup answers "this
digest you sent me is in set X", never "here is the set".

    python -m fme hashserver --store hashes.db --add-user jsmith --role read
    python -m fme hashserver --store hashes.db --host 0.0.0.0 --port 8787 \\
        --cert server.crt --key server.key

Clients point at it with a URL instead of a path; see remotestore.py.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import hashlib
import json
import os
import secrets
import sqlite3
import sys
import threading
import time

from fme import build_number

if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

from fme.review import hashsets
from fme.review import hashstore

# 1.1.0 waits for another writer before answering that the store is busy,
# instead of passing SQLite's "database is locked" back as a refusal. A
# server left running from before this still says the old thing, which is
# why the client checks this number and says so.
__version__ = "1.2.0"     # 1.2.0: refuses codes from another region's scheme

# How long a request waits for the store's writer before answering "busy".
SERVER_LOCK_WAIT = 15.0

LOOPBACK = ("127.0.0.1", "localhost", "::1")

# Unrecognized tokens from one address before more are refused unlogged.
# Tokens are 256 bits, so this is not about guessing: it keeps a probe, or a
# revoked token being retried by a forgotten script, from filling the access
# log, and makes either visible there.
FAILED_LIMIT = 10
FAILED_WINDOW = 300.0

ROLES = ("read", "contribute", "admin")
# What each role may do. Admin is the only one that can change the store's
# contents wholesale.
RIGHTS = {
    "read": {"lookup", "sources", "stats"},
    "contribute": {"lookup", "sources", "stats", "contribute"},
    "admin": {"lookup", "sources", "stats", "contribute", "import", "remove",
              "users", "audit"},
}
# What a version floor applies to: the operations that change what
# everyone else sees. Reading is never blocked by it.
WRITING = {"contribute", "import", "remove"}
MAX_LOOKUP = 10000
# Graded files per contribution request; the client sends in batches below it.
# Clients from v2026.11.303 send 5,000 at a time. Older clients send a whole
# case in one request, so the cap is set where a large case still fits --
# 10,000 refused every case bigger than that until each machine was updated.
MAX_CONTRIBUTE = 500_000
# Bytes per request body. Nothing capped a body, and get_json loads the whole
# of one into memory: one request could exhaust the server. A lookup batch is
# under a megabyte; 128 MiB holds the largest single-request contribution an
# older client sends (about 500,000 graded files).
MAX_BODY = 128 * 1024 * 1024

ACCOUNTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    name TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS access (
    at TEXT NOT NULL,
    who TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '');

CREATE INDEX IF NOT EXISTS access_at ON access(at);
"""


def _now() -> str:
    # Local time with its offset written in ("...T14:05:00-05:00"). Without
    # the offset a time is ambiguous for the hour the clocks go back, in a
    # record of who decided what, and when; with it, it still reads as the
    # examiner's own clock.
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Accounts:
    """Who may use the store, and a record of what they did.

    Kept beside the store rather than inside it: the store is a thing
    examiners copy, hand over and rebuild, and the list of people who may
    reach it should not travel with it.
    """

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._con = sqlite3.connect(self.path, check_same_thread=False,
                                    timeout=60)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA busy_timeout=60000")
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.executescript(ACCOUNTS_SCHEMA)
        self._con.commit()

    def add(self, name: str, role: str, reissue: bool = False) -> str:
        """Create a person and return their token. It is shown once.

        Adding a name that already had an account replaced that person's
        role and token without a word -- typing a colleague's name into Add
        signed them out and could change what they may do -- and left a
        disabled account disabled with a new token nobody could use. A name
        already in use is now refused unless `reissue` is asked for, which
        issues a new token, sets the role and enables the account, on
        purpose.
        """
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        token = secrets.token_urlsafe(32)
        with self._lock:
            exists = self._con.execute(
                "SELECT 1 FROM users WHERE name=?", (name,)).fetchone()
            if exists and not reissue:
                raise ValueError(
                    f"{name} already has an account. Re-issue it to give them "
                    f"a new token (their old one stops working).")
            if exists:
                self._con.execute(
                    "UPDATE users SET role=?, token_hash=?, disabled=0 "
                    "WHERE name=?", (role, _hash_token(token), name))
            else:
                self._con.execute(
                    "INSERT INTO users (name, role, token_hash, created_at) "
                    "VALUES (?,?,?,?)", (name, role, _hash_token(token), _now()))
            self._con.commit()
        return token

    def disable(self, name: str) -> bool:
        with self._lock:
            cur = self._con.execute(
                "UPDATE users SET disabled=1 WHERE name=?", (name,))
            self._con.commit()
        return cur.rowcount > 0

    def identify(self, token: str):
        """The person holding this token, or None."""
        if not token:
            return None
        # Under the lock like every other use of this connection: it is
        # shared between the server's threads, and a read alongside a write
        # on one connection can fail outright, signing someone out at random.
        with self._lock:
            row = self._con.execute(
                "SELECT name, role, disabled FROM users WHERE token_hash=?",
                (_hash_token(token),)).fetchone()
        if row is None or row["disabled"]:
            return None
        return {"name": row["name"], "role": row["role"]}

    def users(self) -> list:
        with self._lock:
            return [dict(r) for r in self._con.execute(
                "SELECT name, role, created_at, disabled FROM users "
                "ORDER BY name")]

    def record(self, who: str, action: str, detail: str, address: str,
               version: str = "") -> None:
        # The client's version goes in the detail where it is given, so
        # a unit with several installs can see who is running what. An
        # older client is not refused -- it is recorded, which is what
        # lets somebody notice before a case depends on it.
        if version:
            detail = f"{detail} [client v{version}]" if detail \
                else f"[client v{version}]"
        with self._lock:
            self._con.execute(
                "INSERT INTO access (at, who, action, detail, address) "
                "VALUES (?,?,?,?,?)", (_now(), who, action, detail, address))
            self._con.commit()

    def record_many(self, who: str, action: str, details: list,
                    address: str, version: str = "") -> None:
        """Several access rows in one commit, for one request's changes."""
        if version:
            details = [f"{d} [client v{version}]" for d in details]
        at = _now()
        with self._lock:
            self._con.executemany(
                "INSERT INTO access (at, who, action, detail, address) "
                "VALUES (?,?,?,?,?)",
                [(at, who, action, d, address) for d in details])
            self._con.commit()

    def usage(self) -> dict:
        """Per-person figures for the admin page, with their limits.

        **On "hours used".** The store sees requests, not sessions: it
        knows when somebody looked a hash up, not when they opened the
        program or how long they sat with it. So what is reported is
        *active time* -- the span of each run of requests less than
        thirty minutes apart, added up. An examiner grading for an hour
        without touching the store contributes nothing to it, and a
        single lookup counts as a minute. It is a floor on time spent,
        not a timesheet, and the page says so.

        Everything else here is counted, not estimated: lookups,
        contributions, the hashes in them, distinct cases, and the days
        on which anything happened.
        """
        import datetime as _dt

        GAP = _dt.timedelta(minutes=30)
        people, order = {}, []
        with self._lock:
            rows = self._con.execute(
                "SELECT who, action, detail, at FROM access ORDER BY at").fetchall()
        for row in rows:
            who = row["who"]
            if who not in people:
                people[who] = {
                    "who": who, "lookups": 0, "contributions": 0,
                    "hashes": 0, "cases": set(), "days": set(),
                    "first": row["at"], "last": row["at"],
                    "active_minutes": 0.0, "_previous": None,
                    "versions": set(),
                }
                order.append(who)
            entry = people[who]
            entry["last"] = row["at"]
            detail = row["detail"] or ""

            try:
                when = _dt.datetime.fromisoformat(row["at"])
                # Rows written before times carried their zone are local and
                # naive; later ones are UTC. Compared as this machine's local
                # time, so a log spanning the change still adds up.
                if when.tzinfo is not None:
                    when = when.astimezone().replace(tzinfo=None)
            except (TypeError, ValueError):
                when = None
            if when:
                entry["days"].add(when.date().isoformat())
                previous = entry["_previous"]
                if previous is not None and when - previous <= GAP:
                    entry["active_minutes"] += (
                        when - previous).total_seconds() / 60.0
                elif previous is not None or True:
                    # A request with nothing near it is still a minute
                    # of somebody's attention.
                    entry["active_minutes"] += 1.0
                entry["_previous"] = when

            if row["action"] == "lookup":
                entry["lookups"] += 1
            elif row["action"] in ("contribute", "import"):
                entry["contributions"] += 1
                # "123 hash(es) from DC26-0001" -- the count is the
                # first number in the detail.
                number = ""
                for ch in detail:
                    if ch.isdigit():
                        number += ch
                    elif number:
                        break
                if number:
                    entry["hashes"] += int(number)
                # Cases are what was contributed, logged "N hash(es) as
                # CASE"; this looked for " from ", which only an import's
                # detail has, so it counted imported files and never a case.
                if row["action"] == "contribute" and " as " in detail:
                    entry["cases"].add(_strip_version(
                        detail.split(" as ", 1)[1]).strip())
            version = _client_mark(detail)
            if version:
                entry["versions"].add(version)

        out = []
        for who in order:
            entry = people[who]
            entry.pop("_previous", None)
            entry["cases"] = sorted(entry["cases"])
            entry["case_count"] = len(entry["cases"])
            entry["days_active"] = len(entry["days"])
            entry["days"] = sorted(entry["days"])
            entry["versions"] = sorted(entry["versions"])
            entry["active_hours"] = round(entry.pop("active_minutes") / 60, 1)
            out.append(entry)
        return {
            "people": sorted(out, key=lambda e: -e["lookups"]),
            "note": ("Active time is the span of each run of requests "
                     "less than thirty minutes apart. The store sees "
                     "requests, not sessions: time spent grading "
                     "without touching the store is invisible to it. "
                     "Treat it as a floor, not a timesheet."),
        }

    def client_versions(self) -> list:
        """Who is running which version, most recent first.

        The question this answers: are the unit's installs the same
        program? A hash store shared by seven people, three of whom can
        remove sources, is not a place for version drift to go
        unnoticed.
        """
        # Under the lock, like every other use of this connection: the
        # threaded server shares it, and a read racing record() failed with
        # "bad parameter or other API misuse" and a 500.
        with self._lock:
            rows = self._con.execute(
                "SELECT who, detail, at FROM access "
                "WHERE detail LIKE '%[client v%'").fetchall()
        # Grouped here by the mark record() appends -- the last one in the
        # detail. Taken from the first, a set or case name containing
        # "[client v" showed a version nobody runs.
        latest: dict = {}
        for row in rows:
            version = _client_mark(row["detail"] or "")
            key = (row["who"], version)
            if version and row["at"] > latest.get(key, ""):
                latest[key] = row["at"]
        return [{"who": who, "version": version, "last_seen": at}
                for (who, version), at in sorted(
                    latest.items(), key=lambda kv: kv[1], reverse=True)]

    def summary(self) -> dict:
        """Per-person use: lookups, contributions and distinct cases.

        Cases are counted from the names people contributed under, which is
        the only count here that means anything beyond activity: it says how
        much work has actually been fed back into the store.
        """
        people = {}
        with self._lock:
            rows = self._con.execute(
                "SELECT who, action, detail, at FROM access ORDER BY at").fetchall()
        for row in rows:
            entry = people.setdefault(row["who"], {
                "who": row["who"], "lookups": 0, "contributions": 0,
                "cases": set(), "last": ""})
            if row["action"] == "lookup":
                entry["lookups"] += 1
            elif row["action"] == "contribute":
                entry["contributions"] += 1
                detail = row["detail"] or ""
                if " as " in detail:
                    entry["cases"].add(_strip_version(
                        detail.split(" as ", 1)[1]).strip())
            entry["last"] = row["at"]

        rows = []
        for entry in people.values():
            entry["cases"] = len(entry["cases"])
            rows.append(entry)
        rows.sort(key=lambda r: (-r["lookups"], r["who"]))
        with self._lock:
            total = self._con.execute(
                "SELECT COUNT(*) AS n FROM access WHERE action='lookup'"
            ).fetchone()
        return {"people": rows, "users": len(self.users()),
                "lookups": total["n"] if total else 0}

    def access(self, limit: int = 200) -> list:
        with self._lock:
            return [dict(r) for r in self._con.execute(
                "SELECT at, who, action, detail, address FROM access "
                "ORDER BY at DESC LIMIT ?", (limit,))]

    def close(self) -> None:
        with self._lock:
            self._con.commit()
            self._con.close()


def _grade_text(code, label) -> str:
    if code is None and not label:
        return "(none)"
    return f"{label} ({code})" if label else str(code)


def _client_mark(detail: str) -> str:
    """The client version record() appended to a detail -- the last mark in
    it, since a set or case name can itself contain "[client v"."""
    at = detail.rfind("[client v")
    return detail[at + 9:].split("]")[0] if at >= 0 else ""


def _strip_version(detail: str) -> str:
    at = detail.rfind(" [client v")
    return detail[:at] if at >= 0 else detail


def make_certificate(cert_path: str, key_path: str, hostnames=None,
                     days: int = 825) -> dict:
    """Write a self-signed certificate for a closed lab.

    A closed network has no certificate authority to ask, and a store that
    is reachable without TLS puts tokens and queried hashes on the wire in
    the clear. Self-signed is the right answer here: the traffic is
    encrypted, and the thing a public CA would add -- a stranger's assurance
    about who you are talking to -- is something you already know, because
    you built the machine.

    What it does not give you is protection against someone who can redirect
    traffic *and* present their own certificate. For that the clients must
    be told to trust this exact certificate, which is what --ca-cert does.

    Names matter: a certificate is checked against the name used to reach
    it, so the server's hostname and its IP address both go in.
    """
    import ipaddress
    import socket

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes as _hashes
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        raise RuntimeError(
            "Making a certificate needs the cryptography package: "
            "pip install cryptography. Or make one with openssl:\n"
            "  openssl req -x509 -newkey rsa:4096 -nodes -days 825 \\\n"
            "    -keyout server.key -out server.crt \\\n"
            "    -subj \"/CN=$(hostname)\" \\\n"
            "    -addext \"subjectAltName=DNS:$(hostname),IP:127.0.0.1\"")

    names = list(hostnames or [])
    if not names:
        names = [socket.gethostname(), "localhost"]
    alternatives = []
    for name in names:
        try:
            alternatives.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            alternatives.append(x509.DNSName(name))
    # Always valid for this machine itself. Whoever sets the server up will
    # open the admin pages on it, and a certificate that covers only the
    # names they typed sends them to a browser warning on the first page
    # they are told to visit.
    for address in ("127.0.0.1", "::1"):
        alternatives.append(x509.IPAddress(ipaddress.ip_address(address)))
    if not any(isinstance(entry, x509.DNSName) and entry.value == "localhost"
               for entry in alternatives):
        alternatives.append(x509.DNSName("localhost"))

    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, str(names[0])),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Hash store"),
    ])
    now = _dt.datetime.now(_dt.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(minutes=5))
        .not_valid_after(now + _dt.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(alternatives), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                       critical=True)
        .sign(key, _hashes.SHA256()))

    with open(key_path, "wb") as fh:
        fh.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()))
    try:
        os.chmod(key_path, 0o600)      # the key is the whole secret
    except OSError:
        pass
    with open(cert_path, "wb") as fh:
        fh.write(certificate.public_bytes(serialization.Encoding.PEM))

    return {"cert": os.path.abspath(cert_path),
            "key": os.path.abspath(key_path),
            "names": [str(n.value) for n in alternatives],
            "expires": (now + _dt.timedelta(days=days)).date().isoformat()}


def _e(value) -> str:
    import html
    return html.escape("" if value is None else str(value))


def _sign_in_form(message: str = "") -> str:
    return (
        ("<div class='warn'>" + _e(message) + "</div>" if message else "")
        + "<div class='signin'><h2>Sign in</h2>"
        "<p class='hint'>Paste the token you were issued. If you do not have "
        "one, ask whoever runs this store: tokens are issued per person, so "
        "that the record of who looked up what means something.</p>"
        "<form method='post'><input name='token' type='password' "
        "placeholder='your token' autofocus required>"
        "<button>Sign in</button></form></div>")


def _denied(person) -> str:
    if person is None:
        return _sign_in_form()
    return (f"<div class='warn'>{_e(person['name'])} has the "
            f"{_e(person['role'])} role, which does not include this page. "
            f"Ask an administrator if you need it.</div>")


# The admin pages' only script, served from /admin/admin.js. It reads what a
# page asks to confirm from a data attribute, as text: no value from the
# store is ever written into JavaScript.
ADMIN_SCRIPT = """
document.addEventListener("submit", function (event) {
  var form = event.target;
  var ask = form && form.getAttribute && form.getAttribute("data-confirm");
  if (ask && !window.confirm(ask)) { event.preventDefault(); }
});
"""

ADMIN_CSP = ("default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
             "img-src 'self' data:; form-action 'self'; base-uri 'none'; "
             "frame-ancestors 'none'")

ADMIN_STYLE = """
 :root { color-scheme: light dark; }
 body { font: 14px/1.55 -apple-system, Segoe UI, Roboto, sans-serif;
        margin: 0; background: #f4f5f7; color: #1d2129; }
 header { display: flex; align-items: center; gap: 22px; padding: 12px 26px;
          background: #11151c; color: #fff; }
 header h1 { font-size: 15px; margin: 0; font-weight: 600; letter-spacing: .02em; }
 header nav { display: flex; gap: 4px; flex: 1; }
 header nav a { color: #c9d1d9; text-decoration: none; padding: 5px 11px;
                border-radius: 6px; font-size: 13px; }
 header nav a:hover { background: #1b212b; color: #fff; }
 header .who { font-size: 12px; color: #8b949e; }
 header .who em { font-style: normal; background: #1b212b; padding: 1px 7px;
                  border-radius: 10px; margin: 0 6px; }
 header .who a { color: #4a9eff; }
 main { max-width: 1000px; margin: 0 auto; padding: 22px 26px 60px; }
 h2 { font-size: 14px; text-transform: uppercase; letter-spacing: .05em;
      color: #5a6472; margin: 26px 0 8px; }
 .hint { color: #5a6472; font-size: 12.5px; margin: 0 0 10px; }
.bar{fill:#2563eb}
.bl{fill:#374151;font-size:12px;font-family:system-ui,sans-serif}
.bv{fill:#6b7280;font-size:12px;font-family:system-ui,sans-serif;
    font-variant-numeric:tabular-nums}
svg{margin:4px 0 18px;display:block}
 .cards { display: grid; gap: 12px; margin-top: 8px;
          grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); }
 .card { background: #fff; border: 1px solid #d8dbe0; border-radius: 8px;
         padding: 14px 16px; }
 .card b { display: block; font-size: 22px; }
 .card span { color: #5a6472; font-size: 12px; }
 table { width: 100%; border-collapse: collapse; background: #fff;
         border: 1px solid #d8dbe0; border-radius: 8px; overflow: hidden; }
 th, td { text-align: left; padding: 7px 12px; border-bottom: 1px solid #eceef1;
          font-size: 13px; vertical-align: top; }
 th { background: #fafbfc; color: #5a6472; font-size: 11px;
      text-transform: uppercase; letter-spacing: .04em; }
 td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
 td.t, th.t { font-family: ui-monospace, Menlo, monospace; font-size: 12px;
              color: #5a6472; }
 form.row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
 input, select { font: inherit; padding: 7px 10px; border: 1px solid #d8dbe0;
                 border-radius: 6px; background: #fff; color: inherit;
                 min-width: 200px; }
 button { font: inherit; padding: 7px 15px; border: none; border-radius: 6px;
          background: #2f6fd8; color: #fff; cursor: pointer; }
 button.quiet { background: transparent; color: #a61b1b; padding: 2px 6px;
                font-size: 12px; }
 .signin { max-width: 440px; margin: 40px auto; background: #fff;
           border: 1px solid #d8dbe0; border-radius: 10px; padding: 22px 24px; }
 .signin form { display: flex; gap: 8px; }
 .issued { background: #eaf4ff; border: 1px solid #b6d7ff; border-radius: 8px;
           padding: 12px 15px; margin-bottom: 14px; }
 .issued code { display: block; margin: 8px 0; padding: 9px 11px;
                background: #11151c; color: #7ee787; border-radius: 6px;
                font-size: 13px; word-break: break-all; }
 table.breakdown { margin: 10px 0 4px; }
 h3 { font-size: 13px; margin: 18px 0 6px; }
 .warn { background: #fff1f1; border: 1px solid #ffc9c9; color: #a61b1b;
         border-radius: 8px; padding: 11px 14px; margin-bottom: 14px; }
 footer { max-width: 1000px; margin: 0 auto; padding: 0 26px 40px;
          color: #8a94a6; font-size: 12px; }
 @media (prefers-color-scheme: dark) {
   body { background: #0d1117; color: #c9d1d9; }
   .card, table, .signin, input, select { background: #161b22;
     border-color: #2b3240; color: #c9d1d9; }
   th { background: #11151c; }
   td { border-color: #21262d; }
   .issued { background: #10233a; border-color: #1f4b7a; }
   .warn { background: #2b1416; border-color: #5c2327; color: #ff9d9d; }
 }
"""


def build_app(store_path: str, accounts_path: str = "",
              min_version: int = 0):
    """The server. Returns a Flask app so it can be tested without a socket."""
    from flask import Flask, Response, jsonify, request

    # A request should not hang while an import runs: the server waits a
    # short while for the writer and then says the store is busy, which an
    # examiner can act on. Waiting minutes inside a request only moves the
    # failure to the other end, where it arrives as a timeout and says
    # nothing.
    store = hashstore.HashStore(store_path, lock_wait=SERVER_LOCK_WAIT)
    accounts = Accounts(accounts_path or
                        os.path.join(os.path.dirname(os.path.abspath(store_path)),
                                     "hash_users.db"))
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY
    app.config["STORE"] = store
    app.config["ACCOUNTS"] = accounts
    failures: dict = {}
    failures_lock = threading.Lock()

    def _turned_away(address: str) -> bool:
        """True if this address has failed too often, too recently."""
        now = time.monotonic()
        with failures_lock:
            recent = [t for t in failures.get(address, ()) if now - t < FAILED_WINDOW]
            failures[address] = recent
            return len(recent) >= FAILED_LIMIT

    def _failed(address: str, where: str, token: str) -> None:
        """A sign-in that failed: counted, and written to the access log.

        Nothing was recorded before, so neither a probe nor a revoked token
        still being tried ever showed in the record of who asked for what.
        The token is never written; the first eight characters of its hash
        are, which is enough to tell one repeated token from many.
        """
        with failures_lock:
            failures.setdefault(address, []).append(time.monotonic())
        mark = _hash_token(token)[:8] if token else "none"
        try:
            accounts.record("(unknown)", "refused",
                            f"{where}: token not recognized [{mark}]", address)
        except Exception:  # noqa: BLE001 - the refusal stands regardless
            pass

    @app.before_request
    def _admin_forms_from_here_only():
        # The admin pages are signed in by cookie, and SameSite=Lax was all
        # that kept another site from posting to them. Same-site is not
        # same-origin: any other web application on this host, on any port,
        # or on the same domain, could add a person, re-issue a token or
        # remove a set with the administrator's cookie. A browser names the
        # page a form came from in Origin (or, failing that, Referer); a
        # post that names another one is refused. The API is untouched: it
        # is signed in by a bearer token, which no other site can send.
        if request.method != "POST" or not request.path.startswith("/admin"):
            return None
        from urllib.parse import urlsplit
        named = request.headers.get("Origin") or request.headers.get("Referer")
        if not named:
            return None             # not a browser; it has no cookie to abuse
        if named == "null" or urlsplit(named).netloc.lower() != request.host.lower():
            return Response("Refused: this form was sent from another site.",
                            status=403, mimetype="text/plain")
        return None
    # The floor is the build counter: v2026.11.271 and the older v271 both
    # compare as 271 (fme.build_number). Read as the first number, a year-first
    # release would have passed every floor there is.
    from fme import build_number
    app.config["MIN_VERSION"] = build_number(min_version or 0)
    app.config["MIN_VERSION_TEXT"] = str(min_version or "")

    def _client_version() -> str:
        """The version the client declared, if it declared one.

        Recorded rather than enforced: an older client is not refused,
        it is noted, which is what lets somebody see the drift before a
        case depends on it.
        """
        return (request.headers.get("X-FME-Version") or "")[:32]

    def caller(need: str):
        """Who is asking, if they may. Returns (person, error response)."""
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        address = request.remote_addr or ""
        person = accounts.identify(token)
        if person is None:
            # A known token is never turned away -- an examiner sharing an
            # address with a misconfigured script must still get answers.
            # Only the unrecognized ones are counted, and past the limit
            # they are refused without another row in the log.
            if _turned_away(address):
                return None, (jsonify({"ok": False,
                                       "error": "Too many unrecognized tokens "
                                                "from this address; try again "
                                                "in a few minutes."}), 429)
            _failed(address, "api", token)
            return None, (jsonify({"ok": False,
                                   "error": "Not a known token."}), 401)
        if need not in RIGHTS.get(person["role"], set()):
            return None, (jsonify({
                "ok": False,
                "error": f"{person['name']} has the {person['role']} role, "
                         f"which cannot {need}."}), 403)

        # A version floor, applied to what writes and not to what reads.
        #
        # Refusing a lookup would stop an examiner part way through a
        # case because of a number, which is a worse outcome than the
        # drift it prevents: their work is already underway and the
        # store is the same store. Refusing a *contribution* is
        # different -- an old client writing into a shared set is how a
        # unit ends up with rows nobody can account for, and it can
        # wait until Monday.
        floor = app.config.get("MIN_VERSION", 0)
        if floor and need in WRITING:
            declared = _client_version()
            running = build_number(declared or "0")
            if running < floor:
                return None, (jsonify({
                    "ok": False,
                    "error": (
                        f"This store accepts {need} from "
                        f"v{app.config.get('MIN_VERSION_TEXT') or floor} and "
                        f"newer. This copy reports "
                        f"{('v' + declared) if declared else 'no version'}."
                        f"\n\nLooking hashes up still works, so a case "
                        f"in progress is not held up. Update the program "
                        f"folder and try again; docs/DEPLOYMENT.md has "
                        f"the steps.")}), 426)
        return person, None

    @app.route("/api/v1/whoami")
    def whoami():
        person, refused = caller("stats")
        if refused:
            return refused
        return jsonify({"ok": True, "name": person["name"],
                        "role": person["role"],
                        "may": sorted(RIGHTS[person["role"]])})

    @app.route("/api/v1/stats")
    def stats():
        person, refused = caller("stats")
        if refused:
            return refused
        return jsonify({"ok": True, "hashes": store.size(),
                        "sources": len(store.sources()),
                        # The scheme its codes are in, so a client can tell
                        # before sending a case graded under another.
                        "region": store.region,
                        "server": __version__})

    @app.route("/api/v1/sources")
    def sources():
        person, refused = caller("sources")
        if refused:
            return refused
        listed = store.sources()
        if person["role"] != "admin":
            # Where an imported set sits on the server, and what it was
            # called on disk, is the administrator's business; a reader
            # needs its name, kind and size.
            listed = [{k: v for k, v in row.items() if k != "path"}
                      for row in listed]
        return jsonify({"ok": True, "sources": listed})

    @app.route("/api/v1/lookup", methods=["POST"])
    def lookup():
        person, refused = caller("lookup")
        if refused:
            return refused
        data = request.get_json(force=True) or {}
        digests = data.get("digests") or []
        if not isinstance(digests, list):
            return jsonify({"ok": False, "error": "digests must be a list"}), 400
        if len(digests) > MAX_LOOKUP:
            return jsonify({"ok": False,
                            "error": f"at most {MAX_LOOKUP:,} digests per "
                                     f"request"}), 413
        # One answer per digest: the batch spans many files, and the client
        # maps each match back to its file by digest.
        matches = store.lookup([str(d).lower() for d in digests],
                               per_digest=True)
        # The stored record carries the file's other hashes, its PhotoDNA
        # and free text. None of it was sent by the caller, so none of it
        # goes back: only the grading fields the client acts on.
        for match in matches:
            match["record"] = hashsets.vic_shareable(match.get("record"))
        accounts.record(person["name"], "lookup",
                        f"{len(digests)} digest(s), {len(matches)} match(es)",
                        request.remote_addr or "", version=_client_version())
        return jsonify({"ok": True, "matches": matches})

    @app.route("/api/v1/contribute", methods=["POST"])
    def contribute():
        person, refused = caller("contribute")
        if refused:
            return refused
        data = request.get_json(force=True) or {}
        rows = data.get("rows") or []
        source = (data.get("source") or f"case via {person['name']}").strip()
        if not isinstance(rows, list) or not rows:
            return jsonify({"ok": False, "error": "nothing to contribute"}), 400
        if len(rows) > MAX_CONTRIBUTE:
            return jsonify({"ok": False,
                            "error": f"at most {MAX_CONTRIBUTE:,} rows per "
                                     f"request"}), 413
        regrades: list = []
        try:
            added = store.contribute_rows(rows, source_name=source,
                                          contributor=person["name"],
                                          admin=person["role"] == "admin",
                                          changes=regrades,
                                          region=str(data.get("region") or "US"))
        except hashstore.NotYourSource as exc:
            accounts.record(person["name"], "contribute refused", str(exc),
                            request.remote_addr or "",
                            version=_client_version())
            return jsonify({"ok": False, "error": str(exc)}), 403
        except sqlite3.OperationalError as exc:
            # Said in terms of what is happening, not what SQLite calls it:
            # "database is locked" reads as a fault, and this is a queue.
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                return jsonify({
                    "ok": False,
                    "error": "The store is busy writing something else -- an "
                             "import of a large set holds it for as long as "
                             "the import runs. Nothing is lost: let it "
                             "finish and add this case again."}), 503
            return jsonify({"ok": False, "error": str(exc)}), 400
        except (ValueError, sqlite3.Error) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        accounts.record(person["name"], "contribute",
                        f"{added} hash(es) as {source}",
                        request.remote_addr or "", version=_client_version())
        # One line per changed grade, old and new: the access log is what an
        # administrator reads, and "12 hash(es)" hid that six of them were
        # CAM made Non-Pertinent. The store keeps the same in grade_changes.
        if regrades:
            accounts.record_many(
                person["name"], "regrade",
                [f"{c['source']} {c['digest']}: "
                 f"{_grade_text(c['old_category'], c['old_label'])} -> "
                 f"{_grade_text(c['new_category'], c['new_label'])}"
                 for c in regrades],
                request.remote_addr or "", version=_client_version())
        return jsonify({"ok": True, "added": added, "source": source,
                        "regraded": len(regrades)})

    @app.route("/api/v1/import", methods=["POST"])
    def import_set():
        person, refused = caller("import")
        if refused:
            return refused
        data = request.get_json(force=True) or {}
        path = (data.get("path") or "").strip()
        # The path is on the server, not the caller's machine: a national set
        # is gigabytes and is not going over this connection.
        if not os.path.isfile(path):
            return jsonify({
                "ok": False,
                "error": (
                    f"The server cannot see {path}.\n\n"
                    f"A set is imported from a path on the server, not from "
                    f"the machine you are sitting at: a national set is "
                    f"gigabytes and is not sent over this connection. Copy "
                    f"the file to the server and give the path as the "
                    f"server sees it, or import it there with:\n"
                    f"    python launch.py store --store <the store> "
                    f"--import <the file>")}), 400
        try:
            # The kind as well as the name: without it the server guessed
            # from the file, and a custom CSV was read as whatever it
            # resembled.
            result = store.import_set(path,
                                      kind=(data.get("kind") or "").strip(),
                                      name=(data.get("name") or "").strip())
        except (OSError, ValueError, sqlite3.Error, csv.Error) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        accounts.record(person["name"], "import",
                        f"{result['stored']} hash(es) from "
                        f"{os.path.basename(path)}", request.remote_addr or "", version=_client_version())
        return jsonify({"ok": True, **result})

    @app.route("/api/v1/remove", methods=["POST"])
    def remove():
        person, refused = caller("remove")
        if refused:
            return refused
        data = request.get_json(force=True) or {}
        name = (data.get("source") or "").strip()
        try:
            removed = store.remove_source(name)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        accounts.record(person["name"], "remove", f"{name}: {removed} hash(es)",
                        request.remote_addr or "", version=_client_version())
        return jsonify({"ok": True, "removed": removed})

    @app.route("/api/v1/users")
    def users():
        person, refused = caller("users")
        if refused:
            return refused
        return jsonify({"ok": True, "users": accounts.users()})

    @app.route("/api/v1/access")
    def access_log():
        person, refused = caller("audit")
        if refused:
            return refused
        try:
            limit = min(2000, max(1, int(request.args.get("limit", "200"))))
        except ValueError:
            limit = 200
        return jsonify({"ok": True, "access": accounts.access(limit)})

    # ---- the admin pages ------------------------------------------------
    def _signed_in():
        """Who is signed in to the browser pages, if anyone."""
        from flask import request as _request
        return accounts.identify(_request.cookies.get("hs_token", ""))

    def _page(title: str, body: str, person=None, refresh: int = 0) -> str:
        tabs = ""
        if person:
            items = [("/admin", "Overview"), ("/admin/people", "People"),
                     ("/admin/sources", "Hash sets"),
                     ("/admin/activity", "Activity")]
            tabs = "".join(
                f'<a href="{href}">{label}</a>' for href, label in items)
        return (
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            # A meta refresh, not script: the CSP lets admin.js run and
            # nothing else, and a page that redraws itself while an import
            # runs needs no script at all.
            + (f"<meta http-equiv='refresh' content='{int(refresh)}'>"
               if refresh else "") +
            f"<title>{_e(title)} — hash store</title><style>" + ADMIN_STYLE +
            "</style><script src='/admin/admin.js' defer></script></head><body><header><h1>Hash store</h1>"
            + (f"<nav>{tabs}</nav><span class='who'>{_e(person['name'])} "
               f"<em>{_e(person['role'])}</em> "
               f"<a href='/admin/signout'>sign out</a></span>"
               if person else "") +
            f"</header><main>{body}</main>"
            "<footer>Every action here is recorded against the person who "
            "took it.</footer></body></html>")

    def _bars(rows, label_of, value_of, unit="", width=640):
        """A bar chart as inline SVG.

        Drawn rather than charted: a server has no internet, a
        charting library would be one more thing to install and one
        more thing to fail, and this is a dozen rectangles.
        """
        rows = [r for r in rows if value_of(r)]
        if not rows:
            return "<p class='hint'>Nothing recorded yet.</p>"
        rows = sorted(rows, key=lambda r: -value_of(r))[:12]
        top = max(value_of(r) for r in rows) or 1
        bar, gap, left = 22, 8, 150
        height = len(rows) * (bar + gap) + 10
        parts = [f"<svg viewBox='0 0 {width} {height}' width='100%' "
                 f"height='{height}' role='img'>"]
        for index, row in enumerate(rows):
            y = index * (bar + gap) + 4
            value = value_of(row)
            span = int((width - left - 90) * value / top)
            parts.append(
                f"<text x='0' y='{y + 15}' class='bl'>"
                f"{_e(str(label_of(row))[:22])}</text>"
                f"<rect x='{left}' y='{y}' width='{max(span, 2)}' "
                f"height='{bar}' rx='3' class='bar'/>"
                f"<text x='{left + max(span, 2) + 8}' y='{y + 15}' "
                f"class='bv'>{value:,}{unit}</text>")
        parts.append("</svg>")
        return "".join(parts)

    @app.route("/admin", methods=["GET", "POST"])
    def admin_home():
        from flask import Response, make_response, request as _request

        if _request.method == "POST":
            token = (_request.form.get("token") or "").strip()
            address = _request.remote_addr or ""
            person = accounts.identify(token)
            if person is None and _turned_away(address):
                return Response(_page("Sign in", _sign_in_form(
                    "Too many unrecognized tokens from this address; try "
                    "again in a few minutes.")), mimetype="text/html",
                    status=429)
            if person is None:
                _failed(address, "admin sign-in", token)
                return Response(_page("Sign in", _sign_in_form(
                    "That token was not recognized.")), mimetype="text/html")
            accounts.record(person["name"], "sign-in", "admin pages",
                            _request.remote_addr or "")
            response = make_response(_page("Signing in", ""))
            response.headers["Location"] = "/admin"
            response.status_code = 302
            # The browser holds the token; the server keeps no session, so
            # there is nothing to steal from it and nothing to expire.
            response.set_cookie("hs_token", token, httponly=True, samesite="Strict",
                                secure=_request.is_secure, max_age=12 * 3600)
            return response

        person = _signed_in()
        if person is None:
            return Response(_page("Sign in", _sign_in_form()),
                            mimetype="text/html")
        summary = accounts.summary()
        detail = accounts.usage()
        people = detail["people"]
        # Every person's lookups, contributions, cases and hours take the
        # audit right, as /admin/activity does; any signed-in reader used to
        # see them all. Without it, a person sees their own row only.
        if "audit" not in RIGHTS.get(person["role"], set()):
            people = [r for r in people if r["who"] == person["name"]]
        rows = "".join(
            f"<tr><td>{_e(r['who'])}</td><td class='n'>{r['lookups']:,}</td>"
            f"<td class='n'>{r['contributions']:,}</td>"
            f"<td class='n'>{r['hashes']:,}</td>"
            f"<td class='n'>{r['case_count']:,}</td>"
            f"<td class='n'>{r['days_active']:,}</td>"
            f"<td class='n'>{r['active_hours']}</td>"
            f"<td class='t'>{_e(', '.join(r['versions']) or '-')}</td>"
            f"<td class='t'>{_e(r['last'][:16].replace('T', ' '))}</td></tr>"
            for r in people)
        body = (
            "<section class='cards'>"
            f"<div class='card'><b>{store.size():,}</b><span>hashes</span></div>"
            f"<div class='card'><b>{len(store.sources()):,}</b><span>sets and "
            f"cases</span></div>"
            f"<div class='card'><b>{summary['users']:,}</b><span>people with "
            f"access</span></div>"
            f"<div class='card'><b>{summary['lookups']:,}</b><span>lookups "
            f"recorded</span></div></section>"
            "<h2>Hashes contributed</h2>"
            + _bars(people, lambda r: r["who"], lambda r: r["hashes"])
            + "<h2>Cases fed back into the store</h2>"
            + _bars(people, lambda r: r["who"], lambda r: r["case_count"])
            + "<h2>Lookups</h2>"
            + _bars(people, lambda r: r["who"], lambda r: r["lookups"])
            + "<h2>Active time</h2>"
            + f"<p class='hint'>{_e(detail['note'])}</p>"
            + _bars(people, lambda r: r["who"],
                    lambda r: r["active_hours"], unit="h")
            + "<h2>Who has used it</h2>"
            "<p class='hint'>Cases are the distinct case names each person "
            "has contributed under. Active time is a floor, for the reason "
            "above. Version is what their copy reported.</p>"
            "<table><thead><tr><th>person</th><th class='n'>lookups</th>"
            "<th class='n'>contributions</th><th class='n'>hashes</th>"
            "<th class='n'>cases</th><th class='n'>days</th>"
            "<th class='n'>hours</th><th class='t'>version</th>"
            "<th class='t'>last seen</th></tr></thead><tbody>"
            + (rows or "<tr><td colspan='5'>Nobody has used it yet.</td></tr>")
            + "</tbody></table>")
        return Response(_page("Overview", body, person), mimetype="text/html")

    @app.route("/admin/signout")
    def admin_signout():
        from flask import make_response
        response = make_response("", 302)
        response.headers["Location"] = "/admin"
        response.set_cookie("hs_token", "", max_age=0)
        return response

    @app.route("/admin/people", methods=["GET", "POST"])
    def admin_people():
        from flask import Response, request as _request

        person = _signed_in()
        if person is None or "users" not in RIGHTS.get(person["role"], set()):
            return Response(_page("People", _denied(person), person),
                            mimetype="text/html"), 403

        issued = ""
        if _request.method == "POST":
            action = _request.form.get("action", "")
            name = (_request.form.get("name") or "").strip()
            if action in ("add", "reissue") and name:
                role = _request.form.get("role", "read")
                try:
                    token = accounts.add(name, role, reissue=action == "reissue")
                except ValueError as exc:
                    token = ""
                    issued = (f"<div class='issued'>{_e(str(exc))}"
                              f"<form method='post' class='row'>"
                              f"<input type='hidden' name='action' value='reissue'>"
                              f"<input type='hidden' name='name' value='{_e(name)}'>"
                              f"<input type='hidden' name='role' value='{_e(role)}'>"
                              f"<button>Re-issue {_e(name)}'s token as {_e(role)}"
                              f"</button></form></div>")
            if action in ("add", "reissue") and name and token:
                accounts.record(person["name"],
                                "reissue-user" if action == "reissue" else "add-user",
                                f"{name} as {role}", _request.remote_addr or "")
                issued = (
                    f"<div class='issued'><b>{_e(name)}</b> can now sign in "
                    f"with the {_e(role)} role. Their token is shown once:"
                    f"<code>{_e(token)}</code>"
                    f"Give it to them the way you would give a password. It "
                    f"is stored hashed here, so it cannot be looked up "
                    f"again — issue a new one if it goes astray.</div>")
            elif action == "disable" and name:
                accounts.disable(name)
                accounts.record(person["name"], "disable-user", name,
                                _request.remote_addr or "")

        rows = "".join(
            f"<tr><td>{_e(u['name'])}</td><td>{_e(u['role'])}</td>"
            f"<td class='t'>{_e(u['created_at'][:10])}</td>"
            f"<td>{'disabled' if u['disabled'] else 'active'}</td>"
            f"<td>" + ("" if u["disabled"] else
                       f"<form method='post'><input type='hidden' name='action' "
                       f"value='disable'><input type='hidden' name='name' "
                       f"value='{_e(u['name'])}'>"
                       f"<button class='quiet'>disable</button></form>")
            + "</td></tr>"
            for u in accounts.users())
        body = (
            issued +
            "<h2>Add someone</h2>"
            "<form method='post' class='row'>"
            "<input type='hidden' name='action' value='add'>"
            "<input name='name' placeholder='their name or badge number' required>"
            "<select name='role'>"
            "<option value='read'>read — look up hashes</option>"
            "<option value='contribute'>contribute — and send cases back</option>"
            "<option value='admin'>admin — and manage this</option>"
            "</select><button>Add</button></form>"
            "<h2>People</h2><table><thead><tr><th>name</th><th>role</th>"
            "<th class='t'>added</th><th>state</th><th></th></tr></thead>"
            f"<tbody>{rows}</tbody></table>")
        return Response(_page("People", body, person), mimetype="text/html")

    # Imports started from the Hash sets page. A national set takes minutes
    # to hours; read inside the request, the browser gave up long before the
    # import did and the administrator never saw what it stored. Each runs
    # in its own thread (the store gives each thread its own connection) and
    # the page redraws itself until it is done.
    imports: list = []
    imports_lock = threading.Lock()
    app.config["IMPORTS"] = imports

    def _run_import(job: dict) -> None:
        def progress(read, stored):
            job["read"], job["stored"] = read, stored
        try:
            result = store.import_set(job["path"], kind=job["kind"],
                                      name=job["name"], progress=progress)
        except Exception as exc:  # noqa: BLE001 -- shown on the page
            job["error"] = str(exc) or exc.__class__.__name__
            job["state"] = "failed"
            accounts.record(job["by"], "import",
                            f"FAILED {job['kind']} {os.path.basename(job['path'])}: "
                            f"{job['error']}", job["address"])
            return
        job["result"] = result
        job["read"], job["stored"] = result["read"], result["stored"]
        job["state"] = "done" if result["stored"] else "failed"
        if not result["stored"]:
            job["error"] = result.get("note") or "nothing was imported"
        accounts.record(job["by"], "import",
                        f"{result['stored']} hash(es) from "
                        f"{os.path.basename(job['path'])} as {result['kind']}"
                        + _category_line(result.get("categories") or {},
                                         result["kind"]),
                        job["address"])

    def _category_line(breakdown: dict, kind: str) -> str:
        """The breakdown in one line, for the activity record."""
        records = breakdown.get("records") or {}
        if not records:
            return ""
        parts = [f"{_vic_label(k, kind)}: {n:,}" for k, n in
                 sorted(records.items(), key=lambda kv: _cat_sort(kv[0]))]
        return " (files by category: " + "; ".join(parts) + ")"

    def _cat_sort(key: str):
        return (1, 0) if key == "none" else (0, int(key))

    def _scheme():
        # The store's own scheme: a code a case contributed means what it
        # means under the region the store grades in (US unless set).
        from fme.review import regions
        return regions.get(store.region)

    def _vic_label(key: str, kind: str) -> str:
        if key == "none":
            return "no category"
        code = int(key)
        if kind == "projectvic":
            return f"{code} {hashsets.VIC_CATEGORY_NAMES.get(code, '(not a VICS category)')}"
        return f"{code} {_scheme().name(code)}".strip()

    def _graded_as(key: str, kind: str) -> str:
        """What a match on a hash in this category does to a file in the
        gallery -- the rule in media_gui._apply_hash_categories, said in
        words, so the administrator sees what the import will mean."""
        scheme = _scheme()
        names = {c[0]: c[2] for c in scheme.rows()}
        if kind == "nsrl":
            if scheme.nonpertinent is None:
                return "nothing: this scheme names no non-pertinent category"
            return (f"{scheme.nonpertinent} {names[scheme.nonpertinent]}, unless "
                    f"a Project VIC or other categorized set names the same file")
        if key == "none":
            return "nothing: shown as a match, left for the examiner"
        code = int(key)
        local = scheme.vic_in.get(code) if kind == "projectvic" else code
        if local is None or local not in names:
            return (f"nothing: left {names[scheme.uncategorized]} "
                    f"for the examiner")
        return f"{local} {names[local]}"

    def _breakdown_table(breakdown: dict, kind: str) -> str:
        records = breakdown.get("records") or {}
        hashes = breakdown.get("hashes") or {}
        keys = sorted(set(records) | set(hashes), key=_cat_sort)
        if not keys:
            return ""
        colors = {str(c[0]): c[3] for c in _scheme().rows()}
        body = ""
        for key in keys:
            # The swatch is the gallery's own color for what a match grades
            # a file as, so the table reads the way the gallery will.
            graded = _graded_as(key, kind)
            local = graded.split(" ", 1)[0]
            swatch = colors.get(local, "#9ca3af") if local.isdigit() \
                else "#9ca3af"
            body += (
                f"<tr><td><span style='display:inline-block;width:10px;"
                f"height:10px;border-radius:2px;background:{swatch};"
                f"margin-right:7px'></span>{_e(_vic_label(key, kind))}</td>"
                f"<td class='n'>{(f'{records[key]:,}' if key in records else '—')}</td>"
                f"<td class='n'>{hashes.get(key, 0):,}</td>"
                f"<td>{_e(graded)}</td></tr>")
        total_r = sum(records.values())
        total_h = sum(hashes.values())
        body += (f"<tr><td><b>total</b></td><td class='n'><b>"
                 f"{(f'{total_r:,}' if records else '—')}</b></td>"
                 f"<td class='n'><b>{total_h:,}</b></td><td></td></tr>")
        return ("<table class='breakdown'><thead><tr><th>category in the set</th>"
                "<th class='n' title='records the set listed; a file listed twice counts in each category it was listed under, and is stored once, at the more severe'>files (records)</th><th class='n'>hashes</th>"
                "<th>a match grades a file in the gallery as</th></tr></thead>"
                f"<tbody>{body}</tbody></table>")

    def _headline(result: dict) -> str:
        """The one sentence an administrator checks against the set's
        documentation."""
        kind = result["kind"]
        records = (result.get("categories") or {}).get("records") or {}
        files = sum(records.values())
        if kind == "nsrl":
            return (f"<b>{files:,}</b> file(s), <b>{result['stored']:,}</b> "
                    f"hash(es) from the NSRL as {_e(result['source'])} — every "
                    f"one Non-Pertinent (0).")
        if kind == "projectvic":
            cam = records.get("1", 0)
            return (f"<b>{files:,}</b> file(s), <b>{result['stored']:,}</b> "
                    f"hash(es) from Project VIC as {_e(result['source'])}, "
                    f"<b>{cam:,}</b> of them CAM (category 1).")
        return (f"<b>{result['stored']:,}</b> hash(es) as "
                f"{_e(result['source'])}.")

    def _import_card(job: dict) -> str:
        what = {"nsrl": "NSRL", "projectvic": "Project VIC",
                "custom": "list"}.get(job["kind"], job["kind"])
        if job["state"] == "running":
            return (f"<div class='issued'>Importing {what} "
                    f"<b>{_e(job['name'])}</b> from <code>{_e(job['path'])}"
                    f"</code>: {job['read']:,} hash(es) read, "
                    f"{job['stored']:,} stored so far. This page refreshes "
                    f"itself until it is done.</div>")
        if job["state"] == "failed":
            return (f"<div class='warn'>{what} import of "
                    f"<b>{_e(job['name'])}</b> stored nothing: "
                    f"{_e(job.get('error') or '')}</div>")
        result = job["result"]
        return (f"<div class='issued'>Imported {_headline(result)}"
                + (f"<br>{_e(result['note'])}" if result.get("note") else "")
                + (f"<br>Skipped: " + ", ".join(
                    f"{n:,} {why}" for why, n in result["skipped"].items())
                   if result.get("skipped") else "")
                + _breakdown_table(result.get("categories") or {}, result["kind"])
                + "</div>")

    def _start_import(person, kind: str, path: str, name: str,
                      address: str) -> str:
        """Check what can be checked at once, start the import, and return
        the message for the page."""
        if kind not in hashstore.KINDS:
            return f"<div class='warn'>Not a kind of set: {_e(kind)}</div>"
        if not os.path.isfile(path):
            return (f"<div class='warn'>No such file on this machine: "
                    f"{_e(path)}. The path is read here, on the server, not "
                    f"on your own computer.</div>")
        if kind != "projectvic" and hashsets.looks_like_vics(path):
            # Said now rather than after the thread starts: this is the
            # mistake that would grade CAM as known software.
            return (f"<div class='warn'>{_e(os.path.basename(path))} is a "
                    f"Project VIC (VICS) file. Import it under <b>Project "
                    f"VIC</b>, so each hash keeps its category.</div>")
        if kind == "projectvic" and not hashsets.looks_like_vics(path):
            return (f"<div class='warn'>{_e(os.path.basename(path))} does "
                    f"not look like a Project VIC (VICS) JSON file: no "
                    f"MediaID records and no Project VIC header at its "
                    f"start. Nothing was imported.</div>")
        name = name or os.path.basename(path)
        if any(src["name"] == name for src in store.sources()):
            return (f"<div class='warn'>A set named {_e(name)} is already in "
                    f"the store. Remove it first, or give this one another "
                    f"name.</div>")
        with imports_lock:
            if any(j["name"] == name and j["state"] == "running"
                   for j in imports):
                return (f"<div class='warn'>{_e(name)} is already being "
                        f"imported.</div>")
            job = {"kind": kind, "path": path, "name": name,
                   "by": person["name"], "address": address,
                   "state": "running", "read": 0, "stored": 0,
                   "started": _now()}
            imports.insert(0, job)
            del imports[20:]
        accounts.record(person["name"], "import",
                        f"started {kind} {os.path.basename(path)} as {name}",
                        address)
        job["thread"] = threading.Thread(target=_run_import, args=(job,),
                                         name=f"import {name}", daemon=True)
        job["thread"].start()
        return ""

    counting: set = set()
    counting_lock = threading.Lock()

    def _count_in_background(name: str) -> None:
        """Count one set by category on a thread of its own, once."""
        with counting_lock:
            if name in counting:
                return
            counting.add(name)

        def work():
            try:
                store.category_breakdown(name)     # counts, and keeps it
            except Exception as exc:  # noqa: BLE001 - shown as not counted yet
                print(f"could not count {name} by category: {exc}", file=sys.stderr)
            finally:
                with counting_lock:
                    counting.discard(name)
        threading.Thread(target=work, daemon=True,
                         name=f"count-{name[:20]}").start()

    @app.route("/admin/sources", methods=["GET", "POST"])
    def admin_sources():
        from flask import Response, request as _request

        person = _signed_in()
        if person is None or "import" not in RIGHTS.get(person["role"], set()):
            return Response(_page("Hash sets", _denied(person), person),
                            mimetype="text/html"), 403

        message = ""
        if _request.method == "POST":
            action = _request.form.get("action", "")
            if action == "import":
                # The kind comes from which section's form was used, never
                # from a guess at the file: a guess is how a Project VIC set
                # with no @odata header came in as a list with no categories.
                message = _start_import(
                    person, (_request.form.get("kind") or "").strip().lower(),
                    (_request.form.get("path") or "").strip(),
                    (_request.form.get("name") or "").strip(),
                    _request.remote_addr or "")
            elif action == "remove":
                name = (_request.form.get("name") or "").strip()
                try:
                    removed = store.remove_source(name)
                except ValueError as exc:
                    # Already gone -- removed in another tab, or by a client
                    # -- was an uncaught error and a bare 500 page.
                    message = f"<div class='warn'>{_e(str(exc))}</div>"
                else:
                    accounts.record(person["name"], "remove",
                                    f"{name}: {removed} hash(es)",
                                    _request.remote_addr or "")
                    message = (f"<div class='issued'>Removed {_e(name)} and "
                               f"its {removed:,} hash(es).</div>")

        with imports_lock:
            recent = list(imports[:5])
        running = any(j["state"] == "running" for j in recent)
        message += "".join(_import_card(j) for j in recent)

        def form(kind, placeholder, button):
            return ("<form method='post' class='row'>"
                    "<input type='hidden' name='action' value='import'>"
                    f"<input type='hidden' name='kind' value='{kind}'>"
                    f"<input name='path' placeholder='{placeholder}' required>"
                    "<input name='name' placeholder='name it (optional)'>"
                    f"<button>{button}</button></form>")

        importing = {j["name"] for j in recent if j["state"] == "running"}
        try:
            sources = store.sources()
        except sqlite3.OperationalError as exc:
            # The store's write lock is an import's while it writes a batch.
            # Said, and tried again, rather than a bare 500 (v2026.11.327).
            body = (message + "<div class='warn'>The store is busy writing an "
                    f"import and could not be read just now ({_e(str(exc))}). "
                    "This page tries again in a few seconds; the import "
                    "carries on.</div>")
            return Response(_page("Hash sets", body, person, refresh=5),
                            mimetype="text/html")
        rows, detail = "", ""
        counting_now = False
        for src in sources:
            rows += (
                f"<tr><td>{_e(src['name'])}</td><td>{_e(src['kind'])}</td>"
                f"<td class='n'>{src['hashes']:,}</td>"
                f"<td class='t'>{_e(src['added_at'][:10])}</td>"
                f"<td class='t'>{_e(src.get('note') or '')}</td>"
                # The name goes in a data attribute and /admin/admin.js reads
                # it as a string. Inside onsubmit="confirm('...')" it was
                # code: the browser decodes html.escape's &#x27; back to a
                # quote before the script runs, so a contributor who named a
                # case x');fetch(...);(' ran script in the administrator's
                # session.
                f"<td><form method='post' data-confirm='Remove "
                f"{_e(src['name'])} and its hashes?'>"
                f"<input type='hidden' name='action' value='remove'>"
                f"<input type='hidden' name='name' value='{_e(src['name'])}'>"
                f"<button class='quiet'>remove</button></form></td></tr>")
            if src["kind"] in ("nsrl", "projectvic"):
                label = 'NSRL' if src['kind'] == 'nsrl' else 'Project VIC'
                if src["name"] in importing:
                    # Counted by the import itself when it finishes; counting
                    # it now would keep a half-imported set's numbers.
                    detail += (f"<h3>{_e(src['name'])} <span class='hint'>{label}"
                               f"</span></h3><p class='hint'>Importing now "
                               f"({src['hashes']:,} hashes so far). Its counts "
                               f"by category are kept when it finishes.</p>")
                    continue
                try:
                    known = store.known_breakdown(src["name"])
                except (ValueError, sqlite3.Error):
                    known = None
                if known is None:
                    # A set imported before its breakdown was kept is counted
                    # once, in the background, and kept; the page says so and
                    # does not wait for it.
                    _count_in_background(src["name"])
                    detail += (f"<h3>{_e(src['name'])} <span class='hint'>{label}"
                               f"</span></h3><p class='hint'>Being counted by "
                               f"category for the first time ({src['hashes']:,} "
                               f"hashes). That takes minutes for a large set; "
                               f"reload this page later. It is counted once and "
                               f"kept.</p>")
                    counting_now = True
                    continue
                table = _breakdown_table(known, src["kind"])
                if table:
                    detail += (f"<h3>{_e(src['name'])} <span class='hint'>"
                               f"{label}</span></h3>{table}")
        body = (
            message +
            "<p class='hint'>Each path is read <b>on this machine</b>. A "
            "national set is gigabytes; it is not uploaded through a browser. "
            "Put the file on this server and give its path.</p>"
            "<h2>NSRL — known software</h2>"
            "<p class='hint'>The RDS SQLite database or NSRLFile.txt. Every "
            "hash comes in as <b>0 Non-Pertinent</b>: a file it matches is "
            "graded 0 in the gallery unless a Project VIC or other "
            "categorized set names the same file, which then decides. "
            "A Project VIC file is refused here.</p>"
            + form("nsrl", "/srv/hashes/RDS_2026.09_modern.db", "Import NSRL") +
            "<h2>Project VIC — categorized</h2>"
            "<p class='hint'>A VICS JSON file. Each hash keeps the category "
            "the set gives it — 0 Non-Pertinent, 1 CAM, 2 Child Exploitive / "
            "Age Difficult, 3 CGI / Animation, 4 Comparison — and a match "
            "grades the file the same in the gallery. VICS 5 "
            "(Uncategorized) is kept and shown, and leaves the file for the "
            "examiner.</p>"
            + form("projectvic", "/srv/hashes/VIC_US_2026.json",
                   "Import Project VIC") +
            "<h2>Another list</h2>"
            "<p class='hint'>A list of your own: one hash per line, or a CSV "
            "with a hash column. It carries no category, so a match is shown "
            "and grades nothing.</p>"
            + form("custom", "/srv/hashes/our_known.txt", "Import list") +
            "<h2>What is in the store</h2><table><thead><tr><th>source</th>"
            "<th>kind</th><th class='n'>hashes</th><th class='t'>added</th>"
            "<th class='t'>note</th><th></th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
            + (("<h2>By category</h2><p class='hint'>Files are the records the "
                "set listed; hashes are what landed (a file can carry an MD5, "
                "a SHA-1 and a SHA-256). A set imported before this release "
                "shows hashes only.</p>" + detail) if detail else ""))
        return Response(_page("Hash sets", body, person,
                              refresh=3 if running else (60 if counting_now else 0)),
                        mimetype="text/html")

    @app.route("/admin/activity")
    def admin_activity():
        from flask import Response, request as _request

        person = _signed_in()
        if person is None or "audit" not in RIGHTS.get(person["role"], set()):
            return Response(_page("Activity", _denied(person), person),
                            mimetype="text/html"), 403
        try:
            limit = min(2000, max(10, int(_request.args.get("limit", "300"))))
        except ValueError:
            limit = 300
        rows = "".join(
            f"<tr><td class='t'>{_e(r['at'].replace('T', ' '))}</td>"
            f"<td>{_e(r['who'])}</td><td>{_e(r['action'])}</td>"
            f"<td>{_e(r['detail'])}</td><td class='t'>{_e(r['address'])}</td></tr>"
            for r in accounts.access(limit))
        body = ("<h2>Activity</h2><p class='hint'>Most recent first. This is "
                "the record of who asked this store for what.</p>"
                "<table><thead><tr><th class='t'>when</th><th>who</th>"
                "<th>what</th><th>detail</th><th class='t'>from</th></tr>"
                f"</thead><tbody>{rows}</tbody></table>")
        return Response(_page("Activity", body, person), mimetype="text/html")

    @app.route("/admin/admin.js")
    def admin_script():
        return Response(ADMIN_SCRIPT, mimetype="text/javascript")

    @app.after_request
    def _admin_pages_run_only_our_script(response):
        # Belt to the data-attribute braces: if a value ever does reach a
        # page as markup again, the browser runs no script but admin.js,
        # sends no form anywhere but here, and will not frame the page.
        if request.path.startswith("/admin"):
            response.headers["Content-Security-Policy"] = ADMIN_CSP
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.route("/")
    def front():
        from flask import redirect
        return redirect("/admin")

    return app


def self_check(args, accounts_path: str) -> int:
    """Start the server, talk to it, and say what is wrong if anything is.

    "localhost refused to connect" is a browser saying nothing happened; it
    covers a server that never started, one serving TLS where http was
    asked for, a port in use, and a file nobody can read. This separates
    them.
    """
    import socket
    import ssl
    import subprocess
    import threading
    import time
    import urllib.error
    import urllib.request

    ok = True
    print("Checking the hash store server\n")

    # 1. The files it needs.
    for label, path, needed in (("store", args.store, False),
                                ("accounts", accounts_path, False),
                                ("certificate", args.cert, bool(args.cert)),
                                ("private key", args.key, bool(args.key))):
        if not path:
            continue
        there = os.path.isfile(path)
        if needed and not there:
            ok = False
        state = "found" if there else ("MISSING" if needed
                                       else "not there yet (it will be made)")
        print(f"  {label:12} {state}: {os.path.abspath(path)}")
        if path and hashstore.is_network_path(path):
            print(f"  {'':12} on a network path -- a scheduled task starting "
                  f"at boot cannot see a mapped drive")

    # 2. The store opens.
    try:
        store = hashstore.HashStore(args.store)
        print(f"\n  the store opens: {store.size():,} hash(es), "
              f"{len(store.sources())} source(s)")
        store.close()
    except Exception as exc:  # noqa: BLE001 - this is the diagnosis
        ok = False
        print(f"\n  the store does NOT open:\n     {exc}")
        return 1 if not ok else 0

    # 3. What it will serve with, which decides whether one slow lookup
    #    holds up everybody else.
    if args.cert and args.key:
        try:
            import cheroot  # noqa: F401
            print("  serving: cheroot, threaded, TLS")
        except ImportError:
            print("  serving: Flask's own server -- single-threaded, so one\n"
                  "           slow lookup blocks the rest. For a shared "
                  "store:\n"
                  "               pip install cheroot")
    else:
        try:
            import waitress  # noqa: F401
            print("  serving: waitress, threaded, no TLS")
        except ImportError:
            print("  serving: Flask's own server (pip install waitress)")

    # 4. The port is free.
    with socket.socket() as probe:
        probe.settimeout(2)
        busy = probe.connect_ex((args.host if args.host not in ("0.0.0.0", "::")
                                 else "127.0.0.1", args.port)) == 0
    print(f"  port {args.port}: {'ALREADY IN USE' if busy else 'free'}")
    if busy:
        print("     something is already listening there. Either the server "
              "is\n     already running, or another program has the port.")
        ok = False

    # 4. It starts and answers.
    if not busy:
        command = [sys.executable, os.path.abspath(__file__),
                   "--store", args.store, "--accounts", accounts_path,
                   "--host", "127.0.0.1", "--port", str(args.port)]
        if args.cert and args.key:
            command += ["--cert", args.cert, "--key", args.key]
        child = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE)
        scheme = "https" if (args.cert and args.key) else "http"
        url = f"{scheme}://127.0.0.1:{args.port}/"
        context = None
        if scheme == "https":
            context = ssl.create_default_context(cafile=args.cert)
        answered = False
        for _ in range(60):
            if child.poll() is not None:
                break
            try:
                urllib.request.urlopen(url, timeout=3, context=context)
                answered = True
                break
            except urllib.error.HTTPError:
                answered = True         # it answered, even if with a refusal
                break
            except Exception:  # noqa: BLE001 - not up yet
                time.sleep(0.5)
        if answered:
            print(f"\n  it answers on {url}")
            print(f"  open this in a browser:  {scheme}://localhost:"
                  f"{args.port}/admin")
            print(f"  it must be {scheme}, not "
                  f"{'http' if scheme == 'https' else 'https'} -- the other "
                  f"one is refused with no explanation")
        else:
            ok = False
            print(f"\n  it did NOT answer on {url}")
            try:
                child.terminate()
                _, errors = child.communicate(timeout=10)
                if errors:
                    print("     the server said:")
                    for line in errors.decode("utf-8", "replace").strip().splitlines()[-8:]:
                        print(f"       {line}")
            except Exception:  # noqa: BLE001
                pass
        try:
            child.terminate()
            child.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass

    if ok:
        print("\nEverything checked out.")
        # Said plainly because the next thing anybody does is open the
        # browser -- and find nothing there. The check starts a server,
        # talks to it, and stops it again; it is a test, not a start.
        print("\n  The server this check started has now stopped: it was a "
              "test.")
        print("  Nothing is listening until you start it for real, in a "
              "window you leave open:\n")
        start = [sys.executable, "launch.py", "hashserver",
                 "--store", args.store]
        if accounts_path:
            start += ["--accounts", accounts_path]
        start += ["--host", "0.0.0.0", "--port", str(args.port)]
        if args.cert and args.key:
            start += ["--cert", args.cert, "--key", args.key]
        printable = " ".join(f'"{part}"' if " " in part else part
                             for part in ["python"] + start[1:])
        print(f"      {printable}\n")
        print("  Then open the admin page in a browser on this machine.")
    else:
        print("\nSomething above needs fixing.")
    return 0 if ok else 1


def _stores_near(path: str) -> list:
    """Other stores in the same folder, to catch a mistyped name."""
    folder = os.path.dirname(os.path.abspath(path)) or "."
    found = []
    try:
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith(".db"):
                continue
            full = os.path.join(folder, name)
            try:
                if hashstore.looks_like_store(full):
                    found.append((name, os.path.getsize(full)))
            except Exception:  # noqa: BLE001
                continue
    except OSError:
        pass
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Serve one hash store to a unit.")
    ap.add_argument("--store", default="",
                    help="the store this server owns. Required: there is no "
                         "default, because a server that invents an empty "
                         "store answers every lookup with 'not known' and "
                         "looks like it is working")
    ap.add_argument("--create", action="store_true",
                    help="make the store if it is not there. Only for a new "
                         "server: without this a missing store is an error")
    ap.add_argument("--min-version", default="", metavar="VERSION",
                    help="refuse contributions, imports and removals from "
                         "clients older than this build. Lookups are "
                         "never refused: stopping an examiner part way "
                         "through a case over a version number is worse "
                         "than the drift it prevents")
    ap.add_argument("--accounts", default="",
                    help="where the user list lives (default: beside the store)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="leave as 127.0.0.1 until you have read the warning "
                         "about TLS below")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--insecure", action="store_true",
                    help="serve plain HTTP on a network interface. Refused "
                         "without this: tokens and every hash looked up would "
                         "cross the network in the clear. Use --make-cert, or "
                         "a reverse proxy that terminates TLS, instead")
    ap.add_argument("--cert", default="", help="TLS certificate")
    ap.add_argument("--key", default="", help="TLS private key")
    ap.add_argument("--make-cert", action="store_true",
                    help="write a self-signed certificate and key, then stop. "
                         "For a closed lab, where there is no certificate "
                         "authority to ask")
    ap.add_argument("--cert-name", action="append", default=[],
                    help="a hostname or address this server is reached by; "
                         "repeat for more. Defaults to this machine's name")
    ap.add_argument("--add-user", default="")
    ap.add_argument("--reissue", action="store_true",
                    help="with --add-user: give an existing person a new token "
                         "(their old one stops working), set their role and "
                         "enable them")
    ap.add_argument("--role", default="read", choices=list(ROLES))
    ap.add_argument("--disable-user", default="")
    ap.add_argument("--list-users", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="start the server, connect to it, report what works, "
                         "and stop. For when it will not come up and the "
                         "browser only says it was refused")
    args = ap.parse_args(argv)

    # Before anything opens anything. A server that invents an empty store
    # answers every lookup with "not known" and looks like it is working,
    # which is the worst way for a hash store to fail: an examiner is told
    # a file is unknown when the store that knows it was never opened.
    # --check is a diagnostic: its job is to report a missing store, not to
    # be stopped by one.
    if not args.store and not args.make_cert and not args.check:
        print("No --store given.\n")
        print("  There is no default on purpose. A server that makes an "
              "empty store\n  answers every lookup with 'not known' and "
              "looks like it is working.\n")
        print("  Name the store this server owns, whatever it is called:\n"
              "      --store C:\\hashstore\\data\\hashes.db\n")
        return 2

    if (args.store and not os.path.isfile(args.store)
            and not args.create and not args.make_cert and not args.check):
        print(f"There is no store at {args.store}\n")
        nearby = _stores_near(args.store)
        if nearby:
            print("  Stores that are in that folder:")
            for found, size in nearby:
                print(f"      {found}   ({size / 1073741824:.1f} GiB)")
            print()
        print("  If this is a new server, add --create. Otherwise check the "
              "path:\n  serving the wrong file means every lookup comes back "
              "'not known'.\n")
        return 2

    accounts_path = args.accounts or os.path.join(
        os.path.dirname(os.path.abspath(args.store or ".")), "hash_users.db")

    if args.make_cert:
        cert = args.cert or "server.crt"
        key = args.key or "server.key"
        try:
            made = make_certificate(cert, key, args.cert_name or None)
        except (RuntimeError, OSError) as exc:
            print(exc, file=sys.stderr)
            return 2
        print(f"Wrote {made['cert']}\n      {made['key']}")
        print(f"Valid until {made['expires']}, for: {', '.join(made['names'])}")
        print("\nStart the server with:")
        print(f"  python -m fme hashserver --store {args.store} "
              f"--host 0.0.0.0 --cert {cert} --key {key}")
        print("\nThe certificate is self-signed, so each examiner's machine "
              "has to be\ntold to trust this exact file. Copy "
              f"{os.path.basename(cert)} to them and give it\nin the Hash "
              "Sets tab, or with --ca-cert on the command line. The key "
              "stays\nhere and is never copied anywhere.")
        return 0

    if args.add_user or args.disable_user or args.list_users:
        accounts = Accounts(accounts_path)
        try:
            if args.add_user:
                try:
                    token = accounts.add(args.add_user, args.role,
                                         reissue=args.reissue)
                except ValueError as exc:
                    print(f"{exc}\nRun again with --reissue to do that.")
                    return 2
                print(f"Added {args.add_user} with the {args.role} role.\n")
                print(f"  token: {token}\n")
                print("This is the only time the token is shown: it is stored\n"
                      "hashed, so it cannot be read back. Give it to that\n"
                      "person over something you would trust with a password,\n"
                      "and issue a new one if it goes astray.")
            if args.disable_user:
                print("disabled" if accounts.disable(args.disable_user)
                      else "no such user")
            if args.list_users:
                print(f"\n  {'name':20} {'role':12} {'added':12} state")
                for user in accounts.users():
                    print(f"  {user['name'][:20]:20} {user['role']:12} "
                          f"{user['created_at'][:10]:12} "
                          f"{'disabled' if user['disabled'] else 'active'}")
        finally:
            accounts.close()
        return 0

    if args.check:
        return self_check(args, accounts_path)

    app = build_app(args.store, accounts_path,
                    min_version=getattr(args, "min_version", 0) or 0)
    if getattr(args, "min_version", 0):
        print(f"Contributions, imports and removals are accepted from "
              f"v{args.min_version} and newer. Lookups are not "
              f"restricted.")
    store_size = app.config["STORE"].size()
    print(f"Hash store server {__version__}")
    print(f"  store    : {os.path.abspath(args.store)} ({store_size:,} hashes)")
    print(f"  accounts : {accounts_path}")

    context = None
    if args.cert and args.key:
        context = (args.cert, args.key)
        scheme = "https"
    else:
        scheme = "http"
        if args.host not in LOOPBACK:
            # A warning was printed and the server came up anyway: bearer
            # tokens, the admin cookie and every digest looked up crossed
            # the lab's network in the clear. Now it takes a decision.
            print("\n  WARNING: serving on a network interface without TLS.\n"
                  "  Tokens and the hashes you look up would cross the\n"
                  "  network in the clear. Give --cert and --key (--make-cert\n"
                  "  writes a pair), or put this behind a reverse proxy that\n"
                  "  terminates TLS.\n")
            if not getattr(args, "insecure", False):
                print("  Not started. Add --insecure to serve plain HTTP on "
                      f"{args.host} anyway.\n")
                return 2
    # The address to type, spelled out. "localhost refused to connect" is
    # almost always this: the server is serving https and the browser was
    # given http, or the other way round. A browser reports both as a
    # refusal, which tells nobody anything.
    visit = "localhost" if args.host in ("0.0.0.0", "::", "") else args.host
    print(f"  address  : {scheme}://{args.host}:{args.port}")
    print(f"\n  Open this in a browser on this machine:")
    print(f"      {scheme}://{visit}:{args.port}/admin")
    if scheme == "https":
        print("  It must be https, not http -- this server is serving TLS,\n"
              "  and a browser given http gets a refusal with no explanation.")
        print("  The certificate is this lab's own, so the browser will warn\n"
              "  on this machine: choose Advanced and continue.")
    else:
        print("  It must be http, not https -- no certificate was given, so\n"
              "  there is no TLS to connect to.")

    # A scheduled task starts before anybody logs in, and a mapped drive
    # belongs to a logged-in session: it is simply not there. This is the
    # usual reason a server that works by hand does not come back after a
    # restart.
    for label, where in (("store", args.store), ("accounts", accounts_path),
                         ("certificate", args.cert), ("key", args.key)):
        if where and hashstore.is_network_path(where):
            print(f"\n  WARNING: the {label} is on a network path:\n"
                  f"      {where}\n"
                  f"  A mapped drive letter exists only inside a logged-in\n"
                  f"  session, so a scheduled task starting this at boot will\n"
                  f"  not find it -- which looks like the task silently not\n"
                  f"  running. Use the full \\\\server\\share path, or keep\n"
                  f"  these on a disk in this machine.")
            break
    print()

    return serve_forever(app, args.host, args.port, args.cert, args.key)


def serve_forever(app, host: str, port: int, cert: str = "", key: str = "",
                  threads: int = 16) -> int:
    """Run the server, with the best of what is installed.

    Three ways, in order of preference:

    **cheroot** serves TLS and serves it from a pool of threads, so one
    examiner scanning a hundred and sixty thousand hashes does not make
    everybody else wait. This is what a shared store should be run with.

    **waitress** is as good, and cannot terminate TLS, so it is used only
    when there is no certificate -- which should mean a store reachable
    only from this machine.

    **Flask's own server** is the fallback and says so loudly: it is
    single-threaded and meant for development. It works, and a unit of a
    few examiners may never notice, but a slow lookup blocks every other
    one behind it.
    """
    serving_tls = bool(cert and key)

    if serving_tls:
        try:
            from cheroot import wsgi
            from cheroot.ssl.builtin import BuiltinSSLAdapter

            server = wsgi.Server((host, port), app, numthreads=threads)
            server.ssl_adapter = BuiltinSSLAdapter(cert, key)
            print(f"  serving with cheroot, {threads} threads, TLS\n")
            try:
                server.start()
            except KeyboardInterrupt:
                server.stop()
            except OSError as exc:
                # The commonest start-up failure, and a traceback about
                # sockets does not say what to do about it.
                if getattr(exc, "winerror", None) == 10048 or \
                        "address already in use" in str(exc).lower():
                    print(f"\n  Port {port} is already taken: something is "
                          f"already serving on it.\n"
                          f"  Almost always this program, started earlier and "
                          f"still running.\n\n"
                          f"  To see what it is:\n"
                          f"      netstat -ano | findstr :{port}\n"
                          f"  and to stop it, with the number that line "
                          f"ends in:\n"
                          f"      taskkill /pid <number> /f\n")
                    return 1
                raise
            return 0
        except ImportError:
            print("  NOTE: serving TLS with Flask's own server, which is\n"
                  "  single-threaded -- one slow lookup blocks the rest. For\n"
                  "  a store a unit shares, install cheroot:\n"
                  "      pip install cheroot\n")
    else:
        try:
            from waitress import serve

            print(f"  serving with waitress, {threads} threads, no TLS\n")
            serve(app, host=host, port=port, threads=threads)
            return 0
        except ImportError:
            pass

    import ssl as _ssl

    context = None
    if serving_tls:
        context = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
    app.run(host=host, port=port, debug=False, threaded=True,
            ssl_context=context)
    return 0


if __name__ == "__main__":
    sys.exit(main())
