"""Forensic Media Extractor.

Recovers images, video and audio from forensic images, live devices, mounted
folders, report packages and databases, then exports the results in the
formats review platforms read.

    fme.engines   reading evidence: containers, file systems, carving,
                  databases, metadata, similarity
    fme.exports   writing reports: Project VIC, C4All, LAVA, KML, HTML
    fme.review    working the results: case decisions, hash sets, triage
    fme.web       the browser interface
"""

import os

__all__ = ["engines", "exports", "review", "web", "RELEASE"]


def _release() -> str:
    """The package release, from the VERSION file beside the source tree."""
    here = os.path.dirname(os.path.abspath(__file__))
    for folder in (here, os.path.dirname(here)):
        path = os.path.join(folder, "VERSION")
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    return "v" + fh.read().strip()
            except OSError:
                break
    return "v?"


RELEASE = _release()


def build_number(text) -> int:
    """The build counter in a release number, whichever form it is written in.

    Releases are numbered Year.Month.Build -- v2026.11.271 -- where the build
    counter carries on from the single integers used up to v270 and never
    resets. The year and month say when a build was made; the build counter
    is what orders them, so it is what a version floor compares. Accepts
    "2026.11.271", "v2026.11.271", the older "270", or an int; anything else
    is 0.
    """
    if isinstance(text, int):
        return text
    parts = str(text or "").strip().lstrip("vV").split(".")
    try:
        if len(parts) == 3 and len(parts[0]) == 4:
            return int(parts[2])
        return int(parts[0])
    except ValueError:
        return 0
