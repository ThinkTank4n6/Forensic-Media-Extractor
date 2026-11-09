#!/bin/bash
# Build a small NTFS volume with a known answer: two pictures written and
# then deleted, one small enough to be **resident** -- held inside the MFT
# record itself, so it never occupies a cluster and no carver can ever
# reach it -- and one not.
#
# Their hashes are recorded before anything is deleted, so the expected
# answer comes from what was written rather than from any reader. That is
# the whole point: a test that compares one of our readers against another
# of our readers proves only that they share an assumption.
#
#     bash tools/make_fixtures/make_ntfs_deleted.sh tests/fixtures/ntfs-deleted.img
#
# Needs ntfsprogs, ntfs-3g, fuse3 and python3-pil. Runs as an ordinary
# user: ntfs-3g mounts a plain file through FUSE, so no loop device and no
# root.
#
#     sudo apt install ntfs-3g ntfsprogs fuse3 python3-pil sleuthkit
#
# Install sleuthkit too and this script cross-checks every recovered file
# against what The Sleuth Kit's icat returns from the same record. Two
# independent readers agreeing on a known input is the strongest evidence
# this project has; without it the fixture is still worth having, and the
# script says which you got.
#
# The approach is GLEAPP's (MIT, Alexis Brignoni). This is our own script.
set -euo pipefail

IMG=${1:-tests/fixtures/ntfs-deleted.img}
OUT=$(dirname "$IMG")
INTENDED="${IMG%.img}.intended"
MNT=$(mktemp -d)
cleanup() {
  mountpoint -q "$MNT" && fusermount3 -u "$MNT" 2>/dev/null || true
  rmdir "$MNT" 2>/dev/null || true
}
trap cleanup EXIT

for needed in mkntfs ntfs-3g fusermount3; do
  command -v "$needed" >/dev/null || {
    echo "$needed is not installed. On Debian or Ubuntu:"
    echo "    sudo apt install ntfs-3g ntfsprogs fuse3 python3-pil sleuthkit"
    exit 1
  }
done

mkdir -p "$OUT"
rm -f "$IMG"
dd if=/dev/zero of="$IMG" bs=1M count=8 status=none
mkntfs -F -f -Q -L FMEKNOWN -s 512 -c 4096 "$IMG" >/dev/null 2>&1
ntfs-3g "$IMG" "$MNT"

# Small enough to live inside its MFT record: a carve can never find this
# one, so recovering it proves the MFT path works rather than the carver.
python3 - "$MNT/photo-resident.jpg" <<'PY'
import sys
from PIL import Image
Image.new("RGB", (2, 2), (200, 40, 40)).save(sys.argv[1], "JPEG", quality=20)
PY

# Large enough to need clusters of its own.
python3 - "$MNT/clip-nonresident.jpg" <<'PY'
import sys
from PIL import Image
picture = Image.new("RGB", (400, 300))
pixels = picture.load()
for y in range(300):
    for x in range(400):
        pixels[x, y] = ((x * 3) % 256, (y * 5) % 256, (x + y) % 256)
picture.save(sys.argv[1], "JPEG", quality=90)
PY

# One that stays, so the walk itself is checked as well as the recovery.
python3 - "$MNT/kept-live.jpg" <<'PY'
import sys
from PIL import Image
Image.new("RGB", (120, 90), (30, 90, 170)).save(sys.argv[1], "JPEG")
PY
sync

{
  echo "# What was written to $(basename "$IMG"), hashed before any of it"
  echo "# was deleted, on $(uname -srm) with $(mkntfs -V 2>&1 | head -1)."
  echo "# The expected answer comes from what went in, not from a reader."
  echo "# sha256  size  name  state"
} > "$INTENDED"

for f in photo-resident.jpg clip-nonresident.jpg kept-live.jpg; do
  state=deleted
  [ "$f" = "kept-live.jpg" ] && state=live
  printf '%s  %s  %s  %s\n' \
    "$(sha256sum "$MNT/$f" | cut -d' ' -f1)" \
    "$(stat -c%s "$MNT/$f")" "$f" "$state" >> "$INTENDED"
done

# Prove the small file really is resident before deleting anything.
#
# The whole purpose of this fixture is a file no carver can reach. If
# NTFS decided to give it a cluster after all -- a different cluster
# size, a longer name, more attributes -- then the fixture still builds,
# still passes, and silently stops testing the one thing it exists for.
# So it is checked, and the build fails if it is wrong.
RESIDENT_OK=unknown
fusermount3 -u "$MNT"

if command -v istat >/dev/null && command -v ifind >/dev/null; then
  INODE=$(ifind -n "/photo-resident.jpg" "$IMG" 2>/dev/null | tr -d '[:space:]')
  if [ -n "$INODE" ]; then
    if istat "$IMG" "$INODE" 2>/dev/null | grep -qi "resident"; then
      RESIDENT_OK=yes
    else
      RESIDENT_OK=no
    fi
  fi
fi

case "$RESIDENT_OK" in
  yes) echo "photo-resident.jpg is resident in its MFT record, as intended" ;;
  no)  echo "ERROR: photo-resident.jpg was given clusters of its own."
       echo "It is supposed to be resident -- held inside the MFT record,"
       echo "where no carver can reach it. A fixture whose resident file"
       echo "is not resident tests nothing that a carve would not find."
       echo "Try a smaller picture, or a larger MFT record size."
       exit 1 ;;
  *)   echo "NOTE: could not confirm the file is resident (needs"
       echo "sleuthkit's istat and ifind). The fixture is still usable,"
       echo "but whether it tests the MFT path is unconfirmed." ;;
esac
echo "# resident_confirmed=$RESIDENT_OK" >> "$INTENDED"

ntfs-3g "$IMG" "$MNT"
rm -f "$MNT/photo-resident.jpg" "$MNT/clip-nonresident.jpg"
sync
fusermount3 -u "$MNT"

echo "wrote $IMG"
echo "wrote $INTENDED:"
cat "$INTENDED"

if command -v icat >/dev/null && command -v fls >/dev/null; then
  echo
  echo "cross-checking against The Sleuth Kit"
  agreed=0
  while read -r _ inode _; do
    inode=${inode%:}
    [ -z "$inode" ] && continue
    digest=$(icat "$IMG" "$inode" 2>/dev/null | sha256sum | cut -d' ' -f1)
    if grep -q "^$digest" "$INTENDED"; then
      agreed=$((agreed + 1))
    fi
  done < <(fls -r -p "$IMG" 2>/dev/null)
  echo "  The Sleuth Kit returns the recorded bytes for $agreed file(s)"
  if [ "$agreed" -eq 0 ]; then
    echo "  NOTE: it agreed on nothing. Either the fixture is wrong or TSK"
    echo "  reads it differently -- worth knowing before trusting either."
  fi
else
  echo
  echo "The Sleuth Kit is not installed, so nothing was cross-checked."
  echo "    sudo apt install sleuthkit"
  echo "Two independent readers agreeing on a known input is the strongest"
  echo "evidence here. The fixture is still usable without it."
fi
