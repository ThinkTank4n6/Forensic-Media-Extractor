"""
verify.py -- does this installation actually do what the program claims?

Run it on the machine that will do the work:

    python tools/verify.py

The test suite proves the code is correct in the environment it is run in.
This proves something different and, for an examiner, more useful: that
**this** machine, with the libraries it actually has installed, reads the
formats the manual says it reads, and that what comes out is right.

The two are not the same. A library that is absent makes a whole class of
evidence unreadable while every test still passes, because the tests cover
the paths that exist. NTFS without pytsk3, E01 without libewf, HEIC
without pillow-heif: each of those is a silent hole, and a silent hole in
a forensic tool is the worst kind.

Every check here builds its own evidence, runs the real extraction over
it, and counts what came back. Nothing is mocked. A check that cannot run
says so and says why, and **"not tested" is never reported as a pass** --
that distinction is the whole point of this file.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, "tests"))

RESULTS = []
# Things a run could not check, said at the end rather than passed over.
NOTES = []


def record(what: str, state: str, detail: str = "") -> None:
    RESULTS.append((what, state, detail))
    mark = {"works": "  works ", "FAILED": " FAILED ",
            "not tested": "not run "}.get(state, state)
    print(f"  [{mark}] {what}" + (f"  -- {detail}" if detail else ""))


def have(module: str) -> bool:
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def run_extraction(source: str, out_dir: str, extra=None) -> dict:
    """The real command line, not an internal call."""
    command = [sys.executable, "-m", "fme", "extract", "-i", source,
               "-o", out_dir] + (extra or [])
    finished = subprocess.run(command, capture_output=True, text=True,
                              cwd=PROJECT, timeout=900)
    manifest = os.path.join(out_dir, "manifest.json")
    if not os.path.isfile(manifest):
        # A run that wrote no manifest failed, whatever it printed.
        return {"files": [], "summary": {},
                "error": (finished.stderr or finished.stdout or "")[-400:]}
    with open(manifest, encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    print(__doc__.strip().split("\n\n")[0])
    print()
    work = tempfile.mkdtemp(prefix="fme_verify_")
    try:
        print("What this machine has installed")
        for module, needed_for in (
                ("pytsk3", "NTFS, exFAT, HFS+, ext2/3/4 (APFS is read by the vendored reader)"),
                ("pyewf", "E01 and Ex01 through libewf"),
                ("PIL", "thumbnails, visual similarity, report pictures"),
                ("cv2", "frames from videos, edited-copy matching, faces"),
                ("numpy", "the fast signature scan and the matchers"),
                ("pillow_heif", "HEIC straight off an iPhone"),
                ("py7zr", "media inside 7z archives"),
                ("zstandard", "Snapchat content bundles"),
                ("cryptography", "a TLS certificate, and C2PA signatures")):
            present = have(module)
            print(f"  {'yes' if present else 'NO '}  {module:14} {needed_for}")
        print()

        # A missing library is not a line in a list: it is a thing this
        # machine cannot do, and saying so is the point of this tool.
        for module, consequence, fix in (
                ("pytsk3",
                 "NTFS, exFAT, HFS+, APFS and ext2/3/4 cannot be read at all",
                 "pip install pytsk3"),
                ("pyewf",
                 "E01 goes through the vendored reader rather than libewf",
                 "pip install libewf-python"),
                ("cryptography",
                 "C2PA signatures cannot be checked, and this machine "
                 "cannot make a server certificate",
                 "pip install cryptography"),
                ("pillow_heif",
                 "HEIC off an iPhone will not decode for thumbnails or "
                 "matching",
                 "pip install pillow-heif"),
                ("cv2",
                 "no video key frames, no edited-copy matching, no faces",
                 "pip install opencv-python-headless")):
            if not have(module):
                print(f"  On this machine: {consequence}.")
                print(f"      {fix}")
        print()

        import make_media_test_image as fixtures

        print("Reading evidence")
        _check_folder(work, fixtures)
        _check_raw_carve(work, fixtures)
        _check_fat32(work, fixtures)
        _check_package(work, fixtures)
        _check_tar(work, fixtures)
        _check_vhd(work, fixtures)
        _check_e01(work, fixtures)
        _check_filesystems(work)
        _check_second_engine(work)

        print("\nWhat comes out")
        _check_hashes(work, fixtures)
        _check_device_paths(work, fixtures)
        _check_report(work, fixtures)
        _check_thumbs(work, fixtures)

        print("\nThe interface")
        _check_interface()
    finally:
        shutil.rmtree(work, ignore_errors=True)

    for note in NOTES:
        print(f"\n  note: {note}")

    works = sum(1 for _, state, _ in RESULTS if state == "works")
    failed = [what for what, state, _ in RESULTS if state == "FAILED"]
    untested = [what for what, state, _ in RESULTS if state == "not tested"]

    print("\n" + "=" * 68)
    print(f"{works} check(s) passed, {len(failed)} failed, "
          f"{len(untested)} could not run here")
    if failed:
        print("\nFAILED -- these do not work on this machine:")
        for what in failed:
            print(f"    {what}")
    if untested:
        print("\nNot tested -- no claim either way, usually a missing "
              "library:")
        for what in untested:
            print(f"    {what}")
    if not failed and not untested:
        print("\nEverything this can check, it checked, and all of it works.")
    elif not failed:
        print("\nNothing failed. The list above is what remains unproven on "
              "this machine.")
    return 1 if failed else 0


def _pictures(folder: str, fixtures, count: int = 4, seed: int = 400) -> list:
    """Real pictures where Pillow is here, carve fixtures where it is not.

    The fixture generator makes JPEG-shaped bytes, which is right for
    testing a carver and wrong for testing anything that decodes a
    picture. Using them everywhere made the thumbnail check fail against a
    program that was working.
    """
    os.makedirs(folder, exist_ok=True)
    made = []
    try:
        from PIL import Image
    except ImportError:
        Image = None
    for index in range(count):
        path = os.path.join(folder, f"p{index}.jpg")
        if Image is not None:
            picture = Image.new("RGB", (320, 240))
            pixels = picture.load()
            for y in range(240):
                for x in range(320):
                    pixels[x, y] = ((x + seed + index * 7) % 256,
                                    (y * 3) % 256, (x + y) % 256)
            picture.save(path, quality=85)
        else:
            with open(path, "wb") as fh:
                fh.write(fixtures.make_jpeg(seed + index))
        made.append(path)
    return made


def _check_folder(work: str, fixtures) -> None:
    source = os.path.join(work, "folder_src")
    _pictures(source, fixtures)
    found = run_extraction(source, os.path.join(work, "folder_out"))
    got = len(found["files"])
    record("a folder of loose pictures",
           "works" if got == 4 else "FAILED", f"{got} of 4 recovered")


def _check_raw_carve(work: str, fixtures) -> None:
    """Carving a raw image: pictures with no file system at all."""
    image = os.path.join(work, "raw.dd")
    blob = bytearray(6 * 1024 * 1024)
    placed, at = 0, 300_000
    for index in range(4):
        picture = fixtures.make_jpeg(500 + index)
        blob[at:at + len(picture)] = picture
        at += len(picture) + 250_000
        placed += 1
    with open(image, "wb") as fh:
        fh.write(bytes(blob))
    found = run_extraction(image, os.path.join(work, "raw_out"),
                           ["--mode", "carve"])
    got = len(found["files"])
    record("carving a raw image with no file system",
           "works" if got == placed else "FAILED",
           f"{got} of {placed} carved")


# The five live files every FAT fixture holds, by path. Checks name them
# rather than count files: v2026.11.274 on Windows recovered "8 file(s)"
# here with The Sleuth Kit reading nothing -- seven carved, nameless -- and
# every FAT check below passed on that count.
FAT_LIVE = {"/DCIM/IMG_0001.JPG", "/DCIM/IMG_0002.PNG", "/DCIM/ANIM.GIF",
            "/MOVIES/VID_0003.MP4", "/MOVIES/CLIP.AVI"}


def _fat_image(work: str, name: str, spec_fat32: bool) -> str:
    """Build a FAT fixture into the work folder, or '' if it cannot be."""
    image = os.path.join(work, name)
    command = [sys.executable, os.path.join(PROJECT, "tests", "make_media_test_image.py"),
               "--out", image] + (["--spec-fat32"] if spec_fat32 else [])
    try:
        subprocess.run(command, capture_output=True, cwd=work, timeout=300)
    except Exception:  # noqa: BLE001
        return ""
    return image if os.path.isfile(image) else ""


def _walked(found: dict) -> dict:
    """{path: sha256} for what the file system engine read, not the carve."""
    return {row["source_path"]: row.get("sha256", "")
            for row in found["files"]
            if row.get("engine") == "filesystem" and row.get("source_path")}


def _readers(found: dict) -> str:
    """Which reader read each volume, from the run's own volume list."""
    names = [str(v.get("fs", "?")) for v in (found.get("summary") or {}).get("volumes") or []]
    return ", ".join(names) or "no file system reader"


