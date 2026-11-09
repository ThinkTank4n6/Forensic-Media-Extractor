"""A resume never shrinks a manifest or leaves it malformed.

Three things a resume got wrong:

- With dedupe off, identical files are stored once each, with a row each.
  The resume reloaded only the first row per digest, and close() rewrites
  manifest.csv from what was loaded: two of three rows vanished, and the
  files stayed on disk, unlisted.
- It appended to manifest.csv as it found it -- after a row torn by the
  crash, under a header an older version may have written in another
  column order. A second crash before close() kept both faults.
- A crash between storing a file and writing its row left temporary files,
  empty claimed names, or a whole file the manifest does not list.
"""
import csv
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import media_core  # noqa: E402
from fme.exports import report  # noqa: E402


def picture(seed: int) -> bytes:
    rng = __import__("random").Random(seed)
    return b"\xff\xd8\xff\xe0" + bytes(rng.getrandbits(8) for _ in range(4000)) + b"\xff\xd9"


def store(out, data: bytes, at: int):
    return out.store(iter([data]), category="image", ext="jpg",
                     stem=f"recovered_{out.next_index():04d}_{at}",
                     engine="carve", offset=at)


def rows(path: str) -> list:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    ok = True
    root = os.path.join(tempfile.gettempdir(), "fme_resume_integrity-") + str(os.getpid())
    shutil.rmtree(root, ignore_errors=True)

    case = os.path.join(root, "nodedupe")
    out = media_core.OutputManager(case, dedupe=False, metadata=False)
    for at in range(3):
        store(out, picture(1), at)
    out.close({})
    out = media_core.OutputManager(case, dedupe=False, metadata=False, resume=True)
    out.close({})
    kept = len(rows(os.path.join(case, "manifest.csv"))) == 3
    ok &= kept
    print(f"a no-dedupe case keeps all three rows through a resume: {kept}")

    # A torn manifest.csv, twice: once in a case from before manifest.db
    # (the CSV is the record, so the torn row's file is an orphan) and once
    # in a current case (manifest.db holds the row the CSV lost).
    for legacy in (True, False):
        case = os.path.join(root, "torn-legacy" if legacy else "torn")
        out = media_core.OutputManager(case, metadata=False)
        store(out, picture(2), 10)
        store(out, picture(3), 20)
        out._csv_fh.close()
        out._dup_csv_fh.close()                 # a crash: close() never ran
        out._db.close()
        if legacy:
            os.remove(os.path.join(case, "manifest.db"))
        path = os.path.join(case, "manifest.csv")
        with open(path, "rb+") as fh:           # and the last row torn
            fh.seek(-25, os.SEEK_END)
            fh.truncate()
        out = media_core.OutputManager(case, metadata=False, resume=True)
        store(out, picture(4), 30)
        out._csv_fh.close()                     # a second crash
        out._dup_csv_fh.close()
        got = rows(path)
        expect = ([("10", "carve"),
                   # The torn row's file: listed by its hashes since
                   # v2026.11.305, not left out of the manifest.
                   ("", media_core.ORPHAN_ENGINE),
                   ("30", "carve")] if legacy
                  # Since v2026.11.306 the row was never only in the CSV.
                  else [("10", "carve"), ("20", "carve"), ("30", "carve")])
        sound = all(r.get("sha256") and len(r["sha256"]) == 64 and None not in r
                    for r in got) and [(r["offset"], r["engine"]) for r in got] == expect
        ok &= sound
        print(f"{'a case from before manifest.db' if legacy else 'a current case'}: "
              f"after a torn row and a second crash every row is whole: {sound} "
              f"({[r.get('offset') for r in got]})")

    case = os.path.join(root, "leftovers")
    out = media_core.OutputManager(case, metadata=False)
    store(out, picture(5), 40)
    out.close({})
    folder = os.path.join(case, "images", "jpg")
    open(os.path.join(case, ".tmp_1_abc"), "wb").write(b"partial")
    open(os.path.join(folder, "recovered_0009_99.jpg"), "wb").close()
    with open(os.path.join(folder, "recovered_0010_77.jpg"), "wb") as fh:
        fh.write(picture(6))
    out = media_core.OutputManager(case, metadata=False, resume=True)
    summary = {}
    out.close(summary)
    gone = not os.path.exists(os.path.join(case, ".tmp_1_abc")) and \
        not os.path.exists(os.path.join(folder, "recovered_0009_99.jpg"))
    ok &= gone
    print(f"a temporary file and an empty claimed name are removed: {gone}")
    named = summary.get("orphaned_files") == [os.path.join("images", "jpg",
                                                           "recovered_0010_77.jpg")]
    ok &= named
    print(f"a whole file with no row is kept and named: {named}")
    told = any("not in its manifest" in line
               for line in report.coverage_limits({"summary": summary}))
    ok &= told
    print(f"and the report says so: {told}")

    shutil.rmtree(root, ignore_errors=True)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
