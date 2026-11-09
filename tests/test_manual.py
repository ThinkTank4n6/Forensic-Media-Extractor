"""The manual has to stay true as the program changes.

A manual that drifts is worse than none: it is confidently wrong. These
checks read the claims the manual makes about keys, categories, flags, file
names and command-line tools, and compare them with the code.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fme import cli
from fme.review import casedb
from fme.web import manual

ok = True
path = manual.manual_path()
print(f"manual: {path}")
ok &= bool(path)
with open(path, encoding="utf-8") as fh:
    text = fh.read()

page = manual.render("vtest")
ok &= "<h1" in page and "<table>" in page and "<pre><code>" in page
print(f"renders to {len(page):,} bytes of HTML")

print("\ncategories the manual lists")
listed = dict(re.findall(r"^\| `(.)` \| (\d+) \|", text, re.M))
for key, code in sorted(listed.items(), key=lambda kv: int(kv[1])):
    match = next((c for c in casedb.CATEGORIES if c[1] == key), None)
    good = match is not None and match[0] == int(code)
    ok &= good
    print(f"  key {key} -> code {code:>2}  {'' if good else '<-- not what the code does'}")
ok &= len(listed) == len(casedb.CATEGORIES)
print(f"  {len(listed)} listed, {len(casedb.CATEGORIES)} in the code")

# The manual states this plainly because getting it wrong turns every
# untouched file into a finding.
claims_eight = "Uncategorized is 8, not 0" in text
ok &= claims_eight and casedb.UNCATEGORIZED == 8
print(f"\nsays uncategorized is 8, and it is: {claims_eight and casedb.UNCATEGORIZED == 8}")

print("\nflags the manual names")
for name, _, key in casedb.DEFAULT_FLAGS:
    named = name in text
    ok &= named
    print(f"  {name:20} {'named' if named else 'MISSING FROM THE MANUAL'}"
          f"{f' (key {key})' if key else ''}")

print("\ncommand-line tools the manual shows")
shown = set(re.findall(r"python launch\.py (\w+)", text))
for tool in sorted(shown):
    known = tool in cli.TOOLS
    ok &= known
    print(f"  {tool:12} {'exists' if known else 'NO SUCH TOOL'}")

print("\nfiles the manual says a run produces")
import fme.engines.media_core as media_core
# The engine and the modules split out of it in v2026.11.314.
_engine_dir = os.path.dirname(media_core.__file__)
source = "".join(open(os.path.join(_engine_dir, f), encoding="utf-8").read()
                 for f in ("media_core.py", "output.py", "carving.py", "sources.py",
                           "fs_walk.py", "deleted_recovery.py"))
for name in ("manifest.csv", "manifest.json", "duplicates.csv",
             "WHICH_FILE_IS_WHICH.txt", "locations.kml"):
    written = name in source
    ok &= written
    print(f"  {name:26} {'written by the engine' if written else 'NOT WRITTEN'}")

# The setup guide tells a non-technical person exactly what to type. Every
# command in it is checked against what the program actually accepts: a
# guide with a wrong flag in it wastes an afternoon and teaches someone that
# the documentation lies.
print("\nthe server setup guide")
setup_path = manual.document_path("SERVER_SETUP.md")
ok &= bool(setup_path)
with open(setup_path, encoding="utf-8") as fh:
    setup = fh.read()
print(f"  {setup_path}")

rendered = manual.render("vtest", "SERVER_SETUP.md")
ok &= "<h1" in rendered and "<table>" in rendered
print(f"  renders to {len(rendered):,} bytes")

import argparse as _argparse
import re as _re
import contextlib as _contextlib
import io as _io
import re as _re
import shlex as _shlex

from fme.review import hashserver as _hashserver

flags_used = set()
for line in setup.splitlines():
    line = line.strip()
    if "launch.py hashserver" not in line:
        continue
    try:
        parts = _shlex.split(line)
    except ValueError:
        continue
    tail = parts[parts.index("hashserver") + 1:]
    flags_used.update(p for p in tail if p.startswith("--"))

# Ask the parser itself what it accepts, rather than keeping a second list.
accepted = set()
parser_text = _io.StringIO()
with _contextlib.redirect_stdout(parser_text):
    with _contextlib.suppress(SystemExit):
        _hashserver.main(["--help"])
accepted = set(_re.findall(r"(--[a-z][a-z-]+)", parser_text.getvalue()))

unknown = sorted(flags_used - accepted)
ok &= not unknown
print(f"  hashserver options used in the guide: {len(flags_used)}")
print(f"  options the program does not accept : {unknown or 'none'}")

for needed in ("--make-cert", "--add-user", "--role", "--cert", "--key",
               "--host", "--port"):
    present = needed in setup
    ok &= present
    print(f"    {needed:14} {'shown' if present else 'MISSING FROM THE GUIDE'}")

# And the roles it describes have to be the roles that exist.
for role in _hashserver.ROLES:
    ok &= role in setup
print(f"  the roles it explains: "
      f"{', '.join(r for r in _hashserver.ROLES if r in setup)}")

# Recovery is what someone reaches for after grading the wrong files, and
# they will be looking for it in a hurry. It needs its own section, and the
# commands in it have to be ones the program accepts.
print("\nthe recovery section")
with open(manual.document_path("MANUAL.md"), encoding="utf-8") as fh:
    body = fh.read()

has_section = "## 7. Undoing a mistake" in body
ok &= has_section
print(f"  it has a section of its own: {has_section}")

from fme.review import recover as _recover

recover_flags = set(_re.findall(r"recover (--[a-z-]+)", body))
accepted = _io.StringIO()
with _contextlib.redirect_stdout(accepted):
    with _contextlib.suppress(SystemExit):
        _recover.main(["--help"])
known_flags = set(_re.findall(r"(--[a-z][a-z-]+)", accepted.getvalue()))
unknown = sorted(recover_flags - known_flags)
ok &= not unknown
print(f"  options shown: {', '.join(sorted(recover_flags))}")
print(f"  options the program does not accept: {unknown or 'none'}")

# And the distinction that caused the confusion has to be spelled out.
explains = "cases.db" in body and "case.db" in body and "keystroke apart" in body
ok &= explains
print(f"  it explains cases.db against case.db: {explains}")

# Documents an examiner may attach to a case should not name another tool.
# The README's acknowledgments and the license notices are the exception
# (v2026.11.321): credit for the code this program takes from GLEAPP is owed
# by name there, and nowhere else.
print("\nwhat the documents name")
import glob as _glob

CREDITS = {"README.md", "THIRD_PARTY_NOTICES.md"}
ours = []
_project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (_glob.glob(os.path.join(_project, "*.md"))
             + _glob.glob(os.path.join(_project, "docs", "*.md"))):
    if os.path.basename(path) in CREDITS:
        continue
    with open(path, encoding="utf-8") as fh:
        if "GLEAPP" in fh.read():
            ours.append(os.path.basename(path))
with open(os.path.join(_project, "README.md"), encoding="utf-8") as fh:
    _readme = fh.read()
_named = [line for line in _readme.splitlines() if "GLEAPP" in line]
_ack = _readme.find("## Acknowledgments")
_in_ack = _ack >= 0 and all(
    _readme.find(line) > _ack for line in _named)
ok &= _in_ack
print(f"  the README names it only in its acknowledgments: {_in_ack}")
ok &= not ours
print(f"  documents naming another tool: {ours or 'none'}")

# The vendored reader is third-party code whose hash is checked; whatever
# it says in its own comments stays as its author wrote it.
print("  (the vendored reader is left exactly as published, as its licence "
      "requires)")

# The validation document is written to be disclosed, so two things
# about it are checked rather than trusted: that it states what has not
# been tested, and that the limitations are not quietly shorter than the
# claims. A document whose limitations section withers while the
# capability list grows is the failure mode here.
print("\nthe validation document")
_validation = os.path.join(ROOT_DIR, "docs", "VALIDATION.md") \
    if "ROOT_DIR" in dir() else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "docs", "VALIDATION.md")
if not os.path.isfile(_validation):
    ok = False
    print("   MISSING -- docs/VALIDATION.md")
else:
    with open(_validation, encoding="utf-8") as _fh:
        _text = _fh.read()
    # Wrapped prose: a phrase can be split across a line break, so the
    # check reads the words rather than the layout.
    _flat = " ".join(_text.split())
    for _wanted in ("has not been tested", "No error rate has been "
                    "established", "not an independent assessment"):
        _there = _wanted.lower() in _flat.lower()
        ok &= _there
        print(f"   says \"{_wanted[:38]}\": {'yes' if _there else 'NO'}")

    _limits = _text[_text.index("## 5. Known limitations"):]
    _claims = _text[_text.index("## 2. What it does"):
                    _text.index("## 5. Known limitations")]
    ok &= len(_limits) >= len(_claims)
    print(f"   the limitations are not shorter than the claims: "
          f"{len(_limits):,} vs {len(_claims):,} characters")

# The handbook is read by whoever works on this next, usually a session
# with no memory of the last one. A path it names that does not exist
# sends them looking for something that was moved or never made.
print("\nthe engineering handbook")
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_handbook = os.path.join(_root, "CLAUDE.md")
if not os.path.isfile(_handbook):
    ok = False
    print("   MISSING -- CLAUDE.md")
else:
    with open(_handbook, encoding="utf-8") as _fh:
        _text = _fh.read()
    _named = re.findall(
        r"`(fme/[\w/.]+|tools/[\w.]+|tests/[\w.]+|docs/[\w.]+)`", _text)
    _gone = sorted({p for p in set(_named)
                    if not os.path.exists(os.path.join(_root, p))})
    ok &= not _gone
    print(f"   {len(set(_named))} path(s) named; missing: "
          f"{', '.join(_gone) if _gone else 'none'}")

    # The gotchas are the part worth having. If that section withers,
    # the handbook has become a description instead of a warning.
    _gotchas = _text[_text.index("## 5. Gotchas"):]
    ok &= len(_gotchas) > 1500
    print(f"   the gotchas section is {len(_gotchas):,} characters")

# A procedure is only useful if it is there before it is needed. The
# store's recovery steps were printed by the check and written nowhere,
# so the first time anybody read them was the moment the store was
# already damaged.
print("\nthe store's recovery procedure")
_setup = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "SERVER_SETUP.md")
with open(_setup, encoding="utf-8") as _fh:
    _guide = _fh.read()
for _wanted in ("Checking it for damage", "When it is damaged",
                "--integrity", "Re-import the sets",
                "Re-contribute the cases"):
    _there = _wanted in _guide
    ok &= _there
    print(f"   the setup guide covers \"{_wanted}\": "
          f"{'yes' if _there else 'NO'}")

# And the examiner's manual has to point at it: an examiner meets the
# symptom, an admin runs the fix.
_manual_text = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "MANUAL.md"), encoding="utf-8").read()
_pointed = "--integrity" in _manual_text and "SERVER_SETUP" in _manual_text
ok &= _pointed
print(f"   and the manual points an examiner at it: "
      f"{'yes' if _pointed else 'NO'}")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