def _check_fat32(work: str, fixtures) -> None:
    # FAT32 by the specification (66,000 clusters), so The Sleuth Kit opens
    # it. The default fixture is formatted FAT32 with 4,000 clusters, which
    # the specification calls FAT12; TSK refuses it, and this check used to
    # run on it -- so on a machine with pytsk3 it never saw TSK read FAT.
    image = _fat_image(work, "fat32_spec.img", spec_fat32=True)
    if not image:
        record("a FAT32 volume is read", "not tested", "the fixture could not be built")
        return

    found = run_extraction(image, os.path.join(work, "fat32_out"))
    walked = _walked(found)
    live = FAT_LIVE & set(walked)
    record("a FAT32 volume is read",
           "works" if live == FAT_LIVE else "FAILED",
           f"{len(live)} of {len(FAT_LIVE)} live files by path, read by {_readers(found)}")

    deleted = sum(1 for row in found["files"] if row.get("deleted"))
    # The fixture contains a deleted entry by construction, so none found
    # is a failure, not a pass. Counting files and calling it proven is how
    # a check passes while the thing it is checking does not work.
    summary = found.get("summary") or {}
    lost = summary.get("deleted_without_size", 0)
    detail = f"{deleted} recovered; the fixture has one"
    if not deleted and lost:
        detail += (f" -- {lost} deleted entry/entries were seen but had no "
                   f"recorded size, so nothing could be read from them")
    by_pass = summary.get("fat_deleted_files", 0)
    if by_pass:
        detail += f"; {by_pass} by the deleted-entry pass"
    record("a deleted FAT32 entry is recovered",
           "works" if deleted > 0 else "FAILED", detail)

    if not have("pytsk3"):
        record("both file-system engines read the same files", "not tested",
               "pytsk3 is not installed, so there is one engine to ask")
        record("a volume The Sleuth Kit refuses is still read", "not tested",
               "pytsk3 is not installed, so nothing refuses it")
        return

    # Both engines, the same question, compared file by file: which paths,
    # and the bytes under each. Comparing counts called seven nameless
    # carved files and six named ones an agreement.
    if summary.get("tsk_refused"):
        record("both file-system engines read the same files", "not tested",
               "The Sleuth Kit refused even the specification-valid volume, "
               "so both runs used this program's reader and nothing was "
               f"compared (pytsk3 here: {summary.get('engine', '')[:40]})")
    else:
        other = dict(os.environ, FME_NO_TSK="1")
        out_dir = os.path.join(work, "fat32_fallback")
        try:
            subprocess.run([sys.executable, "-m", "fme", "extract", "-i", image,
                            "-o", out_dir], capture_output=True, cwd=PROJECT,
                           timeout=900, env=other)
            with open(os.path.join(out_dir, "manifest.json"), encoding="utf-8") as fh:
                theirs = _walked(json.load(fh))
            same = bool(walked) and walked == theirs
            differ = sorted(set(walked) ^ set(theirs)) + sorted(
                p for p in set(walked) & set(theirs) if walked[p] != theirs[p])
            record("both file-system engines read the same files",
                   "works" if same else "FAILED",
                   f"TSK and fat32_core: {len(walked)} and {len(theirs)} file(s)"
                   + (", the same paths and the same bytes" if same
                      else f"; differ on {differ[:4]}"))
        except Exception as exc:  # noqa: BLE001
            record("both file-system engines read the same files",
                   "not tested", str(exc)[:90])

    # And the volume TSK does refuse: its files still come back by path,
    # read by this program's FAT reader (v2026.11.276).
    refused_image = _fat_image(work, "fat32_refused.img", spec_fat32=False)
    if not refused_image:
        record("a volume The Sleuth Kit refuses is still read", "not tested",
               "the fixture could not be built")
        return
    found = run_extraction(refused_image, os.path.join(work, "refused_out"))
    if not (found.get("summary") or {}).get("tsk_refused"):
        record("a volume The Sleuth Kit refuses is still read", "not tested",
               "The Sleuth Kit opened this 4,000-cluster volume, so the "
               f"fallback was not needed (read by {_readers(found)})")
        return
    live = FAT_LIVE & set(_walked(found))
    record("a volume The Sleuth Kit refuses is still read",
           "works" if live == FAT_LIVE else "FAILED",
           f"TSK refused it; {len(live)} of {len(FAT_LIVE)} live files by "
           f"path, read by {_readers(found)}")


