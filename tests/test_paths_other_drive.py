"""A file path on another Windows drive is refused like any other path that
leaves the case, not answered with a 500.

os.path.commonpath raises ValueError for paths on different drives. Stood in
for here, so this runs on any platform.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.web import media_gui, request_guard  # noqa: E402

ok = True
real = os.path.commonpath


def other_drive(paths):
    raise ValueError("Paths don't have the same drive")


os.path.commonpath = other_drive
try:
    inside = request_guard.contained("C:\\cases\\one", "D:\\elsewhere\\x.jpg")
    with media_gui.JOBS_LOCK:
        media_gui.JOBS["d" * 32] = {"out_dir": os.path.abspath("."), "status": "done"}
    client = media_gui.app.test_client()
    answer = client.get(f"/api/file?job={'d' * 32}&file=x.jpg")
    status = answer.status_code
finally:
    os.path.commonpath = real
    with media_gui.JOBS_LOCK:
        media_gui.JOBS.pop("d" * 32, None)

good = inside is False and status == 400
ok &= good
print(f"a file on another drive: contained {inside}, /api/file answers {status} "
      f"(400) -- {'ok' if good else 'WRONG'}")
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
