"""The folder picker can make a folder (v2026.11.335).

Choosing a results folder meant leaving the program to make one: the picker
could open folders and choose one, never create one. It now has New folder,
which makes one folder, by name, inside the folder it is showing, and opens
it so Use this folder takes it.

The route first -- what it refuses -- then the whole thing in Chromium, from
the results folder's Browse button to the field holding the new folder.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

ROOT = os.path.join(tempfile.gettempdir(), "fme_new_folder-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(os.path.join(ROOT, "results"))
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


from fme.web import media_gui as gui  # noqa: E402

client = gui.app.test_client()
H = {"X-FME-Request": "1"}
parent = os.path.join(ROOT, "results")


def mkdir(name, where=parent, headers=H):
    return client.post("/api/browse/mkdir", headers=headers,
                       json={"parent": where, "name": name})


print("The route")
made = mkdir("DC26-0412")
check("makes the folder it is asked for",
      made.status_code == 200 and os.path.isdir(os.path.join(parent, "DC26-0412")),
      made.get_json())
check("and says where", made.get_json().get("path") == os.path.join(parent, "DC26-0412"))
again = mkdir("DC26-0412")
check("refuses one that is already there", again.status_code >= 400,
      again.get_json().get("error", "")[:80])
for bad, why in [("", "no name"), ("..", "the folder above"), ("a/b", "a path"),
                 ("a\\b", "a Windows path"), ('x:y', "a colon"), ("CON", "a reserved name"),
                 ("trailing.", "a trailing period"), ("tab\there", "a control character")]:
    answer = mkdir(bad)
    check(f"refuses {why}", answer.status_code >= 400
          and sorted(os.listdir(parent)) == ["DC26-0412"],
          answer.get_json().get("error", "")[:70])
check("refuses a parent that is not there",
      mkdir("x", where=os.path.join(ROOT, "nowhere")).status_code >= 400)
check("refuses a request that did not come from the page",
      mkdir("unsent", headers={}).status_code >= 400
      and not os.path.exists(os.path.join(parent, "unsent")))
check("nothing but the one folder was made",
      sorted(os.listdir(parent)) == ["DC26-0412"], sorted(os.listdir(parent)))

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("(the page is not checked: playwright is not installed)")
    shutil.rmtree(ROOT, ignore_errors=True)
    sys.exit(0 if ok else 1)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
server = subprocess.Popen(
    [sys.executable, "-m", "fme", "gui", "--port", str(PORT), "--no-browser"],
    cwd=PROJECT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2)
            break
        except OSError:
            time.sleep(0.2)
    print("The page")
    with sync_playwright() as play:
        try:
            browser = play.chromium.launch()
        except Exception as exc:                           # noqa: BLE001
            print(f"Chromium will not launch here: {str(exc)[:120]}")
            sys.exit(0 if ok else 1)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        thrown = []
        page.on("pageerror", lambda error: thrown.append(str(error)))
        page.on("dialog", lambda d: (thrown.append("a browser dialog opened"), d.dismiss()))
        page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")

        page.evaluate("() => showTab('review')")        # the extraction screen
        page.wait_for_selector("#out-root", state="visible", timeout=10000)
        page.fill("#out-root", parent)
        page.click("#btn-out-browse")
        # The picker opens on the folder holding the current one; into it.
        page.wait_for_function("(p) => document.getElementById('browse-path').value === p",
                               arg=ROOT, timeout=10000)
        page.click(f"#browse-list .entry[data-path='{parent}']")
        page.wait_for_function("(p) => document.getElementById('browse-path').value === p",
                               arg=parent, timeout=10000)
        check("the picker has a New folder button",
              page.is_visible("#browse-newdir") and page.is_enabled("#browse-newdir"))
        page.click("#browse-newdir")
        page.fill("#browse-newname", "Bad/Name")
        page.click("#browse-newdir-go")
        page.wait_for_function(
            "() => document.getElementById('browse-newdir-err').textContent !== ''", timeout=10000)
        check("a name it cannot use is said in the picker, not a pop-up",
              "cannot contain" in page.inner_text("#browse-newdir-err"),
              page.inner_text("#browse-newdir-err"))
        page.fill("#browse-newname", "DC26-0413 phone")
        page.press("#browse-newname", "Enter")
        made = os.path.join(parent, "DC26-0413 phone")
        page.wait_for_function("(p) => document.getElementById('browse-path').value === p",
                               arg=made, timeout=10000)
        check("Enter makes it, and the picker opens it", os.path.isdir(made))
        check("the name box closes again",
              not page.is_visible("#browse-newdir-row"))
        page.click("#browse-pick-dir")
        page.wait_for_function("(p) => document.getElementById('out-root').value === p",
                               arg=made, timeout=10000)
        check("Use this folder makes it the results folder",
              page.input_value("#out-root") == made, page.input_value("#out-root"))

        page.click("#btn-out-browse")
        # Opened again, the picker shows the folder the results folder is in:
        # wait for that listing, not for whatever the last one left there.
        page.wait_for_function("(p) => document.getElementById('browse-path').value === p",
                               arg=parent, timeout=10000)
        # Up to the top, one folder at a time, each step finished before the
        # next: on Windows the top is the list of drives, elsewhere "/".
        for _ in range(64):
            before = page.input_value("#browse-path")
            if before in ("Drives", "/") or page.is_disabled("#browse-up"):
                break
            page.click("#browse-up")
            page.wait_for_function("(p) => document.getElementById('browse-path').value !== p",
                                   arg=before, timeout=10000)
        if page.input_value("#browse-path") == "Drives":
            check("no New folder in the list of drives", page.is_disabled("#browse-newdir"))
        # Two listings in flight: the first held back, the second asked for
        # after it. The picker must end on the second, the one asked last.
        held = []
        page.route("**/api/browse?path=*results", lambda route: held.append(route))
        # Started, not awaited: loadDir waits on the very answer held here.
        page.evaluate("(p) => { loadDir(p); }", parent)
        for _ in range(50):
            if held:
                break
            page.wait_for_timeout(50)
        page.evaluate("(p) => { loadDir(p); }", made)
        page.wait_for_function("(p) => document.getElementById('browse-path').value === p",
                               arg=made, timeout=10000)
        for route in list(held):
            route.continue_()
        page.unroute("**/api/browse?path=*results")
        page.wait_for_timeout(800)
        check("a listing that arrives late does not take the picker back",
              bool(held) and page.input_value("#browse-path") == made,
              page.input_value("#browse-path"))
        check("nothing the page did threw", not thrown, "; ".join(thrown[:2]))
        browser.close()
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
    shutil.rmtree(ROOT, ignore_errors=True)

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
