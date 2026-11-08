"""
fme.py -- one entry point for everything, and what the executable is built
from.

With no arguments it starts the GUI, which is what a double-clicked
executable should do. Otherwise the first argument picks a tool and the rest
are passed to it unchanged:

    ForensicMediaExtractor                     the GUI
    ForensicMediaExtractor gui -o D:\\cases     the GUI, results on D:
    ForensicMediaExtractor extract -i case.E01 -o D:\\out --vic DC26-253
    ForensicMediaExtractor inspect -i case.E01
    ForensicMediaExtractor triage -m D:\\out\\manifest.json --videos
    ForensicMediaExtractor hashsets --scan D:\\out\\manifest.json --nsrl NSRLFile.txt
    ForensicMediaExtractor report -m D:\\out\\manifest.json -o D:\\out\\report.html
    ForensicMediaExtractor doctor              what is installed and where

The multiprocessing guard at the bottom matters more than it looks. Parallel
carving starts workers with the "spawn" method, and a frozen executable
creates each one by re-running itself. Without freeze_support() every worker
restarts the whole program instead of running the worker function -- which,
for a GUI that opens a browser window, means a new window per worker, over
and over.
"""

# Allow this file to be run directly -- "python fme/engines/media_core.py" --
# as well as imported. Run as a script there is no package context, so the
# project root goes on the path before the package imports below.
if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

import multiprocessing
import os
import sys

TOOLS = {
    "gui": ("fme.web.media_gui", "the browser interface (default)"),
    "extract": ("fme.engines.media_core", "run an extraction from the command line"),
    "inspect": ("fme.engines.media_core", "report what an image is, then exit"),
    "triage": ("fme.review.triage_report", "summarize a manifest for review"),
    "hashsets": ("fme.review.hashsets",
                 "match a run against sets loaded for one run"),
    "recover": ("fme.review.recover",
                "put a case back after a grading went wrong, using its "
                "audit log"),
    "cases": ("fme.review.registry",
              "the list of cases processed here: list, scan, forget"),
    "hashserver": ("fme.review.hashserver",
                   "serve one hash store to a unit, with per-user access"),
    "store": ("fme.review.hashstore",
              "the hash store kept between cases: import, match, contribute"),
    "screen": ("fme.engines.screening",
               "score images by skin-tone proportion, to order a backlog "
               "(not a nudity classifier)"),
    "winsearch": ("fme.engines.winsearch",
                  "name cached thumbnails using a Windows Search index"),
    "scheme": ("fme.review.savedset",
               "your own categories and flags, carried between cases"),
    "framematch": ("fme.engines.frame_match",
                   "match stills to the videos they came from"),
    "frames": ("fme.engines.video_frames",
               "decode frames for videos a browser cannot play (needs OpenCV)"),
    "similar": ("fme.engines.similarity",
                "group visually similar images (needs Pillow)"),
    "report": ("fme.exports.report", "write a case report: HTML, CSV, JSON, "
               "KML, MD5, Project VIC"),
    "contactsheet": ("fme.exports.report_html", "the quick contact sheet"),
    "lava": ("fme.exports.lava_export", "write a LAVA output set"),
    "openlava": ("fme.exports.lava_launch", "open a LAVA project locally"),
    "vic": ("fme.exports.projectvic_export", "write Project VIC (VICS 2.0) JSON"),
    "vicimport": ("fme.exports.vic_import",
                  "read a Project VIC file, or match a hash set"),
    "c4all": ("fme.exports.c4all_export", "write C4All-style XML reports"),
    "kml": ("fme.exports.kml_export", "geolocated media as KML or KMZ"),
    "basemap": ("fme.engines.basemaps",
                "offline maps for locator images: import, list, use, remove"),
    "db": ("fme.engines.db_extract", "extract media from a SQLite database"),
    "doctor": ("", "report what is installed and where output goes"),
    "version": ("", "the version and a fingerprint of the code running"),
}


