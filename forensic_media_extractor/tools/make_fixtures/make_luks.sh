#!/bin/bash
# Build a LUKS volume with a known answer, twice: once locked and once
# as the unlocked bytes.
#
#     bash tools/make_fixtures/make_luks.sh            # LUKS2, the default
#     bash tools/make_fixtures/make_luks.sh 1          # LUKS1
#
# Two images come out of this, and the pair is the point:
#
#   luks2-locked.img    the volume as it is seized -- the program should
#                       say it is LUKS2 and locked, and recover nothing
#   luks2-unlocked.img  the same volume after cryptsetup opened it --
#                       the program should recover the pictures
#
# The hashes are recorded before anything is encrypted, so the expected
# answer comes from what was written.
#
# Needs cryptsetup and root: mapping a LUKS device is a kernel
# operation and there is no unprivileged way to do it. In WSL2 sudo
# works normally.
#
#     sudo apt install cryptsetup-bin python3-pil
#     sudo bash tools/make_fixtures/make_luks.sh
set -euo pipefail

VERSION=${1:-2}
case "$VERSION" in
  1) TYPE=luks1 ;;
  2) TYPE=luks2 ;;
  *) echo "usage: $0 [1|2]"; exit 1 ;;
esac

command -v cryptsetup >/dev/null || {
  echo "cryptsetup is not installed."
  echo "    sudo apt install cryptsetup-bin python3-pil"
  exit 1
}
[ "$(id -u)" -eq 0 ] || {
  echo "This needs root: mapping a LUKS device is a kernel operation."
  echo "    sudo bash $0 $VERSION"
  exit 1
}

HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$(cd "$HERE/../../tests/fixtures" && pwd)
STEM="luks$VERSION"
LOCKED="$OUT/$STEM-locked.img"
UNLOCKED="$OUT/$STEM-unlocked.img"
INTENDED="$OUT/$STEM.intended"
NAME="fme_fixture_$$"
MNT=$(mktemp -d)
PASS="fme-fixture-passphrase"

cleanup() {
  mountpoint -q "$MNT" && umount "$MNT" 2>/dev/null || true
  cryptsetup status "$NAME" >/dev/null 2>&1 && \
    cryptsetup close "$NAME" 2>/dev/null || true
  rmdir "$MNT" 2>/dev/null || true
}
trap cleanup EXIT

echo "building a $TYPE volume"
rm -f "$LOCKED" "$UNLOCKED"
dd if=/dev/zero of="$LOCKED" bs=1M count=48 status=none
echo -n "$PASS" | cryptsetup luksFormat --type "$TYPE" --batch-mode \
    "$LOCKED" - 
echo -n "$PASS" | cryptsetup open "$LOCKED" "$NAME" -
mkfs.ext4 -q -F "/dev/mapper/$NAME"
mount "/dev/mapper/$NAME" "$MNT"

python3 - "$MNT" <<'PY'
import struct, sys
# A BMP each, written by hand: a Linux image may have no Pillow.
for index, (name, width, height, seed) in enumerate((
        ("kept-one.bmp", 100, 80, 17),
        ("kept-two.bmp", 160, 120, 59))):
    rows = bytearray()
    pad = (-width * 3) % 4
    for y in range(height):
        for x in range(width):
            rows += bytes(((x * 3 + seed) % 256, (y * 5 + seed) % 256,
                           (x + y + seed) % 256))
        rows += b"\x00" * pad
    head = (b"BM" + struct.pack("<IHHI", 54 + len(rows), 0, 0, 54)
            + struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 24, 0,
                          len(rows), 2835, 2835, 0, 0))
    open(f"{sys.argv[1]}/{name}", "wb").write(head + bytes(rows))
PY
sync

{
  echo "# What was written inside $STEM-locked.img, hashed before the"
  echo "# volume was closed, on $(uname -srm)."
  echo "# $(cryptsetup --version)"
  echo "# passphrase: $PASS"
  echo "# The locked image should yield nothing and be reported as"
  echo "# $TYPE. The unlocked image should yield these files."
  echo "# sha256  size  name  state"
} > "$INTENDED"
for f in kept-one.bmp kept-two.bmp; do
  printf '%s  %s  %s  live\n' \
    "$(sha256sum "$MNT/$f" | cut -d' ' -f1)" \
    "$(stat -c%s "$MNT/$f")" "$f" >> "$INTENDED"
done

# The unlocked bytes, as an examiner would image them after opening the
# volume. This is the half that proves the program reads what is inside;
# the locked half proves it says so rather than reporting an empty disk.
dd if="/dev/mapper/$NAME" of="$UNLOCKED" bs=1M status=none
umount "$MNT"
cryptsetup close "$NAME"
trap - EXIT
rmdir "$MNT" 2>/dev/null || true

echo "wrote $LOCKED   (the volume as seized)"
echo "wrote $UNLOCKED (the same volume, opened)"
echo "wrote $INTENDED:"
cat "$INTENDED"
echo
echo "The locked image holds a passphrase in its header that is written"
echo "in the .intended file. That is deliberate -- it is a fixture, not"
echo "evidence -- but do not put it anywhere a real key would go."
