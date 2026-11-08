"""What the standalone build must carry so it may be distributed at all.

The program's own license, the notices file, the full license texts the
notices point at, and each bundled Python package's own license files as its
installed metadata lists them. Called from packaging/fme.spec; checked by
tests/test_licenses.py. A build that leaves these out is a build that may not
be handed to anybody.
"""
import importlib.metadata as _metadata
import os
import re

LICENSE_NAME = re.compile(r"(^|/)(licen[cs]e|copying|notice|authors)[^/]*$", re.I)


def project_files(project: str):
    """(source, destination folder) pairs for the project's own notices."""
    pairs = [(os.path.join(project, "LICENSE"), "."),
             (os.path.join(project, "THIRD_PARTY_NOTICES.md"), ".")]
    folder = os.path.join(project, "packaging", "licenses")
    for name in sorted(os.listdir(folder)):
        pairs.append((os.path.join(folder, name), "licenses"))
    return pairs


def distributions_for(modules):
    """The installed distributions that provide these top-level modules."""
    owners = _metadata.packages_distributions()
    found = []
    for module in modules:
        for dist in owners.get(module, []):
            if dist not in found:
                found.append(dist)
    return found


def package_license_files(modules):
    """(source, destination folder) for every license file of every
    distribution that provides one of these modules, as installed."""
    pairs = []
    for name in distributions_for(modules):
        try:
            dist = _metadata.distribution(name)
        except _metadata.PackageNotFoundError:
            continue
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        for entry in dist.files or []:
            text = str(entry).replace("\\", "/")
            if not LICENSE_NAME.search(text):
                continue
            path = str(dist.locate_file(entry))
            if not os.path.isfile(path):
                continue
            # Kept at its own relative place: several packages ship more than
            # one LICENSE.md, and one folder would keep only the last.
            inner = text.split(".dist-info/", 1)[-1]
            folder = os.path.dirname(inner).strip("/")
            pairs.append((path, f"licenses/python/{safe}" + (f"/{folder}" if folder else "")))
    return pairs
