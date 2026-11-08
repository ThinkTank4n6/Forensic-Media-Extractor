"""A folder walk that stops says so, and every reader stops at the same depth.

TSK's walk stopped at 64 levels and the FAT fallback at 32, both silently,
and the FAT fallback dropped a folder it could not read with a bare
`return`. The cross-check between the two readers then showed differences
that came from the limits, and a damaged folder looked like an empty one.
"""
import logging
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

from fme.engines import media_core  # noqa: E402
import _engine  # noqa: E402 -- replaces a name in every engine module

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


def entry(name, path, cluster, is_dir):
    return types.SimpleNamespace(name=name, path=path, first_cluster=cluster,
                                 is_dir=is_dir, is_deleted=False, size=10)


DEPTH = 80          # deeper than any limit
BROKEN = 5          # the cluster of a folder that cannot be read


def fake_list(volume, cluster, path, released):
    if cluster == BROKEN + 1000:
        raise OSError("bad sector in the directory cluster")
    level = cluster - 2
    out = [entry(f"f{level}.jpg", f"{path}f{level}.jpg", 0, False)]
    if level < DEPTH:
        out.append(entry(f"d{level}", f"{path}d{level}/", cluster + 1, True))
    if level == 0:
        out.append(entry("broken", f"{path}broken/", BROKEN + 1000, True))
    return out


saved = _engine.replace("_list_folder", fake_list)
try:
    fs = types.SimpleNamespace(bs=types.SimpleNamespace(root_cluster=2))
    stats = {"errors": 0}
    files = list(media_core._fat32_all_files(fs, False, stats))
finally:
    _engine.restore(saved)

deepest = max(f.path.count("/") - 1 for f in files)
print("the FAT fallback walk")
check("goes as deep as the TSK walk", deepest == media_core.WALK_DEPTH_LIMIT,
      deepest)
check("counts the folder it did not enter", stats.get("fs_dirs_too_deep") == 1,
      stats.get("fs_dirs_too_deep"))
check("counts the folder it could not read, as the TSK walk does",
      stats.get("fs_dirs_unreadable") == 1 and stats["errors"] == 1,
      stats.get("fs_dirs_unreadable"))

print("\none limit")
source = ""
for part in ("media_core", "fs_walk", "deleted_recovery", "embedded", "sources"):
    with open(os.path.join(os.path.dirname(media_core.__file__), part + ".py"),
              encoding="utf-8") as fh:
        source += fh.read()
check("no walk in the engine keeps its own number",
      "depth > 32" not in source and "depth > 64" not in source)

print("\nthe report names both")
from fme.exports import report  # noqa: E402
limits = " ".join(report.coverage_limits(
    {"summary": {"fs_dirs_too_deep": 1, "fs_dirs_unreadable": 1}}))
check("too deep", "nested too deep" in limits)
check("unreadable", "could not open" in limits)

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
