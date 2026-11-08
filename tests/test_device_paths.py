"""Two things a phone case needs that a disk-image tool does not.

**One photograph is not three photographs.** Android exposes the same
storage through several paths and a full extraction records all of them, so
registering each spelling multiplies a device's media by three or four --
in the counts, in the duplicate groups, and in a report somebody has to
explain in evidence.

**A video keeps its location in the QuickTime structure, not in EXIF.**
Until this was read, a case whose pictures were placed and whose videos
were not looked like a case whose videos were taken somewhere unrecorded.
They were not: nobody had read it.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fme.engines import storage_views, videometa

ok = True

print("the same file under several Android views")
same = [{"device_path": path, "size": 2048, "sha256": "a" * 64} for path in (
    "/data/media/0/DCIM/Camera/IMG_0001.jpg",
    "/storage/emulated/0/DCIM/Camera/IMG_0001.jpg",
    "/mnt/user/0/emulated/0/DCIM/Camera/IMG_0001.jpg",
    "/sdcard/DCIM/Camera/IMG_0001.jpg")]
app = [{"device_path": path, "size": 99, "sha256": "b" * 64} for path in (
    "/data/data/com.whatsapp/databases/msgstore.db",
    "/data/user/0/com.whatsapp/databases/msgstore.db",
    "/data_mirror/data_ce/null/0/com.whatsapp/databases/msgstore.db")]

result = storage_views.collapse(same + app)
ok &= len(result["files"]) == 2 and result["folded_away"] == 5
print(f"  7 rows became {len(result['files'])} file(s), "
      f"{result['folded_away']} folded away")

kept = {entry["device_path"] for entry in result["files"]}
ok &= "/storage/emulated/0/DCIM/Camera/IMG_0001.jpg" in kept
print(f"  the spelling kept is the same every time: "
      f"{'/storage/emulated/0/...' if '/storage/emulated/0/DCIM/Camera/IMG_0001.jpg' in kept else 'NO'}")

# An examiner asked where a file was should be able to say all of them.
photo = next(e for e in result["files"] if "DCIM" in e["device_path"])
ok &= len(photo.get("also_seen_at", [])) == 3
print(f"  and the other paths are recorded: "
      f"{len(photo.get('also_seen_at', []))} of them")

print("\nwhat must never be folded together")
# Credential-encrypted and device-encrypted hold different files.
pair = storage_views.collapse([
    {"device_path": "/data/user/0/com.x/f.db", "size": 1, "sha256": "c" * 64},
    {"device_path": "/data/user_de/0/com.x/f.db", "size": 1, "sha256": "c" * 64}])
ok &= len(pair["files"]) == 2
print(f"  credential- and device-encrypted stay apart: "
      f"{len(pair['files'])} file(s)")

# A second user, or a work profile, is a different person.
users = storage_views.collapse([
    {"device_path": "/storage/emulated/0/a.jpg", "size": 1, "sha256": "d" * 64},
    {"device_path": "/storage/emulated/10/a.jpg", "size": 1, "sha256": "d" * 64}])
ok &= len(users["files"]) == 2
print(f"  user 0 and user 10 stay apart: {len(users['files'])} file(s)")

# And views that do not hold the same bytes are kept in full: a file being
# written while the extraction ran differs between views, and quietly
# keeping one of two different files is worse than reporting both.
differ = storage_views.collapse([
    {"device_path": "/data/media/0/log.db", "size": 10, "sha256": "e" * 64},
    {"device_path": "/storage/emulated/0/log.db", "size": 12, "sha256": "f" * 64}])
ok &= len(differ["files"]) == 2 and len(differ["disagreed"]) == 1
print(f"  views that disagree are kept in full: "
      f"{len(differ['files'])} file(s), {len(differ['disagreed'])} reported")

print("\nwhere a video was taken")


def atom(kind, body):
    return struct.pack(">I", 8 + len(body)) + kind + body


text = b"+53.4808-002.2426/"
android = (atom(b"ftyp", b"isom" + bytes(8))
           + atom(b"moov", atom(b"udta", atom(
               b"\xa9xyz", struct.pack(">HH", len(text), 0x15C7) + text))))
found = videometa.read_bytes(android)
ok &= found["found"] and abs(found["latitude"] - 53.4808) < 1e-6
print(f"  Android: {found.get('latitude')}, {found.get('longitude')}")

key = atom(b"mdta", videometa.APPLE_KEY)
keys = atom(b"keys", struct.pack(">II", 0, 1) + key)
value = atom(b"data", struct.pack(">II", 1, 0) + b"+40.7580-073.9855+010.000/")
ilst = atom(b"ilst", struct.pack(">I", 8 + len(value))
            + struct.pack(">I", 1) + value)
apple = (atom(b"ftyp", b"qt  " + bytes(8))
         + atom(b"moov", atom(b"meta", keys + ilst)))
found = videometa.read_bytes(apple)
ok &= found["found"] and abs(found["longitude"] + 73.9855) < 1e-6
print(f"  Apple  : {found.get('latitude')}, {found.get('longitude')}")

# The sign is the hemisphere. Dropping it moves a point to the opposite
# side of the planet, which is why the format is parsed rather than having
# numbers pulled out of it.
print("\n  the notations, and their hemispheres:")
for written, latitude, longitude in (
        ("+53.4808-002.2426/", 53.4808, -2.2426),
        ("-33.8688+151.2093/", -33.8688, 151.2093),
        ("+5329.00-00214.00/", 53.483333, -2.233333),
        ("+352139-1384339/", 35.360833, -138.7275)):
    place = videometa._degrees(written)
    right = (place and abs(place["latitude"] - latitude) < 1e-5
             and abs(place["longitude"] - longitude) < 1e-5)
    ok &= bool(right)
    print(f"    {written:22} -> {place['latitude']}, {place['longitude']}"
          if place else f"    {written:22} -> NOT READ")

# Anything that is not a location must be refused rather than guessed at.
for rubbish in ("hello", "+99.0+200.0/", "", "53.4808,-2.2426"):
    ok &= videometa._degrees(rubbish) is None
print(f"  anything that is not a location is refused: True")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