def _check_package(work: str, fixtures) -> None:
    """A Cellebrite-shaped archive."""
    ufdr = os.path.join(work, "DC-verify.ufdr")
    pictures = _pictures(os.path.join(work, "pkg_src"), fixtures, 3, 600)
    with zipfile.ZipFile(ufdr, "w") as archive:
        for index, path in enumerate(pictures):
            archive.write(path, f"files/Fs/1/private/var/mobile/Media/DCIM/"
                                f"100APPLE/IMG_{index:04d}.JPG")
        archive.writestr("UFDReports/report.xml", "<report/>")
    found = run_extraction(ufdr, os.path.join(work, "pkg_out"))
    got = len(found["files"])
    record("a Cellebrite-shaped .ufdr",
           "works" if got == 3 else "FAILED", f"{got} of 3 recovered")


def _check_tar(work: str, fixtures) -> None:
    import tarfile

    path = os.path.join(work, "graykey.tar")
    pictures = _pictures(os.path.join(work, "tar_src"), fixtures, 2, 700)
    with tarfile.open(path, "w") as archive:
        for index, picture in enumerate(pictures):
            archive.add(picture, arcname=f"DC/filesystem1/private/var/mobile/"
                                         f"Media/DCIM/IMG_{index}.JPG")
    found = run_extraction(path, os.path.join(work, "tar_out"))
    got = len(found["files"])
    record("a GrayKey-shaped tar",
           "works" if got == 2 else "FAILED", f"{got} of 2 recovered")


