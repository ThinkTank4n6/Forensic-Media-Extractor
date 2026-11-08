"""Every package the code imports is one requirements.txt installs.

requirements.txt had drifted from the code: cryptography (C2PA checks, the
hash store's TLS certificate), pycryptodome (encrypted acquisitions,
BitLocker), argon2-cffi (LUKS2), numpy (the fast MPEG-TS carve), Pillow
and seven more were imported by shipped code and left out or commented
out, so a machine set up from it quietly could not do those things. And
pyproject.toml declared one table twice, which is not valid TOML, so
`pip install .` failed before it began -- and it left the interface's
page out of the package, so an installed copy had no page to serve.

Checked: every third-party import under fme/ names a package in
requirements.txt or requirements-vm.txt; every line of every requirements
file is a valid requirement; pyproject.toml parses, agrees with VERSION,
and packages the interface's files.
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)

# Import name -> the distribution that provides it.
PROVIDES = {
    "flask": "flask", "werkzeug": "werkzeug", "pytsk3": "pytsk3",
    "pyewf": "libewf-python", "pyvmdk": "libvmdk-python",
    "pyvhdi": "libvhdi-python", "pyqcow": "libqcow-python",
    "PIL": "pillow", "pillow_heif": "pillow-heif",
    "texture2ddecoder": "texture2ddecoder", "liblzfse": "pyliblzfse",
    "cv2": "opencv-python-headless", "numpy": "numpy",
    "Crypto": "pycryptodome", "argon2": "argon2-cffi",
    "cryptography": "cryptography", "py7zr": "py7zr",
    "zstandard": "zstandard", "waitress": "waitress", "cheroot": "cheroot",
    "reportlab": "reportlab", "webview": "pywebview",
}
# Imported only as a fallback for one of the above, or not a package at all.
ALTERNATIVES = {
    "Cryptodome": "pycryptodomex, accepted in place of pycryptodome",
    "pyzstd": "accepted in place of zstandard",
    "backports": "backports.zstd, accepted in place of zstandard",
    "compression": "the standard library's zstd, Python 3.14 and later",
    "PyInstaller": "build only (requirements: pyproject [build])",
}
VENDORED = {"ewfprobe", "qnxprobe", "exoprobe", "c2pareader", "mediacarve",
            "impacket_ese"}


def requirements(name: str) -> set:
    sys.path.insert(0, PROJECT)
    try:
        from packaging.requirements import Requirement
    except ImportError:
        from pip._vendor.packaging.requirements import Requirement
    names = set()
    with open(os.path.join(PROJECT, name), encoding="utf-8") as fh:
        for line in fh:
            line = line.split(" #")[0].strip()
            if line and not line.startswith(("#", "-r")):
                names.add(Requirement(line).name.lower())
    return names


def imported() -> dict:
    std = set(getattr(sys, "stdlib_module_names", ())) or set(sys.builtin_module_names)
    found = {}
    for root, _, files in os.walk(os.path.join(PROJECT, "fme")):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                    mods = [node.module]
                else:
                    continue
                for mod in mods:
                    top = mod.split(".")[0]
                    if top not in std and top != "fme" and top not in VENDORED:
                        found.setdefault(top, os.path.relpath(path, PROJECT))
    return found


def main() -> int:
    ok = True
    try:
        listed = {f: requirements(f) for f in ("requirements.txt", "requirements-vm.txt",
                                               "requirements-dev.txt",
                                               "requirements-minimal.txt")}
        valid = True
    except Exception as exc:  # noqa: BLE001 - what this checks
        print(f"a requirements file has a line pip cannot read: {exc}")
        listed, valid = {}, False
    ok &= valid
    print(f"every line of every requirements file is a valid requirement: {valid}")

    installs = listed.get("requirements.txt", set()) | listed.get("requirements-vm.txt", set())
    found = imported()
    unknown = sorted(m for m in found if m not in PROVIDES and m not in ALTERNATIVES)
    ok &= not unknown
    print(f"every third-party import is known to this test: {not unknown}"
          + (f" -- add {', '.join(f'{m} ({found[m]})' for m in unknown)}" if unknown else ""))
    missing = sorted(f"{PROVIDES[m]} (imported as {m} in {found[m]})"
                     for m in found if m in PROVIDES and PROVIDES[m] not in installs)
    ok &= not missing
    print(f"and requirements.txt installs each one: {not missing}"
          + ("".join(f"\n   missing: {m}" for m in missing)))

    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            tomllib = None
    if tomllib is None:
        print("no TOML reader on this Python (3.11+ has one); pyproject not checked")
    else:
        with open(os.path.join(PROJECT, "pyproject.toml"), "rb") as fh:
            try:
                project = tomllib.load(fh)
                parsed = True
            except Exception as exc:  # noqa: BLE001
                print(f"   {exc}")
                parsed = False
        ok &= parsed
        print(f"pyproject.toml is valid TOML, so `pip install .` can read it: {parsed}")
        if parsed:
            with open(os.path.join(PROJECT, "VERSION"), encoding="utf-8") as fh:
                same = project["project"]["version"] == fh.read().strip()
            ok &= same
            print(f"its version is VERSION's: {same}")
            data = project["tool"]["setuptools"]["package-data"].get("fme.web", [])
            import fnmatch
            # Every file under ui/, the case map's assets in ui/maps/ too.
            # setuptools' "*" does not cross a "/", so each pattern is
            # matched one level at a time.
            base = os.path.join(PROJECT, "fme", "web", "ui")
            ui = sorted(os.path.relpath(os.path.join(d, f), base).replace(os.sep, "/")
                        for d, _dirs, names in os.walk(base) for f in names
                        if "__pycache__" not in d)

            def _matches(rel, pattern):
                return (rel.count("/") == pattern.count("/")
                        and fnmatch.fnmatch(rel, pattern))
            missing = [f for f in ui
                       if not any(_matches("ui/" + f, pat) for pat in data)]
            shipped = not missing
            ui = missing or [f"{len(ui)} file(s)"]
            ok &= shipped
            print(f"and it packages every file of the interface's page: {shipped} {sorted(ui)}")
            full = {n.split(">")[0].split(";")[0].split("=")[0].strip().lower()
                    for n in project["project"]["optional-dependencies"]["full"]}
            extra = sorted(installs - full - set(project["project"]["dependencies"] and
                                                 ["flask", "werkzeug"])
                           - listed.get("requirements-vm.txt", set()))
            ok &= not extra
            print(f"pip install .[full] installs what requirements.txt does: {not extra}"
                  + (f" (only in requirements.txt: {extra})" if extra else ""))

    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
