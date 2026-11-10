"""
Build this program into something that runs with nothing installed.

    python packaging/build.py              a folder  -> dist/ForensicMediaExtractor/
    python packaging/build.py --onefile    one file  -> dist/ForensicMediaExtractor[.exe]
    python packaging/build.py --check      what would be bundled, and what is missing

A build is made **on the platform it is for**: a Windows executable is
built on Windows, a macOS one on macOS. Nothing cross-compiles, and a build
machine should have every optional dependency installed, because what is
absent when the build is made is absent from the build.

The one-file form unpacks itself to a temporary folder each time it starts,
so it opens more slowly than the folder form and will not run where a
policy forbids running programs from the temporary folder. Where that is
the case, ship the folder.
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)

# Everything the program reads at run time. The spec carries these; this
# list is what tests/test_packaging.py checks the spec against.
SHIPPED = [
    "MANUAL.md", "SERVER_SETUP.md", "CHANGELOG.md", "README.md", "VERSION",
    os.path.join("docs", "AUDIT.md"),
    os.path.join("fme", "VERSION"),
    os.path.join("fme", "vendor", "vendored.json"),
]

# Imported by name at run time, so a freezer cannot see them.
BY_NAME = ["qnxprobe", "ewfprobe", "c2pareader", "exoprobe", "mediacarve",
           "impacket_ese"]

OPTIONAL = ["PIL", "cv2", "numpy", "flask", "waitress", "cheroot",
            "cryptography", "py7zr", "zstandard", "pytsk3", "pyewf",
            "pillow_heif", "reportlab", "texture2ddecoder", "liblzfse"]


def missing_files() -> list:
    """The files and vendored readers a build carries that are not here,
    reported as it goes. Shared with build_exe.py."""
    print(f"building from {PROJECT}\n")
    missing = []
    print("files the program reads at run time:")
    for relative in SHIPPED:
        path = os.path.join(PROJECT, relative)
        there = os.path.exists(path)
        if not there:
            missing.append(relative)
        print(f"  {'found  ' if there else 'MISSING'}  {relative}")

    print("\nvendored readers:")
    vendor = os.path.join(PROJECT, "fme", "vendor")
    for name in BY_NAME:
        path = os.path.join(vendor, f"{name}.py")
        there = os.path.isfile(path)
        if not there:
            missing.append(f"fme/vendor/{name}.py")
        licence = os.path.join(vendor, f"LICENSE-{name}")
        print(f"  {'found  ' if there else 'MISSING'}  {name}"
              f"{'' if os.path.isfile(licence) else '   (NO LICENCE FILE)'}")
        if not os.path.isfile(licence):
            missing.append(f"fme/vendor/LICENSE-{name}")
    return missing


def check() -> int:
    """What would go into a build from this machine, and what would not."""
    missing = missing_files()

    print("\noptional dependencies on this machine "
          "(absent here means absent from the build):")
    for module in OPTIONAL:
        try:
            __import__(module)
            print(f"  present  {module}")
        except ImportError:
            print(f"  absent   {module}")

    try:
        import PyInstaller  # noqa: F401
        print(f"\nPyInstaller: {PyInstaller.__version__}")
    except ImportError:
        print("\nPyInstaller is not installed: pip install pyinstaller")
        missing.append("PyInstaller")

    if missing:
        print(f"\nThe build cannot be made: missing {', '.join(missing)}.")
        return 1
    print("\nEverything the build needs is here.")
    return 0


def build(onefile: bool) -> int:
    if check() != 0:
        return 1
    for folder in ("build", "dist"):
        shutil.rmtree(os.path.join(PROJECT, folder), ignore_errors=True)
    environment = dict(os.environ, FME_ONEFILE="1" if onefile else "0")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm",
               "--clean", os.path.join(HERE, "fme.spec")]
    print("\n$ " + " ".join(command))
    finished = subprocess.run(command, cwd=PROJECT, env=environment)
    if finished.returncode != 0:
        return finished.returncode

    made = os.path.join(PROJECT, "dist")
    print(f"\nbuilt into {made}")
    print("\nBefore handing it to anybody, run it once and check:")
    print("    ForensicMediaExtractor doctor")
    print("  It reports what the build can read. Anything that says it is "
          "absent\n  was absent from the machine this was built on.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onefile", action="store_true",
                        help="one executable instead of a folder")
    parser.add_argument("--check", action="store_true",
                        help="say what would be bundled, build nothing")
    args = parser.parse_args(argv)
    return check() if args.check else build(args.onefile)


if __name__ == "__main__":
    sys.exit(main())
