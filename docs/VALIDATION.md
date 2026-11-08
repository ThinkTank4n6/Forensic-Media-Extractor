# Validation protocol and known limitations

**Forensic Media Extractor**

This document is written to be disclosed. It sets out what the program
does, how that has been tested, and — at equal length — what it does not
do and what has not been tested. An examiner relying on this tool should
be able to hand this to opposing counsel without adding anything, and
should read the limitations before relying on a result.

It is written by the people who build the tool. It is not an independent
assessment, and no part of it should be described as one.

---

## 1. What the program is

A tool that recovers still images and video from storage media and from
phone extractions, records what it recovered and where from, lets an
examiner categorize what it found, and writes a report.

It is **not** a general forensic suite. It does not parse application
artifacts, chat databases, call logs, browser history or account records,
and it is not a substitute for a tool that does.

**Version and build.** Every validation record names the version and a
SHA-256 fingerprint of the code that ran. A result from one version is
evidence about that version.

**Every case says which build produced it.** The manifest records the
version, the engine version, the code fingerprint and the Python and
platform it ran on; the HTML, Word and PDF reports carry a *Produced
by* line; and the Griffeye set names it in
`SourceApplicationVersion`. A case processed before v202 does not
record it, and the reports say so rather than leaving the line blank.

## 2. What it does

- Reads raw images (`.dd`, `.img`, split `.001` and `.aa` sets), E01/Ex01, VHD and VMDK,
  mounted volumes and folders, and phone extractions packed by Cellebrite
  (`.ufdr`), GrayKey, Magnet and Android backup (unencrypted `adb` backups,
  read since v2026.11.318; before, they were claimed and carved as a disk).
- Walks file systems and lists what they hold, recovers deleted entries
  from FAT directory records and from the NTFS MFT, and carves
  unallocated space by signature.
- Extracts media held inside archives, documents, SQLite databases and
  application caches.
- Hashes every recovered file with MD5, SHA-1 and SHA-256 **as it is
  written**, and records each in a manifest.
- Checks hashes against imported sets (Project VIC, NSRL, CAID) and a
  shared store.
- Writes reports in HTML, Word and PDF, and exports for Griffeye
  (ProjectVIC JSON), LAVA and C4All.
- Reads AFF4 acquisitions, QCOW2, VMDK and VHDX disks, and LVM2 logical
  volumes, and walks ext, XFS, Btrfs and ReFS with the vendored reader.
- Records the hashes an acquisition stored about itself and, when asked,
  hashes the whole source and says whether it matches (v2026.11.313).
- Carves an assembled RAID array as well as walking it, and hashes every
  member when the source is hashed (v2026.11.317).
- Grades a case under one of seventeen regional category schemes, fixed
  for the life of the case (v2026.11.317).
- Ships the image models content and face search use, each checked by
  SHA-256; every result is labelled an investigative lead only
  (v2026.11.317).

## 3. How it has been tested

Three kinds of testing, which are not equally strong. They are described
separately on purpose.

### 3.1 Known-answer tests — the strongest

A volume is built by writing files whose contents are known, hashing them
**before any are deleted**, and recording those hashes beside the image.
The test asks whether the program returns those exact bytes.

The expected answer comes from what was written, not from any reader, so
a reader that is confidently wrong cannot satisfy it.

- `tools/make_fixtures/make_fat_deleted.py` builds the FAT32 fixture.
- `tools/make_fixtures/make_ntfs_deleted.sh` builds the NTFS fixture on a
  machine with `ntfsprogs`, including a **resident** file held inside its
  MFT record, which no carver can reach.
- `tests/test_known_answer.py` runs them.
- `tests/test_real_volumes.py` (v2026.11.306) reads volumes **written by
  the operating systems themselves** -- an NTFS volume Windows 11
  formatted and filled, recording each file's SHA-256 with `Get-FileHash`;
  exFAT, FAT32 and NTFS deletion volumes written by Linux's drivers and
  hashed before deletion -- through both readers, against those hashes.
