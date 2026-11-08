# Forensic Media Extractor

Recovers images, video and audio from forensic images, live devices, mounted
folders, report packages and databases — then exports the results in the
formats review platforms actually read.

The release number lives in the `VERSION` file and is reported by both GUIs,
the console banner, and the Project VIC and LAVA exports, so a report can be
traced back to the build that produced it. The engine has its own version
(`media_core.__version__`) which tracks changes to the carving and extraction
code; the two move independently.

A local browser GUI, a command-line tool, or an importable library. Started
life as a FAT32 recovery tool; `fat32_core.py` is still in here and still
does that job, but the rest of the package is file-system agnostic.

**What it reads:** raw/dd, split raw, EnCase E01/Ex01, VMDK/VHD/QCOW, live
block devices, mounted folders, Cellebrite `.ufd` chunk sets, UFDR and other
zip/tar packages, and Project VIC exports.

**Where it looks:** every file system TSK supports (FAT12/16/32, exFAT, NTFS,
HFS+, APFS, ext2/3/4 and the rest), unallocated space by signature carving,
SQLite BLOB columns including overflow chains, compound files such as
documents and archives, and base64/hex-encoded images embedded in text.

**What it writes:** the recovered files with a full manifest (MD5, SHA-1,
SHA-256, provenance), Project VIC JSON for Griffeye, C4All-style XML reports,
and a LAVA output set for the LEAPP Artifact Viewer App.

## License

Copyright (c) 2026 Christopher A. Tankelewicz (ThinkTank4n6). Released under the
[MIT License](LICENSE).

**No warranty.** The program is provided "as is", without warranty of any
kind, express or implied, and the author is not liable for any claim or
damages arising from its use (see `LICENSE`). It is a tool for an examiner and
does not replace the examiner's own verification of what it reports;
[docs/VALIDATION.md](docs/VALIDATION.md) says what it has and has not been
tested against.

Code, models and libraries by other people keep their own licenses -- among
them The Sleuth Kit (IBM Public License 1.0 / Common Public License 1.0,
through pytsk3), libewf (LGPL-3.0) and pillow-heif's bundled codecs (LGPL-3.0
and GPL-2.0). [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) lists them all;
the standalone build carries that file, the full license texts and each bundled
package's own license files, and the program shows them under File → Licenses.

## Acknowledgments

FME builds on other people's open-source work, and is grateful for it:

