"""The program's license, and the notices it owes, travel with it (v2026.11.321).

FME is MIT. What it includes or bundles is not: The Sleuth Kit (through
pytsk3) is under the IBM Public License 1.0 and the Common Public License
1.0, which allow a binary to be handed on only with a warranty disclaimer on
behalf of every contributor, a statement of where the source is, and IBM's
copyright notice somewhere conspicuous; libewf and pillow-heif's codecs are
LGPL and GPL. A build without these notices is a build nobody may be given.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import contextlib
import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, "packaging"))
ok = True
IBM = ("Copyright (c) 1997,1998,1999, International Business Machines")


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


def read(*parts):
    with open(os.path.join(PROJECT, *parts), encoding="utf-8") as fh:
        return fh.read()


print("this program's license")
mit = read("LICENSE")
check("LICENSE is the MIT license, with its copyright line",
      mit.startswith("MIT License")
      and "Copyright (c) 2026 Christopher A. Tankelewicz (ThinkTank4n6)" in mit
      and 'PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND' in mit)
project = read("pyproject.toml")
check("pyproject says MIT, not Proprietary",
      'license = {text = "MIT"}' in project and "Proprietary" not in project)
check("the README says so, and that there is no warranty",
      "[MIT License](LICENSE)" in read("README.md") and "No warranty" in read("README.md"))

print("\nwhat The Sleuth Kit's license asks of a binary")
notices = read("THIRD_PARTY_NOTICES.md")
flat = re.sub(r"\s+", " ", notices)
check("IBM's copyright notice", IBM in notices)
check("a disclaimer of all warranties on behalf of all contributors",
      "disclaims, on behalf of all contributors, all warranties" in flat)
check("an exclusion of liability on behalf of all contributors",
      "excludes, on behalf of all contributors, all liability" in flat)
check("differing provisions are this author's alone",
      "offered by the author of this program alone" in flat)
check("where the source can be had", "https://github.com/sleuthkit/sleuthkit" in notices)
texts = os.listdir(os.path.join(PROJECT, "packaging", "licenses"))
check("the license texts it cites are present", {
    "IBM-PL-1.0.txt", "CPL-1.0.txt", "Apache-2.0.txt", "LGPL-3.0.txt",
    "GPL-3.0.txt", "GPL-2.0.txt", "LGPL-2.1.txt"} <= set(texts), sorted(texts))
ibm = read("packaging", "licenses", "IBM-PL-1.0.txt")
cpl = read("packaging", "licenses", "CPL-1.0.txt")
check("and are the licenses, whole",
      ibm.startswith("IBM PUBLIC LICENSE VERSION 1.0") and "jury trial" in ibm
      and cpl.startswith("Common Public License Version 1.0") and "jury trial" in cpl)
check("pillow-heif's GPL-2.0 codec is named", "x265" in notices and "GPL-2.0" in notices)
for cited in re.findall(r"`(licenses/[\w.-]+\.txt)`", notices):
    check(f"{cited} exists",
          os.path.isfile(os.path.join(PROJECT, "packaging", cited)))

print("\nthe standalone build carries them")
import notices as builder  # noqa: E402
pairs = builder.project_files(PROJECT)
check("LICENSE, the notices and every text go in",
      {os.path.basename(s) for s, _ in pairs} >= {"LICENSE", "THIRD_PARTY_NOTICES.md",
                                                  "IBM-PL-1.0.txt", "CPL-1.0.txt"})
spec = read("packaging", "fme.spec")
check("the spec adds them, and every bundled package's own",
      "_notices.project_files(PROJECT)" in spec
      and "_notices.package_license_files(" in spec)
found = builder.package_license_files(["flask"])
check("a bundled package's license file is found from its metadata",
      any(dest.startswith("licenses/python/") for _, dest in found), found[:1])
dests = [(d, os.path.basename(s)) for s, d in builder.package_license_files(["numpy"])]
check("no two license files land on the same name", len(dests) == len(set(dests)))

print("\nthe program says it")
out = subprocess.run([sys.executable, "-m", "fme", "version"], cwd=PROJECT,
                     capture_output=True, text=True, timeout=120).stdout
check("fme version shows the MIT notice and IBM's",
      "MIT License" in out and "1997,1998,1999, International" in out)
with contextlib.redirect_stdout(io.StringIO()):
    from fme.web import media_gui as gui  # noqa: E402
page = gui.app.test_client().get("/doc/licenses")
check("File → Licenses opens the notices", page.status_code == 200
      and b"International Business Machines" in page.data)
script = read("fme", "web", "ui", "app.js")
check("and the ribbon has it", '"/doc/licenses"' in script)

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
