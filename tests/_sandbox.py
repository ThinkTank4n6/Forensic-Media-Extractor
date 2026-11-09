"""Somewhere for a test's program state that is not the source tree.

Imported first by every test that starts the GUI. The GUI keeps its case
list, hash store and media_out/ beside the program, and its settings in the
user's own settings folder; run from the source tree, the GUI tests left
case databases and case folders among the code (107 files once made it into
a release zip) and wrote into the settings of whoever ran them. This points
both at a temporary folder for the life of the test, then removes it.
"""
import atexit
import os
import shutil
import tempfile

def _playwright_browsers() -> str:
    """Where Playwright installed its browsers, worked out before anything
    below moves the folders it looks in."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "")
    elif os.uname().sysname == "Darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "ms-playwright") if base else ""


# Playwright finds its Chromium under %LOCALAPPDATA% on Windows, and that is
# moved below: every browser test on Windows said "Chromium will not launch
# here: executable doesn't exist at ...\fme_test_data-...\config\ms-playwright"
# while Chromium sat installed where it always was (found on the lab machine,
# Session E). Its real place is pinned first, by Playwright's own setting,
# unless the examiner has already set one.
if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
    _browsers = _playwright_browsers()
    if _browsers and os.path.isdir(_browsers):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _browsers

DATA = tempfile.mkdtemp(prefix="fme_test_data-")
os.environ["FME_DATA_DIR"] = DATA
os.environ["XDG_CONFIG_HOME"] = os.path.join(DATA, "config")
os.environ["LOCALAPPDATA"] = os.path.join(DATA, "config")
atexit.register(shutil.rmtree, DATA, True)