- `tests/test_libewf_acquisition.py` (v2026.11.306) reads an E01 written by
  libewf's own `ewfacquire` through libewf and through the vendored reader.

**Until v2026.11.306, `FME_NO_TSK=1` did not switch TSK off.** It was
honored by the partition listing and not by the file-system walk, so on a
machine with `pytsk3` installed the "vendored" reading below was TSK
again, and the two readings agreed because they were one. Any comparison
recorded on such a machine before v2026.11.306 is a comparison of TSK with
itself and should be run again. The summary of every run now says whether
TSK read, was absent, or was switched off.

**What counts as independent here, stated carefully.** This program
reads NTFS, exFAT, APFS, HFS+ and ext through `pytsk3`, which is The
Sleuth Kit. Comparing that output against TSK's `icat` is therefore the
same library answering twice, and is not evidence that the library is
right — only that we call it consistently.

The independent comparison available in this tree is between its **two
readers**: `pytsk3`, and a vendored reader that reads the same file
systems with no C extension at all. They share no code. The known-answer
test runs the fixture through both (`FME_NO_TSK=1` selects the vendored
path) and compares each against the hashes recorded before deletion.

An `icat` comparison is still run where The Sleuth Kit is installed, and
is meaningful for the vendored reader — a reader from outside this
project agreeing on a known input. The record notes, where it applies,
that for the `pytsk3` path it is not an independent check. **Where TSK is
absent, the record says so and nothing is cross-checked.**

### 3.2 Behavioural tests

Thirty-eight test files covering the engines, the reports, the exports,
the hash store, the interface and the packaging. These check the program
behaves as its authors intend. They catch regressions. **They are not
evidence that an intention was correct**: a reader that has always been
wrong passes them every time.

### 3.3 Interface testing

`tests/test_browser.py` drives the real program in a real browser and
watches for anything the page throws while it is used. It opens a case
from the list, selects a file, grades it from the keyboard and confirms
the case on disk recorded it, undoes that, opens and closes the side
pane, clears the filters, opens every screen — the workspace, the case
list, the hash store, custom signatures, similar media — checks the
extraction options are present and that ticking one changes it, opens
the export and Project VIC panels, and asks the browser whether
anything is sitting on top of a control.

From v2026.11.293 every ribbon button and control (76) is also pressed in a
real browser by `tests/test_ui_sweep.py`, each from a freshly loaded page
with a case open and a file selected. That establishes that each control
runs without error and closes what it opens; it does not establish that the
output each one writes is correct, which the engine tests cover.

## 4. Running the protocol

```
python tools/validate.py --examiner "<name>" --agency "<agency>" \
                         --note "<why this run was made>"
```

Writes `validation-record.md`: the version, a code fingerprint, the
machine, which libraries were present, every test that ran, what each
asserts, and **every check that did not run**. A check that could not run
is recorded as not run, never as passed.

Run it after installing, after any upgrade of Python or a library, and
before a case where the tool's output will be relied on. Keep the record.

## 5. Known limitations

This section is the most important one.

### 5.1 Capability depends on libraries that may be absent

Without `pytsk3`, **NTFS, exFAT, HFS+, APFS and ext2/3/4 cannot be read
at all**; without `pyewf`, E01 is read by a vendored reader rather than
libewf; without `pillow-heif`, HEIC does not decode; without OpenCV,
there are no video key frames, no edited-copy matching and no face
search.

The program reports which are present (`python tools/verify.py`), and the
validation record names what was therefore untested. **A validation
record from one machine does not describe another.**

### 5.2 Carving recovers content, not context

A carved file has no name, no path and no file-system dates, because
those live in a directory record the carver never reads. Where a
directory entry for the same bytes is found, the name and the deleted
flag are attached, and the manifest says which engine supplied each.

### 5.3 A deleted file may be incomplete

Deleting a file frees its clusters. Where the chain is gone, the program
reads the recorded size straight through from the first cluster, which
**assumes the file was not fragmented**. Every such file carries a note
saying so. A recovered deleted file should be treated as what is in those
clusters now, not as what was there when the file was written.

