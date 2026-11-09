"""
Known-answer tests: the expected result comes from what was written.

Every other test here asks whether this program behaves as this program's
author expected. That catches regressions and nothing else: a reader that
has always been wrong passes every time, and two of our own readers
agreeing proves only that they share an assumption.

A known-answer test is different. A fixture is built by writing known
files to a volume, hashing them **before** anything is deleted, and
recording those hashes beside the image. The test then asks: did the
program return those exact bytes? The answer does not depend on any
reader, ours or anyone's.

This is the beginning of something an examiner can point at when asked
how they know the tool is right. It is not a validation report, and it
should not be described as one. What it is: a record that on a volume
whose contents were known in advance, this program returned them.

**On what counts as independent here.** This program reads NTFS, exFAT,
APFS, HFS+ and ext through `pytsk3`, which is The Sleuth Kit: comparing
that output against `icat` is the same library answering twice, and
proves nothing about whether the library is right. The independent pair
in this tree is the **vendored reader** -- which reads those file systems
with no C extension at all -- against TSK. `FME_NO_TSK=1` takes the
vendored path, and the test runs the fixture both ways and compares. On
FAT32 the two readers are genuinely different code regardless of TSK.

The approach is GLEAPP's (MIT, Alexis Brignoni), whose fixtures record
hashes before deletion and cross-check against The Sleuth Kit at build
time.
"""

import tempfile
import hashlib
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)

FIXTURES = os.path.join(HERE, "fixtures")
# One directory per process. Twenty-seven tests used a
# fixed path here and each wipes it on startup, so two
# runs overlapping -- which happens whenever a batch is
# cut short and its child keeps going -- had the second
# deleting the first's working files mid-run. That is
# the only mechanism found for this suite's intermittent
# failures.
ROOT = os.path.join(tempfile.gettempdir(), "fme_known_answer-") + str(os.getpid())
ok = True
notes = []


