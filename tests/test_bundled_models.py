"""The image models ship with the program, are checked, and are labelled (v2026.11.317).

Content search and face search each needed a model path in Settings, and no
model shipped: on a machine where nobody had set one, both features were
there and unusable. DINOv2-small, YuNet and SFace now ship in fme/models/
with their licences. Checked here:

1. each is present, matches its published SHA-256, and is packaged;
2. a search with nothing in Settings uses them -- content search finds a
   re-framed copy of a picture first; Find faces runs;
3. one that does not match its hash is refused, with the reason;
4. every result is labelled an investigative lead only.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import contextlib
import hashlib
import io
import json
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

from fme.engines import content_search, face_match, models  # noqa: E402

ROOT = os.path.join(tempfile.gettempdir(), "fme_models-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- "
          f"{'ok' if good else 'WRONG'}")


print("what ships")
for kind, (name, digest, size, what, licence) in models.BUNDLED.items():
    path, why = models.check(kind)
    check(f"{name}: present, {size:,} bytes, its published SHA-256", bool(path), why)
    check(f"  with its licence, {licence}",
          os.path.isfile(os.path.join(models.folder(), licence)))
check("and a record of where they came from",
      os.path.isfile(os.path.join(models.folder(), "README.md")))
spec = open(os.path.join(PROJECT, "packaging", "fme.spec"), encoding="utf-8").read()
project = open(os.path.join(PROJECT, "pyproject.toml"), encoding="utf-8").read()
check("the frozen build and the package both carry them",
      '"fme", "models"' in spec and "models/*.onnx" in project
      and "models/LICENSE-*" in project)

print("\none that has been changed is not used")
real_folder = models.folder
copy = os.path.join(ROOT, "models")
os.makedirs(copy)
name = models.BUNDLED["face_detector"][0]
data = bytearray(open(os.path.join(real_folder(), name), "rb").read())
data[1000] ^= 0xFF
open(os.path.join(copy, name), "wb").write(bytes(data))
models.folder = lambda: copy
try:
    path, why = models.check("face_detector")
    check("a detector with one byte changed is refused, and the reason given",
          path == "" and "SHA-256" in why, why)
    path, source, _ = models.resolve("face_detector", "/units/own/yunet.onnx")
    check("a path in Settings is used as given", path == "/units/own/yunet.onnx"
          and source == "settings")
finally:
    models.folder = real_folder

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    print("\n(searches not run: OpenCV is not installed)")

if cv2 is not None:
    content_ready, why = content_search.available(models.check("content")[0])
    print("\ncontent search, with nothing in Settings")
    rng = np.random.default_rng(7)
    case_dir = os.path.join(ROOT, "case")
    os.makedirs(os.path.join(case_dir, "images"))

    def scene(seed):
        r = np.random.default_rng(seed)
        img = np.full((240, 320, 3), r.integers(0, 255, 3), np.uint8)
        for _ in range(12):
            x, y = r.integers(0, 280), r.integers(0, 200)
            cv2.rectangle(img, (int(x), int(y)), (int(x) + 40, int(y) + 30),
                          tuple(int(v) for v in r.integers(0, 255, 3)), -1)
        return img

    target = scene(1)
    # The same picture, smaller and brighter: no byte and no hash in common.
    # (Synthetic scenes of rectangles all look alike to the model, so the
    # distractors score high too; the copy must still come first.)
    copy = cv2.convertScaleAbs(cv2.resize(target, (256, 192)), alpha=1.1, beta=10)
    pictures = {"target.jpg": target, "reframed.jpg": copy}
    for n in range(2, 10):
        pictures[f"other{n}.jpg"] = scene(n)
    rows = []
    for fname, img in pictures.items():
        path = os.path.join(case_dir, "images", fname)
        cv2.imwrite(path, img)
        blob = open(path, "rb").read()
        rows.append({"rel_path": f"images/{fname}", "out_path": f"images/{fname}",
                     "category": "image", "ext": "jpg", "size": len(blob),
                     "sha256": hashlib.sha256(blob).hexdigest(), "md5": "",
                     "sha1": "", "source_path": "/" + fname, "deleted": False})
    with open(os.path.join(case_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"summary": {"image": "synthetic"}, "files": rows}, fh)

    with contextlib.redirect_stdout(io.StringIO()):
        from fme.web import media_gui as gui
    client = gui.app.test_client()
    H = {"X-FME-Request": "1"}
    gui.settings.save({"content_model": "", "face_detector": "",
                       "face_recognizer": ""})
    job = "models-job"
    with gui.JOBS_LOCK:
        gui.JOBS[job] = {"out_dir": case_dir, "files": rows}
    told = client.get("/api/content/model?path=", headers=H).get_json()
    if content_ready:
        check("Settings is told the bundled model is ready",
              told["ready"] and told["bundled"] and "DINOv2" in told["name"], told)
        answer = client.post("/api/content/search", headers=H,
                             json={"job": job, "file": "images/target.jpg"}).get_json()
        order = [r["name"] for r in answer.get("results", [])]
        check("the bundled DINOv2 finds the altered copy first",
              order[:2] == ["target.jpg", "reframed.jpg"], order[:3])
        check("and the answer is labelled an investigative lead only",
              answer.get("lead") == "Investigative lead only"
              and "not an identification" in answer.get("caveat", ""))
    else:
        check("where OpenCV cannot load DINOv2, Settings is told why",
              not told["ready"] and "OpenCV" in told["why"], told["why"][:80])

    print("\nface search, with nothing in Settings")
    ready, why = face_match.available(models.check("face_detector")[0],
                                      models.check("face_recognizer")[0])
    check("the bundled YuNet and SFace load", ready, why)
    answer = client.post("/api/faces/scan", headers=H, json={"job": job})
    body = answer.get_json()
    check("Find faces runs over the case with them",
          answer.status_code == 200 and body["pictures"] == len(rows), body)
    check("and its answer is labelled an investigative lead only",
          body.get("lead") == "Investigative lead only")

    print("\nwhat is kept, and how a face search is reached (v2026.11.318)")
    case = gui._case_for(job)
    if content_ready:
        model = models.check("content")[0]
        check("content descriptions are kept under the model that made them",
              case.derived(content_search.cache_name(model))
              and not case.derived(content_search.CACHE_NAME))
    other = "faces-unscanned"
    with gui.JOBS_LOCK:
        gui.JOBS[other] = {"out_dir": os.path.join(ROOT, "unscanned"), "files": []}
    never = client.get(f"/api/faces/of?job={other}&sha256={rows[0]['sha256']}").get_json()
    check("a case never searched for faces says so, rather than \"no face\"",
          never.get("scanned") is False, never)
    found = client.get(f"/api/faces/of?job={job}&sha256={rows[0]['sha256']}").get_json()
    check("the faces found in a picture can be asked for, once Find faces has run",
          found.get("ok") and found.get("scanned") is True and found["faces"] == [], found)
    page_js = open(os.path.join(PROJECT, "fme", "web", "ui", "app.js"), encoding="utf-8").read()
    check("and the details pane has the button that starts a face search",
          "data-face=" in page_js and "chooseFace(" in page_js
          and "await searchFace(sha256, which)" in page_js)

page = open(os.path.join(PROJECT, "fme", "web", "ui", "app.js"), encoding="utf-8").read()
check("the page shows the label with every content and face result",
      page.count('<strong class="lead">') == 3)

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