### 5.4 Hash-set matches state membership, not nature

A match says a file appears in a set somebody imported. It does not say
what the file is. A file matching nothing has not been cleared of
anything.

### 5.5 Scores are not findings

Skin-tone screening, similarity, edited-copy matching and face search
produce scores to order an examiner's attention. **They are not
classifications and never grade anything.** A face-search score is not an
identification.

Since v2026.11.317 the models content and face search use ship with the
program (DINOv2-small, YuNet, SFace, each under its own licence, each
checked against its published SHA-256 before use), and every result is
labelled **Investigative lead only**. What was tested: the files are intact
and packaged; the bundled DINOv2 ranks an altered copy of a synthetic
picture first; YuNet and SFace load and Find faces runs over a case. **Face
detection and face matching have not been tested here on photographs of
people**: no such fixture is in the suite, and SFace was trained on adults'
faces. DINOv2 needs OpenCV 5.0 or newer.

### 5.6a Regional category schemes (v2026.11.317)

A case grades under the scheme chosen in Settings when it was made, and
keeps it. The schemes other than the United States are the category sets
the agencies distribute, as the other tool in this space carries them
(MIT); they have not been checked here against each agency's own current
release. Only the US scheme is Project VIC's own: in any other case a VICS
category is taken in, and exported, only as Non-Pertinent where the scheme
has one, and every other grade exports with no category. A hash store holds
one scheme and refuses a case graded under another.

**In v2026.11.317 grades still crossed schemes** through the examiner's
stash and the store scan, and a C4All import took an index's numbers as
the case's own; an outside review found all three. Since v2026.11.318 every
stash row records its scheme, the scan carries the store's, a code whose
scheme is not known is never applied, and a C4All import must name the
index's scheme and is refused when it is not the case's
(`tests/test_scheme_leaks.py`, `tests/test_c4all_import.py`). Limits:

- A stash row written before v2026.11.318 has no scheme and is no longer
  applied in any case, US cases included; it is still shown as a match.
- A build before v2026.11.317 knows nothing of regions. Since v2026.11.318
  a non-US case's locked categories are marked so such a build leaves their
  names alone, and the case records the build it needs, which builds from
  v2026.11.318 on enforce -- but an older build will still treat that
  case's codes as US ones (its 8 as Uncategorized, its 1 as VICS 1) in
  reports and exports. **Every install that opens a non-US case, and the
  hash server, must be v2026.11.318 or newer.** The client refuses to send
  a non-US case to a hash server older than 1.2.0.

### 5.6b Grayscale and masks change the view, not the evidence (v2026.11.317)

Grayscale and masks change how a picture is shown in the interface. They
are not applied to a recovered file, a report, an export or a thumbnail in
a report; a mask is kept with the case and every one drawn or cleared is in
its audit log. A storyboard's frames are decoded for viewing and kept with
the case's other derived frames; since v2026.11.318 they are decoded into a
temporary folder and moved into place whole, so a decode in progress or one
that died is never served as the whole video.

### 5.6c The bundled models, checked then opened (v2026.11.318)

Each bundled model's SHA-256 is checked before it is used, and OpenCV then
opens the file by its path. A file swapped between the two would be used
unchecked. This is not closed: doing it needs write access to the program's
own folder, which already allows replacing the program. Descriptions are
kept with the case per model since v2026.11.318, so a search is never made
across two models' numbers.

### 5.6 Content Credentials are claims

C2PA data states what a file says about itself. A valid signature shows
the claim has not been altered since signing; it does not show the claim
is true. The program reports invalid, valid-but-untrusted, and
valid-and-trusted separately, and ships no trust list.

### 5.7 A partial case says it is partial

A run that was interrupted can be rebuilt from the rows already written,
and can be resumed. A rebuilt manifest records that it was rebuilt and
that the case is partial. Treat it as covering what the run reached.

### 5.8 Times

Stored timestamps are never altered. Reports convert to a chosen time
zone, with daylight saving applied for the date in question, and state
which zone was used.

