"""A recovered file can never run as this program's own page.

Recovered files are the suspect's files. /api/file served them inline with
a guessed type and no other headers, from the same origin as the GUI. An
SVG or HTML file opened through its link ran its scripts as the GUI --
with the run controls, the case files and the local disk behind it.

Now every route that hands back evidence bytes says nosniff and a sandbox
policy, and anything the gallery cannot draw is sent as a download.
"""
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.web import media_gui  # noqa: E402


def main() -> int:
    ok = True
    root = os.path.join(tempfile.gettempdir(), "fme_evidence_headers-") + str(os.getpid())
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(os.path.join(root, "images"))
    with open(os.path.join(root, "images", "evil.svg"), "w") as fh:
        fh.write('<svg xmlns="http://www.w3.org/2000/svg"><script>fetch("/api/cases")'
                 '</script></svg>')
    with open(os.path.join(root, "images", "photo.jpg"), "wb") as fh:
        fh.write(b"\xff\xd8\xff\xe0" + b"\x00" * 200 + b"\xff\xd9")
    job = "f" * 32
    with media_gui.JOBS_LOCK:
        media_gui.JOBS[job] = {"out_dir": root}
    client = media_gui.app.test_client()

    svg = client.get(f"/api/file?job={job}&file=images/evil.svg")
    sandboxed = "sandbox" in svg.headers.get("Content-Security-Policy", "") and \
        svg.headers.get("X-Content-Type-Options") == "nosniff"
    ok &= sandboxed
    print(f"a recovered SVG carries a sandbox policy and nosniff: {sandboxed}")
    download = "attachment" in svg.headers.get("Content-Disposition", "")
    ok &= download
    print(f"and is sent as a download, not shown: {download}")

    jpg = client.get(f"/api/file?job={job}&file=images/photo.jpg")
    shown = jpg.status_code == 200 and \
        "attachment" not in jpg.headers.get("Content-Disposition", "") and \
        "sandbox" in jpg.headers.get("Content-Security-Policy", "")
    ok &= shown
    print(f"a JPEG is still shown inline, under the same policy: {shown}")

    page = client.get("/")
    free = "sandbox" not in page.headers.get("Content-Security-Policy", "")
    ok &= free
    print(f"and the program's own page is not sandboxed: {free}")

    with media_gui.JOBS_LOCK:
        media_gui.JOBS.pop(job, None)
    shutil.rmtree(root, ignore_errors=True)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
