#!/bin/bash
# Build a small exFAT volume with a known answer: pictures written, their
# hashes recorded, then two of them deleted.
#
# exFAT is what SD cards and most phone storage over 32 GB are formatted
# as, so it is the file system a great many of this unit's cases sit on.
# Deletion there works differently from FAT32 -- the directory entry set
# survives with its name intact rather than losing a character, and the
# allocation bitmap is cleared -- so a reader that handles FAT32 deleted
# entries correctly may still be wrong here.
#
#     bash tools/make_fixtures/make_exfat_deleted.sh tests/fixtures/exfat-deleted.img
#
# Needs exfatprogs (mkfs.exfat), fuse-exfat or a kernel with exfat
# support, and python3-pil. On Debian or Ubuntu:
#
#     sudo apt install exfatprogs exfat-fuse python3-pil sleuthkit
#
# Mounting exFAT for writing needs either root or fuse-exfat. The script
# tries fuse first and falls back to sudo mount, saying which it used.
set -euo pipefail

IMG=${1:-tests/fixtures/exfat-deleted.img}
OUT=$(dirname "$IMG")
INTENDED="${IMG%.img}.intended"
MNT=$(mktemp -d)
MOUNTED_BY=""
cleanup() {
  if mountpoint -q "$MNT"; then
    case "$MOUNTED_BY" in
      fuse) fusermount3 -u "$MNT" 2>/dev/null || fusermount -u "$MNT" 2>/dev/null || true ;;
      *)    sudo umount "$MNT" 2>/dev/null || true ;;
    esac
  fi
  rmdir "$MNT" 2>/dev/null || true
}
trap cleanup EXIT

command -v mkfs.exfat >/dev/null || {
  echo "mkfs.exfat is not installed. On Debian or Ubuntu:"
  echo "    sudo apt install exfatprogs exfat-fuse python3-pil sleuthkit"
  exit 1
}

mkdir -p "$OUT"
rm -f "$IMG"
# 64 MiB: above the floor mkfs.exfat will accept, small enough to commit.
dd if=/dev/zero of="$IMG" bs=1M count=64 status=none
mkfs.exfat -n FMEKNOWN "$IMG" >/dev/null

if command -v mount.exfat-fuse >/dev/null; then
  mount.exfat-fuse "$IMG" "$MNT" && MOUNTED_BY=fuse
elif command -v exfatfuse >/dev/null; then
  exfatfuse "$IMG" "$MNT" && MOUNTED_BY=fuse
else
  echo "no fuse exfat driver found; mounting with sudo"
  sudo mount -o loop,uid="$(id -u)",gid="$(id -g)" "$IMG" "$MNT"
  MOUNTED_BY=sudo
fi
echo "mounted by: $MOUNTED_BY"

# One that stays, so the walk is checked as well as the recovery, and two
# that go. A long name on one of them, because exFAT stores names across
# several directory entries and a reader that reassembles them wrongly
# should be caught here rather than on a case.
python3 - "$MNT/kept-live.jpg" <<'PY'
import sys
from PIL import Image
Image.new("RGB", (120, 90), (30, 90, 170)).save(sys.argv[1], "JPEG")
PY

python3 - "$MNT/deleted-small.jpg" <<'PY'
import sys
from PIL import Image
Image.new("RGB", (64, 48), (200, 40, 40)).save(sys.argv[1], "JPEG")
PY

python3 - "$MNT/a rather long file name for this deleted picture.jpg" <<'PY'
import sys
from PIL import Image
picture = Image.new("RGB", (400, 300))
pixels = picture.load()
for y in range(300):
    for x in range(400):
        pixels[x, y] = ((x * 7) % 256, (y * 3) % 256, (x ^ y) % 256)
picture.save(sys.argv[1], "JPEG", quality=92)
PY
sync

{
  echo "# What was written to $(basename "$IMG"), hashed before any of it"
  echo "# was deleted, on $(uname -srm)."
  echo "# $(mkfs.exfat -V 2>&1 | head -1)"
  echo "# The expected answer comes from what went in, not from a reader."
  echo "# sha256  size  name  state"
} > "$INTENDED"

write_row() {
  local f="$1" state="$2"
  printf '%s  %s  %s  %s\n' \
    "$(sha256sum "$MNT/$f" | cut -d' ' -f1)" \
    "$(stat -c%s "$MNT/$f")" \
    "$(echo "$f" | tr ' ' '_')" "$state" >> "$INTENDED"
}
write_row "kept-live.jpg" live
write_row "deleted-small.jpg" deleted
write_row "a rather long file name for this deleted picture.jpg" deleted

# Deleted last, so nothing reuses their entries or their clusters.
rm -f "$MNT/deleted-small.jpg" \
      "$MNT/a rather long file name for this deleted picture.jpg"
sync
cleanup
trap - EXIT

echo "wrote $IMG"
echo "wrote $INTENDED:"
cat "$INTENDED"

if command -v fls >/dev/null && command -v icat >/dev/null; then
  echo
  echo "cross-checking against The Sleuth Kit"
  agreed=0
  while read -r line; do
    inode=$(echo "$line" | awk '{print $2}' | tr -d ':')
    [ -z "$inode" ] && continue
    digest=$(icat "$IMG" "$inode" 2>/dev/null | sha256sum | cut -d' ' -f1)
    grep -q "^$digest" "$INTENDED" && agreed=$((agreed + 1))
  done < <(fls -r -p "$IMG" 2>/dev/null)
  echo "  The Sleuth Kit returns the recorded bytes for $agreed file(s)"
  echo "  NOTE: this program reads exFAT through pytsk3, which is The"
  echo "  Sleuth Kit, so this is the same library answering twice. The"
  echo "  independent comparison is FME_NO_TSK=1, which the test runs."
else
  echo
  echo "The Sleuth Kit is not installed, so nothing was cross-checked."
fi
