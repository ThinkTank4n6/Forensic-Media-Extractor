"""The Filters pane of a large case is counted once, not after every grading
(v2026.11.323).

Reported against a case of 221,592 files: the pane took "forever", and grading
a page with it open did not move on to the next. Every grading recounted every
file's metadata from scratch -- seconds of server time, holding the
interpreter while the page asked for its next files. A file's metadata does
not change when it is graded, so its facet values are now worked out once per
file list (and time zone) and the counting after a grading reads them.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fme.review import galleryquery as g  # noqa: E402

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


N = 60000
rows = [{"rel_path": f"r{i}.jpg", "sha256": f"{i:064x}", "name": f"r{i}.jpg",
         "ext": "jpg" if i % 4 else "png", "category": "image", "size": 1000 + i * 37,
         "deleted": i % 7 == 0, "engine": "tsk",
         "captured": f"2021-0{i % 9 + 1}-01T10:00:00+00:00" if i % 3 == 0 else "",
         "mtime": "2023-12-31T23:30:00-05:00", "crtime": "2022-01-01T10:00:00+00:00",
         "camera_make": "Apple" if i % 5 == 0 else ""} for i in range(N)]
state = g.State({"timeZone": "America/New_York", "catFilter": "8"})

print("counting a large case")
started = time.perf_counter()
first = g.facet_counts(rows, {}, state)
cold = time.perf_counter() - started
graded = {rows[0]["sha256"]: {"category": 1}}
started = time.perf_counter()
again = g.facet_counts(rows, graded, state)
warm = time.perf_counter() - started
check("after a grading, counted from the values already worked out",
      warm < cold * 0.5, f"{cold:.2f}s, then {warm:.2f}s")

# The same answer as counting from nothing.
g._TABLES.clear()
fresh = g.facet_counts(rows, graded, state)
check("and the same counts as working them out afresh", again == fresh)
check("the grading is in them: one file fewer on Uncategorized",
      sum(first["ext"]["counts"].values()) - sum(again["ext"]["counts"].values()) == 1)
check("a year is counted in the page's zone: 23:30 EST on Dec 31 is that year",
      again["mtime_year"]["counts"].get("2023") == N - 1)
other = g.State({"timeZone": "UTC", "catFilter": "8"})
check("and in another zone, that zone's year",
      g.facet_counts(rows, graded, other)["mtime_year"]["counts"].get("2024") == N - 1)

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
