"""
remotestore.py -- a hash store that lives on another machine.

Presents the same handful of methods as the local store, so the rest of the
program does not know or care which it is holding: point the Hash Sets tab
at a path and it opens a file, point it at a URL and it opens one of these.

What crosses the network is deliberately small. Lookups send digests and get
back only the matches, so the store's contents never travel; a contribution
sends hashes and the labels they were graded with, never a file. Importing a
national set is a server-side operation on a path the server can see --
gigabytes are not going over this connection, and the person who runs the
server is the one who should decide what goes into it.

A failure here is reported, never swallowed. An examiner who thinks they
checked a run against the store, and did not, is worse off than one who
knows the server is down.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

__version__ = "1.1.0"

# What this client needs of a server to behave as documented. A unit runs
# one server and many copies of the program, and the server is the one
# nobody restarts -- so when it is older than this, say so plainly rather
# than letting an examiner meet a fault that was fixed months ago.
NEEDS_SERVER = (1, 2, 0)
# The first server that checks the scheme a contribution was graded under.
# An older one ignores the region it is sent and stores a UK 1 as a US 1.
REGION_SERVER = (1, 2, 0)

# How long to wait for the server. Generous, because the first request
# after a restart can be slow on a large store -- a national set is tens of
# millions of rows and the machine has just come up cold. Giving up at a
# minute turned "slow once" into "could not reach the hash store", which
# sends an examiner looking for a network fault that is not there.
TIMEOUT = 300
BATCH = 5000        # digests per request; the server caps this too
CONTRIBUTE_BATCH = 5000   # graded files per contribution request, likewise


def _client_version() -> str:
    """The version of the program making the request."""
    import os as _os

    here = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    for candidate in (_os.path.join(here, "VERSION"),
                      _os.path.join(_os.path.dirname(here), "VERSION")):
        try:
            with open(candidate, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            continue
    return "unknown"


class RemoteStoreError(RuntimeError):
    """Something went wrong talking to the server, said plainly."""


class RemoteHashStore:
    """The shared store, over HTTP."""

    def __init__(self, url: str, token: str, timeout: int = TIMEOUT,
                 ca_cert: str = ""):
        self.url = url.rstrip("/")
        self.path = self.url            # what the interface shows
        self.token = token
        self.timeout = timeout
        self.ca_cert = ca_cert
        self.remote = True

    def _context(self):
        """How to verify the server, for a lab with its own certificate.

        A self-signed certificate is not signed by anyone the machine
        already trusts, so the certificate itself is named here as the thing
        to check against. That is a stronger position than the usual one:
        this connection will accept exactly one certificate, rather than any
        of the hundred-odd authorities a machine ships trusting.

        Verification is never simply turned off. An encrypted connection to
        whoever answered is not the same as an encrypted connection to the
        hash store, and the difference is the whole point of doing this.
        """
        if not self.url.lower().startswith("https://"):
            return None
        import ssl

        if self.ca_cert:
            return ssl.create_default_context(cafile=self.ca_cert)
        return ssl.create_default_context()

    # -- plumbing --------------------------------------------------------
    def _call(self, route: str, payload=None, method: str = "GET"):
        request = urllib.request.Request(
            f"{self.url}{route}", method=method,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={"Authorization": f"Bearer {self.token}",
                     # So the server's audit shows which version each
                     # examiner was running. Seven installs drift, and
                     # "which version produced this?" is a question
                     # somebody will eventually have to answer about a
                     # case rather than about a tool.
                     "X-FME-Version": _client_version(),
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout,
                                        context=self._context()) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
                message = body.get("error") or str(exc)
            except Exception:  # noqa: BLE001
                message = str(exc)
            if exc.code == 401:
                raise RemoteStoreError(
                    f"The hash store did not accept this token. {message}") from exc
            if exc.code == 403:
                raise RemoteStoreError(message) from exc
            if "database is locked" in message.lower():
                # An old server passing SQLite's own words back. Say what it
                # means and what to do, since the server cannot.
                raise RemoteStoreError(
                    "The store is busy writing something else -- an import "
                    "holds it for as long as it runs. Nothing is lost: let "
                    "it finish and add this case again.\n\n"
                    "This server is also running an old version: a current "
                    "one waits for the writer instead of refusing. Restart "
                    "the server from the updated program folder."
                ) from exc
            if exc.code == 503:
                # The store is busy, which is a queue rather than a refusal.
                raise RemoteStoreError(message) from exc
            raise RemoteStoreError(f"The hash store refused: {message}") from exc
        except urllib.error.URLError as exc:
            # A certificate failure arrives wrapped in a URLError, and
            # "could not reach" would send someone to check the network when
            # the connection was made and refused on trust.
            if "CERTIFICATE_VERIFY_FAILED" in str(exc.reason):
                raise RemoteStoreError(
                    f"Reached {self.url}, but its certificate is not trusted "
                    f"by this machine. If your lab issued its own, give the "
                    f"program a copy of that certificate (the .crt file) in "
                    f"the Hash Sets tab.") from exc
            raise RemoteStoreError(
                f"Could not reach the hash store at {self.url}: "
                f"{exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            import ssl as _ssl
            if isinstance(exc, _ssl.SSLCertVerificationError):
                raise RemoteStoreError(
                    f"The hash store's certificate was not trusted: {exc.reason}. "
                    f"If this is your lab's own self-signed certificate, give "
                    f"the program a copy of it (the .crt file) in the Hash "
                    f"Sets tab.") from exc
            detail = str(exc)
            if "timed out" in detail.lower():
                raise RemoteStoreError(
                    f"The hash store at {self.url} answered too slowly "
                    f"({self.timeout}s). The connection worked, so this is "
                    f"the server being busy rather than unreachable: an "
                    f"import running, or the first request after a restart "
                    f"on a large store. Try again -- the second request is "
                    f"usually quick.") from exc
            raise RemoteStoreError(
                f"Could not reach the hash store at {self.url}: {exc}") from exc

    # -- the same methods the local store has ----------------------------
    def whoami(self) -> dict:
        return self._call("/api/v1/whoami")

    def size(self) -> int:
        stats = self._call("/api/v1/stats")
        self.server_version = str(stats.get("server", ""))
        return int(stats.get("hashes", 0))

    def server_is_current(self) -> dict:
        """Is the server new enough to behave as this client expects?

        A unit runs one server and many copies of the program, and the
        server is the one nobody restarts. An examiner meeting a fault that
        was fixed months ago should be told that is what happened.
        """
        try:
            stats = self._call("/api/v1/stats")
        except RemoteStoreError as exc:
            return {"known": False, "why": str(exc)}
        running = str(stats.get("server", "0.0.0"))
        try:
            parts = tuple(int(piece) for piece in running.split(".")[:3])
        except ValueError:
            return {"known": False, "running": running}
        current = parts >= NEEDS_SERVER
        return {
            "known": True, "running": running,
            "needs": ".".join(str(n) for n in NEEDS_SERVER),
            "current": current,
            "why": ("" if current else
                    f"The server is running {running}; this program expects "
                    f"{'.'.join(str(n) for n in NEEDS_SERVER)} or newer. An "
                    f"older server stores a case graded under another region's "
                    f"scheme as if it were US codes, and refuses a "
                    f"contribution while an import runs. Restart the server "
                    f"from the updated program folder."),
        }

    def sources(self) -> list:
        return self._call("/api/v1/sources").get("sources", [])

    def lookup(self, digests) -> list:
        """Matches for these digests. Sent in batches, because a run is big."""
        digests = [str(d).lower() for d in digests if d]
        matches = []
        for start in range(0, len(digests), BATCH):
            chunk = digests[start:start + BATCH]
            answer = self._call("/api/v1/lookup", {"digests": chunk}, "POST")
            matches.extend(answer.get("matches", []))
        return matches

    def scan_manifest(self, manifest_path: str,
                      include_duplicates: bool = True) -> dict:
        """Check a run against the shared store.

        Every digest in the run goes up in batches and only the matches come
        back, so a hundred and sixty thousand files is a few dozen requests
        rather than a hundred and sixty thousand. The store's contents never
        travel: the server answers about the digests it was sent, and says
        nothing about the ones it holds.
        """
        import json as _json

        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = _json.load(fh)
        records = list(manifest.get("files", []))
        if include_duplicates:
            records += manifest.get("duplicates", [])

        wanted, per_record = [], []
        for record in records:
            digests = [(record.get(key) or "").lower()
                       for key in ("md5", "sha1", "sha256")]
            digests = [d for d in digests if d]
            per_record.append(digests)
            wanted.extend(digests)

        found = {}
        for match in self.lookup(sorted(set(wanted))):
            found.setdefault(match.get("digest", "").lower(), []).append(match)

        # One match per source per file, and each file counted once. Hits
        # were gathered per digest, so a file whose MD5 and SHA-256 were both
        # in a set had that set twice in its matches and was counted twice;
        # and every duplicate location was counted again. The local store
        # already merged per source -- the two now count alike.
        by_source, rows, seen = {}, [], {}
        for record, digests in zip(records, per_record):
            key = (record.get("sha256") or "").lower() or tuple(sorted(digests))
            if key not in seen:
                merged = {}
                for digest in digests:
                    for hit in found.get(digest, []):
                        source = hit.get("source", "")
                        if source in merged:
                            algo = hit.get("algo", "")
                            have = merged[source].get("matched_on", "")
                            if algo and algo not in have.split(", "):
                                merged[source]["matched_on"] = ", ".join(
                                    sorted(filter(None, have.split(", ") + [algo])))
                            continue
                        merged[source] = dict(hit, matched_on=hit.get("matched_on")
                                              or hit.get("algo", ""))
                seen[key] = list(merged.values())
                for source in merged:
                    by_source[source] = by_source.get(source, 0) + 1
            rows.append({"record": record, "matches": seen[key]})
        matched = sum(1 for hits in seen.values() if hits)
        return {"checked": len(seen), "matched": matched,
                "unmatched": len(seen) - matched, "by_source": by_source,
                "locations": len(records),
                "locations_matched": sum(1 for row in rows if row["matches"]),
                "rows": rows}

    def contribute_rows(self, rows, source_name: str,
                        contributor: str = "", admin: bool = False,
                        changes: list = None, region: str = "US") -> int:
        # Who contributes, and whether they may add to another examiner's
        # case, are decided by the server from the token -- never by what
        # the client says. The parameters match HashStore's so a caller can
        # use either store.
        return self._contribute_batches(rows, source_name, region)["added"]

    def _check_region(self, region: str) -> None:
        """Refuse to send a non-US case to a server that cannot check it.

        A server before 1.2.0 ignores the region a contribution carries and
        stores its codes as US ones, where every US client then applies them.
        """
        if (region or "US") == "US":
            return
        stats = self._call("/api/v1/stats")
        try:
            running = tuple(int(p) for p in
                            str(stats.get("server", "0")).split(".")[:3])
        except ValueError:
            running = (0,)
        if running < REGION_SERVER:
            raise RemoteStoreError(
                f"This case is graded under the {region} scheme, and the "
                f"server ({stats.get('server', 'unknown')}) is too old to "
                f"keep that apart from US gradings. Nothing was sent. Update "
                f"the server to this program's version first.")
        if stats.get("region") and stats["region"] != region:
            raise RemoteStoreError(
                f"The server's store holds {stats['region']} gradings; this "
                f"case is graded under {region}. Nothing was sent.")

    def _contribute_batches(self, rows, source_name: str,
                            region: str = "US") -> dict:
        # In batches, like lookups: the server caps a request's size
        # (hashserver.MAX_CONTRIBUTE), and a large case in one body was
        # a request of hundreds of megabytes.
        rows = list(rows)
        self._check_region(region)
        added = regraded = 0
        for start in range(0, len(rows), CONTRIBUTE_BATCH):
            answer = self._call("/api/v1/contribute",
                                {"rows": rows[start:start + CONTRIBUTE_BATCH],
                                 "source": source_name,
                                 # The server refuses codes from another
                                 # region's scheme than its store's.
                                 "region": region}, "POST")
            added += int(answer.get("added", 0))
            regraded += int(answer.get("regraded", 0))
        return {"added": added, "regraded": regraded}

    def contribute(self, out_dir: str, source_name: str,
                   categories=None, note: str = "") -> dict:
        """Send this case's gradings to the shared store.

        The reading of the case happens here, on the machine that has it;
        only hashes and the labels they were graded with go over the wire.
        """
        import os

        from fme.review import casedb

        manifest_path = os.path.join(out_dir, "manifest.json")
        case_path = os.path.join(out_dir, "case.db")
        if not os.path.isfile(manifest_path) or not os.path.isfile(case_path):
            raise RemoteStoreError(
                f"{out_dir} has no case to contribute: nothing has been "
                f"categorized there.")

        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        case = casedb.CaseDB(case_path)
        decisions = case.decisions()
        names = {c["code"]: c["name"] for c in case.categories()}
        uncategorized, region = case.uncategorized, case.region
        case.close()

        wanted = set(categories) if categories else None
        rows, contributed, skipped, by_sets = [], 0, 0, 0
        for record in manifest.get("files", []):
            decided = decisions.get((record.get("sha256") or "").lower())
            code = decided["category"] if decided else uncategorized
            if code == uncategorized or (wanted and code not in wanted):
                skipped += 1
                continue
            if decided and decided.get("auto"):
                # A hash set's match, not anyone's judgment: sent on, it came
                # back to every case as "case X graded this".
                by_sets += 1
                continue
            rows.append({"md5": record.get("md5", ""),
                         "sha1": record.get("sha1", ""),
                         "sha256": record.get("sha256", ""),
                         "category": code,
                         "label": names.get(code, str(code))})
            contributed += 1

        if not rows:
            raise RemoteStoreError(
                "No categorized files to contribute. Grade some first: an "
                "uncategorized hash teaches the next case nothing"
                + (f", and the {by_sets} graded by a hash set are its "
                   f"match, not this case's finding." if by_sets else "."))

        answer = self._contribute_batches(rows, source_name, region)
        # The keys HashStore.contribute returns, so the page reads one shape
        # whichever store is open. "hashes" is the digests sent, as there;
        # "stored" is what the server says landed, and "regraded" how many
        # earlier grades it changed.
        hashes = sum(1 for row in rows for key in ("md5", "sha1", "sha256")
                     if row.get(key))
        return {"source": source_name, "files": contributed, "hashes": hashes,
                "skipped_uncategorized": skipped, "skipped_hash_set": by_sets,
                "stored": answer["added"],
                "regraded": answer["regraded"]}

    def import_set(self, path: str, kind: str = "", name: str = "",
                   progress=None) -> dict:
        """Ask the server to import a set from a path *it* can see.

        In HashStore.import_set's order. It was (path, name, kind), the web
        route passes kind second as the local store takes it, and the server
        was sent the kind as the set's name and no kind at all.
        """
        return self._call("/api/v1/import",
                          {"path": path, "kind": kind, "name": name}, "POST")

    def remove_source(self, name: str) -> int:
        return int(self._call("/api/v1/remove", {"source": name},
                              "POST").get("removed", 0))

    def close(self) -> None:
        """Nothing to close: each call is its own connection."""


def looks_remote(where: str) -> bool:
    return str(where).lower().startswith(("http://", "https://"))
