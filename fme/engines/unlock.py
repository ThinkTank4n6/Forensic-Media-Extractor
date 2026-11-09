"""
unlock.py -- opening the encrypted volumes an examiner holds the key to.

The vendored reader can open a BitLocker volume (with its password, its
48-digit recovery password or its startup key file), a LUKS1 or LUKS2
container (with its passphrase, since v2026.11.318) and an APFS volume
FileVault encrypted (with the user's password or the personal recovery key).
This module is how the rest of the program reaches that, and the rules it
keeps:

**The secret is never written down.** A password lives in a Credentials
object for the length of a run and nowhere else: not in the manifest, the
progress note, the log, case.db, a report or the interface's job list.
Credentials print as a count. A resumed run asks for the key again.

**What opened a volume is written down.** Which volume, and with what kind
of protector -- a password, a recovery password, a startup key file (named,
with the SHA-256 of the file, which identifies the key without being it) --
goes into the run's summary and the report, because the recovered files
were read through a decryption and a reader of the report is entitled to
know which.

**A key that opens nothing is said.** The volume stays locked, is reported
as locked, and the report says keys were tried and none opened it; an APFS
volume's passphrase hint, which the volume stores, is shown as stored.

**BitLocker and LUKS are decrypted underneath the program; APFS is not.** An unlocked
BitLocker volume is presented as plaintext at its own offset, so the file
system walk, TSK, the carve and the deleted-entry passes all read it as if
it had never been encrypted. FileVault encrypts each APFS volume with its own
key inside a container shared with other volumes, so only the APFS walker
can decrypt it: its files are listed and read with their names and dates,
but the carve still reads that volume's free space as ciphertext, and the
report says so.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading

LOG = logging.getLogger("fme.unlock")


class UnlockUnavailable(RuntimeError):
    """A key was given, and this installation cannot use one."""


class Credentials:
    """Passwords and key files for one run, never shown or stored.

    Prints as a count. Pickles (a carve worker needs the key to read the
    same plaintext as its parent; it travels over the process pipe, never
    through a file).
    """

    __slots__ = ("_passwords", "_key_files")

    def __init__(self, passwords=(), key_files=()):
        self._passwords = tuple(p for p in (str(x).rstrip("\r\n") for x in passwords or ())
                                if p)
        self._key_files = tuple(str(k) for k in key_files or () if k)

    def __bool__(self):
        return bool(self._passwords or self._key_files)

    def __repr__(self):
        return (f"Credentials({len(self._passwords)} password(s), "
                f"{len(self._key_files)} key file(s))")

    __str__ = __repr__

    def __getstate__(self):
        return (self._passwords, self._key_files)

    def __setstate__(self, state):
        self._passwords, self._key_files = state

    @property
    def passwords(self):
        return self._passwords

    @property
    def key_files(self):
        return self._key_files

    def key_file_record(self) -> list:
        """Each key file by name and SHA-256: which key, not the key."""
        out = []
        for path in self._key_files:
            try:
                with open(path, "rb") as fh:
                    digest = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                digest = ""
            out.append({"name": os.path.basename(path), "sha256": digest})
        return out


def from_files(password_files=(), key_files=()) -> Credentials:
    """Credentials from files: one password per line, blank lines ignored.

    Read from a file rather than taken on the command line, where a password
    would sit in the shell's history and in the process list for anyone on
    the machine to read.
    """
    passwords = []
    for path in password_files or ():
        with open(path, encoding="utf-8-sig") as fh:
            passwords += [line.rstrip("\r\n") for line in fh if line.strip()]
    for path in key_files or ():
        if not os.path.isfile(path):
            raise FileNotFoundError(f"key file not found: {path}")
    return Credentials(passwords, key_files)


def ask(prompt="Password or recovery key (blank to finish): ") -> Credentials:
    """Passwords typed at the terminal, not echoed, until a blank line."""
    import getpass

    passwords = []
    while True:
        typed = getpass.getpass(prompt)
        if not typed:
            break
        passwords.append(typed)
    return Credentials(passwords)


def available() -> bool:
    """Can this installation decrypt? The readers need pycryptodome."""
    from fme.engines import vendor_fs

    module = vendor_fs.probe()
    return module is not None and getattr(module, "_BDE_AES", None) is not None


def require(credentials) -> None:
    """Refuse a run that was given a key it cannot use.

    Run without it, the volume would be reported locked -- true, but not
    what the examiner asked, and easy to read past. Said before anything is
    read instead.
    """
    if credentials and not available():
        raise UnlockUnavailable(
            "A password or key file was given, but this installation cannot "
            "decrypt: the pycryptodome package is not installed (pip install "
            "pycryptodome; it is in requirements.txt). Nothing was read.")


# --------------------------------------------------------------------------
# Opening the image
# --------------------------------------------------------------------------
class _ReaderFile:
    """An ImageReader as the seekable file the vendored unlockers expect."""

    def __init__(self, reader):
        self._reader, self.size, self._pos = reader, reader.size, 0

    def seek(self, offset, whence=0):
        self._pos = offset if whence == 0 else (
            self._pos + offset if whence == 1 else self.size + offset)
        return self._pos

    def tell(self):
        return self._pos

    def read(self, n=-1):
        end = self.size if n is None or n < 0 else min(self.size, self._pos + n)
        data = self._reader.read(self._pos, max(0, end - self._pos)) if end > self._pos else b""
        self._pos += len(data)
        return data

    def close(self):
        pass


def _results(bitlocker_found=(), apfs_found=(), credentials=None,
             luks_found=()) -> list:
    """Every encrypted volume met, as plain facts: no key material."""
    out = []
    files = credentials.key_file_record() if credentials else []
    for bl in bitlocker_found:
        by = getattr(bl, "unlocked_by", None) or ""
        entry = {"kind": "BitLocker", "partition": getattr(bl, "label", ""),
                 "offset": getattr(bl, "base", 0), "opened": bool(by),
                 "opened_with": by}
        if by == "startup key" and files:
            entry["key_files"] = files
        if not by:
            entry["why"] = getattr(bl, "why", "") or ""
        out.append(entry)
    for lk in luks_found:
        opened = getattr(lk, "mk", None) is not None
        entry = {"kind": f"LUKS{getattr(lk, 'version', '')}",
                 "partition": getattr(lk, "label", ""),
                 "offset": getattr(lk, "base", 0), "opened": opened,
                 "opened_with": (getattr(lk, "unlocked_by", None) or "password")
                 if opened else ""}
        if not opened:
            entry["why"] = getattr(lk, "why", "") or ""
        out.append(entry)
    for lk in apfs_found:
        by = getattr(lk, "unlocked_by", None) or ""
        entry = {"kind": "APFS", "partition": getattr(lk, "label", ""),
                 "offset": getattr(lk, "base", 0), "volume": getattr(lk, "name", ""),
                 "opened": lk.vek is not None, "opened_with": by}
        if lk.vek is None:
            entry["why"] = getattr(lk, "why", "") or ""
            if getattr(lk, "hint", ""):
                entry["hint"] = lk.hint
        out.append(entry)
    return out


def private_keys(credentials) -> list:
    """The key files that are private keys (PEM or DER), for an acquisition
    sealed to a certificate. A BitLocker startup key (.BEK) is not one, and
    handed to the certificate reader it was an error, not a miss."""
    found = []
    for path in getattr(credentials, "key_files", ()) or ():
        try:
            with open(path, "rb") as fh:
                head = fh.read(64)
        except OSError:
            continue
        if head.lstrip().startswith(b"-----BEGIN") or head[:2] == b"\x30\x82":
            found.append(path)
    return found


def is_password_error(exc) -> bool:
    """Whether an acquisition reader refused for want of the right key, rather
    than because the file is damaged: the readers name these by class."""
    return any("Password" in klass.__name__ for klass in type(exc).__mro__)


def open_vendored(module, image, credentials):
    """The vendored reader's image, with every volume the keys open opened.

    Returns (image, results). With no credentials, the image unchanged.
    """
    if not credentials:
        return image, []
    image, bitlocker = module.unlock_bitlocker(
        image, passwords=credentials.passwords, key_files=credentials.key_files)
    # LUKS1 and LUKS2, with a passphrase. The vendored reader has opened them
    # since it was vendored; nothing here asked it to, so a LUKS volume whose
    # passphrase the examiner held was reported locked (v2026.11.317).
    # Before LVM, so a physical volume inside a container is seen.
    luks = []
    if hasattr(module, "unlock_luks"):
        image, luks = module.unlock_luks(image, passwords=credentials.passwords)
    image, apfs = module.unlock_apfs(image, passwords=credentials.passwords)
    return image, _results(bitlocker, apfs, credentials, luks)


def wrap(reader, credentials):
    """This program's reader, reading unlocked BitLocker volumes as plaintext.

    Returns (reader, results). The reader is the one given when nothing was
    opened; otherwise an UnlockedReader over it, which every engine reads
    exactly as it read the original.
    """
    if not credentials:
        return reader, []
    require(credentials)
    from fme.engines import vendor_fs

    module = vendor_fs.probe()
    view, results = open_vendored(module, _ReaderFile(reader), credentials)
    # LUKS, like BitLocker, is decrypted underneath the program: its data is
    # plaintext at its own offset, so every engine reads it as never encrypted.
    if any((r["kind"] == "BitLocker" or r["kind"].startswith("LUKS")) and r["opened"]
           for r in results):
        reader = UnlockedReader(reader, view)
    return reader, results


def _reader_base():
    from fme.engines.media_core import ImageReader
    return ImageReader


class UnlockedReader(_reader_base()):
    """An image with its unlocked BitLocker volumes read as plaintext."""

    def __init__(self, inner, view):
        self._inner, self._view = inner, view
        self.name, self.size = inner.name, inner.size
        self._lock = threading.Lock()

    @property
    def bad_ranges(self):
        # The device's own record of what it read as zeros. Hidden behind
        # this wrapper, the carve took decrypted garbage over bad sectors for
        # a whole file instead of stopping and marking it short (v2026.11.319).
        return getattr(self._inner, "bad_ranges", None)

    def read(self, offset: int, length: int) -> bytes:
        with self._lock:
            self._view.seek(offset)
            return self._view.read(length)

    def close(self) -> None:
        self._inner.close()


# --------------------------------------------------------------------------
# Saying what happened
# --------------------------------------------------------------------------
def where(entry: dict) -> str:
    place = ("the image" if not entry.get("offset") else
             f"partition {entry.get('partition') or '?'}")
    if entry.get("volume"):
        return f"the {entry['kind']} volume {entry['volume']} in {place}"
    return f"the {entry['kind']} volume in {place}"


def opened_line(entry: dict) -> str:
    how = entry.get("opened_with") or "a key"
    files = entry.get("key_files") or []
    if how == "startup key" and len(files) == 1:
        how = (f"the startup key file {files[0]['name']} "
               f"(SHA-256 {files[0]['sha256']})")
    elif how == "startup key" and files:
        how = "one of the startup key files given (" + ", ".join(
            f"{f['name']} SHA-256 {f['sha256']}" for f in files) + ")"
    else:
        how = f"a {how}" if how in ("password", "recovery password") else how
    line = (f"{where(entry)[0].upper()}{where(entry)[1:]} was encrypted and was opened "
            f"with {how} supplied by the examiner; its files were read decrypted.")
    if entry.get("kind") == "APFS":
        line += (" Its files were listed and read through its file system; its "
                 "free space stays encrypted, so the carve recovered nothing "
                 "deleted from it.")
    return line


def refused_line(entry: dict) -> str:
    line = (f"{where(entry)[0].upper()}{where(entry)[1:]} is encrypted, and none "
            f"of the passwords or key files given opened it")
    why = entry.get("why")
    line += f" ({why})." if why else "."
    if entry.get("hint"):
        line += f' Its passphrase hint, as the volume stores it: "{entry["hint"]}".'
    return line