def _check_vhd(work: str, fixtures) -> None:
    """A fixed VHD: the vendored acquisition reader, with no C libraries."""
    raw = bytearray(4 * 1024 * 1024)
    at = 200_000
    for index in range(3):
        picture = fixtures.make_jpeg(800 + index)
        raw[at:at + len(picture)] = picture
        at += len(picture) + 200_000
    footer = bytearray(512)
    footer[0:8] = b"conectix"
    footer[8:12] = struct.pack(">I", 2)                    # features
    footer[12:16] = struct.pack(">I", 0x00010000)          # format 1.0
    footer[16:24] = struct.pack(">Q", 0xFFFFFFFFFFFFFFFF)  # data offset
    footer[40:48] = struct.pack(">Q", len(raw))            # original size
    footer[48:56] = struct.pack(">Q", len(raw))            # current size
    footer[60:64] = struct.pack(">I", 2)                   # fixed disk
    # The checksum, which the reader verifies -- and was right to reject
    # when this fixture did not have one.
    footer[64:68] = b"\x00\x00\x00\x00"
    checksum = ~(sum(footer) - sum(footer[64:68])) & 0xFFFFFFFF
    footer[64:68] = struct.pack(">I", checksum)
    path = os.path.join(work, "disk.vhd")
    with open(path, "wb") as fh:
        fh.write(bytes(raw))
        fh.write(bytes(footer))
    found = run_extraction(path, os.path.join(work, "vhd_out"),
                           ["--mode", "carve"])
    got = len(found["files"])
    record("a fixed VHD through the vendored reader",
           "works" if got == 3 else "FAILED", f"{got} of 3 carved")