### 5.9 PDF images

Media is extracted from PDFs only where it is stored verbatim. Pages are
not rasterized: a rebuilt image's hash would be the tool's, not the
evidence's.

### 5.11 A format with no length field can carve one frame long

Some formats record no length anywhere: AMR and SILK voice recordings
among them. Their end is found by walking frames until one does not
make sense.

Whatever follows the file on the disk is then being judged as if it
were a frame, and unrelated bytes pass that judgement **about 1.8% of
the time**, measured over four hundred trials. The carve then takes one
frame more than the file holds.

**What that means.** The recovered file's hash is not the hash of the
original, so it will not match a hash set, and two tools carving the
same voice note may disagree. The content is right; the trailing frame
is not part of it.

It affects formats without a length field. Everything with one —
JPEG, PNG, MP4, and most of what a case holds — is unaffected.

This was found by chasing an intermittent test failure for nine
sessions. The test padded its image with random bytes, so it was
rolling that 1.8% on every run, and the failure looked like a problem
under load because load meant more runs.

### 5.12 Custom signatures are interface-only

An examiner can define a signature the program does not know — a
header, an optional footer and a maximum size — on the Custom
Signatures screen, and a run started from the page carves with it.
Verified end to end: a file type the program had never heard of was
recovered at its exact length.

**There is no `--signatures` flag on `extract`**, so a run started from
the command line cannot use them. A case that depends on a custom
signature must be run from the interface.

### 5.13 Encrypted volumes are recognized, and opened with the examiner's key

A BitLocker or LUKS volume reads as random bytes: every engine finds
nothing and a run reports **no files** — which is exactly what an
empty disk reports. The program now says which it is, in the log
before anything is carved and in the manifest afterwards, and says
what to do about it.

**Since v2026.11.302 it opens BitLocker and FileVault volumes with a
key the examiner supplies** -- a password, a recovery password or key,
or a BitLocker startup key file -- and otherwise decrypts nothing. Which
volume was opened, and with what kind of key, is stated in every report;
the key itself is not kept anywhere in the case (MANUAL.md, *Encrypted
volumes*). A BitLocker volume is decrypted beneath every engine. A
FileVault volume is decrypted for its file-system walk only: its free
space is carved as ciphertext, so nothing deleted from it is recovered
by carving, and the report says so. Without a key, unlock the volume on
its own machine, image it unlocked, and run that.

**An encrypted APFS volume (FileVault) is named too, since
v2026.11.301.** It is found differently: an APFS container is not
encrypted, the volumes inside it are, so no header at a partition's
start says "locked". The vendored APFS reader tests each volume; a
locked one is named in the log, the manifest's summary and the report,
by its volume name. Before v2026.11.301 a run over one recorded the
volume as read and empty, with nothing in the report to say otherwise.
A volume flagged encrypted whose blocks read in the clear -- an image
made through the Mac's own decryption -- is read, not reported locked.

**Opening them is the vendored reader's work**, which this program
calls with the examiner's key since v2026.11.302. Tested on a real
BitLocker volume opened by its startup key and a real FileVault volume
opened by its password, whose three files read back with the hashes
recorded when it was made; that a wrong key leaves the volume reported
locked and says so; that a carve worker reads the same plaintext as the
run that started it; that a run which read decrypted volumes will not
resume without the key; and that neither the password nor the key
file's bytes appear anywhere in a finished case, its log or its reports
(`tests/test_unlock.py`). Where pycryptodome was not installed, the
ciphers came from the `cryptography` package behind pycryptodome's
interface, and the test says so.

LUKS1 and LUKS2 are told apart, and the version, the UUID and the
advice differ accordingly — LUKS2 needs cryptsetup 2.0 or newer, and
the UUID is how a volume is matched to a key somebody else holds.

