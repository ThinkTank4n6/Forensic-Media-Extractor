"""Would a frozen build actually work?

PyInstaller cannot be run here, so this does not build anything. It checks
the two things that decide whether a build works, both of which are lists
somebody has to keep up to date and nobody remembers to:

* **files the program reads at run time** must be in the spec, or the build
  starts and then fails when somebody asks for the manual,
* **modules imported by name** must be named as hidden imports, or the
  build loses QNX, E01-without-libewf, Content Credentials and streamed
  video — silently, because each of those is written to carry on when its
  reader is absent.

Both failures look like a working program until the moment they don't,
which is why they are checked against the repository rather than trusted.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

from fme import resources

ok = True
spec_path = os.path.join(PROJECT, "packaging", "fme.spec")
build_path = os.path.join(PROJECT, "packaging", "build.py")

for path in (spec_path, build_path,
             os.path.join(PROJECT, "packaging", "entrypoint.py")):
    there = os.path.isfile(path)
    ok &= there
    print(f"  {'found' if there else 'MISSING'}  "
          f"{os.path.relpath(path, PROJECT)}")

with open(spec_path, encoding="utf-8") as fh:
    spec = fh.read()

print("\nfiles the program reads at run time are in the spec:")
# Everything resources.document() can be asked for, plus what the vendored
# readers need beside them.
needed = ["MANUAL.md", "SERVER_SETUP.md", "VERSION", "docs",
          "vendored.json", "LICENSE"]
# The FAT32 browser's templates went with it in v2026.11.319; a spec still
# naming them would fail the build on a folder that is not there.
gone = "templates" not in spec and "fme.web.app" not in spec
ok &= gone
print(f"  {'yes' if gone else 'NO'}   no trace of the retired FAT32 browser")
for name in needed:
    there = name in spec
    ok &= there
    print(f"  {'yes' if there else 'NO'}   {name}")

print("\nmodules imported by name are named as hidden imports:")
# Found from the code rather than from a list, so a reader added later is
# caught without anybody remembering this test exists.
imported_by_name = set()
for folder, _, names in os.walk(os.path.join(PROJECT, "fme")):
    if "vendor" in folder:
        continue
    for name in names:
        if not name.endswith(".py"):
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as fh:
            body = fh.read()
        if "VENDOR_DIR" not in body and "vendor_dir()" not in body:
            continue
        # Only modules that are actually vendored: an indented "import os"
        # in the same function is not a hidden import, and counting it made
        # this check cry wolf.
        # A trailing comment is allowed: "import qnxprobe  # noqa: ..." is
        # how three of the five are written, and requiring the line to end
        # at the name left exactly those three unchecked.
        for match in re.finditer(r"^\s+import (\w+)\s*(?:#.*)?$", body, re.M):
            name = match.group(1)
            if os.path.isfile(os.path.join(PROJECT, "fme", "vendor",
                                           f"{name}.py")):
                imported_by_name.add(name)

for name in sorted(imported_by_name):
    there = f'"{name}"' in spec
    ok &= there
    print(f"  {'yes' if there else 'NO'}   {name}")

if not imported_by_name:
    ok = False
    print("  found none, which means this check stopped working")

print("\nevery vendored reader ships its licence:")
vendor = os.path.join(PROJECT, "fme", "vendor")
for name in sorted(os.listdir(vendor)):
    if not name.endswith(".py") or name == "__init__.py":
        continue
    stem = name[:-3]
    licence = os.path.join(vendor, f"LICENSE-{stem}")
    there = os.path.isfile(licence)
    ok &= there
    print(f"  {'yes' if there else 'NO'}   {stem}")

print("\nshipped files are found through resources, not by walking __file__:")
# A frozen one-file build unpacks itself somewhere new every time it
# starts, so code that walks up from __file__ finds nothing there.
offenders = []
for folder, _, names in os.walk(os.path.join(PROJECT, "fme")):
    if "vendor" in folder:
        continue
    for name in names:
        if not name.endswith(".py") or name == "resources.py":
            continue
        path = os.path.join(folder, name)
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        for document in ("MANUAL.md", "SERVER_SETUP.md", "AUDIT.md"):
            if document in body and "resources" not in body:
                offenders.append(f"{os.path.relpath(path, PROJECT)} "
                                 f"opens {document} directly")
ok &= not offenders
print(f"  {offenders or 'yes'}")

print("\nresources finds them from this checkout:")
for name in ("MANUAL.md", "SERVER_SETUP.md", "AUDIT.md"):
    found = resources.document(name)
    ok &= bool(found)
    print(f"  {'yes' if found else 'NO'}   {name}")
found = resources.vendor_dir()
ok &= bool(found) and os.path.isdir(found)
print(f"  {'yes' if found else 'NO'}   the vendored readers")

# The interface is three files beside media_gui.py rather than a string
# inside it. A build that leaves them behind starts and serves a blank
# page -- the exact failure this file exists to catch, and the reason
# splitting the page had to be followed here.
print("\nthe interface's own files")
for part in ("page.html", "styles.css", "app.js"):
    path = os.path.join(PROJECT, "fme", "web", "ui", part)
    there = os.path.isfile(path)
    ok &= there
    print(f"  {part:12} {'present' if there else 'MISSING'}"
          f"{'' if not there else f' ({os.path.getsize(path):,} bytes)'}")

shipped = 'os.path.join(PROJECT, "fme", "web", "ui")' in spec
ok &= shipped
print(f"  and the spec ships the folder: {shipped}")

# Assembling it must not leave a placeholder behind: a page served with
# a literal {script} in it is a page with no program in it.
from fme.web import media_gui

whole = media_gui.PAGE
ok &= "{styles}" not in whole and "{script}" not in whole
ok &= "function renderRibbon" in whole and "ribbon-control" in whole
print(f"  the page assembles with no placeholder left: "
      f"{'{styles}' not in whole and '{script}' not in whole}")

# Each route module is imported at the end of media_gui, which is what
# registers its routes. A build that ships the file but never imports
# it starts, serves the page, and returns 404 on a third of the
# program.
print("\nthe route modules are reached")
with open(os.path.join(PROJECT, "fme", "web", "media_gui.py"),
          encoding="utf-8") as _fh:
    _core = _fh.read()
_modules = sorted(n[:-3] for n in os.listdir(
    os.path.join(PROJECT, "fme", "web")) if n.startswith("routes_"))
for _module in _modules:
    _imported = f"from fme.web import {_module}" in _core
    ok &= _imported
    print(f"  {_module:16} {'imported' if _imported else 'NEVER IMPORTED'}")

# And the count the program actually registers, which is the only
# number that proves the split did not lose anything.
from fme.web import media_gui as _gui

_registered = len(list(_gui.app.url_map.iter_rules()))
ok &= _registered >= 80
print(f"  {_registered} route(s) registered in total")

# A function defined twice is a function whose fixes have to be
# applied twice. media_core.py held twelve such pairs -- the whole
# parallel-carve machinery -- and two real defects came out of it: a
# worker initializer whose signature did not match its call, so every
# carve worker died and a run reported no carved files; and a
# progressive-write version of the carve that was shadowed and never
# ran.
# Nothing may assume a Unix path. An examiner runs this on Windows,
# where "/tmp/x" is not a temporary folder -- it is \tmp\x on whatever
# drive happens to be current, which may not be writable and is not
# where anyone would look for it.
print("\nno Unix-only paths")
_unix = []
for _folder, _dirs, _names in os.walk(PROJECT):
    if any(skip in _folder for skip in ("__pycache__", "vendor", ".git")):
        continue
    for _name in sorted(_names):
        if not _name.endswith(".py"):
            continue
        _path = os.path.join(_folder, _name)
        with open(_path, encoding="utf-8") as _fh:
            for _number, _line in enumerate(_fh, 1):
                if _name == "test_packaging.py":
                    continue            # this check names the pattern
                if "/tmp/" in _line and ("\"" in _line or "'" in _line):
                    if "screenshot is at" in _line:
                        continue        # a message, not a path it opens
                    _unix.append(f"{_name}:{_number}")
ok &= not _unix
print(f"   {'none' if not _unix else ', '.join(_unix[:6])}")

print("\nnothing is defined twice")
import ast as _dup_ast
import collections as _dup_count

_twice = []
for _folder, _dirs, _names in os.walk(os.path.join(PROJECT, "fme")):
    if "vendor" in _folder or "__pycache__" in _folder:
        continue
    for _name in sorted(_names):
        if not _name.endswith(".py"):
            continue
        _path = os.path.join(_folder, _name)
        with open(_path, encoding="utf-8") as _fh:
            _tree = _dup_ast.parse(_fh.read())
        _defs = [n.name for n in _tree.body
                 if isinstance(n, (_dup_ast.FunctionDef,
                                   _dup_ast.AsyncFunctionDef,
                                   _dup_ast.ClassDef))]
        for _dupe, _count in _dup_count.Counter(_defs).items():
            if _count > 1:
                _twice.append(f"{_name}:{_dupe}")
ok &= not _twice
print(f"   {'none' if not _twice else ', '.join(_twice[:6])}")

# Checking that a name appears in the spec is not checking that PyInstaller
# can find it. Every vendored reader was named as a hidden import, the
# check above passed, and none of them was on the build's path: PyInstaller
# warns about a hidden import it cannot find and builds without it.
print("\nthe build can find what it is told to bundle:")
import importlib.machinery as _machinery

_vendor = os.path.join(PROJECT, "fme", "vendor")
on_pathex = re.search(r"pathex=\[PROJECT, VENDOR\]", spec) is not None and \
    re.search(r'VENDOR = os\.path\.join\(PROJECT, "fme", "vendor"\)', spec) is not None
ok &= on_pathex
print(f"  fme/vendor is on the spec's pathex: {on_pathex}")
sys.path.insert(0, os.path.join(PROJECT, "packaging"))
import build as _build  # noqa: E402 - packaging/build.py

listed = set(_build.BY_NAME)
agree = listed == imported_by_name
ok &= agree
print(f"  packaging/build.py's list matches what the code imports by name: {agree}"
      + ("" if agree else f" (code {sorted(imported_by_name)}, list {sorted(listed)})"))
for name in sorted(imported_by_name | listed):
    found = _machinery.PathFinder.find_spec(name, [PROJECT, _vendor]) is not None
    ok &= found
    print(f"  {'yes' if found else 'NO'}   {name} resolves on [PROJECT, fme/vendor]")

# One spec. A second one, at the project root, was what build_exe.py and
# the build instructions used; it drifted from this one with nothing
# checking it, until a build made from it had no interface page, no
# vendored readers, no licences and no OpenCV.
print("\none spec, and the documented build uses it:")
stray = sorted(os.path.relpath(os.path.join(folder, name), PROJECT)
               for folder, dirs, names in os.walk(PROJECT)
               for name in names if name.endswith(".spec")
               if not any(part in ("build", "dist", ".git") for part in
                          os.path.relpath(folder, PROJECT).split(os.sep)))
single = stray == [os.path.join("packaging", "fme.spec")]
ok &= single
print(f"  the only spec is packaging/fme.spec: {single} ({stray})")
with open(os.path.join(PROJECT, "build_exe.py"), encoding="utf-8") as fh:
    driver = fh.read()
uses = 'os.path.join(HERE, "packaging", "fme.spec")' in driver
ok &= uses
print(f"  build_exe.py builds from it: {uses}")
for flag in ("numpy",):
    excluded = re.search(r"excludes=\[[^\]]*['\"]" + flag + r"['\"]", spec) is not None
    ok &= not excluded
    print(f"  {flag} is not excluded (OpenCV cannot import without it): {not excluded}")


# The frozen build starts in packaging/entrypoint.py, and nothing else runs
# that file. It once called fme.cli.main with an argument main does not take,
# so every packaged executable died with TypeError before drawing anything,
# while the suite, CI and launch.py all passed. Run it the way the freezer does:
# as a plain script, with a tool name after it.
import subprocess as _sp
print("\nthe frozen entry point starts the program:")
_entry = _sp.run([sys.executable, os.path.join(PROJECT, "packaging", "entrypoint.py"),
                  "version"], capture_output=True, text=True, cwd=PROJECT,
                 env=dict(os.environ, PYTHONPATH=PROJECT))
_entry_ok = _entry.returncode == 0 and "Traceback" not in _entry.stderr
ok &= _entry_ok
print(f"  entrypoint.py version exits {_entry.returncode}: {_entry_ok}"
      + ("" if _entry_ok else f"\n{_entry.stderr.strip()[-400:]}"))

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