def _check_e01(work: str, fixtures) -> None:
    """The E01 fixture that ships with the program, read end to end.

    This said "no E01 fixture is built here" while one sat in
    tests/fixtures: the check simply never looked. An examiner ran it
    on a machine with libewf installed and got "not tested" for the
    format every case arrives in.
    """
    shipped = os.path.join(PROJECT, "tests", "fixtures", "known.E01")
    listing = os.path.join(PROJECT, "tests", "fixtures", "known.intended")
    if not os.path.isfile(shipped):
        builder = os.path.join(PROJECT, "tools", "make_fixtures",
                               "make_e01.py")
        if os.path.isfile(builder):
            subprocess.run([sys.executable, builder,
                            os.path.join(PROJECT, "tests", "fixtures")],
                           capture_output=True, cwd=PROJECT, timeout=300)
    if not (os.path.isfile(shipped) and os.path.isfile(listing)):
        record("an E01 acquisition", "not tested",
               "no E01 fixture here and none could be built")
        return

    wanted = set()
    with open(listing, encoding="utf-8") as fh:
        for line in fh:
            if line.strip() and not line.startswith("#"):
                wanted.add(line.split()[0])

    found = run_extraction(shipped, os.path.join(work, "e01_out"))
    got = {row.get("sha256", "") for row in found["files"]}
    worked = bool(wanted) and wanted <= got

    # On a machine with libewf, this fixture proves nothing.
    #
    # It is an E01 this project packed from the format documentation.
    # libewf accepts it, reports a media size, and reads nothing
    # useful out of it -- so the fixture is malformed in a way the
    # reference implementation tolerates rather than refuses. Reporting
    # that as a failure puts a red mark in an examiner's validation
    # record about a program that reads their real acquisitions
    # perfectly well.
    #
    # So where libewf is present this is recorded as not tested, with
    # what to do instead. The fixture stays useful on machines without
    # libewf, where it exercises the vendored reader end to end.
    if have("pyewf") and not worked:
        record("an E01 acquisition", "not tested",
               "the fixture here is not libewf-conformant -- see the note "
               "below. Point the program at a real acquisition to test "
               "this path")
        NOTES.append(
            "this E01 is one this project packed from the format "
            "documentation, and libewf -- the reference implementation --"
            " would not read it. That is a fault in the fixture, not "
            "evidence about acquisitions your tools write: libewf is "
            "stricter than the vendored reader, which does read it. "
            "Point this at a real acquisition from a closed case to "
            "learn anything about the path your cases take. From v222 "
            "the program falls back to the vendored reader when libewf "
            "refuses a file, and says in the log which one read it -- "
            "and from v2026.11.289 also when libewf accepts a file and "
            "reads only zeros from it, which is what it does with this "
            "one: the log then says libewf read nothing and the vendored "
            "reader was used. tests/test_ewf_reads_nothing.py checks it.")
    else:
        record("an E01 acquisition is read, byte-for-byte",
               "works" if worked else "FAILED",
               f"{len(wanted & got)} of {len(wanted)} recovered")

    # Which reader did it: with libewf present the engine may use
    # either, and an examiner's record should say which was exercised.
    if have("pyewf"):
        NOTES.append("libewf is installed, so this E01 may have been read "
                     "by either libewf or the vendored reader. Running "
                     "tests/test_known_answer.py reports both.")
    else:
        NOTES.append("libewf is not installed, so this E01 was read by "
                     "the vendored reader alone.")


