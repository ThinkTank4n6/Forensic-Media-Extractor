"""The image models on the graphics cards, through ONNX Runtime
(fme/engines/accel.py, v2026.11.332).

What runs here for real: ONNX Runtime on the processor, against the OpenCV
path the descriptions were always made by -- the same descriptions, so a case
described partly each way ranks as one. What cannot: no graphics card is
here, so the cards' branch is driven through a stand-in ONNX Runtime that
says it has two, and the checks are of what the code does with what a card
says -- two sessions a card, DirectML's runs taken one at a time, and a
"card" that quietly fell back to the processor not counted as one. The real
check is `bench` on the workstation (docs/EXAMINER_TASKS.md, Session H).
"""
import os
import shutil
import sys
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


from fme.engines import accel, content_search, models  # noqa: E402

model, _source, why = models.resolve("content", "")
if not model:
    print(f"(not checked: no content model -- {why})")
    sys.exit(0)

ROOT = os.path.join(tempfile.gettempdir(), "fme_accel-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

print("on the processor, for real")
if accel.runtime() is None:
    print("  not checked: ONNX Runtime is not installed here")
else:
    import numpy as np
    from PIL import Image
    paths = []
    for n in range(8):
        path = os.path.join(ROOT, f"p{n}.jpg")
        Image.fromarray((np.random.RandomState(n).rand(300, 400, 3) * 255)
                        .astype("uint8")).save(path)
        paths.append(path)
    broken = os.path.join(ROOT, "broken.jpg")
    with open(broken, "wb") as fh:
        fh.write(b"\xff\xd8\xff not a picture")
    with mock.patch.dict(os.environ, {accel.ENV: "cpu"}):
        engines = accel.plan_for(model)
        check("ONNX Runtime on the processor is used", engines and
              all("processor" in e.label for e in engines), accel.describe_label(engines))
        fast = content_search.describe_many(paths + [broken], model)
    with mock.patch.dict(os.environ, {accel.ENV: "opencv"}):
        check("and asked not to, it is not", accel.plan_for(model) == [])
        slow = content_search.describe_many(paths, model)
    agree = min(content_search.similarity(fast[p], slow[p]) for p in paths)
    check("the same descriptions OpenCV makes", agree > 0.999, round(agree, 5))
    check("a picture that will not read is no description, and stops nothing",
          fast[broken] is None and all(fast[p] for p in paths))

print("the cards, through a stand-in that says it has two")


class Session:
    def __init__(self, path, options=None, providers=None):
        name, settings = providers[0] if isinstance(providers[0], tuple) else (providers[0], {})
        self.device = settings.get("device_id", 0)
        if self.device >= Fake.cards:
            raise RuntimeError("invalid device ordinal")
        self.provider = name if not Fake.falls_back else "CPUExecutionProvider"
        if name == "CPUExecutionProvider":
            self.provider = name

    def get_providers(self):
        return [self.provider, "CPUExecutionProvider"]

    def get_inputs(self):
        return [type("I", (), {"name": "pixel_values", "shape": [1, 3, 224, 224]})()]

    def run(self, _outputs, feed):
        import time
        # A card takes 1 ms, the processor 20, and the software renderer --
        # the adapter after the last card, when Fake.renderer is on -- 200.
        if self.provider == "CPUExecutionProvider":
            time.sleep(0.02)
        elif Fake.renderer and self.device == Fake.cards - 1:
            time.sleep(0.2)
        else:
            time.sleep(0.001)
        return [feed["pixel_values"]]


class Fake:
    cards, falls_back, renderer, __version__ = 2, False, False, "stand-in"
    InferenceSession = Session

    class SessionOptions:
        pass

    class ExecutionMode:
        ORT_SEQUENTIAL = 0

    @staticmethod
    def get_available_providers():
        return ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]


with mock.patch.object(accel, "runtime", lambda: Fake):
    for choice, label in (("cuda", "CUDA"), ("dml", "DirectML")):
        accel._plans.clear()
        with mock.patch.dict(os.environ, {accel.ENV: choice}):
            engines = accel.plan_for(model)
        cards = sorted({e.label for e in engines})
        check(f"{label}: both cards, two sessions each",
              len(engines) == 4 and cards == [f"{label} card 0", f"{label} card 1"],
              accel.describe_label(engines))
        check(f"  {label}: runs one at a time on a session" if label == "DirectML"
              else f"  {label}: runs side by side on a session",
              all((e.lock is not None) == (label == "DirectML") for e in engines))
    accel._plans.clear()
    Fake.cards, Fake.renderer = 3, True
    with mock.patch.dict(os.environ, {accel.ENV: "dml"}):
        real = accel.plan_for(model)
    check("a software renderer listed after the two cards is timed, and let go",
          sorted({e.label for e in real}) == ["DirectML card 0", "DirectML card 1"],
          accel.describe_label(real))
    Fake.cards, Fake.renderer = 2, False
    accel._plans.clear()
    Fake.falls_back = True
    with mock.patch.dict(os.environ, {accel.ENV: "cuda"}):
        quiet = accel.plan_for(model)
    check("a session that fell back to the processor is not counted as a card", quiet == [],
          accel.describe_label(quiet))
    accel._plans.clear()
    Fake.falls_back = False
    with mock.patch.dict(os.environ, {accel.ENV: "auto"}):
        auto = accel.plan_for(model)
    check("left to choose, it takes CUDA first", auto and auto[0].label.startswith("CUDA"))
    # Every picture through the four sessions, each once.
    seen = []
    results = accel.run_many(list(range(40)), lambda n: n, lambda n, out: seen.append(n) or out,
                             auto)
    check("every item through the sessions, once", sorted(seen) == list(range(40))
          and len(results) == 40)
accel._plans.clear()

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
