"""Encrypted volumes open with the examiner's key, and the key is never kept.

What is checked, on real volumes (tests/fixtures/*.intended says where each
comes from):

- a BitLocker volume opens with its startup key file, and every engine --
  including a parallel carve's worker processes -- reads it as plaintext;
- an APFS volume FileVault encrypted opens with its password, and its files
  are read decrypted, matching the hashes recorded when it was made;
- a key that opens nothing leaves the volume reported locked, and says so;
- a run that read decrypted volumes cannot be resumed without the key;
- the report says which volume was opened and with what kind of key;
- the password and the key file's bytes appear nowhere in a finished case,
  its reports, its log, or what the interface hands back to the page;
- there is no --password VALUE, and a key this installation cannot use is
  refused before anything is read.

The readers decrypt with pycryptodome. Where it is not installed, the ciphers
come from tests/support/pycryptodome_standin (the cryptography package behind
pycryptodome's interface) and the run says so: on a machine with pycryptodome,
which requirements.txt asks for, it is the real one that is tested.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder
import glob
import gzip
import hashlib
import io
import json
import logging
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
try:
    import Crypto.Cipher.AES  # noqa: F401
    STANDIN = False
except ImportError:
    standin = os.path.join(HERE, "support", "pycryptodome_standin")
    sys.path.insert(0, standin)
    os.environ["PYTHONPATH"] = standin + os.pathsep + os.environ.get("PYTHONPATH", "")
    STANDIN = True

from fme.engines import media_core, unlock, vendor_fs  # noqa: E402
from fme.exports import report  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
PASSWORD = "qnxprobe-apfs-convert"          # the FileVault fixture's, see its .intended
# Wrong ones, distinctive enough that finding one in a case means it leaked.
WRONG = ("wrong-Pw-7731-a", "wrong-Pw-7731-b")
BEK = os.path.join(FIX, "bitlocker-xts128.BEK")
ok = True
LOG_TEXT = io.StringIO()


def check(what, passed):
    global ok
    ok &= bool(passed)
    print(f"  {what}: {bool(passed)}")


def case_folder(out_dir):
    if os.path.isfile(os.path.join(out_dir, "manifest.csv")):
        return out_dir
    found = glob.glob(os.path.join(out_dir, "**", "manifest.csv"), recursive=True)
    return os.path.dirname(found[0]) if found else out_dir


def main():
    root = os.path.join(tempfile.gettempdir(), "fme_unlock-") + str(os.getpid())
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    handler = logging.StreamHandler(LOG_TEXT)
    handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.DEBUG)

    if not vendor_fs.available():
        print("not checked: the vendored reader is not installed here")
        return 0
    if STANDIN:
        print("pycryptodome is not installed here: decrypting with the stand-in "
              "in tests/support -- pycryptodome itself not checked")

    def unpack(name, ext):
        path = os.path.join(root, name + ext)
        with gzip.open(os.path.join(FIX, name + ext + ".gz")) as src, open(path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return path

    bitlocker = unpack("bitlocker-xts128", ".img")
    apfs = unpack("apfs-filevault", ".sparseimage")
    with open(BEK, "rb") as fh:
        bek_bytes = fh.read()
    bek_sha = hashlib.sha256(bek_bytes).hexdigest()
    outputs = []

    print("\nthe credentials themselves")
    creds = unlock.Credentials([PASSWORD], [BEK])
    check(f"they print as a count ({creds!r})", PASSWORD not in repr(creds) + str(creds))
    again = pickle.loads(pickle.dumps(creds))
    check("they reach a worker process intact",
          again.passwords == (PASSWORD,) and again.key_files == (BEK,))

    print("\nthe command line")
    errors = io.StringIO()
    old_stderr, sys.stderr = sys.stderr, errors
    try:
        media_core.main(["-i", apfs, "-o", os.path.join(root, "cli"), "--password", "secret"])
        refused = False
    except SystemExit as exc:
        refused = exc.code == 2
    finally:
        sys.stderr = old_stderr
    check("--password VALUE is refused, saying why",
          refused and "shell's history" in errors.getvalue())
    code = media_core.main(["-i", apfs, "-o", os.path.join(root, "cli"),
                            "--key-file", os.path.join(root, "no-such.BEK")])
    check("a key file that is not there stops the run", code == 2)
    password_file = os.path.join(root, "pw.txt")
    with open(password_file, "w", encoding="utf-8") as fh:
        fh.write(WRONG[0] + "\n\n" + PASSWORD + "\n")
    got = unlock.from_files([password_file])
    check("--password-file reads one per line, blank lines skipped",
          got.passwords == (WRONG[0], PASSWORD))

    print("\na key this installation cannot use")
    real = unlock.available
    unlock.available = lambda: False
    try:
        media_core.run_extraction(apfs, os.path.join(root, "nocrypto"),
                                  credentials=unlock.Credentials([PASSWORD]))
        said = ""
    except unlock.UnlockUnavailable as exc:
        said = str(exc)
    finally:
        unlock.available = real
    check("is refused before anything is read", "pycryptodome" in said
          and not os.path.exists(os.path.join(root, "nocrypto", "manifest.csv")))

    print("\nBitLocker, with its startup key file")
    key = unlock.Credentials(key_files=[BEK])
    out = os.path.join(root, "bitlocker")
    outputs.append(out)
    summary = media_core.run_extraction(bitlocker, out, workers=2, credentials=key)
    opened = summary.get("unlocked_volumes") or []
    check("the volume is opened by the startup key",
          [(u["kind"], u["opened_with"]) for u in opened] == [("BitLocker", "startup key")])
    check("the key file is recorded by name and SHA-256",
          opened and opened[0].get("key_files") == [{"name": "bitlocker-xts128.BEK",
                                                     "sha256": bek_sha}])
    check("and the volume is not reported locked", not summary.get("encrypted_volumes"))
    notes = report.decryption_notes({"summary": summary})
    check("the report says so, naming the key file", notes and "bitlocker-xts128.BEK"
          in notes[0] and bek_sha in notes[0] and "supplied by the examiner" in notes[0])

    raw = media_core.open_image(bitlocker)
    parent, _ = unlock.wrap(media_core.open_image(bitlocker), key)
    media_core._worker_init(bitlocker, [], False, False, False, None, None, key)
    worker = media_core._WORKER["reader"]
    check("a carve worker reads the same plaintext as its parent, not ciphertext",
          worker.read(0, 65536) == parent.read(0, 65536) != raw.read(0, 65536))
    check("which is the file system inside (SquashFS)", parent.read(0, 4) == b"hsqs")
    for reader in (raw, parent, worker):
        reader.close()

    out = os.path.join(root, "bitlocker-wrong")
    summary = media_core.run_extraction(bitlocker, out, workers=1,
                                        credentials=unlock.Credentials([WRONG[1]]))
    limits = report.coverage_limits({"summary": summary})
    check("a wrong key leaves it reported locked, and says the key did not open it",
          [v["kind"] for v in summary.get("encrypted_volumes") or []] == ["BitLocker"]
          and any("did not open it" in line for line in limits))

    print("\nFileVault (APFS), with its password")
    expected = {}
    with open(os.path.join(FIX, "apfs-filevault.intended"), encoding="utf-8") as fh:
        for line in fh:
            if line[:1] not in ("#", "\n"):
                digest, path = line.split()
                expected[path] = digest
    read = {}

    def take(entry):
        read[entry["path"]] = hashlib.sha256(b"".join(entry["read"]())).hexdigest()

    vendor_fs.walk(apfs, take, kinds={"apfs"}, credentials=unlock.Credentials([PASSWORD]))
    found = {path.split("/", 1)[-1]: digest for path, digest in read.items()}
    check(f"its files read decrypted match the hashes recorded when it was made "
          f"({len(expected)})", all(found.get(p) == d for p, d in expected.items()))
    out = os.path.join(root, "apfs")
    outputs.append(out)
    summary = media_core.run_extraction(apfs, out, workers=1,
                                        credentials=unlock.Credentials([WRONG[0], PASSWORD]))
    opened = summary.get("unlocked_volumes") or []
    check("the run opens it with the password, among others given",
          [(u["kind"], u.get("volume"), u["opened_with"]) for u in opened]
          == [("APFS", "CONVVOL", "password")])
    check("and does not report it locked", not summary.get("encrypted_volumes"))
    notes = report.decryption_notes({"summary": summary})
    check("the report says its free space was not carved decrypted",
          notes and "free space stays encrypted" in notes[0])

    out = os.path.join(root, "apfs-wrong")
    summary = media_core.run_extraction(apfs, out, workers=1,
                                        credentials=unlock.Credentials([WRONG[0]]))
    limits = report.coverage_limits({"summary": summary})
    said = next((line for line in limits if "CONVVOL" in line), "")
    check("a wrong password: locked, the key did not open it, and the stored hint",
          "did not open it" in said and "the qnxprobe conversion test" in said)

    print("\nresuming a run that read decrypted volumes")
    out = os.path.join(root, "resume")
    outputs.append(out)
    # Stopped at its first chance: the fixture is small enough that a run
    # allowed a few checks of the cancel finishes first, and a finished run
    # leaves no note to resume.
    media_core.run_extraction(bitlocker, out, workers=1, credentials=key,
                              cancel_cb=lambda: True, mode="carve")
    folder = case_folder(out)
    note = media_core.read_progress(folder)
    check(f"the interrupted run's note says where it decrypted ({note.get('unlocked')}), "
          f"not with what", bool(note.get("unlocked"))
          and bek_bytes.hex() not in json.dumps(note) and "BEK" not in json.dumps(note))
    try:
        media_core.run_extraction(bitlocker, folder, resume=True, workers=1,
                                  mode="carve")
        stopped = ""
    except unlock.UnlockUnavailable as exc:
        stopped = str(exc)
    check("resuming without the key is refused, saying why",
          "Resume it with the same password or key file" in stopped)
    summary = media_core.run_extraction(bitlocker, folder, resume=True, workers=1,
                                        credentials=key, mode="carve")
    check("and with it, the run finishes", summary.get("canceled") is not True)

    print("\nthe interface")
    from fme.web import media_gui
    client = media_gui.app.test_client()
    headers = {"Content-Type": "application/json", "X-FME-Request": "1"}
    with media_gui.ACTIVE_LOCK:
        media_gui.ACTIVE.update(path=os.path.abspath(apfs), info=None)
    gui_root = os.path.join(root, "gui")
    outputs.append(gui_root)
    bad = client.post("/api/start", headers=headers, data=json.dumps(
        {"path": apfs, "output_root": gui_root, "key_files": [os.path.join(root, "nope.BEK")]}))
    check("a key file that is not there is refused", bad.status_code >= 400)
    started = client.post("/api/start", headers=headers, data=json.dumps(
        {"path": apfs, "output_root": gui_root, "mode": "fs", "workers": 1,
         "passwords": [PASSWORD]}))
    job = started.get_json().get("job_id")
    seen = []
    for _ in range(240):
        status = client.get(f"/api/status?job_id={job}")
        seen.append(status.get_data(as_text=True))
        if status.status_code != 200 or status.get_json().get("status") != "running":
            break
        time.sleep(0.5)
    with media_gui.JOBS_LOCK:
        held = repr(media_gui.JOBS.get(job))
        done = (media_gui.JOBS.get(job) or {}).get("summary") or {}
    check("the run opened the volume", [u.get("volume") for u in
                                        done.get("unlocked_volumes") or []] == ["CONVVOL"])
    check("the password is not in the job the page reads, nor in what it was sent",
          PASSWORD not in held and not any(PASSWORD in text for text in seen))

    print("\nnothing kept the key")
    reports = os.path.join(root, "reports")
    outputs.append(reports)
    report.build(case_folder(os.path.join(root, "apfs")), reports,
                 formats=["html", "csv", "json", "docx", "pdf"])
    texts = []
    # Every case this test made, refused runs included -- a key that did not
    # open a volume is as much a secret as one that did -- and the reports.
    # Not the evidence copies or the password file the test wrote itself.
    inputs = {bitlocker, apfs, password_file}
    for top in [root]:
        for path in glob.glob(os.path.join(top, "**", "*"), recursive=True):
            if not os.path.isfile(path) or path in inputs:
                continue
            with open(path, "rb") as fh:
                texts.append((path, fh.read()))
            if path.endswith(".docx"):
                with zipfile.ZipFile(path) as z:
                    texts += [(path + "!" + n, z.read(n)) for n in z.namelist()]
            if path.endswith(".pdf") and shutil.which("pdftotext"):
                text = subprocess.run(["pdftotext", path, "-"], capture_output=True).stdout
                texts.append((path + "!text", text))
    texts.append(("log", LOG_TEXT.getvalue().encode("utf-8")))
    needles = [bek_bytes, bek_bytes.hex().encode()]
    for secret in (PASSWORD,) + WRONG:
        needles += [secret.encode("utf-8"), secret.encode("utf-16-le")]
    leaks = [path for path, data in texts if any(n in data for n in needles)]
    check(f"the password and key file bytes in {len(texts)} files, the log and "
          f"the reports: {leaks or 'nowhere'}", not leaks)
    check("and the reports were written", any(p.endswith(".docx") for p, _ in texts))

    logging.getLogger().removeHandler(handler)
    shutil.rmtree(root, ignore_errors=True)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
