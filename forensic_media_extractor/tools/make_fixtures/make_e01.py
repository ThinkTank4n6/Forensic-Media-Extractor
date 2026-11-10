"""
make_e01.py -- a known-answer fixture in E01, the format cases arrive in.

Every case this unit works arrives as an E01, and until now no test had
ever read one. Nothing on PyPI writes EWF and the reference
implementation is LGPL, so the fixture has to be packed here, from the
format documentation: a header, a volume section, chunked and
zlib-compressed data with a chunk table, and the digests at the end.

    python tools/make_fixtures/make_e01.py tests/fixtures/

Writes `known.E01` and `known.intended`.

**What this does and does not establish.** The pictures are hashed
before they are packed, so reading the E01 and getting those bytes back
proves the acquisition is unpacked correctly. The writer is a separate
program from the reader -- it packs from the specification, the reader
parses independently -- but both live in this project, so this is a
round-trip, not an independent check. An E01 written by `ewfacquire` or
FTK Imager and read here would be stronger, and §5.10 of
`docs/VALIDATION.md` says so.

The approach is taken from the other tool in this space (MIT, Alexis
Brignoni), which writes its own EWF fixtures for the same reason.
"""

from __future__ import annotations

import hashlib
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, "tests"))
sys.path.insert(0, os.path.join(PROJECT, "fme", "vendor"))

SECTION_SIZE = 76
CHUNK_SECTORS = 64
SECTOR = 512
CHUNK = CHUNK_SECTORS * SECTOR


def _adler(data: bytes) -> bytes:
    return struct.pack("<I", zlib.adler32(data) & 0xFFFFFFFF)


def _section(fh, name: str, payload: bytes, last: bool = False) -> None:
    """One EWF section: its name, where the next one starts, a checksum."""
    start = fh.tell()
    size = SECTION_SIZE + len(payload)
    following = start if last else start + size
    head = bytearray(SECTION_SIZE)
    head[0:16] = name.encode("ascii").ljust(16, b"\x00")
    struct.pack_into("<Q", head, 16, following)
    struct.pack_into("<Q", head, 24, size)
    head[72:76] = _adler(bytes(head[:72]))
    fh.write(bytes(head))
    fh.write(payload)


def _header(description: str) -> bytes:
    """The deflated, tab-separated header EWF uses."""
    text = ("1\nmain\nc\tn\ta\te\tt\tm\tu\tp\n"
            f"{description}\tknown-answer\tFME\tfixture\tnotes\t\t\t0\n\n")
    return zlib.compress(text.encode("ascii"))


def write_e01(path: str, payload: bytes, description: str = "FME") -> dict:
    """Pack `payload` as a single-segment E01."""
    sectors = (len(payload) + SECTOR - 1) // SECTOR
    padded = payload + b"\x00" * (sectors * SECTOR - len(payload))
    chunks = [padded[at:at + CHUNK] for at in range(0, len(padded), CHUNK)]

    with open(path, "wb") as fh:
        # Signature, the constant 1, the segment number, and two bytes
        # that must be zero: 13 bytes in all. The reader checks each.
        fh.write(b"EVF\x09\x0d\x0a\xff\x00")
        fh.write(struct.pack("<BHH", 1, 1, 0))

        _section(fh, "header2", _header(description))
        _section(fh, "header", _header(description))

        volume = bytearray(94)
        struct.pack_into("<I", volume, 4, 1)              # chunk count
        struct.pack_into("<I", volume, 8, CHUNK_SECTORS)  # sectors/chunk
        struct.pack_into("<I", volume, 12, SECTOR)        # bytes/sector
        struct.pack_into("<Q", volume, 16, sectors)       # sectors
        volume[90:94] = _adler(bytes(volume[:90]))
        _section(fh, "volume", bytes(volume))

        # The chunk table records where each chunk begins, with the top
        # bit set when it is compressed.
        table_at = fh.tell()
        data_start = table_at + SECTION_SIZE + 24 + len(chunks) * 4 + 4
        offsets, blobs = [], []
        at = data_start
        for chunk in chunks:
            squeezed = zlib.compress(chunk, 6)
            if len(squeezed) < len(chunk):
                blobs.append(squeezed)
                offsets.append(at | 0x80000000)
            else:
                blobs.append(chunk + _adler(chunk))
                offsets.append(at)
            at += len(blobs[-1])

        table = bytearray(24)
        struct.pack_into("<I", table, 0, len(chunks))
        table[20:24] = _adler(bytes(table[:20]))
        table += b"".join(struct.pack("<I", o) for o in offsets)
        table += _adler(bytes(table[24:]))
        _section(fh, "table", bytes(table))

        for blob in blobs:
            fh.write(blob)

        digest = hashlib.md5(padded).digest()
        _section(fh, "digest", digest + b"\x00" * 16 + _adler(digest))
        _section(fh, "done", b"", last=True)

    return {"path": path, "sectors": sectors, "chunks": len(chunks),
            "md5": hashlib.md5(padded).hexdigest()}


def build(out_dir: str) -> dict:
    import make_media_test_image as fixtures

    os.makedirs(out_dir, exist_ok=True)

    # A raw volume with pictures in it, hashed before it is packed.
    blob = bytearray(3 * 1024 * 1024)
    intended, at = [], 200_000
    for index in range(4):
        picture = fixtures.make_jpeg(40 + index)
        blob[at:at + len(picture)] = picture
        at += len(picture) + 400_000
        intended.append({
            "sha256": hashlib.sha256(picture).hexdigest(),
            "size": len(picture),
            "name": f"carved-{index}.jpg",
        })

    image = os.path.join(out_dir, "known.E01")
    made = write_e01(image, bytes(blob))

    listing = os.path.join(out_dir, "known.intended")
    with open(listing, "w", encoding="utf-8") as fh:
        fh.write("# What was packed into known.E01, hashed before it was\n"
                 "# packed. The expected answer comes from what went in.\n"
                 f"# the acquisition holds {made['sectors']} sector(s) in "
                 f"{made['chunks']} chunk(s); MD5 of the raw image "
                 f"{made['md5']}\n"
                 "# sha256  size  name  state\n")
        for row in intended:
            fh.write(f"{row['sha256']}  {row['size']}  {row['name']}  "
                     f"live\n")

    return {"image": image, "intended": listing, "files": intended,
            **made}


if __name__ == "__main__":
    where = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        PROJECT, "tests", "fixtures")
    result = build(where)
    print(f"wrote {result['image']} "
          f"({os.path.getsize(result['image']):,} bytes, "
          f"{result['chunks']} chunk(s))")
    print(f"wrote {result['intended']}:")
    for row in result["files"]:
        print(f"  {row['sha256'][:16]}…  {row['size']:>7}  {row['name']}")