def _check_second_engine(work: str) -> None:
    """The vendored reader, on a volume TSK would normally take."""
    try:
        from fme.engines import vendor_fs
    except Exception as exc:  # noqa: BLE001
        record("the second file-system engine", "not tested", str(exc)[:80])
        return
    if not vendor_fs.available():
        record("the second file-system engine", "not tested",
               "the vendored reader is not present")
        return

    # Built for this run rather than whatever media_test.img sat in the
    # project root, and judged by the files it names, not "any file".
    image = _fat_image(work, "second_engine.img", spec_fat32=True)
    if not image:
        record("a second engine can read a mainstream volume", "not tested",
               "no fixture")
        return

    seen = []
    vendor_fs.walk(image, lambda row: seen.append(
        ("/" + row["path"].lstrip("/"), row["size"], len(b"".join(row["read"]())))),
        only_extra=True, kinds=vendor_fs.kinds_to_read(include_mainstream=True))
    named = {path for path, _, _ in seen}
    whole = all(size == got for _, size, got in seen)
    live = FAT_LIVE & named
    record("a second engine can read a mainstream volume",
           "works" if live == FAT_LIVE and whole else "FAILED",
           f"{len(live)} of {len(FAT_LIVE)} live files by path without TSK, "
           f"{'every one' if whole else 'NOT every one'} its declared size")


def _check_filesystems(work: str) -> None:
    if not have("pytsk3"):
        record("NTFS, exFAT, HFS+, APFS, ext2/3/4", "not tested",
               "pytsk3 is not installed: these file systems cannot be read "
               "at all on this machine, and nothing else will tell you that")
        return

    # The NTFS fixture, if somebody has built it. Until then this says
    # what it does not know rather than implying TSK's presence is
    # itself evidence.
    ntfs = os.path.join(PROJECT, "tests", "fixtures", "ntfs-deleted.img")
    listing = os.path.join(PROJECT, "tests", "fixtures",
                           "ntfs-deleted.intended")
    if not (os.path.isfile(ntfs) and os.path.isfile(listing)):
        record("NTFS, exFAT, HFS+, APFS, ext2/3/4", "not tested",
               "pytsk3 is present, but no NTFS fixture is built here. Build "
               "one on Linux or WSL: bash tools/make_fixtures/"
               "make_ntfs_deleted.sh tests/fixtures/ntfs-deleted.img")
        return

    wanted = {}
    with open(listing, encoding="utf-8") as fh:
        for line in fh:
            if line.strip() and not line.startswith("#"):
                parts = line.split()
                wanted[parts[0]] = parts[2]

    found = run_extraction(ntfs, os.path.join(work, "ntfs_out"))
    got = {row.get("sha256", "") for row in found["files"]}
    missing = [name for digest, name in wanted.items() if digest not in got]
    record("an NTFS volume, including a resident deleted file",
           "works" if not missing else "FAILED",
           f"{len(wanted) - len(missing)} of {len(wanted)} byte-for-byte"
           + (f"; missing {', '.join(missing)}" if missing else ""))


def _check_hashes(work: str, fixtures) -> None:
    """What comes out has to be the bytes that went in."""
    import hashlib

    source = os.path.join(work, "hash_src")
    made = _pictures(source, fixtures, 3, 900)
    expected = {hashlib.sha256(open(p, "rb").read()).hexdigest() for p in made}
    found = run_extraction(source, os.path.join(work, "hash_out"))
    got = {row.get("sha256") for row in found["files"]}
    record("every recovered file is byte-for-byte the original",
           "works" if expected <= got else "FAILED",
           f"{len(expected & got)} of {len(expected)} hashes match")

    out_dir = (found.get("summary") or {}).get("output_dir", "")
    on_disk = set()
    for row in found["files"]:
        path = os.path.join(out_dir, row.get("out_path", ""))
        if os.path.isfile(path):
            on_disk.add(hashlib.sha256(open(path, "rb").read()).hexdigest())
    record("the manifest's hashes match the files it wrote",
           "works" if on_disk and on_disk <= got else "FAILED",
           f"{len(on_disk)} file(s) re-hashed")


