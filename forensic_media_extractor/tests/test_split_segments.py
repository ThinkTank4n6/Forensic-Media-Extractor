"""A split image is read whole and in order, or refused.

Three ways a split raw set used to give a wrong image without a word:

- a missing middle segment ended the set at the gap: the image was just
  shorter, and everything after it unread;
- opening the second segment started the image there, so every offset in
  it -- every partition, every carved file's position -- was wrong;
- .aa/.ab sets, which the reader said it detected, were read as one file.

And a .ufd whose chunks had to be found beside it joined them in text
order, dump10 before dump2.
"""
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import media_core  # noqa: E402


def refused(path: str, words: str) -> bool:
    try:
        media_core.open_image(path)
    except RuntimeError as exc:
        return words in str(exc)
    return False


def main() -> int:
    ok = True
    root = os.path.join(tempfile.gettempdir(), "fme_split_segments-") + str(os.getpid())
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    for n in (1, 2, 3):
        with open(os.path.join(root, f"card.{n:03d}"), "wb") as fh:
            fh.write(bytes([n]) * 4096)
    reader = media_core.open_image(os.path.join(root, "card.001"))
    whole = reader.size == 3 * 4096 and reader.read(4096, 1) == b"\x02"
    reader.close()
    ok &= whole
    print(f"a complete .001 set reads as one image, in order: {whole}")
    later = refused(os.path.join(root, "card.002"), "card.001")
    ok &= later
    print(f"opening its second segment is refused, naming the first: {later}")
    os.remove(os.path.join(root, "card.002"))
    gap = refused(os.path.join(root, "card.001"), "card.002")
    ok &= gap
    print(f"a set with a missing segment is refused, naming it: {gap}")

    for suffix in ("aa", "ab", "ac"):
        with open(os.path.join(root, f"disk.{suffix}"), "wb") as fh:
            fh.write(suffix.encode() * 1024)
    reader = media_core.open_image(os.path.join(root, "disk.aa"))
    lettered = reader.size == 3 * 2048 and reader.read(2048, 2) == b"ab"
    reader.close()
    ok &= lettered
    print(f"an .aa/.ab/.ac set reads as one image: {lettered}")

    phone = os.path.join(root, "phone")
    os.makedirs(phone)
    for n in (1, 2, 10):
        with open(os.path.join(phone, f"Physical{n}.bin"), "wb") as fh:
            fh.write(bytes([n]) * 512)
    with open(os.path.join(phone, "Physical.ufd"), "w") as fh:
        fh.write("[General]\nVendor=Test\n")
    chunks = [os.path.basename(p) for p in
              media_core.read_ufd_segments(os.path.join(phone, "Physical.ufd"))]
    ordered = chunks == ["Physical1.bin", "Physical2.bin", "Physical10.bin"]
    ok &= ordered
    print(f"chunks found beside a .ufd are joined in number order: {ordered} {chunks}")

    shutil.rmtree(root, ignore_errors=True)
    print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