**Since v2026.11.318 a LUKS container opens with its passphrase**, decrypted
beneath every engine as BitLocker is. Tested on the real LUKS1 container
(aes-cbc-essiv): opened, no longer reported locked, and both files of the
ext4 inside read to the SHA-256 sha256sum recorded from the mounted volume;
a wrong passphrase leaves it reported locked (`tests/test_luks_unlock.py`).
The LUKS2 container's Argon2id key slot needs the `argon2-cffi` package,
which this environment could not install: there it is refused with that
reason, and **the LUKS2 opening has not been tested here**.

Tested against real volumes since v2026.11.301, each made by the tool
that makes them and carried with what it holds: a BitLocker volume
(Windows), a LUKS1 and a LUKS2 container (cryptsetup), and an APFS
volume macOS encrypted (`tests/fixtures/*.intended` says where each
comes from). Each is named as locked, with no files recovered and the
report saying it was locked rather than empty. Also tested: that an
ordinary FAT volume -- whose first sector begins the way BitLocker's
does -- and unencrypted APFS volumes are not called encrypted. Not
tested: a whole Mac disk, where a locked Data volume sits beside
readable system volumes; the volume-by-volume test is the same, but
no such image has been run.

### 5.14 A volume that cannot be read says so

ReFS, and any other file system the readers recognise but cannot
walk, used to produce the same output as an empty disk: no files, no
explanation.

A run that recovers nothing now asks the vendored reader what the
volume is and reports its answer in the reader's own words — the file
system, its version, and why the walk failed, such as a ReFS
superblock whose checksum does not hold. The manifest records which
file system it was.

**ReFS is recognised and described; its contents are not read.** No
part of this program walks a ReFS volume. If a case turns on one, image
it from a running Windows machine that has it mounted, and run this
against that image.

### 5.15 Windows Search: what is read, and how far it has been tested

The search index is read only to name cached thumbnails: it matches a
thumbnail in the cache to the file name and path the index holds for it.
Two forms are read.

**Windows 11 22H2 and later keep it as SQLite (`Windows.db`).** That
read carries a sixty-second limit. An index off a seized machine may be
truncated or have been written as the machine was pulled, and a damaged
one could otherwise send a query round a loop that does not end.

**Windows 10 and earlier keep it as an ESE database (`Windows.edb`),
read from v2026.11.293.** The ESE reader is third-party code
(`fme/vendor/impacket_ese.py`, Apache 1.1), and it loops forever on a
truncated file. It is therefore only ever run in a separate process that
is stopped after 120 seconds; a read that hangs, crashes or fails is
reported as **"could not be read"**, never as an index that named
nothing, because the second would read as a finding about the exhibit.
Rows the reader cannot decode are counted and reported; a run of 1,000
in a row ends the read, and says so.

**How far that has been tested.** A `Windows.edb` can only be written
by Windows' own database engine, which the development environment does
not have. The table and column handling, the byte order of each Windows
version, the counting of unreadable rows and the separate process were
tested against a stand-in for the reader that returns rows in the shape
the real one does. The real reader was run only on a damaged file, to
confirm it is stopped. **No real `Windows.edb` has been read by the
developers** (section 5.10, and docs/EXAMINER_TASKS.md).

### 5.16 An unreadable stretch of a disk is a gap, and is reported

On a live device with bad sectors, a failed read is retried a run of
sectors at a time and then sector by sector. Only the sectors that still
fail are lost; they are read as zeros, and every stretch that could not be
read is listed in the summary (`unreadable_ranges`, `unreadable_bytes`)
and in the report's coverage limits.

Before v2026.11.272 one failed read returned nothing for the whole request,
the carve took that as the end of the image, and the run reported a full
carve. A run made with an earlier version on a failing disk may have
examined nothing past the first bad sector, and does not say so.

A carved file whose extent runs into a stretch the source cannot give --
an acquisition reader returning nothing, as opposed to a device read as
zeros -- is stored up to that point and no further. No bytes are invented
to fill the gap. Its manifest row says `bounded = "short"`, and its note
gives the offset where reading stopped and how much of the judged length
was stored; the report counts these files among its coverage limits, and
the stretch is in `unreadable_ranges`. The stored file's digest is of the
part stored. Before v2026.11.279 such a file was stored short under the
`bounded` value of its full judged length, and nothing said it was short.

