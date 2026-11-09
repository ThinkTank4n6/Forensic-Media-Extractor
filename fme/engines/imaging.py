"""
imaging.py -- one place that opens an image file.

Everything that decodes a picture goes through here: thumbnails, perceptual
hashes, report contact sheets and the interface's previews. Three reasons it
is one place rather than five.

**HEIC.** Pillow cannot read it alone, and iPhones write it. Registering the
opener in one module means a photograph off a phone is readable everywhere
or nowhere, rather than readable in the gallery and invisible in the report.

**Warnings belong in the run, not on the terminal.** Carved files are
routinely truncated: a JPEG whose EXIF block was cut off mid-field makes
Pillow print `Corrupt EXIF data`, once per file, into whatever window the
program was started from. On a run that recovers a few thousand of them that
is a screen of noise that hides anything that actually matters. The warnings
are captured and counted here instead, and the count is reported with the
run -- the information is kept, it just stops shouting.

**Truncated files should still decode as far as they go.** A carved image
missing its last rows is still the picture, and is still worth a thumbnail
and a hash, so Pillow is told to accept what it has.
"""

from __future__ import annotations

import contextlib
import threading
import warnings

__version__ = "1.0.0"

try:
    from PIL import Image, ImageFile
    PILLOW = True
    # A carved file often stops mid-image. Decoding what is there beats
    # refusing the file.
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        HEIF = True
    except ImportError:
        HEIF = False
except ImportError:  # pragma: no cover - Pillow is optional throughout
    Image = None
    PILLOW = False
    HEIF = False

# What was noticed while decoding, so a run can report it rather than a
# terminal scrolling past it.
_LOCK = threading.Lock()
_NOTICED: dict = {}


def noticed() -> dict:
    """Decode warnings seen so far, by kind."""
    with _LOCK:
        return dict(_NOTICED)


def reset() -> None:
    with _LOCK:
        _NOTICED.clear()


def _record(message: str) -> None:
    # The first few words identify the kind; the rest names one file's
    # particular damage and would make every entry unique.
    kind = " ".join(str(message).split()[:6])
    with _LOCK:
        _NOTICED[kind] = _NOTICED.get(kind, 0) + 1


_QUIET = threading.local()
_SHOW = warnings.showwarning


def _show(message, category, filename, lineno, file=None, line=None):
    """Record a warning raised inside quiet() on this thread; pass any other
    on as Python would have shown it."""
    if getattr(_QUIET, "depth", 0):
        _record(message)
        return
    _SHOW(message, category, filename, lineno, file, line)


@contextlib.contextmanager
def quiet():
    """Capture Pillow's warnings instead of letting them print.

    Per thread. warnings.catch_warnings swaps the process's one filter list
    and showwarning, and is not safe across threads: images are opened from
    several at once (thumbnails, hashes, a report), and one thread leaving
    its block restored the filters under another still inside -- whose
    warnings then printed, or were lost, or landed in the wrong count. A
    hook installed once records what is raised while this thread is inside.
    """
    if warnings.showwarning is not _show:
        warnings.showwarning = _show
        # Every occurrence, not once per place: the counts are the point.
        warnings.filterwarnings("always", module=r"PIL(\.|$)")
    _QUIET.depth = getattr(_QUIET, "depth", 0) + 1
    try:
        yield
    finally:
        _QUIET.depth -= 1


@contextlib.contextmanager
def open_image(path: str):
    """Open an image for reading, quietly, with HEIC understood.

    Two Apple formats are recognized by their bytes and rendered first
    (fme/engines/textures.py): a CgBI PNG, which Pillow opens without error
    and draws black, and a KTX or AAPL GPU texture, which Pillow cannot open
    at all. Doing it here, the one place pictures are opened, is what makes
    them a thumbnail, a preview, a perceptual hash and a contact-sheet tile
    together, rather than in whichever caller remembered.
    """
    if not PILLOW:
        raise RuntimeError("Pillow is not installed")
    from fme.engines import textures

    kind = textures.kind_of_file(path)
    with quiet():
        if kind == "cgbi":
            image = textures.decode_cgbi(path)
        elif kind in ("ktx", "aapl"):
            # In a child process: the block decoder can crash, and here that
            # would be the examiner's whole interface.
            image = textures.render_texture(path)
        else:
            image = Image.open(path)
        try:
            yield image
        finally:
            image.close()


def needs_rendering(path: str) -> bool:
    """True for a file a browser cannot show as-is although its extension
    suggests it can -- a CgBI PNG -- or one it cannot show at all, a GPU
    texture. The viewer asks for a rendered preview of these instead."""
    from fme.engines import textures
    return textures.kind_of_file(path) is not None


def status() -> dict:
    return {"available": PILLOW, "heif": HEIF}
