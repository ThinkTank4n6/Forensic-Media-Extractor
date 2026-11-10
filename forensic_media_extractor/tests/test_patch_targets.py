"""A test that replaces an engine name replaces it where the engine looks it up.

media_core.py was split into layered modules in v2026.11.314. It re-exports
every name, so `media_core.X` still reads the right thing -- but assigning
`media_core.X = stand_in` only rebinds media_core's copy. Code in
carving.py, fs_walk.py and the rest looks X up in its own module, carries
on with the real X, and the test passes or fails for a reason that has
nothing to do with what it says it checks.

So this reads every test, finds each engine name it replaces (by assignment,
setattr or mock.patch.object), and requires that every engine module whose
code reads that name is one the test replaces it in.
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
ENGINES = os.path.join(PROJECT, "fme", "engines")
SPLIT = ["media_core", "engine_common", "image_readers", "signatures",
         "length_parsers", "carve_judging", "embedded", "fs_walk",
         "deleted_recovery", "carving", "volume_survey", "sources"]

ok = True


def readers_of(module: str) -> set:
    """Names the module's code reads (not just binds or imports)."""
    with open(os.path.join(ENGINES, module + ".py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            names.add(node.id)
    return names


READS = {m: readers_of(m) for m in SPLIT}


def aliases(tree) -> dict:
    """Local name -> engine module, from the test's imports."""
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("fme.engines",):
            for a in node.names:
                if a.name in SPLIT:
                    found[a.asname or a.name] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module and \
                node.module.startswith("fme.engines.") and False:
            pass
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("fme.engines.") and a.name.split(".")[-1] in SPLIT:
                    if a.asname:
                        found[a.asname] = a.name.split(".")[-1]
    return found


def replaced(tree, alias_map) -> dict:
    """{name: {modules the test replaces it in}}."""
    out = {}

    def note(target_module, name):
        if target_module in alias_map:
            out.setdefault(name, set()).add(alias_map[target_module])

    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for t in targets:
            for x in ast.walk(t):
                if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name) \
                        and isinstance(x.ctx, ast.Store):
                    note(x.value.id, x.attr)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and getattr(node.func.value, "id", "") == "_engine" \
                and node.func.attr in ("replace", "replaced") and node.args \
                and isinstance(node.args[0], ast.Constant):
            # tests/_engine.py replaces the name in every module binding it.
            out.setdefault(node.args[0].value, set()).update(SPLIT)
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in ("setattr", "object") and len(node.args) >= 2 and \
                    isinstance(node.args[0], ast.Name) and \
                    isinstance(node.args[1], ast.Constant):
                note(node.args[0].id, node.args[1].value)
    return out


problems = []
checked = 0
for name in sorted(os.listdir(HERE)):
    if not (name.startswith("test_") and name.endswith(".py")):
        continue
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    alias_map = aliases(tree)
    for engine_name, modules in replaced(tree, alias_map).items():
        readers = {m for m in SPLIT if engine_name in READS[m]}
        if not readers:
            continue
        checked += 1
        missing = readers - modules
        if missing:
            problems.append(f"{name}: replaces {engine_name} in {sorted(modules)}, "
                            f"but {sorted(missing)} read it too")

print(f"engine names replaced by tests, and checked: {checked}")
for p in problems:
    print("  " + p)
ok &= not problems and checked > 20
print(f"every replacement reaches every module that reads the name: {not problems}")

# The checker itself: a replacement made only in media_core of a name the
# carving module reads is caught.
sample = ast.parse("from fme.engines import media_core\nmedia_core.STRIPE_MAX_BYTES = 1\n")
caught = {m for m in SPLIT if "STRIPE_MAX_BYTES" in READS[m]} - \
    replaced(sample, aliases(sample)).get("STRIPE_MAX_BYTES", set())
ok &= "carving" in caught
print(f"and it catches a replacement made only in media_core: {'carving' in caught}")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
