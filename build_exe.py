"""
build_exe.py -- build the self-contained executable, with the checks worth
doing before spending ten minutes on a build that turns out to be missing
half its capability.

    python build_exe.py --check     report what would be included, then stop
    python build_exe.py             build it

PyInstaller bundles what is importable at build time. A build made in an
environment without pytsk3 produces an executable that cannot read NTFS and
says so only when someone points it at an exhibit, so the capabilities are
reported before the build and recorded inside it.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# The one spec. There used to be a second one here, at the project root,
# that this script built from; it had drifted from packaging/fme.spec until
# a build made with it had no interface page, no vendored readers and no
# OpenCV (v2026.11.275 removed it). packaging/build.py drives the same spec.
SPEC = os.path.join(HERE, "packaging", "fme.spec")

# (import name, what it gives you, whether its absence should stop the build)
CAPABILITIES = [
    ("flask", "the browser interface", True),
    ("waitress", "a faster server, and no development-server warning", False),
    ("pytsk3", "exFAT, NTFS, HFS+, APFS, ext2/3/4", False),
    ("pyewf", "EnCase E01/Ex01 images", False),
    ("PIL", "visual similarity and report thumbnails", False),
    ("py7zr", "media inside 7z archives", False),
    ("cv2", "frames from video a browser cannot play", False),
    ("zstandard", "Snapchat content bundles", False),
    ("pillow_heif", "HEIC photographs, which iPhones write", False),
    ("webview", "a window of its own rather than a browser tab", False),
    ("pyvmdk", "VMDK images", False),
    ("pyvhdi", "VHD/VHDX images", False),
    ("pyqcow", "QCOW2 images", False),
]


def probe() -> list:
    found = []
    for module, description, required in CAPABILITIES:
        try:
            __import__(module)
            found.append((module, description, required, True))
        except ImportError:
            found.append((module, description, required, False))
    return found


def report(found: list) -> bool:
    print(f"Python      : {sys.version.split()[0]} ({platform.machine()})")
    print(f"Platform    : {platform.platform()}")
    print("\nWhat will be built in:")
    ok = True
    for module, description, required, present in found:
        mark = "yes" if present else ("MISSING" if required else "no")
        print(f"  {module:<10} {mark:<8} {description}")
        if required and not present:
            ok = False
    missing = [m for m, _, _, present in found if not present]
    if missing:
        print("\nThe executable will not be able to use: " + ", ".join(missing))
        print("They cannot be added afterwards. Install them first:")
        print("    pip install -r requirements.txt")
        print("    pip install pillow py7zr waitress")
    return ok


def write_build_info(found: list) -> str:
    path = os.path.join(HERE, "build_info.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
            "capabilities": {m: present for m, _, _, present in found},
        }, fh, indent=2)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the executable.")
    ap.add_argument("--check", action="store_true",
                    help="report what would be included, then stop")
    ap.add_argument("--clean", action="store_true",
                    help="remove build/ and dist/ first")
    ap.add_argument("--portable", action="store_true",
                    help="build a folder (an .exe beside _internal/) rather "
                         "than one file. It starts immediately instead of "
                         "unpacking itself on every launch, and is the right "
                         "shape for a stick or a share")
    args = ap.parse_args(argv)

    found = probe()
    complete = report(found)
    # The files and vendored readers the build carries, checked by the same
    # code test_packaging.py checks the spec with.
    sys.path.insert(0, os.path.join(HERE, "packaging"))
    import build as _packaging  # noqa: PLC0415 - packaging/build.py
    print()
    missing = _packaging.missing_files()
    if missing:
        print(f"\nMissing from this checkout: {', '.join(missing)}")
    if args.check:
        return 0 if complete and not missing else 1
    if not complete:
        print("\nRefusing to build: Flask is required for the interface.")
        return 2
    if missing:
        print("\nRefusing to build: a build without the files above would "
              "start and then fail when they were needed.")
        return 2

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("\nPyInstaller is not installed:\n    pip install pyinstaller",
              file=sys.stderr)
        return 2

    if args.clean:
        for folder in ("build", "dist"):
            shutil.rmtree(os.path.join(HERE, folder), ignore_errors=True)

    write_build_info(found)
    print(f"\nBuilding the {'portable folder' if args.portable else 'single file'}. "
          f"This takes a few minutes.\n")
    # packaging/fme.spec builds a folder unless told FME_ONEFILE=1; this
    # script's default has always been the single file.
    environment = dict(os.environ, FME_ONEFILE="0" if args.portable else "1")
    result = subprocess.run([sys.executable, "-m", "PyInstaller", SPEC,
                             "--noconfirm"], cwd=HERE, env=environment)
    if result.returncode != 0:
        return result.returncode

    name = "ForensicMediaExtractor" + (".exe" if os.name == "nt" else "")
    built = os.path.join(HERE, "dist", name)
    if args.portable:
        folder = os.path.join(HERE, "dist", "ForensicMediaExtractor")
        built = os.path.join(folder, name)
        if os.path.isdir(folder):
            size = sum(os.path.getsize(os.path.join(root, f))
                       for root, _, files in os.walk(folder) for f in files)
            print(f"\nBuilt {folder} ({size / (1024 * 1024):.0f} MB)")
            print("  Copy the whole folder: the .exe needs _internal/ beside it.")
            print(f"\nCheck it before handing it over:\n    {built} doctor")
        return 0
    if os.path.isfile(built):
        size = os.path.getsize(built) / (1024 * 1024)
        print(f"\nBuilt {built} ({size:.0f} MB)")
        print("\nCheck it before handing it over:")
        print(f"    {built} doctor")
        print("which reports the version, what made it in, and where results go.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        os._exit(0)
