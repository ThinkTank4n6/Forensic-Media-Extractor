"""Content search describes the whole picture, the right way, and ranks honestly.

Recipe 2 (fme/engines/content_search.py): the picture is fitted and padded,
never cropped; the description is the summary token and the patch average,
each unit length, joined; descriptions are kept compact and under a name
that carries the recipe, so an old case's recipe-1 vectors are remade
rather than compared with new ones and scoring nothing.

No model ships with this program, so the parts that need one run only when
FME_CONTENT_MODEL names an ONNX file (DINOv2-small is the one this was
written against). The rest needs NumPy and OpenCV.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import cv2
    import numpy as np
except ImportError as exc:
    strict = os.environ.get("FME_TESTS_STRICT") == "1"
    print(f"(needs NumPy and OpenCV: {exc}; {'FAILING, strict mode' if strict else 'skipped'})")
    sys.exit(1 if strict else 0)

from fme.engines import content_search as cs  # noqa: E402

ok = True


def check(label, passed, detail=""):
    global ok
    ok &= bool(passed)
    print(f"  {label}: {bool(passed)}" + (f"  ({detail})" if detail and not passed else ""))


print("the description")
rng = np.random.default_rng(3)
tokens = rng.normal(size=(1, 257, 384)).astype(np.float32)
v = cs._vector(tokens)
check("a token model gives summary + patch average: 768 numbers", v.shape == (768,), v.shape)
check("unit length", abs(float(np.linalg.norm(v)) - 1) < 1e-5)
summary = tokens[0, 0] / np.linalg.norm(tokens[0, 0])
check("its first half is the summary token, not averaged into the patches",
      np.allclose(v[:384] * np.sqrt(2), summary, atol=1e-5))
single = cs._vector(rng.normal(size=(1, 512)).astype(np.float32))
check("a model returning one vector is used as it is", single.shape == (512,))

print("\nwhat is kept with the case")
kept = cs.encode(v)
back = cs.decode(kept)
check("kept compactly (float16, base64)", isinstance(kept, str) and len(kept) < 2100, len(kept))
check("and comes back to within float16", np.allclose(back, v, atol=2e-3))
check("the cache name carries the recipe", cs.CACHE_NAME.endswith(f"v{cs.RECIPE}")
      and cs.CACHE_NAME != "content_vectors")
check("an old 384-number description is not compared with a new one",
      cs.similarity(kept, list(rng.normal(size=384))) == 0.0)

print("\nthe score")
a = np.zeros(768, np.float32)
a[0] = 1
near = np.zeros(768, np.float32)
near[0], near[1] = 0.2, np.sqrt(1 - 0.04)
far = np.zeros(768, np.float32)
far[0], far[1] = -0.5, np.sqrt(1 - 0.25)
check("further apart never scores higher (cosine -0.5 vs +0.2)",
      cs.similarity(a, far) < cs.similarity(a, near),
      (cs.similarity(a, far), cs.similarity(a, near)))
unit = lambda x: x / np.linalg.norm(x)
library = {f"d{i}": cs.encode(unit(rng.normal(size=768))) for i in range(2000)}
library["same"] = cs.encode(a)
ranked = cs.rank(cs.encode(a), library, limit=5)
check("the picture itself ranks first", ranked and ranked[0]["sha256"] == "same", ranked[:1])
check("ranks are 1, 2, 3 in order of score",
      [r["rank"] for r in ranked] == list(range(1, len(ranked) + 1))
      and all(x["score"] >= y["score"] for x, y in zip(ranked, ranked[1:])))

print("\nthe whole picture reaches the model")
wide = np.full((300, 900, 3), 255, np.uint8)
wide[50:250, 20:220] = (0, 0, 255)          # subject at the far left edge
sample = cs._prepare(wide)[0]               # normalised, channels first
red = (1 - cs._MEAN[0]) / cs._STD[0]
check("a subject at the edge of a 3:1 picture is still in the model's input",
      float((np.abs(sample[0] - red) < 0.05).mean()) > 0.03)
check("the input is the model's size", cs._prepare(wide).shape == (1, 3, cs.SIZE, cs.SIZE))

model = os.environ.get("FME_CONTENT_MODEL", "")
print("\nwith the model")
if not model:
    print("  (not checked: FME_CONTENT_MODEL names no model, and none ships with this program)")
else:
    ready, why = cs.available(model)
    check("the model loads", ready, why)
    if ready:
        work = tempfile.mkdtemp(prefix="fme_content-")
        y, x = np.mgrid[0:240, 0:320]
        photo = np.clip(np.stack([x, y, x + y], -1) * 0.6
                        + np.random.default_rng(9).integers(0, 60, (240, 320, 3)), 0, 255).astype(np.uint8)
        other = np.random.default_rng(10).integers(0, 255, (240, 320, 3), dtype=np.uint8)
        framed = np.full((240, 960, 3), 235, np.uint8)
        framed[:, :320] = photo
        paths = {}
        for name, img, quality in (("photo", photo, 95), ("again", photo, 40),
                                   ("framed", framed, 90), ("other", other, 90)):
            paths[name] = os.path.join(work, f"{name}.jpg")
            cv2.imwrite(paths[name], img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        d = {n: cs.describe(p, model) for n, p in paths.items()}
        check("every picture is described", all(d.values()))
        if all(d.values()):
            lib = {n: d[n] for n in ("again", "framed", "other")}
            order = [r["sha256"] for r in cs.rank(d["photo"], lib)]
            check("the same picture recompressed, and shown off-centre in a wide frame, "
                  "both rank above an unrelated one", order[-1] == "other", order)

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