**A file that overlaps a bad stretch is stored with zeros there.** The
report lists the stretches; it does not yet mark the individual files
that cross them. Compare a file's offset and length against the list
before relying on its bytes. This program does not replace imaging a
failing disk with a tool built to retry and map bad sectors.

### 5.17 Every worker count carves the same files

The parallel carve splits the image into stripes. Before v2026.11.272 a
worker that could not see a file begun in the stripe before its own could
hide a real file lying just past that file's end, and the merge never
recovered it: measured, two workers lost a JPEG that one worker found.
The merge now re-scans whatever a rejected record had hidden, and
`tests/test_parallel_merge.py` checks one, two, three and four workers
against each other on an image built to contain that shape. The examiner's
minimum file sizes reach the workers too; before, workers used the
defaults whatever was set.

A case carved with more than one worker by an earlier version may lack
files a single-worker run of the same image finds. Where that matters,
carve it again.

**Candidate counts differ between worker counts.** Workers count every
header they judge, including some a single worker would have skipped; the
files carved are the same, the `candidates` figure is not comparable.

### 5.18 A split image is read whole, or refused

A split raw set with a missing segment, a set opened from any segment but
its first, and an `.aa`/`.ab` set were each read wrongly without a word
before v2026.11.272: a shorter image, an image with every offset moved, or
one segment read as the whole. Each is now refused with a message naming
the file to open or the segment missing. Chunks found beside a Cellebrite
`.ufd` that names none are joined in number order (dump2 before dump10),
and the log lists them.

### 5.19 A file read short, or across zero-filled sectors, says so (v2026.11.310)

A file a file system names but that cannot be read to the size it
records -- a read error part way, a cluster chain that ends early -- is
kept as far as it could be read and marked bounded "short", with a note
giving the recorded size, the size stored and why reading stopped. Its
digest is of the part stored, not of the original file. A file read from a
failing device across sectors the device returned as zeros is marked the
same way, with a ZERO-FILLED note naming the source offsets.

Limits of that check:

- It covers every read made while that file was being read, so a bad
  metadata sector read at the same moment is counted against the file
  too; the note says "while this file was read".
- TSK keeps its own block cache. A repeat read of a bad sector that TSK
  answers from that cache never reaches the device and is not seen.
- Volumes read by the vendored readers (QNX, F2FS, and the others in
  `vendor_fs`) read by path, so their reads are checked for being short
  but not for zero-filled sectors.
- `--overwrite` lets a new run use a folder that already holds a case. The
  earlier run's files stay on disk beside the new ones; without it, such a
  run is refused.
- LZNT1 (NTFS compression) is decompressed in pure Python. It matches
  Windows on files Windows wrote, but a multi-gigabyte compressed file is
  slow to recover.
- The Content Credentials (C2PA) check of the file's hash against the
  signed claim was tested on manifests this project builds itself, not on
  one from a real C2PA producer.

### 5.20 Hashing the source, and what a match does and does not show (v2026.11.313)

With **Hash the source** (`--hash-source`) a run reads the whole source
before anything else and records its MD5, SHA-1 and SHA-256, and whether
they match the hash the acquisition recorded. Tested against an E01 that
`ewfacquire` wrote, whose stored MD5 the run's own agreed with, and against
the same E01 with one byte changed, which was reported as not matching.

Limits of that check:

- It hashes the media as the reader presents it. An E01 read by libewf and
  by the vendored reader present the same media; this was checked on the
  vendored reader only, because libewf is not installed where the suite
  runs.
- A device read directly gives zeros where it cannot read a sector. Those
  zeros are in the digest, and the record says how many stretches there
  were. Such a digest will not match an image made by a tool that retried
  or mapped the bad sectors differently.
- Off by default on the command line, because it reads the evidence a
  second time end to end. A case made without it records the acquisition's
  stored hash, unchecked, and says so in every report.
