"""Replacing an engine name for a test, in every module that looks it up.

media_core was split into layered modules in v2026.11.314 and re-exports
every name. Assigning ``media_core.X = stand_in`` rebinds only media_core's
copy; carving.py, fs_walk.py and the rest carry on with the real X. A name
is looked up in each module that binds it -- by defining it or importing it
-- so replacing it in all of those reaches every caller.

    with _engine.replaced("STRIPE_MAX_BYTES", 512 << 10):
        ...

or, where a with-block does not fit the test's shape,

    saved = _engine.replace("open_image", stand_in)
    try: ...
    finally: _engine.restore(saved)

tests/test_patch_targets.py refuses a replacement that misses a module.
"""
import contextlib
import importlib

SPLIT = ["media_core", "engine_common", "image_readers", "signatures",
         "length_parsers", "carve_judging", "embedded", "fs_walk",
         "deleted_recovery", "carving", "volume_survey", "sources"]
MODULES = [importlib.import_module(f"fme.engines.{name}") for name in SPLIT]


def replace(name: str, value) -> dict:
    """Bind name to value in every engine module that binds it; returns
    what was there, for restore()."""
    saved = {}
    for module in MODULES:
        if name in vars(module):
            saved[module] = vars(module)[name]
            setattr(module, name, value)
    if not saved:
        raise AttributeError(f"no engine module binds {name!r}")
    saved["__name__"] = name
    return saved


def restore(saved: dict) -> None:
    name = saved.pop("__name__")
    for module, value in saved.items():
        setattr(module, name, value)


@contextlib.contextmanager
def replaced(name: str, value):
    saved = replace(name, value)
    try:
        yield
    finally:
        restore(saved)
