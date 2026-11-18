"""After a grading the gallery's counts and filtered list are adjusted, not
made again, and are the same as made again (v2026.11.332).

Every grading asked for the counts around the gallery; on a case of 220,000
files making them took 0.7 to 0.9 s of the server, holding the interpreter
while the next page and its tiles waited. A grading changes the decisions of
the files graded, and the counts are a sum over files, so only those files'
part of the sum changes. This grades a 60,000-file case at random, under
several filters, and holds the adjusted counts to a count made from nothing
after every step.
"""
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from fme.review import galleryquery as g  # noqa: E402

ok = True


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + str(detail)) if detail != '' else ''} -- {'ok' if good else 'WRONG'}")


N = 60000
rng = random.Random(20261118)
files = [{"rel_path": f"f{i}.jpg", "sha256": f"{i % (N - 500):064x}", "name": f"f{i}.jpg",
          "ext": "jpg", "category": "image" if i % 7 else "video", "size": i,
          "deleted": i % 5 == 0, "engine": "filesystem", "container": i % 97 == 0,
          "similar_group": (i // 3) if i % 11 == 0 else None,
          "hash_sets": [{"kind": "nsrl"}] if i % 53 == 0 else []}
         for i in range(N)]          # 500 digests carried by two files each
states = [{"filter": "all"}, {"filter": "needs-review"},
          {"filter": "needs-review", "stackSimilar": True}, {"filter": "deleted", "catFilter": "1"},
          {"filter": "all", "search": "f1"}, {"filter": "auto", "flagFilter": [2]}]

for raw in states:
    st = g.State(raw, uncategorized=8)
    cache = g.Cache()
    decisions, version = {}, 0
    worst = 0.0
    same = True
    for step in range(40):
        decisions = dict(decisions)          # a new table each version, as casedb gives
        for _ in range(rng.choice((1, 1, 1, 3, 40))):
            digest = files[rng.randrange(N)]["sha256"]
            if rng.random() < 0.15:
                decisions.pop(digest, None)
            else:
                decisions[digest] = {"category": rng.choice((1, 2, 3, 8)),
                                     "auto": rng.random() < 0.2,
                                     "flags": rng.sample((1, 2, 3), rng.randrange(3))}
        version += 1
        started = time.perf_counter()
        adjusted = cache.aggregates(files, decisions, version, raw, st)
        if step:
            worst = max(worst, time.perf_counter() - started)
        started = time.perf_counter()
        shown = cache.visible(files, decisions, version, raw, st)
        if step:
            worst = max(worst, time.perf_counter() - started)
        if adjusted != g.aggregates(files, decisions, st) or \
                [f["rel_path"] for f in shown] != [f["rel_path"] for f in g.visible(files, decisions, st)]:
            same = False
            break
    check(f"{raw}: counts and the filtered list the same as made from nothing, "
          f"after each of 40 gradings", same)
    check("  and each adjusted under a twentieth of a second", worst < 0.05,
          f"{worst * 1000:.1f} ms at most")

print("after the list itself is replaced (a new run, an open)")
st = g.State({"filter": "all"}, uncategorized=8)
cache = g.Cache()
cache.aggregates(files, {}, 1, {"filter": "all"}, st)
fresh = [dict(f) for f in files[:1000]]
check("it is counted from nothing", cache.aggregates(fresh, {}, 2, {"filter": "all"}, st)
      == g.aggregates(fresh, {}, st))

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