- A raw image and a device carry no hash of their own, so there is nothing
  to verify against; the digests only identify the source as this run read
  it, for comparison with the acquisition notes.
- The digest of `manifest.json` written beside it shows a change to the
  record after the run. It is not a signature: anyone who can change the
  record can write a new digest beside it.

### 5.21 Volumes read by the vendored reader, and RAID members (v2026.11.315)

Checked against real images whose answers were recorded outside this
program: an LVM2 volume group (linear, split, striped, mirrored and grown
logical volumes, every file to the hash recorded when it was made), a
member of a Linux md RAID1 with 0.90 metadata, ReFS 3.14 written by
Windows 11 (all 2,544 files listed, 2,540 read to the SHA-256 Windows
recorded; the other four are not in the fixture), one ext4 disk as QCOW2,
VMDK and VHDX, and an AFF4 acquisition.

Limits of that check:

- Since v2026.11.316 a RAID array or an LVM group across disks is read with
  its other members (`--member`, or `--find-members`, on in the interface),
  and a RAID4/5/6 array missing a member is rebuilt from parity -- checked
  on md RAID1 and RAID5 and LVM RAID5/6/10 to the hashes the Linux kernel
  presented. Since v2026.11.317 the assembled array is carved as well as
  walked, and with Hash the source each member is hashed. The array carve
  is checked on the md RAID5 fixture (its 9 MiB carved) and, for a picture
  striped across members, on a stand-in array, not on real RAID evidence
  holding pictures. Since v2026.11.318 each array keeps its own account (its
own size budget, unreadable stretches in its own offsets, its partition on
anything found inside its files, and no second copy on a resume), and each
member is compared with its own acquisition hash. A stretch the carve
cannot read is measured in 64 KiB steps (before, the rest of a 1 MiB chunk
was skipped and called unreadable). **A Btrfs volume over several devices is still read
  from the device given**; the report says so in the reader's words. No
  multi-device fixture was available to test an assembly against.
- Single-device Btrfs is checked to its source's hashes, uncompressed and
  in zlib and LZO (572 files each). Files compressed with zstd need Python
  3.14's `compression.zstd` or the reader's own; where neither is present
  they are counted as unread and named, not read (`fme doctor` says which).
- An LVM logical volume holding no file system the reader knows (a raw
  volume, swap) is named in the report and not read; the carve still covers
  its bytes.
- These fixtures hold text and binary files, not pictures. They check that
  the volumes are read and every file in them is read correctly, not what
  this program then recovers from them.
- Encrypted AFF and AD1 images need the optional pycryptodome package. The
  suite opens them through a test stand-in built on the `cryptography`
  package when pycryptodome is absent, not through pycryptodome itself.
- An APFS file compressed with LZBITMAP is not read: Apple has not published
  the format. The report counts and names such files.
- Logical evidence (L01, Lx01, AD1) is read entry by entry since
  v2026.11.316, each checked against the MD5 its acquisition recorded;
  the AD1 fixture holds no pictures, so storage of a picture from logical
  evidence is checked with a stand-in, not a real L01. Since v2026.11.318 an
  entry read short is stored, marked short, its MD5 not claimed; a damaged
  entry no longer ends the phase; a mismatching MD5 is on the file's own row.

### 5.22 Where a grade goes, and what a resume will take (v2026.11.320)

A grading, flag, note or mask is refused by the server unless the file's
SHA-256 is in the case the request names, and the page discards an answer
to an open, run or close that a newer one has overtaken. Gradings made by a
hash set, the store or the stash never replace one an examiner has made,
even one made while the scan was running; they fill only files nobody has
graded. Gradings record the examiner named in this machine's Settings.

Limits:

- A progress note written before v2026.11.320 records no run identity.
  `--resume` still takes such a note, as before, rather than refuse every
  older interrupted run; check that the image and options are the same.
- The stash follows undo from v2026.11.320 on. A grade undone under an
  earlier build may still be in the stash; File → My stash shows what it
  holds, and grading the file again in any case replaces the row.