def usage() -> int:
    if getattr(sys, "frozen", False):
        name = os.path.basename(sys.executable)
    elif os.path.basename(sys.argv[0]) in ("__main__.py", "cli.py"):
        name = "fme"          # installed, or python -m fme
    else:
        name = os.path.basename(sys.argv[0])
    print("Forensic Media Extractor\n")
    print(f"  {name}                     start the interface")
    print(f"  {name} <tool> [options]    run one tool\n")
    launcher = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "launch.py")
    if not getattr(sys, "frozen", False) and os.path.isfile(launcher):
        print("From any directory, without installing anything:")
        print(f"  python \"{launcher}\" <tool> [options]")
        print("  (python -m fme works too, but only from the project root)\n")
    print("Tools:")
    for key, (_, description) in TOOLS.items():
        print(f"  {key:<10} {description}")
    print(f"\n  {name} <tool> --help      options for that tool")
    return 0


# What the vendored acquisition reader opens without the C extension this
# program would otherwise need.
VENDOR_COVERS = {"pyewf", "pyvmdk", "pyvhdi", "pyqcow"}


def code_fingerprint() -> str:
    """A SHA-256 over the code actually loaded.

    The version in a file says what somebody meant to install. This says
    what is there. Two examiners comparing results need to know they are
    running the same program, and a folder copied over a network is not
    self-evidently intact.
    """
    import os as _os

    here = _os.path.dirname(_os.path.abspath(__file__))
    if getattr(sys, "frozen", False):
        # The code is inside the build's archive; the fingerprint of the
        # source it was built from was recorded beside it (packaging/fme.spec).
        try:
            with open(_os.path.join(here, "BUILD_FINGERPRINT"),
                      encoding="ascii") as fh:
                return fh.read().strip()
        except OSError:
            return "unknown (this build carries no fingerprint)"
    return fingerprint_of(here)


def fingerprint_of(root: str) -> str:
    """SHA-256 over the program's files under `root`, each named by its path
    from there. By bare name, two files of one name in different folders
    could swap contents without changing it."""
    import hashlib
    import os as _os

    digest = hashlib.sha256()
    for folder, dirs, names in sorted(_os.walk(root)):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(names):
            if not name.endswith((".py", ".js", ".css", ".html")):
                continue
            full = _os.path.join(folder, name)
            relative = _os.path.relpath(full, root).replace(_os.sep, "/")
            with open(full, "rb") as fh:
                digest.update(relative.encode() + b"\0")
                digest.update(fh.read())
    return digest.hexdigest()


def show_version() -> int:
    """What is running here, in a form two machines can compare."""
    import platform
    import os as _os

    here = _os.path.dirname(_os.path.abspath(__file__))
    version = "unknown"
    for candidate in (_os.path.join(here, "VERSION"),
                      _os.path.join(_os.path.dirname(here), "VERSION")):
        if _os.path.isfile(candidate):
            with open(candidate, encoding="utf-8") as fh:
                version = fh.read().strip()
            break

    print(f"Forensic Media Extractor v{version}")
    print(f"  code fingerprint  {code_fingerprint()}")
    print(f"  installed at      {_os.path.dirname(here)}")
    print(f"  Python            {platform.python_version()} "
          f"on {platform.system()} {platform.release()}")
    print()
    print("Two machines reporting the same version and the same")
    print("fingerprint are running the same program. A different")
    print("fingerprint on the same version means one of them has been")
    print("edited or did not copy cleanly.")
    print()
    print(LICENSE_LINES)
    return 0


