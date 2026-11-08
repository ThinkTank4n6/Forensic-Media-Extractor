"""A file-system file that cannot be read to its recorded size is marked short.

_fs_chunks (TSK walk), _fat32_chunks (fat32_core) and the vendor-fs reader
all ended their stream quietly on a read error, and the caller stored the
part read -- hashed, engine=filesystem -- as if it were the whole file. An
8 MB video with a bad block at 2 MB went into the manifest as a complete
2 MB file. Now the row is bounded "short", its note gives the recorded size,
the size stored and why, and the summary counts it.
"""
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import media_core  # noqa: E402

MIB = 1 << 20


class BadBlockFile:
    """A pytsk3 File whose data cannot be read past 2 MiB."""
    def read_random(self, off, n):
        if off >= 2 * MIB:
            raise IOError("TSK: unable to read block")
        head = b"\xff\xd8\xff\xe0" if off == 0 else b""
        return head + os.urandom(n - len(head))


class GoodFile:
    def read_random(self, off, n):
        head = b"\xff\xd8\xff\xe0" if off == 0 else b""
        return head + os.urandom(n - len(head))


def main() -> int:
    ok = True
    root = os.path.join(tempfile.gettempdir(), "fme_fs_short-") + str(os.getpid())
    shutil.rmtree(root, ignore_errors=True)
    out = media_core.OutputManager(root, metadata=False)
    stats = {"errors": 0}

    got: dict = {}
    rec = out.store(media_core._fs_chunks(BadBlockFile(), 8 * MIB, MIB, got=got),
                    after_read=media_core._short_after_read(8 * MIB, "", got, stats),
                    category="image", ext="jpg", stem="recovered_0001_bad",
                    engine="filesystem", source_path="/DCIM/bad.jpg")
    check = rec is not None and rec.size == 2 * MIB
    ok &= check
    print(f"the part that could be read is kept (2 MiB): {check}")
    check = rec is not None and rec.bounded == "short"
    ok &= check
    print(f"and it is marked bounded=short: {check}")
    check = rec is not None and "SHORT" in rec.note and "8,388,608" in rec.note \
        and "unable to read block" in rec.note
    ok &= check
    print(f"its note gives the recorded size and the read error: {check}")
    check = stats.get("short_reads") == 1 and stats["errors"] == 1
    ok &= check
    print(f"and it is counted: {check}")

    got = {}
    rec = out.store(media_core._fs_chunks(GoodFile(), 3 * MIB, MIB, got=got),
                    after_read=media_core._short_after_read(3 * MIB, "x", got, stats),
                    category="image", ext="jpg", stem="recovered_0002_good",
                    engine="filesystem", source_path="/DCIM/good.jpg", note="x")
    check = rec is not None and rec.bounded == "" and rec.note == "x" \
        and stats.get("short_reads") == 1
    ok &= check
    print(f"a file read whole is not marked: {check}")

    out.close({})
    shutil.rmtree(root, ignore_errors=True)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