- A package entry or folder file read short is stored as far as it read and
  marked "short"; its digest is of the part stored and will not match a
  hash list entry for the whole file.
- A case folder replaced while it is open is reopened from disk on the next
  write; a decision made in the moment between the replacement and that
  write, in another window, is in the old file.
- The page's first-run question about blurring is remembered in that
  browser only; another browser, or a cleared one, asks again.

### 5.10 What has not been tested by the developers

Stated plainly, because it is the question that matters:

- **NTFS, exFAT and FAT32 through TSK** were not exercised in the
  development environment until v2026.11.306, which built TSK from source.
  They now are, on volumes the operating systems wrote (section 3.1) --
  by the developers, not by an examiner, and on small fixtures, not on
  evidence. **APFS and HFS+ with pictures on them** remain untested: the
  Apple volumes available hold none, so they show only that a reader walks
  them. **ext** has no fixture. The `pytsk3` published today cannot open
  an APFS container at all; APFS is read by the vendored reader only, on
  every machine, and so has no second reading to compare against.
- **E01 through libewf.** The E01 fixture this project packs is read
  by the vendored reader and **refused by libewf**, which is stricter
  and is the reference implementation. The fixture is therefore
  evidence about the vendored reader only. Reading a real acquisition
  — one written by FTK Imager or `ewfacquire` — is the only thing that
  says whether the libewf path works. Since v2026.11.306 one written by
  `ewfacquire` is read by both readers in the suite; one written by FTK
  Imager, and one of a real drive, have not been.

  From v222, where libewf refuses a file or opens it and reports
  nothing, the vendored reader is tried before the run gives up, and
  the log says which reader was used. **That fallback does not help
  with this fixture**: libewf accepts it, reports a media size, and
  reads nothing useful from it, which nothing can detect at the moment
  the file is opened. On a machine with libewf the fixture is
  therefore recorded as not tested rather than failed. An E01 fixture *is* now built and
  read — `tools/make_fixtures/make_e01.py` packs one from the format
  documentation and the pictures in it come back byte-for-byte — but
  the writer and the reader both live in this project, so that is a
  round-trip rather than an independent check, and on a machine without
  libewf it exercises the vendored reader only. An acquisition written
  by `ewfacquire` or FTK Imager and read here would be stronger, and
  has not been done.
- **Cross-checking against The Sleuth Kit** was not performed in the
  development environment before v2026.11.306, which lacked it; it now
  runs there, on the fixtures named in section 3.1 only. Note also that for the file
  systems read through `pytsk3`, TSK is not an independent check at all
  — it is the reader being checked. Independence for those comes from
  the vendored reader, or from a tool neither of them shares code with.
- **A real `Windows.edb`** has not been read (section 5.15). Only
  Windows can write one; the reading was tested against a stand-in.
- **Compressed GPU textures.** iOS app-switcher snapshots (`.ktx` and
  Apple `AAPL` files) are usually ASTC, often LZFSE-packed first. The
  libraries that decode those (`texture2ddecoder`, `pyliblzfse`) were not
  installed in the development environment, so only uncompressed textures
  were rendered there; a compressed one was checked only to say which
  package it needs. Textures and Apple CgBI PNGs are rendered for review
  only -- the recovered file and its hashes are never changed.
- **Anything behind a file dialog** is not covered by the browser
  tests: a native Browse dialog cannot be driven from the page, so
  choosing a source, a logo or a hash set that way is untested. The
  screens around those controls are covered.
- **Producing a report through the interface** is not covered end to
  end. The panels are opened and their controls checked; the reports
  themselves are tested separately, from the engine rather than
  through the page.
- **No independent party** has assessed this tool.
- **No error rate has been established.** None is claimed.

## 6. What to do when something looks wrong

The manifest, the audit log and the case file record what the program
did; a case can be reconstructed from them. If output appears incorrect,
preserve the case folder, note the version, and re-run against the
original source rather than the recovered output.

A known-answer run on the machine in question (`§4`) is the first
diagnostic.
