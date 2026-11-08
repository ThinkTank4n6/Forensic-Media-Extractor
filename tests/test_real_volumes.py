"""Volumes written by real operating systems, read both ways, against what the
operating system itself said was on them.

Every other file-system fixture here is built by this project's own code,
and code that builds a volume shares its author's assumptions with the code
that reads it. These were made by the systems that write the formats --
Windows 11 formatted and filled ntfs-windows.img and recorded each file's
SHA-256 with Get-FileHash; Linux's own drivers wrote the exFAT, FAT32 and
NTFS deletion fixtures and hashed each file before it was deleted -- and
are carried from the comparison tool's suite (MIT; fme/vendor/vendored.json).

Each is read twice: by The Sleuth Kit where it is installed, and by this
program's own readers with FME_NO_TSK=1. Both must return every picture the
operating system recorded, byte for byte, under its path, and they must
agree with each other. Run on a machine without pytsk3, only the second
reading happens, and the run says so.

What it found the first time it ran with pytsk3 installed (v2026.11.306):
FME_NO_TSK=1 did not switch TSK off, so the two readings were one; TSK's
APFS was absent and nothing read an APFS volume instead; a file deleted
with its exFAT folder lost the folder's name; and both readers stored a
volume's upcase table as a picture.
"""
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
sys.path.insert(0, PROJECT)

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_real_volumes-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
MEDIA = (".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mov", ".heic")


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"  {label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


def answers(name):
    """{path: sha256} of the pictures the operating system recorded; '-'
    for one it could not read (a cloud placeholder with no data)."""
    want = {}
    if name == "ntfs-deleted":
        for line in open(os.path.join(FIXTURES, "ntfs-deleted.intended")):
            digest, _size, path = line.split(None, 2)
            want[path.strip()] = digest
    elif name == "ntfs-windows":
        for line in open(os.path.join(FIXTURES, "ntfs-windows.known.tsv"), encoding="utf-8"):
            if line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            want[cols[0]] = cols[4]
    else:
        for line in open(os.path.join(FIXTURES, f"{name}.deleted.sha256")):
            if line.strip() and not line.startswith("#"):
                digest, path = line.split(None, 1)
                want[path.strip()] = digest
    return {p: d for p, d in want.items() if p.lower().endswith(MEDIA)}


def extract(image, no_tsk):
    out = os.path.join(ROOT, os.path.basename(image) + ("-own" if no_tsk else "-tsk"))
    env = dict(os.environ)
    if no_tsk:
        env["FME_NO_TSK"] = "1"
    else:
        env.pop("FME_NO_TSK", None)
    done = subprocess.run([sys.executable, "-m", "fme", "extract", "-i", image,
                           "-o", out, "--mode", "both"],
                          cwd=PROJECT, env=env, capture_output=True, text=True,
                          errors="replace", timeout=900)
    path = os.path.join(out, "manifest.json")
    if not os.path.isfile(path):
        return None, done.stdout + done.stderr
    with open(path, encoding="utf-8") as fh:
        return json.load(fh), done.stdout + done.stderr


def unpacked(name):
    image = os.path.join(ROOT, name + ".img")
    with gzip.open(os.path.join(FIXTURES, name + ".img.gz"), "rb") as src, \
            open(image, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return image


try:
    from fme.engines import media_core
    have_tsk = media_core.pytsk3 is not None
except ImportError:
    have_tsk = False
modes = ([("The Sleuth Kit", False)] if have_tsk else []) + [("this program's own readers", True)]
if not have_tsk:
    print("not checked: pytsk3 is not installed, so TSK's reading is not compared")

for name in ("ntfs-windows", "ntfs-deleted", "exfat-deleted", "fat32-deleted"):
    print(f"\n{name}")
    image = unpacked(name)
    want = answers(name)
    found = {}
    for label, no_tsk in modes:
        manifest, log = extract(image, no_tsk)
        if manifest is None:
            check(f"{label}: the run finishes", False, log[-300:])
            continue
        files = manifest["files"]
        by_hash = {f["sha256"]: f for f in files}
        found[label] = set(by_hash)
        names = globals().setdefault("NAMES", {})
        names[label] = {d: (f.get("source_path") or "").lstrip("/") for d, f in by_hash.items()
                        if f.get("engine") != "carve"}
        for path, digest in sorted(want.items()):
            if digest == "-":
                stored = [f for f in files if (f.get("source_path") or "").endswith(path)]
                check(f"{label}: {path} (no data on the volume) is not invented",
                      not stored)
                continue
            rec = by_hash.get(digest)
            check(f"{label}: {path} comes back byte for byte", rec is not None)
            if rec is not None and rec.get("engine") not in ("carve",):
                where = (rec.get("source_path") or "").lstrip("/")
                same = where == path or (rec.get("instances", 1) > 1
                                         and digest in [d for p, d in want.items() if p == where])
                if not same and name.startswith("fat") and rec.get("deleted"):
                    # FAT overwrites the first byte of a deleted entry's name,
                    # and with no long name to recover it from (these were
                    # written as short names with case bits) that letter is
                    # gone. Everything after it must still be right.
                    got, exp = where.split("/"), path.split("/")
                    same = len(got) == len(exp) and all(
                        g[1:] == e[1:] for g, e in zip(got, exp))
                check(f"{label}:   under its path", same, rec.get("source_path"))
        system = [f["source_path"] for f in files if media_core.is_fs_metadata(f.get("source_path", ""))]
        check(f"{label}: no file-system structure is listed as a picture", not system,
              ", ".join(system))
        sidecars = [f["source_path"] for f in files
                    if os.path.basename(f.get("source_path") or "").startswith("._")]
        check(f"{label}: no macOS ._ sidecar is listed as a picture", not sidecars,
              ", ".join(sidecars))
    if len(found) == 2:
        a, b = found.values()
        check("the two readers return the same files", a == b,
              f"{len(a - b)} only by TSK, {len(b - a)} only by this program's")
        x, y = NAMES.values()
        differ = sorted((x[d], y[d]) for d in set(x) & set(y) if x[d] != y[d])
        check("and name each one the same way", not differ, str(differ[:3]))

# Apple volumes are read on a machine with pytsk3 too. The pytsk3 published
# today cannot open an APFS container, and the vendored reader was not asked:
# nobody read it. Neither of these holds a picture; what is checked is that
# some reader walked the volume, which the log and the summary record.
for name, kind, tsk_says in (("apfs-fixture", "apfs", None),
                             ("hfsplus-fixture", "hfsplus", "Mounted HFS")):
    print(f"\n{name} ({kind}, no pictures on it)")
    image = unpacked(name)
    for label, no_tsk in modes:
        manifest, log = extract(image, no_tsk)
        own = (manifest is not None and manifest["summary"].get("vendor_volumes", 0) >= 1) \
            or f"the main engine cannot: {kind}" in log
        by_tsk = (not no_tsk) and tsk_says is not None and tsk_says in log
        check(f"{label}: some reader walks the volume", own or by_tsk,
              "TSK" if by_tsk else "this program's reader" if own else "nobody")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
