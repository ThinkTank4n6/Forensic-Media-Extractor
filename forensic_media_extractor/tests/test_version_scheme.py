"""Releases are numbered Year.Month.Build, and compare by the build.

The build counter carries on from the single integers used up to v270, so it
is what orders releases. A version floor that read the first number would
have taken a year-first release as build 2026 and let every one through.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
import fme
from fme import build_number
from fme.engines import media_core

ok = True


def check(label, passed):
    global ok
    ok &= bool(passed)
    print(f"   {label}: {bool(passed)}")


print("the release number")
texts = {name: open(os.path.join(PROJECT, name), encoding="utf-8").read().strip()
         for name in ("VERSION", os.path.join("fme", "VERSION"))}
form = re.fullmatch(r"(\d{4})\.(1[0-2]|[1-9])\.(\d+)", texts["VERSION"])
check(f"VERSION is Year.Month.Build ({texts['VERSION']})", form)
check("fme/VERSION says the same", len(set(texts.values())) == 1)
check("and so does pyproject.toml", f'version = "{texts["VERSION"]}"'
      in open(os.path.join(PROJECT, "pyproject.toml"), encoding="utf-8").read())
check("the program reports it with a v", fme.RELEASE == media_core.RELEASE == "v" + texts["VERSION"])
check("its build carries on from v270", form and int(form.group(3)) > 270)
newest = re.search(r"^## v(\S+)", open(os.path.join(PROJECT, "CHANGELOG.md"), encoding="utf-8").read(), re.M)
check("the newest CHANGELOG entry is this release", newest and newest.group(1) == texts["VERSION"])

print("\ncomparing releases")
check("2026.11.271 is build 271", build_number("2026.11.271") == 271)
check("so is v2026.11.271", build_number("v2026.11.271") == 271)
check("the older 270 is build 270", build_number("270") == 270)
check("nonsense is 0", build_number("unknown") == 0)

print("\nthe hash store's version floor")
from fme.review import hashserver
root = tempfile.mkdtemp(prefix="fme_version_floor-")
accounts_path = os.path.join(root, "users.db")
token = hashserver.Accounts(accounts_path).add("jsmith", "contribute")
for floor, client, allowed in (("2026.11.271", "2026.11.271", True),
                               ("2026.11.271", "2026.11.270", False),
                               ("2026.11.271", "270", False),
                               ("271", "2026.12.272", True),
                               ("300", "2026.11.271", False)):
    app = hashserver.build_app(os.path.join(root, "store.db"), accounts_path,
                               min_version=floor)
    answer = app.test_client().post(
        "/api/v1/contribute", data=json.dumps({"case": "c", "hashes": []}),
        headers={"Authorization": f"Bearer {token}", "X-FME-Version": client,
                 "Content-Type": "application/json"})
    got = answer.status_code != 426
    check(f"floor {floor}, client {client}: {'accepted' if allowed else 'refused'}",
          got == allowed)
shutil.rmtree(root, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
