"""Another site cannot use an administrator's sign-in on the hash store.

The admin pages are signed in by a cookie, and SameSite=Lax was all that
stood between them and a form on another site. Same-site is not
same-origin: any other web application on the store's host, on any port,
could post to /admin/people with the administrator's cookie and add a
person or re-issue a token. A post whose Origin names another site is now
refused, and the cookie is SameSite=Strict.
"""
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.review import hashserver  # noqa: E402


def main() -> int:
    ok = True
    root = os.path.join(tempfile.gettempdir(), "fme_hashserver_forms-") + str(os.getpid())
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    app = hashserver.build_app(os.path.join(root, "store.db"),
                               os.path.join(root, "users.db"))
    accounts = app.config["ACCOUNTS"]
    token = accounts.add("Admin", "admin")
    client = app.test_client()
    signed = client.post("/admin", data={"token": token},
                         headers={"Origin": "http://localhost"})
    cookie = signed.headers.get("Set-Cookie", "")
    strict = "SameSite=Strict" in cookie
    ok &= strict
    print(f"signing in sets a SameSite=Strict cookie: {strict}")

    form = {"action": "add", "name": "Mallory", "role": "admin"}
    forged = client.post("/admin/people", data=form,
                         headers={"Origin": "http://intranet-app.local:8080"})
    refused = forged.status_code == 403 and \
        "Mallory" not in {u["name"] for u in accounts.users()}
    ok &= refused
    print(f"a form posted from another site is refused, and adds nobody: {refused}")

    real = client.post("/admin/people", data={**form, "name": "Dana"},
                       headers={"Origin": "http://localhost"})
    works = real.status_code == 200 and "Dana" in {u["name"] for u in accounts.users()}
    ok &= works
    print(f"the same form from the store's own page still works: {works}")

    app.config["STORE"].close()
    accounts.close()
    shutil.rmtree(root, ignore_errors=True)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