- **[GLEAPP](https://github.com/abrignoni/GLEAPP)** by Alexis Brignoni and
  charpy4n6 (MIT). FME's reader for QNX, F2FS, Btrfs, XFS, ReFS and other file
  systems (qnxprobe), the carver's length judge (mediacarve), the Windows Search
  (ESE) reader, the image models and the case map's assets were taken from
  GLEAPP, unchanged.
- **Alexis Brignoni**'s standalone readers, also MIT:
  [ewfprobe](https://github.com/abrignoni/ewfprobe) (EnCase images without
  libewf), [exoprobe](https://github.com/abrignoni/exoprobe) (Android ExoPlayer
  caches) and [c2pareader](https://github.com/abrignoni/c2pareader) (Content
  Credentials).
- **[The Sleuth Kit](https://www.sleuthkit.org/)** by Brian Carrier and
  contributors, read through [pytsk3](https://github.com/py4n6/pytsk).
- **[libewf](https://github.com/libyal/libewf)** by Joachim Metz, for EnCase
  images.
- **[Impacket](https://github.com/fortra/impacket)** (Fortra), on which that
  ESE reader is built.
- The authors of the image models (DINOv2, SFace, YuNet), MapLibre, PMTiles and
  Protomaps, and the Python libraries listed in
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

FME is an independent project, not a release of any of these, and their authors
are not responsible for it.

## Files

### Layout

```
fme/
  cli.py            one entry point; what the executable is built from
  engines/          reading evidence
      media_core      run_extraction, the options and the command line;
                      re-exports the modules below, split out of it
        engine_common, image_readers, signatures, length_parsers,
        carve_judging, embedded, fs_walk, deleted_recovery, carving,
        volume_survey, sources
      output          storing recovered files, hashing, the manifest
      fat32_core      the original FAT32 parser, the engine's fallback
      db_extract      media out of SQLite BLOBs
      exif_read       EXIF, camera and GPS, no dependency
      similarity      three tiers of look-alike grouping (needs Pillow)
      mount_detect    attached drives and mount points
  exports/          writing reports
      projectvic_export, c4all_export, lava_export, lava_launch,
      kml_export, report_html, vic_import
  review/           working the results
      casedb          categories, flags, notes, audit log
      hashsets        Project VIC, NSRL and custom hash sets
      triage_report   manifest breakdown for comparison
  web/              the browser interface
      media_gui, app, templates/
tests/              every check, plus the fixtures they build
```

**Double-click `launch.py`** at the top of the source tree. It opens the
browser interface and puts `media_out/` beside the project, which is why it
lives there rather than next to the code: a double-clicked file runs with its
own folder as the working directory.

**The same file runs every tool, from any directory, with nothing
installed:**

```bash
python "C:\tools\forensic_media_extractor\launch.py" store --inspect VIC_US.json
python "C:\tools\forensic_media_extractor\launch.py" store --store D:\hashes\store.db --verify
python "C:\tools\forensic_media_extractor\launch.py" extract -i case.E01 -o D:\out
python "C:\tools\forensic_media_extractor\launch.py" --help
```

`python -m fme <tool>` does the same, but **only from the project root** —
Python looks for the package in the current directory, so from anywhere else
it reports `No module named fme`. The launcher has no such restriction
because it puts the package on the path itself. Once installed with
`pip install .`, the `fme` command works from anywhere too.

Or run it four ways, none of which needs the package installed except the
third:

```bash
python -m fme extract -i case.E01 -o ./out      # from the project root
python fme/engines/media_core.py -i case.E01 -o ./out   # any module, directly
pip install .                                   # then: fme extract ...
python build_exe.py                             # a self-contained executable
```

Every module that has a command line still runs as a plain script, from any
directory, installed or not — `media_core.py`, `hashsets.py`,
`triage_report.py`, the exporters and `media_gui.py`. Each one puts
the project root on the path when it finds itself running without a package
context.

As a library:

```python
from fme.engines import media_core
summary = media_core.run_extraction("case.E01", "./out", mode="both")
```

**The engines**

- `media_core.py` — the heart of it: every input format, every file system,
  carving, database blobs, compound files. Usable from the command line or
  importable. Everything else is either a front end or an export format.
  Since v2026.11.314 the work is in eleven modules beside it, each using
  only the ones before it (the list is in media_core's docstring);
  media_core runs a case and re-exports every name, so code that imports
  it is unchanged.
- `fat32_core.py` — the original FAT32 parser, kept because it still works
  and because `media_core` falls back to it when `pytsk3` is not installed.
- `db_extract.py` — pulls media out of SQLite BLOB columns, including blobs
  split across overflow pages.

**Front ends**

- `media_gui.py` — the interface. File picker, attached-drive list, live
  progress, cancel, gallery, custom signatures, review, reports, exports.
- The original FAT32 browser (`app.py`) was retired in v2026.11.319: runs
  started from it never became cases (no case.db, not in the case list,
  never checked against the hash store). `fme fat32gui` now says so.

**Exports**

- `projectvic_export.py` — Project VIC (VICS 2.0) JSON for Griffeye.
- `c4all_export.py` — C4All-style XML reports (`C4M_Index.xml`,
  `C4P_Index.xml`, `Case_Report.xml`, `Victim_Report.xml`, `Results.txt`).
- `lava_export.py` — a LAVA output set for the LEAPP Artifact Viewer App.
- `lava_launch.py` — finds a locally installed LAVA and opens a project in it.
- `vic_import.py` — reads Project VIC files: imports their media, or uses a
  hash set to flag known files.
- `CHANGELOG.md` — what changed in each numbered release.
- `MANUAL.md` — the manual, also at **Menu → Manual** in the interface.
- `SERVER_SETUP.md` — setting up the shared hash store, step by step.
- `docs/AUDIT.md` — where this program stands against the other tool in this space.
- `fme/vendor/` — two readers written by somebody else, used under MIT, with
  their licences and provenance. See the manual's "Code from other people".
- `triage_report.py` — breaks a manifest down by engine, signature and size,
  for comparing results against another tool.
- `hashsets.py` — loads Project VIC, NSRL and custom hash sets and matches a
  run against them.
- `similarity.py` — three tiers of grouping. Optional; needs Pillow.
- `casedb.py` — categories, flags, notes and the audit log, in `case.db`.
- `registry.py` — the list of cases processed here, in `cases.db`.
- `savedset.py` — your own categories and flags, carried between cases.
- `recover.py` — putting a case back after a grading went wrong.
- `settings.py` — what an examiner's machine remembers between sessions.
- `hashserver.py` — serves one store to a unit, with per-user access.
- `remotestore.py` — the client for it: the same methods, over HTTP.
- `fme.py` — one entry point for every tool; what the executable is built from.
- `build_exe.py`, `packaging/fme.spec` — build a self-contained
  executable. See `BUILDING.md`.
- `report_html.py` — the quick contact sheet and MD5 list.
- `report.py` — the case report: scope, header, and every format.
- `exif_read.py` — EXIF, camera and GPS from images and video, no dependency.
- `containers.py` — thumbnail caches, PDFs and saved pages.
- `ntfs_mft.py` — deleted files and resident data from the NTFS MFT.
- `winsearch.py` — naming cached thumbnails from a Windows Search index.
- `imaging.py` — the one place an image is opened: HEIC, truncation, warnings.
- `screening.py` — skin-tone proportion and face counts, for ordering a backlog.
- `frame_match.py` — matching stills to the videos they came from.
- `protocol.py` — the hand-over report; `docx_parts.py` — the Word pieces.
- `export_files.py` — handing over the files themselves, verified.
- `appcache.py` — ExoPlayer caches and Snapchat content bundles.
- `video_frames.py` — frames from unplayable video. Optional; needs OpenCV.
- `kml_export.py` — geolocated media as KML or KMZ.

**Test fixtures and checks**

- `make_test_image.py` — generates a small synthetic FAT32 image with a few
  files and one "deleted" JPEG, for trying the tool out without a real image.
- `test_filesystems.py` — the file system walk and database blob extraction
  against NTFS, exFAT, HFS+, APFS, ext4 and FAT32.
- `test_false_positives.py` — the failures found on real evidence: a
  zero-filled `.MOV`, an entry claiming more space than the image holds.
- `test_compound_scan.py` — images inside a `.docx` and an EXIF thumbnail.
- `test_similarity.py` — look-alike grouping, skipped cleanly without Pillow.
- `test_metadata.py` — EXIF and video metadata built byte by byte, plus KML.
- `test_casedb.py` — decisions surviving a restart, the locked scheme.
- `test_hashstore.py` — importing, matching and contributing back.
- `test_registry.py` — the case list surviving a restart and being rebuilt.
- `test_containers.py` — thumbcache, PDF and saved-page extraction.
- `test_report.py` — report scope, and what a report says about itself.
- `test_manual.py` — the manual's claims against the code.
- `test_endpoints.py` — every endpoint, driven as the browser drives them.
- `test_gui_boot.py` — the page's script, actually run: does it start at all.
- `test_lava.py` — the layout a LAVA project needs to render.
- `test_appcache.py` — rejoining a cached video, and stopping at a gap.
- `test_gui_markup.py` — every element the interface script reaches for
  exists, and anything built at run time is reached through a guard.
- `make_media_test_image.py` — generates a FAT32 image full of real media:
  live JPEG/PNG/GIF/MP4/AVI, a deleted JPEG whose directory entry survives
  but whose FAT chain was freed, and two files with no directory entry at
  all. Use it to exercise a run end to end.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt      # .venv\Scripts\pip on Windows
python3 -m fme                                  # the interface, on port 5001
```

Four requirements files, because pip aborts the whole install on the first
package that fails to build and the compiled ones are the fragile part:

| file | what it gets you |
| --- | --- |
| `requirements-minimal.txt` | Flask only. The interface, carving, the FAT32 walk, SQLite blob extraction and every export format. Nothing compiles. |
| `requirements.txt` | The above plus `pytsk3` (exFAT, NTFS, HFS+, APFS, ext2/3/4) and `libewf-python` (.E01). |
| `requirements-vm.txt` | VMDK, VHD and QCOW2 images. |
| `requirements-dev.txt` | Everything, plus Pillow for the test suite. |

Start with `requirements.txt`. If `pytsk3` or `libewf` will not build on the
machine in front of you, fall back to `requirements-minimal.txt` and add the
rest later — nothing needs reinstalling. `pytsk3` compiles against libtsk on
Linux and macOS, so install `libtsk-dev` (or `brew install sleuthkit`) first;
the file has the exact commands.

Python 3.8 or newer.

It opens **http://127.0.0.1:5001** in a browser.

Click **Browse…** next to the path box to navigate the local filesystem
(of the machine running the server) and pick an image file, instead of
typing the path by hand. This is a server-side directory listing, not a
browser file-upload — so it works for very large images without actually
transferring their bytes.

It listens on this machine only. Reaching it from another machine has to be
asked for (`--host`), and then needs the access key it prints.

## Image format support

- **Raw / dd images**: `.img`, `.dd`, `.raw`, or a raw block device path.
  Works out of the box.
- **Whole-disk images with an MBR partition table**: the tool reads the MBR
  and locates the first FAT-typed partition (FAT12/16 types `0x01`, `0x04`,
  `0x06`; FAT32 types `0x0B`, `0x0C`, `0x0E`) automatically — you don't need
  to pre-carve out the volume yourself.
- **EWF / EnCase (`.E01`, `.Ex01`, ... segments)**: read by libewf (`pyewf`)
  when it is installed, and otherwise by the vendored acquisition reader,
  which needs nothing installed. Either way the run records the hashes the
  acquisition stored, and with **Hash the source** (`--hash-source`) checks
  the media against them.
- GPT, exFAT, NTFS, HFS+, APFS, ext and the rest are read by the engine as
  well; `fat32_core` on its own reads MBR or unpartitioned FAT12/16/32.

## Try it without a real image

```bash
python3 make_test_image.py    # writes test_image.img in this directory
python3 -m fme
```

Pick `test_image.img` with **Browse…** and start a run: three live files,
plus one recovered by the carve (a JPEG whose directory entry was stripped
and FAT chain zeroed, simulating a real deletion).

## Media Extraction (all file systems)

The interface (and `media_core.py` on the command line) recovers every known
image and video file from an image, whatever its file systems.

```bash
# both engines, everything
python3 media_core.py -i evidence.E01 -o ./out

# carving only, cluster aligned — far fewer false positives
python3 media_core.py -i disk.dd -o ./out --mode carve --align 512

# file system only, images only, keep NTFS system files
python3 media_core.py -i case.001 -o ./out --mode fs --images-only --include-system
```

### Driving it

**The interface** — `python3 media_gui.py` (or `python -m fme`) starts a local server and opens a
browser window. Nothing is uploaded; the image is read directly off disk.

```bash
python3 media_gui.py                  # opens the GUI on port 5001
python3 media_gui.py -i case.E01      # pre-fill the image path
python3 media_gui.py --port 5050 --no-browser
```

It gives you a **Browse…** picker that flags likely image files, a
**Load / Inspect** button that opens the image and reports what it found —
container type, size, segment count, partitioning, and every file system it
could mount — before you commit to a long run, engine and alignment options,
**custom carve signatures** (header, optional footer, maximum size, with the
option to derive them from a sample file),  a progress bar that names the current phase, a **Cancel**
button that stops the run and keeps everything recovered so far, and a
results panel that can show either a **gallery** — thumbnails of recovered
photos, inline players for browser-playable video, extension tiles for the
rest — or a **table** with sizes, engine, source path and SHA-256. Filter
chips narrow the view to images, videos, deleted files or carved files, and
the manifest downloads from the same toolbar.

**From the command line** — `python3 media_core.py -i case.E01 --inspect -o /tmp`
prints the same image report and exits without extracting anything. Useful
for confirming an E01 opens at all before queueing work.

### Custom carve signatures

The interface has a **Custom Signatures** tab: add one box per sample file (with a Browse button on each),
press **Analyze**, and it shows the per-file header and footer bytes in hex
and ASCII, then fills in an editable signature from the bytes **common to
all** the samples. Give it several files of the same type — one sample's
header often contains bytes that vary between files (timestamps, random
IDs), and the common-to-all row is what strips those out. Edit anything, then
**Add Signature**; active signatures are listed with extension, header,
footer and max size, and can be removed.

Signatures are held server-side and apply to every extraction started from
the program until removed, so nothing has to be re-entered per job. The carve
rule: run to the first footer, or to the maximum
size or the next file header if no footer is given. Extensions that match a
known media type are filed under `images/` or `videos/`; anything else lands
in `other/`.

### Load, then Use

Extraction runs against an image you have explicitly accepted:

1. **Load / Inspect** opens the image read-only and reports what it found —
   container type, size, segment count, partitioning, and every file system
   it could mount, plus warnings for anything it could not read.
2. **Use This Image** accepts it. A green banner names the file in use and
   what is on it, the path locks, and Start Extraction becomes available.
3. **Change** releases it so a different image can be loaded.

Start Extraction is refused until an image has been accepted, and refuses a
path that is not the accepted one, so a half-typed path can never become the
thing that gets carved. The accepted image survives a browser refresh.

### Keeping the files in step

`media_gui.py` checks `media_core.__version__` and the signature of
`run_extraction()` at startup. If the two files come from different releases
it shows a banner naming what is missing and disables just those controls,
rather than failing mid-job. The header badge always shows both versions, and
hovering it shows the full path of each file. Both files are shipped together
in the archive; if you copy one out, copy both.

If something still looks wrong:

```bash
python3 media_gui.py --doctor
```

It prints which `media_core.py` was actually imported, its version and
features, what `run_extraction()` accepts, every other copy of
`media_core.py` it can find nearby, and any `__pycache__` directories that
could be shadowing an update. Python imports from the first directory on
`sys.path` that has the file — normally the folder you launched from, which
is not necessarily where you unpacked the newest release.

`run_extraction()` also now accepts `progress_cb`, `cancel_cb`,
`extra_signatures` and `source` whether they arrive as named arguments or in
`**options`, so a caller from a slightly different release no longer fails
the run over an argument the function understands perfectly well.

### Two engines

**File system engine** — enumerates the volume system, mounts every
recognized file system through The Sleuth Kit, and walks it recursively,
recovering allocated *and* deleted entries with path, inode and MAC times.
Files are classified by extension first and by magic bytes second, so
renamed and extensionless media are still found.

Nothing in this engine is FAT-specific. It asks TSK to mount whatever is at
each partition offset and walks whatever comes back, so it handles every file
system the local TSK build supports — FAT12/16/32, exFAT, NTFS, HFS+/HFSX,
APFS, ext2/3/4, UFS, YAFFS2, ISO9660 and the rest — and it reports the type
TSK gives it rather than a fixed list. The same is true of the SQLite blob
extraction: it hooks into the walk, not into any particular file system, so a
database on NTFS or ext4 is read exactly as one on FAT32 is. `test_filesystems.py`
exercises the walk and the blob extraction against NTFS, exFAT, HFS+, APFS,
ext4 and FAT32 in turn, checking that a 258 KB JPEG spread over 63 overflow
pages comes back byte-exact on each.

The carving engine has no file system involvement at all: it scans the image
bytes, so it works on unsupported, damaged and unformatted volumes too.

`fat32_core` is used only as a fallback when `pytsk3` is not installed, and
only for FAT32.

If `pytsk3` is not installed it falls back to `fat32_core` for FAT32
volumes, so the file system engine still works with no extra dependencies —
including deleted directory entries, which it reads contiguously from the
recorded first cluster since FAT32 has already zeroed the chain.

**Carving engine** — finds file headers anywhere in the image and determines
each file's true length by parsing its structure rather than guessing:
JPEG marker walk (survives EXIF thumbnails and progressive scans), PNG chunk
walk to IEND, GIF block walk, RIFF size fields, ISO-BMFF box walk for
MP4/MOV/3GP/HEIC/AVIF, EBML for MKV/WebM, ASF object walk, FLV tag walk, Ogg
page walk, MPEG-TS sync-lock, MPEG-PS pack walk, TIFF IFD and strip-extent
walk (covers CR2, NEF, DNG), BMP, ICO, SWF and EMF. Where a length genuinely
cannot be derived, the carve stops at the next file header instead of running
to a fixed cap.

Both engines write through one output manager that de-duplicates by SHA-256,
so a file recovered by both appears once.

### Media inside databases

Carving cannot recover media stored in a database. SQLite splits any payload
bigger than about one page across a chain of overflow pages, each carrying a
four-byte pointer to the next, so a 400 KB photo is not contiguous on disk.
A carver finds the JPEG header, runs into a page pointer, and produces
something that is the wrong length and does not decode. Thumbnail caches,
messaging databases, Photos.sqlite and browser caches all store media this
way.

The file system engine now opens every SQLite file it finds and reads the
blobs out properly: it walks the schema and each table b-tree (rowid and
WITHOUT ROWID), decodes the record format, reassembles overflow chains, and
optionally sweeps free space — freelist pages, per-page gaps and freeblocks —
where deleted rows survive. The blob's stored length ends the file, so unlike
carving there is no guessing where it stops.

Measured on a FAT32 image holding a 720 KB gallery database:

| | carving alone | with database extraction |
| --- | --- | --- |
| PNG thumbnail, 8 KB | missed | byte-exact |
| JPEG, 400 KB (overflowed) | 422,848 bytes, corrupt from byte 472, will not decode | byte-exact |
| MP4, 300 KB (overflowed) | missed | byte-exact |

Results are tagged `engine=database` with the table, column and rowid in the
source path, and the GUIs have an **In databases** filter. Turn it off with
`--no-databases`, or skip just the deleted-row sweep with `--no-db-freespace`.

What to expect from deleted rows: a blob small enough to have lived on one
page comes back byte-exact. A blob that overflowed leaves only its first few
KB in the freeblock, and whether the rest can be followed depends on whether
the pointer to the freed chain survived — when it has not, you get the
truncated local part, flagged in the manifest as possibly partial. If the
database was built with `secure_delete` on (some Android builds do this),
freed pages are zeroed and nothing is recoverable from them at all.

Not supported: encrypted databases (SQLCipher), ESE (.edb), and
Thumbs.db/thumbcache. WAL and journal side files are not merged — point
`db_extract.py` at them separately if they matter.

### What you can point it at

Six kinds of input, all through the same `-i` argument and the same GUI box.
The tool works out which it is.

| input | example | what you get |
| --- | --- | --- |
| image file | `evidence.E01`, `disk.dd`, `img.001` | everything: file system walk, deleted entries, carving, database blobs |
| raw device | `/dev/sdb1`, `/dev/disk2s1`, `\\.\PhysicalDrive1` | the same, on a live disk — the whole volume including unallocated space |
| folder | `/media/usb`, `E:\`, an export directory | the files the OS hands out: media, database blobs, and optionally media embedded inside other files |
| Cellebrite `.ufd` | `extraction.ufd` + its `.bin` chunks | the chunks stitched into one image, then treated exactly like an image file — carving and deleted files included |
| package | `report.ufdr`, `.zip`, `.ab` | every media entry read straight out of the archive, plus blobs from any database inside it |
| Project VIC | `case.json` | the media it references, verified against its own hashes — see below |

**Raw devices.** A USB stick read as a device gives you what an image would,
without imaging it first — sector-aligned reads, size taken from the device
rather than a directory entry, opened read-only. It needs root on Linux and
macOS, an Administrator prompt on Windows. Read-only opening is *not* a write
blocker: the OS can still touch a mounted volume. Unmount it or use a
hardware blocker before touching anything that matters.

**Folders.** This is the mode for a stick the OS has already mounted, a
network share, or a logical export. There is nothing to carve here — a mount
point serves files, not the volume behind them — so deleted data is out of
reach. To recover deleted files from a USB stick you must read the device.
The tool says so in the inspection panel rather than quietly finding less.

What folder mode does do: classify every file by extension and then by magic
bytes, so renamed and extensionless media are still caught; open any SQLite
database it finds and pull the blobs out; and, with `--carve-files`, scan
inside non-media files for embedded media — office documents, mail stores,
application caches. Symlinks are not followed, and `System Volume
Information`, `$RECYCLE.BIN` and the macOS metadata folders are skipped.

Both GUIs have an **Attached drives and folders…** button that lists the
mount points and block devices on the machine, marks which are removable, and
warns when the program is not running elevated — before a raw read fails with
a bare permission error.

### Cellebrite extractions

A **physical extraction** (`.ufd` plus numbered `.bin` chunks) is a split raw
image with an INI manifest. Point at the `.ufd` and the chunks are resolved
and read as one contiguous image, so everything applies: the file system
walk, deleted entries, carving unallocated space, database blobs. Key names
in the manifest vary between UFED versions, so rather than matching on one,
every value naming a file that exists beside the `.ufd` is taken in order,
falling back to the sibling chunks on disk. Verified against a three-chunk
set: identical output to the same image as a single file.

A **UFDR report** is a zip. Entries are read straight out of it — nothing is
unpacked first — and classified by extension then magic bytes, so media
stored under hashed or numbered names is still found. SQLite entries get the
blob treatment too, which matters for the app databases UFDR reports carry
alongside the media. Encrypted or damaged entries are counted, not fatal.

The distinction worth keeping in mind: a UFDR is a *report*, so it holds what
Cellebrite decided to include. For deleted data you need the physical
extraction. The inspection panel says so rather than quietly finding less.

### Project VIC files

`vic_import.py` reads VICS JSON in the two roles it turns up in.

**As an export with its media.** Reads the JSON, resolves every
`RelativeFilePath`, re-hashes each file against the record, and writes the
media out in the normal layout — so a VICS export folds into the same
manifest, gallery and C4All/VICS reports as anything else. Missing files,
hash mismatches and records whose bytes are not media are all counted and
reported rather than passed over.

```bash
python3 vic_import.py -i case.json -o ./out
```

**As a hash set.** The national sets are hundreds of megabytes of categorized
hashes with no media at all. Pointed at a manifest from a previous run, it
flags which recovered files are in the set and what category the set gives
them:

```bash
python3 vic_import.py -i VIC_US_2026.json --match out/manifest.json --csv matches.csv
```

Both are in the GUI's **Project VIC** panel once a run finishes.

The set decides the category; nothing here classifies anything. Files not in
the set are reported as **unknown**, which is not the same as cleared, and
the output says so.

Records stream one object at a time rather than loading the file: measured on
the 217 MB set supplied, 160,124 records in 9.6 seconds at 124 MB of memory,
and a full hash-set match against a finished job in about four seconds.

### Containers

| Format | Requirement |
| --- | --- |
| raw / dd / img / bin, and raw devices | none |
| split raw (`.001`, `.002`, …) | none — segments are stitched automatically |
| EnCase `.E01` / `.Ex01` | `pip install libewf-python` |
| VMDK / VHD / QCOW | `pip install libvmdk-python libvhdi-python libqcow-python` |
| AFF4, AD1 | not supported — convert to raw or E01 first |

`GET /api/media/capabilities` reports what the local install can actually
open; the tab shows this before you start a job.

### Output

```
media_out/<job_id>/
  images/jpg/recovered_0000_3_IMG_0001.jpg
  videos/mp4/recovered_0004_10_VID_0003.mp4
  manifest.csv      one row per file, written as it goes
  manifest.json     same data plus a run summary
```

Naming follows the `fat32_core` carve convention (`recovered_NNNN_…`), with
a `deleted_` prefix for recovered deletions. Manifest fields: engine,
category, path, size, MD5, SHA-256, extension, source path in the volume,
inode, file system type, partition, byte offset for carved files, deleted
flag, signature, and MAC times.

### Interop with fat32_core

`media_core` deliberately reuses the existing conventions:

- `ImageSource` objects (`RawFileSource`, `EwfSource`, `OffsetSource`) can be
  passed to `media_core.wrap_source()` — an already-open session or a single
  partition can be handed straight over.
- `FileSignature` objects, including the ones the Custom Signatures tab
  produces via `signature_from_hex()`, are accepted as `extra_signatures` and
  carved by header/footer exactly as `carve_unallocated` does. Custom
  signatures defined against an open image apply to media jobs too.
- `progress_cb(done, total, recovered, phase)` mirrors the `fat32_core` carve
  callback with one extra argument naming the current phase.
- `human_size()` behaves the same.

`run_extraction(image_path, out_dir, ...)` is the single entry point the
Flask layer uses; it returns a summary dict and takes every option in
`media_core.DEFAULT_OPTIONS` as a keyword.

## Exporting for Griffeye / C4All

`manifest.json` is a working record, not an interchange format — Griffeye will
not read it. `c4all_export.py` turns a finished run into the report set the
C4All EnScript produces, which Griffeye and the C4All viewer already ingest:

```bash
python3 c4all_export.py -m out/manifest.json -o ./export \
    -c DC23-282 -a "J. Smith" --evidence "1TB WD from inside HP 20 AIO"
```

Both GUIs have an **Export for Griffeye / C4All** panel that appears once a
run finishes, taking the same fields.

It writes:

```
export/
  DVD01(DC23-282)/01/00000001.MP4     renamed and batched for disc
  DVD01(DC23-282)/01/00000002.JPG
  C4M_Index.xml       movie index    (<Movie> records)
  C4P_Index.xml       picture index  (<Picture> records)
  Case_Report.xml
  Victim_Report.xml
  Results.txt
```

Files are hard-linked into the batch layout, so the export costs no extra
disk; `--copy` forces real copies, and a hard link that cannot be made
(different volume, or a filesystem without them) silently becomes a copy.
`--media` sets the batch size — `cd`, `dvd`, `dvd-dl`, `bd`, `none`, or a size
like `8G` — and a new numbered subfolder starts every 1000 files.

Fields map like this: MD5 and SHA-1 into `hash` and `sha1Hash` (SHA-1 is now
computed during extraction for this reason), MAC times as epoch seconds and
empty when unknown, carved files as `File, Unallocated Clusters` with the byte
offset in `fileoffset` and `physicalLocation`, deleted file system entries as
`File, Deleted, Archive`, and `fullpath` rooted at
`CASE\evidence item\path`. Category is written as `0` — nothing in this
pipeline classifies content, so grading stays with the examiner in Griffeye.

### LAVA (LEAPP Artifact Viewer App)

```bash
# during the run
python3 media_core.py -i evidence.E01 -o ./out --lava DC26-253

# afterwards, from the manifest
python3 lava_export.py -m out/manifest.json -o out/lava -c DC26-253
```

or tick **Also write a LAVA output set** in the GUI's export panel.

It writes what LAVA expects:

```
lava/
  _lava_artifacts.db     one table per artifact, plus LAVA's own
                         _lava_media_items / _lava_media_references and the
                         artifact-pattern tables
  _lava_data.lava        the project index: parser info, per-category
                         artifact descriptors, column labels, typed columns
  media/<sha1>.<ext>     the recovered files, hard-linked
```

**Open the project file, not the folder:** `lava/_lava_data.lava`. That is
what iLEAPP hands to LAVA, and what the `.lava` file association points at.
`python3 lava_launch.py lava/` finds a local LAVA and opens it; if none is
installed it says so and points at leapps.org rather than failing silently.
The GUI shows an **Open in LAVA** button when LAVA is detected.

Recovered media becomes one artifact per recovery engine — file system,
carved, database blobs, packages, folder scan, Project VIC import — so the
provenance stays visible, since a file listed in a directory entry is a
different kind of evidence from one carved out of unallocated space. Each row
carries the MAC times as `datetime` columns, a `media` column holding the
media item id (LAVA renders the preview from it), the size, deleted flag,
source path, byte offset, signature, and MD5/SHA-1/SHA-256. A **Media
Extraction Summary** artifact records the case number, examiner, evidence
item and the run's counts.

Structure was derived from a real iLEAPP 2.5.0 output set and checked against
it: identical index key sets at every level, identical `_lava_media_items`,
`_lava_media_references` and `_artifact_search_patterns` column shapes, epoch
-second timestamps, SHA-1 ids, and `object_columns` types drawn only from the
set that output uses. Validate any set before opening it:

```bash
python3 lava_export.py --validate out/lava
```

The validator reads it the way LAVA does: every `tablename` in the index must
exist, every `column_map` and `object_columns` name must be a real column,
`record_count` must match the table, every media item must resolve to a file
that is actually present, and — the one that matters most — **every media
cell must resolve through the whole chain**: cell → `_lava_media_references`
→ `_lava_media_items` → file on disk.

That last check exists because of a real bug. A media cell holds the
*reference* id, not the media item id. Storing the item id produced an
artifact that looked correct in every other respect — the column was there,
the classification was there — and displayed no pictures at all. The
validator now names that specific mistake if it recurs.

**Not tested against LAVA itself** — this environment has no install. The
structure matches the reference set field for field, but the first real open
is yours. If LAVA rejects it, its message plus the output of `--validate`
will pin down the difference quickly.

### Reloading the page does not stop a run

The extraction runs in a thread inside the local server, not in the browser,
so a reload never killed it — but the page lost the job id and looked as
though it had. Now:

* the page asks the server what it is working on when it loads, and rejoins a
  run in progress, progress bar and all;
* a reload after a run has finished restores that run's results, gallery and
  export panels;
* closing or reloading while work is in progress prompts first;
* a failed status poll retries several times before reporting an error, since
  the usual cause is the page being reloaded rather than a failed job.

Verified against a 316 MB image: a run interrupted by a simulated refresh
mid-carve was rejoined at the same byte offset and ran to completion.

### Knowing when it has actually finished

The page polls job status about twice a second and pulls a thumbnail for
every recovered file, and the web server logs a line for each. That scroll of
GET lines after a run made it look like work was still going on when it was
not. Successful requests to those endpoints are now filtered out of the
console; errors and anything that changes state still print.

When a run really ends, the console prints a banner naming the counts, the
elapsed time and every file written:

```
==================================================================
  FINISHED
==================================================================
  Source        : /cases/evidence.E01
  Files recovered: 43102
         41880  from the file system
          1222  carved
  Duplicates    : 9034 collapsed
  Elapsed       : 4211.6s
  Output        : /cases/out
  Project VIC   : /cases/out/ProjectVIC.json
  LAVA          : /cases/out/lava/_lava_data.lava
==================================================================
  Nothing further is running. Log lines after this point are the
  browser loading previews.
```

The page shows the same thing as a blue **Finished** banner under the
progress bar, alongside the green **In use** banner for the image.

### Where results are written

Results do not have to land next to the program. A 128 GB image can produce
more output than the system drive has spare, and a run that fills the disk
hours in is worse than one that never started.

```bash
# the command-line tool has always taken an output path
python3 media_core.py -i evidence.E01 -o /mnt/case-drive/DC26-253

# the GUI now takes one too, at startup
python3 media_gui.py -o /mnt/case-drive/DC26-253
```

Both GUIs also have a **Results folder** box with a Browse button, changeable
at any time before a run. It shows the free space on that volume and, once an
image is accepted, warns when free space is under a quarter of the image
size — recovered files can approach the size of the source.

Each run gets its own subfolder inside whatever you choose, so several runs
can share one case drive without colliding. The default is `media_out/`
beside the program, and it is only created when something is actually written
there.

The Browse dialog lists every drive as a shortcut, and going up from a drive
root returns to that list. Without both, the picker could only ever reach the
drive the program was started from: on Windows the parent of `G:\` is `G:\`,
so the Up button had nothing to offer and the other drives were unreachable.

### Give a case number once

Enter a **Case number** in the options before starting a run — or pass
`--vic CASE` and `--lava CASE` on the command line — and the run writes its
own reports:

```bash
python3 media_core.py -i evidence.E01 -o ./out --vic DC26-253 --lava DC26-253 \
    --vic-evidence "1TB WD" --lava-examiner "J. Smith"
```

`ProjectVIC.json` and `lava/` appear alongside the recovered files, no second
step. Without a case number they are skipped, and `WHICH_FILE_IS_WHICH.txt`
says how to produce them after the fact. The GUI carries whatever you typed
up front into the export panel, so nothing is entered twice.

### Which file goes to Griffeye

`manifest.json` is the run's own record — one row per recovered file, with
hashes and provenance, for your use and for review. It is **not** an
interchange format. Loading it into Griffeye gives:

> a json file could not be read. File: manifest.json was corrupt.
> Error: Could not determine format of 0 data

That message means "this is not a Project VIC file", which is correct: it
never claimed to be. The file Griffeye wants is **ProjectVIC.json**.

Three ways to get one:

```bash
# during the run
python3 media_core.py -i evidence.E01 -o ./out --vic DC23-282

# afterwards, from the manifest
python3 projectvic_export.py -m out/manifest.json -o out/ProjectVIC.json -c DC23-282
```

or the **Export for Griffeye / C4All** panel in either GUI, which writes it
alongside the C4All XML reports.

Every output folder now contains `WHICH_FILE_IS_WHICH.txt` saying which file
is which, and whether a Project VIC file was written. To check any file
before importing it:

```bash
python3 projectvic_export.py --validate ProjectVIC.json
```

The validator identifies what a file actually is — a VICS export, a VICS hash
set, or this tool's manifest — and lists what would stop it loading: a
missing `@odata.context`, an empty `Media` array, records with no `MD5`, or
records pointing at files that are not on disk beside the JSON. Confirmed
against the two Cellebrite exports supplied: both identify correctly, and the
hash set is recognized as a hash set rather than reported as broken.

### Project VIC (VICS 2.0) JSON

Tick **Also write ProjectVIC.json** in the export panel, pass `--vic` to
`c4all_export.py`, or run the exporter directly:

```bash
python3 projectvic_export.py -m out/manifest.json -o case.json \
    -c DC23-282 --org "Anytown PD" --contact "J. Smith" --evidence "1TB WD"
```

The schema was taken from a real Project VIC US 2.0 file and matches it field
for field, in the same order: the `@odata.context` string, the case block
through to `SourceApplicationVersion`, and each `Media` record from `MD5`
through `AlternativeHashes` and `MediaFiles`. Conventions match too —
lowercase hex hashes, `MimeType` of `Image` or `Video`, ISO 8601 timestamps
with an offset and `0001-01-01T00:00:00+00:00` for unknown, Windows-style
backslashes in path fields, two-space indentation, CRLF, ASCII-escaped.

**One Media per file.** A file found in several places -- a camera folder, a
copy, unallocated space -- is one `Media` record with one `MediaFiles` entry
per place, each with its own path, times and deleted flag.
`TotalMediaFiles` counts the `Media` records, so it agrees with the case's
own report, and `TotalPrecategorized` counts the records marked
`IsPrecategorized`. (Before v2026.11.283 each place was a `Media` record of
its own, and `TotalPrecategorized` was always 0.) `--validate` warns when a
VICS file from anywhere repeats an MD5 across `Media` records or carries
totals that disagree with its records.

`AlternativeHashes` is present but empty: PhotoDNA hashing is licensed and
nothing here computes it. Cellebrite writes one entry per record with an
empty `HashValue`, which carries no information either way.

Two conventions differ between this exporter and Cellebrite's, both
switchable:

- **`MediaID`** is written sequentially (1, 2, 3…), which is unique per
  record and what the data model describes. Cellebrite writes `0` on every
  record. If a tool ever objects, `--vic-media-id zero` matches Cellebrite
  exactly.
- **File layout.** By default `RelativeFilePath` points at the recovered
  files where they already are — the DVD batch copies when exported with the
  XML reports. `--vic-files-layout hash` instead stages them beside the JSON
  in a `<name>Files` folder, each named by its MD5 with no extension, which
  is what Cellebrite produces. The GUI exposes this as **Stage VIC files by
  MD5**. Staged files are hard-linked, so this costs no extra disk.

**`Category` is `null` and `IsPrecategorized` is `false` on every record.**
This tool recovers and hashes files; it does not classify them. Grading stays
with the examiner in Griffeye, which is the only place that decision belongs.

When written as part of a C4All export, `RelativeFilePath` is rewritten to
point at the batched copies (`DVD01(CASE)\01\00000001.JPG`), so the XML
reports and the JSON describe the same files on disc.

### What was checked, and the one guess

The `<Movie>` layout was derived field for field from a real C4All
`C4M_Index.xml` and verified against it: identical field order, identical
`ReportIndex version="2.0"` root, CDATA on exactly `path` and `fullpath`,
UTF-16 LE with BOM, CRLF throughout, and the same empty-tag and
epoch-timestamp conventions. Every index entry was confirmed to point at a
file that exists with a matching hash.

The `<Picture>` layout is still **inferred by analogy**: no reference
`C4P_Index.xml` was available to check it against, and the accompanying
`Case_Report.xml` and `Victim_Report.xml` are identical between picture and
movie runs, so they say nothing about record structure. If Griffeye rejects
`C4P_Index.xml`, run with `--all-as-movies` (or tick the equivalent box in
the GUI) to put everything in the verified `<Movie>` format. The first few
hundred KB of a real `C4P_Index.xml` would settle it.

Both exporters were run at 50,000 records: about 11 seconds, producing a
40 MB picture index that reparses cleanly and a 51 MB VICS file that reparses
to the right record count.

### False positives: what changed in 2.4.1

A 128 GB E01 produced 2.34 TB of `.mov` output. Two separate defects, both
fixed, both now guarded:

**The ISO-BMFF parser was far too permissive.** It accepted any four
printable bytes as a box type. Roughly 2% of random data passes that test, so
a stray `moov` on disk plus one plausible-looking header 2 GB later produced a
1.88 GB "video". On a 128 GB image with hundreds of `moov` hits, that is
dozens of multi-gigabyte false positives.

The parser now requires: a known top-level box type from a fixed list; a
first box of `ftyp`, `styp`, `jP` or `moov`; a `moov` first box to actually
contain `mvhd`; at least one box that carries content rather than headers
alone; and, for anything over 64 MB, at least three boxes including an
`mdat`. "Extends to end of file" is only honored as the final box of
something that has already proved itself.

**Parallel workers wrote before the parent could reject.** Each worker
extracted what it found in its stripe and the parent then deleted the
overlaps. But a stripe cannot see a file that began in the previous stripe,
so every stripe wrote its own overlapping copies — peak disk use ran to many
times the image size before any of it was cleaned up. Workers now detect
only, returning offsets and lengths; the parent merges, and extracts once,
for the candidates that survive.

**A guard, in both engines.** Non-overlapping extents cannot exceed the image
they came from. If the running total ever does, the carve stops with an error
naming both figures rather than filling the disk. If you ever see that
message, it is a bug — please report it with the image size and the manifest.

Real video is unaffected: MP4, MOV, AVI, FLV, ASF, MPEG-TS and Ogg all still
carve byte-exact across the test fixtures, and serial and parallel runs
produce identical output.

### False positives from the file system engine (2.4.2)

A 6.8 GB `.mov` containing nothing but `0x00` came from a different defect
than the carving one above — note the size: it is larger than the carver's
4 GiB cap, so carving could not have produced it.

The cause was `sniff_category` classifying **by file name first**. A deleted
directory entry named `SOMETHING.MOV`, whose recorded size was corrupt and
whose clusters read as zeros, was extracted wholesale on the strength of
those three letters. Three changes:

1. **Content decides, not the name.** The header is checked against the
   signature list first. The extension is only consulted when no signature
   matched, and then only if the header carries information at all — a header
   that is one byte value repeated (a sparse extent, a wiped region) is
   rejected outright.
2. **A file of one repeated byte is never written.** Tracked while hashing,
   so it costs nothing, and counted as `blank_dropped` in the summary.
3. **A file cannot be larger than the image it came from.** Corrupt metadata
   routinely claims sizes exceeding the volume, deleted entries especially;
   those are now counted as `skipped_large`.

Files still classified by name are marked in the manifest as
`fs-entry (extension only)` with a note saying the header did not match, so a
truncated or renamed file is still recovered — but you can see which is which.

`test_false_positives.py` covers all of it: the 6.8 GB zero-filled `.MOV`, an
entry claiming 20 GB on an 8 GB image, a real MP4 renamed to `.dat` (still
recovered, by magic), and a normal `.MP4` (still recovered). The bad entries
are rejected at the header check, before a single byte is streamed.

### File systems: what is actually supported

The primary file system engine is The Sleuth Kit through `pytsk3`. Where
TSK is absent, refuses a volume, or is switched off (`FME_NO_TSK=1`), the
program's own readers take over: `fat32_core` (FAT12/16/32), `ntfs_mft` (the
NTFS MFT, for deleted files), and the vendored readers for FAT, exFAT, NTFS,
HFS+ and APFS (see the manual's "Code from other people"). `tools/verify.py`
says which of these this machine actually reads with. In a current TSK the
list is:

**Walked file by file, keeping names, paths and dates:** ext2/3/4, FAT12/16/32,
exFAT, NTFS, HFS+/HFSX, APFS (TSK 4.7+ with pool support), UFS/FFS, ISO 9660,
YAFFS2.

**Not supported by TSK, so no file listing:** F2FS, QNX4, QNX EFS, QNX ETFS,
QNX IFS, SquashFS, JFFS2, UBI, UBIFS, YAFFS1. Reading those needs parsers
written for each one, which some tools carry and this does not.

That is not the same as recovering nothing from them. **Carving is file
system independent** — it reads the volume as bytes — so media on an F2FS
phone dump or a SquashFS firmware image is still recovered, with hashes,
offsets and EXIF. What is lost is the file name, the path and the file
system timestamps. If the volume can be mounted, pointing the tool at the
mount adds names back for live files, at the cost of everything deleted.

`python3 media_core.py -i image.dd --inspect` says which file systems TSK
found before a run starts.

### Reviewing: categories, flags and notes

The decisions are the work, so they live in `case.db` beside the recovered
files and survive the program closing. Every change commits immediately and
is recorded in an audit log with who made it and when.

Decisions are keyed by **SHA-256, not by path**. Re-run the extraction into a
fresh folder and the categories come with it; a file found in three places is
categorized once; moving the output loses nothing.

| key | code | category |
| --- | --- | --- |
| `0` | 0 | Non-Pertinent |
| `1` | 1 | Child Abuse Material (CAM) |
| `2` | 2 | Child Exploitive (non-CAM) / Age Difficult |
| `3` | 3 | CGI Animation - Child Exploitive |
| `4` | 4 | Comparison Images |
| `5` | 5 | Adult Pornography |
| `6` | 6 | Abnormal Adult Porn |
| `7` | 7 | Notable |
| `8` | 8 | **Uncategorized** — what everything starts as |
| `9` | 9 | Suspect Pictures |

Press the key to apply the category to the selected file; the cursor advances
to the next one. The scheme is locked — these codes mean the same thing in
every case — and your own categories are added above it, from 10.

**Whether something was AI-generated is a flag, not a category.** A picture
is CAM or it is not; separately, it was generated or it was not. As a flag it
combines with any grading, and more than one can apply:

| key | flag |
| --- | --- |
| `-` | AI CSAM |
| `.` | AI Age Difficult |
| `*` | AI Adult |
| | S&M Violence, Infant/Toddler, Bestiality |

A flag key toggles and stays on the file; a category key grades it and moves
on. Both are shown on the button, so neither has to be remembered.

Flags are seeded once, when the case is created — not on every open — so one
you delete stays deleted. They are not locked the way the categories are:
delete the ones a case does not need, add the ones it does.

**Uncategorized is 8, not 0.** Nothing assumes otherwise: 0 is Non-Pertinent,
which is a finding somebody made, and a file nobody has looked at must not be
confused with one judged irrelevant. The backlog filter counts files with no
decision *and* files explicitly set to 8.

| | |
| --- | --- |
| Flags | **S&M Violence, Infant/Toddler, Bestiality, AI CSAM, AI Age Difficult, AI Adult** in every new case, plus whatever you add. Any number per file, independent of category, and shown on the tile |
| Notes | Autosave a second after you stop typing |
| Snapshots | On demand, into `backups/`, standalone copies to roll back to |
| Audit | Every categorize, flag, note and deletion, with a timestamp |

The **Project VIC export carries these categories back out** — `Category`,
`IsPrecategorized` and `PrecategorizationSource` — where a review has
happened, mapping the local scheme onto the codes VICS defines:

| local | VICS |
| --- | --- |
| 0 Non-Pertinent, 1 CAM, 2 Child Exploitive, 3 CGI, 4 Comparison | the same code |
| 5-12 and your own categories | **no category written** |

Project VIC defines no equivalent for Adult Pornography, Notable, Suspect or
the AI categories. Writing a number VICS does not define, or the nearest one
that looks similar, would misstate a finding in a file other agencies read,
so those export with a null category and the export says how many. Their
grading stays in `case.db` and in this tool's own reports.

### The report

**Export report**, top right, or **Menu → Export report**. One export writes
every format asked for over one scope, so a CSV never disagrees with the HTML
beside it.

| | |
| --- | --- |
| Header | agency, case number, evidence item, examiner, a logo, free-text notes, the source image, when it was generated and in which time zone |
| Scope | everything, what the filters show, the files selected, or categorized only |
| Formats | HTML, CSV, JSON, MD5 list, KML of geolocated media, Project VIC |
| Layout | choose the columns, and optionally a section per flag |
| Summary | counts overall, by category and by flag, on the front page |

Three things the report says about itself, because a reader cannot otherwise
tell:

- **Scope is on the front page.** A report covering one category states how
  many of the run it covers and that it does not cover the rest.
- **Thumbnails can be left out entirely**, and the header then says the
  report holds no thumbnail, full-size copy or video frame of any file. That
  is the report for someone who must not be shown the material — counsel, a
  filing, an expert under terms — and it has to be distinguishable from a
  report whose images merely failed to load.
- **Blurring is a reviewing aid, not a safeguard.** It makes a contact sheet
  readable over a shoulder; the underlying image is still in the file, and
  the header says so.

```bash
python -m fme report -o out -d ./reports --case DC26-253 \
    --agency "Delaware County DA" --format html --format csv --no-thumbnails
```

### Reports to hand over

```bash
python3 report_html.py -m out/manifest.json -o out/report.html --md5 out/md5_list.csv
```

A contact sheet in one self-contained HTML file: embedded thumbnails, the
metadata, tags for deleted, carved, GPS and hash-set hits. Plus a
distinct-hash CSV, which is the format a hash list is usually wanted in.
Thumbnails need Pillow; without it the pictures are linked rather than
embedded and the report says so.

### The layout

Three columns, each scrolling independently: settings on the left, results in
the middle, details on the right. Each has its own scrollbar, so moving
through one leaves the others where they are.

In the results column, the toolbar, the category bar and the active-filter
bar stay put while **only the tiles scroll**. The filters and the counts are
needed at every point in a page of results, and the scrollbar sits beside the
tiles rather than at the edge of the window, which is where a scrollbar for a
gallery belongs.

Below 1400px the details pane moves under the results rather than squeezing
them, and below 900px everything stacks.

### Viewing: tile size, paging and time zone

Three controls in the results toolbar, each remembered per browser so a long
review does not restart from the defaults every morning.

**Tile size** is a slider. The gallery takes its column width from a CSS
variable, so dragging it reflows the tiles without re-fetching a single
thumbnail — useful when a page of icons needs a glance and a page of
photographs needs a look.

**Per page** limits how many tiles are built at once: 200, 500, 1500, 5000 or
all, with first/previous/next/last underneath. Building fifty thousand tiles
in one pass is what makes a gallery unusable, whatever machine it runs on.

**Time zone** converts every timestamp that carries one. The list covers the
common zones plus whatever this machine is set to, and the conversion is done
by the browser, which knows when daylight saving applied on any particular
date — a January timestamp shows EST, a July one EDT, from the same setting.

One distinction the interface keeps rather than hiding:

| | |
| --- | --- |
| File system times | recorded in UTC, so they convert exactly |
| EXIF capture time | wall-clock as the camera recorded it, with no zone at all |
| FAT times | local to whatever machine wrote them; the format stores no zone |

The last two cannot be converted without inventing information, so they are
shown as recorded and labeled **(as recorded)**. Shifting them by the
selected zone would produce a confident answer that is wrong by however many
hours the camera's clock differed.

### The case list

The program opens on **Cases**: every case processed here, by case number,
evidence item and examiner, with how many files were recovered and how many
have been graded. Click one and it opens — **nothing is carved or hashed
again**. The manifest already records what was found and `case.db` already
holds what was decided; opening is reading two files.

New runs register themselves as they start, so a run that is cancelled still
appears with whatever it produced. The counts are re-read from the case's own
files rather than remembered, so a number in the list never drifts from the
evidence.

**The list holds no evidence and no decisions.** It records where output
folders are, not copies of them, which has two consequences worth knowing:

- A folder that has been moved or deleted is shown as **missing** rather than
  offered to open.
- Losing the list costs the list and nothing else. **Scan** a drive and it
  finds the cases again by their manifests, reading the case number and
  examiner back out of each one.

```bash
python -m fme cases --list
python -m fme cases --scan D:\cases
```

The list lives in `cases.db` beside the program. Put it on a share to see an
office's cases rather than one machine's.

### Three panes

The right-hand column shows one of three things, chosen by the chips in the
toolbar: **Details**, **Classification**, **Filters**. Clicking the chip of
the pane already open closes the column.

**Classification** is the state of the review and a way into it. Every
category with how many files are in it, and every tag with how many —
counted *within* whatever category is showing, so with category 1 selected
the tag counts answer "how many of these are also AI". Click a category to
see only it; click a tag to narrow further; click either again to undo.

**Filters** builds facets from each file's own metadata and from the file
system:

| facet | values |
| --- | --- |
| File type | Still image, Animated image, Video, Audio, Other |
| File extension | every extension in the run |
| File size | under 100 KB, 100 KB–1 MB, 1–10 MB, 10–100 MB, 100 MB–1 GB, over 1 GB |
| Deleted | Deleted, Not deleted |
| Metadata | Has metadata, Has GPS, No metadata |
| Date taken (EXIF) | by year, newest first |
| File created, last written, last accessed | by year |
| Camera author | EXIF `Artist`, the camera owner tag, or XMP `dc:creator` |
| Camera make, Camera model | EXIF |
| Camera serial number | EXIF `BodySerialNumber`, or XMP `aux:SerialNumber` |
| City, State, Country | IPTC or XMP — **a camera does not write these** |

**Still or animated** is worked out by reading the file, not the extension:
GIF by counting image descriptors, APNG by its `acTL` chunk, WebP by its
`ANIM` chunk. Most GIFs on a disk are static, and an animation is a
different thing to look at and to grade.

A file can sit in two values of one facet — a photograph with coordinates is
both **Has metadata** and **Has GPS** — and selecting either finds it.

Several values inside one facet mean any of them; different facets all have
to hold, so "this camera body, in this city" is a question you can ask. Each
value carries the count it would give, computed against the other filters
rather than against the whole run. Files with nothing recorded for a facet
are grouped as **(not recorded)** and can be filtered on too.

On the place names: no geocoding is done and none is guessed. A city appears
only because a person or a cataloguing tool wrote it into the file, which is
what makes it worth having. A photograph with GPS but no IPTC has
coordinates and no city — use **Has GPS** and the map link for those.

**What is filtering** sits at the bottom of the results, listing every active
filter with an ✕ to drop one and a button to clear them all. It counts what
is shown against the total, so a narrow view never looks like an empty run.

### The details pane

Click a thumbnail or a file name and everything known about that file appears
in the right-hand column, with the file itself highlighted in the gallery: source image, type, original name, the path it came from,
where the copy is stored, size, the three MAC timestamps, which engine
recovered it and on what signature, byte offset, MD5/SHA-1/SHA-256, and the
notes for the row.

Two rows are worth calling out:

- **Exact copies** — every other place those same bytes were found, taken
  from the duplicate records. "none" means the file appeared once.
- **Hash sets** — which loaded sets the file appears in, and what each set
  recorded about it.

**Refresh** re-reads the current run and redraws without restarting anything:
useful after a hash-set scan, or to pick up a run that finished while the
page was on another tab. It never re-carves.

### The manual

`MANUAL.md` covers everything the program does, from loading an image to
writing a report: what it reads, what each option changes, the review
workflow and its keys, hash sets, cases, reports and exports, the command
line, what to install, troubleshooting, and what it deliberately does not do.

**Menu → Manual** opens it rendered in a tab, with a contents list. It is
also a plain text file, readable in any editor and printable from the
browser.

`tests/test_manual.py` checks the manual against the code — the category
codes and keys it lists, the flags it names, the command-line tools it shows
and the files it says a run produces. A manual that drifts is worse than
none, because it is confidently wrong.

### The menu

**☰ Menu**, top left: open a case, start a new extraction, the hash sets and
store, custom signatures, export a report, the results folder, open the
output folder on the desktop, snapshot the case, read the audit log, and what
is installed.

### The right-click menu

Everything that can be done to a file, where the file is. A review is
thousands of small decisions, and crossing the screen for each one is the
difference between an hour and an afternoon.

| | |
| --- | --- |
| Find similar images | shows only that file and what looks like it |
| Show details | opens it in the details pane |
| View full size | the picture or video, full window |
| Hex view | the bytes, a page at a time, with ASCII alongside |
| the categories | each with its key and a tick on the current one |
| Clear category | back to uncategorized |
| the flags | each with its key and a tick where it applies |
| Export MD5 | a hash list of this file, or of everything selected |
| Open original file / Show in folder | hands it to the desktop |

Right-clicking a file that is part of a selection acts on the **whole
selection**, and the menu says so at the top. Right-clicking outside one
selects that file first, so the menu never acts on something out of view.

### Grading many files at once

Most of a review is "these three hundred are all the same thing", so
selecting many and grading them once is the normal case rather than a special
one.

| | |
| --- | --- |
| click | select one and show it in the details pane |
| **ctrl**-click (**⌘** on a Mac) | add or remove one |
| **shift**-click | select a range — the run of similar files between the two |
| **ctrl**-**A** | select everything the filter and search match |
| **Esc** | clear the selection |
| **Select all matching** | the same as ctrl-A, as a button |
| **Select this page** | only what is shown now |

With several selected, a category key or button grades all of them, and a
flag applies to all of them. Over fifty files it asks first, naming the
category and the count, because this is the action that is expensive to get
wrong.

Selected tiles carry a blue outline and a tick — deliberately louder than the
highlight for "currently open in the details pane", since those are different
things and acting on the wrong three hundred files is the mistake worth
designing against. The details pane says in blue what an action will cover.

Every copy of a file is covered: decisions are keyed by hash, so grading one
grades the duplicates found elsewhere on the exhibit.

### The category bar

A row of chips above the results, one per category, each colored as the
category is and carrying its key and its count. Clicking one shows only the
files in it; clicking it again goes back to everything, so the bar is its own
way out. **All** is there as well.

The counts are the state of the review at a glance — how much is graded, how
much is left, and where it went. Uncategorized counts both files explicitly
set to it and files nobody has touched, which are the same thing from the
backlog's point of view.

Categories with nothing in them are dimmed rather than hidden, so the bar
does not reflow as a review progresses and a key is always in the same place.

### Searching

A search box sits with the filters. It matches the file name, the path the
file came from, all three hashes, the camera, and your note on it, and
narrows as you type. Hashes match on any prefix, so the first eight
characters out of a report find the file.

Several terms all have to match, and a prefix restricts one to a field:

```
img_0042              anywhere
91a3b5                a hash prefix
md5:91a3b5            that hash only
path:100apple         the source path only
cam:iphone note:dc24  both, each in its own field
```

`name:`, `path:`, `md5:`, `sha1:`, `sha256:`, `cam:` and `note:` are the
fields. Escape clears the box.

### The hash store, kept between cases

Sets loaded for one run were always the wrong model: the NSRL is tens of
millions of hashes, and an office's own record of what it has seen before is
the most valuable set it owns. Both belong somewhere permanent.

**Hash Sets tab → Hash store.** Import a set once and every case from then on
is checked against it. Point the store at a share to use one across an
office. On the command line:

```bash
python -m fme store --store D:\hashes\store.db --import NSRLFile.txt
python -m fme store --store D:\hashes\store.db --list
python -m fme store --store D:\hashes\store.db --scan out/manifest.json
```

Project VIC JSON, NSRL (`NSRLFile.txt` or an RDS SQLite database) and your own
lists all import, detected by looking inside the file. Sets are streamed in
batches rather than loaded into memory, so size is a matter of time and disk
rather than whether it fits.

**When an import finds nothing**, the import says what it saw rather than
reporting a bare zero: how many rows it read, how many it skipped and why,
and whether a blind scan of the file found hashes the structured reader
missed. If it did, they are imported anyway and the note says so — a set that
cannot be read is worse than one read crudely.

To ask the store file itself what it holds, rather than the running program:

```bash
python -m fme store --store D:\hashes\store.db --verify
```

or **Verify** in the tab. It opens the file read-only and reports the total,
the split by algorithm, and the count per source — which is what a colleague
opening the same file would see.

The store is checkpointed after every import, so the `.db` is self-contained
and can be copied to a share on its own. Without that, recent writes sit in a
`-wal` file beside it, and a `.db` copied without its `-wal` is missing
whatever had not been folded in — which looks exactly like an import that did
nothing.

To look before importing:

```bash
python -m fme store --inspect VIC_US_2026.json
```

or **What is this file?** next to the import box. It reports the size, what
the detector makes of it, what the structured reader gets, what a scan finds,
and for a Project VIC file that reads as empty, whether there is a `Media`
array where one is expected and what keys are there instead.

**A match can categorize a file nobody has looked at**, on four rules: only
an uncategorized file; a set that asserts a category beats one that does not,
most severe first; an NSRL hit means Non-Pertinent only when nothing notable
flags the file too; and the decision is recorded as the **set's**, not the
examiner's — striped on the tile, named in the details pane, filterable under
**Set by a hash set**, and written as that set in the Project VIC export's
`PrecategorizationSource`. **Not yet reviewed** shows everything a person has
still not looked at, including what a set categorized.

**Contributing a case back** is the other half:

```bash
python -m fme store --store D:\hashes\store.db \
    --contribute out --source DC26-253
```

or the button in the tab. Only files an examiner **graded** are contributed,
and the grading travels with the hash — so a later case matching one is told
*"Child Abuse Material (CAM), from DC26-253"* rather than a bare "seen
before". Uncategorized files are not contributed: a hash nobody has looked at
teaches the next case nothing.

A file whose MD5, SHA-1 and SHA-256 are all in one source is **one** match,
reported with which algorithms agreed — three agreeing is a stronger
statement than one. A digest in two sources is two matches, both reported,
because "in the NSRL and in a Project VIC set" is worth seeing.

**A Project VIC import keeps the whole record**, not just the hash and the
category:

| | |
| --- | --- |
| kept | MediaID, Category, MD5, SHA1, SHA256, MediaSize, DateUpdated, Tags, Series, IsPrecategorized, PrecategorizationSource, VictimIdentified, OffenderIdentified, IsDistributed, RelativeFilePath, Description |
| where it shows | the details pane under the matching set, the report's **Hash sets** column, and the CSV and JSON exports |
| hashes read | MD5, SHA-1, SHA-256 and anything hash-shaped in `AlternativeHashes`, so any of them finds the record |

A match that says only "this hash is in the set" cannot be acted on. One that
says *MediaID 900500, Series Blue, victim identified* can.

If you imported a set before this version, **re-import it**: the hashes are
there, the records are not. Remove the source first, or import under another
name.

### Hash sets for a single run

On the **Hash Sets** tab, next to Extract Media and Custom Signatures. Sets
can be loaded before a run as well as after one, and stay loaded for the life
of the program. Sets stay loaded for the life of the program, because a national set is
hundreds of megabytes and reloading it per job would make the feature
unusable.

```bash
python3 hashsets.py --scan out/manifest.json \
    --vic VIC_US_2026.json --nsrl NSRLFile.txt --custom our_known.txt \
    --csv matches.csv
```

| kind | formats accepted | what a match means |
| --- | --- | --- |
| Project VIC | VICS JSON | the category the set recorded, with the rest of its record -- MediaID, Series, Tags, the identified and distributed flags -- in the details pane and the match CSV |
| NSRL | `NSRLFile.txt`, an RDS SQLite database, or a plain export | known software or OS file — usually a reason to set a file aside |
| Custom | one hash per line, or a CSV with a hash column | whatever your list says; anything after the hash on a line is kept as its note |

Matching is on MD5, SHA-1 and SHA-256, so it does not matter which algorithm
a set was built with, and a file is checked against every loaded set rather
than stopping at the first hit — being in both a known-software set and a
categorized set is itself worth seeing.

Results are written to `hash_matches.csv` in the run folder and shown against
each file in the details pane. Files matching nothing are reported as
**unknown**, which is not the same as cleared.

Both match CSVs (`hash_matches.csv` for these sets, `hash_store_matches.csv`
for the shared store) end with `vic_media_id`, `vic_category`, `vic_series`,
`vic_tags`, `vic_victim_identified`, `vic_offender_identified` and
`vic_is_distributed`, each holding the values from every matching set joined
with `; `. The first nine columns are unchanged.

Checking against these sets and checking against the shared store each
replace their own earlier matches in the case and keep the other's. (Before
v2026.11.280, checking against a set loaded here erased the store's matches
from the case.)

### What each file says about itself

A recovered photograph carries more than its bytes. The camera that took it,
when it was taken, and where, sit *inside* the file, so they survive carving
even when the file system that held them is gone. Those are read from every
recovered image and video, with no dependency at all:

| format | what is read |
| --- | --- |
| JPEG, TIFF and the raw formats built on TIFF | EXIF: capture time, camera make and model, GPS, pixel dimensions |
| HEIC, AVIF | the EXIF item inside the container |
| MP4, MOV, 3GP | creation time from the movie header, and Apple's and Android's location atoms |

The fields appear in the manifest, in the details pane, in the LAVA export as
their own columns, and as a **Has GPS** filter in the gallery. `--no-metadata`
turns the pass off.

Malformed input is expected rather than exceptional — a recovery run is full
of truncated carves — so anything that cannot be parsed returns nothing at
all instead of raising. `test_metadata.py` checks that against random bytes,
truncated files, empty files and runs of zeros.

### Geolocated media on a map

Any run where something carries coordinates writes `locations.kml` beside the
other reports. For a self-contained file with the pictures inside it:

```bash
python3 kml_export.py -m out/manifest.json -o out/locations.kmz --thumbs
```

Each placemark carries the capture time (so Google Earth can play them in
order), the camera, the source path, the hashes, and whether the file was
deleted. Deleted files get a different marker color.

Nothing is geocoded or looked up: a coordinate here came out of the file
itself. The map link in the details pane is the one thing that leaves the
machine, and it is a link you choose to click.

### Visually similar images (optional: needs Pillow)

A hash answers "are these the same bytes". It cannot answer "are these the
same picture", because re-saving a JPEG, resizing it or screenshotting it
changes every byte while leaving the image recognizable. That needs the
pixels, so this is the one feature with a dependency outside the standard
library:

```bash
pip install pillow
python3 similarity.py -m out/manifest.json --distance 8 --csv similar.csv
```

or the **Find similar images** button in the hash sets panel. Without Pillow,
the controls are replaced by a line saying what to install; nothing else is
affected.

The method is a difference hash: decode, grayscale, shrink to 9x8, and record
for each pixel whether it is brighter than the one to its right. That is 64
bits describing structure rather than bytes. Two images are similar when few
of those bits differ, and images are clustered transitively, so a group means
"these pictures belong together" rather than "these two happen to be close".

Measured on a photo and four variants of it, at the default 8 bits:

| variant | distance | grouped |
| --- | --- | --- |
| grayscale copy | 0 | yes |
| re-saved at lower quality | 4 | yes |
| cropped | 7 | yes |
| resized to half | 15 | no |
| unrelated picture | 19 | no |

So the default is conservative: it will not group things that merely look
vaguely alike, and it will miss some real relatives. Raise the distance to
12 or 16 to catch more, and expect stray matches in return. The distance is
shown against every match in the details pane, so the judgment stays with
you rather than being hidden behind a yes or no.

Exact duplicates never appear here — identical bytes were collapsed during
extraction and are listed under **Exact copies** instead. A similar group of
two means two genuinely different files that look alike.

Cost: every image is decoded, so allow roughly ten to twenty minutes on a run
of 160,000 pictures. Files that cannot be decoded — truncated carves,
unsupported formats, things that are not really images — are counted and
reported rather than passed over silently.

### Scrubbing video without opening it

Hovering a video tile seeks it: moving across the tile moves through the
clip, so a video can be judged from frames without being played. The details
pane does the same, with the controls still there to play it properly.

Only the header is fetched up front, so a page of videos does not download
itself; the frames arrive as you scrub.

Formats the browser cannot play — most AVI, some MOV codecs, MPEG-2, much of
what comes off a DVR — are decoded instead. With `opencv-python-headless`
installed, opening one of those in the details pane produces a **filmstrip**
of evenly spaced frames, decoded on demand and cached. Without it, the pane
says what to install rather than showing a black rectangle.

```bash
pip install opencv-python-headless
python -m fme frames -o out/manifest.json
```

Frames are **generated images, not evidence**: their hashes are this tool's,
not the file's, which is why they are written to `_frames/` outside the
recovered media. The original is never touched. Spacing is by position
rather than by key frame, because the key frames of a long static shot are
all the same picture and an examiner wants a view of the whole clip.

### Opening files in another application

Every recovered file in the gallery and the table has **Open with…** and
**Show**. The first hands the file to the operating system, so video opens in
your default player and images in your default viewer — the choice of
application stays with the OS rather than being reinvented in a browser tab.
The second reveals the file in Explorer, Finder or your file manager.

This is also the way round LAVA's preview limits: a format Chromium cannot
draw still opens fine in a native application.

### When LAVA shows a broken image

Two different causes, and they look identical in the interface.

**The format cannot be drawn by a browser.** LAVA is an Electron
application, so it displays what Chromium can display. EMF, WMF, TIFF, PSD,
camera raw, JP2, and most video containers are not on that list. The record
is correct, the file is there, and the preview pane simply cannot render it —
open the file from `media/` in something native. Rows in these formats now
carry a note saying so, rather than leaving a broken icon unexplained.

**Nothing previews at all, including JPEG and PNG.** That was a real defect,
fixed in v64, and the cause is worth knowing. A media item records its
location as `media/<id>.<ext>`. The viewer joins that onto `<report>/media/`,
giving `<report>/media/media/<id>.<ext>`, which no report contains — then
falls through to `<report>/_HTML/<extraction_path>`, which is the copy it
actually serves. Its "open externally" handler resolves only against
`_HTML/` and has no fallback at all.

So every file is now written **twice**: once at `media/` and once under
`_HTML/media/`, hard-linked where the file system allows it. iLEAPP writes
both copies for the same reason. `--validate` checks for the second copy and
says plainly what a missing one means.

An older export cannot be repaired in place — re-export it.

**It is not really an image.** Check the row's source path first. A run of
rows sourced from binaries — `memtest.efi`, a driver, a DLL — with `.emf` or
`.ico` extensions is a false positive, not a recovery.

That was a real defect here, and a bad one. EMF was being keyed off the
`iType` field (`01 00 00 00`) rather than the `dSignature` field (`" EMF"`),
and `iType` is four bytes that occur constantly in compiled code: **22,847
matches in a single 7.6 MB binary**. ICO has the same problem — a four-byte
header common in binaries — and was only checking that its directory looked
self-consistent.

Both are now validated properly: EMF against its real signature field and
the header/file sizes agreeing, ICO by following its first directory entry
and requiring a PNG header or a valid DIB header at the other end. The same
binary now yields **nothing at all**, and genuine EMF and ICO files still
come back byte-exact.

If a scan produced large numbers of `.emf` or `.ico` rows sourced from system
binaries, re-run it: those were noise, and they were inflating the file count.

### Duplicates: collapsed, not discarded

Two files with the same SHA-256 are the same bytes. Rather than writing them
twice, the bytes are stored once and **every place they were found is
recorded**. A run reporting 163,338 files and 213,419 duplicates found
376,757 things in total; it wrote 163,338 files.

That distinction matters for a comparison as well as for the case: a tool
that writes every copy separately would report the larger figure for the same
evidence.

Where the provenance lives:

- **`duplicates.csv`** — one row per additional location, with the same
  columns as `manifest.csv`: engine, source path, byte offset, deleted flag,
  file system, timestamps, hashes. `out_path` points at the single stored
  copy.
- **`manifest.json`** — a `duplicates` array with the same records, and an
  `instances` count on each kept file saying how many places it was found.
- **The exports** — Project VIC, C4All XML and LAVA all list **one record per
  occurrence** by default, so a reviewer sees every location with the preview
  attached, while the media folder still holds the bytes once. Pass
  `--no-duplicates` to any of the exporters for one record per unique file.

If you need the copies as separate files on disk — for hashing against
another tool's output, or because a workflow expects them — run with
`--no-dedupe`. Be aware of the cost: a run with 213,419 duplicates would
write roughly 2.3 times as many files, and the disk usage rises accordingly.

Nothing is lost either way. The default trades disk space for completeness of
record, not for completeness of findings.

### Should I mount the image first?

For the main scan, no. Mounting hands you the operating system's view, which
is the *live files only*. Everything a forensic tool exists to find is
outside that view.

Measured on the test volume, which holds five live media files and three
deleted ones:

| | recovered |
| --- | --- |
| the image directly | 8 |
| the same volume mounted, scanned as a folder | 5 |

The three missing were a deleted directory entry read contiguously from its
first cluster, and two files with no directory entry at all, carved out of
unallocated space. No amount of scanning a mount point will produce them,
because the mount does not expose them.

**Mount when the alternative is not reading it at all.** There are real cases:

- **Encrypted volumes** — BitLocker, FileVault, encrypted APFS, VeraCrypt.
  Decrypt by mounting, then scan.
- **File systems the local TSK build cannot parse** — ReFS, or APFS on a TSK
  older than 4.7.
- **RAID or LVM sets** that have to be assembled before anything is readable.
- **Volume Shadow Copies.** A Windows image often holds older versions of
  files in its shadow copies. Exposing them adds material that is genuinely
  not reachable from the live volume.

In those cases, prefer a mounter that presents a **disk device** rather than
a drive letter — Arsenal Image Mounter does this, as does `xmount` on Linux.
Point this tool at that device (`\\.\PhysicalDriveN`, `/dev/nbd0`) instead of
at the mounted folder, and you keep everything: file system walk, deleted
entries, unallocated space, slack. A drive letter gives you the files; a
device gives you the volume.

So the order of preference is: the image itself, then a mounted *device*,
and a mounted *folder* only when neither is possible.

### Alignment: the setting that costs the most recall

`--align` decides which offsets are even considered. The default is now **1 —
every byte** — and that matters more than any signature:

| | files recovered |
| --- | --- |
| `--align 512` | 1 |
| `--align 1` | 3 |

That is a synthetic test holding six sector-aligned JPEGs, six PNGs knocked
off the boundary, and six base64 GIFs in HTML. Sector alignment found the
aligned JPEGs and nothing else. A picture inside a document, a thumbnail
inside a photo, or base64 in a web cache is almost never on a sector
boundary, so aligning the scan discards exactly the material that a suite
mounting compound files would find.

Carvers differ here: some scan block by block for speed, others examine every
byte. This examines every byte by default. 512-byte alignment remains
available and is roughly three times faster, but the recall cost is real and
it is labeled as such in both GUIs.

### Formats worth carving that a short signature list misses

- **QuickTime without an `ftyp` box.** Plenty of `.mov` files start with
  `mdat`, `wide`, `skip` or `pnot` — camera and screen-capture output
  especially — and matching only `ftyp`/`moov` skipped every one. Now
  detected, with a requirement that a `moov` appears in the box chain so
  "mdat" inside other video does not produce false hits.
- **AVCHD `.m2ts`** — the same transport stream as `.ts` but with a
  four-byte timestamp per packet, so the stride is 192 rather than 188.
- **MNG, ICNS, JXR, PCX**, and ISO-BMFF brands for **CR3** (Canon raw),
  **MJ2** and **F4V**, which were being written out as `.mp4`.
- **Nested media inside carved files.** A carved photo is now scanned for its
  own EXIF thumbnail, and a carved document for its images — the same
  treatment file-system files already got.

One format was tightened rather than added: JXR's three-byte magic matched
random data seven times in 200 MB and carved megabytes each time. With the
version byte included and a real length parser, 200 MB of random data now
yields a single false positive.

### App caches: video an app kept in pieces

**ExoPlayer** is the media library most Android apps stream through —
Instagram, Snapchat, Reddit, X, Spotify and many more. What it caches is not
one file per video: each is cut into pieces named for where they start,
scattered across ten numbered subfolders, with the name kept in a separate
index. **Carving finds the first piece and nothing else**, because every
later piece begins mid-stream.

Pointing a folder scan at a cache rejoins them. The index is read for the
name — usually the URL the video came from — from either the app's SQLite
index or the older `cached_content_index.exi`.

Pieces are joined from the start **until the first gap**, and no further. A
piece after a gap cannot be placed in a playable file without the bytes
before it, so joining across one would produce something that looks whole,
plays wrongly and hashes as nothing. Every rejoined file records how many
pieces went in and whether the result is complete or stops at a gap — a cache
usually holds only as much as the user watched, so partial is the normal
case.

A `.exo` piece is never registered as a video in its own right. The first
one opens with a real file header, so classifying it would put a truncated
clip in the gallery beside the rejoined file that supersedes it.

**Snapchat content bundles** under `com.snap.file_manager_*_SCContent_` are
named by hash with no extension. Most are plain images, which the scan finds
by signature; about a third are an `LZC` container of Zstandard frames, which
needs `pip install zstandard` (Python 3.14 and later have one built in).
Without a decompressor they are counted and reported rather than passed over.

### Thumbnail caches, PDFs and saved pages

Three places media hides that a signature scan walks straight past. All are
opened with the compound-file pass, which is on by default.

**Windows thumbnail caches** (`thumbcache_*.db`) are the most valuable of
the three. Explorer keeps a copy of every thumbnail it has drawn, and those
copies outlive the originals — a picture deleted and its clusters overwritten
is often still in the cache. Records are walked by the offsets in the file
header rather than by scanning for the `CMMM` signature, because a
thumbnail's own compressed bytes can contain those four characters and a
blind scan reads the coincidence as a new record. A truncated record ends the
walk rather than yielding part of an image.

What a cached thumbnail does **not** come with is a reliable path: the cache
records an identifier that needs the Windows Search index to resolve. So it
is the same evidentiary shape as a carved file — real content, uncertain
provenance — and is labeled that way.

**PDFs**: every image XObject. A `DCTDecode` or `JPXDecode` stream *is* the
file the producer wrote, so it comes out byte for byte and its hashes can
match a hash set. Images a PDF stores as raw pixels are **counted, not
rebuilt**: reconstructing one would produce bytes whose hashes belong to this
tool rather than to any evidence. Attachments carried in a PDF come out
verbatim.

**Saved web pages** (`.mht`, `.mhtml`): every image and video part, verbatim,
plus media held in `data:` URIs in an HTML file.

### Encoded images: what a byte-level carver cannot see

Dedicated base64 decoding is the single biggest reason one tool reports more
pictures than another. An image embedded in an HTML page, an email body, a JSON blob or a
web cache is stored as **text**, base64-encoded. No signature for JPEG or PNG
will ever match those bytes, because the bytes are not a JPEG — they are the
letters that describe one.

The carver now detects and decodes them:

| signature | what it finds |
| --- | --- |
| `jpeg_b64`, `png_b64`, `gif_b64_87/89` | base64 images in HTML, email, JSON, caches |
| `wmf_rtf`, `jpeg_rtf` | hex-encoded images inside RTF documents |
| `wmf` | Windows metafiles |
| `art`, `art2` | AOL ART, still found in long-lived collections |
| `wav` | WAV audio, which some tools count among their movie types |

Encoded hits are decoded before being written, so the output is the picture,
not a wall of text. Each is validated by decoding it and running the real
format parser over the result, so a random run of base64-looking characters
cannot produce a file: on 200 MB of random data these added no false
positives at all.

Audio is its own category, written to `audio/`, since some tools count WAV in
their movie totals and a like-for-like comparison needs it.

The cost is scanning throughput: each signature is a separate pass over the
data, so going from 35 to 44 signatures took the scan from about 75 MB/s to
about 44 MB/s per worker. Use `-w auto` to get it back.

### Comparing counts with another tool

A suite that runs after a forensic platform has mounted compound files, and
that decodes encoded streams as it searches, sees material a plain carver
never does. Matching those counts needs three things, two of which used to be
off by default here:

1. **Compound files are now opened by default** (`--no-scan-inside` turns it
   off). Documents, zips and archives are opened; gzip streams are
   decompressed; a container inside a container recurses. Previously this was
   opt-in, so a default run was being compared against a suite that had
   mounted everything first.
2. **Encoded images** — base64 and RTF-hex — were added in the previous
   version. This is the one no byte-level carver can do at all.
3. **De-duplication.** Add `duplicates_suppressed` from the run summary to
   the file count before comparing, or re-run with `--no-dedupe`. Tools that
   list every copy report the larger figure.

What a full forensic suite still gets that this does not:

- **RAR archives.** Unpacking RAR needs a licensed
  unpacker that is not in the standard library. Files inside a RAR are
  counted as `rar_skipped` rather than passed over silently.
- **Formats a forensic platform mounts as entries** — PST/OST mail stores,
  registry hives and the like. Only zip, tar, gzip, bzip2, xz, 7z and SQLite
  are opened here.
- **Every copy of a duplicate**, as above.

### Comparing results with another tool

When a suite reports far more images than this does, the difference is almost
always one of four things, and the manifest tells you which:

```bash
python3 triage_report.py -m out/manifest.json --images
python3 triage_report.py -m out/manifest.json --videos --top 40
```

It breaks the run down by engine, signature, extension and size band, lists
the largest files, and reports the counts that explain gaps.

1. **Compound files.** A suite that mounts compound files before its picture
   scan sees every image inside every document, zip, mail store and thumbnail
   cache, plus the EXIF thumbnail inside each photo. Carving cannot: a
   `.docx` stores its images deflated, so they are not contiguous bytes on
   disk. Pass `--scan-inside` (or tick **Look inside compound files** in the
   GUI) to open them properly. On a test volume holding one `.docx` with two
   pictures and one photo with an EXIF thumbnail, that took the image count
   from 1 to 4 — every one of the extra three invisible to a byte scan.
2. **De-duplication.** This tool collapses identical files by SHA-256 and
   reports how many in `duplicates_suppressed`. A tool that lists every copy
   separately reports a much larger number for the same evidence. Add the two
   figures before comparing.
3. **Thumbnails counted as pictures.** EXIF thumbnails, video poster frames
   and cache entries are separate pictures to some tools. `--scan-inside`
   recovers those too, tagged `embedded:` in the signature column so they can
   be filtered out again.
4. **False positives.** A count that is higher for one category and much
   lower for another usually means junk, not coverage. Sort by size: false
   positives cluster in the large bands, and `triage_report.py` prints the
   largest files first for exactly that reason.

### Speed: workers, not the GPU

Carving is not a GPU workload. The job is scanning bytes for headers and
following length fields — memory-bandwidth and I/O bound, with almost no
arithmetic to offload. Shipping data across PCIe to a GPU and back costs more
than the matching saves, and for E01 images the other real cost is zlib
decompression inside libewf, which is CPU work no GPU library will take over.
Measured on one core here: signature scanning runs about 75 MB/s and reading
about 95 MB/s from page cache, so scanning and I/O are the same order of
magnitude, and the disk is usually the floor.

What does help is using the cores you have. Carving is embarrassingly
parallel across the image:

```bash
python3 media_core.py -i case.E01 -o ./out -w auto     # one per core, less one
python3 media_core.py -i case.E01 -o ./out -w 4
```

Both GUIs expose the same setting as **Parallel workers**. Each worker opens
the image itself, carves one stripe and writes what it finds to a temp
directory; the parent merges the results in offset order, applies the same
overlap and de-duplication rules a single-process run would, and owns the
manifest. The image is read once, not twice. The work is split into four
stripes per worker so one dense stripe cannot leave the other cores idle at
the end.

Output is identical either way, which is the property that matters — verified
on every test fixture: the same files, offsets, sizes and SHA-256 hashes from
a serial run and a parallel one, custom signatures included. Cancellation
works in both modes and cleans up its partial stripe output.

Expect roughly linear gains until the disk or the E01 decompressor becomes
the limit — typically somewhere around four to eight workers on a spinning
disk, more on NVMe. If your image lives on a USB or network drive, the drive
is the bottleneck and extra workers will not help.

### False positives and `--align`

On 400 MB of pure random data the carver produced 2 false positives with
byte-by-byte scanning, and none at all with `--align 512`. Unallocated space
often holds compressed or encrypted data that looks random, so prefer
`--align 512` (or 4096) on formatted media — real files start on a sector or
cluster boundary anyway. Throughput is roughly 55 MB/s single-threaded, so a
500 GB image takes a few hours; run per-partition jobs in parallel if that
matters.

### Validated

- 20 embedded media files (JPEG including EXIF-thumbnail, progressive and
  nested variants, PNG, GIF including animated, BMP, LZW TIFF, WebP, ICO,
  MP4, MOV, AVI, FLV, ASF, MPEG-TS, Ogg) carved from a synthetic raw image:
  20/20 byte-for-byte exact, verified by SHA-256 against the originals.
- `make_media_test_image.py`'s FAT32 volume: 8/8 byte-for-byte, comprising 5
  live files via the file system engine, 1 deleted entry read contiguously,
  and 2 unreferenced files via carving — with `NOTES.TXT` correctly ignored
  and 6 duplicate recoveries collapsed.
- The existing `make_test_image.py` volume: its deleted JPEG carves
  byte-identically.
- Split raw sets (`.001/.002/.003`) with files spanning segment boundaries:
  results identical to the single-file image.
- Flask layer in `media_gui.py`: job lifecycle, progress
  phases, cancellation mid-carve, directory browsing, inline preview with the
  right MIME type, per-file and manifest download, path-traversal rejection,
  and input validation. Frontend JavaScript is syntax-checked; the gallery
  layout was rendered and inspected.

**Not yet validated on real evidence.** The `pytsk3` and `pyewf` code paths
were written against those APIs but could not be executed in the environment
this was built in (neither module was installable there); the file system
engine was exercised against a mock TSK layer instead. Before casework, run
it against a ground-truth image — NIST CFReDS, or the Digital Corpora
`nps-2009-canon2` and `nps-2009-ntfs1` sets — and compare the manifest with
`fls` / `tsk_recover` output.

### Known limits

- APFS needs The Sleuth Kit 4.7+ built with pool support; without it, APFS
  volumes fall through to carving.
- BitLocker / FileVault / encrypted APFS volumes must be decrypted first.
- Carving cannot reassemble fragmented files — it stops at the first
  fragment. The file system engine handles fragmentation correctly because it
  follows the runlist.
- NTFS alternate data streams are not enumerated separately (default `$DATA`
  only).

## Custom carve signatures

The **Custom Signatures** tab lets you derive a header/footer byte pattern
for a file type the built-in list doesn't cover:

1. Enter one or more local paths to sample files of the format (more
   samples = better — the tool computes the byte pattern common to *all*
   of them, which filters out bytes that vary between files, like
   timestamps or IDs, that a single sample can't distinguish from a real
   signature).
2. Click **Analyze**. Review the computed header/footer hex — you can
   hand-edit either before saving if you want to trim or adjust it.
3. Give it a file extension and click **Add Signature**.
4. It's now included automatically the next time you run Carve, alongside
   the built-ins.

Custom signatures live for the current session only (tied to the open
image); they're not persisted to disk.

## Opening a mounted drive

You can't carve deleted data through a mount point directly — once a
partition is mounted, the OS's own filesystem driver serves file access
through normal file APIs, which never exposes unallocated space. What you
need instead is the **raw device node backing the mount**.

The interface lists the drives attached to this machine with the raw device
path behind each (`mount_detect.list_attached_sources`): `/dev/sdb1` on
Linux, `/dev/diskNsM` on macOS, `\\.\D:` or `\\.\PhysicalDriveN` on
Windows. Reading one needs `sudo` or an Administrator prompt. Only the Linux
listing has been exercised against a real system; macOS and Windows follow
documented OS conventions and are best-effort.

(The FAT32-only "Detect Mounted FAT32 Volumes" button belonged to the old
FAT32 browser, retired in v2026.11.319.)

## Known Windows gotcha

`\\.\E:` (a raw volume handle) doesn't support the same "seek to end to
find the size" trick that works on regular files — Windows rejects it
with `[Errno 22] Invalid argument`. This is fixed: the tool now probes
size lazily and only as a last resort (opening/reading a bare FAT32
volume never actually needs the raw size at all, since the boot sector
carries its own size field), and falls back to Windows'
`IOCTL_DISK_GET_LENGTH_INFO` if something else in the code path ever does
need it. If you still hit `Errno 22` on `\\.\X:`, make sure you're running
as Administrator — that's the far more common blocker for raw volume
access on Windows.

## What "carving" does and doesn't guarantee

FAT32 zeroes a file's FAT chain on delete — only the directory entry's
first-cluster pointer (and slack data) may survive, and even that's often
overwritten by a new file. So the carver doesn't do chain-based recovery of
deleted files; it scans unallocated clusters for known magic bytes (JPEG,
PNG, GIF, PDF, ZIP/Office, BMP, WAV, MP3) and reassembles matches from
**contiguous** unallocated clusters until it hits a footer or a size cap.
Fragmented deleted files, or ones partially overwritten by newer data, will
carve incompletely or not at all — that's inherent to the technique.

## Safety / evidence handling

Always run this against a **copy** of an image, never a live mounted device
or an original evidence file, to avoid any risk of modification.