def check(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok &= bool(passed)
    print(f"  [{'  works ' if passed else ' FAILED '}] {label}"
          + (f"  -- {detail}" if detail else ""))


def read_intended(path: str) -> list:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            state = parts[3] if len(parts) > 3 else "live"
            rows.append({"sha256": parts[0], "size": int(parts[1]),
                         "name": parts[2], "state": state,
                         # Anything that is not "live" was deleted; APFS
                         # adds a state of its own for blocks the file
                         # system may already have released.
                         "deleted": state != "live"})
    return rows


def run_over(image: str, out_dir: str, env: dict = None, log: list = None) -> list:
    shutil.rmtree(out_dir, ignore_errors=True)
    finished = subprocess.run(
        [sys.executable, "-m", "fme", "extract", "-i", image, "-o", out_dir],
        capture_output=True, text=True, errors="replace", cwd=PROJECT,
        timeout=900, env=dict(os.environ, **(env or {})))
    if log is not None:
        log.extend((finished.stdout + finished.stderr).splitlines())
    manifest = os.path.join(out_dir, "manifest.json")
    if not os.path.isfile(manifest):
        return []
    with open(manifest, encoding="utf-8") as fh:
        return json.load(fh)["files"]


def have(module: str) -> bool:
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def have_tsk() -> bool:
    return shutil.which("icat") is not None and shutil.which("fls") is not None


shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

# ---------------------------------------------------------------------------
# FAT32, built here: tools/make_fixtures/make_fat_deleted.py
# ---------------------------------------------------------------------------
print("a FAT32 volume whose contents were known before the test existed")

image = os.path.join(FIXTURES, "fat-deleted.img")
intended_path = os.path.join(FIXTURES, "fat-deleted.intended")
if not os.path.isfile(image):
    print("  the fixture is not built. Make it with:")
    print("    python tools/make_fixtures/make_fat_deleted.py "
          "tests/fixtures/")
    sys.exit(0)

intended = read_intended(intended_path)
print(f"  {len(intended)} file(s) were written to it, "
      f"{sum(1 for row in intended if row['deleted'])} of them deleted")

recovered = run_over(image, os.path.join(ROOT, "fat"))
got = {row.get("sha256", "") for row in recovered}

for row in intended:
    found = row["sha256"] in got
    what = "deleted" if row["deleted"] else "live"
    check(f"{row['name']} ({what}, {row['size']:,} bytes) comes back "
          f"byte-for-byte", found,
          "" if found else f"sha256 {row['sha256'][:16]}… not among "
                           f"{len(got)} recovered")

# What came back must also be what is on disk, not only what the manifest
# claims -- a manifest is a record of a run, not evidence of one.
out_dir = ""
if recovered:
    with open(os.path.join(ROOT, "fat", "manifest.json"),
              encoding="utf-8") as fh:
        out_dir = json.load(fh)["summary"].get("output_dir", "")
rehashed = set()
for row in recovered:
    path = os.path.join(out_dir, row.get("out_path", ""))
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            rehashed.add(hashlib.sha256(fh.read()).hexdigest())
wanted = {row["sha256"] for row in intended}
check("the files on disk hash to the same values",
      wanted <= rehashed,
      f"{len(wanted & rehashed)} of {len(wanted)} match on disk")

# ---------------------------------------------------------------------------
# A second, independent reader
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# The two readers in this tree, on the same known input
# ---------------------------------------------------------------------------
print("\nthe two readers in this tree, on the same volume")
other = dict(os.environ, FME_NO_TSK="1")
vendored_dir = os.path.join(ROOT, "fat_vendored")
shutil.rmtree(vendored_dir, ignore_errors=True)
subprocess.run([sys.executable, "-m", "fme", "extract", "-i", image,
                "-o", vendored_dir], capture_output=True, cwd=PROJECT,
               timeout=900, env=other)
vendored = set()
manifest = os.path.join(vendored_dir, "manifest.json")
if os.path.isfile(manifest):
    with open(manifest, encoding="utf-8") as fh:
        vendored = {row.get("sha256", "")
                    for row in json.load(fh)["files"]}

check("the vendored reader returns the recorded bytes too",
      wanted <= vendored,
      f"{len(wanted & vendored)} of {len(wanted)}")
check("and the two readers agree with each other",
      (wanted & got) == (wanted & vendored),
      f"{len(wanted & got)} vs {len(wanted & vendored)}")

print("\nagainst a reader from outside this project")
if not have_tsk():
    notes.append("The Sleuth Kit is not installed, so nothing was "
                 "cross-checked. Install sleuthkit and run this again: two "
                 "readers agreeing on a known input is the strongest "
                 "evidence here, and this run does not have it.")
    print("  (The Sleuth Kit is absent -- see the note below)")
else:
    listing = subprocess.run(["fls", "-r", "-p", image],
                             capture_output=True, text=True, timeout=120)
    agreed = disagreed = listed = 0
    for line in listing.stdout.splitlines():
        # "r/r 4:  NAME" for a live file and "r/r * 6:  NAME" for a deleted
        # one. Only the first form was read, so The Sleuth Kit never checked
        # a deleted file -- the files this program exists to recover.
        parts = line.split("\t", 1)[0].split()
        if not parts or not parts[0].startswith("r/"):
            continue
        inode = next((p.rstrip(":") for p in parts[1:] if p.endswith(":")), "")
        inode = inode.split("(")[0]          # "6(realloc)" -> "6"
        if not inode:
            continue
        listed += 1
        blob = subprocess.run(["icat", image, inode],
                              capture_output=True, timeout=120).stdout
        if not blob:
            continue
        digest = hashlib.sha256(blob).hexdigest()
        if digest in wanted:
            agreed += 1
        elif digest in got:
            disagreed += 1
    if not listed:
        # It compared nothing, and said only "0 agreed, 0 differed". Why it
        # listed nothing is the finding, so it is printed.
        why = (listing.stderr.strip().splitlines() or ["no error; it listed no files"])[0]
        check("The Sleuth Kit lists the files on the volume", False,
              f"fls exited {listing.returncode}: {why[:120]}")
    check("The Sleuth Kit returns the same bytes for the same files",
          agreed == len(wanted) and disagreed == 0,
          f"{agreed} of {len(wanted)} agreed, {disagreed} differed, "
          f"{listed} listed")
    if have("pytsk3"):
        notes.append("this machine reads NTFS and the rest through "
                     "pytsk3, which is The Sleuth Kit -- so on those file "
                     "systems the icat comparison is one library "
                     "answering twice. The independent comparison is the "
                     "vendored reader above.")

# ---------------------------------------------------------------------------
# NTFS, which has to be built where mkntfs exists
# ---------------------------------------------------------------------------
print("\nan NTFS volume")
ntfs = os.path.join(FIXTURES, "ntfs-deleted.img")
if not os.path.isfile(ntfs) and os.path.isfile(ntfs + ".gz"):
    # Since v2026.11.306 an NTFS volume Windows 11 itself wrote, and one
    # with deleted resident and non-resident pictures, are read both ways
    # against the operating system's own hashes by test_real_volumes.py.
    print("  checked by tests/test_real_volumes.py (volumes written by "
          "Windows and Linux, read by TSK and by this program's reader)")
elif not os.path.isfile(ntfs):
    notes.append("there is no NTFS fixture, so NTFS -- including a "
                 "resident file, which no carver can reach -- is not "
                 "checked here at all. Build one on a machine with "
                 "ntfsprogs: bash tools/make_fixtures/make_ntfs_deleted.sh "
                 "tests/fixtures/ntfs-deleted.img")
    print("  (not built -- see the note below)")
else:
    intended_ntfs = os.path.join(FIXTURES, "ntfs-deleted.intended")
    rows = read_intended(intended_ntfs)
    with open(intended_ntfs, encoding="utf-8") as fh:
        preamble = fh.read()

    # The builder records whether it could confirm the small file is
    # held inside its MFT record. If it could not, this test still runs
    # but says what it does not know.
    if "resident_confirmed=yes" not in preamble:
        notes.append("the fixture does not confirm that "
                     "photo-resident.jpg is resident, so recovering it "
                     "may not prove the MFT path. Rebuild with "
                     "sleuthkit installed.")

    found = run_over(ntfs, os.path.join(ROOT, "ntfs"))
    by_digest = {row.get("sha256", ""): row for row in found}
    for row in rows:
        got_row = by_digest.get(row["sha256"])
        check(f"{row['name']} ({'deleted' if row['deleted'] else 'live'}) "
              f"comes back byte-for-byte", got_row is not None)

        # A resident file never occupied a cluster, so a carve cannot
        # have found it. If it came back, it came back from the record
        # -- and the engine that supplied it should say so. This is the
        # one assertion in this file that a carver cannot satisfy by
        # luck.
        if got_row and row["name"] == "photo-resident.jpg":
            engine = got_row.get("engine", "")
            # "not carved" is the claim that matters: a resident file
            # never occupied a cluster, so a carver cannot have found
            # it. Which engine did is recorded too, because on a real
            # NTFS volume a deleted file can only have come from the
            # MFT, and a reader of the validation record should see
            # that rather than take it on trust.
            check("and it came from the MFT, not from carving",
                  bool(engine) and "carve" not in engine,
                  f"recovered by {engine or 'an unnamed engine'}")

    # Both readers again, on NTFS: pytsk3 is The Sleuth Kit, the
    # vendored reader is not, and they share no code.
    other_ntfs = dict(os.environ, FME_NO_TSK="1")
    vendor_out = os.path.join(ROOT, "ntfs_vendored")
    shutil.rmtree(vendor_out, ignore_errors=True)
    subprocess.run([sys.executable, "-m", "fme", "extract", "-i", ntfs,
                    "-o", vendor_out], capture_output=True, cwd=PROJECT,
                   timeout=900, env=other_ntfs)
    vendor_manifest = os.path.join(vendor_out, "manifest.json")
    vendor_digests = set()
    if os.path.isfile(vendor_manifest):
        with open(vendor_manifest, encoding="utf-8") as fh:
            vendor_digests = {r.get("sha256", "")
                              for r in json.load(fh)["files"]}
    want_ntfs = {row["sha256"] for row in rows}
    check("the vendored reader returns the same NTFS files",
          want_ntfs <= vendor_digests,
          f"{len(want_ntfs & vendor_digests)} of {len(want_ntfs)}")

# ---------------------------------------------------------------------------
# exFAT -- what SD cards and large phone storage are formatted as
# ---------------------------------------------------------------------------
print("\nan exFAT volume")
exfat = os.path.join(FIXTURES, "exfat-deleted.img")
if not os.path.isfile(exfat) and os.path.isfile(exfat + ".gz"):
    print("  checked by tests/test_real_volumes.py (a volume Linux's exFAT "
          "driver wrote, read by TSK and by this program's reader)")
elif not os.path.isfile(exfat):
    notes.append("there is no exFAT fixture, so exFAT -- what SD cards "
                 "and most large phone storage are formatted as -- is "
                 "not checked. Build one on Linux with exfatprogs: bash "
                 "tools/make_fixtures/make_exfat_deleted.sh "
                 "tests/fixtures/exfat-deleted.img")
    print("  (not built -- see the note below)")
else:
    rows = read_intended(os.path.join(FIXTURES, "exfat-deleted.intended"))
    found = run_over(exfat, os.path.join(ROOT, "exfat"))
    digests = {row.get("sha256", "") for row in found}
    for row in rows:
        check(f"{row['name']} ({'deleted' if row['deleted'] else 'live'}) "
              f"comes back byte-for-byte", row["sha256"] in digests)

    # exFAT keeps a deleted file's name across several directory
    # entries. A reader that reassembles them wrongly loses the name
    # while still returning the bytes, so the name is checked too.
    long_one = [r for r in rows if "long_file_name" in r["name"]]
    if long_one:
        match = [f for f in found
                 if f.get("sha256") == long_one[0]["sha256"]]
        # The whole name, not a word from it: "long" passed a name with its
        # spaces turned to underscores. The .intended file writes spaces as
        # underscores (its columns are split on whitespace), so the recovered
        # name is compared in that form.
        recovered_name = os.path.basename(
            (match[0].get("source_path", "") or "").replace("\\", "/")) if match else ""
        named = recovered_name.replace(" ", "_") == long_one[0]["name"]
        check("a deleted file with a long name keeps its name", named,
              match[0].get("source_path", "") if match else "not found")

    other_exfat = dict(os.environ, FME_NO_TSK="1")
    vendored_exfat = os.path.join(ROOT, "exfat_vendored")
    shutil.rmtree(vendored_exfat, ignore_errors=True)
    subprocess.run([sys.executable, "-m", "fme", "extract", "-i", exfat,
                    "-o", vendored_exfat], capture_output=True,
                   cwd=PROJECT, timeout=900, env=other_exfat)
    seen = set()
    where = os.path.join(vendored_exfat, "manifest.json")
    if os.path.isfile(where):
        with open(where, encoding="utf-8") as fh:
            seen = {r.get("sha256", "") for r in json.load(fh)["files"]}
    want_exfat = {row["sha256"] for row in rows}
    check("the vendored reader returns the same exFAT files",
          want_exfat <= seen,
          f"{len(want_exfat & seen)} of {len(want_exfat)}")

# ---------------------------------------------------------------------------
# APFS and HFS+ -- Mac volumes, built on a Mac
# ---------------------------------------------------------------------------
for kind, stem, label in (("hfs", "hfsplus-deleted", "an HFS+ volume"),
                          ("apfs", "apfs-deleted", "an APFS volume")):
    print(f"\n{label}")
    mac_image = os.path.join(FIXTURES, f"{stem}.img")
    if not os.path.isfile(mac_image):
        notes.append(f"there is no {label.split()[1]} fixture, so that "
                     f"file system is not checked. Build one on a Mac: "
                     f"bash tools/make_fixtures/make_mac_deleted.sh "
                     f"{kind}")
        print("  (not built -- see the note below)")
        continue

    rows = read_intended(os.path.join(FIXTURES, f"{stem}.intended"))
    found = run_over(mac_image, os.path.join(ROOT, stem))
    digests = {row.get("sha256", "") for row in found}

    for row in rows:
        back = row["sha256"] in digests
        # APFS is copy-on-write and trims: a deleted file's blocks may
        # genuinely be gone, which is the file system behaving as it
        # does rather than the reader being wrong. Those are reported
        # and not failed on; everything else is held to the usual
        # standard.
        soft = row.get("state", "") == "deleted-may-be-gone"
        if soft and not back:
            notes.append(f"{row['name']} was not recovered from "
                         f"{stem}.img. It is marked "
                         f"deleted-may-be-gone: APFS may have released "
                         f"its blocks before the image was taken, so "
                         f"this is not counted as a failure. It is also "
                         f"not evidence the reader is right.")
            print(f"  [not run ] {row['name']} -- deleted under APFS and "
                  f"not recovered; see the note")
            continue
        check(f"{row['name']} ({row.get('state', '')}) comes back "
              f"byte-for-byte", back)

# ---------------------------------------------------------------------------
# E01 -- the format every case arrives in, and until now never read by a test
# ---------------------------------------------------------------------------
print("\nan E01 acquisition")
e01 = os.path.join(FIXTURES, "known.E01")
if not os.path.isfile(e01):
    print("  the fixture is not built. Make it with:")
    print("    python tools/make_fixtures/make_e01.py tests/fixtures/")
else:
    rows = read_intended(os.path.join(FIXTURES, "known.intended"))

    # Read by each reader on its own and held to each separately. One run
    # used "whichever the engine chose": on a machine with libewf that was
    # libewf, which opens this fixture and reads only zeros from it, so the
    # test failed on every validation run there while saying nothing of why
    # -- and the engine, until v2026.11.289, carved the zeros without a word.
    print("  by the vendored reader")
    found = run_over(e01, os.path.join(ROOT, "e01"), {"FME_NO_LIBEWF": "1"})
    digests = {row.get("sha256", "") for row in found}
    for row in rows:
        check(f"{row['name']} ({row['size']:,} bytes) comes back "
              f"byte-for-byte from the acquisition",
              row["sha256"] in digests)

    if not have("pyewf"):
        notes.append("libewf is not installed, so this E01 was read by "
                     "the vendored reader alone. A machine with "
                     "libewf-python reads it by the other path too.")
    else:
        print("  as a case reads it, with libewf installed")
        log = []
        found = run_over(e01, os.path.join(ROOT, "e01-libewf"), log=log)
        digests = {row.get("sha256", "") for row in found}
        took_over = any("vendored reader" in line for line in log)
        # Whichever read it, a case must get every picture back.
        for row in rows:
            check(f"{row['name']} comes back from the run a case would make",
                  row["sha256"] in digests)
        if took_over:
            # The engine noticed libewf read nothing and changed reader --
            # the behaviour that matters. libewf itself is unproven by this
            # fixture, which the project packed and libewf does not read.
            check("and the log says libewf was not the reader",
                  any("libewf" in line and "vendored" in line for line in log))
            notes.append("libewf opened this fixture and read nothing from "
                         "it, and the run went to the vendored reader and "
                         "said so. That proves the fall-back, not libewf: "
                         "the fixture is written by this project, and "
                         "libewf does not read it. libewf is proved by a "
                         "real acquisition -- run an E01 from a closed case "
                         "and compare its files with the case's records.")
        else:
            check("and libewf was the reader",
                  any("read by libewf" in line for line in log))

for note in notes:
    print(f"\nnot checked: {note}")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