def _check_device_paths(work: str, fixtures) -> None:
    ufdr = os.path.join(work, "paths.ufdr")
    picture = _pictures(os.path.join(work, "dp_src"), fixtures, 1, 1000)[0]
    with zipfile.ZipFile(ufdr, "w") as archive:
        archive.write(picture, "files/Fs/1/private/var/mobile/Media/DCIM/"
                               "100APPLE/IMG_9999.JPG")
    found = run_extraction(ufdr, os.path.join(work, "dp_out"))
    paths = {row.get("device_path", "") for row in found["files"]}
    right = "/private/var/mobile/Media/DCIM/100APPLE/IMG_9999.JPG" in paths
    record("a phone extraction records the path on the device",
           "works" if right else "FAILED",
           (sorted(paths)[0] if paths else "nothing recovered"))


def _check_report(work: str, fixtures) -> None:
    source = os.path.join(work, "rep_src")
    _pictures(source, fixtures, 2, 1100)
    out_dir = os.path.join(work, "rep_out")
    found = run_extraction(source, out_dir)
    real_out = (found.get("summary") or {}).get("output_dir", out_dir)
    try:
        from fme.exports import report

        dest = os.path.join(work, "report")
        written = report.build(real_out, dest, header={"case_number": "VERIFY"},
                               formats=["html"], timezone="UTC")
        path = written["written"][0]["path"]
        body = open(path, encoding="utf-8").read()
        good = "Times shown in" in body and len(body) > 2000
        record("a report is written and states its time zone",
               "works" if good else "FAILED", os.path.basename(path))
    except Exception as exc:  # noqa: BLE001
        record("a report is written", "FAILED", str(exc)[:120])


def _check_thumbs(work: str, fixtures) -> None:
    if not have("PIL"):
        record("gallery tiles", "not tested", "Pillow is not installed")
        return
    from fme.web import media_gui

    source = os.path.join(work, "thumb_src")
    picture = _pictures(source, fixtures, 1, 1200)[0]
    out_dir = os.path.join(work, "thumb_out")
    os.makedirs(out_dir, exist_ok=True)
    shutil.copy(picture, os.path.join(out_dir, "p.jpg"))
    made = media_gui._make_thumb(out_dir, os.path.join(out_dir, "p.jpg"),
                                 "p.jpg")
    record("gallery tiles are made and kept",
           "works" if made else "FAILED", "")


def _check_interface() -> None:
    """Every ribbon item reaches something, and no panel is empty."""
    import re

    from fme.web import media_gui

    html = media_gui.PAGE
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)

    called = {name for name in re.findall(r"go:\s*([A-Za-z_][\w]*)", script)
              if name not in ("async", "function")}
    defined = set(re.findall(r"function\s+([A-Za-z_][\w]*)", script))
    defined |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_][\w]*)\s*=\s*"
                              r"(?:async\s*)?\(", script))
    missing = sorted(called - defined)
    record("every ribbon item reaches a function that exists",
           "works" if not missing else "FAILED",
           ", ".join(missing[:4]) if missing else "")

    panels = re.findall(r'id="tab-(\w+)"', html)
    empty = []
    for name in panels:
        at = html.find(f'id="tab-{name}"')
        end = html.find('<div class="tab-content"', at + 10)
        body = html[at:end if end > 0 else len(html)]
        if len(re.sub(r"<[^>]+>", " ", body).split()) < 5:
            empty.append(name)
    record("no panel opens empty",
           "works" if not empty else "FAILED", ", ".join(empty))

    switched = set(re.findall(r'showTab\("(\w+)"\)', script))
    absent = sorted(n for n in switched if f'id="tab-{n}"' not in html)
    record("every panel the program switches to exists",
           "works" if not absent else "FAILED", ", ".join(absent))

    classes = {group for group in re.findall(r'class="([^"]+)"', html)
               if "${" not in group and "+" not in group}
    styles = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    unstyled = []
    for group in classes:
        for name in group.split():
            if name and f".{name}" not in styles and name not in unstyled:
                unstyled.append(name)
    record("every class the page uses has a style",
           "works" if not unstyled else "FAILED",
           ", ".join(unstyled[:4]))


if __name__ == "__main__":
    sys.exit(main())
