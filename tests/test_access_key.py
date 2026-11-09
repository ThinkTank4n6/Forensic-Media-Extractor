"""A GUI other machines can reach answers only with this run's access key.

Started with --host set to anything but loopback, the GUI served the
evidence it shows and started runs for anyone who could reach the port:
there was no sign-in at all. It now makes a random key at start-up,
prints an address carrying it, and refuses every request without it.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

from fme.web import media_gui, request_guard

ok = True
app = media_gui.app
client = app.test_client()


def check(label, passed):
    global ok
    ok &= passed
    print(f"   {label}: {passed}")


print("bound to loopback, as by default")
check("no key is asked for", client.get("/api/health").status_code != 403)

print("\nreachable from other machines")
key = request_guard.require_key(app)
check("a request without the key is refused", client.get("/api/health").status_code == 403)
check("so is one with the wrong key",
      client.get("/api/health?key=wrong").status_code == 403)
first = client.get(f"/?key={key}")
check("the printed address is answered", first.status_code == 200)
cookie = first.headers.get("Set-Cookie", "")
check("and leaves the key as a strict, HttpOnly cookie",
      "fme_key=" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie)
check("so the page then works without it in the address",
      client.get("/api/health").status_code != 403)
fresh = app.test_client()
check("a client that never had the key is still refused",
      fresh.get("/api/health").status_code == 403)
check("the X-FME-Key header also carries it",
      fresh.get("/api/health", headers={"X-FME-Key": key}).status_code != 403)
app.config.pop("FME_ACCESS_KEY", None)

print("\nstarted from the command line with --host 0.0.0.0")
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
server = subprocess.Popen([sys.executable, "-m", "fme", "gui", "--host", "0.0.0.0",
                           "--port", str(port), "--no-browser"], cwd=PROJECT,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
printed, url = "", None
started = time.time()
while time.time() - started < 60 and not url:
    line = server.stdout.readline()
    printed += line
    found = re.search(r"http://127\.0\.0\.1:\d+/\?key=[\w-]+", line)
    url = found.group(0) if found else None
check("it prints a localhost address carrying a key", bool(url))
try:
    if url:
        time.sleep(1.5)
        base = url.split("/?")[0]
        try:
            urllib.request.urlopen(base + "/api/health", timeout=10)
            refused = False
        except urllib.error.HTTPError as exc:
            refused = exc.code == 403
        check("over the network, a request without the key is refused", refused)
        with urllib.request.urlopen(url, timeout=10) as answer:
            check("and the printed address is answered", answer.status == 200)
finally:
    server.terminate()
    server.wait(timeout=20)

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
