#!/bin/bash
# Build an APFS or HFS+ volume with a known answer, on a Mac.
#
#     bash tools/make_fixtures/make_mac_deleted.sh apfs
#     bash tools/make_fixtures/make_mac_deleted.sh hfs
#
# Writes `apfs-deleted.img` / `hfsplus-deleted.img` and the matching
# `.intended` into tests/fixtures. Needs nothing but macOS: hdiutil and
# diskutil are part of the system.
#
# **Read this before trusting an APFS result.** APFS is copy-on-write
# and the container trims aggressively; a deleted file's blocks may be
# released and reused within seconds, and a snapshot may hold a version
# of a file that no directory entry points at. So:
#
#   - The *live* files are a fair test of the reader and are expected to
#     come back.
#   - The *deleted* ones are recorded in the fixture as "deleted-may-be-
#     gone". If they do not come back, that is APFS behaving as APFS,
#     not necessarily a fault in the reader. The test reports them
#     separately and does not fail on them.
#
# That distinction is the point of building the fixture rather than
# assuming: it tells us which it is, on a volume whose contents were
# known in advance.
#
# HFS+ is not copy-on-write and its deleted files behave much more like
# FAT's, so those are expected back and the test does fail on them.
set -euo pipefail

KIND=${1:-apfs}
case "$KIND" in
  apfs) FS="APFS";  STEM="apfs-deleted";    DELSTATE="deleted-may-be-gone" ;;
  hfs)  FS="HFS+";  STEM="hfsplus-deleted"; DELSTATE="deleted" ;;
  *) echo "usage: $0 [apfs|hfs]"; exit 1 ;;
esac

case "$(uname -s)" in
  Darwin) ;;
  *) echo "This builds a Mac file system and needs macOS."
     echo "On Linux, build the NTFS and exFAT fixtures instead."
     exit 1 ;;
esac

HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${2:-$(cd "$HERE/../../tests/fixtures" && pwd)}
mkdir -p "$OUT"
WORK=$(mktemp -d)
DMG="$WORK/$STEM.dmg"
IMG="$OUT/$STEM.img"
INTENDED="$OUT/$STEM.intended"
MNT=""
cleanup() {
  [ -n "$MNT" ] && hdiutil detach "$MNT" -quiet 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

echo "building a $FS volume"
hdiutil create -size 96m -fs "$FS" -volname FMEKNOWN -layout NONE \
        -type UDIF -quiet "${DMG%.dmg}"
# The mount point is read from hdiutil's plist answer. It was read from its
# printed lines with -quiet set, which prints none: MNT was always empty and
# every run on every Mac stopped at "could not attach the image" -- found the
# first time this was run on a Mac (v2026.11.290, Session D). An APFS attach
# answers with several entities (the container, then the volume); the one
# with a mount point is the volume. hdiutil's own message is shown if it fails.
if ! hdiutil attach "$DMG" -nobrowse -plist >"$WORK/attach.plist" 2>"$WORK/attach.err"; then
  echo "could not attach the image. hdiutil said:"
  cat "$WORK/attach.err"
  exit 1
fi
MNT=$(/usr/bin/python3 - "$WORK/attach.plist" <<'PY'
import plistlib, sys
with open(sys.argv[1], "rb") as fh:
    answer = plistlib.load(fh)
print(next((e["mount-point"] for e in answer.get("system-entities", [])
            if e.get("mount-point")), ""))
PY
)
if [ ! -d "$MNT" ]; then
  echo "hdiutil attached the image but reported no mount point. Its answer:"
  cat "$WORK/attach.plist"
  exit 1
fi
echo "mounted at $MNT"

# Pictures made without Pillow, which a Mac will not have by default:
# a BMP is trivial to write and our reader carves it.
make_picture() {   # path  width  height  seed
  /usr/bin/python3 - "$1" "$2" "$3" "$4" <<'PY'
import struct, sys
path, width, height, seed = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
rows = bytearray()
pad = (-width * 3) % 4
for y in range(height):
    for x in range(width):
        rows += bytes(((x * 3 + seed) % 256, (y * 5 + seed) % 256,
                       (x + y + seed) % 256))
    rows += b"\x00" * pad
size = 54 + len(rows)
head = (b"BM" + struct.pack("<IHHI", size, 0, 0, 54)
        + struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 24, 0,
                      len(rows), 2835, 2835, 0, 0))
open(path, "wb").write(head + bytes(rows))
PY
}

make_picture "$MNT/kept-live.bmp" 80 60 11
make_picture "$MNT/deleted-one.bmp" 120 90 37
make_picture "$MNT/deleted-two.bmp" 160 120 73
sync

{
  echo "# What was written to $STEM.img, hashed before any of it was"
  echo "# deleted, on $(sw_vers -productName) $(sw_vers -productVersion)"
  echo "# $(uname -m), file system $FS."
  echo "# The expected answer comes from what went in, not from a reader."
  if [ "$KIND" = "apfs" ]; then
    echo "# APFS is copy-on-write and trims aggressively: a deleted"
    echo "# file's blocks may already be gone. Those rows are marked"
    echo "# deleted-may-be-gone and the test reports them without"
    echo "# failing on them."
  fi
  echo "# sha256  size  name  state"
} > "$INTENDED"

row() {
  printf '%s  %s  %s  %s\n' \
    "$(shasum -a 256 "$MNT/$1" | cut -d' ' -f1)" \
    "$(stat -f%z "$MNT/$1")" "$1" "$2" >> "$INTENDED"
}
row kept-live.bmp live
row deleted-one.bmp "$DELSTATE"
row deleted-two.bmp "$DELSTATE"

rm -f "$MNT/deleted-one.bmp" "$MNT/deleted-two.bmp"
sync
hdiutil detach "$MNT" -quiet
MNT=""

# A raw image, which is what the program reads. UDRW is the bytes with
# no compression and no wrapper.
hdiutil convert "$DMG" -format UDRW -o "$WORK/raw" -quiet
mv "$WORK/raw.dmg" "$IMG"

echo "wrote $IMG ($(stat -f%z "$IMG") bytes)"
echo "wrote $INTENDED:"
cat "$INTENDED"
echo
echo "Copy both into tests/fixtures on the machine with the repository,"
echo "then run:  python3 tests/test_known_answer.py"
