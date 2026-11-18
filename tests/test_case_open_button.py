"""Each case in the list has an Open button, and opening shows its progress.

Reported against v2026.11.330: opening a case was hard -- the whole row was
the only thing to click, and a large case gave nothing on screen for ten
seconds and more, so it looked as if the click had not landed. Drives the
page in Chromium: the button is there, the row shows a bar and what is being
done while the case opens, a second click does not start a second open, and
the case ends up open on the review tab.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import json
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

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("(not checked: playwright is not installed)")
    sys.exit(0)

ROOT = os.path.join(tempfile.gettempdir(), "fme_case_open_button-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


N = 150000
case_dir = os.path.join(ROOT, "cases", "DC26-0001")
os.makedirs(case_dir)
with open(os.path.join(case_dir, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"summary": {"image": "phone.zip", "case_number": "DC26-0001"},
               "files": [{"out_path": f"images/jpg/f{i:06d}.jpg", "ext": "jpg",
                          "category": "image", "engine": "package", "size": 1000 + i,
                          "deleted": False, "sha256": f"{i:064x}", "md5": f"{i:032x}",
                          "sha1": f"{i:040x}", "source_path": f"/DCIM/IMG_{i:06d}.JPG",
                          "offset": None, "fs_type": "zip", "note": "entry " * 12,
                          "mtime": "2024-05-01T10:00:00+00:00", "captured": ""}
                         for i in range(N)]}, fh)

PORT = free_port()
server = subprocess.Popen(
    [sys.executable, "-m", "fme", "gui", "--port", str(PORT), "--no-browser"],
    cwd=PROJECT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def api(path, payload=None):
    url = f"http://127.0.0.1:{PORT}{path}"
    if payload is None:
        with urllib.request.urlopen(url, timeout=120) as r:
            return json.loads(r.read())
    req = urllib.request.Request(url, method="POST", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "X-FME-Request": "1"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


try:
    for _ in range(150):
        try:
            api("/api/capabilities")
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    api("/api/cases/scan", {"path": os.path.join(ROOT, "cases")})

    with sync_playwright() as play:
        try:
            browser = play.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            print(f"Chromium will not launch here: {str(exc)[:120]}")
            sys.exit(0)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        thrown = []
        page.on("pageerror", lambda e: thrown.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        opens = []
        page.on("request", lambda r: opens.append(r.url)
                if r.method == "POST" and r.url.endswith("/api/cases/open") else None)
        page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
        page.wait_for_selector("#cases-list .case")

        print("the case list")
        button = page.locator("#cases-list .case .opencase").first
        check("each case has an Open button", button.is_visible()
              and button.inner_text().strip() == "Open")
        check("and no bar until it is pressed",
              not page.locator("#cases-list .case .openprog").first.is_visible())

        print("pressing Open")
        button.click()
        seen, widths, labels = [], [], set()
        deadline = time.time() + 120
        while time.time() < deadline:
            if page.evaluate("document.getElementById('tab-review')"
                             ".classList.contains('active')"):
                break
            # Read in one step: asked for in three, the open could finish
            # between them and the next read waited for a row no longer
            # opening (2 runs in 6 under load, v2026.11.331).
            row = page.evaluate("""() => {
              const r = document.querySelector('#cases-list .case.opening');
              if (!r) return null;
              return {what: r.querySelector('.openprog .what').textContent,
                      label: (r.querySelector('.opencase') || {}).textContent || '',
                      width: parseFloat(r.querySelector('.openprog .progress-inner').style.width) || 0};
            }""")
            if row:
                what = row["what"]
                if what and (not seen or seen[-1] != what):
                    seen.append(what)
                labels.add(row["label"].strip())
                widths.append(row["width"])
                if len(seen) == 1:
                    # A second press of the same case while it opens.
                    page.locator("#cases-list .case .opencase").first.click(force=True)
            page.wait_for_timeout(60)
        check("the row shows a bar and what is being done", len(seen) >= 2,
              " | ".join(s.split(" — ")[0] for s in seen))
        check("which moves forward, not back", widths and widths == sorted(widths)
              and widths[-1] > widths[0], f"{widths[0] if widths else '-'}% to "
              f"{widths[-1] if widths else '-'}%")
        check("the button says it is opening while it does", "Opening…" in labels,
              ", ".join(sorted(labels)))
        check("and the case opens on the review tab",
              page.evaluate("document.getElementById('tab-review').classList.contains('active')"))
        page.wait_for_timeout(800)
        check("one open, not two, for the second press", len(opens) == 1,
              f"{len(opens)} request(s)")
        total = page.evaluate("caseSize()")
        check("with every file", total == N, f"{total:,}")
        check("no script errors", not thrown, "; ".join(thrown[:2]))
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