# Said wherever the program says what it is: the MIT license's notice, the
# no-warranty terms, and the notice The Sleuth Kit's IBM Public License
# requires "in a conspicuous location in the Program".
LICENSE_LINES = (
    "Copyright (c) 2026 Christopher A. Tankelewicz (ThinkTank4n6).\n"
    "MIT License; provided \"as is\", without warranty of any kind (LICENSE).\n"
    "Includes The Sleuth Kit: Copyright (c) 1997,1998,1999, International\n"
    "Business Machines Corporation and others. All Rights Reserved.\n"
    "Other components and their licenses: THIRD_PARTY_NOTICES.md.")


def doctor() -> int:
    from fme.engines import media_core
    from fme.engines import vendor_fs

    vendored = vendor_fs.ewfprobe() is not None
    print(f"Forensic Media Extractor {media_core.RELEASE}")
    print(f"  engine      : media_core {media_core.__version__}")
    print(f"  frozen      : {bool(getattr(sys, 'frozen', False))}")
    print(f"  app folder  : "
          f"{os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))}")
    print(f"  reading     : {media_core._engine_description()}")
    mixed = media_core.mixed_install()
    if mixed:
        print(f"  WARNING     : {mixed}")
    for module, what in (("flask", "the browser interface"),
                         ("waitress", "a faster server, no warning"),
                         ("pytsk3", "exFAT, NTFS, HFS+, ext2/3/4 (APFS is read by the vendored reader)"),
                         ("pyewf", "EnCase E01/Ex01"),
                         ("PIL", "visual similarity, report thumbnails"),
                         ("py7zr", "media inside 7z archives"),
                         ("cv2", "frames from videos a browser cannot play"),
                         ("zstandard", "Snapchat content bundles"),
                         # Only the machine that serves the hash store needs
                         # this, and only once -- but without it the
                         # certificate cannot be made and the setup stops
                         # there, which is a poor moment to find out.
                         ("cryptography", "making a certificate for a shared "
                                          "hash store (server only)"),
                         ("cheroot", "serving the hash store over TLS without "
                                     "blocking (server only)"),
                         ("pyvmdk", "VMDK"), ("pyvhdi", "VHD/VHDX"),
                         ("pyqcow", "QCOW2"),
                         # Without it a key given for a BitLocker or FileVault
                         # volume is refused at the start of the run.
                         ("Crypto", "opening BitLocker and FileVault volumes "
                                    "with a key (pycryptodome)")):
        try:
            __import__(module)
            present = "yes"
        except ImportError:
            present = "no "
        # Several of these stopped being required: the vendored readers
        # open those formats with nothing installed. Saying "no" against a
        # format that works anyway sends someone off to install a C
        # extension they do not need, which on Windows is an afternoon.
        covered = present == "no " and module in VENDOR_COVERS and vendored
        note = f"{what} — read by the vendored reader anyway" if covered else what
        print(f"  {module:<12}: {present}  {note}")
    # zstd inside file systems (Btrfs, and the others the vendored reader
    # decompresses) comes from the standard library, from Python 3.14 only.
    # Without it such files are counted and named in the report, not read
    # (tests/test_real_evidence_fixtures.py).
    try:
        import compression.zstd  # noqa: F401
        zstd = "yes"
    except ImportError:
        zstd = "no "
    print(f"  {'zstd (3.14)':<12}: {zstd}  zstd-compressed files inside Btrfs and "
          f"other file systems; Python {sys.version_info.major}.{sys.version_info.minor} "
          f"here{'' if zstd == 'yes' else ' -- build with Python 3.14 or later to read them'}")
    # OpenCV's version decides two things an examiner cannot otherwise
    # tell apart: SIFT for finding edited copies (4.4 and up) and whether
    # the content model will load at all (5.0 and up).
    try:
        import cv2

        here = tuple(int(part) for part in cv2.__version__.split(".")[:2])
        print(f"  OpenCV      : {cv2.__version__}")
        print(f"    edited-copy matching : "
              f"{'yes' if here >= (4, 4) else 'needs 4.4 or newer'}")
        print(f"    content model        : "
              f"{'loads on this version' if here >= (5, 0) else 'needs 5.0 or newer'}")
        from fme.review import settings as _settings
        from fme.engines import content_search as _content

        from fme.engines import models as _models

        model, source, why = _models.resolve(
            "content", _settings.load().get("content_model", ""))
        if model:
            ready, why = _content.available(model)
        else:
            ready = False
        print(f"    content model        : "
              f"{(source + ', ready') if ready else why[:72]}")
        for entry in _models.describe():
            print(f"    bundled {entry['kind']:15}: "
                  f"{'intact' if entry['ready'] else entry['why'][:60]} "
                  f"({entry['what']})")
    except ImportError:
        pass

    state = vendor_fs.status()
    if vendored or state.get("available"):
        print("  vendored readers (MIT, see the manual's credits):")
        if vendored:
            print("    acquisitions : E01, Ex01, L01, AFF, AFD, AFF4, DMG, "
                  "sparseimage,")
            print("                   sparse bundle, AD1, VHD, VHDX, VMDK, "
                  "QCOW — no modules needed")
        if state.get("available"):
            print(f"    file systems : {', '.join(state['kinds'])}")
            print(f"    qnxprobe     : {state.get('version', '')} "
                  f"({state.get('licence', '')})")
        from fme.engines import carve_judge
        judge = carve_judge.status()
        print(f"    mediacarve   : "
              + (f"{judge['version']} (MIT) -- judges carved lengths for "
                 f"{len(judge['families'])} formats" if judge["available"]
                 else f"MISSING ({judge.get('error', '')}) -- this build cannot carve"))
    else:
        print("  vendored readers: not present — fme/vendor/ is missing, so "
              "QNX, F2FS and the")
        print("                   flash file systems cannot be read, and an "
              "E01 needs libewf")

    try:
        from fme.exports.lava_launch import find_lava
        print(f"  LAVA        : {find_lava() or 'not found on this machine'}")
    except ImportError:
        pass
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help", "help"):
        return usage()

    # The hidden first argument a frozen build gives itself to run one
    # isolated reader in a child process (fme/engines/isolated.py). Not a
    # tool, so not in TOOLS and not in the usage text, and it runs only the
    # workers isolated.WORKERS names.
    if len(sys.argv) > 2 and sys.argv[1] == "_worker":
        from fme.engines import isolated
        return isolated.dispatch(sys.argv[2], sys.argv[3:])

    tool = "gui"
    args = sys.argv[1:]
    if args and args[0] in TOOLS:
        tool, args = args[0], args[1:]
    elif args and args[0] == "fat32gui":
        # Retired in v2026.11.319: its Media Extraction tab ran the engine
        # outside the case -- no case.db, not in the case list, never checked
        # against the hash store -- and nothing an examiner reads described it.
        print("The old FAT32 browser was retired in v2026.11.319. Start runs "
              "from the main interface (fme, or fme gui); it reads FAT32 and "
              "every other file system the program knows.", file=sys.stderr)
        return 2
    elif args and not args[0].startswith("-"):
        print(f"Unknown tool: {args[0]}\n", file=sys.stderr)
        usage()
        return 2

    if tool == "version":
        return show_version()

    if tool == "doctor":
        return doctor()

    import importlib
    module = importlib.import_module(TOOLS[tool][0])
    if tool == "inspect":
        args = args + ["--inspect", "-o", os.devnull]

    sys.argv = [TOOLS[tool][0].rsplit(".", 1)[-1] + ".py"] + args
    return module.main() or 0


def run() -> int:
    """The console entry point.

    `python -m fme` gets these guards from __main__; an installed `fme`
    command calls straight into here, so they belong in both places:
    freeze_support before any worker starts, and a quiet exit when the output
    is piped into something that closes early.
    """
    multiprocessing.freeze_support()
    try:
        return main()
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        os._exit(0)


if __name__ == "__main__":      # python -m fme.cli also works
    sys.exit(run())
