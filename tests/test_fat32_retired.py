"""The old FAT32 browser is retired (v2026.11.319).

Its Media Extraction tab ran the engine outside the case: the run's folder
had no case.db, was not in the main interface's case list, and was never
checked against the hash store, so a known CAM hash in it was never flagged.
Nothing an examiner reads described it. Its one unique use, browsing every
file of a FAT32 image, the examiner did not need. This checks it is gone,
that `fme fat32gui` says where to go instead, and that fat32_core -- which
the engine still uses for FAT32 volumes The Sleuth Kit refuses -- stays.
"""
import contextlib
import importlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


check("its server and page are gone",
      not os.path.exists(os.path.join(PROJECT, "fme", "web", "app.py"))
      and not os.path.exists(os.path.join(PROJECT, "fme", "web", "templates")))
try:
    importlib.import_module("fme.web.app")
    importable = True
except ImportError:
    importable = False
check("and cannot be imported", not importable)

from fme import cli  # noqa: E402
said = io.StringIO()
saved_argv = sys.argv
sys.argv = ["fme", "fat32gui"]
try:
    with contextlib.redirect_stderr(said), contextlib.redirect_stdout(io.StringIO()):
        code = cli.main()
finally:
    sys.argv = saved_argv
check("`fme fat32gui` refuses, and says to use the main interface",
      code == 2 and "retired" in said.getvalue() and "main interface" in said.getvalue(),
      said.getvalue().strip()[:80])
check("it is not listed among the tools", "fat32gui" not in cli.TOOLS)

from fme.engines import fat32_core, fs_walk  # noqa: E402
check("fat32_core stays, for the engine", hasattr(fat32_core, "Fat32FileSystem")
      and "fat32_core" in open(fs_walk.__file__, encoding="utf-8").read())
check("and its retired carve is not left behind as dead code",
      not hasattr(fat32_core, "carve_unallocated"))

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
