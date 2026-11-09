"""
compression.py -- large JSON answers gzipped for the browser that asked.

The page still receives a whole case's file list when it opens a case and
when a run finishes: 46 MB of JSON for 55,508 files (the audit document, item "Open"
3), more than 160 MB at 200,000. Most of that is the same keys repeated per
file, which gzip removes: measured on a 55,508-file list, 51 MB became
4.0 MB in 73 ms at level 1. Every browser decompresses it without being
asked, so the page's own code does not change.

This shortens the transfer, not the work: the browser still parses and
holds the whole list. That part goes when the page asks the server for its
page of files instead (the audit document, "Open" 3), and is not done here.
"""

from __future__ import annotations

import gzip

# Below this an answer is sent as it is: compressing a few kilobytes costs
# more than it saves.
THRESHOLD = 64 * 1024
# Level 1: about 13x smaller on a file list, at a fraction of level 6's time.
LEVEL = 1


def install(app) -> None:
    """Gzip every JSON answer over THRESHOLD for a browser that accepts it."""
    from flask import request

    @app.after_request
    def _gzip_json(response):
        if (response.mimetype != "application/json"
                or response.direct_passthrough
                or response.status_code < 200 or response.status_code >= 300
                or "Content-Encoding" in response.headers
                or "gzip" not in (request.headers.get("Accept-Encoding") or "").lower()):
            return response
        body = response.get_data()
        if len(body) < THRESHOLD:
            return response
        response.set_data(gzip.compress(body, compresslevel=LEVEL))
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(response.get_data()))
        vary = response.headers.get("Vary", "")
        if "accept-encoding" not in vary.lower():
            response.headers["Vary"] = (vary + ", " if vary else "") + "Accept-Encoding"
        return response
