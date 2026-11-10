# -*- mode: python ; coding: utf-8 -*-
"""How this program is frozen into something that runs with nothing installed.

    python build_exe.py                  one file    -> dist/ForensicMediaExtractor.exe
    python build_exe.py --portable       one folder  -> dist/ForensicMediaExtractor/
    python packaging/build.py [--onefile]   the same spec, without the
                                            capability report

This is the only spec. The one at the project root, which build_exe.py and
the manual's build instructions used, carried none of the interface's page,
the vendored readers or their licences, and excluded NumPy while bundling
OpenCV, which cannot import without it; nothing tested it. It was removed in
v2026.11.275.

Two things decide whether a frozen build works, and neither is the code:

**Files the program reads at run time** have to be carried with it. The
manual, the setup guide, the comparison, the vendored readers' licences and
their record -- all of those are opened from disk, and a build without them
starts and then fails at the moment somebody asks for help.

**Modules imported by name rather than by statement** have to be named
here. The vendored readers are imported after their folder is put on the
path, which a freezer cannot see, so they are listed as hidden imports. A
build without them loses QNX, F2FS, E01 without libewf, Content Credentials
and streamed video -- silently, because each is written to carry on when
its reader is absent.

tests/test_packaging.py checks both lists against the repository, so a file
added later that nobody thought about is caught here rather than by an
examiner.
"""
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

PROJECT = os.path.abspath(os.path.join(os.getcwd()))

# Read at run time, so they travel with the program.
datas = [
    (os.path.join(PROJECT, "MANUAL.md"), "."),
    (os.path.join(PROJECT, "SERVER_SETUP.md"), "."),
    (os.path.join(PROJECT, "CHANGELOG.md"), "."),
    (os.path.join(PROJECT, "CLAUDE.md"), "."),
    (os.path.join(PROJECT, "BUILDING.md"), "."),
    (os.path.join(PROJECT, "README.md"), "."),
    (os.path.join(PROJECT, "VERSION"), "."),
    (os.path.join(PROJECT, "docs"), "docs"),
    (os.path.join(PROJECT, "fme", "VERSION"), "fme"),
    # The interface: markup, stylesheet and script, which were one string
    # inside media_gui.py until they were split out. A frozen build that
    # leaves them behind starts and then serves a blank page, which is
    # exactly the failure this spec's test exists to catch.
    (os.path.join(PROJECT, "fme", "web", "ui"), "fme/web/ui"),
    # The bundled image models and their licences (fme/engines/models.py).
    (os.path.join(PROJECT, "fme", "models"), "fme/models"),
    # The vendored readers' licences and the record of what they are. The
    # licences must travel with the code: that is the condition under which
    # it may be used at all.
    (os.path.join(PROJECT, "fme", "vendor", "vendored.json"), "fme/vendor"),
]

# The code fingerprint, taken here from the source being frozen. A frozen
# build's .py files are inside its archive, not on disk, so the fingerprint
# worked out at run time hashed only the page's files: every build with the
# same interface reported the same fingerprint, whatever its engine.
import sys as _sys

_sys.path.insert(0, PROJECT)
from fme.cli import fingerprint_of  # noqa: E402

_stamp = os.path.join(PROJECT, "build", "BUILD_FINGERPRINT")
os.makedirs(os.path.dirname(_stamp), exist_ok=True)
with open(_stamp, "w", encoding="ascii") as _fh:
    _fh.write(fingerprint_of(os.path.join(PROJECT, "fme")))
datas.append((_stamp, "fme"))
# What build_exe.py records about the machine and libraries it built with.
if os.path.isfile(os.path.join(PROJECT, "build_info.json")):
    datas.append((os.path.join(PROJECT, "build_info.json"), "."))
for name in os.listdir(os.path.join(PROJECT, "fme", "vendor")):
    if name.startswith("LICENSE"):
        datas.append((os.path.join(PROJECT, "fme", "vendor", name),
                      "fme/vendor"))

# The vendored readers live in fme/vendor/, which is not a package (no
# __init__.py) and not on the build's path, so naming them as hidden imports
# was not enough: PyInstaller looks for a hidden import on sys.path and
# pathex, did not find these, warned, and built without them. A build that
# cannot find mediacarve will not carve. VENDOR goes on pathex below, and
# tests/test_packaging.py checks that every name here resolves there.
VENDOR = os.path.join(PROJECT, "fme", "vendor")

# Imported by name at run time, which a freezer cannot see.
hiddenimports = [
    "qnxprobe", "ewfprobe", "c2pareader", "exoprobe", "mediacarve", "impacket_ese",
    "fme.cli", "fme.web.media_gui", "fme.web.manual",
    "fme.review.hashserver", "fme.review.recover",
]
hiddenimports += collect_submodules("fme")

# Optional at run time: present if installed when the build is made, and
# everything that uses them already copes with their absence.
for module in ("PIL", "cv2", "numpy", "flask", "waitress", "cheroot",
               "cryptography", "py7zr", "zstandard", "pytsk3", "pyewf",
               "pillow_heif", "reportlab", "texture2ddecoder", "liblzfse"):
    try:
        datas += collect_data_files(module)
        hiddenimports.append(module)
    except Exception as exc:  # noqa: BLE001 - absent is fine
        print(f"[fme.spec] not bundling {module}: {exc}")

# The licenses. The program's own, the notices, the license texts they cite,
# and every bundled package's own license files: The Sleuth Kit's license
# (inside pytsk3) and the LGPL and GPL components (libewf, pillow-heif's
# libraries) allow the build to be handed on only with these in it.
_sys_path_saved = list(__import__("sys").path)
__import__("sys").path.insert(0, os.path.join(PROJECT, "packaging"))
import notices as _notices  # noqa: E402

datas += _notices.project_files(PROJECT)
datas += _notices.package_license_files(
    [m for m in hiddenimports if "." not in m] +
    ["werkzeug", "jinja2", "markupsafe", "itsdangerous", "click", "blinker",
     "Crypto", "pyppmd", "pybcj", "pyzstd", "multivolumefile", "inflate64",
     "brotli", "texttable"])
__import__("sys").path[:] = _sys_path_saved

# pycryptodome opens BitLocker and FileVault volumes with an examiner's key.
# Its ciphers are compiled modules loaded by name, so they are collected
# explicitly. Absent, the build still works -- and refuses every key at the
# start of a run -- so it is said loudly here rather than found on a case.
try:
    hiddenimports += collect_submodules("Crypto.Cipher") + collect_submodules("Crypto.Util")
    datas += collect_data_files("Crypto")
    hiddenimports.append("Crypto")
except Exception as exc:  # noqa: BLE001
    print("[fme.spec] WARNING: pycryptodome is not installed, so this build "
          f"cannot open BitLocker or FileVault volumes with a key ({exc}). "
          "pip install pycryptodome and build again.")

analysis = Analysis(
    [os.path.join(PROJECT, "packaging", "entrypoint.py")],
    pathex=[PROJECT, VENDOR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=["tkinter", "matplotlib", "pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

ONEFILE = os.environ.get("FME_ONEFILE") == "1"
NAME = "ForensicMediaExtractor"

if ONEFILE:
    exe = EXE(pyz, analysis.scripts, analysis.binaries, analysis.datas, [],
              name=NAME, console=True, upx=False, strip=False,
              icon=None)
else:
    exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name=NAME,
              console=True, upx=False, strip=False, icon=None)
    COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False,
            name=NAME)
