# Changelog

The release number is in `VERSION` and is reported by both GUIs, the console
banner, and the Project VIC and LAVA exports, so a report can be traced back
to the build that produced it.

## v2026.11.324 — The Filters pane of a large case

Reported against a case of 221,592 files: the Filters pane took "forever" to
fill, and grading a whole page did not move on to the next page.

- **The pane worked out every file's metadata values again on every count**,
  and counted again after every grading while it was open -- several seconds of
  server time each time on that case, during which the page's own requests (the
  next page of files after a grading among them) waited for the interpreter.
  A file's metadata does not change when it is graded, so its values are now
  worked out once per file list and time zone and kept; a count after a grading
  reads them (3.6 s, then 0.35 s, for 221,592 files on the test machine). The
  values are worked out in the background as soon as a large case's first page
  is asked for, so the pane is usually ready when it is opened. Capture-time
  years are read with the standard library's ISO parser first, which is most of
  the cold time saved. (`galleryquery.facet_table`, `galleryquery.warm`;
  `tests/test_filters_speed.py`)
- Grading a whole page and moving on is now checked in a browser, held whole
  and paged from the server, at 10 and at 500 files a page
  (`tests/test_bulk_grade_advances.py`). It moves on in both; the case it was
  reported against could not be reproduced here at full size, so if it still
  stays put with the Filters pane closed, that is a different cause.

## v2026.11.323 — The run summary, folded, shows its first line

With a case open, the run summary is folded to its first line so the tiles
start near the top. It was folded by clipping the whole summary to one line's
height -- and the results column, a flex box with its overflow hidden, was free
to shrink it further. In a window of ordinary height it showed as half a line of
cut-off text; in a short one, as nothing at all. Measured: 19 pixels tall at
720, 0 at 430. Nothing said there was more, and clicking a strip that could not
be seen was the only way to it.

The summary is now its first line ("221,592 file(s) -- …") and a **Run summary**
button; the rest opens below it and closes again. Nothing above the tiles in the
results column shrinks any more (`flex: none`); only the tiles scroll.
`tests/test_review_layout.py` checks the first line is whole at 720 and at 430
pixels, and that the button opens and closes the rest.

In a large case the Filters pane said only "Counting…" while the server counted
every file's values, which can take a minute for two hundred thousand files and
read as stuck. It now says how many files it is counting and that the gallery
works meanwhile.

## v2026.11.322 — What the suite found with The Sleuth Kit installed

The GitHub test run installs pytsk3, libewf-python and pycryptodome from
requirements.txt; the machine the suite had been run on had none of them. Four
tests failed there. Two were defects in the program, two in the tests.

**The program:**

- **A volume inside LVM or an unlocked LUKS volume was not read when TSK was
  installed.** The vendored reader is asked only for the file systems TSK does
  not read, and ext4 is one TSK reads -- but the pytsk3 on PyPI opens neither
  LVM nor LUKS, so TSK never saw those volumes and nothing read them. With the
  passphrase given, a LUKS volume's ext4 came back with no files; the five
  logical volumes of an LVM group were reported "not opened". A volume the
  vendored reader finds inside a container (`lvm_`, `luks_`, `md_`) is now read
  by it whatever its kind (`vendor_fs.inside_container`).
- **An ext4 volume TSK read in full, holding no pictures, was reported as one
  "this program could not read".** The check counted only the vendored reader's
  volumes as explained; it now counts TSK's too.

**The tests:**

- `test_evidence_hash` wrapped the E01's reader assuming the vendored reader;
  where libewf is installed it opens that E01 first. The wrapper now passes
  everything through.
- `test_filevault_locked` compared the unlocked FileVault volume's files by a
  path without the volume's name (`docs/readme.txt` against
  `CONVVOL/docs/readme.txt`). Without pycryptodome that check was skipped, so it
  had never run. The decryption was right.

Each of the four now passes with pytsk3 and pycryptodome installed and with
`FME_NO_TSK=1`.

## v2026.11.321 — Released under the MIT License

FME is now released under the MIT License (`LICENSE`), Copyright (c) 2026
Christopher A. Tankelewicz (ThinkTank4n6), written as a private project. `pyproject.toml`
said "Proprietary"; it says MIT.

What it includes keeps its own license, and the notices those licenses ask
for now travel with the program:

- **The Sleuth Kit**, read through pytsk3, is under the IBM Public License
  1.0 and the Common Public License 1.0. To hand on a build containing it,
  those require a disclaimer of every warranty and all liability on behalf of
  all contributors, a statement of where the source is, and IBM's copyright
  notice in a conspicuous place. `THIRD_PARTY_NOTICES.md` says all three; the
  notice is also in `fme version`, under File → Licenses, and in the version
  badge's tooltip.
- **libewf** (LGPL-3.0) and **pillow-heif**'s codecs -- libheif and libde265
  (LGPL-3.0) and x265 (GPL-2.0, which pillow-heif says makes its wheels GPLv2
  as a whole) -- are named, with their source addresses.
- The standalone build now carries `LICENSE`, `THIRD_PARTY_NOTICES.md`, the
  full license texts (`packaging/licenses/` → `licenses/`) and every bundled
  Python package's own license files from its installed metadata
  (`licenses/python/`, `packaging/notices.py`). The IBM and CPL texts are
  copied from The Sleuth Kit's own `licenses/` folder.
- README and MANUAL gain a "License and warranty" section, and the README an
  "Acknowledgments" section crediting, by name, the project the vendored
  readers came from (Alexis Brignoni and charpy4n6), Alexis Brignoni's own
  readers, The Sleuth Kit, libewf, Impacket and the rest. The rule that no
  document names that project now excepts those credits, in README and
  THIRD_PARTY_NOTICES.md only (`tests/test_manual.py`); the manual, the
  validation documents and this changelog still do not name it.

Test: `tests/test_licenses.py`.

## v2026.11.320 — What a review of v2026.11.319 found

A review of v2026.11.319 (bugs, edge cases, race conditions and usability)
found twelve serious defects, seven smaller ones and twelve usability
problems. Each defect was reproduced before it was fixed. All are fixed
here; what stays limited is said in VALIDATION.md.

**The most serious: a grade could land in the wrong case.** Opening case A
and then case B before A's file list arrived put A's files on screen under
B's job, and the next grading of one of them was written into B's case.db.
The page now takes a ticket for every open, run, close and restore, and
drops an answer a newer one has overtaken; and the server refuses a
grading, flag, note or mask for a hash that is not in the case it names
(409, "not in this case"). (`repro_case_open_race.py`,
`tests/test_grading_lands_right.py`)

### Grades go only where they were made

- A hash-set or store scan read every decision once and then wrote file by
  file, so a grade the examiner made during the scan was replaced by the
  set's. Machine gradings are now written with `only_if_undecided`, checked
  inside the same guarded write; a C4All import likewise leaves what the
  examiner decided alone unless told to overwrite.
- The stash only ever added. A grade undone in one case was applied to the
  next case holding the file as "your stash"; undo now takes it back out
  (`_sync_stash`, also used by grading).
- An examiner's own categories (10 and up) were carried to other cases as a
  bare number, where it named a different category. Only the scheme's own
  codes travel.

### Nothing truncated is stored as whole

- A zip or UFDR entry, or a folder file, whose read failed part way was
  stored truncated, unmarked, with no error counted. It is now stored as far
  as it read, marked `bounded: "short"` with the reason (a bad CRC, an I/O
  error), and counted as `read_short` in the summary and the report; a read
  that yields nothing is a read failure, as before.
- Under an unlocked BitLocker or LUKS volume the carve could not see the
  device's bad sectors; the decrypting reader now passes them through.

### The case on screen is the case on disk

- A case folder deleted or replaced while the program ran kept receiving
  gradings in the old, deleted case.db. The handle is checked against the
  file on every use and reopened, or refused if the folder is gone; Close
  now lets go of it (`/api/cases/close`).
- Gradings were credited to whoever ran the extraction; they carry the
  examiner this machine's Settings name.
- A failed duplicate write still changed the kept file's record (engine,
  path, instances); the change is applied only once the duplicate is stored.
- `duplicates.csv` is rewritten at close, so a row that failed to append
  is not missing from it.

### A resume belongs to its run

- `--resume` took any progress note for this run's. The note now records
  the source, its size and the options that shape the output, and a resume
  that differs is refused, saying what differs.
- A resumed folder or package run wrote every earlier file down again as a
  duplicate of itself; `package` and `folder` are resume-keyed engines now.

### Smaller fixes

- Importing a basemap deleted the hash-store token the examiner kept
  (`settings.save` keeps a stored token unless told otherwise).
- A failed frame decode removed every decode of that video in progress,
  another request's included; each decode has its own working folder.
- A model replaced at the same path stayed loaded; models are keyed by
  path, size and modification time.
- An Android backup's decompressed copy outlived the archive.
- One CaseDB method (`masks`) read the shared connection outside its guard.
- A failed save of a grading put back a file that a newer grading had since
  changed; it now rereads the case instead.
- The run summary's "recovered nothing" advice never showed (it compared a
  function with 0).

### Usability and flow

- **The pictures get the screen.** With a case open, the extraction form
  and the Project VIC and C4All panels step aside behind one line (Show
  extraction settings, the panels, New extraction), the run summary is one
  line until clicked, and the details pane keeps its place on the right
  from the start -- at 1280 px too -- so picking a file no longer reflows
  the gallery.
- **The viewer is a review mode.** Double-click or Enter opens a file full
  size; ← and → (and ‹ › in its header) move through the gallery; the header
  shows the file's category and flags and confirms each grading; M toggles
  the mask tool when M grades nothing.
- **Undo covers flags and notes**, newest first, and says which file went
  back to what.
- **Keyboard and screen readers**: ribbon items, ribbon tabs and filter
  chips are focusable and pressed with Enter or Space; tiles name their
  file; a skip link goes straight to the gallery; the search box has a
  label; status and messages are announced.
- **Messages** sit bottom right, clear of the ribbon, with a close button
  and the last ten under Earlier; an error stays until closed.
- **Wellbeing**: the first case reviewed on a machine asks, in a card that
  stops nothing, how pictures should be shown; the default blur covers what
  is not yet graded; which categories "the categories ticked" means is
  chosen per scheme (Display → Blurred categories).
- **Display controls** live on the Display tab from the start, rather than
  sitting in the toolbar until the tab was first opened and then vanishing.
- **The report dialog** fills the case number, examiner, evidence and
  agency from the run, the case as registered and Settings; warns when the
  case number or examiner is missing; and shows where it will write.
- **Status text**: a reopened case reads its folder for ProjectVIC.json
  instead of saying there is none; the store check says how many matched in
  the store's sets and in your stash, and how many of those were
  categorized; the details pane's Exact copies no longer shows a literal
  "<br>"; a hint that named a tab that no longer exists, and an error that
  told GUI users about `--store`, are reworded.
- **Words and one-click risks**: the badge names the case, not a job number
  that changed on every open; "Show" is "Show in folder"; the context menu
  names the Uncategorized category as the key does; Save asks before
  downloading evidence and points at Export files; removing a case from the
  list can be undone; the ribbon wraps at 1280 px; the manual says seven
  tabs.

Tests: `test_grading_lands_right.py`, `test_engine_honesty_320.py`,
`test_small_fixes_320.py`, `test_review_layout.py` (Chromium at 1280 x 720).

## v2026.11.319 — The old FAT32 browser is retired

**A run started from the old FAT32 browser never became a case.** Its Media
Extraction tab (`fme fat32gui`, port 5000) ran the engine and stopped: the
run's folder had no case.db, was not in the interface's case list, and was
never checked against the hash store, so a known CAM hash in it was never
flagged -- and nothing an examiner reads (the manual, VALIDATION.md)
described the browser at all. Its one unique use, browsing every file of a
FAT32 image, the examiner did not need. Retired, rather than repaired: the
main interface does all of the rest, through the case.
(`repro_fat32_gui_bypasses_case.py`, `tests/test_fat32_retired.py`)

- Removed: `fme/web/app.py` and `fme/web/templates/` (about 2,260 lines),
  the `fat32gui` tool, its place in the build spec and package data.
  `fme fat32gui` now says it is retired and where to go instead.
- Removed with it, as code nothing else called: fat32_core's own carve
  (`carve_unallocated` and its footer rules, 269 lines),
  `fat32_core.probe_fat32_volume`, and mount_detect's FAT32-only mount scan.
  fat32_core stays: the engine reads FAT32 volumes The Sleuth Kit refuses
  with it.
- Tests: `test_fat32_download.py` went with the browser; its sections of
  `test_request_guard.py`, `test_web_review_fixes.py`,
  `test_review_302_web.py`, `test_fat_carve_ends.py` and `test_packaging.py`
  likewise. README rewritten around the one interface.

## v2026.11.318 — What an outside review of v2026.11.317 found

A review of v2026.11.317 by four independent reviewers, given only the code,
found 29 defects; the suite passed over every one. All are fixed here except
one, said below. Most severe first:

- **Grades crossed regional schemes through the stash and the store scan.**
  The stash kept no region, and `/api/store/scan` rebuilt the store's matches
  without theirs, so both read as US codes: a file graded 1 in a Swedish case
  (not a notable grade there) was auto-graded **1 Child Abuse Material** in the
  next US case, and a Europol store's 3 (NON PERTINENT) became US 3 (CGI
  Animation). The stash now records the region of every grade; the scan
  carries the store's; a code whose scheme is not known is shown and never
  applied. A stash row from before this release has no region and is no
  longer applied anywhere. (`repro_stash_region_leak.py`, `test_scheme_leaks.py`)
- **The full-size viewer graded a file it was not showing.** A grading key
  moved the cursor on and left the viewer on the old picture, so the next key
  graded a file never shown; 0 and 1 were taken for zoom there, so in a US case
  Non-Pertinent and CAM did nothing. The viewer now follows the cursor and
  grades what it shows; 0 and 1 zoom only where they grade nothing.
- **A C4All import recorded another unit's grades as the examiner's own**, and
  copied its numbers into the case's scheme. They are now recorded as the
  index's (`hash set: C4All index …`), and the import asks which scheme the
  index was graded under and refuses one that is not the case's.
- **A non-US case could be sent to a hash server too old to keep it apart.**
  The server is 1.2.0 and says its region; the client refuses to send a
  non-US case to an older server or one holding another scheme.
- **Grades a hash set made were contributed as the case's own.** They are no
  longer contributed (`skipped_hash_set`).
- **The RAID array carve** shared the image carve's byte budget (an array
  stored one file and stopped, blamed on false matches), listed its gaps as
  the image's, lost its partition on embedded finds, and re-stored every file
  as a duplicate on resume. Each array now keeps its own account, gaps and
  partition, and skips what an interrupted run stored. With Hash the source,
  each member is compared with its own acquisition hash, as the source is.
- **After a short read, the carve skipped the rest of the 1 MiB chunk** and
  called it unreadable: a 49 KB gap was reported as 706 KB, and a whole picture
  after it was missed. A gap is now probed in 64 KiB steps for where reading
  resumes.
- **Logical evidence**: an entry read short was stored as whole with its MD5
  check silently skipped; a damaged compressed entry ended the phase; a resume
  wrote every entry down again as a duplicate of itself; a small entry was
  dropped as noise. Each is fixed; a short entry is stored and marked short,
  and a mismatching MD5 is on the file's own row.
- **A storyboard decoded part way was served as the whole video.** Frames are
  decoded beside the final folder and moved into place whole.
- **Any website could make the program hash or open a path** through three
  read requests (`/api/content/model?path=/dev/zero` tied up a thread for
  ever; on Windows a `\\host\share` path offers the examiner's credentials).
  A request naming a path now needs this program's header, as a write does.
- **Content and face descriptions were not tied to the model that made them**:
  a changed model found nothing, or compared two models' numbers. They are
  kept per model. Inference is serialized, so two searches cannot swap
  results. **And nothing in the page could start a face search**: the toast
  pointed at an "Others like this face" button that did not exist; it does now.
- **Case database**: undo could restore a deleted category, or (by number)
  wipe out later grades; a write that failed part way left a transaction open
  (a phantom undo batch, and "database is locked" for others); a build from
  before regions renamed a UK case's locked categories to US names (now marked
  so older builds leave them alone, and the case records the build it needs);
  a `#` in a case folder's name made the file index read every file as
  ungraded; a VICS export whose case could not be read went out with every
  Category null. Each is fixed.
- **Masks** were cached by file alone and leaked between cases, and could be
  saved to the next file; **blur** defaulted to US codes in every scheme.
- **Android `.ab` backups** were claimed and never read; they are now (an
  encrypted one is refused with its reason). XRY, also claimed, is not read
  and is no longer claimed.
- **LUKS containers open with their passphrase.** The vendored reader could
  always do it; nothing asked it to. Tested on the real LUKS1 container; the
  LUKS2 one needs `argon2-cffi`, which this environment lacks.
- The manual's eleven duplicated sections are removed; AUDIT.md is
  re-measured and corrected (the other tool does not parse app artifacts).

Not fixed, and why: the bundled models' SHA-256 is checked, then OpenCV opens
the file by path (finding 28). Exploiting the gap needs write access to the
program folder, which already allows replacing the program itself.

Tests: `test_scheme_leaks.py`, `test_viewer_grading.py`, `test_case_integrity.py`,
`test_android_backup.py`, `test_luks_unlock.py`; additions to
`test_raid_sets.py`, `test_acquisition_keys.py`, `test_request_guard.py`,
`test_wellness_storyboard.py`, `test_bundled_models.py`, `test_c4all_import.py`,
`test_sqlite_uri.py`.

## v2026.11.317 — Arrays carved, regional schemes, review aids, models shipped

The open items v2026.11.316 left, most severe first:

- **A deleted picture in a RAID array was missed.** Assembled arrays were
  walked but not carved: the carve covered only the image named, and a
  picture striped across members is whole on none of them. Each array the
  vendored reader assembles is now carved too, as the kernel would present
  it (phase `raid-carve`); its finds are marked with the array they came
  from (`raid:<array>`) at the array's own offset, kept apart from the
  image's resume point, and counted in `raid_carved`. With **Hash the
  source**, every other member is hashed like the source, and each report's
  evidence section lists them. (`repro_raid_not_carved.py`)
- **Every case graded in the Project VIC (US) scheme, wherever the unit
  was.** A grade is a bare number, and the same number means different
  things in different schemes: Europol's 3 is NON PERTINENT and the US 3 is
  CGI Animation; the UK's 0 is Uncategorised and the US 0 is Non-Pertinent.
  Seventeen regional schemes are now offered (File → Settings), taken from
  the other tool in this space (MIT). A case keeps the scheme it was made
  under for life; what a file starts as, where the NSRL may grade it, and
  how a Project VIC category maps in and out (outside the US, only
  Non-Pertinent) come from the case's scheme. A hash store holds one
  scheme: a case under another is not contributed to it, and its codes are
  not applied to that case. Reports say *Graded under*. A case from before
  this release is a US case. (`repro_region_blind_codes.py`)
- **Grayscale and masking.** View → Grayscale shows every picture in grey
  and is remembered. A mask drawn over part of a picture (full-size view →
  Mask) covers it there and in the details pane, is kept with the case and
  audited. Both change how a file is shown, never the file, an export or a
  report.
- **Storyboards.** A video's details pane has *Storyboard*: 48 evenly
  spaced frames, each with its time, decoded in a process of its own;
  clicking one opens the video at that moment.
- **The image models ship.** Content search and face search needed a model
  path nobody had set. DINOv2-small, YuNet and SFace now ship in
  `fme/models/` with their licences, each checked against its published
  SHA-256 before use; a path in Settings still wins. Every result is
  labelled **Investigative lead only**.
- Found on the way: the stash stored every grading with no label (it asked
  the case for a method that did not exist); a C4All index grading with a
  code the case does not have stopped the whole import -- now counted and
  left; a note on an ungraded file made a decision row with the US
  Uncategorized code whatever the case.
- Single-device Btrfs is now checked against its source's hashes,
  uncompressed and in zlib, LZO and zstd; zstd needs Python 3.14 (or the
  reader's own) and its files are counted as unread where it is missing.
  `fme doctor` says which.

Not done: a Btrfs volume across several devices is still read from the one
device given, and the report says so in the reader's words. No multi-device
fixture or tool to make one was available to test an assembly against.

Tests: `test_raid_sets.py` (arrays carved, members hashed),
`test_regions.py`, `test_wellness_storyboard.py`, `test_bundled_models.py`,
`test_real_evidence_fixtures.py` (Btrfs).

## v2026.11.316 — Files dropped without a word, keys never used, RAID sets

More real-evidence fixtures (an APFS volume macOS wrote with every decmpfs
method, APFS encrypted in place, encrypted and certificate-sealed AFFs, an
AD1 from FTK Imager 4.7, and md and LVM RAID member sets), and the four
defects they found, most severe first:

- **A file the vendored reader refused to decode was dropped without a
  word.** An APFS file compressed with LZBITMAP -- a format Apple has not
  published, which the reader declines rather than guess at -- came back
  empty, and the walk returned at its first read: not stored, not counted,
  in no report. Such files are now counted (`vendor_unread_files`), the
  first fifty named in the summary with the reader's reason, and every
  report says so.
- **A run took the examiner's passwords and keys and never gave them to the
  acquisition reader.** `VendorImageReader` had the parameters; nothing
  passed them. An AFF encrypted with a passphrase, one sealed to a
  certificate, or an AD-encrypted image was refused with the right key in
  hand. The run's credentials now reach the reader, the file-system walk
  and the parallel carve's workers; each password and each private key
  (PEM or DER; a BitLocker .BEK is not offered as one) is tried in turn. The
  encrypted AFF now opens and its six pictures are carved; a wrong password
  or key is refused and says which.
- **Logical evidence was carved as if it were a disk.** An L01, Lx01 or AD1
  holds files, each with its path, times and the MD5 the acquisition tool
  took, and the reader lists them; nothing asked. Each entry is now read as
  a file (engine `logical`), stored under its own path with its own times,
  and checked against its recorded MD5; a mismatch is a limit of every
  report.
- **A RAID array or an LVM group across disks could be read only from one
  member.** The vendored reader assembles md arrays (RAID0/1/4/5/6/10, a
  missing member rebuilt from parity) and LVM volume groups across images;
  nothing asked it to. `--member IMAGE` names the other members;
  `--find-members` (on in the interface) finds them in the image's folder
  by what each file records about itself, never by name, and the report
  lists every file used, any member missing, and any array rebuilt from
  parity.

Tests: `test_acquisition_keys.py`, `test_raid_sets.py` (every file of md
RAID1, RAID5 with all members and with one missing, and LVM RAID5/6/10
across five images, to the hashes the Linux kernel presented). The
pycryptodome test stand-in gained DES3 and RSA, test support only.

## v2026.11.315 — Real-evidence fixtures, and what they found

Thirteen real-evidence fixtures were added (an AFF4 acquisition; one ext4
disk as QCOW2, VMDK and VHDX; an LVM2 volume group; Linux md RAID1 members;
one device of a Btrfs RAID1; ReFS 3.14 written by Windows 11), each checked
against an answer recorded outside this program
(`tests/fixtures/FIXTURES-PROVENANCE.md`, `tests/test_real_evidence_fixtures.py`).
The readers read every one correctly -- 2,540 ReFS files and every LVM and
RAID file to the hashes recorded when they were made. What this program did
with the result was wrong five ways, most severe first:

- **An AFF4 acquisition was processed as a phone package.** An AFF4 is a
  ZIP, and any ZIP was taken for a package before the image readers were
  asked: the run listed the container's seven members, never walked or
  carved the disk inside, reported the container's size (17,683 bytes)
  rather than the disk's (63,894), recorded no source hash, and finished as
  if complete. An AFF4 is now recognised by what it holds
  (`image_readers.is_aff4`), whatever its name, and read as an image.
- **LVM volumes the vendored reader had read were reported "not opened".**
  The check for unopened containers runs before the walk; nothing took it
  back afterwards. Every report said of both physical volumes that the
  volumes inside were not opened, while five logical volumes had been
  walked. It is now reconciled after the walk (`lvm_volumes_read`), and the
  two logical volumes that hold no file system are named with the reader's
  reason.
- **A volume read in full that held no pictures was reported "could not be
  read by this program".** An ext4 volume walked file by file, with nothing
  in it this program recovers, was given the sentence meant for a volume
  nobody could read. It is now said only of a volume that was not walked.
- **The vendored reader's own reasons were dropped.** One device of a Btrfs
  RAID1 ("one device (devid 2) of 2"), a ReFS volume whose newest changes
  are still in its log, and an md RAID member ("read as part of its array
  when the other members are given with it") all came out as the same
  generic sentence. `vendor_fs.walk` now returns each volume it walked and
  each it did not, with the reader's note, and every report carries it.
- **Volumes read by the vendored reader were missing from the summary's
  list of volumes.** They were counted and not named; they are now listed
  like every other volume, marked "(vendored reader)".

Not changed, and said in VALIDATION.md: a RAID or multi-device volume is read
only from the member given; there is no way yet to give a run several member
images at once.

## v2026.11.314 — media_core.py split into layered modules

The last open item of the outside review: `media_core.py` was 8,829 lines
holding 222 top-level definitions, every reader, the carve, the deleted-file
passes and the command line.

- **Split into eleven modules plus media_core**, each using only the ones
  before it, so there is no import cycle:

  | module | what it holds | lines |
  | --- | --- | --- |
  | engine_common | constants, optional third-party modules, cancel | ~90 |
  | image_readers | raw, split, device, EWF, vendored readers, open_image | ~850 |
  | signatures | what a file's first bytes look like | ~270 |
  | length_parsers | how long a carved file is | ~1,440 |
  | carve_judging | `_carve_candidate`, the one judge of length | ~220 |
  | embedded | media inside documents, archives, databases | ~610 |
  | fs_walk | TSK, the FAT fallback, partitions and volumes | ~1,500 |
  | deleted_recovery | MFT, FAT and exFAT deleted entries | ~650 |
  | carving | the signature carve, serial and parallel | ~1,160 |
  | volume_survey | inspect_image, encrypted and unopened volumes | ~380 |
  | sources | packages, folders, rejoined streams | ~660 |
  | media_core | options, run_extraction, end of run, CLI | ~1,700 |

- **Nothing was rewritten.** The split was made by a script that moves each
  top-level statement, with its comments, unchanged, and checks that no
  module uses a name from a module after it. media_core re-exports every
  name it had, so `from fme.engines import media_core` and `media_core.X`
  work as before (checked: every top-level name of the old file is still an
  attribute).
- **The hazard the split brings, and the check for it.** Assigning
  `media_core.X = stand_in` now rebinds only media_core's copy of X; code in
  carving.py or fs_walk.py still calls the real one. Sixteen replacements in
  thirteen tests did that, and would have gone on passing while testing the
  real code. They now use `tests/_engine.py`, which rebinds the name in
  every engine module that binds it, and `tests/test_patch_targets.py`
  refuses any replacement that misses a module that reads the name.
- The parallel carve's workers import `carving` and what it needs, not
  media_core: a spawned worker no longer loads the whole engine.
- `tests/test_free_names.py`, `test_manual.py` and `test_walk_limits.py` read
  the new modules as well as media_core.

## v2026.11.313 — An outside review's findings, evidence hashing, and the case map

Fixed what a third outside review found, most severe first.

- **The hash server gave a read-only account hashes it was never sent.**
  A lookup returned the stored Project VIC record whole: send one MD5, get
  back the file's SHA-1, SHA-256, its PhotoDNA (licence-controlled) and the
  record's Description, Comment and RelativeFilePath. Its own docstring said
  it never did. A lookup now returns only `hashsets.VIC_SHAREABLE_FIELDS` --
  MediaID, Category, Series, Tags, the three flags and the precategorization
  fields -- an allow-list, so a field added later stays private until
  someone decides otherwise. The gallery's VIC detail is unchanged.
- **A DASH manifest's Representation id could write a file outside the case
  folder.** It went into the joined video's name unchanged; every other name
  was cleaned. Fixed at the call, and `OutputManager._dest` -- which every
  stored file passes -- now cleans any stem and extension (separators, a
  drive or alternate-data-stream colon, wildcards, control characters) and
  refuses a path that resolves outside the case.
- **Nothing tied a case to its evidence by hash.** A run now records the
  hashes the acquisition stored about itself (an E01's MD5 and SHA-1),
  always; with **Hash the source** (on by default in the interface,
  `--hash-source` on the command line) it hashes the whole source -- MD5,
  SHA-1, SHA-256 -- before the run and says whether it matches. A mismatch
  is stated in every report as a limit. `manifest.json` gets its SHA-256
  written beside it (`manifest.json.sha256`, as `sha256sum -c` reads it), and
  a report says whether the record has changed since. Every report format
  has a new section, The evidence. `fme/engines/evidence_hash.py`,
  `tests/test_evidence_hash.py` (an E01 written by ewfacquire verifies; the
  same E01 with one byte changed does not).
- **The count of unreadable bytes shrank past 1,000 bad stretches.** The
  list was cut to 1,000 and the next count was taken from the cut list:
  1,501 gaps reported a third less unread than there was. The whole list is
  kept for the run; only the summary's copy is trimmed, with
  `unreadable_range_count` saying how many there were, and the report gives
  the real number.
- **The hash server served plain HTTP on a network interface with only a
  warning.** It now refuses to start off loopback without `--cert` and
  `--key`, unless `--insecure` says a proxy handles TLS.
- **Failed sign-ins were not recorded.** An unrecognized token, on the API or
  the admin page, is now in the access log as "(unknown) refused", with the
  first eight characters of its hash and never the token; after ten from one
  address in five minutes the next are refused without another row. A known
  token is never turned away.
- **A reader saw the server's file paths** in the list of sets. Only an
  administrator does now.
- The hash store's regrade log and the C4All export now keep their times'
  zone offset.
- **Twenty read-only SQLite opens broke on a path with `?`, `#` or `%`**
  ("Case #12", "50% done"): the path went into a URI unescaped. All go
  through `fme.sqlite_uri.ro_uri` now; a test refuses an `f"file:..."`.
- **The FAT was held as a tuple of Python integers**, nine times its size:
  137 MiB for four million entries, over 2 GiB for a 256 GB card. Now an
  array of 32-bit integers (16 MiB for the same table). `extract_file`
  writes cluster by cluster instead of holding the file.
- **Folder walks stopped silently, at different depths.** TSK's at 64
  levels, the FAT fallback's at 32, and the FAT fallback dropped an
  unreadable folder with a bare `return`. One limit now
  (`WALK_DEPTH_LIMIT`, 64); a folder past it is counted
  (`fs_dirs_too_deep`) and an unreadable one is counted as the TSK walk
  counts it (`fs_dirs_unreadable`); both are in every report.
  `fat32_core.walk` says when it stops at 100,000 folders.
- **`extraction.walk` removed.** Nothing in the program called it, and its
  path builder split only on "/", so a member named `..\..\x.jpg` could
  escape on Windows. The two tests that used it now test `device_path`.
- **The contact sheet linked suspect SVG files.** A link opened one as a
  page, scripts and all. SVG, HTML and XML files are still shown through
  `<img>`, where no script runs, but never linked.
- `similarity.visual_stacks` compares a row at a time with NumPy: a group
  of 3,000 took two seconds in Python, now a twentieth of that, with the
  same stacks named after the same members (checked against the Python
  loop).
- Documentation: the README no longer says the FAT32 tabs ignore FAT12/16 or
  that the program has no readers of its own; AUDIT.md's fixture count is
  35, not 4; SERVER_SETUP.md's scheduled-task command had a form feed where
  `\f` of `\forensic_media_extractor` was.

Adopted from the other tool in this space (see docs/AUDIT.md):

- **The case map** (View → Case map). Every file the gallery's filter shows
  that carries a location, on the offline basemap, clustered, red for
  ungraded and amber for graded; a point takes you to its file. The filter
  is applied on the server, so it works on a paged case. No thumbnail is put
  on the map. MapLibre, pmtiles.js, the layer styles, fonts and sprites are
  vendored from it unchanged, under `fme/web/ui/maps/` with their licences; the
  page's policy now allows MapLibre's blob: worker. A raster PMTiles is
  drawn as raster (the style it came with assumed vector, and drew
  nothing).
  `tests/test_case_map.py` presses the button in Chromium.

## v2026.11.312 — NSRL and Project VIC on the hash server

- **The hash server's Sets page is now Hash sets, with a section for the
  NSRL and one for Project VIC** (and one for a list of your own). The
  section decides how the file is read; the page no longer guesses. Each
  import says how many files and hashes came in and, by category, what they
  are and what a match does to a file in the gallery: every NSRL file 0
  Non-Pertinent; so many Project VIC files CAM, so many Comparison, and so
  on. The counts are kept with the set (`sources.categories`) and shown for
  every NSRL and Project VIC set in the store, and written to the Activity
  record. An import runs in its own thread and the page refreshes until it
  is done: read inside the request, a national set outlasted the browser.
- **A Project VIC Category written as "1" was imported as no category.**
  Every reader checked `isinstance(category, int)`, so a set converted from
  CSV, or a vendor's export that quotes its numbers, imported with no
  categories and a CAM file matched against it stayed Uncategorized, with
  nothing to say why. `hashsets.vic_category` reads it in the store import,
  a set loaded for the session, matching and auto-grading.
- **A Project VIC file with no `@odata.context` was taken for a custom
  list** -- a bare array of records, or a case export -- and the server's
  page had no way to say otherwise, so every category was lost. A VICS file
  is now recognized by its records (`hashsets.looks_like_vics`).
- **A Project VIC file imported as the NSRL became known software.** Nothing
  read as the NSRL, so the salvage scan stored every hash in it as NSRL, and
  a CAM file no other set named was graded Non-Pertinent. A VICS file is now
  refused as anything but Project VIC, an NSRL import that finds nothing is
  never salvaged, and an unknown kind is refused rather than read as a
  text list.
- NSRL rows still hold no category in the store; they mean 0 because they
  are NSRL (`_apply_hash_categories`), as before, so a Project VIC category
  still beats them and clients of earlier releases read them the same way.
- `tests/test_hashset_import_categories.py`: imports through the server's
  page, then runs a case through the program and reads the gallery's
  grades.

## v2026.11.311 — the last two open items

- **A C4All re-export no longer leaves the earlier export's disc folders
  behind.** A case exported, graded down and exported again into the same
  folder kept the first export's `DVD02(...)`, `DVD03(...)` and so on. A
  disc burned from the folder, or a tool pointed at it, carried files the
  new index no longer listed. Now each export records how it placed every
  file (`C4All_placed.json`). The next export removes what it no longer
  lists, and the folders that leaves empty, but only where removing cannot
  lose evidence: a link or copy of a recovered file still in the case.

  A file an earlier export *moved* is the only copy. It is kept, named on
  screen and on the command line, and left out of the new index. A folder
  this program never exported into is not touched.
  `tests/test_c4all_stale_batches.py`.
- **Doing more with a finished case has buttons.** `/api/case/reprocess`
  (look inside recovered archives and documents; rebuild the gallery tiles)
  was written and tested in v2026.11.302 but reachable from nothing an
  examiner could press. The buttons are under **Do more with this case** on
  the Review screen. `tests/test_browser.py` opens the panel and presses
  one.

  Looking inside now reads the case's manifest rather than the page's copy
  of it, and the gallery is refreshed with what it found.
- **Correction to v2026.11.310.** It said the MD5 export "also works on
  large paged cases" because the server's file list was empty past
  `PAGED_THRESHOLD`. That was wrong: only the status answer sent to the
  page is empty; the server's list is not. The export does read the case's
  own records, which is still right. The comment in `routes_report.py` now
  says so.

## v2026.11.310 — what an outside review found, fixed

An outside review of v2026.11.309 found about forty defects. The worst
class was silent: evidence stored as if complete when it was not. Each item
below has a check in `tests/`.

**Stored as whole when it was not**

- A file a file system names (TSK walk, `fat32_core`, the vendor-fs readers)
  whose read failed part way was stored, hashed and listed as the whole
  file. It is now bounded "short", its note gives the recorded size, the
  size stored and why, and the report counts it (`short_reads`).
  `tests/test_fs_short_read.py`.
- A file read across sectors a failing device zero-filled (TSK walk, MFT,
  FAT deleted entries) is now bounded "short" with a ZERO-FILLED note
  naming the source offsets. A `--mode fs` run reports the unreadable
  ranges. A device read that came back short is marked bad instead of
  padded silently. `tests/test_zero_filled.py`.
- Segmented streams from two folders overwrote each other in scratch, and
  the survivor was recorded under the other folder's manifest. A manifest
  that cannot be planned is now logged and counted. A stream whose
  manifest gives no segment count is no longer called complete.
- Deleted NTFS files that were LZNT1-compressed are decompressed as Windows
  reads them. EFS-encrypted, overlay-compressed and online-only files are
  refused with a reason instead of being stored as pictures. A file over
  2 GiB is streamed instead of dropped.
- A deleted NTFS name whose MFT record was reused is no longer stored with
  another file's content, marked not deleted. A deleted folder name can no
  longer hide the live folder that reused its record.
- The FAT32 browser's carve no longer cuts a camera JPEG at its EXIF
  thumbnail, a ZIP at its end-record signature, or an updated PDF at its
  first %%EOF. Its carve thread and request threads can no longer read
  each other's clusters.
- A Content Credential's hash of the file counts only when the signed
  claim's hash covers it. A signed manifest moved into another picture
  with its hash box rewritten no longer reports that the hash matches.

**Found nothing, and said nothing**

- AVCHD .m2ts (192-byte packets) is now found by the carve. Its signature
  was tested at the 188-byte MPEG-TS stride and never matched.
- A file beginning "PK" that will not open as a zip is scanned as bytes
  instead of yielding nothing.
- The TSK walk logs and counts folders it cannot open and files it cannot
  read.
- MBR partitions on 4Kn disks are found at their real offsets.
- Vista thumbnail caches are read with Vista's own layout. A cache walk
  that stops early is logged and reported.
- A read that raises during the carve, or an error in the carve judge, is
  counted and passed over. Before, it ended the serial carve or lost a
  parallel stripe. The carve phase is caught like every other phase; a
  carve worker that dies still fails loudly (`CarveIncomplete`).
- The report lists everything a run passed over and counted.

**The record of a case**

- A run without `--resume` into a folder that already holds a case is
  refused (`--overwrite` to force). Before, it emptied the manifest and
  left the recovered files to be adopted as orphans.
- A resumed run no longer records files the interrupted run stored as
  duplicates of themselves: file system, MFT, FAT/exFAT deleted, embedded,
  and carve duplicates.
- A recovered file and its folder entry are flushed to disk before its
  manifest.db row commits. A manifest.csv line that fails after its row
  commits no longer drops the committed record when the run closes.
- The embedded scan of a carved file reads only the bytes stored, not its
  judged length into an unreadable stretch.
- MFT and FAT deleted-entry passes record creation time as `crtime`, as
  the TSK walk does, not `ctime`. NTFS times keep their fractions of a
  second and no longer drop dates before 1970.
- A video's `mvhd` capture time now says it is the container's own claim.
  A GoPro's is kept as its device clock with no zone.
- Cached video pieces are joined only within their own app's cache, and
  the record names where they are on disk instead of their URL.
- Phone extractions no longer invent device paths from a UFDR's category
  folders or from unrecognized members, and hidden folders keep their
  leading dot.

**Security**

- Stored XSS in the interface: `esc()` did not escape quotes, so a quote
  and event handler in an EXIF Make/Model/Artist, a path or a file name
  ran script with the page's own rights. It now escapes them, and the page
  is served with a nonce-based Content-Security-Policy that refuses inline
  handlers and injected scripts.
- Hash server: the admin Sets page put a source's name inside an inline
  `onsubmit` script, where a crafted name ran script in the
  administrator's session. Confirmation now reads a data attribute from
  `/admin/admin.js`, and the admin pages send a Content-Security-Policy.
- Hash store: a case source belongs to the person who first contributed
  it. A contribution to another examiner's case is refused (403) unless it
  comes from an administrator. Every changed grade is recorded old to new
  in `grade_changes` and in the server's access log. See DEPLOYMENT.md for
  how older cases are owned.
- Exported CSVs (exported_files.csv, the Project VIC match list, the MD5
  export) neutralize cells beginning with = + - @.

**Decisions and the shared store**

- `recover --undo` replays each file's whole history: undos, clears,
  notes, deleted categories and scheme migrations. Before, a file graded,
  undone and regraded could come back as CAM. Recovery applies all or
  nothing before the snapshot and keeps the original examiner and time.
  Undo and category deletion now log every file they change.
- Remote hash store: `import_set` takes `(path, kind, name)` like the
  local store and sends the kind. Before, a remote import sent the kind as
  the set's name. `contribute` returns the local store's keys, so a
  successful contribution is no longer shown as an error. A refusal is
  shown as the server's message.
- Hash-store imports never use `synchronous=OFF`. case.db and cases.db use
  a rollback journal on network shares. The case list reads under its
  lock. Gallery caches can no longer serve another run's results or raise
  a KeyError mid-refresh.
- Removing a hash set that is already gone says so instead of returning a
  500 error.

**Reports and exports**

- A report scoped to some categories now writes an MD5 list, KML and
  Project VIC file of those files only. Before, they covered the whole run.
- Two reports built at once can no longer show each other's time zone or
  hash-set matches.
- The C4All index is valid XML when a path holds a control character.
  Concurrent C4All exports no longer delete each other's half-placed
  files, and a second export of a case is refused while one runs. A
  re-export removes index files left by its earlier export.
- Files recovered by expand-containers appear in the gallery without
  reopening the case.
- The agency logo appears on HTML, Word and PDF reports. "Generated" is
  shown in the report's zone. A rebuild removes its own stale pages and
  never deletes a `_media` or `_thumbs` folder it did not create.
- Importing a Project VIC set whose paths name another Windows drive no
  longer fails. Importing into a shared store no longer requires the file
  to exist on this machine.

Left open here, both closed in v2026.11.311: `/api/case/reprocess` (rebuild
thumbnails, expand containers) had no button in the interface, and a C4All
re-export with fewer files left the earlier export's DVD batch folders
behind.

## v2026.11.309 — the gallery holds a page, not the case

A case past 20,000 files is no longer sent to the page. Opening one cost
46 MB of JSON at 55,508 files and would not have opened at a million; the
status answer is now a few hundred bytes, and the page asks the server for
the page it shows (`/api/gallery/query`, whose filter has been held to the
page's own since v2026.11.294). Everything that read the whole list was moved:

- the gallery, the pager and "all" (pages of 5,000), and a held page,
  re-read as its files stand now;
- the category bar, the Classify pane and the toggles' counts, and the
  Filters pane's facets, counted on the server and cached per filter;
- Select all matching and a report of what the filters show, from the
  server's list of every match; grading a selection of files no page has
  shown;
- a stacked tile, which still grades every file in its group;
- grading the last file of a page, which moves to the first file after it
  as the filter now stands; and a page graded whole, which moves on only
  once the gradings are stored, so the server's answer includes them;
- a run that passes the threshold while it goes, which switches to pages.

Below the threshold nothing changes. `FME_PAGED_THRESHOLD` sets it; at 0
every case is paged, and the browser tests pass that way too.
`tests/test_gallery_paged.py` (new) drives a 30,000-file case in Chromium;
`tests/test_gallery_parity.py` now also compares the counts. On a synthetic
million files: 3 ms a page once cached, 0.4 s a new filter, 1 s the counts,
3 s a search, 11 s the facet counts (drawn after the gallery). The facet
count was rewritten to decide each file once rather than once per facet.

Found while doing it: a second `remember()` would have replaced the one that
keeps the page's settings; caught by the duplicate-function check.

## v2026.11.308 — streams at the end of an image, and segmented downloads

**An MPEG transport stream that ran to the end of the image was never
carved.** A parser that reaches its cap has found no end, so its length is
treated as a guess and cut at the next header-like bytes inside it. The
transport-stream parser checks a sync byte on every packet; when it stops at
the end of the image the stream simply ended there. Read as a guess, a
93-packet recording was cut at a "moov" in its payload and dropped as too
short -- so a .ts that was the last thing on a card, or a segment carved on
its own, was lost. Stream parsers (MPEG-TS, M2TS, MPEG-PS) that stop at the
end of the image are now taken at their word. And a carved stream is no
longer found again inside itself: every packet boundary of a stream begins
another valid one. `tests/test_stream_at_end.py` fails on v2026.11.307.

**A video saved as DASH or HLS segments beside its manifest is joined.**
An app's offline download or a browser cache leaves an initialization
segment, many .m4s or .ts segments and a .mpd or .m3u8 side by side. Read
file by file, the initialization segment -- a few hundred bytes that play
nothing -- was stored as a video and every media segment was dropped. Each
stream the manifest lists is now joined in the manifest's order, a video and
its sound put in one MP4 without re-encoding, and a stream missing segments
says how far it got (`fme/engines/streams.py`, using the vendored exoprobe;
`tests/test_streams.py`, with a real three-second DASH encoding). The
segments are kept and listed, and left out of the gallery's tiles.

## v2026.11.307 — partitions TSK refused, one label per partition

**Partitions TSK refused were never walked, after the first.** TSK refuses
FAT32 volumes under 65,525 clusters -- small cards, and the EFI system
partition of many Windows disks -- and those go to this program's FAT
reader. They were handed over as (offset, label) tuples, which it did not
read, so each became offset 0: the first FAT volume was found from the start
of the image and credited to "p0", and every other refused partition was
skipped -- its pictures came back only from the carve, with no name, folder
or partition. Only on a machine with pytsk3. `tests/test_tsk_refused_partitions.py`
fails on v2026.11.306.

**A partition has one label whichever reader listed it.** TSK numbers its
own list, counting the partition table and every unallocated gap, so the
first partition of an MBR disk was "p2(Win95 FAT32 (0x0c))" with pytsk3 and
"p1" without. Partitions TSK lists are now named by their slot in the table,
as the table read here names them.

**A deleted FAT name's lost first letter is "?" from every reader.** TSK and
the FAT walk wrote "_", which a FAT name can really begin with
("_MG_0009.JPG"); the deleted-entry pass already wrote "?". A deleted name
TSK rebuilt from its long-name entries is left as it is: its first letter is
real. `tests/test_real_volumes.py` now also requires the two readers to name
every file the same way. `tests/test_fat_lost_letter.py`.

**macOS `._` sidecars are not pictures.** A Mac writes an AppleDouble file
beside everything it copies to a card; "._one.jpg" was stored as an image
by its extension.

**A case's file list is sent gzipped** (`fme/web/compression.py`): 51 MB was
4 MB. The browser still holds the whole list; the page asking for its page
of files is still open (docs/AUDIT.md). `tests/test_compression.py`.

## v2026.11.306 — run with The Sleuth Kit and libewf; the case record in SQLite

**`FME_NO_TSK=1` did not switch The Sleuth Kit off.** The partition listing
honored it and the file-system walk did not, so on a machine with pytsk3 --
every lab machine -- the "independent" reading was TSK again and the
two-reader cross-check (`tools/verify.py`, `tests/test_known_answer.py`)
compared TSK with itself. One decision now, `media_core.tsk_enabled()`; the
summary of every run says whether TSK read, was absent, or was switched off.
Comparisons recorded on a machine with pytsk3 before this release should be
run again. `tests/test_tsk_switch.py` fails on v2026.11.305.

**APFS was read by nobody on a machine with pytsk3.** The pytsk3 published
today has no `Pool_Info`, so TSK cannot open an APFS container, and with
pytsk3 installed the vendored reader was not asked for APFS. It now is:
`tsk_kinds()` says what TSK can read on this machine and the vendored reader
reads everything else. A Mac image is walked, by name and folder, instead of
carved.

**The first FAT partition was credited to "p0" when TSK listed the table.**
TSK lists the gap before the first partition as a partition at offset 0; the
image start is now tried last.

**A volume's upcase table was stored as a picture.** NTFS's `$UpCase` (read
by the vendored reader) and exFAT's `$UPCASE_TABLE` (presented by TSK) begin
`00 00 01 00`, which is how an icon begins. File-system structures at the top
of a volume are no longer stored unless `--include-system` asks.

**A file deleted with its exFAT folder keeps the folder's name** (it was
"(folder at cluster 6)/one.jpg"), and FAT short names written with Windows
NT's case bits read as the operating system shows them ("one.jpg", not
"ONE.JPG").

**Real volumes, both ways.** `tests/test_real_volumes.py` reads an NTFS
volume Windows 11 wrote and hashed, and exFAT, FAT32 and NTFS deletion
volumes Linux's drivers wrote, through TSK and through this program's
reader, against the operating systems' own hashes; the two readers now
return the same files on every one. `tests/test_libewf_acquisition.py` reads
an E01 written by libewf's `ewfacquire` through libewf and through the
vendored reader. The fixtures come from the comparison tool's suite (MIT;
fme/vendor/vendored.json). This environment now builds TSK, pytsk3 and
libewf from source, and the suite runs with them and without them.

**manifest.db.** The case record is now SQLite (`fme/engines/manifestdb.py`):
a stored file and its row, or a duplicate and what it taught the kept file,
are one transaction. A torn `manifest.csv` no longer loses a row; resume
and the rebuild of an interrupted case read the database, and a case from
before it is read from its CSVs as before and carried into a database on
resume. `manifest.csv`, `duplicates.csv` and `manifest.json` are still
written. `tests/test_manifestdb.py`.

**Offline maps.** Import a `.pmtiles` or `.mbtiles` basemap once (Settings,
or `fme basemap import region.pmtiles`); the case report then draws an
overview map of the located files and a locator on each located card, and
the details pane shows one, all drawn locally and each naming the basemap by
SHA-256. Adapted from the comparison tool (MIT). `tests/test_basemaps.py`.

## v2026.11.305 — files lost without a word, and a comparison review

**A full output drive dropped evidence and reported success.**
`OutputManager.store` caught every `OSError` with one log warning and
returned nothing, and nothing counted it. Once the output drive filled, every
later find was dropped the same way and the run still said "Finished". A read
error from the evidence went down the same path and was logged as "write
failed". Now reading and writing are told apart and both are counted
(`read_failed`, `write_failed`), the first 200 losses are named under
`failures` in manifest.json, and a full, over-quota or read-only output
stops the run through the engines' own cancel checks, keeping what was
recovered (`output_failure` in the summary). The page, the console summary,
the triage report and LAVA's case information all show the counts; the
command line exits 3 when files were lost. `tests/test_output_failure.py`
fails on v2026.11.304.

**A file a crash left unlisted is now in the manifest.** A crash between
storing a file and writing its row left a whole recovered file that was
named in `orphaned_files` and nowhere else: not in manifest.json, so not in
any export, and not in the dedupe table, so the resumed run stored the same
bytes again beside it. On resume it is now listed by its own hashes, engine
`unlisted-after-crash`, with a note that its origin was lost; C4All and the
HTML report say "origin unknown" rather than unallocated space. When the
resumed run finds the same bytes again, the record gets back where it was
found and the file is not stored twice (`orphans_restored`).
`tests/test_orphan_adoption.py`.

**qnxprobe 1.77**, re-vendored byte-identical from upstream (the copy the
comparison tool ships, same SHA-256). It stops an empty FAT12/FAT16 file reading the
root directory's cluster, and reads only the clusters a short read needs.

**`fme/engines/output.py`.** `OutputManager`, `Record`, the manifest helpers,
the size floors and `is_animated` moved out of `media_core.py` (now under
8,000 lines). Every name is re-exported from `media_core`, so nothing that
imports them changes.

## v2026.11.304 — what a second code review found

Reproduced against v2026.11.303 before each fix; `tests/test_review_303.py`
fails there and passes now (with additions to `test_review_302_store.py`,
`test_similarity_kept.py` and `test_video_isolation.py`).

**Auto-grading could grade CAM as Non-Pertinent.** Where hash sources
disagreed, the "most severe" category was taken as the lowest code -- and
code 0 is Non-Pertinent. A file the shared store graded 0 and the
examiner's stash graded 1 was auto-graded 0 and left the backlog. Severity
now ranks 0 last (`casedb.severity_rank`), and a stash/store disagreement,
which the scan reports as "neither applied", is no longer applied.

**Regressions in v2026.11.303, fixed.**
- C4All `--move`: a move that could not land deleted the case's file. A move
  now goes straight to its place and refuses one that is taken; a copy after
  an earlier hard-link export is a real copy.
- A resume's progress note could move behind the point it resumed from, and
  the next resume then stored duplicates again; it is clamped.
- A rebuilt manifest now carries instance counts, names and deleted flags
  from duplicates.csv, as a resume does.
- The FAT32 tool's page rejoins a running extraction after a reload, shows
  when an extracted file is short, will not start a second extraction of one
  image, and will not release an image a run is reading. The main window's
  start no longer races a release.
- A FAT32 boot sector with a stale 16-bit size no longer reads as empty.
- Contributions: the regrade runs inside the write lock. The server accepts
  up to 500,000 rows and 128 MiB in one request, so clients older than
  v2026.11.303 (which send a whole case at once) can still contribute.
- A time at the calendar's edge with an offset no longer stops a report.

**Project VIC.** A `RelativeFilePath` outside the export is refused (it read
any file on the machine into the case). A file failing any of its record's
hashes is imported without the record's category, and every hash given is
checked, not only MD5. A damaged record stops the read with an error instead
of ending the set silently there. A hash listed twice keeps the more severe
category, in the hash-set reader, the store import and session sets.

**Reports and exports.** Per-device counts put every copy on the device of
its first; each copy now counts where it was found. "Total media in the
case" in scoped reports was the report's own count. Evidence names with
characters XML forbids broke the DOCX and KML; they are written visibly
(`\x01`). CSV cells beginning `=`, `+`, `-` or `@` are not run as formulas.
The contact sheet links rather than embeds past 400 pictures. Picture ids in
the protocol DOCX no longer collide past 900 thumbnails. C4All index entries
that grade one hash two ways are reported, not resolved by order.

**Content Credentials.** A certificate was "trusted" when its issuer *name*
matched a trust-list entry -- a self-made certificate naming a real root
passed. Chains are now verified by signature. `verify()` never reached a
signature at all (three mismatches with the reader's structures); it does
now, and it checks the `c2pa.hash.data` binding, so it no longer says a
valid signature means the manifest belongs to this file unless it checked.
A file the reader could not read is no longer counted as "no credentials".

**Engines.** A SQLite blob whose overflow pages are missing is stored as
`bounded = short` with a note, not as a whole blob from a live row. A video
whose moov sits past the first 64 MB keeps its capture time and location.
An ExoPlayer rejoin no longer calls itself complete. EXIF DateTime (0x0132,
the time a file was changed) is no longer reported as the capture time;
latitudes beyond 90 degrees are refused. 16-bit grayscale pictures hash as
they look (`similarity.HASH_VERSION` 2: such hashes are redone), a picture
that would not decode is tried again next scan, and `Image.getdata()`
(deprecated in Pillow 12, removed in 14) is replaced. Batch workers' answers
are marked, so a library printing to stdout cannot shift every answer onto
the wrong file; frames left by a decoder that was stopped are removed.
Decode warnings are captured per thread. SD cards and NVMe partitions are
recognized as removable on Linux.

**Case state.** Undo keeps a note written after a grading and restores one
a "clear" removed, with the original grading time. Category and flag
changes check and write under one lock; re-adding a flag no longer logs its
creation. Relinking a case to its own path no longer deletes it. Settings
are written atomically, under a lock, never readable by others even for a
moment; saving one setting no longer blanks the rest. The gallery's file
list syncs and pages in one step. A saved category scheme keeps its codes.
Frozen builds record their code fingerprint at build time (it hashed only
the page's files before), and the fingerprint names files by path.

## v2026.11.303 — what a code review of v2026.11.302 found

Every item below was reproduced against v2026.11.302 before it was fixed,
and each has a check that fails on v2026.11.302 and passes now:
`tests/test_remote_scan_batch.py`, `test_review_302_engine.py`,
`test_review_302_store.py` and `test_review_302_web.py`.

**A scan through the shared hash server reported known files as
unmatched.** The server's `/lookup` merged matches per hash set across the
whole batch, and the client maps them back to files by digest, so five
files of one set came back as 1 matched, 4 unmatched; the local store said
5 of 5. `HashStore.lookup(per_digest=True)` answers per digest, and the
server uses it. *The server must be updated for this to take effect:* an
updated client against an older server still undercounts.

**Evidence integrity.**
- *A parallel resume lost a file at a stripe boundary.* A file starting just
  before a boundary with its header just after it (MP4 +4, WAV/AVI +8) was
  in no stripe on a resume from that boundary. The stripe stays while its
  header window reaches past the resume point, and neither carve stores
  again what came before the resume point -- which also ends a resume
  writing duplicates down a second time and inflating "found in N places".
  A record taken off the merge queue but not yet stored is kept in front of
  the resume point; a crash inside a carved file's embedded scan is finished
  on resume; and how far earlier carved files reach is taken from duplicates
  too, by the bytes the carve stepped over (new `extent` column), not the
  bytes stored.
- *A crash and a resume undid what duplicates had taught a kept file* --
  its instance count, and a carved file's name, path and deleted flag from
  a directory entry. They are rebuilt from duplicates.csv.
- *A rebuilt manifest called every file deleted* ("False" read as true),
  listed a torn last row with no hash, and dropped duplicates.csv; the
  rebuilt case could not be opened (sizes were strings). Rows now go through
  `_record_from_row`, as a resume reads them.
- *A second C4All export overwrote the case's own files* through the hard
  links of the first. Files are placed under a scratch name and renamed,
  never written through an existing one. Files whose names contain ":" are
  no longer left in the index when they could not be placed.
- *NTFS:* a run that does not read in full now ends the file there; the
  rest used to be appended at the wrong offset, stored and hashed. A sparse
  stretch of `$MFT` no longer renumbers every later record, and a run of
  zero clusters no longer reads the rest of the volume as MFT records.
- *FAT:* chains stop at the volume's last cluster, not the FAT's padded
  length, which read into the next partition. The access time is a date
  (it came back as midnight), and the creation time keeps its hundredths.
- *A carved file across a bad sector* stops at the sector and is recorded
  short, instead of being stored with zeros where the sector was.

**Shared hash store.**
- A contributor could add hashes to an imported set (NSRL, Project VIC);
  contributions now go only into case sets.
- A regraded file contributed again kept its old grading while the reply
  said "added"; it takes the new one, and counts are of rows that landed.
- A failed import left a partial set that looked complete and blocked a
  retry; it is taken out again, and the API answers 400, not 500.
- A contribution racing a removal could land under a deleted set, and two
  first contributions under one new name collided; both now take the write
  lock first.
- Account reads used the shared connection without its lock (random 500s).
- `/admin` showed every person's activity to anyone signed in; without the
  audit right a person sees their own row. Request bodies are capped
  (16 MiB); the client sends contributions in batches of 5,000.
- "Cases fed back" counted imported files, not contributed cases.

**Interface and exports.**
- Reprocess rewrote manifest.json with an empty summary, losing the source
  image, its hashes and the tool version; it keeps the summary now, and two
  reprocesses of one case cannot run at once. Its audit entry and the C4All
  import's were left uncommitted, holding case.db's write lock until
  something else committed.
- "Rebuild" and "Open" refuse a folder an extraction is still writing.
- A report that failed with an unexpected error stayed "running" for ever.
- Credential verification and content search accepted any path on the
  machine; they go through the same containment as every other route.
- Cancel answers "ok" only for a run that will stop; the page shows why not.
- The similarity scan left the job's "recovered" holding images hashed.
- `fme fat32gui` had no `main()` to call. The FAT32 tool's extracted files
  no longer overwrite one another by name, links are URL-encoded, a short
  chain is reported, and starting a carve no longer loses the page's media
  extraction.
- PDF: a JPEG under `[/FlateDecode /DCTDecode]` comes out as the JPEG, not
  zlib bytes named .jpg; an attachment with `/Filter` before `/Type` is
  decoded; payloads keep their own trailing newlines, and `/Length` is used.
- Project VIC staging no longer gives two files that share an MD5 one file.
- Times at the calendar's ends (0001-01-01, Project VIC's "unknown") are
  shown in UTC instead of stopping the report.

## v2026.11.302 — encrypted volumes open with the examiner's key

**BitLocker and FileVault volumes open with a key the examiner supplies.**
The vendored reader could always do it; nothing called it, and the unit
decided it wants it. A run now takes passwords, recovery passwords or keys,
and BitLocker startup key (`.BEK`) files -- in the window, the *Encrypted
volumes* fields; on the command line `--ask-password`, `--password-file` and
`--key-file` -- and tries each locked volume with every one
(`fme/engines/unlock.py`).

- *BitLocker* is decrypted beneath the program: the file-system walk, TSK,
  the MFT and FAT passes and the carve all read plaintext, and so does every
  parallel carve worker, which opens the image itself and is now given the
  key over its process pipe.
- *FileVault (APFS)* is decrypted by the APFS reader, the only one that can:
  its files come back with names, folders and dates, read by that reader
  even where TSK reads APFS (TSK cannot take a password). Its free space is
  carved as ciphertext; the report says nothing deleted from it was
  recovered by carving.
- *A key that opens nothing* leaves the volume reported locked, with the
  report adding that the keys given did not open it, and a FileVault
  volume's passphrase hint as stored.

**The key is never written down.** Not in the manifest, the progress note,
the log, case.db, a report, or the job the window reads back; credentials
print as a count. What is written is which volume was opened and with what
kind of key -- a key file by name and SHA-256 -- in every report, under
*Encrypted volumes opened*. A run that read decrypted volumes refuses to
resume without the key, rather than carve the rest as ciphertext. There is
no `--password VALUE`: it would sit in the shell's history and the process
list, and typed anyway it is refused with that reason (before this, argparse
would have read it as an abbreviation of `--password-file`). A key given to an
installation without pycryptodome stops the run before anything is read;
`fme doctor` reports it, and the PyInstaller spec now collects it, warning at
build time when it is absent -- nothing ensured the frozen build had it.

`tests/test_unlock.py` (26 checks) opens the BitLocker fixture with its
startup key (added to `tests/fixtures/`, made for the fixture) and the
FileVault fixture with its password, whose three files read back with the
hashes recorded when it was made; checks that a carve worker reads the same
plaintext as its parent; and searches every file the runs left -- refused
runs included -- their log and their reports in five formats for the
passwords and the key file's bytes. Made to leak a password into a refused
run's summary, it names the file. pycryptodome was not installed where this
was built: the ciphers came from a test-only stand-in on the cryptography
package (`tests/support/pycryptodome_standin`), and the test says so; on a
machine with pycryptodome it is the real one that is tested.

Also: the "nothing recovered" diagnosis described an opened BitLocker volume
as BitLocker; it now describes what is inside it.

## v2026.11.301 — a FileVault-locked APFS volume is named, not read as empty

**A Mac volume FileVault had locked came back as a volume read and found
empty.** On a real APFS volume macOS encrypted, made on a Mac, the run logged
"Reading 1 volume(s) the main engine cannot: apfs" and "0 file(s)", recorded
the volume as read (`vendor_volumes` 1, `vendor_damaged` 0), and put nothing in
the summary or the report to say it was encrypted. The vendored reader knew:
it tests each APFS volume, lists nothing from a locked one, and says why --
and `vendor_fs.walk()` kept the count and dropped the reason. BitLocker and
LUKS were never affected; they are found by the header at a partition's
start, and an APFS container has no such header, because the volumes inside
it are what is encrypted.

`vendor_fs.locked_volumes()` now asks the reader which APFS volumes are
locked, from its own per-volume state rather than its wording, and the run
and `inspect_image` add them to the same list BitLocker and LUKS use -- so
the log warning, the manifest's summary and the report's limits name the
volume ("The APFS volume CONVVOL in partition ... is encrypted (FileVault)
and was locked ... That is not the same as it being empty."). It is asked
whichever engine walks the image. A volume flagged encrypted that reads in
the clear -- imaged through the Mac's own decryption -- is not reported. The
advice names the password and the FileVault recovery key and promises no
unlock: the vendored unlocker is still not called (AUDIT.md item 2).

**The first real encrypted volumes in the suite.** The FileVault volume, a
BitLocker volume (Windows) and LUKS1 and LUKS2 containers (cryptsetup) are in
`tests/fixtures/`, each with an `.intended` file saying where it comes from
and what it holds; all four are from the comparison tool's fixtures, MIT.
Until now these checks were tested only against the signatures they look
for. `tests/test_filevault_locked.py` fails on the previous code;
`tests/test_real_encrypted_volumes.py` passes on both, which is what it is
for. Checked for false alarms on three unencrypted APFS and HFS+ volumes and
the existing fixtures: none. A 20-line standalone reproduction was delivered
alongside this release.

## v2026.11.300 — AUDIT.md's open list, checked item by item

**The list that decides what to build next said "open" for work done long
before.** Each item was checked against the code, and the list rewritten
with what is open, ranked, and what closed it below:

- *Closed:* duplicate definitions (since v218: `test_packaging.py` refuses
  a second definition, and an AST scan finds none); random-byte carve tests
  (each named test is seeded, zero-padded or does not carve; `test_containers`
  ran 160 times and `test_nothing_recovered_warning` 40, without a failure);
  and, from this series, the time model (.298) and `test_resume` (.299).
- *Found open while checking:* the vendored reader's encrypted-APFS
  unlocker is as unreachable as its BitLocker one, and nothing establishes
  that an encrypted APFS (FileVault) volume is reported as encrypted rather
  than as empty -- `ENCRYPTED_VOLUMES` names BitLocker and LUKS, and no test
  builds an encrypted APFS volume. Recorded, not changed: it needs a fixture
  made on a Mac.
- *Corrected:* the fixture gap is 69 against 4, not 40 against 4.

`docs/EXAMINER_TASKS.md`'s Results table had no row for Session G, the only
test of the ESE reader against a real index; it has one. No program code
changed.

## v2026.11.299 — test_resume means the same thing on a busy machine

**`tests/test_resume.py` passed or failed with the machine's load, and printed
"True" either way.** It started a run, slept a fixed 6 or 8 seconds, and killed
it. On a loaded machine the run had recorded nothing by then, so the check that
a finished phase and the carve's position share one progress note failed --
beneath a line printing "both survive in the same note: True", which was a
fixed string. On a fast machine the run could finish first, and "killed part
way" would have tested nothing. Each kill now waits for the state its checks
need (files kept and the carve's position recorded; a finished phase and the
carve's position), and a run that finishes before it can be interrupted, or
shows nothing to interrupt in four minutes, fails the test with that reason.
The line reports the value it checked. With the one core here oversubscribed
two to one, the previous test failed ("the carve reaching 0 MiB ... True") and
this one passed. No program code changed. A 16-line standalone reproduction
was delivered alongside this release.

## v2026.11.298 — instants and zone-less readings are kept apart

**A record's times are kept by kind.** `captured`, `crtime`, `mtime`, `atime`
and `ctime` now hold only times that say their zone. A time the evidence
recorded with none -- a FAT or exFAT file time, a camera clock with no offset
-- moves to a new `recorded_times` field, JSON naming the field it belongs
to; one that is not a readable time at all is kept there as the text it was.
`timeutil.settle()` does this in `OutputManager.store`, the one path every
record takes, and every reader in `exports/`, `review/` and `web/` now goes
through `timeutil.stored()`, `instant()` or `reading()`. Those also read
cases made by earlier versions, where both kinds sat in the time fields, so
an existing case opens, reports and exports as it did.

Why: v2026.11.295, .296 and .297 each fixed a reader that took a time
straight from its field and cut the string -- dropping the zone of a UTC time
or dating it in UTC. With the two kinds in one field, the end of a string was
all that told them apart. `tests/test_time_model.py` refuses any reader that
goes around the accessors (it names the file and line), and checks what
`store()` writes, that old manifests read the same, and every report format.

**The Word and PDF reports printed times as stored.** Found while moving the
readers: an NTFS time appeared in UTC rather than the case's zone, and a FAT
time with no mark, beneath a header naming the case's zone. The HTML report
was right. All three now go through one conversion (`report._time_shown`);
the report's CSV keeps the stored ISO 8601 text, readings included.

**For anything built on `manifest.csv`:** FAT and camera-clock times are now
in the `recorded_times` column, not `mtime` or `captured`. MANUAL.md, "How
the case folder keeps them".

Checked against v2026.11.297 on `tests/media_test.img`: the same files, and
for every file and time field `stored()` returns exactly the text the earlier
version wrote; its six FAT-timed files' readings moved to `recorded_times`.
The time model follows the comparison tool's (docs/AUDIT.md).

## v2026.11.297 — the gallery's year filters count a time where the examiner is

**Filtering the gallery to a year hid evening videos from December 31.** The
year facets -- Captured, File created, last written, last accessed -- took
the first four characters of the stored time. A video's capture time is UTC
(MP4), so a clip taken at 9 PM on December 31 in Philadelphia counted as
2025, and choosing 2024 hid it while its details pane said 2024-12-31 21:00
EST. The gallery rows also cut file-system times to 19 characters, so an NTFS
time in UTC reached the page with no zone at all. Rows now carry the whole
time; a time with a zone is counted in the zone the page shows times in, and
one without (a camera clock, a FAT time) keeps the year the device recorded.
The page (`app.js year()`) and the server (`galleryquery.year`, which takes
the page's `timeZone`) change together. `tests/test_gallery_parity.py` now
generates zoned times either side of New Year and varies the zone per state:
a server that ignores the zone disagrees with the page on half the states.
`tests/test_gallery_year_zone.py` fails on the previous code. A 19-line
standalone reproduction was delivered alongside this release.

## v2026.11.296 — the report's summary dates evidence in the report's zone

**The summary put evening videos on the next day.** A report's rows show
each capture time in the zone the case chose, but its "Capture dates" range
and its "When they were taken" chart cut the stored string instead. A video's
capture time is MP4's, which is UTC, so in Philadelphia every clip taken
after 7 PM (8 PM in summer) was counted on the following day -- and one taken
at 9 PM on December 31 was counted in the next year, by the same report
whose row for it said December 31 at 21:00 EST. Both now use
`report._shown_day`, which converts a zoned time into the report's zone as
the rows do and leaves a zone-less time (a camera clock, a FAT time) on the
day the device recorded. `tests/test_report_dates_in_zone.py` fails on the
previous code. A 17-line standalone reproduction was delivered alongside
this release.

Still open in the same class: the gallery's year facets read the stored
string's year, so they too are UTC for a video (`media_gui._job_file_entry`,
`galleryquery.year`, `app.js year()`). The structural fix is the comparison tool's time
model -- zoned instants and zone-less readings in separate fields (AUDIT.md) -- planned
as its own release.

## v2026.11.295 — the contact sheet says which zone its times are in

**The contact sheet printed UTC times with their zone cut off.** Its
"Modified" cell was the stored time cut to 19 characters. An NTFS time is
stored in UTC, so `2024-07-04T18:00:00+00:00` printed as `2024-07-04T18:00:00`
-- read in Philadelphia as 6 PM when it was 2 PM EDT -- and exactly like a FAT
time, which is the device's own clock with no zone at all. The "Captured" cell
printed EXIF times unlabeled, and "generated" was the examining machine's clock
with no zone. All three now go through `timeutil.show`, as every other export
already did: a zoned time is converted and carries its offset, a zone-less one
is shown as recorded and labeled `(device clock; zone not recorded)`, and the
header says which zone was used. The sheet takes a zone: `--timezone` on
`fme contactsheet`, `timezone` on `/api/report/html`; the default is UTC.
`tests/test_contact_sheet_zones.py` fails on the previous code. A 19-line
standalone reproduction was delivered alongside this release. The v2026.11.281
review of exported times did not list `report_html.py` among what it read.

## v2026.11.294 — no video is opened in the examiner's process; rescans stop re-decoding

**One malformed video could end the session.** Opening a video is FFmpeg
parsing it, and a carved clip can crash that outright -- which is why
filmstrips were decoded in a child process. Three paths did it in-process
anyway: a frozen build (what examiners run) decoded every filmstrip here
because it had no interpreter to start; a child that would not start fell
back to decoding here; and the report opened every video in the case here,
in every build, to measure its length. All three now go through
`fme/engines/isolated.py` (frozen builds re-run themselves with `_worker`).
The report measures every video with one child that answers file by file;
a file it crashes or stalls on is recorded as unknown length and a fresh
child carries on. `tests/test_video_isolation.py` replaces OpenCV's video
opener in the program's own process with a tripwire; the previous code trips
it. A 19-line standalone reproduction was delivered alongside this release.

**The gallery's filter runs on the server, and agrees with the page.**
`/api/gallery/query` (`fme/review/galleryquery.py`) answers what the gallery
shows -- chips, search, categories, flags, facets and their counts, stacks --
and `tests/test_gallery_parity.py` requires the page's own answer for 400
random filter states; a planted one-line difference fails it. The page does
not use it yet (AUDIT.md item 6, stage 3).

**A similarity rescan no longer decodes the case again.** Each scan decoded
every picture four times -- `is_featureless` decoded it again only to dhash
it -- and kept nothing, so rescanning at another distance repeated it all.
Now three decodes on the first scan and none on a rescan: hashes are kept in
case.db by content and algorithm version (`HASH_VERSION`), and are
bit-identical to the previous code's (`tests/test_similarity_kept.py`
compares against it).

**AUDIT.md measured v214 against 2026.5.6** for eighty releases. Re-measured
at this release against 2026.5.7, and its ESE, similarity and gallery items
brought up to date.

## v2026.11.293 — the frozen build starts, Windows 10 search indexes are read, similar pictures are all found

**Every packaged executable failed on launch.** `packaging/entrypoint.py`
called `fme.cli.main(sys.argv[1:])`, and `main()` takes no arguments: a
`TypeError` before anything was drawn. Nothing ran that file -- the suite,
CI and `launch.py` all start elsewhere -- so it passed everything.
`tests/test_packaging.py` now runs it as the freezer does, and fails on
the old code.

**`Windows.edb` is read.** Thumbnail caches from Windows 10 and earlier can
now be named from the search index, which was refused before. The ESE
reader is vendored (`fme/vendor/impacket_ese.py`, Apache 1.1, its required
acknowledgment in MANUAL.md section 15) and loops forever on a truncated
file, so it runs only in a child process stopped at 120 seconds
(`fme/engines/isolated.py`, new). A hang or crash is reported as "could not
be read", never as an index that named nothing. Undecodable rows are
counted and reported, and 1,000 in a row ends the read -- the code this was
adapted from skipped them with no count. Tested against a stand-in reader
and a damaged file only: VALIDATION.md 5.15, EXAMINER_TASKS.md Session G.

**`isolated.py` works in a frozen build.** The executable runs itself with
a hidden `_worker` argument, limited to the workers it names. (The video
frame decoder still runs in-process in a frozen build; moving it onto this
is a later change.)

**Similarity grouping missed most of the pairs it was asked for.** It split
each 64-bit hash into four 16-bit bands at every distance, which only
guarantees a shared band up to 3 bits apart. At the default distance of 8
it missed 68% of pairs 8 bits apart, 53% at 7, 42% at 6 and 10% at 4, and
skipped any bucket over 2,000 files without a word. It now uses distance+1
bands -- every pair within the distance shares one, by the pigeonhole
principle -- compares buckets in bulk, and skips none. Groups are now
identical to comparing every pair with every other (checked in
tests/test_similarity.py, which the old code fails). 160,000 images: 4.6 s
random, 11.6 s clustered (was 1.9 s and 5.9 s while missing pairs).

**Apple CgBI PNGs and KTX textures are shown.** A PNG from an iOS app bundle
is CgBI -- named .png, drawn black by Pillow and every browser. It is now
decoded wherever pictures are opened, so it has a thumbnail, a preview, a
perceptual hash and a report tile; the details pane asks for a rendering
rather than showing it raw. `.ktx` files (app-switcher snapshots) are now
kept by a file-system walk and rendered in a child process, since the block
decoder can crash. A file claiming more pixels than Pillow allows is refused
before anything is allocated, and decompression stops at what the header
implies. Recovered files and their hashes are never changed. ASTC and LZFSE
need `texture2ddecoder` and `pyliblzfse` (now in requirements.txt), which
were not available to test: VALIDATION.md 5.10.

**Escape in the full size viewer deselected the file instead of closing.**
The viewer is deliberately left out of "the dialog in front" so grading
keys reach the file it shows; that also left Escape nothing to close, so it
fell through to the gallery and cleared the selection, with the viewer still
open and the next grading key doing nothing. Escape now closes the viewer
and the file stays selected; grading from the viewer is unchanged
(tests/test_browser_dialog_keys.py).

**Every ribbon button is now pressed by the suite** (tests/test_ui_sweep.py):
76 buttons and controls on all seven tabs, each from a freshly loaded page
with a case open and a file selected, in Chromium. A press fails the test if
the page throws, the server answers 5xx or logs a traceback, a dialog will
not close by its own button or Escape, or an error message appears that is
not the right answer here (no hash sets loaded, no case number for C4All, no
face or content model -- none ships). It also checks that a CgBI PNG and a
KTX texture are drawn in the gallery as their pictures. "Open with..." and
"Show in folder" hand off to the desktop; the test can see that they raise
no error, not that a desktop application opened.

**`/api/files` filtered by category returned nothing.** It compared a `grade`
field the job's rows never have; gradings are in case.db. It also copied the
whole list under the job lock on every request. It is now a SQLite query
(`fme/review/fileindex.py`) joined to case.db read-only, with sort and filter
names checked against a list and counts kept until the files or gradings
change. 200,000 files: pages of 500 in 120-180 ms. The gallery does not call
it yet (AUDIT.md item 6).

## v2026.11.292 — a run is checked against the hash store, and the gallery shows what it classified

Reported on case DC25-320: a hash store of 367 million hashes -- earlier
cases' gradings, Project VIC, a VICS CaseDB, the NSRL -- and nothing in the
gallery classified. Pressing Case Data > Check this run against the store
reported 1,007 in DC25-320, 444 in the NSRL, 413 and 451 in the VIC sets.

**The check ran only when somebody pressed it.** Nothing checked a finished
run; a case nobody pressed the button for showed its known files as never
seen. A run that completes in the program is now checked against the store
by itself, when there is a store; an absent or unreachable store is passed
over and the run stands. A command-line run consults no store, as before:
open its output as a case and press the button.

**When it was pressed, the gallery never showed what it did.** The check
wrote each category to the case -- correctly: a set's category, the most
severe where several assert one, Non-Pertinent for an NSRL-only file, never
over an examiner's grading, recorded as set by the set. The gallery draws
categories from the case's decisions and never read them again, so every
file it classified still showed as uncategorized, beneath a line saying they
had been categorized. Both checks (the store and the loaded hash sets) now
read the case again when they finish.

**Its counts were locations, not files.** Every duplicate location was looked
up and counted again: a picture found in three places was "3 in DC25-320",
and a case with 242,815 duplicate locations made as many needless queries of
a 367-million-row store. Each file is now looked up once and counted once;
every location is still listed in hash_store_matches.csv, and the page says
"N file(s) (found in M places)". The shared-store client also counted a file
twice when its MD5 and SHA-256 were both in one set; matches are merged per
set, as the local store already did.

**The manual said "Nothing is classified automatically. No category is ever
assigned by the program."** That was not so: the check has assigned hash-set
categories to ungraded files since it was written. It now says what happens,
when, and that nothing but a hash set ever assigns a category.

`tests/test_store_classifies.py`.

## v2026.11.291 — what the lab's sessions A to E found in the test tools

Sessions A to E were run on v2026.11.290: Windows (verify, validate, a 10 TB
drive), two real E01s through libewf (one of 289 segments, 465.8 GiB: the
size libewf read equals FTK Imager's recorded sector count to the byte), WSL
(FAT, NTFS, exFAT, The Sleuth Kit agreeing 3 of 3), the Mac, and the
interface. All four fixes below are to the tools that test the program, not
to the program.

**make_mac_deleted.sh stopped on every Mac at "could not attach the image".**
It read the mount point from what `hdiutil attach -quiet` printed, and
-quiet prints nothing. It reads hdiutil's plist answer now, takes the entity
with a mount point (an APFS attach lists the container first), and shows
hdiutil's own message when an attach really fails.

**Every browser test on Windows said "Chromium will not launch here".**
`tests/_sandbox.py` moves %LOCALAPPDATA% to a temporary folder, and that is
where Playwright looks for Chromium on Windows. Playwright's real folder is
now pinned first through its own PLAYWRIGHT_BROWSERS_PATH, unless one is
already set. The browser tests had never run on Windows before this; any
earlier Windows result naming them as passed was them standing aside.

**The exFAT long-name check passed on a word.** It wanted "long" in the
recovered name. It now wants the whole name: the program gave back "a rather
long file name for this deleted picture.jpg" -- the name on disk, spaces and
all -- and the .intended file writes spaces as underscores because its
columns are split on whitespace, so the two are compared in that form.

**HFS+: neither deleted picture came back** (Session D), while both APFS
ones did. Not yet explained. `check_deleted_bytes.py` (sent separately)
makes the pictures again from the script's formula and looks for their bytes
in the image: absent means macOS released them before the image was taken,
present means the program missed them. Recorded here as open.

## v2026.11.290 — deleted exFAT files keep their names, and The Sleuth Kit cross-check checks something

From the lab's Session C, test_known_answer.py under WSL on v2026.11.288,
with the NTFS and exFAT fixtures built and sleuthkit's fls and icat installed.

**Deleted exFAT files came back without their names.** exFAT keeps a deleted
file's whole entry set: the name across as many 15-character name entries as
it needs, the first cluster and the length. The vendored reader lists those
(`deleted_files()`) and reads them (`read_deleted()`); nothing in the engine
asked it -- the deleted-entry pass read FAT12, 16 and 32 only. Without pytsk3,
every deleted exFAT file was recovered by carving, unnamed. The fat-deleted
phase now also runs `run_exfat_deleted_recovery`: each file under its full
name, in the folder it was deleted from, marked `exfat-deleted`, with a log
line for any it could not recover and why. `tests/test_exfat_deleted_names.py`
builds an exFAT volume from the specification (no mkfs.exfat, no mount) with
a 52-character name in four name entries crossing a directory cluster, and a
file deleted inside a folder.

**Without pytsk3, no exFAT, HFS+, APFS or ext file was walked -- live ones
included.** `run_vendor_fs` asked `extra_kinds()` whether the image held
anything to read, and that answered for the extra kinds alone (QNX, F2FS, the
flash file systems), ignoring the mainstream kinds the walk is meant to read
when TSK is absent. On an image holding only those, the answer was "nothing"
and the walk was never reached: every live file came back from carving. The
question is now asked with the same kinds the walk reads.

**The summary did not say what deleted entries gave back.** FAT and exFAT
deleted-entry recoveries were counted and printed nowhere, so a run whose
files all came back that way read "File system engine: 0 ... Carving engine:
0". Each now has its own line.

**The Sleuth Kit cross-check compared nothing ("0 agreed, 0 differed").**
fat-deleted.img was FAT12, not FAT32: the specification decides the type by
the number of clusters alone, and 16 MiB in 4 KiB clusters is 4,083 -- under
the 4,085 that makes FAT16, let alone FAT32. The boot sector said FAT32; this
program's reader believes the boot sector, The Sleuth Kit believes the
specification, read a FAT12 volume with no root directory and listed nothing.
The fixture is now 34 MiB in 512-byte clusters (68,526, FAT32 by any reader),
its files are laid out from their sizes, and the generator refuses to write a
volume under the FAT32 minimum. **Rebuild it** with
`python3 tools/make_fixtures/make_fat_deleted.py tests/fixtures/`.
This program reading such a volume as FAT32 is kept: mkfs.fat -F 32 writes
volumes that small when asked to, and evidence is read as it declares itself.

**The cross-check skipped every deleted file.** It read only fls lines of the
form "r/r 4:", so "r/r * 6:" -- a deleted file -- was never compared: The
Sleuth Kit checked the one live file and none of the two it exists to test.
Both are read now (and "(realloc)" inodes), all three must agree, and when
fls lists nothing the check says why, with fls's own message.

## v2026.11.289 — an E01 libewf reads nothing from is no longer carved as a blank drive

From the lab's runs of v2026.11.288 (Session A: verify, validate, a 10 TB
physical drive opened; Session B: a real E01 carved through libewf).

**libewf could open an E01, report its size and read only zeros, and the run
carved nothing without a word.** `_open_ewf` fell back to the vendored reader
when libewf refused a file or reported no media, not when it opened one and
returned zeros -- which is what it does with the E01 this project builds as a
fixture. The run said "read by libewf", carved 0 files and stopped: the
result a blank drive gives, so nothing an examiner could see was wrong. Now
64 KiB at the start, middle and end are read; if all of it is zeros, the
vendored reader reads the same places, and if it finds data the run uses it
and logs a warning naming both. Zeros through both is a blank drive and stays
with libewf. A normal E01 costs three small reads and never opens the second
reader. `tests/test_ewf_reads_nothing.py` stands in for libewf (reading
zeros, reading the media, refusing the file), so it runs on any machine.

**test_known_answer.py failed on every validation run on a machine with
libewf.** It read its E01 once, by whichever reader the engine chose; with
libewf installed that was libewf, which read nothing, so all four pictures
failed. It now reads the fixture by the vendored reader alone (FME_NO_LIBEWF,
like FME_NO_TSK) and holds it to every picture, then, where libewf is
installed, as a case would and says which reader produced them -- recording
that the fixture proves the fall-back, not libewf. libewf is proved by a real
acquisition; the lab's Session B carve of one is that.

**test_carve_worker_stall.py failed now and then, over noise, not the stall.**
It carved 160 MiB of fresh os.urandom each run and wanted exactly 10 files;
random data carves a stray picture often enough that one run in several came
out 11. The noise is seeded now, and the 10 planted pictures are found by
hash rather than counted.

**Random data carved as DDS and EXR.** Carving six 160 MiB runs of pure noise
gave three "pictures": an EXR, a DDS and an ART. All three were matched on
four magic bytes with no structure checked (`generic`). DDS now needs the two
header sizes its format fixes (124 and 32) and EXR a version of 2, no
undefined flag bits and a `channels` attribute; noise meets neither. The ART
signature (AOL Johnson-Grace) is unchanged: without a published header layout
to check against, a check would be invented. The same six runs now carve one
ART and nothing else.

**validate.py showed what was skipped and not what failed.** A failed test
printed FAILED over three "not checked" lines; the failing checks were only in
the record's tail. They are now printed (up to five) and listed in the record.

## v2026.11.288 — a name that is not UTF-8 no longer ends the run

**One file with a non-UTF-8 name ended the whole extraction.** A folder from
another machine holds names in that machine's encoding: a Windows-1252 "café"
is the byte E9, which Python hands back as a lone surrogate. `store()` kept
the record in memory and then wrote its manifest.csv row, which refused it;
the file was recovered without a row, and at the end `close()` rewrote the
manifest from memory, met the same name and failed. The run exited 2 with no
manifest.json -- the files beside it lost to the case as well. Every text
field of a record is now made writable as it is stored (`writable_text`):
each undecodable byte is written as `\xNN`, as `ls -b` shows it, so the name
is legible and its bytes recoverable; an unpaired UTF-16 surrogate, which
NTFS allows, is written as `\uNNNN`. The row is written before the record is
kept, so memory and manifest.csv cannot disagree.
`tests/test_undecodable_names.py`. The FAT and NTFS readers were not
affected: both decode names so that no such character can reach a record.

**The first Details click on a large running case parsed the whole manifest**
(7 s at 200,000 rows). The run already parses every new row for the gallery;
it now hands those records to the Details cache (`_feed_case_records`), so a
click has nothing left to read. A click that has read ahead of the run is
never added to twice. `tests/test_case_records_incremental.py`.

**A damaged manifest row stopped the live view at that row.** Any wrong-length
row made a look take nothing, which is right for a last row still being
written and wrong for a damaged one with rows after it, which never changes.
Only a wrong last row is waited for now; the others are dropped, counted and
logged. manifest.json is unaffected. `tests/test_manifest_tail_damaged.py`.

## v2026.11.287 — a shared case cannot be closed by a borrower, and keys stay in their dialog

Reported from the lab: Details answered "ProgrammingError: Cannot operate on a
closed database"; and the File ribbon's "Categories" opened Custom Signatures.

**The case handle refuses to be closed by anything that borrows it.** The
Details error is v2026.11.285's: a route closed the one handle every request
shares (removed in .286). Removing the calls fixed the code that was there;
`CaseDB.shared` fixes the next one. `_case_for` marks its handle shared, and
`close()` on a shared handle commits and leaves it open; its owner passes
`force=True`. `tests/test_shared_case_stays_open.py` checks both.

**The ribbon item is Custom Signatures, and categories can be added.** The
item named Categories opened Custom Signatures, and is now called that. The
manual sent examiners to a Categories screen to add their own categories;
there was none -- `/api/case/category` could add, rename and delete them and
nothing on the page called it. The Classification pane now has *Your own
categories*. Renaming and deleting checked a category's lock outside the
case's lock, and a rename could set an empty name; both fixed.

**A key pressed in a dialog graded the file behind it.** The grading keys
skipped text boxes and nothing else: with Settings, Report, Export, Hex or
Logo open and a button or dropdown focused, a digit graded the hidden file
(reproduced: "3" in Settings graded the selected file 3), Ctrl-Z undid the last
grading and Ctrl-A selected a page. Keys now stay in an open dialog, and
dropdowns count as fields. **Escape closes the dialog in front**, which it
did for none of them. `tests/test_browser_dialog_keys.py`.

**Output folder answered "File not found" every time.** It asked
`/api/open-file` for the empty name, and that route takes files only. With no
file named it now opens the case folder.

**Smaller.**
- A failed ribbon action said nothing: most are async, and the `try` around
  the call returned before the promise failed. They are awaited now.
- Two automatic snapshots could be taken at once (four gradings arriving
  together took four), the check read the shared connection without its lock,
  and two snapshots in one second shared a file name, the second replacing the
  first. One at a time, under the lock, each name claimed by creating it.
- Upkeep (snapshot, case list counts) ran on one clock for every open case,
  checked and set without a lock. One clock per case, under a lock.
- The tile thread called `os.nice(10)`, which on macOS lowers the whole
  process -- the server -- by ten more with every run. Linux only now, where
  it affects the thread. The frame decoder dropped priority through
  `preexec_fn`, which Python documents as unsafe in a program with threads; it
  runs under `nice -n 10` instead, and `test_screening.py` checks what is
  launched rather than reading the source for `preexec_fn`.
- A file that would not thumbnail was served as itself whatever its type, so
  an SVG or HTML file went out from `/api/thumb` as a document; only what
  `/api/file` serves inline is served now.
- Content search and Find edited copies saved their caches over one another
  when run together; merged now (`merge_derived`).

**Checked and not a fault.** Switching hash stores closes only the switching
thread's connection; a request in flight keeps its own (`HashStore.close`).

**Still open.** The first Details click on a large run still parses what has
been written so far (about 7 s at 200,000 rows); later clicks read only new
rows. A manifest row that never parses would stop the live tail at that row.
(Both closed in v2026.11.288.)

## v2026.11.286 — a case stays open after any analysis, and nothing half-done is shown as done

From a review of v2026.11.285 that pressed every ribbon item and button on a
finished case in Chromium, and timed and raced the code changed in .284-.285.

**Five analyses closed the case for everyone.** `_case_for()` hands back the
one `CaseDB` the program keeps for a case, shared by every request. Find faces,
face search, Others like this, Content Credentials and Find edited copies each
called `close()` on it when they finished. From then until the program was
restarted, grading, opening the case, the audit log, Check this case, Similar
images and Screening answered 500 "Cannot operate on a closed database". The
calls are gone; `tests/test_shared_case_stays_open.py` refuses one in
`fme/web/` and grades after the two analyses that need no model.

**A failed grading said it put files back as they were, and did not.** It
deleted each file's decision from the page, so an earlier grade, its flags and
its note vanished from view (case.db still held them). The page now restores
what each file held before.

**A late answer for the last case put its grades on the new run.** Start
cleared the grades and categories, but an answer for the previous case still
in flight could land afterwards: the last case's grades showed on this run's
files that shared a hash, and its categories stopped this run asking for its
own. Each ask is now numbered and an answer for a case the page has left is
dropped (`caseGen`, `caseFor`); the poll asks until the page holds this job's
case. `tests/test_browser_stale_case.py` delays the last case's answer and
starts a run at once: on .285 the shared file showed category 7.

**Details during a run parsed the whole manifest on every click.** .285 cached
the parse keyed on manifest.csv's mtime and size, which change between any two
clicks while a run writes; at 200,000 rows a click cost 7-11 s with the GIL
held. Each case now keeps a tail and reads only what was appended
(`_CaseRecords`). `tests/test_case_records_incremental.py`.

**Gallery tiles could be served half-written.** A tile was saved straight to
its name and `/api/thumb` serves any tile that exists, with a one-day cache;
the tile pass that runs after a run raced the page for the same tiles. A save
that failed part way left a broken tile for good. Tiles and previews are now
written beside their name and renamed into place (`save_jpeg_atomically`).
`tests/test_thumb_atomic.py`.

**Two hash checks at once could drop one's matches from the case.** Each read
the case's matches and saved its merge under separate locks; whichever saved
second erased the other's from case.db -- and from every report -- while the
page still showed them. `CaseDB.merge_derived` reads, merges and saves under
one lock. `tests/test_derived_merge.py`.

**Smaller.** Find similar images wrote its errors to the Hashes tab, so on the
Similar tab a failure showed nothing; Match stills to videos and Screening now
say "Run an extraction first." rather than "'Unknown job_id'". The toast sat
over the middle of the ribbon for twelve seconds and took its clicks; it is now
click-through.

**The suite left a 16 MB image in the source tree.** `test_filesystems.py` and
`test_vendor.py` looked for `media_test.img` at the project root rather than in
`tests/`, found nothing and built one there, where a release zip would pick it
up. Both now read the committed fixture. `test_filesystems.py`'s output
went to a fixed `/tmp/fme_fat_deleted`, shared by concurrent runs. It now reads
the committed fixture and writes to a folder of its own, removed after.

**Not changed, noted.** The derived caches (faces, edit fingerprints, content
descriptions) still read and save separately; losing a concurrent write there
costs a recompute, not a finding. `autosnapshot` reads the shared connection
without the lock, and `HOUSEKEEPING` is one clock for every open case.

## v2026.11.285 — a run's case can be classified while the run goes on

Reported from the lab on v2026.11.284, with screenshots, all during a run:
the category bar showed "All" and none of the ten categories; the
Classification pane said the case's categories "could not be read" and
that its folder "may have moved"; and Details answered "[Errno 2] No such
file or directory: ...manifest.json" for any file clicked.

**The categories and tags were loaded only when a run ended.** The page
asked for the case (`loadCase`) when a run finished or a case was opened,
never during one -- though the server answers for a running job's case
without trouble. On a long run that was hours with no way to classify.
The page now asks as soon as the run's case exists, within seconds of
Start, and the Classification pane says it is loading rather than that
something is broken.

**Details read manifest.json, which a run writes when it ends.** It now
reads a case's records through `case_records`, from manifest.csv and
duplicates.csv while a run goes on and from manifest.json after, and keeps
the parsed result until the file changes: it used to re-read the whole
manifest -- forty megabytes, on a case the size of DC25-320 -- for every
click. The source image named in Details comes from the job, not from a
summary that does not exist yet.

**Starting a run kept the last case's grades.** They are keyed by hash, so
a file the new run shared with the last case showed the last case's
category until the run ended. A new run now starts with none.

`tests/test_browser_live_gallery.py` now checks, in Chromium and during
the run, that the ten categories and six tags are on the page, that
clicking a file opens its details, and that a file classified with a key
is saved to the case. On v2026.11.284 all four fail.

## v2026.11.284 — while a run goes on, the gallery shows every file it has found

Reported from the lab on v2026.11.283, with a screenshot: during a run the
gallery said "No files match this filter" under every chip -- All, Images,
Videos, Deleted, Carved and the rest -- until Show containers was clicked;
and the page-size choice did nothing past 500, the pager read "1-200 of
500" and the All count stayed at 500.

**Every file was taken for a container.** The live list is read from
manifest.csv as the run writes it. `_live_row` converted a few fields from
text by hand and passed the rest on as text, so `container` arrived as
`"False"`, which is true: every file was a container, and the gallery
hides containers by default. `animated` was true for every file the same
way, and a carved file's source read "offset " with no number. Rows are
now read through `_record_from_row`, the function a resume uses, so every
field has the type the Record declares and the live view cannot disagree
with the finished one.

**The page only ever had 500 files.** `/api/status` sends a running job's
newest 500 (`LIVE_WINDOW`) so that a large case is not resent whole several
times a second. That is all the page had, so every page size past 500
showed the same 500. The page now says how many it holds (`files_from`)
and is sent the files after those, 2,000 per poll (`LIVE_BATCH`), and
builds the whole list while the run goes on. Every batch is kept whether
or not that poll redraws; a new run starts its own list. Callers that do
not ask still get the window, now never taken for the whole list
(`takeFiles`).

Finished runs and reopened cases were not affected: both read
`manifest.json`, whose values are typed.

`tests/test_gui_live_gallery.py` covers the server side as the page uses
it and the CSV reading. `tests/test_browser_live_gallery.py` starts a
carve from the page in Chromium and watches the gallery during the run:
on v2026.11.283 it held 480 files and showed none at every moment; here
the tiles follow the files as they arrive (560, then 1,120 of 1,200),
past 500, with nothing thrown.

## v2026.11.283 — the Project VIC export counts files, not places, and its totals agree with its records

**A file found in several places was several Media records.** The export
appended the manifest's duplicates to its files and wrote a Media record
for each, so one picture in a camera folder, a copy and unallocated space
was three Media with one MD5. `TotalMediaFiles`, and the export's image
count, were the number of places: a case whose report said 2 files exported
"4 images". Each distinct file (by SHA-256, which the manifest deduplicates
on) is now one Media record with a `MediaFiles` entry for every place it was
found, each keeping its own path, times and deleted flag.

`TotalMediaFiles` counts Media records -- distinct files. VICS does not say
in so many words whether it counts records or places, and the reference
file cannot settle it (every record there has one place); records is what
agrees with the case's report and with what `--validate` counts. The
summary adds `places` for the other number.

**`TotalPrecategorized` was always 0**, written "by construction" before
examiner categories were exported, and wrong after: the header said none
were precategorized over records marked `IsPrecategorized`. It is now
counted from the records.

**The importer reads every place.** `vic_import` read only `MediaFiles[0]`,
so a file exported in three places came back in one. Every entry is now
read; the extra places are recorded as further places the same file was
found.

**`--validate` warns** when a VICS file from anywhere repeats an MD5 across
Media records, or carries a total that disagrees with its records.

`tests/test_vics_export_counts.py`, which also exports and re-imports.
`docs/AUDIT.md` item closed.

## v2026.11.282 — exporting files never overwrites one with another

**`export_files` said it never overwrites, and could.** A name collision
fell back once, to `<stem>_<first 8 of SHA-256>`, and the copy was then
opened with `"wb"` without looking: a third file of the same name and hash
prefix -- identical copies, or rows with no recorded hash, whose suffix was
empty -- replaced the second. Reproduced with three `IMG_0001.jpg`:
`exported_files.csv` listed three, each `hash_verified`, over a folder that
held two. Every file and thumbnail is now written to a name claimed by
creating it exclusively (`_claim`), so no two can share one, whatever the
rows hold.

How reachable this was: an export of a case this program built takes its
rows from the manifest's files, whose names carry a unique index
(`recovered_0042_...`), and no real case here produced the collision. It
took rows from elsewhere. The promise is now enforced rather than assumed.

**Thumbnails.** One `_thumbnails` folder serves every category folder, and a
thumbnail was named by its file's stem alone: two files of one name in two
categories left one thumbnail, showing whichever came last, with nothing to
say which file it belonged to. Thumbnails are claimed the same way, and the
manifest's new `thumbnail` column names each file's.

**"Verified" with nothing to verify against.** A row with no recorded hash
was written `hash_verified = True`, and the page said every copy matched.
It is now blank, counted in the result (`unverified`), and the page says
how many could not be checked.

**A copy that failed part way** was left in the export beside the good
ones; it is now removed, and reported as before.

`tests/test_export_no_overwrite.py`. `docs/AUDIT.md` item closed.

**Separately: `tests/test_fs_walk_failure.py` failed about once in eighty
runs.** Found when it failed in this release's suite run; measured at 1 in
80 on v2026.11.281, before this change, with the same signature -- two files
carved where one was planted. Its 4 MiB background was `os.urandom`, and
noise occasionally walks as AMR or MP3 frames (`docs/VALIDATION.md` §5.11).
The background is now seeded bytes, checked to hold no match, so every run
is the same run. Three other tests carve random backgrounds and check exact
counts (`test_carve_worker_stall`, `test_containers`, `test_sized_files`);
whether they are exposed was not established -- `docs/AUDIT.md`.

## v2026.11.281 — a time recorded with no zone is never given one

Engine 2.6.4. From a review of the exports, the page and what the program
does with the vendored readers' output (`docs/AUDIT.md`).

**Times the evidence recorded with no zone were given one on the way out.**
A FAT file time and an EXIF capture time are what the device's clock said,
in a zone nobody recorded. The page showed them "(as recorded)". Everything
else invented a zone:
- the report read them as UTC and converted them: a camera's 14:00 was
  printed "10:00:00 -04:00 (EDT)" under *Captured (EXIF)*;
- the Project VIC export wrote them with +00:00;
- the C4All and LAVA exports made epochs of them in the examining
  machine's own zone -- the same file exported at 14:00 New York time on
  one lab machine and 14:00 Tokyo time on another;
- the KML export put them on the time slider, which reads them as UTC.

Now a zone-less time is shown as recorded and marked *device clock; zone
not recorded* in the report, whose header says so; is written as VICS's
own "unknown" in the Project VIC export, whose summary counts them
(`times_without_zone`); gets no epoch in C4All and LAVA; and stays off the
KML time slider. One rule, in `timeutil` (`zoned`, `aware`), which
`parse` no longer bends: it used to read every zone-less time as UTC.

**Times that are UTC now say so.** NTFS (`ntfs_mft._filetime`) and an MP4's
`mvhd` were computed in UTC and stored with the zone stripped, so they
could not be told from the zone-less ones. In a case made by an earlier
version they show as *zone not recorded* until the image is run again.

**A camera that recorded its zone is believed.** EXIF's
`OffsetTimeOriginal`, `OffsetTimeDigitized` and `OffsetTime`, which most
phones write, are now read; a capture time that has one carries it and
converts like any other.

`tests/test_zoneless_times.py`; `tests/test_metadata.py` now expects the
mvhd time with its zone. The same review found two export faults not fixed
here, now `docs/AUDIT.md` items 5 and 6: the Project VIC export's
`TotalPrecategorized` and its handling of duplicates, and `export_files`
overwriting a third file of the same name.

## v2026.11.280 — Project VIC series and flags, from either kind of check; one check no longer erases the other's matches

**Checking a case against a set loaded for the session erased the store's
matches.** `/api/hashsets/scan` saved its own matches as the case's whole
list (`save_derived` replaces everything of a kind), so every match the
shared store had made -- with its MediaID, Series, Tags and flags -- was gone
from `case.db`, from the running job, and from every report written after.
The store's check had the opposite fault on a smaller scale: it updated by
file, replacing each matched file's whole list, session matches included.
Both now merge by origin (`hashsets.merge_matches`): each check replaces its
own earlier matches and keeps the other's, so re-running one neither erases
nor piles up. Matches record where they came from (`via`: store, session or
stash); ones saved before this are told apart by what each path wrote.

**A VICS file loaded for the session kept only "category N".** Its matches
had no MediaID, Series, Tags or identified/distributed flags, so the details
pane -- which already shows them for the store's matches -- had nothing to
show, and no numeric category, so auto-categorize did nothing for a VICS
file loaded that way. Only MD5 and SHA-1 were read, so an entry carrying
just a SHA-256, or its hashes in AlternativeHashes, did not match at all.
The session loader and the store now read records through one function
(`hashsets.vic_record`, `hashsets.vic_digests`); each record is held once per
entry as compact JSON, shared by that entry's hashes.

**The match CSVs carry the record.** `hash_matches.csv` and
`hash_store_matches.csv` end with `vic_media_id`, `vic_category`,
`vic_series`, `vic_tags`, `vic_victim_identified`, `vic_offender_identified`
and `vic_is_distributed`; the first nine columns are unchanged.

`tests/test_vic_match_detail.py`. `docs/AUDIT.md` item closed; the source
tool's Exif reading, which the comparison tool also shows, is still not kept.

## v2026.11.279 — a carved file the source could not give whole says it is short

Engine 2.6.3.

**A carved file that ran into an unreadable stretch was stored short,
silently, under the length it was judged to have.** `_carve_chunks` stopped
at the first empty read and said nothing. Reproduced with a 4 MiB video
whose header recorded its length and a source that gives nothing back from
6 to 7 MiB: 2 MiB was stored, the manifest row said `bounded = "header"`,
and no stretch was listed as unread. Found during the v2026.11.278 review
and logged in `docs/AUDIT.md`.

Now:
- What the source gave is stored and nothing past the gap: no bytes are
  invented, so the digest is of what was read.
- The row is marked `bounded = "short"`, a fifth value, and its note gives
  the offset where reading stopped, how much of the judged length was
  stored, and how that length had been judged. The row is written once, by
  `OutputManager.store`, which takes an `after_read` hook for this.
- The stretch goes into `unreadable_ranges`, and `carved_short` counts the
  files. The report's coverage limits say how many there are and what the
  marking means.
- The report said every unreadable stretch "was read as zeros". Only a
  device read directly zero-fills; it now says so, and that any other
  source gives nothing there.

`tests/test_carve_short_file.py`; `docs/VALIDATION.md` §5.16.

## v2026.11.278 — an outside review's findings: resume, the progress note, the GUI's hold on an image, the hash store

Engine 2.6.2. Sixteen findings from a review of the concurrency-heavy paths,
each fixed with a test that fails on v2026.11.277 and passes here.

**A cancel during the parallel carve's merge lost files on resume.** The
merge stores a finished stripe's records one at a time and checks for a
cancel before each; the progress note said the first unfinished stripe was
where a resume should start, and the records still waiting lay below it.
Reproduced with ten records and a cancel after four: the note said 1000,
the six files at 360-810 were stored by neither run. When the stripe being
merged was the last, the note said the whole image was done. The note now
records the first header nothing has stored (`resume_point`), and a cancel
in the final drain is caught too. `tests/test_carve_cancel_drain.py`.

**Resuming a case could rewrite its manifest from a partial read.**
`_load_existing` returned on any csv.Error with the rows read so far, and
the constructor then rewrote manifest.csv from those and duplicates.csv from
nothing; the files after the bad row became orphans with no offset, path or
deleted flag. Reproduced with a field over csv's 128 KiB limit: 5 rows
became 2, 1 duplicate became 0. A manifest that will not read in full is now
refused (`ManifestUnreadable`) and the case left as it was; the field limit
is raised. `tests/test_resume_unreadable_manifest.py`.

**A row torn in its last column was read as whole.** csv returns a complete
row for a line cut inside its final field, and the final field is
`bounded`: "capped" could come back "cap", or "" -- what a file a file
system sized carries. A last line without a line end is now dropped (its
file is reported as an orphan), and `bounded` must be one of the four
values this program writes.

**"Nothing was recovered" never said so on the volume it is for.** It
tested `out.counter == 0`, which counts names handed out; every engine
takes a name before store() decides, so one dropped candidate silenced the
"this is a ReFS volume, not an empty disk" warning. Now `out.records`.
`tests/test_nothing_recovered_warning.py`.

**The GUI and the accepted image.** A finished run cleared the accepted
image whatever it was, so one accepted during the run was forgotten; it
now clears only its own. `/api/release` during a run answered "the program
is no longer holding it" while the run read on -- it walked the job table
closing a `reader` key no job has ever had -- and now refuses with 409; the
Change button shows that instead of clearing the label. A Start's results
folder is decided once from its own request: it was read from the shared
`OUT_ROOT` after the request had rebound it, so two Starts could swap
folders, and a Start refused as busy had already moved later jobs'.
`tests/test_gui_active_and_root.py`.

**Healthy carve workers could be killed as stuck.** The heartbeat beat once
per scan chunk; judging one header reads up to max_carve_size without
returning to the scan. Every read in a worker now beats, and opening the
image does. A carve that ends because workers stalled no longer blames
memory. `tests/test_carve_worker_heartbeat.py`.

**The hash store.** A batch holding more than one source moved each
source's count by the rows asked for, not the rows that landed.
`remove_source` counted, deleted and moved the total in three steps, the
last a read then a write, so a contribution between them was lost from the
total; it is one transaction now, counted by the DELETE, and the total moves
in one UPDATE. A write that failed after its INSERT left its thread's
connection holding the write lock, and on the threaded servers every other
writer then got "database is locked"; write methods now roll back on
error. `close()` closed connections other threads were mid-query on; it
now closes only its own thread's and dead threads'.
`tests/test_hashstore_integrity.py` -- the remove_source race itself is
not forced by the test; it checks the counts agree.

**Smaller.**
- An E01 libewf opens and reports no media: libewf's handle is now closed
  before the vendored reader opens the same segments, and a misnamed E01
  (recognized by its header) falls back the same way a named one does --
  it had no fallback. `tests/test_ewf_fallback.py`.
- A read that came back short of its chunk is listed as unread, as an empty
  one was. A source that cannot report its size is not recorded as
  carved. `tests/test_carve_short_reads.py`.
- The gallery's live manifest view starts again when the manifest is
  replaced, not only when it shrinks. `tests/test_manifest_tail_replaced.py`.
- The progress note no longer raises through the carve on a value json
  cannot write, leaves no scratch file, and a run started straight after
  another in the same folder writes its first note at once.
  `tests/test_progress_note_errors.py`.
- `_carved_reach` and `_already_carved` agree on what a carved file is,
  including one a deleted entry later named ("carve+fat-deleted").
  `tests/test_carved_merged_resume.py`.
- A file path on another Windows drive is refused with 400, not a 500
  (`request_guard.contained`). `tests/test_paths_other_drive.py`.
- The Project VIC import closes the manifest it reads.

## v2026.11.277 — verify.py compares what was read, not how much; a FAT32 fixture The Sleuth Kit will open

**verify.py's FAT checks passed on the bug v2026.11.276 fixed.** Run on
Windows with pytsk3 and v2026.11.274, it printed "a FAT32 volume is read --
8 file(s) via pytsk3 4.15.0" and "both file-system engines recover the same
files -- TSK 8/1, fat32_core 8/1" while TSK had read nothing: the 8 were
seven nameless carved files and a deleted entry, against six named files
and two carved without TSK. Reproduced line for line with a stand-in for
pytsk3 that refuses every volume. The first check counted files from any
engine, and "via pytsk3 4.15.0" was the list of installed libraries, not
the reader; the second compared totals.

Now:
- "a FAT32 volume is read" requires the five live files by path from the
  file system engine, and names the reader that read the volume, from the
  run's own volume list.
- "both file-system engines read the same files" compares the two runs file
  by file -- the same paths, the same SHA-256 under each -- and says "not
  tested" when TSK refused the volume, since both runs then used the same
  reader and nothing was compared.
- "a volume The Sleuth Kit refuses is still read" is new: on a machine with
  pytsk3 it checks v2026.11.276's fallback with the real TSK, which this
  project's own environment cannot.
- "a second engine can read a mainstream volume" requires the five live
  files by path at their declared sizes, rather than any file.
- Fixtures are built into the run's work folder, not picked up from
  whatever `media_test.img` was lying in the project root.

**A FAT32 fixture that is FAT32 by the specification.**
`make_media_test_image.py --spec-fat32` builds the same files on 66,000
512-byte clusters (FAT32 starts at 65,525), with the FSInfo sector and
backup boot sector the boot sector points at, which the default image
leaves as zeros. About 33 MB. Checked here three ways -- the
specification's arithmetic, this program's FAT reader, and the vendored
reader, which is independent code -- all three reading the same five files.
Whether TSK reads it is for a machine with pytsk3: verify.py's two-engine
check is that test. The default image is unchanged, byte for byte, and
now says in its output that the specification calls it FAT12.

**The generator ignored its arguments.** verify.py passed `--out`; the
image went to `media_test.img` in the working directory regardless, which
is how a stray copy came to sit in the project root. `--out` and
`--spec-fat32` are now read.

## v2026.11.276 — a volume The Sleuth Kit refuses is read by this program's own FAT reader

Engine 2.6.1.

**With pytsk3 installed, a FAT volume TSK refused lost every file name.**
Seen on Windows with v2026.11.274: over the test fixture TSK logged "No
recognized file system at offset 0", the file system engine found 0
files, and the five live files came back from the carve with no name, no
path and no dates. Without pytsk3 the same image gives all five with their
paths, because only then was this program's FAT reader asked. Installing
the recommended library made the run worse.

TSK was following the FAT specification, which decides the FAT type by
cluster count alone: the fixture is formatted FAT32 with 4,000 clusters,
and FAT32 starts at 65,525. Such volumes occur on evidence -- `mkfs.fat
-F 32` makes them and some devices write them -- and this program's reader
opens them. Each volume TSK refuses now goes to it, and only those: a
volume TSK read is not read twice. The run's summary lists the refused
volumes (`tsk_refused`) and its volume list names the reader that read
each one (`FAT32 (fat32_core)`), so a report can say what read the
evidence. Reproduced here with a stand-in for pytsk3 that refuses every
volume, as TSK refused this one: v2026.11.274 gives exactly the Windows
figures (0 file system files; 7 carved, 132.6 KiB, 11 candidates), this
build 6 file system files with their paths and 2 carved.

**Both committed FAT fixtures are out of specification the same way**
(`media_test.img`, 4,000 clusters; `fat-deleted.img`, 4,083). On a machine
with pytsk3 -- the lab's, and CI's -- no fixture has ever had TSK read a FAT
volume, so the "two readers agree" cross-check on FAT has not happened
anywhere. A specification-valid FAT32 fixture is the next step; it cannot
be confirmed here, where pytsk3 is not installed.

**`test_vendor.py` passed while checking little.** Run on its own it
skipped the walk and every acquisition check when the fixture image was
absent, then printed ALL CHECKS PASS; it now builds the image. And it
accepted a run over the VHD that named one file of eight; it now requires
all five live files back with their paths. `tests/test_tsk_refuses.py`
covers the fallback, and fails on v2026.11.274.

## v2026.11.275 — the embedded scan uses the carve's judge; one build spec, and it can find the vendored readers

Engine 2.6.0.

**The embedded scan had its own copy of the length logic.** Media inside a
file -- a document, a thumbnail cache, a file the carve stored and then
looked inside -- was judged by a loop that had none of what v2026.11.274
gave the carve: no mediacarve, no cut for a guessed length, no `bounded`
label, and parser errors outside four exception types ended the scan of
that file uncounted. Measured: inside a container, a truncated photo still
claimed 52,964 bytes and hid the intact JPEG 29,000 bytes after its start.
The loop now calls `_carve_candidate` over a `BytesReader`, so a length is
decided in one place wherever it is decided; the partial is cut where the
next file starts and the JPEG is recovered. A file too small to keep is
stepped over as the carve steps over one, rather than searched inside.
Same speed (64 MiB container: 2.57 s before, 2.56 s after). Embedded
records now carry `bounded`.

**The documented build was the broken one.** BUILDING.md and the README
build with `build_exe.py`, which built from `forensic_media_extractor.spec`
at the project root. Nothing tested that spec, and it had drifted: it
carried neither `fme/web/ui/` (the interface's page -- v2026.11.273 fixed
this in the other spec only) nor `fme/vendor/` (the vendored readers, so no
E01 without libewf, no QNX or F2FS, and since v2026.11.274 no carve), nor
their licences, and it excluded NumPy while bundling OpenCV, which does not
import without it. It is removed; `build_exe.py` keeps its options and its
capability report and builds from `packaging/fme.spec`, the one spec.

**That spec could not find the vendored readers either.** It named them as
hidden imports, but `fme/vendor/` is not a package and was not on the
spec's pathex, and PyInstaller drops a hidden import it cannot find with a
warning. `fme/vendor` is now on pathex. `tests/test_packaging.py` had
passed throughout, because it checked that each name was written in the
spec, not that it could be found -- and its pattern for finding by-name
imports skipped any line with a trailing comment, which is how three of
the five are written. It now resolves every one on the spec's path,
checks the list against `packaging/build.py`'s, refuses a second spec,
and checks that NumPy is not excluded. No build was made here
(PyInstaller is not installed in this environment): the first build will
show whether anything else is missing, and `ForensicMediaExtractor
doctor` on it is the check.

**The FAT32 tool's templates, once frozen, were looked for where the build
did not put them** (`_MEIPASS/templates`, against `fme/web/templates`).
Found through `fme.resources` now, like every other shipped file.

`build_exe.py --check` failed on any machine without PyInstaller, and a
refused build blamed Flask whatever the cause; each now says what is
actually missing, including a vendored reader's licence file.

## v2026.11.274 — mediacarve judges carved lengths; real-media fixtures; pytest and CI; content search recipe 2

Engine 2.5.0.

**A truncated JPEG swallowed the files behind it.** With no end marker,
`_p_jpeg` fell back to the *last* FFD9 within 8 MiB. Measured: a photo cut
off part-way ran 63 KB over the RIFF header of an intact AVI, which was
then never reported as a video; its frames came out as twenty loose JPEGs.
On 256 MiB of noise with 20 planted copies of that layout, v2026.11.273
recovered 0 of 20 AVIs and 18 of 20 of each other file, plus 347 spurious
carves; this build recovers all 100 at exact length, with 1 spurious carve
(an AOL ART hit, which FME's own parser still judges). Throughput is
unchanged (11.6 s against 11.8 s).

**mediacarve is now the carve's judge of length** (`fme/engines/carve_judge.py`,
vendored as `fme/vendor/mediacarve.py`, MIT, Alexis Brignoni; SHA-256 in
`vendored.json`). It is called from `_carve_candidate` only, which the
serial carve, the parallel workers and the re-scans share, so there is
still one judge. Where it confirms a length, that length is used. Where the
end cannot be confirmed, FME's partial is kept, cut at the next header
mediacarve validates -- searched for after the file's own header segments,
so a truncated photo is not cut at its own EXIF thumbnail, and not at all
when the file runs off the end of the image. A JPEG partial is kept only
if its header walks to a valid start of scan: FME's parser, which resyncs
past broken segments, had accepted multi-megabyte "JPEGs" of random data.
Formats mediacarve does not read keep FME's parsers.

**The manifest says how far to trust each carved length:** a new `bounded`
column -- `header` (the file recorded its length), `parsed` (its structure
was walked to the end) or `capped` (the end was not confirmed). Empty for
files a file system or container sized. Older manifests load on resume.
`fme doctor` reports the judge.

**An MP4's own boxes were reported again as an embedded .mov.** The
embedded scan matched the parent's mdat as a QuickTime file of almost the
same size. Box signatures inside an ISO base media parent are now the
parent's.

**The JPEG fixtures were not JPEGs.** `make_jpeg` produced marker-shaped
filler with no frame header and a scan header no decoder accepts; 35 test
scripts built on it. It now returns a real 48x48 baseline JPEG, padded to
the size asked for with COM segments, which decodes at every size. The
judge correctly rejected the old stubs as not JPEGs, which is how this
was found. `known.E01` was rebuilt with `tools/make_fixtures/make_e01.py`.
`tests/test_carve_judge.py` is a known-answer test built from JPEG, PNG,
GIF, WAV, AVI and MP4 that Pillow, OpenCV and the wave module decode; run
against v2026.11.273 it fails, losing five intact files to one truncated
photo.

**Importing the web app made folders.** `fme/web/app.py` created
`extracted/`, `carved/` and `media_out/` beside the code at import, which is
how two empty folders came to be in the v2026.11.273 release zip, and why
a run given `--output` still left an empty `media_out/` behind. Each is now
made when something is first written there; free space is measured on the
nearest folder that exists.

**`test_compound_scan.py` could not fail.** It printed its findings and
exited 0 whatever they were. It now checks them.

**pytest and CI.** `tests/runner.py` runs every script in its own
interpreter (most call sys.exit at import, and some spawn workers) and
judges it by exit code and FAILURES ABOVE; lines where a script says it
did not check something are reported beside the verdict. `python -m pytest
tests` collects them through `tests/conftest.py`; `python tests/runner.py`
runs the same suite without pytest. The shared `media_test.img` is rebuilt
when its generator changes. `.github/workflows/test_cases.yml` (Linux,
Python 3.9 and 3.12) and `windows_smoke.yml` (an import sweep of every
module, `launch.py version` and `doctor`, then the suite) set
FME_TESTS_STRICT=1, so a test that cannot run for want of a library fails
there instead of passing.

**Content search, recipe 2** (the recipe of the other tool in this space, MIT, Alexis Brignoni). The whole
picture is fitted and padded rather than centre-cropped -- a 16:9 photo
lost 44% of its width -- and the description is the summary token and the
patch average, each unit length, joined, rather than all 257 tokens
averaged. Measured with DINOv2-small on 19 photographs among re-framings
and distractors: a photo shown off-centre in a wide frame reached the top
three 42% of the time before and 95% now; mean average precision 0.742 to
0.978. The score was non-monotonic (a cosine of -0.5 outranked +0.2) and is
now the cosine, floored at 0. Ranking is one matrix product. Descriptions
are kept as float16 under `content_vectors_v2` (about 2 KB a picture,
against about 15 KB as JSON); a case's recipe-1 descriptions are remade
on its next search rather than compared with new ones.

## v2026.11.273 — requirements.txt installs what the code uses; `pip install .` works

**requirements.txt had drifted from the code.** Twelve packages that
shipped code imports were missing or commented out: cryptography (C2PA
signature checks, the hash store's TLS certificate), pycryptodome
(encrypted E01/AFF, BitLocker -- the vendored readers), argon2-cffi (LUKS2
Argon2 keyslots), numpy (the fast MPEG-TS carve, which without it was
measured at 77% of a run), pillow, pillow-heif, opencv-python-headless,
py7zr, zstandard, waitress, cheroot, reportlab and pywebview. None fails a
run when absent; each quietly narrows what a run can do, which is why the
file now installs them all and says, block by block, what each adds.
pywebview is marked for Windows and macOS only: on Linux it needs GTK or
Qt, and installed without them the window fails to open.
requirements-minimal.txt is still Flask alone. requirements-dev.txt adds
the three test-only readers (python-docx, pypdf, playwright), and
pyproject's `full` extra gained numpy, pycryptodome and argon2-cffi.

**The stated minimum Python was wrong.** requirements.txt said 3.8; the
carve and the report use `shutdown(cancel_futures=True)`, which is 3.9.
It now says 3.9, as pyproject.toml does.

**`pip install .` could not run.** pyproject.toml declared
`[tool.setuptools.package-data]` twice, which is not valid TOML, so pip
refused the file -- the install the README and the manual give failed
before it began. Merged, it also now packages `fme/web/ui/` (page.html,
styles.css, app.js): neither copy of the table had, so an installed
program would have started a GUI with no page to serve. Checked by
building the wheel, installing it in a fresh environment and fetching
the page from the installed copy.

DEPLOYMENT.md, BUILDING.md and EXAMINER_TASKS.md install with
`pip install -r requirements.txt` instead of hand-typed lists that had
drifted the same way. `tests/test_requirements.py` fails if code imports
a package requirements.txt does not install, if any requirements line is
invalid, or if pyproject.toml stops parsing or stops packaging the page.

## v2026.11.272 — a review's findings: the parallel carve, bad sectors, resume, the hash store

An outside review of the code found these. Each is fixed with a test that
fails on v2026.11.271; three of the tests were run against that version
to confirm it.

**The parallel carve lost files at stripe boundaries.** A worker cannot see
a file begun in the previous stripe, so it could accept something nested
inside that file and, stepping over it, hide a real file just past the
outer file's end; the merge then correctly dropped the nested record, and
the real file was reported by nobody. Two workers lost a JPEG one worker
found. The serial carve, the workers and the merge now share one judge of
a header (`_carve_candidate`) and one scan (`_detect_range`); the merge
takes records in the order the serial scan meets their headers and, when
it drops one, re-scans what that record had hidden.
`tests/test_parallel_merge.py` compares worker counts against one.

**An unreadable sector ended the carve, and the run said it was complete.**
`DeviceReader.read` returned nothing for the whole request on an error,
logged at debug, and the carve took an empty read as the end of the image.
Reads now retry and zero-fill only the bad sectors, the carve moves past
an empty read, and the stretches are in the summary and the report
(`tests/test_bad_sectors.py`, VALIDATION §5.16).

**The minimum file sizes were globals.** run_extraction reassigned them, so
concurrent GUI jobs changed each other's, and spawned workers never saw
them at all. They are per run now, in a context variable, and passed to
workers. The workers had also dropped an undersized file before stepping
over it, unlike the serial carve; both now step over it.

**The parallel carve kept nothing until every stripe was in.** A worker
exception other than a death ended the carve; giving up discarded every
stripe that had come back; a cancel kept no file; no progress note was
written. Stripes are now stored as soon as every stripe before them is
in, the note records how far that is (saved at once on a cancel), a
failed stripe is retried like a dead worker's, and giving up stores
everything found before raising. A resume no longer writes the files it
finds again as duplicates of themselves (`tests/test_carve_progressive.py`).

**Healthy workers on a slow source were killed as stuck.** The stall check
waited for a stripe to come back, and stripes were sized only by count --
32 GB each on a 2 TB image with sixteen workers. A worker now beats a
heartbeat per chunk read, only silence counts as stuck, and stripes are
capped at 1 GiB (`FME_STRIPE_MAX_BYTES`). Throttled to a slow source,
v2026.11.271 failed the run outright; this finishes it.

**Resume.** A `--no-dedupe` case lost all but the first row per digest from
its manifest on resume (files stayed on disk, unlisted). The manifest is
now rewritten before anything is appended, so a torn row or an older
version's column order cannot survive into it; a torn row is refused
rather than reloaded with blanks; temporary files and empty claimed names
left by a crash are removed, and a stored file with no row is named in the
summary (`orphaned_files`) and the report (`tests/test_resume_integrity.py`).

**The hash store shared one SQLite connection between threads.** Eight
threads adding 1,600 hashes left it reporting 6,032, and concurrent use
raised `InterfaceError`. Each thread has its own connection now, and
connections of finished threads are closed (`tests/test_hashstore_threads.py`).

**Split images.** A missing segment, a set opened from a later segment, and
`.aa` sets are refused or read correctly instead of read wrongly; `.ufd`
fallback chunks are joined in number order (`tests/test_split_segments.py`).

**Smaller things.** A file whose size a file system recorded is no longer
dropped by the size floor, and drops are counted (`undersized_dropped`).
Evidence is served with `nosniff` and a sandbox policy, and anything the
gallery cannot draw is a download (`tests/test_evidence_headers.py`). The
hash store's admin forms refuse a post from another site, and its cookie
is SameSite=Strict (`tests/test_hashserver_forms.py`). The progress note
is updated under one lock. A failing file-system walk is caught and
retried like every other phase (`tests/test_fs_walk_failure.py`). Devices
are sized and aligned on Windows and macOS (`tests/test_device_queries.py`;
not yet run on either -- `docs/EXAMINER_TASKS.md`).

**Corrected in the review itself:** it said a lookup during an import could
see uncommitted rows. `_write` commits on every call, so imports are
committed batch by batch already; the measured harm was the counts and
the errors.

## v2026.11.271 — releases are numbered Year.Month.Build

Releases were numbered with a single integer (v270). They are now numbered
**Year.Month.Build** -- this one is v2026.11.271: the year and month of the day it
was made, and a build counter that carries on from the integers and never
resets. `VERSION`, `fme/VERSION` and `pyproject.toml` (a valid PEP 440
version) carry the same string, and the program reports it with a `v`, as
before. Entries below this one keep the numbers they shipped with.

**The hash store's version floor compares the build counter.** It read the
first number of a client's version, which for a year-first release is the
year: every client would have passed every floor there is.
`fme.build_number()` now gives the build counter of either form --
`2026.11.271`, `v2026.11.271` or an older client's `270` -- and the floor
compares that. `--min-version` takes either form too
(`--min-version 2026.11.271` or `--min-version 271`), and its refusal names
the floor as it was given.

`docs/DEPLOYMENT.md` and `CLAUDE.md` describe the new form.
`tests/test_version_scheme.py` checks the three files agree, the newest
CHANGELOG entry is this release, `build_number` reads every form, and the
floor accepts and refuses clients of both forms correctly.

## v270 — a base record's $ATTRIBUTE_LIST decides which extension records count

v242 joined extension records to their base record by the records'
back-pointers alone, and left the attribute list itself unread (its "still
open" item). When the base record's `$ATTRIBUTE_LIST` is resident -- the
usual case -- `parse_record` now reads which records hold the file's
unnamed `$DATA` (`_data_segments`), and `_joined` uses it: a record that
still points back at the base but is no longer listed is left out, and a
listed record that is gone or reused marks the file partial, with the note
v242 gives. A non-resident list, or one that cannot be read, falls back to
the back-pointers as before.

`tests/test_ntfs_attribute_list_read.py` plants a stale record pointing at a
listed file, and a file whose listed second record has been reused.

## v269 — a deleted FAT folder is read past its first cluster

A deleted folder's chain is freed, so v240 read only its first cluster:
with 4 KiB clusters, files listed past about the 126th entry were left to
the carve, unnamed. Its clusters are usually contiguous, so the clusters
after the first are now read too (`_list_folder`) -- each only while the FAT
still marks it free and every 32-byte slot in it is a plausible directory
entry (`_looks_like_folder`), and no further than the folder's end mark or
64 clusters. The first cluster that fails either test ends the folder
there, so a cluster reused for something else adds nothing. Both
deleted-file paths use it, for deleted folders and the folders inside them.

`tests/test_fat_big_deleted_folder.py` deletes a folder of 140 pictures
that spans two clusters, then overwrites its second cluster.

## v268 — Apple sparse bundles open; unreachable container branches are gone

- **A sparse bundle is a folder by design**, and `open_image` refused every
  folder before reaching the branch that hands one to the vendored reader,
  which reads sparse bundles: that branch could never run, and a
  `.sparsebundle` was sent to the folder engine, which looks for media among
  its band files. A folder named `.sparsebundle`, or holding a `bands`
  folder, now opens as the disk it holds.
- **Branches that could never run are removed.** `.vhd`, `.vhdx`, `.qcow`,
  `.qcow2`, `.aff`, `.aff4` and `.ad1` are answered by the vendored reader
  first; later branches for them -- one saying AFF4 and AD1 "are not
  supported" -- were unreachable.
- **libewf and libvmdk are preferred, not required.** A raw-named file that
  is really an E01, and a `.vmdk`, fell over when the libyal module was
  missing; both now use the vendored reader, as an `.e01` already did.

`tests/test_sparsebundle.py` builds a sparse bundle of the FAT32 test card,
with its empty bands absent, and reads it back whole.

## v267 — the FAT32 browser's walk survives a looping folder; free space stops at the volume's end

- **`fat32_core.walk()`** recursed into every folder it found with no record
  of where it had been. On a damaged card whose folder entry points back at
  a folder above it, it went round that loop until Python's recursion limit
  stopped the FAT32 browser. It is now iterative and reads each folder once.
  The extraction walks were already guarded.
- **`FatTable.free_clusters()`** counted the FAT's padding -- entries past the
  last real cluster, written because the FAT fills whole sectors -- as free
  space, so the browser over-reported free space and anything reading those
  clusters read past the end of the volume. It now stops at
  `FatTable.cluster_limit`, the volume's own last cluster (the deleted-file
  reads had their own bound since v233).

`tests/test_fat_walk_loop.py` adds a folder pointing back at the root to the
FAT32 test card and checks both.

## v266 — LVM physical volumes and Windows dynamic disks are named, not passed over

Neither TSK nor this program opens those volume managers, so the volumes
inside them -- and any BitLocker or LUKS volume there (the v239 "still open"
item) -- were reached only by the carve, without names, and nothing said why
a run found so little. `unopened_containers` now finds them by MBR
partition type (0x42 dynamic, 0x8E LVM), GPT type GUID (LDM metadata, LDM
data, Linux LVM) and the LVM label itself (`LABELONE` / `LVM2 001`, which a
partition typed "Linux" can carry). Each is named in the inspect warnings,
in the run summary as `unopened_containers`, in the log, and in the report's
"What this report does not cover".

The volumes inside are still not opened; this says so instead of leaving it
to be guessed.

`tests/test_volume_managers.py` checks an LVM label, an MBR and a GPT dynamic
disk, an ordinary partition, the run summary and the report.

## v265 — a run that found nothing still gets a report, saying why

`report.build` refused any report with no files in it ("Nothing is in scope
for this report"). That is right for a selection that leaves out every file
of a run that found some, and wrong for a run that recovered nothing: the
runs where an explanation matters most -- a fully locked disk, a disk inside
a volume manager -- got no report at all, so the "What this report does not
cover" section of v241 could never be read for them. A run that recovered
nothing now gets a report in every format, its header saying "No files were
recovered in this run" above what the run could not read. A selection that
leaves everything out is still refused.

`tests/test_report_empty.py` runs over a locked BitLocker volume and writes
the report in all six formats.

## v264 — without TSK, volumes in MBR logical partitions are found

The partition reader used when TSK is not installed (`_table_partitions`,
v232) listed the MBR's primary entries and skipped the extended partition,
so a volume in a logical partition inside it was never opened by the
deleted-file passes or the no-TSK walk. The chain of extended boot records
is now followed (`_logical_partitions`) -- each record's first entry is a
logical partition, its second the next record -- with a guard against a
chain that loops.

`tests/test_logical_partitions.py` builds a disk with two logical FAT32
partitions, and one whose chain points back at itself.

## v263 — without TSK, the file-system walk reads every FAT partition

The no-TSK walk (`run_fat32_fallback`) opened only the first FAT32
partition the MBR listed, and labelled it `p0` whatever it was. On a card
or disk with more than one FAT partition, or a GPT disk, the live files of
the others were left to the carve, without names (the v232 "still open"
item).

The volume discovery the deleted-entry pass uses is now its own function,
`_open_fat_volumes` -- every partition at its own offset, never one volume
twice, nothing that only resembles a FAT boot sector -- and the no-TSK walk
uses it too, walking each FAT12/16/32 volume under its own partition label
(`_fat32_walk`).

`tests/test_fallback_partitions.py` builds a disk with two FAT32 partitions
and checks the walk returns both partitions' pictures, each under its own
label.

## v262 — a deleted NTFS file is read only as far as it was ever written

NTFS records how much of a file was written -- the initialized size, or
valid data length -- and Windows shows anything past it as zeros, whatever
the clusters hold: a file preallocated and part-written, a recording cut
off. The MFT pass read those clusters as the file, so it held bytes it
never had, and a hash nothing else would match. `parse_record` now reads
the initialized size, `content` reads only that far and fills the rest
with zeros, and the note says how much had been written.

A record whose run list stops before the file's size (the v234 "still
open" item) now says so in its note, and is counted as `mft_partial`, rather
than coming back short as though it were whole.

The hand-built NTFS volume in `make_ntfs_test_image.py` left the initialized
size empty, which no NTFS driver writes; it now sets it to the file's size.

`tests/test_ntfs_initialized.py` plants a file written to 4 KiB of 12 KiB
over stale bytes, and one whose run list stops at 8 KiB of 16 KiB.

## v261 — the carve looks inside carved files without the database reader

`_scan_carved` (v237) kept a condition from the code it replaced: it ran
only when `fme.engines.db_extract` had imported. Looking inside a carved
file needs nothing from the database reader, so an install where that
module failed to load found no embedded media in carved files, and said
nothing. The condition is removed.

`tests/test_embedded_without_db.py` carves a JPEG holding another JPEG with
`db_extract` absent.

## v260 — a carve worker that is alive but stuck does not stall the run

v243 made the parallel carve survive a worker that dies. A worker that
stays alive inside a library call that never returns was not noticed: no
stripe came back and the run waited. Past `STRIPE_STALL_SECONDS` (30
minutes by default; `FME_STRIPE_STALL_SECONDS` sets it) with no stripe
back, the workers are now dealt with as dead ones are -- the outstanding
stripes go to a fresh pool with half as many workers -- and the summary
counts it as `carve_worker_stalls`. Workers are now ended with SIGKILL,
which a stopped or stuck process cannot ignore.

`tests/test_carve_worker_stall.py` stops one worker with SIGSTOP part way
through a carve and checks the run finishes with every file.

## v259 — every image reader's read() runs under a lock

A read is a seek and then a read on one open file. Only the vendored
reader locked the pair; the raw, segmented, device, libyal and source
readers did not, so two threads reading one reader could each seek before
the other read and get each other's bytes. Nothing in the program shares a
reader between threads today -- six threads hammering a raw and a split
image on v258 did not show it -- which is when a lock is cheap to add and
easy to forget later.

`ImageReader.__init_subclass__` now wraps the `read()` of every reader class
in a per-reader re-entrant lock, so a reader written later is covered
without anyone remembering to.

`tests/test_reader_threads.py` reads a raw and a split image from six
threads and checks every reader class is wrapped.

## v258 — two writers storing into one folder never overwrite each other's files

`OutputManager.store` checked that a name was free, then renamed the
finished file onto it afterwards. `os.replace` overwrites without a word,
so a second writer taking the same name in between replaced the first
writer's file -- a recovered file silently gone, its manifest row pointing
at another file's bytes. Its temporary name, built from the process id and
the record count, could also be the same for two writers in one process.
Measured on v256: two writers storing 40 files each under the same names
into one folder kept 62 of the 80.

A name is now claimed by creating it exclusively (`open(path, "xb")`),
then filled by the rename; temporary names are unique per call; and a
claimed name whose rename fails is removed.

`tests/test_store_names.py` runs the two writers and checks every file holds
what was stored under its name.

## v257 — each case's progress note is throttled on its own

`_save_progress` wrote at most once every few seconds across the whole
process, through one module-wide clock. With two GUI jobs running side by
side, one job's save held back the other's, so a crash could cost the
second far more than the few seconds the throttle is meant to risk. It is
now kept per case folder, under a lock.

`tests/test_progress_throttle.py` saves two cases a moment apart.

## v256 — hash-server accounts: no silent overwrite, safe under load; times carry their zone

- **Adding a name already in use replaced that person's account without a
  word.** Typing a colleague's name into Add gave them a new role and a new
  token, signing them out; a disabled account got a new token and stayed
  disabled, so nobody could use it. `Accounts.add` now refuses a name in
  use; the People page offers "Re-issue" instead, and the command line takes
  `--reissue`. A re-issue sets the role, issues a new token (the old one
  stops working) and enables the account, and the access log records it as
  `reissue-user`.
- **Sign-ins read the shared database connection without its lock.** Every
  write took the lock and `identify()`, `users()` and `usage()` did not, so a
  sign-in alongside a write could fail outright and sign someone out at
  random. All reads now take it.
- **Times were local with no zone** in the hash server's access log, the
  case database and the case registry -- ambiguous for the hour the clocks go
  back, in a record of who decided or looked up what, and when. They are now
  local time with the offset written in (`2026-11-03T14:05:00-05:00`), so
  they still read as the examiner's clock. Older rows without a zone are
  read as local time, so the access log's figures and a case's snapshot
  timer work across the change.

`tests/test_hash_accounts.py` covers the refusal, the re-issue, six threads
signing in while two write, the offsets, and a log and a case mixing older
zoneless times with new ones.

## v255 — the FAT32 browser serves only files from its own output folders

`/api/download?dir=carved&job=...` put the job id into the path unchecked
and then checked the file against that same folder. **A job id of
`../elsewhere` moved the folder, the check passed, and any file on the
machine could be fetched by name.** The check was also a string-prefix
comparison, under which `/out/case1` contains `/out/case10`. Measured on
v254: `job=../elsewhere&file=secret.txt` returned the file.

A job id must now be 32 hex characters, as the program makes them, and the
file must sit directly in its job's folder under the browser's own output
root, compared by path component (`os.path.commonpath`). The request guard
of v238 already kept other websites from asking; this closes it for the
page itself.

`tests/test_fat32_download.py` asks for a real carved file and for the
traversals.

## v254 — no engine function reads a name it does not have

`_run_folder_extraction` called `vendor_fs.describe(image_path)`, and
`image_path` is not one of its names. The NameError was swallowed by the
`try` around it, so the block never did anything. It is removed: the
vendored reader's account of an unreadable volume has no meaning for a
folder, which has its own explanation (the acquisition it holds).

`OutputManager.adopt()` -- left from when parallel workers wrote carved files
themselves, uncalled since, and broken by a NameError on `digest` -- is
deleted.

`tests/test_free_names.py` reads the engine, the GUI, the request guard and
the report writer and lists every name a function reads that neither it, an
enclosing function, its module nor Python defines. It found those two and
nothing else.

## v253 — a carve that stops early, or a pass that fails, says so in the report

Both carves stop when they have carved more bytes than the image holds,
which only false matches produce. They said so in the log alone: the run
finished, the carve was recorded as done, and the report read as a whole
run over the whole image.

A stopped carve now records why and where in the summary
(`carve_incomplete`), is listed in `failed_phases` and is not recorded as
done, so `--resume` carves again. The report's "What this report does not
cover" section (v241) now names it, and names any deleted-file or
extra-file-system pass that stopped with an error (v245's
`failed_phases`), so a report never reads as complete when part of the run
was not.

`tests/test_carve_stopped.py` forces the stop during a real run and checks
the summary, the progress note and the HTML report.

## v252 — manifest.csv agrees with manifest.json, and both are written whole

A file's row in `manifest.csv` is written when the file is first stored,
so a crash keeps what was found. What is learned about the file afterwards
-- another place it was found, its instance count, `also_seen_at`, a
carved file matched to a deleted entry -- reached only `manifest.json`. A
resume reads the CSV, so after a resume those values went back to what
they had been when the row was first written (the "still open" item of
v231).

`OutputManager.close` now rewrites `manifest.csv` from the records, so it
agrees with `manifest.json`. Both are written beside their final name,
flushed to disk and renamed into place (`_write_replacing`): a crash while
writing used to leave an empty or half-written `manifest.json`, the file
every report reads.

`tests/test_manifest_close.py` finds a file twice, closes, resumes, finds it
a third time, and checks the counts in both manifests.

## v251 — a resumed carve does not cut up a file the interrupted run had carved

A resume restarts the scan a little before where the run stopped, and the
scan's record of where the last carved file ended started again at zero.
A file the interrupted run had carved, beginning before the restart point
and running past it, was therefore unknown to the resumed scan, and the
signatures nested inside it -- a picture inside a video's data -- were
carved as files of their own. A run that was never interrupted does not
produce them. Measured on v246: a 2 MiB video at 0.5 MiB holding a JPEG,
resumed at 2 MiB, came back as the video and the JPEG carved separately.

Both carves now begin a resume past the end of every carved file already in
the case that starts before the restart point (`_carved_reach`).

`tests/test_resume_straddle.py` checks the single-worker and the parallel
carve against a run left alone.

## v250 — a GUI other machines can reach needs this run's access key

Started with `--host` set to anything but loopback, the GUI served the
evidence it shows and started runs for anyone who could reach the port.
v238 limited the names it answers to, but there was still no sign-in.

Bound like that, it now makes a random access key at start-up
(`request_guard.require_key`) and refuses every request without it -- reads
included. The address it prints carries the key once
(`http://127.0.0.1:PORT/?key=...`); the reply sets it as an HttpOnly,
SameSite=Strict cookie, so the page then works as usual. A script can send
it as `X-FME-Key`. Keys are compared in constant time. Bound to loopback,
as by default, nothing changes. Bound to `0.0.0.0`, the printed address is
`127.0.0.1` rather than `0.0.0.0`, which is not a name a browser asks by.

`tests/test_access_key.py` checks requests with no key, a wrong key, the
printed address, the cookie and the header in-process, then starts the GUI
with `--host 0.0.0.0` and checks the same over the network.

## v249 — the GUI tests no longer write into the source tree

The GUI keeps its case list (`cases.db`), hash store and `media_out/`
beside the program, and its settings in the user's own settings folder.
Run from the source tree, the GUI tests left case databases and case
folders among the code -- 107 of them once made it into a release zip --
and wrote into the settings of whoever ran them.

`FME_DATA_DIR`, when set, is where both GUIs keep that state instead: a
folder on an evidence drive, a read-only install, or a test. Every test
that starts the GUI now imports `tests/_sandbox.py` first, which points
`FME_DATA_DIR` and the settings folder at a temporary folder for the life
of the test and removes it afterwards. Running the whole suite from the
source tree leaves it as it was.

## v248 — the GUI runs one extraction of an image at a time

A second Start on the same image -- a double click was enough -- began a
second extraction of it into a second case, and whichever run finished
first released the image while the other was still reading it.
`/api/start` now refuses a second run of an image while one is running,
with a 409 saying which job holds it. The check is made before anything
changes (so a refused Start does not move the output folder) and again
where the job is recorded, under the lock, so two Starts sent at the same
moment cannot both pass. A run releases the image only if no other run is
still using it.

`tests/test_gui_one_run.py` sends two Starts at once, a third during the
run, and one after it ends.

## v247 — FAT long names are trusted only when they belong, and deleted ones come back

Each FAT long-name entry carries a checksum of the short name it belongs
to, and `parse_dir_entries` never read it. **A leftover long name was
attached to whatever short entry followed it** -- slots reused by a
short-name-only writer, as cameras are, gave a file a name it never had.
And **a deleted long name was thrown away**, so a deleted file came back
with only its short name and its first letter lost: `?HOTO_~1.JPG` for
`Photo at the park.jpg`, though deleting overwrites only the first byte of
each entry and leaves the long name's characters where they were.

A long name is now used only when its entries are whole and in order and
their checksum is the short entry's (`_long_name`, `lfn_checksum`). For a
deleted file, whose order numbers and short-name first byte are gone, the
entries are taken in the order they sit and the checksum is checked with
the first letter the long name itself gives the short name -- trying every
possible byte would match any checksum and prove nothing. A deleted file
with a long name now comes back with its whole name, first letter included.

A short name whose first byte is 0x05 is read as 0xE5 (a Shift-JIS lead
byte), as FAT intends, rather than as a control character.

`tests/test_fat_long_names.py` checks live, deleted, leftover, reused-slot,
mismatched and out-of-order long names, the 0x05 name, and the deleted-entry
pass on the FAT32 test card with a deleted long-named picture.

## v246 — the GUI's live file list costs the same at any size of case

While a run is going, the GUI refreshes its file list from `manifest.csv`
every 4 seconds, so the gallery fills as files are found. It re-read the
whole manifest each time and rebuilt every entry -- on the extraction
thread, so the carve stopped while it did, and for longer with every file
found. It read everything even when nothing new had been written, since
it counted the new rows only after reading them all. Measured on v245:

| Files in the case | One refresh | Share of the run spent refreshing |
| --- | --- | --- |
| 25,000 | 0.51 s | 11% |
| 100,000 | 1.49 s | 27% |
| 200,000 | 3.02 s | 43% |

`_ManifestTail` now keeps its place in the file and reads only the rows
written since the last look; the GUI converts only those and adds them to
the list. Only complete lines are taken, and a row caught half-written --
including a quoted field whose line break has arrived but not the rest --
is left for the next look. A manifest written afresh is read afresh. With
200,000 files in the case, 500 new rows are read in about 15 ms.

`tests/test_gui_refresh.py` writes a manifest a few rows at a time,
including awkward and half-written rows, checks every row comes back once
and exactly as written, and times a refresh at 25,000 and 200,000 files.

**Still open.** A row that never parses into the manifest's columns would
stop the live list there; the list at the end of the run comes from the run
itself and is unaffected.

## v245 — a resume retries what failed, and the deleted-file passes survive the GUI

### The deleted-file passes gave up early in the GUI

The MFT, deleted-FAT and extra-file-system passes reported progress as
`progress_cb(0, 0, out.count, ...)`, and `OutputManager` has no `count`.
The command line passes no callback, so every test and every command-line
run was fine. **The GUI always passes one**, and there the call raised the
first time it was reached: on the first file that came back a duplicate
while nothing new had been stored -- the usual case, since the file-system
walk recovers some deleted files before these passes run -- or on the 25th
file. `run_extraction` caught it as "recovery stopped", so in the GUI each
pass gave up after at most a handful of files, and the run finished as if
nothing were wrong. Measured on v244: the MFT pass, given a progress
callback and a case already holding one of its files, raised on its first
record. It now reports `len(out.records)`.

This was found while testing the fix below, not in the earlier reviews, and
it reaches back before v230. **GUI cases with deleted NTFS or FAT files are
worth re-running.**

### A resume retries what failed

- **A phase is recorded as done only when all of it worked.** The
  file-system phase logged the extra reader's failure (F2FS, QNX, the flash
  file systems) and recorded itself finished anyway, so a resume skipped it
  and that partition was never read again. The extra reader is now its own
  phase, `other-filesystems`; it, `mft` and `fat-deleted` are recorded only
  on success and skipped on resume only once recorded, and the carve is
  recorded too. A run's summary lists what failed as `failed_phases`.
- **A finished run leaves no progress note.** `clear_progress()` existed
  and nothing called it, so `run_state.json` outlived every finished run and
  a later `--resume` skipped what the stale note said was done. It is now
  removed when a run finishes with nothing failed; kept, with a warning
  saying what `--resume` will retry, when a phase failed.

A note written by v244 marked the whole file-system phase done; resuming
from one now runs the extra reader it skipped.

### The suite no longer fills the temp folder

`test_resume.py` left its working folder -- a 320 MiB image and several
carves of it, about 640 MB -- behind on every run. It removes it now.

`tests/test_progress_callback.py` runs the MFT and deleted-FAT passes with a
progress callback over a case already holding one of their files.
`tests/test_resume_phases.py` fails the extra reader, stops a run during the
carve, resumes it, and checks clean and partly failed runs and a v244 note.
Both fail on v244.

## v244 — FAT12 and FAT16 volumes are read, deleted files included

`fat32_core.parse_boot_sector` refused any boot sector whose 16-bit FAT
size was set -- which is how FAT12 and FAT16 say what they are. The
deleted-entry pass treated the refusal as "not FAT, or too damaged" and
logged it at debug level only, and the no-TSK walk could not read the
volume at all. **Deleted files on every card of 2 GB and under** -- older
cameras, dashcams, trail cameras, voice recorders -- came back, if at all,
as unnamed carved files. Measured on v243 with a hand-built FAT16 card
holding one deleted JPEG: 0 FAT volumes examined, and one unnamed carve.

FAT12 and FAT16 keep deleted entries exactly as FAT32 does. What differs
is now handled in `fat32_core`:

- **The type** is FAT32 when the 16-bit FAT size is zero, as before (small
  FAT32 volumes exist); otherwise FAT12 or FAT16 by cluster count, as the
  specification says. `BootSector.fat_type` carries it, and the label and
  file-system name are read from where FAT12/16 keep them.
- **The root folder** is a fixed region after the FATs; `list_dir(0)`
  reads it, and the data area starts after it.
- **FAT entries** of 16 and 12 bits are widened to FAT32's values on
  loading, so following chains, the free-cluster checks of v233 and the
  deleted-folder checks of v240 work unchanged.
- **Partition types** 0x01, 0x04 and 0x06 are recognised beside FAT32's.
- **A boot sector with no FAT, no reserved area or no size** -- NTFS,
  exFAT -- is refused by the parser itself now, not only by its callers.

Recovered files record `FAT12`, `FAT16` or `FAT32` as their file system,
where the deleted-entry pass said only `FAT` and the no-TSK walk always
said `FAT32`.

`tests/test_fat16.py` builds a FAT16 card and a FAT12 card by hand -- a
deleted picture in the root, a live folder with a live and a deleted
picture, a deleted folder with a picture -- and checks both deleted-file
paths, the same card as a partition on a disk, and a full run.

**Not checked with TSK.** `pytsk3` could not be installed here (no
network), so the run with TSK was not seen; the test runs it wherever
`pytsk3` is installed. The deleted-entry pass runs whether or not TSK is
installed, so the deleted files are recovered by name either way; what the
TSK run would show is whether TSK's own walk agrees.

## v243 — the parallel carve survives a worker dying, and Cancel always works

The parallel carve ran its stripes on a `multiprocessing.Pool`. A pool
whose worker is killed outright -- by the operating system when memory
runs out, or by a crash in a C library such as libewf on a damaged segment
-- replaces the worker and never hands its stripe out again, so
`imap_unordered` waited for it forever. Cancel was checked only when a
stripe came back, so it could not stop the run, and the parallel carve
stores nothing until every stripe is in: the examiner lost the run, and
every hour of it.

Measured on v242: a 256 MB carve that takes 14 s was still running 90 s
after one worker was killed, and 30 s after Cancel.

`_carve_stripes` now runs the stripes on a `ProcessPoolExecutor`, which
notices a dead worker and says so. The stripes still outstanding go to a
fresh pool with half as many workers -- memory being the usual reason a
worker dies -- up to `WORKER_RETRIES` (3) times, after which the run stops
with an error that says a worker died and suggests `--workers 1`. Cancel is
checked twice a second whether or not anything has come back, and a cancel
or an error ends every worker. Deaths are counted in the summary as
`carve_worker_deaths`.

`tests/test_carve_worker_death.py` kills a worker part way through a carve
and checks the run finishes with the same files as one left alone; checks a
run that keeps losing workers stops with that error; and checks Cancel
stops a carve within seconds and leaves no worker running. On v242 the run
with a killed worker never finished.

**Still open.** A worker that is alive but stuck -- a C library that never
returns -- is not detected; only one that dies is. Cancel does stop it.

## v242 — a deleted NTFS file split across MFT records comes back whole

When a file's run list outgrows its 1 KiB MFT record -- a heavily
fragmented file, most often a large video on a busy drive -- NTFS keeps
the name, the dates and an `$ATTRIBUTE_LIST` in the base record, and moves
the `$DATA` runs out to extension records that point back to it.
`ntfs_mft` judged every record on its own and never read that pointer:

- **The named base record was dropped**, having no `$DATA` of its own.
- **The first extension came back as the whole file**: no name, no folder,
  no dates, and only its own stretch of runs, so it was cut off where the
  next extension took over, with a hash that was not the file's.
- **Later extensions were dropped**, their data not starting with a header.

Measured on v241 with a deleted 41,604-byte MP4 in two fragments: one
nameless, undated 20,480-byte file, and nothing named `trip.mp4`.

`parse_record` now reads each record's base reference, whether it holds an
`$ATTRIBUTE_LIST`, and the starting VCN of its `$DATA`. `deleted_files`
gathers extension records under their base and joins them in file order
(`_joined`), and never yields an extension on its own. An extension counts
only if it is itself free and still names the base by the base's sequence
number (or one behind, as Windows advances it on freeing -- the v235 rule).
At the first missing stretch, or a run list that stops short of the file's
size, the file is cut off there and its note says so; it is never finished
with bytes that are not its own. Joined files are counted as `mft_joined`,
cut-off ones as `mft_partial`.

`tests/test_ntfs_attribute_list.py` plants deleted videos in two fragments
with their runs in extension records -- in order, out of order, with the
first stretch in the base record, with a later record taken by a live
file, and with one naming the base by an old sequence number. On v241 none
came back by name.

**Expect large deleted NTFS videos by name** where earlier runs had
nameless fragments, or nothing.

**Still open.** The attribute list's own contents are not read: the
extension records are found by their back-pointers. A base record whose
`$ATTRIBUTE_LIST` is itself non-resident is handled the same way, which
holds while its extension records survive.

## v241 — the report says what the run could not read

Since v239 a run records a locked partition in its summary
(`encrypted_volumes`), beside a volume it could not mount
(`unreadable_volume`). Every report was built from the file list alone, so
a BitLocker laptop came out as **"All 8 file(s) recovered in this run"** --
eight files from the small system partition -- with nothing anywhere to say
the main partition was never read. The report is what goes in the case
file, to people who never saw the console or the program's warnings.

`report.coverage_limits()` turns those summary entries into plain
sentences, and every format a report is written in now carries them:

- **HTML**: a "What this report does not cover" box straight after the
  header, before any count, and the header's "Covers" line points to it.
- **Word**, the **investigation protocol** and **PDF**: the same section in
  the same place. In the PDF the lines are wrapped rather than cut at the
  page width.
- **JSON**: a `does_not_cover` list beside the files.
- **The contact sheet** (`report_html`): a warning line above the tiles.

The CSV is left as one row per file, so it still opens as a plain table.
A run from before v239, whose summary names only the kind, is still
described, as the image being encrypted.

`tests/test_report_coverage.py` runs the program over a disk with a FAT32
partition and a BitLocker partition, writes the report in every format, and
checks each names the locked partition -- and that a report of the same
disk with nothing locked says nothing of the kind. On v240 no format
mentioned it.

**Reports already written are unchanged.** Regenerating one from a case
run with v239 or later adds the section; a case run earlier recorded
nothing to add.

## v240 — files in a deleted FAT folder come back with their names

Deleting a folder on FAT marks the folder's own entry deleted and every
entry inside it too, and those inner entries usually survive intact:
name, size, first cluster, dates. Both deleted-file paths -- the
deleted-entry pass and the no-TSK file-system walk -- stepped only into
folders that were not deleted, so they never read them. **Every file in a
deleted folder was left to the carve**, which returned it as
`recovered_NNNN` with no name, no path and no dates, and the folder itself
-- which may be called "Private", or a person's name -- vanished from the
report. Deleting a whole folder is one of the commonest things a user
does.

Both paths now open a deleted folder, but only while it can be believed:
the FAT must still mark its first cluster free (`_fat_free_run`, from
v233), and the cluster must still read as that folder -- a `.` entry
pointing at its own cluster, then `..`, as every FAT32 subfolder begins.
Everything inside a deleted folder is treated as deleted, whatever its own
first byte says, and its subfolders pass the same test. Each file's note
says it was in a deleted folder, its path shows the folder's lost first
letter as `?`, and the run counts them as `fat_deleted_in_deleted_folders`.

`tests/test_fat_deleted_folders.py` deletes the test card's DCIM folder the
way FAT does and checks both paths and a full run, then takes the folder's
cluster two ways -- back in use, and free but overwritten -- and checks
nothing is credited to the folder. On v239 none of the three files came
back by name.

**The test card's folders now begin with `.` and `..`.** The hand-built
card in `make_media_test_image.py` had subfolders without them, which no
FAT32 driver writes; the check above needs them, as real cards have them.

**Expect named deleted files where earlier runs had only carved ones**,
on cards where a folder was deleted.

**Still open.** A deleted folder is read from its first cluster only --
its chain is freed, so the rest of a large folder's entries cannot be
located with certainty. A folder with more entries than one cluster holds
comes back partly; the rest are still found by the carve.

## v239 — a locked volume is flagged wherever it sits on the image

A BitLocker or LUKS header is at the start of its own volume, and both
checks looked only at the start of the evidence:

- `run_extraction` read the first 512 bytes of the file itself. On an E01
  that is the E01's own header, not the disk inside it.
- `inspect_image` read sector 0 of the disk, which on a partitioned disk
  is the partition table, not a volume.

Only a bare volume image was ever flagged. The commonest case -- an E01 of
a laptop with a BitLocker partition -- never was. The run recovered a few
files from the unencrypted system and recovery partitions and read as a
nearly empty disk, and the "nothing recovered" explanation never fired,
because something had been.

`encrypted_volumes()` reads through the image reader, so it sees the disk
an E01 holds, and checks the start of the image and the start of every
partition (`_partition_offsets`, from v232). Both checks use it. Each
locked volume is reported with its partition and offset, and the warning
for a partition says plainly that the files that come back are from the
other partitions and are not the whole disk. A run's summary now carries
`encrypted_volumes`; `encrypted` still names the first, as before.

`tests/test_encrypted_volumes.py` covers a bare BitLocker volume, an MBR
disk with a FAT32 partition and a BitLocker partition (inspected and run),
the same disk behind an E01-style header, a GPT disk with a LUKS2
partition, and an ordinary disk. On v238 only the bare volume was flagged.
The E01 case is a stand-in -- a file with an EWF header, read through a
source presenting the disk inside it -- because no E01 writer is available
here; check it once against a real E01 of a BitLocker disk.

**Still open.** The flag is in the summary and the log, and in the
inspect warnings the GUI shows; the HTML report does not mention it yet.
A LUKS volume inside LVM, or BitLocker inside a dynamic disk, is not
looked for.

## v238 — only this program's own page can drive the GUI

The GUI listens on 127.0.0.1, and any website open in the examiner's
browser can send requests there. Every POST was read with
`get_json(force=True)`, so a request marked `text/plain` -- which a
browser sends cross-site without asking the target first -- was taken as
JSON, and nothing checked where it came from. Once an image was accepted,
**one request from any website started an extraction and pointed its
output at a folder the sender chose**: measured on v237, 16 recovered
files written to that folder within seconds. Nothing checked the Host
header either, so a site that re-points its own name at 127.0.0.1 (DNS
rebinding) could read case media and listings as well.

`fme/web/request_guard.py` is installed on both Flask apps -- the media
GUI and the FAT32 browser -- and covers every route:

- **The Host must be this machine's loopback name** (`127.0.0.1`,
  `localhost`, `::1`), the address given to `--host`, or a name listed in
  `FME_ALLOWED_HOSTS`. A rebound name is refused, reads included.
- **A request that changes anything must carry `X-FME-Request: 1`.** A
  browser will not add a custom header to a cross-site request without
  asking the server first, and the server never agrees, so only the
  program's own page can send one. An `Origin`, when sent, must be this
  server's own.

Both pages add the header to every request. Refusals come back as 403 in
the usual `{ok: false, error}` shape, saying why.

`--host 0.0.0.0` no longer answers to every name: reaching the GUI from
another machine means listing that machine's name for this one in
`FME_ALLOWED_HOSTS`, and the console says so at start-up. There is still
no sign-in, and the console says that too. The hash server is a separate
program and is unchanged.

`tests/test_request_guard.py` sends what a browser would from another
website and from this program's page, to both apps. Four GUI tests now
send the header, as the page does.

**Not checked in a real browser here.** `playwright` is not installed in
the environment these were run in, so `test_browser.py` skipped its
browser checks; run it where it is installed.

## v237 — the parallel carve looks inside what it carves

After the single-worker carve stores a file, it opens it and pulls out
the media inside -- a JPEG's thumbnail, the pictures in a document. The
carve skips signatures nested inside a file on purpose, so that pass is
the only one that reaches them. The parallel carve stored its files and
never looked inside.

The GUI defaults to `default_workers()`, which is more than one on any
multi-core machine, so **most GUI runs dropped every embedded file**,
and the same image gave different files depending on the worker count.
An embedded thumbnail can be the picture as it was before it was
cropped or edited.

The look inside is now one function, `_scan_carved`, called by both
carves after each file is stored, under the same `scan_inside` and
`max_inside_size` settings.

`tests/test_parallel_embedded.py` carves an image holding a JPEG with a
second JPEG inside it, with one worker and with four, and with
`--no-scan-inside`. On v236 four workers missed the embedded picture.
The worker-count check in `test_resume.py` could not see this: it
compares only what the carve itself finds, on an image with nothing
embedded.

**Expect more embedded files from cases carved with several workers**
before this version.

## v236 — MFT recovery works on 4Kn volumes

NTFS protects each MFT record with an update sequence: the last two bytes
of every 512-byte block are swapped for a marker when the record is
written, and the reader puts them back. The blocks are 512 bytes whatever
the device's sector size -- ntfs-3g and the Linux ntfs3 driver both fix the
stride at 512. `apply_fixups` stepped by the volume's sector size instead.

On a 512-byte-sector drive the two are the same, so every test passed. On
a 4Kn drive every record was rejected, the table's own included, and MFT
recovery found no deleted files on that volume and gave no sign it had not
looked.

`apply_fixups` now steps by 512 bytes, and also requires the record's
fixup count to match its size, as ntfs-3g does -- a record claiming fewer
blocks than it has was accepted before, with its last block left
unrepaired. Its `bytes_per_sector` argument is kept so callers need not
change, and no longer decides the stride.

`tests/test_ntfs_4kn.py` checks 1,024- and 4,096-byte records at both
sector sizes, refuses a torn record and a miscounted one, and runs the MFT
pass over the hand-built NTFS volume described both ways. On v235 the 4Kn
volume returned nothing.

**Expect deleted NTFS files from 4Kn volumes** on cases run before, where
there were none.

**Not yet confirmed against a volume formatted by Windows** on a 4Kn
device. The test volume is hand-built to the format; a small VHDX with
4 KiB logical sectors, formatted by Windows, is the check still owed.

## v235 — a deleted NTFS file's path never runs through a folder it was not in

A file's record points to its folder by MFT record number and by the
sequence number that record had at the time. NTFS reuses records and
bumps the sequence number when it does, so the number alone can name a
different folder. `parse_record` dropped the sequence number from the
reference and never read the folder record's own, so `path_of` walked
through whatever folder held that record now.

A file from a deleted folder whose record had gone to a new folder was
reported as being in the new one: a specific, confident path the file
never had, in the manifest, the reports and every export, with nothing
marking it as reconstructed. On an active volume record reuse is
routine, and the check was missing at every level of the path, not just
the first.

Each record now keeps its own sequence number and its parent's, and
`path_of` checks them at every step. A folder that is not the one the
reference meant is reported as `[deleted folder]`, as one that is gone
always was -- because the original is. A deleted folder whose sequence
number is one ahead still counts as the same folder, because Windows
advances the number when it frees a record. A reference with sequence 0
carries no check.

`tests/test_ntfs_parent_reuse.py` covers a reused folder, a deleted one
with and without the advance, one reused and freed again, a live match,
a reused grandparent, and a full MFT pass over a volume with a reused
folder record. On v234 every reused case reported the new folder.

**Expect `[deleted folder]` in place of some folder names** on cases run
before. The names it replaces were folders the files were never in.

## v234 — a deleted NTFS file is recovered at its own size

Every MFT record carries a file's size twice. `$FILE_NAME` holds a copy
that Windows updates only when the name changes, so it is often stale;
`$DATA` holds the real one. Attributes are stored in type order, so
`$FILE_NAME` was always read first, and `parse_record` kept its size
whenever it was not zero:

- **A file that had grown since it was named came back cut short** -- the
  rest of the picture or video was never read.
- **A file that had shrunk came back with its slack on the end.**

Either way the recorded hash was not the file's, which is what a
Project VIC match, and anyone re-hashing the file from the image later,
depends on. Since v232 the MFT pass reaches every NTFS partition, so this
reached every deleted NTFS file held in clusters.

`parse_record` now keeps the two sizes apart and uses `$DATA`'s; the
`$FILE_NAME` copy is used only for a record with no `$DATA` size at all.
The record says which it used (`size_from`). A zero-byte `$DATA` is now
read as an empty file rather than as its allocation.

`tests/test_ntfs_sizes.py` adds records to the hand-built NTFS volume
whose two sizes disagree both ways, and one resident file with a stale
name size. On v233 the grown and shrunk files came back at the wrong size
with the wrong hash.

**Expect different sizes and hashes for some deleted NTFS files** on cases
run before. The new ones are the files' own.

**Still open.** Bytes past a file's initialized size (the valid-data
length) are read from disk as they are; Windows shows them as zeros. And a
record whose runs cover less than its size comes back short without a
note saying so.

## v233 — a deleted FAT file is never given another file's bytes

A deleted FAT entry keeps its name, first cluster and size after its
clusters are released, and anything written since may have taken them.
Both deleted-file paths read them without asking:

- **When the first cluster had been reused**, the deleted-entry pass
  followed the FAT chain from it -- and the chain now described the file
  that owned it. That file came back whole, under the deleted file's
  name, dates and a hash of content that was never its own.
- **When a cluster further in had been reused**, both the deleted-entry
  pass and the no-TSK fallback walk read straight through and mixed the
  newer file's bytes into the middle.

On a card that has been written to since the deletion -- most cards -- this
is the common case, not the rare one, and the record's own note gave no
sign of it.

Both paths now read a deleted file only as far as the FAT still marks its
clusters free (`_fat_free_run`). A file whose first cluster is in use again
is not recovered at all, and is counted as `fat_deleted_overwritten`. A
file cut off part way is kept up to the first cluster in use, counted as
`fat_deleted_partial`, and its note says which cluster stopped it and why.
Clusters past the end of the volume count as in use, so padding at the end
of the FAT no longer reads into whatever follows the volume.

`tests/test_fat_overwritten.py` checks both paths on the same volume
untouched, with the deleted file's first cluster reused, and with its
second cluster reused. On v232 every reused case failed on both paths.

**Expect fewer, and shorter, deleted FAT files on cards written to after
the deletion.** What is no longer reported was never the deleted file's.
An earlier report on such a card may credit a deleted name with content
that belongs to a live file; re-running the case with v233 shows which.

**Still open.** A deleted file whose clusters are free but were never
contiguous is still read as though they were -- the FAT no longer records
the order, so nothing can say otherwise. The note says so, as before.

## v232 — deleted-file recovery reaches every partition

The deleted-FAT and MFT passes take a list of partitions to look in, and
nothing in a real run ever supplied one, so both looked only at the start
of the image. On a whole-disk image that is the partition table:

- **MFT recovery found nothing on any partitioned disk**, because no NTFS
  volume starts at offset 0. Only a bare volume image was ever read.
- **The deleted-FAT pass read only the first FAT32 partition** the MBR
  listed, and none at all on a GPT disk.

Nothing failed and nothing warned: the run finished with no deleted files
from those volumes, which looks exactly like a disk that had none.

Both passes now find the partitions themselves when not given them --
through TSK when it is installed, as the file-system walk does, and
otherwise from the MBR or the GPT behind a protective MBR. The FAT pass
reads each volume at its own offset, does not read one volume twice when
the start of the image leads back to a partition already read, and no
longer counts an NTFS or exFAT boot sector as an empty FAT32 volume.

`tests/test_partitioned_recovery.py` builds a disk with two FAT32
partitions and one NTFS partition, as MBR and as GPT, and checks that
every volume's deleted files come back credited to their own partition.
Before this change it found one FAT volume on the MBR disk, none on the
GPT disk, and nothing from NTFS on either.

**Expect more deleted files on cases that were run before.** A
re-examination of a whole-disk image with v232 will recover NTFS and
second-partition FAT files that earlier versions never looked for.

**Still open.** Without TSK, logical partitions inside an MBR extended
partition are not listed. The live-file FAT32 fallback (`_open_fat32`,
used only when TSK is not installed) still opens only the first FAT32
partition; that is the same shape of fault in a separate path.

## v231 — a resumed case keeps what it already had

A resume reloaded only the digests of the files the earlier run had
found: enough to stop them being recovered twice, not enough to list
them. `manifest.json` is written from the files held in memory, so a
resumed case's `manifest.json` named **only the files found after the
resume**, while `manifest.csv` beside it named them all. Every report,
export and gallery reads `manifest.json`. Nothing failed and nothing
warned; the case reported less than it held.

The same gap lost a second kind of evidence. A file found again after
the resume was counted as a duplicate, but where it was found was never
written down, because the copy it duplicated was not in memory to record
it against. Duplicates found before the resume were dropped from
`manifest.json` the same way.

`_load_existing` now rebuilds the earlier rows as records, with their
types restored (csv gives back `"False"`, which is true, and text where
an offset should be a number), and reloads `duplicates.csv`. A row that
will not convert -- the half-written last line a crash leaves -- is
skipped rather than half-read. `tests/test_resume_manifest.py` checks all
of it in a couple of seconds, without an image.

**Still open.** An earlier file's `instances` and `also_seen_at` are
raised in memory after its row is written, so `manifest.csv` never had
the higher values and a resume reads back the lower ones. Fixing that
means writing those changes back to the rows, which is a separate change.

## v230 — step two: a page of files, served like theirs

`/api/files?job_id=&limit=&offset=&kind=&deleted=&q=` returns one page
and the true total, in the same shape the other tool uses:
`{total, offset, files, server_ms}`. Filters are applied where the
rows are, the limit is capped at 5,000 as theirs is, and an offset
past the end comes back to the last page rather than returning
nothing -- during a run the list grows under the examiner.

On 200,000 files: **a page of 500 in 77 KB and 2 ms**, against the
99 MB the old whole-list response cost.

**Step three is not done, and was deliberately backed out.**

The gallery's filters -- hiding known software, collapsing
containers, similar groups, flags -- are applied in the browser and
interlock. Paging through the server without moving that model too
gives a gallery that pages correctly and **filters wrongly**, which on
a case is worse than one that is merely slow: an examiner would be
looking at a page that quietly leaves files out.

The wiring was written, run, and removed. What is left is the
endpoint, tested, and a comment in `app.js` saying exactly why the
gallery does not use it yet and what has to move first.

Shipping half of it would have been the fourth avoidable fault in a
week, and this one would have hidden evidence rather than shown none.

## v229 — the gallery fills during a run

Reading the other tool's code settled what four versions of guessing
had not. Their gallery asks for one page at a time --
`LIMIT 500 OFFSET n`, filters as SQL, the total count cached because
counting is a pass over every row. Ours kept every file in the job
and shipped the whole list on every poll: **ninety-nine megabytes,
several times a second**, on a case the size of the examiner's.

Step one of three, and it uncovered the real fault.

**The live poll never filled the gallery at all.** That branch updated
the status line and polled again; `files` was only ever set when the
run finished or the examiner pressed Refresh. The gallery was not
empty because the files had not arrived -- nothing ever asked for
them. Hours of a long run showing nothing, reading as "the program is
finding nothing".

Both halves are fixed:

- a running job sends the **most recent 500** files rather than all of
  them, with `files_known` for the true total. 99 MB becomes 80 KB.
- the live poll now draws them, every three seconds rather than on
  every poll -- redrawing a gallery twice a second while sixteen
  workers carve helps nobody.

A finished job still hands over the whole list, which is what the
gallery browses once a case is open, so opening a case is untouched.

Measured on a 400 MiB carve: files appear eight seconds in, largest
response 67 KB.

Steps two and three -- a paged `/api/files`, and a gallery that asks
for the page it is showing -- are still to do, and are what make a
200,000-file case comfortable rather than merely possible.

## v228 — an empty gallery during a run: what was and was not found

The examiner reported 8,150 recovered and nothing in the gallery.
Four things were checked rather than assumed:

**Manifest rows are flushed as they are written** — the file on disk
is current, not buffered until the end.

**The job's folder and the manifest's folder are the same.** An
earlier suspicion that they differed is wrong.

**The page is given files during a run**, not only at the end. On a
400 MiB carve they arrived five seconds in. A shorter test showed
nothing only because the refresh is throttled to four seconds and the
run was over before the first one.

**An out-of-range page is already corrected** before anything is
drawn, so a held page cannot be the cause either.

So none of the four candidates is it, and **the cause is not yet
known.** What went in is one defensive change — a page index past the
end now falls back to the last page with something on it, which the
existing guard should already prevent — and one diagnostic:
`/api/status` returns **`files_known`**, the number of files the
program is holding for the page.

That single number separates the two possibilities in one step: if it
climbs while the gallery is empty, the program has the files and the
page is failing to draw them; if it stays at zero, the files are not
reaching the page at all. Guessing further without it would waste
another version, as it has twice this week.

## v227 — a saved results folder was saved and never read

Spotted by the examiner: **File → Settings** showed the central folder
they had set some time ago, and every run still went to the default
beside the program.

Settings took it and wrote it to the settings file. The next launch
restored the store path, the store token and the certificate from that
same file — and not this. It had never been read back.

Two bugs in two days about the same field, from different causes: v225
was a stale copy in the route modules, this is a setting that was
written and never loaded. The first made the field snap back while you
watched; this one let it look right and quietly do nothing, which is
worse.

Restored defensively: a folder on a drive that is not attached today
does not stop the program starting. It says which folder, why it
cannot be used, and where results will go instead.

    The saved results folder E:\Cases\results cannot be used
    (the system cannot find the path). Results will go to
    ...\media_out until it is changed in Settings.

`tests/test_settings.py` starts the program in a separate process with
a folder saved, and checks it is used — and that an unusable one is
survived and explained.

## v226 — auditing the rest of the routes split

The examiner asked what else the split broke. Three audits:

**Every route called.** 38 GET routes with no arguments and 54 POST
routes with an empty body — 92 in all. None returned a server error;
none broke on a missing name.

**Every borrowed name checked** against what media_gui actually holds,
and against the list of names media_gui *rebinds*. No stale borrows
left in the code.

**And a test for the class, not the instance.** The results-folder bug
returned **200 with the wrong value** — calling routes would never
have found it. The suite now reads media_gui for every `global`
statement, rebinds each name, and asks each route module what it sees.

That test immediately found more than the v225 fix did: the stale
copies were still sitting in every route module. v225 changed the
*references*; the copies remained, so a future edit using the bare
name would have silently picked up the old value again. The rebound
names are now excluded from the copy entirely, so a bare reference
raises NameError at once instead.

The list is derived from the code, so a value that becomes rebindable
later is caught without anyone remembering to add it.

One more thing the suite caught on the way: the import shim this
needed put a constant in each route file, which the "route files hold
routes and nothing else" rule refused. The rule was right; the
exception is named rather than the rule weakened.

## v225 — the results folder could not be changed

Reported by the examiner: load an image, scroll to **Results folder**,
type a central location, and the field snaps back to the default. It
could not be moved.

Reproduced exactly. `/api/output` accepted the new folder, called
`set_output_root`, and then answered with the **old** one — so the
page, which fills the field from the answer, put the old one back.

The cause is in how the routes were split out of `media_gui.py` in
v191 and v203. Each route module copies media_gui's namespace when it
loads. That is right for everything shared by reference -- the Flask
app, the job table -- and wrong for anything media_gui **rebinds**
afterwards: the copy keeps the value it had at import.

`OUT_ROOT` is rebound every time an examiner chooses a results folder.
So is `STORE_PATH`, and `DEFAULT_IMAGE_PATH`. All three are now read
through the module at the moment they are used, not from the copy.

Checked end to end: an examiner sets a folder, starts a run, and the
case is written inside it -- not only that the endpoint answers
correctly. A folder that cannot be written to is still refused.

An attempted fix that did not work is worth recording: a module-level
`__getattr__` proxying to media_gui. PEP 562 applies to attribute
access from outside a module, not to plain name lookups inside it, so
every route broke at import.

## v224 — a red mark that was not the program's fault

v223 added a fallback for an E01 libewf refuses. The examiner's run
shows it did not fire: libewf **accepts** the fixture this project
packs, reports a media size, and reads nothing useful out of it. No
fallback can see that at the moment a file is opened -- by the time
the run has recovered nothing, the reader is long since chosen.

So the fixture is malformed in a way the reference implementation
tolerates rather than rejects, and on a machine with libewf it proves
nothing either way. Reporting it as **FAILED** put a red mark in an
examiner's validation record about a program that reads their real
acquisitions perfectly well -- which is its own kind of wrong answer.

Where libewf is present it is now recorded as **not tested**, with
what to do instead. On a machine without libewf it stays a real
end-to-end check of the vendored reader.

Fixing the writer needs libewf to test against, which this environment
does not have, and guessing at it blind is how dead code gets written.

**Session B is now the only open question about E01**, and
`docs/EXAMINER_TASKS.md` says so where the task is described.

## v223 — one reader refusing is not the end of the evidence

The examiner's v221 run: **`test_recover.py` passes** — the Unix path
assumption was the cause, as thought. And the E01 check moved from
"not tested" to **FAILED, 0 of 4**, on a machine with libewf, while
reading 4 of 4 here without it.

The fixture is at fault, not the program: libewf is the reference
implementation and is stricter than the vendored reader, and the E01
this project packs from the format documentation is not conformant
enough for it. A real acquisition from that same lab read fine on
v180 — 79,980 files walked — so the libewf path works on what the
acquisition tools write.

**The useful part is what it exposed.** With libewf installed, an E01
went to libewf and nowhere else: if libewf refused it, the run ended
with nothing. Now the vendored reader is tried before giving up, and
the log says which reader was used. An examiner whose acquisition
libewf will not take should get whatever the other reader can give
them, and know which one gave it.

`verify.py` explains that result rather than leaving a bare FAILED,
and `docs/VALIDATION.md` says the E01 fixture is evidence about the
vendored reader only.

Still open, and now the clearest gap in this project: **nothing has
read a real acquisition as a recorded test.** That is Session B.

## v222 — validation records survived nothing

The examiner ran the protocol in the morning and the record was gone
by the afternoon. **They were written inside the program folder**, in
`docs/validation/` — the one place guaranteed to be destroyed, because
updating means replacing that folder.

A record whose whole value is that it persists, kept in the only
directory certain not to.

They now live outside the program:

- Windows: `%LOCALAPPDATA%\ForensicMediaExtractor\validation`
- Linux and macOS: `~/.local/share/forensic-media-extractor/validation`
- or wherever `FME_VALIDATION_DIR` points — a case share, or a folder
  the unit backs up.

**And `validate.py` rescues what older versions left behind.** On a
copy updated from v221 or earlier it moves any records still sitting
in `docs/` before an update can delete them, and says how many it
moved. Tested: two old records and an existing log carried across, the
log's earlier rows intact.

The log's links were also relative to the old layout and would have
pointed nowhere.

## v221 — the tests assumed Unix

The first validation run on the lab machine: **9 of 11 passed, 2
failed** — `test_known_answer.py` and `test_recover.py`. Both pass
here. The difference is the operating system.

**Thirty-five test files hardcoded `/tmp`.** On Windows that is not a
temporary folder: it is `\tmp\` on whatever drive happens to be
current, which may not exist, may not be writable, and is not where
anyone would look. Every one now uses the platform's own temporary
folder.

`tests/test_packaging.py` fails if a Unix-only path appears anywhere
in the package again.

**This is a guess until the examiner re-runs it.** The two failures
are consistent with the path assumption and I have not reproduced them
— I cannot, on this machine. The validation record written on that run
holds the tail of each failing test, which would settle it; it is at
`docs/validation/2026-11-02T132128Z-v218.md` on that machine.

Three self-inflicted faults while making the change: a bulk edit that
put `import tempfile` after its first use in five files, then after it
again in four, before placing it before the first import in all
thirty-five. The suite caught each round.

## v220 — Session A came back, and closed the oldest open question

The examiner ran `tools/verify.py` on the lab machine: **18 passed, 0
failed**, with every library present including pytsk3 4.15.0 and
libewf.

**The deleted-FAT question is answered.** Open since v164, when a run
here recovered a deleted entry and a run there did not:

    both file-system engines recover the same files
       -- TSK 8 file(s)/1 deleted, fat32_core 8/1

Two readers that share no code, the same volume, the same answer. That
is the cross-check this project could not run on its own.

**Two of the three "not run" lines were my fault, and are fixed.**

`verify.py` reported "no E01 fixture is built here" while
`tests/fixtures/known.E01` sat in the same tree — the check simply
never looked. On a machine with libewf installed, the format every
case arrives in came back as "not tested". It now reads the shipped
fixture, or builds one, and checks the pictures come back
byte-for-byte: 4 of 4.

The NTFS line now uses `tests/fixtures/ntfs-deleted.img` when it
exists, and otherwise says how to build it rather than implying that
TSK being installed is itself evidence.

Three faults of my own on the way: a boolean passed where the reporter
wanted "works", so a passing check printed `[True]`; a note appended
to a list that did not exist, which ended the run in a traceback after
all the work; and the notes themselves were collected and never
printed.

## v219 — the documents, inside the program

Ten documents shipped with this program and none could be opened from
it except the manual and the server guide. **A limitations statement
nobody opens is not a limitations statement**, and an examiner asked
in a hearing what the tool does not do should find the answer in the
tool rather than in a folder.

**File → Help** continues with: *What it does not do*
(`docs/VALIDATION.md`), *Checks to run here* (`docs/EXAMINER_TASKS.md`),
*Installing* (`docs/DEPLOYMENT.md`) and *What changed*
(`CHANGELOG.md`), alongside the manual and the store guide.

**`docs/AUDIT.md` is deliberately not offered.** It names another tool
and is a working document for whoever builds this; it has no place in
front of an examiner working a case, or a defence expert reading over
their shoulder. The suite fails if it is ever added.

The suite also checks every offered document opens and is shipped, and
the frozen-build spec now carries `docs/`, `BUILDING.md` and
`CLAUDE.md` — a build that left them behind would offer an examiner a
Help menu of error pages.

Found while wiring the test: the checks first ran **after** the
endpoint summary had already been printed, so a failure would have
been noted under the verdict rather than changing it.

## v218 — twelve functions defined twice

The crashed carve in v217 was not one mistake. `media_core.py` held
**two copies of the entire parallel-carve machinery** — twelve
definitions, the second shadowing the first. Found by looking for
duplicates across the whole package rather than by meeting another
one.

Eleven pairs were byte-identical. One was not: `_parallel_carve_body`
existed as a version that writes each stripe's files as soon as the
stripe before it finishes — so an examiner sees something during a
long carve — and as an older version that writes nothing until the
whole pass ends. **The older one was the live copy.** That is why a
long run shows an empty gallery for hours.

Both copies are gone and one remains. Two further things came out of
removing them, and the second is the uncomfortable one:

**The progressive version does not work.** Promoted to live, it
recovered **0 of 24** planted pictures. It had been dead code since it
was written, so nothing had ever run it. The working version is back
in place, with a comment saying what happened and that the improvement
needs writing again — against a test that carves with more than one
worker, which is the test that was missing when it was written.

**A run could crash at the very end**, after all the work: the v211
code that explains an unreadable volume assumed the reader always
names what it found, and `identify_fs` can return no name. Guarded.

`tests/test_packaging.py` now fails if any function or class in the
package is defined twice.

## v217 — parallel carving recovered nothing

**The most serious defect found in this project.**

An examiner ran an E01 through v180 and the gallery was empty. The
console was full of:

    TypeError: _worker_init() takes 4 positional arguments but 5 were given

`_worker_init` runs once in each carve worker to open the image and
build the signature list. The `artifacts` argument had been added to
the call **and to the body** and not to the signature, so every worker
process died the moment the pool started.

**The run did not fail.** The pool returned, the carve reported no
files, and that is indistinguishable from an image with nothing in it.
The default is sixteen workers, so this is the path nearly every real
run takes.

It went unnoticed because **every test here that exercises carving uses
one worker**, and with one worker the carve runs in-process and never
touches the initializer. Two copies of the function, both wrong.

`tests/test_resume.py` now runs the same image with one worker and with
four and requires the same files back, and fails if any worker process
crashed.

What an examiner should know: a run on a machine with more than one
worker, between the version that added `artifacts` and this one,
**carved nothing**. Files found by walking the file system and by
looking inside containers are unaffected — those are the 79,980 and
115,736 in the report — but anything that existed only in unallocated
space was not recovered. Those cases need re-running.

## v216 — the Windows Search claim, corrected and the real risk closed

v215's comparison said a truncated `Windows.edb` would hang our run.
**That was wrong.** We do not read ESE at all: we read the SQLite index
Windows 11 22H2 and later use, and a `Windows.edb` is recognised and
refused with an explanation. Checked, including one truncated to
nothing but its header.

The document is corrected. The gap is narrower than claimed and still
real: **most casework is Windows 10**, and on those exhibits a cached
thumbnail cannot be matched to a file name at all.

**One genuine risk was there and is now closed.** The SQLite index is
opened with `immutable=1`, which tells SQLite to trust the file rather
than check it — right for a read-only forensic copy, and it means a
damaged b-tree can send a query round a loop that does not end. The
read now carries a sixty-second limit through a progress handler. A
healthy index of any size is read in a fraction of that; a run that
hangs with no message is the worst way for this to fail, because an
examiner waits, kills it, and cannot say what stalled.

`tests/test_metadata.py` checks all four: ESE recognised, ESE refused,
a truncated ESE recognised, and a corrupt SQLite index returning
rather than running forever.

## v215 — the comparison, rewritten

`docs/AUDIT.md` was last rewritten at v139. Seventy-five versions on,
most of it was wrong — and it was being used to decide what to build,
which makes a wrong comparison worse than none.

Measured rather than remembered: theirs is 23,976 lines in 56 modules
with **92 test files and 40 fixtures**; ours is 31,227 in 65 with 39
test files and **4 fixtures**. That ratio is the honest gap behind
every other line in the document.

What they have that we do not, after checking module by module rather
than from memory: application artifacts, their own file-system readers
with no C extension, **offline basemaps**, **Project VIC record detail
beyond the category**, Windows Search read in an isolated worker so a
truncated database cannot hang a run, a similarity index built from
thumbnails already made, and signed builds.

What we have that they do not: C2PA signature verification, resume,
rebuilding an interrupted case, a shared hash store with accounts and
an integrity procedure, a FAT deleted-entry pass of its own, saying
when a volume cannot be read — and the validation documents, which are
the largest difference and are not a feature.

Three claims the old document made that were wrong are recorded as
wrong, including the one an examiner caught: that comparing against
The Sleuth Kit gave two independent readers, when `pytsk3` **is** The
Sleuth Kit.

## v214 — the recovery procedure, written down

v213 made `--integrity` print what to do when a store is damaged. The
examiner asked where that was written. It was not: the steps existed
only in the program's output, which means the first time anybody would
read them is the moment the store is already broken.

`SERVER_SETUP.md` Part 11 now carries the whole thing as one
procedure — **Backing it up**, **Checking it for damage**, **When it
is damaged** — in that order, because the recovery steps depend on a
backup existing and the backup instruction was five hundred lines away
in a different section.

Two things the backup section now says that it did not: keep the
**imported set files** as well, because they are what makes the store
rebuildable and a backup without them is half a backup; and `--move-to`
is safer than a file copy because it checks the result.

Two things the recovery steps now say that the program's output did
not: **check the restored copy before putting it in service**, and
**tell the other examiners from when** — a lookup that found nothing
during the damaged period found nothing because of the damage, not
because the file was unknown.

`MANUAL.md` gains a short section under the symptom an examiner would
actually meet — "the hash store stops matching things" — pointing at
the procedure.

The suite checks both documents still carry it.

## v213 — the admin page, and checking the store for damage

**The admin page reports what was asked for, with charts.** Hashes
contributed, cases fed back, lookups and active time, as inline SVG —
drawn rather than charted, because a server has no internet and a
charting library is one more thing to fail. The table gains hashes,
cases, days active, hours and the version each person's copy reported.

**"Hours used" is reported honestly.** The store sees requests, not
sessions: it knows when somebody looked a hash up, not when they
opened the program. What is shown is active time — the span of each
run of requests less than thirty minutes apart — and the page says it
is a floor, not a timesheet. An examiner grading for an hour without
touching the store is invisible to it.

**`--integrity` checks the store for damage.** A hash store is one
large SQLite file several people write to over a network on a machine
that can lose power mid-import, and when it goes wrong the symptom is
not an error: lookups quietly stop matching. The check is read-only,
reports whether the pages are sound, and when they are not says what
to do in order.

**It does not repair in place, deliberately.** A salvaged store is one
whose contents nobody can account for, and this store decides what an
examiner is shown about a file. Restoring the last good copy and
re-importing the sets is the right answer, because a reference store
is rebuildable: the sets are files you still have.

It also says what a clean result does *not* prove — structurally
intact is not the same as holding the right hashes.

## v212 — the guide, prioritised

`docs/EXAMINER_TASKS.md` had eleven tasks and no order of importance,
which makes a list easy to put off whole. It now opens with the three
worth an hour: the capability report, a real E01, and the NTFS and
exFAT fixtures. The rest are worth doing and none is urgent in the
same way, and saying so is more useful than listing them equally.

`CLAUDE.md`'s current focus updated: the three encryption and
file-system items are closed, and what is next is a
duplicate-definition sweep and the open question about the vendored
BitLocker unlocker.

## v211 — a volume that cannot be read says so

ReFS last, and the same pattern as BitLocker: before building a
fixture I ran a ReFS volume through the program to see what the
fixture would test. It reported **no files and nothing else** — the
same output as an empty disk.

The vendored reader knew perfectly well what it was looking at. It
had the version, the cluster size, and the exact reason the walk was
impossible, and none of it reached the examiner.

A run that recovers nothing now asks the reader what the volume is and
prints its answer:

    Nothing was recovered, and this is a REFS volume: that is not the
    same as an empty disk.
        version      ReFS 3.7 (boot sector)
        boot sector  its checksum does not hold
        walk         not possible: no superblock with a checksum that holds

The manifest records the file system. This covers every format the
reader recognises and cannot walk, not only ReFS.

**ReFS contents are still not read**, and `docs/VALIDATION.md` §5.14
says so: image a ReFS volume from a running Windows machine that has
it mounted.

Noted while there: `phase_done` is defined twice in `media_core.py`,
as `_stripe_tasks` was. Where there are two there are probably more.

## v210 — LUKS, told apart by version

v209 recognised LUKS but called everything "LUKS". The version matters
to the examiner who has to open it: LUKS2 headers cannot be read by
cryptsetup older than 2.0, and the two derive their keys differently.
The version is read from the header, the advice changes with it, and
the **UUID** is reported — that is how a volume is matched to a key
somebody else holds.

`tools/make_fixtures/make_luks.sh` builds the fixture on Linux or WSL,
and writes **two** images: the volume as it would be seized, and the
same volume after cryptsetup opened it. The pair is the point. The
locked one must be reported as LUKS and recover nothing; the unlocked
one must give back the pictures, whose hashes were recorded before the
volume was ever closed.

Root is needed, because mapping a LUKS device is a kernel operation.
The test branch was exercised against stand-ins before shipping, so it
does not reach the examiner unrun.

## v209 — a locked volume no longer looks like an empty one

Asked for a BitLocker fixture. Before building one, I ran a BitLocker
volume through the program to see what the fixture would be testing.

**It reported "0 file(s)" and said nothing else.** That is also what an
empty disk reports. An examiner reading that output would conclude
there was nothing on the drive, which about a locked drive is wrong in
the way that ends a case.

So the fixture was the wrong thing to build first. The program now
recognises BitLocker and LUKS from the volume's first sector, says so
in the log **before anything is carved**, records it in the manifest,
and says what to do: unlock it and image the unlocked volume.

It does not decrypt anything and the documentation says so plainly.

**Found while doing it: a vendored reader in this project can unlock
BitLocker, and nothing calls it.** The same pattern as the file-system
readers — a capability present and unreachable. Recorded in
`docs/VALIDATION.md` §5.13 as an open question rather than quietly
wired up, because taking a recovery key and opening a volume is a
larger claim to stand behind than reading one somebody else unlocked.

`tests/test_encrypted.py` checks both signatures are recognised, that
an ordinary FAT volume — whose first sector begins the same way — is
not falsely called encrypted, and that a run over a locked volume
explains itself.

`docs/EXAMINER_TASKS.md` gains the PowerShell to build a real
BitLocker VHDX and what the program should say about it.

## v208 — a debugging protocol in the handbook

From a piece on working with an AI as an engineering partner rather
than a code generator. Most of it this project already does; one part
it plainly does not.

**Five hypotheses before changing a line**, each with how to tell
whether it is true, cheapest first. The intermittent failure took nine
sessions because each session took its most plausible hypothesis,
fixed something real, and shipped — twice fixing a genuine bug that
was not the cause. The actual answer would have been on any five-item
list.

Two rules recorded with it: **instrument before fixing** (the probe
that cracked that bug did so by producing nothing, which ruled out
half the search space), and **a fix that does not change the symptom
was not the fix** — keep it if it corrects something real, say so,
and keep looking.

`CLAUDE.md` also gains a **Current focus** line, which the article is
right that a handbook needs: what the next session should pick up,
and what is still untested and why.

## v207 — an engineering handbook, and where custom signatures went

**`CLAUDE.md`** in the project root: what the program is for, the
architecture in three sentences, the entry points and how a case
flows, the conventions, and — the part worth having — the gotchas that
have actually bitten. Route modules losing a module-level constant.
The page being three files. The interface's controls existing once.
`pytsk3` being The Sleuth Kit. A format with no length field carving
one frame long.

The suite keeps it honest: every path it names must exist (fifteen
checked), and the gotchas section must not wither into a description.

**Custom signatures are not lost.** The screen, the endpoints and the
carve all work — verified end to end by defining a type the program
had never heard of and recovering it at its exact length, 1,041 bytes.

What is missing is a command-line flag: `extract` has no
`--signatures`, so only a run started from the interface can use them.
Now stated in `docs/VALIDATION.md` §5.12 rather than left to be
discovered mid-case.

## v206 — the nine-session mystery, solved

It was never concurrency.

AMR records no length anywhere; its end is found by walking frames
until one stops making sense. The test padded its image with
`os.urandom`, so the bytes after the file were being judged as a frame
— and random bytes pass that judgement **1.8% of the time**, measured
over four hundred trials. The carve takes one frame more, the hash
changes, and the test reports the file as lost.

Running ten copies at once made it look load-related. Load only meant
more rolls of the same dice.

Found by instrumenting the parser and getting **no output at all**:
`_p_amr` was never rejecting the file, so it was succeeding with a
different extent — which pointed at the padding rather than at the
reader, where I had been looking.

The test pads with zeros now, because a test has to be reproducible.
Ten copies at once: zero failures.

**The behaviour is real and is now stated** in `docs/VALIDATION.md`
§5.11: a format with no length field can carve one frame long, the
recovered hash then differs from the original, and it will not match a
hash set. AMR and SILK are affected; anything with a length field —
JPEG, PNG, MP4, most of a case — is not.

Two genuine faults were fixed on the way, in v205: twenty-seven tests
sharing fixed `/tmp` paths, and parallel carve stripes that abutted
with no overlap so a file straddling a boundary was recovered by
neither worker. That second one was evidence loss, and nothing to do
with this.

## v205 — the intermittent failure, reproduced

Nine sessions of "it failed in the batch and passed alone". It
reproduces: run `tests/test_containers.py` six times at once and about
one copy loses an audio file.

**It is not a flaky test.** The file is built from constant bytes. The
carver returned different results for identical input, which is a
different and much worse thing.

Two real faults found on the way, both fixed:

**Twenty-seven tests shared fixed `/tmp` paths** and each wipes its
directory on startup, so two overlapping runs had the second deleting
the first's working files. Each now uses a directory of its own.

**The parallel carve's stripes abutted exactly, with no overlap.** A
file whose signature header straddled a boundary belonged to no stripe
and was simply not recovered — and because the worker count follows the
machine's load, the boundaries move, so the same image could give
different results on different runs. Stripes now reach one signature
span past their end, and the duplicate that overlap can cause is
suppressed by digest as any other is.

**Neither was the cause.** The loss still reproduces. The hypothesis,
the symptom and the next diagnostic are written up in `docs/AUDIT.md`
under *OPEN: a carved file can be lost under load*, including what it
would mean for casework if confirmed: a small carved file silently
dropped on a loaded machine, with nothing in the manifest to show it.

Recorded rather than guessed at. I am not shipping a speculative change
to the carver.

## v204 — the rest of the interface, in a real browser

The harness covered opening a case, grading and the side pane. It now
also opens **every screen** — the workspace, the case list, the hash
store, custom signatures, similar media — and checks each draws
something and throws nothing; checks the six extraction options an
examiner sets at the start of every case are present, and that ticking
one **actually changes it** rather than moving a tick wired to nothing;
opens the export and Project VIC panels; and checks the hash-store
controls.

"Present in the page" was not good enough for the panels: both start
hidden, and a panel that is in the markup but never opens is a feature
nobody can reach. They are opened, and their contents counted.

Two of the control ids I wrote from memory were wrong — `engine-select`
and `store-url` do not exist; they are `mode` and `store-path`. The
test found them, which is the point, and I corrected the test rather
than loosening it.

What is still not covered, and now says so in `docs/VALIDATION.md`:
anything behind a native Browse dialog, which cannot be driven from the
page, and producing a report end to end through the interface rather
than from the engine.

## v203 — the rest of the routes

Thirty-three more routes moved out: `routes_case.py` (opening, listing
and reworking cases), `routes_media.py` (serving a file, a thumbnail, a
frame, its details) and `routes_setup.py` (signatures, the scheme,
settings, the logo, the stash, C4All).

**media_gui.py: 463,126 characters six versions ago, 75,643 now.**
Eighteen routes remain — the run itself: start, status, cancel, the job
table and the image. Those touch shared mutable state constantly and
are the ones worth leaving where that state lives.

**It broke again, in the same way wearing a different hat.** v191 swept
`CONVERTIBLE` and `THUMB_EDGE` out of sight; the guard added then looks
for `ast.Assign`. `REPORTS: dict = {}` is an `ast.AnnAssign` — a
different node — so it walked straight past, the dict left the page's
sight, and starting a report returned 500.

Both guards now catch annotated assignments: the one in the extraction
script and the one that ships in the suite. And the suite now also
checks each route module is actually **imported** by media_gui — a
build that ships a file it never imports starts fine and returns 404 on
a third of the program — and that the registered route count has not
dropped.

Caught by the endpoint test each time, not by reading the diff. That is
the argument for having it.

## v202 — every case says which build produced it

Nothing recorded it. A manifest said `tool: media_core.py` and no
version; a case processed under v150 and reopened under v201 left
nothing behind to say which did the work.

One provenance block now reaches everything: the manifest records the
version, the engine version, a SHA-256 fingerprint of the code, and the
Python and platform; the HTML, Word and PDF reports carry a **Produced
by** line taken from the manifest rather than from the program writing
the report, because those differ when a case is reopened later; and a
rebuilt manifest records the build that rebuilt it separately from the
one that made the run.

**The Griffeye export was reporting a version that has never existed.**
`SourceApplicationVersion` defaulted to the export module's own
`__version__` — "1.0.0" — so every ProjectVIC set this program has ever
written has told Griffeye something untrue about what produced it. It
now says `v202 (d342be84b426)`.

The suite checks all four outputs name the same build, so they cannot
drift apart again.

Found on the way: my own PDF check could not read reportlab's ASCII85
layer and reported the line missing when it was there. The test does it
properly now — a check that cannot see what it is checking is worse
than no check.

## v201 — a version floor, scoped deliberately

`--min-version N` on the hash server refuses **contributions, imports
and removals** from older clients. It does **not** refuse lookups.

That split is the whole design. Stopping an examiner part way through a
case because of a version number is a worse outcome than the drift it
prevents: their work is underway and the store is the same store. An
old client *writing* into a set everyone shares is different — that is
how a unit ends up with rows nobody can account for, and it can wait
until the update is done.

A refused write returns 426 and says which version is required, which
one the client reported, that lookups still work, and where the update
steps are.

On numbering: the single incrementing integer stays. It is monotonic
and compares numerically, which is exactly what this floor needs, and a
`1.4.2` would advertise a compatibility promise this project does not
make. The fingerprint, not the number, identifies a build exactly.

The deployment guide now says to set the floor to a build the unit has
**approved**, not to the newest one — a version existing is not the
same as a version having been validated here.

## v200 — deployment, for a unit of seven

**`python -m fme version`** reports the version and a SHA-256
fingerprint of every code file present. Same version plus same
fingerprint means the same program; same version with a different
fingerprint means one install was edited or did not copy cleanly.
Checked three ways: stable across runs, changes when a file is added,
returns to the original when it is removed.

**Every request to the hash store now declares the client's version,
and the server records it.** An admin can list who is on what:

    tankelewicz        v199   2026-11-01T02:50:56
    second_examiner    v178   2026-11-01T02:50:56

Recorded, never refused — refusing would stop an examiner mid-case over
a version number, while recording lets somebody notice the drift and
fix it deliberately. Proved against a running server with two clients
declaring different versions.

**`docs/DEPLOYMENT.md`**: installing, knowing what you are running,
seeing what the unit is running, updating in the right order (server
first), the three account roles and what each may do, and what a new
examiner should be given.

Every command in that document was run as written before it shipped —
which caught one that did not exist: it told an admin to call
`Accounts.all()`, and the method is `users()`.

## v199 — the guide, rewritten as sessions

`docs/EXAMINER_TASKS.md` was a list of jobs. It is now six ordered
sessions, each with what to run, what it proves, what to send back and
what a failure means, plus a results table to fill in — because the
table is the part that is worth keeping.

The order changed on reflection: reading **a real E01** moved to second,
ahead of building fixtures. Every case arrives as one, and the fixture
this project writes and reads proves only that we are self-consistent.

It also now names what none of the sessions cover, including one thing
that has never been on any list: **there is no deployment procedure.**
A second examiner has no documented way to install this, update it, or
confirm which version they are running — and version drift has already
cost this project a day on the hash server.

## v198 — APFS and HFS+

`tools/make_fixtures/make_mac_deleted.sh` builds either on a Mac, with
nothing to install: hdiutil and diskutil are part of the system. It
writes pictures, hashes them, deletes two, and converts the result to a
raw image.

**APFS is reported honestly rather than optimistically.** It is
copy-on-write and trims aggressively, so a deleted file's blocks may be
released before an image is taken. Those rows are marked
`deleted-may-be-gone`: if they do not come back the test says so and
does not fail, because that is the file system behaving as it does and
not evidence about the reader. On HFS+, which is not copy-on-write, the
deleted files are expected and the test fails without them.

Two things checked here rather than assumed: the script's embedded
picture writer was run and its output decoded (a Mac has no Pillow, so
it writes a BMP by hand), and the carver was confirmed to recognise BMP
at all — a fixture full of pictures the program does not carve would
have tested nothing.

Both branches were exercised against stand-ins before shipping,
including the APFS soft-fail path, so neither reaches the Mac unrun.

## v197 — exFAT, and a log that keeps its history

**The validation log was being overwritten.** `validate.py` wrote
`validation-record.md` and replaced it each run, which answers "does it
work now" and nothing else. What a validation history is for is the
other question — what was true on the day of the case.

Each run now keeps its own dated record under `docs/validation/` and
appends a row to `docs/VALIDATION_LOG.md`. Rows are never edited, and a
failed run stays: a log that records only successes is not a log.

**exFAT**: `tools/make_fixtures/make_exfat_deleted.sh` builds the
fixture on Linux. It carries a large share of this unit's evidence — SD
cards and most phone storage above 32 GB — and deletion there is not
FAT32's: the directory entry set survives with its name intact, so a
reader correct on FAT32 can still be wrong here.

The test checks one thing beyond the bytes: a deleted file with a long
name must **keep its name**. exFAT spreads a name across several
directory entries, and a reader that reassembles them wrongly returns
the right bytes under the wrong name — worse than failing, because
nothing looks broken.

Exercised against a stand-in before shipping, as with NTFS. The
long-name check failed against it, which is correct: the stand-in is a
FAT32 volume, where the first character of a deleted name is gone.

## v196 — an E01 that a test actually reads

Every case this unit works arrives as an E01, and no test had ever read
one. Nothing on PyPI writes EWF and the reference implementation is
LGPL, so `tools/make_fixtures/make_e01.py` packs one from the format
documentation: header sections, a volume section, zlib-compressed chunks
with a chunk table, and the digests at the end.

Four pictures, hashed before packing, all four returned byte-for-byte
through the full extraction path.

**What it establishes, stated carefully.** The writer packs from the
specification and the reader parses independently, but both live in this
project: it is a round-trip, not an independent check. `docs/VALIDATION.md`
§5.10 says so, and `docs/EXAMINER_TASKS.md` asks for the thing that would
close it — pointing the program at a real acquisition made by FTK Imager
or `ewfacquire`.

The first attempt was rejected by our own reader: the file header is
thirteen bytes — signature, a constant, the segment number and two
zeroes — and I had written ten.

**`docs/EXAMINER_TASKS.md`** is new: a running list of what can only be
done on a machine with the right hardware or libraries, with what each
task proves and what to send back. Items stay on it once done, with the
date and result.

## v195 — the NTFS fixture, finished

The builder written in v192 assumed the small picture would end up
resident. If NTFS gave it a cluster after all — a different cluster
size, a longer name, one more attribute — the fixture would still build,
still pass, and **silently stop testing the one thing it exists for**: a
file no carver can reach.

It now checks, with sleuthkit's `ifind` and `istat`, and **fails the
build** if the file is not resident, saying what to change. Where
sleuthkit is absent it records `resident_confirmed=unknown`, and the
test repeats that rather than assuming.

The test side was strengthened to match. A recovered resident file must
not have come from the carver — that is the assertion a carve cannot
satisfy by luck — and the engine that did supply it is named in the
output, so a reader of the validation record sees it rather than taking
it on trust. Both readers are run over the NTFS fixture as well, since
`pytsk3` is The Sleuth Kit and the vendored reader is not.

The NTFS branch cannot run in the development environment, so it was
exercised against a stand-in volume to prove the branch itself works
before shipping — rather than shipping a code path nobody had executed,
which is how five features reached this examiner unwired.

Still to build, in order: exFAT deleted, E01, then APFS and HFS+.

## v194 — a claim about independence, corrected

v193's validation document said a comparison against The Sleuth Kit's
`icat` gave "two independent readers agreeing on a known input". For this
program that is wrong, and it was wrong in a document written to be
disclosed.

**`pytsk3` is The Sleuth Kit.** NTFS, exFAT, APFS, HFS+ and ext are read
through it, so comparing that output against `icat` is the same library
answering twice. It shows we call TSK consistently. It shows nothing
about whether TSK is right.

The independent pair is already here: `pytsk3` and the vendored reader,
which reads the same file systems with no C extension and shares no code
with it. The known-answer test now runs the fixture through **both**
(`FME_NO_TSK=1` selects the vendored path) and compares each against the
hashes recorded before deletion. On the FAT32 fixture: both readers
return all three files byte-for-byte and agree with each other.

The `icat` comparison stays, because it is meaningful for the vendored
reader. The validation document and the run record now say where it is
not independent, and the limitations section says it too.

Caught by the examiner reading the document, not by me writing it.

## v193 — validation protocol and known limitations

`docs/VALIDATION.md` is written to be handed to opposing counsel: what
the program does, how it has been tested, and — at equal length, which
the suite now enforces — what it does not do and what has not been
tested. It says in its own words that it is not an independent
assessment and that no error rate has been established.

Its limitations section covers what carving cannot know, why a recovered
deleted file may be incomplete, what a hash-set match does and does not
mean, that scores are not findings, that a signature does not make a
claim true, and a plain list of what the developers have not tested:
NTFS, exFAT, APFS, HFS+, ext and E01 through libewf, no cross-check
against The Sleuth Kit, half the interface, and no independent review.

`tools/validate.py` runs the protocol and writes `validation-record.md`
— version, a SHA-256 fingerprint of the code that ran, the machine, the
libraries present, every test with what it asserts, and **every check
that did not run**. The first run: 11 of 11 passed, 3 checks did not run,
and the record names all three rather than reporting a clean sweep.

The suite now guards the document: it must state what has not been
tested, must say no error rate is established, and its limitations must
not be shorter than its claims — the failure mode being a limitations
section that withers while the capability list grows.

## v192 — known-answer testing

Every test here until now asked whether this program behaves as I
expected. That catches regressions and nothing else: a reader that has
always been wrong passes every time, and two of our own readers agreeing
proves only that they share an assumption.

`tests/test_known_answer.py` asks something different. A fixture is built
by writing known files to a volume and hashing them **before** anything
is deleted, so the expected answer comes from what went in. On the FAT32
fixture: three files, two of them deleted, all three returned
byte-for-byte, and the copies on disk hash to the same values.

`tools/make_fixtures/make_fat_deleted.py` builds that fixture here.
`tools/make_fixtures/make_ntfs_deleted.sh` builds the NTFS one on a
machine with ntfsprogs — including a **resident** file, held inside its
MFT record, which no carver can ever reach, so recovering it proves the
MFT path rather than the carver.

Where The Sleuth Kit is installed, both the builder and the test compare
each file against what `icat` returns from the same record: two
independent readers agreeing on a known input. Neither is installed here,
so this run cross-checked nothing and says so — "not checked" is printed
rather than passed.

The approach is taken from the other tool in this space, whose fixtures
record hashes before deletion and cross-check against The Sleuth Kit at
build time. Theirs: 92 test files, 40 fixtures. Ours: 38 and 1. The gap
is worth stating plainly, and it is the next thing to close.

This is not a validation report and the documentation says so where it
describes it.

## v191 — routes out of media_gui

Thirty-four of the eighty-five routes now live in files of their own:
`routes_store.py` (the hash store and hash sets), `routes_report.py`
(reports and exports) and `routes_apps.py` (the per-file tools). The
bodies are unchanged — what they lean on is bound from media_gui when
each module is imported at the end of that file, so every name resolves
as it did inside it.

**media_gui.py: 463,126 characters four versions ago, 113,035 now.**

**It broke the program on the way, exactly where I said it would.** Two
module-level constants, `CONVERTIBLE` and `THUMB_EDGE`, sat between
routes and moved with them — out of sight of the page that uses them.
Opening a case then returned 500. Caught by the endpoint test, not by
reading the diff.

So the endpoint test now refuses a route file that holds anything but
routes: a stray constant, a plain function or a class fails the build.
The same mistake cannot be made quietly twice.

The remaining fifty-one routes are the page, the case, the run and the
scheme — the ones that touch the job table most, and the ones worth
moving last.

## v190 — the browser harness drives a case

v189 opened on the case list and checked the ribbon around an empty
screen. It now does what an examiner does: opens a case from the list,
clicks a tile, grades it from the keyboard, and closes the pane.

Everything it now covers passes, including the two faults reported from
real use:

- the ✕ closes the side pane **and the ribbon stops showing it lit** —
  the exact thing that could only be cleared by reopening the pane;
- a keyboard key grades the selected file, **the case on disk records
  it**, and Ctrl-Z takes it back.

It also checks something only a rendered page can show: that nothing is
sitting on top of a control. A notification landing over the pane's tabs
is invisible to the markup and to a shim — both report the tabs are
there.

## v189 — a real browser

`tests/test_browser.py` starts the program, opens the page in Chromium and
presses things. Every other interface test reads the page as text or runs
its script against a shim that does what it was taught to do; this one
watches what the browser actually does.

**It found a serious bug on its first run.** The Display tab's controls
are the page's only copies, moved into the ribbon as it draws — and the
ribbon redraws with `innerHTML`, which destroys whatever is inside. So
switching away from Display **threw the tile slider, both dropdowns and
the toggles away permanently**, and everything that looked them up
afterwards got null. The page then threw on the next redraw. They are
moved somewhere hidden before each redraw now.

It also settled a question I had to ask the examiner to check by hand:
moving the tile slider does resize the tiles —
`minmax(170…)` becomes `minmax(420…)`.

**What it does not yet cover, and says so rather than passing:** it opens
on the case list, so the gallery and the side pane are not exercised —
including closing the pane with its own ✕, which is the fault that
prompted this. The first version of the gallery check passed on hidden
tiles, which is the shim's mistake in a new costume; it counts visible
ones now and reports the gap instead.

## v188 — the interface is three files

`media_gui.py` held a 307,000-character string containing the markup, the
stylesheet, the script and, around it, eighty-five routes. Everything was
adjacent to everything, which is where most of this week's interface bugs
came from: an empty extract panel, three classes with no styles, a ribbon
that stayed lit, a button that ran the wrong thing.

The page is now `fme/web/ui/page.html`, `styles.css` and `app.js`, read and
assembled at import. **media_gui.py went from 463,126 characters to
157,112**, and what the browser receives is unchanged — checked by
hashing the assembled page before and after: `85a5bf28…` both times.

What it buys, immediately:

- `app.js` is valid JavaScript on its own, so `node --check` reads it
  directly rather than it being dug out of a Python string.
- The suite now looks for a function defined twice (98 functions, none
  repeated) and for a class styled in more than two places — the check
  that would have found the two identical copies of `_stripe_tasks` on
  the Python side.
- The spec ships the folder and the packaging test fails if any of the
  three is missing or if the assembled page still has a placeholder in
  it: a build that leaves them behind would start and serve a blank page.

The routes are still in `media_gui.py`. Splitting those is the next step
and a larger one.

## v187

**The resume did not work on the path most runs take.** v182 and v183
were tested with one worker. A real machine carves in parallel — the
default is sixteen — and that path builds stripes rather than walking
chunks, and ignored the resume offset entirely. No file came back twice,
because the digests are known, but the hours a resume exists to save were
spent re-reading the image.

Stripes that finished are dropped now, backed off by one signature's span
so a file straddling the stripe the run died in is still found. On a 300
MiB image resumed at 200: sixteen stripes become six, the first begins at
188 MiB, and the last still reaches the end. The suite checks all three.

Found by auditing rather than by it failing: the tests passed because
they used one worker, which is not what anyone runs.

## v186

**The Display tab holds the controls, not buttons that open a box.**
Pressing "Time zone" opened a dialog containing per page, tile size, time
zone, blur, hold-the-page and the blur scope — six settings behind a
button named after one of them.

The tile-size slider, the per-page and time-zone dropdowns and the
toggles are now on the ribbon itself. A ribbon item can be a control as
well as a button, and the control put there is **the one the page already
has**, moved into its slot as the ribbon draws rather than rebuilt — so a
dropdown on the ribbon and the setting it changes cannot drift apart.

Rendered and looked at rather than assumed: the first draw truncated the
zone name to "America/New_York (this ma…", which is the one thing that
control exists to show.

## v185

The contents ring broke a promise the reports make: **a report built
without images must hold no image**, which is what makes it safe to
disclose, and a chart is still an image. The ring is left out of those
reports; the numbers stay. Caught by the existing test, which asserted
exactly that and was right to fail.

## v184 — what a report covers

Every report now opens with a **Report contents** block and a ring of the
categories: files split into images, videos and other; each category with
its count and share; each flag; hash-set matches; and the case's total
media beside the report's, so a reader can see what share of the case they
are holding.

**Counted once for all three documents.** `report_contents` does the
counting and the HTML, Word and PDF writers lay out the same numbers —
checked by building all three from one case and comparing what they claim.
Three documents disagreeing about how many CAM pictures a case holds turns
a hearing about pictures into a hearing about the tool.

The ring uses the gallery's own colours, drawn with Pillow rather than a
charting library: a report that will not build because a charting library
is missing is worse than one without a picture.

Found while building it: the first version read a row's grading code as if
it were its kind, because a row calls the first "category" and the second
"kind". It fell over on the first real case rather than on a fixture,
which is the right way round.

## v183 — resume, properly

v182 resumed the carve. A run that died anywhere else still redid the
file-system walk, the MFT recovery and the deleted-entry pass to reach the
same place.

Each phase now records that it finished, in the same note as the carve's
position, and a resume skips what is already done. Killed eight seconds
into a run: the note said `filesystem, mft, fat-deleted` finished and the
carve at 16 MiB of 300; the resume skipped all three, finished the carve,
and found **48 files — the same as a run that was never interrupted.**

Found while building it: the carve's checkpoint wrote a fresh note every
few seconds and threw away the record of finished phases, so a resume
would have redone them all while believing it had not. The note is merged
now, and the suite checks both survive together.

The other tool has no resume of any kind -- checked rather than assumed.

## v182 — the carve resumes

**A crash used to cost the whole run.** The recovered files were on disk,
but nothing recorded where the scan had reached, so the only way forward
was to start again — most of a working day on a large image.

A run now writes its position every few seconds, beside the case and
renamed into place so it is never found half-written. `--resume` reopens
that case, reads back the rows already written so nothing is recovered
twice, and starts from the recorded offset, backed off by one signature's
span in case a file straddled the point where it died.

The test is not that a resume finishes. It is that it finds the same
files: on a 320 MiB image with 48 pictures, killed after six seconds with
24 recovered, the resumed run found **48 — the same as a run that was
never interrupted, nothing missed, nothing twice.** A resume that quietly
skipped evidence would be worse than no resume.

## v181

**"Folder missing" on a folder that was there.** The case list judged a
case present by whether `manifest.json` existed, which is also what an
interrupted run leaves behind — so a crashed case, with every recovered
file and the row-by-row list still on disk, was reported as missing. An
examiner told that has no reason to look inside.

Three states are told apart now: the folder is gone, the folder is there
with nothing recoverable, or **the run was interrupted** — and the last
offers to rebuild the manifest from `manifest.csv`, which a run writes as
it goes. Tested on a case with its manifest deleted: six files recovered,
and the rebuilt manifest says it is partial and why.

**An empty manifest is not a manifest.** A run killed while writing one
leaves a zero-byte file, which counted as "this case is fine" — the worst
answer, because the recoverable rows are right there. It counts as missing
now. Found by causing it: my own first version of the rebuild failed
mid-write and left exactly that, so the rebuild writes beside and renames
into place.

## v180

**Display is a ribbon tab of its own**, with real controls rather than a
dialog: Tiles (smaller, larger, reset), Per page (200, 500, 1500, All,
with the one in use lit), Blur (the review blur, what it covers, and hide
the screen), Times, and Hold the page. It sits between View and Apps, and
nothing on it changes a file, a grading or a report.

They drive the same controls the toolbar held rather than new copies of
them, so there is still one of each.

**Setting a control from code does not tell the page.** The handlers
listen for the event a person typing would cause, so moving a slider or a
dropdown from the ribbon has to send it — otherwise the control moves and
nothing follows. Found because the test shim could not dispatch events and
reported eight items throwing; the shim can now, and all 75 ribbon items
across all seven tabs run cleanly.

## v179

**Details, Classification and Filters stayed lit after the pane was
closed.** The ribbon lights them from the pane's state and was told when a
ribbon item was pressed, not when the pane was closed with its own ✕ or
switched from inside. It is told in both places now, so the only way to
clear them is no longer to open the pane again.

**"Hash store" on Case Data ran a check instead of opening the store**,
which is why a button with that label complained that the job had no
manifest. It opens the store. Checking a case against it is its own
button, on Home, where it was already.

**Importing a set into a shared store said only "No such file on the
server".** True, and useless: the file is read from a path on the server,
not from the machine the examiner is sitting at, because a national set is
gigabytes and is not sent over the connection. The message now says that,
and gives the command to import it on the server itself. The setup guide
says it too.

Checked while there: both forms of an NSRL RDS database are read — the
modern `METADATA` view and the older `FILE` table — along with
`NSRLFile.txt` and Project VIC JSON. The refusal was never about the
format.

## v178

**The gallery toolbar is cleaned up.** It held eleven filter chips and
nine controls in one band. The chips and the search stay — they are what
is touched while grading. The rest moved: tile size, per page, time zone,
blur and its scope, and hold-the-page into **View → Display options**;
Gallery and Table onto the View tab; Refresh, Undo and Side pane onto
Home.

**Nothing was rebuilt in its new place.** The controls are moved into the
dialog, and the ribbon drives the originals by pressing them, so there is
one of each. Rebuilding them would have meant two of every control and the
second one quietly not working — and the suite now fails if any of them
exists twice or stops existing.

Writing it, my own check caught a tip of 24 characters that restated its
label. Rewritten.

## v177

**Clearing the filters left the ribbon lit.** "Hide known software" and
"Show containers" went on showing as active over a gallery that was
showing everything. The ribbon lights its toggles from live state and was
only redrawn when a ribbon item was pressed, so anything that changed that
state elsewhere left it stale. It is now redrawn whenever the gallery is,
which covers every case rather than the two that were noticed.

**A run says what it has found, not only what it is doing.** Walked,
carved, from databases, embedded, deleted and unreadable, updating as the
run goes — the numbers the finished summary already reported, shown while
they are still moving. Every progress report carries them, through one
wrapper rather than twenty-odd call sites each remembering to.

## v176

**"No files match this filter" while the count said 112,995.** Both cannot
be true. During a run the page is held, so arriving files do not shuffle
what is being read — which eventually leaves an examiner standing on a page
past the end while the count climbs behind them. The message blamed the
filter.

It now distinguishes the three cases: nothing extracted yet, nothing
matching the filter, or a page that is empty because it is held — and for
the last it says to press First or untick "Hold the page" to follow the
run.

**Carve alignment measured rather than argued about.** On a 250 MiB image
with 40 pictures, one worker: every byte took 9.3s and found 40 of 40;
sector alignment took 7.6s and found **1 of 40**. 22% faster and
effectively blind, because a picture inside a document or a web cache is
almost never on a sector boundary. The default stays.

## v175

**An empty Classification pane said "Nothing extracted yet" or nothing at
all**, which reads as a program with no categories in it and sent an
examiner looking for a scheme to set up that was already there.

It now says which of three things it is: no case open, no files in the
case yet, or the case's own file did not load. Only the third is a problem,
and it says what to do about it.

Checked rather than assumed, because the question was whether categories
are seeded per case: a fresh case returns ten categories and six flags, all
locked, and the same ten after the case is closed and reopened. The manual
now lists them with their keys and says plainly that an examiner does not
set them up.

## v174

**All 61 ribbon items now explain themselves**, not the 14 from v173. A
button an examiner cannot ask about is one they will either avoid or press
blind, and both are worse than a sentence.

The suite requires it: every item must carry an explanation, and none may
be short enough to be a restated label. Three were written across two
lines and slipped a first pass that matched single-line items — caught by
counting what the page actually produces rather than how many edits were
made.

## v173

**The ribbon buttons say what they do.** Every item already had hover
text; it repeated the label, which tells an examiner nothing they cannot
already read. The fourteen that do real work to a case now carry a
sentence instead, and the manual sets them out in full.

Writing them made a duplication plain, and the manual says so rather than
tidying it away: **Find similar** on Apps and **Similar images** on Case
Data are the same search — one started from the selected file, the other
run over the whole case. And three buttons answer questions that are easy
to confuse, so they are compared directly: Find similar and Find edited
copies both look for *this picture again*; **Others like this** is the
only one that looks for *a different picture of the same thing*.

The View buttons are grouped with a line saying what matters most about
them: none of them changes a file, a grading or a report.

## v172

**The 4,096-byte truncation was mine, not a disagreement between
engines.** The fixture's deleted picture is 9,276 bytes and both readers
report that size correctly. The v167 recovery pass followed the FAT chain,
which deleting a file frees, so it stopped after one cluster. It now reads
the recorded size straight through when the chain is short, and says in
the file's note that this assumes the file was not fragmented.

The main engine was never affected: it recovers that file whole, named,
and marked deleted, with no TSK installed. The test now checks that -- the
file comes back whole and named, by whichever engine reaches it -- rather
than checking that one particular pass did it, which is what made a
correct suppression look like a failure.

**A carved file can now take a name from a directory entry.** Carving
finds content and loses everything else; where the same bytes arrive again
from a deleted entry or an MFT record, the name, the path and the deleted
flag are added to the file that was kept. The bytes are not stored twice.

**"Limits & comparison" is gone from the program.** It is a working
document about another tool, written for the people building this one. It
does not belong in front of an examiner working a case, or in a program a
defence expert may be shown. It ships as `docs/AUDIT.md` in the release.

## v171

**The second file-system engine is no longer restricted to rare formats.**
The vendored reader in this tree reads NTFS, exFAT, FAT, HFS+, APFS and
ext2/3/4, and was limited to QNX, F2FS, SquashFS, YAFFS and similar on the
grounds that "the engine reads those" — true only where `pytsk3` is
installed. A machine without it could read a car system and not a Windows
laptop, while carrying a reader for both.

Measured on the FAT32 fixture, with no TSK: **6 files through the vendored
reader, 0 under the old restriction.** `tools/verify.py` now reports it, so
a machine that is quietly blind to NTFS says so.

**An open discrepancy, recorded rather than papered over.** The two engines
disagree about the same deleted FAT entry: ours reads its size as 4,096,
the vendored reader as 9,276. One is misreading the directory entry and it
is not established which. A file recovered at 4,096 bytes when it is 9,276
is a truncated picture, so deleted FAT recovery should be treated as
recovering something from the right place rather than the whole file until
this is settled. It is the next thing to resolve.

## v170

**A C4All index can be read, not only written.** The exchange was one-way:
this program exported C4All and could not take one back, so a set from
another unit was unusable.

Three decisions in it, each of which would be a real harm to get wrong.
C4All's `0` means "nobody categorized this", and reading it as category 0
would mark a whole case Non-Pertinent on a decision nobody made. Matching
is on MD5 and SHA-1, never on name, because two cases full of
`IMG_0001.JPG` collide instantly. And an existing grading in this case is
kept: where the index disagrees, the difference is reported and the
examiner's own decision stands.

The index is summarized before anything is taken, because its numbers mean
whatever the scheme of the unit that wrote it meant.

Written against the format as the other tool's reader documents it, rather
than copied: theirs speaks to their case database and this has to speak to
ours.

## v169 — documentation

**The setup guide and the manual said `hash_store.db` throughout.** That
was only ever the name in my examples, and this lab's store is `hashes.db`.
Sixteen places in the guide and three in the manual now use the real one,
and the note explaining that the name is the examiner's own choice stays.

**And the guide told you to restart a task called "Hash store".** It never
had that name here. It now tells you to look the name up —

```
schtasks /query /fo table | findstr /i "hash fme"
```

— before using it, gives this lab's (`Start_FME_Hash`), and says plainly
that `schtasks /end` stops only a server the task started: one started by
hand in a Command Prompt needs Ctrl-C or taskkill, which is the exact
confusion that cost an hour.

The part of the guide that creates the task now says to name it something
recognizable and write the name down, because nothing else in the program
knows it.

## v168

**Manage store listed every source, including every case.** A unit
contributing a few hundred cases a year turns the one place an examiner
looks at the reference sets into pages of case numbers. The imported sets
are listed; the cases collapse to one line -- "900 hash(es) contributed by
300 case(s)" -- with a button to show them when somebody actually wants
one. The server's admin pages still list every one, which is where a
per-case view belongs.

**Their `archive.py` was evaluated and is not being vendored.** It imports
standalone and is MIT, but its public functions are bound to their own case
model -- 120 references to `case.`, 110 to `case.db` -- so taking it means
forking it.

What the evaluation actually found is better: **the file systems are not
read in `archive.py` at all.** They are read in `qnxprobe.py`, which this
program already vendors. Our copy identifies FAT32, exFAT, NTFS, APFS and
HFS+, returns a walker for each, and that walker has a `deleted_files`
method -- checked against our own fixture, not read. We restrict it to the
exotic file systems and leave everything mainstream to TSK.

The second engine is already in the tree and unused. Wiring it up is now
the first item on the list.

## v167

**A FAT deleted-entry pass, which runs whether or not TSK is installed.**
It reads the directory entries directly, where a deleted file keeps its
name, first cluster and size — the same thing the MFT pass does for NTFS,
and the thing we had no equivalent of for FAT.

This is the fix for a machine with `pytsk3` finding **fewer** deleted
files than one without. TSK's entry for a deleted FAT file can carry no
size, so its walk has nothing to read; our own reader reads the entry
itself. The better-equipped machine was the one finding less, which is the
worst shape that kind of difference can take.

Measured on the fixture: one deleted entry seen, one recovered, marked
deleted, with its path and dates.

**And the name is shown honestly.** FAT overwrites the first character of
a deleted name, so it is gone. It used to reach the manifest as a
replacement character — mojibake in a report. It is now `?`, and the file
carries a note saying FAT overwrote it and it is not recoverable.

Not vendored: the other tool's 4,700-line file-system reader. It is MIT
and it does import standalone, which was checked rather than assumed, and
it remains the right next thing to evaluate against real NTFS and APFS
images. It was not the fix for this.

## v166

**Deleted entries a file system no longer sizes are counted, not dropped
silently.** The TSK walk skips any entry whose size is zero, which is what
a deleted FAT entry often looks like once its first cluster is cleared.
That skip was invisible: a volume could report nothing deleted while an
engine reading directory entries directly found files. The count now
reaches the summary, and `tools/verify.py` names it as the likely cause
when a deleted entry is not recovered.

This is a diagnosis, not a fix: whether TSK or `fat32_core` is right about
a given volume is not yet established, and the comparison document now
says so rather than implying deleted recovery is proven everywhere.

Recorded in the comparison: the other tool uses **no TSK at all** -- FAT,
exFAT, NTFS, HFS+, HFSX and APFS are read by their own code. The trade is
real in both directions and is written out rather than scored.

## v165

Three changes, all from reading a real machine's verify output rather than
my own.

**The FAT32 check counted files and called deleted recovery proven.** It
reported "8 file(s), 0 of them deleted" as a pass. The fixture contains a
deleted entry by construction, so none found is a failure. It is now its
own check and fails when nothing deleted comes back — which is what a
machine with pytsk3 installed reported, and what nothing flagged.

**The two file-system engines can now be compared on the same volume.**
`FME_NO_TSK=1` ignores pytsk3 and takes the fat32_core path, and verify
runs both where both exist and fails if they disagree about what a volume
holds. An examiner should be able to answer "do my two engines recover
the same files" about their own tool.

**A missing library now states its consequence**, not just its absence.
"NO cryptography" in a list is easy to read past; "on this machine, C2PA
signatures cannot be checked and this machine cannot make a server
certificate — pip install cryptography" is not.

## v164 — verification

**`tools/verify.py`**: builds its own evidence, runs the real extraction
over it, and reports **works**, **FAILED** or **not tested** for each
claim. The third state is the point — a missing library makes a class of
evidence unreadable while every test still passes, and nothing else says
so.

Fifteen checks pass here: a folder, a raw carve, a FAT32 volume with its
deleted entries, a Cellebrite-shaped `.ufdr`, a GrayKey-shaped tar, a
fixed VHD, hashes matching the originals and the manifest matching what
was written, device paths, a report, gallery tiles, and four on the
interface.

**Two things it cannot check here, and says so**: `pytsk3` is absent, so
NTFS, exFAT, HFS+, APFS and ext2/3/4 are unreadable and untested in this
sandbox; `libewf-python` is absent, so neither E01 path was exercised.
That was true for every version before this one and nothing reported it.

Three faults came out of writing it:

**An acquisition that will not open gave a Python traceback and no
manifest.** It now says which file, what the reader objected to, that a
truncated copy is the usual cause, and how to carve it as plain bytes
instead.

**Three classes the page uses had no style rule** — `rp-cat`, `xf-cat`,
`bulknote` — so they inherited whatever was around them. The existing
markup test missed them because it only checks classes the script creates.

**And three of the harness's first four failures were the harness.** Its
fixtures were JPEG-shaped bytes rather than decodable pictures, its VHD
footer had no checksum and wrong field offsets, and two of its regular
expressions matched things that were not names. Fixed, and worth recording:
a verification tool that cries wolf is worse than none.

## v163

The three things the comparison document said to do next.

**Containers are kept by default.** Without them a finished case has
nothing left to open: the archives and databases are in the evidence
rather than in the case. This is what makes a rerun possible at all, and
it was off.

**`expand-containers` works.** The v162 note said the cause was not found;
it was `out_dir`, which I had deleted along with the block that set it, and
a broad `except` turned the NameError into a quiet zero. Two more faults
came out of testing it properly: resume seeded the dedupe map but not the
set dedupe actually consults, so a second run **added every file again**
and the case quietly claimed to hold more than it did; and closing the
shared case handle left every later call working on a closed database.
First run recovers, second run adds nothing, the original manifest
survives.

**Failures record why.** `note_error` keeps which file failed and what the
error said, capped at 500, and the reasons travel in the summary. A count
told an examiner that eleven files could not be read and nothing else,
which made "try those again" impossible to offer.

Honest limit: the recorder and the summary passthrough are tested
directly, and one call site — a file that cannot be read during a folder
walk — is converted. The other error paths still only count. Converting
them is mechanical and has not been done.

## v162

Read their code module by module, looking for techniques rather than
features. The findings are in the comparison document; two things came
out of it in code.

**`OutputManager` can resume.** It opens a finished case's manifest for
appending instead of truncating it, reads back the digests so nothing is
recovered twice, and continues the file numbering. Before this, anything
that reopened a finished case's output destroyed the manifest of a case
somebody may have spent a week grading. Verified by reopening a finished
case and checking the rows survived.

**A reprocess endpoint, with one reason that works.** `rebuild-thumbs`
throws away the gallery tiles and makes them again, and writes why to the
case's audit log.

**And one that is not shipped.** `expand-containers` — open the archives
and documents in a finished case without carving the source again — was
written, and recovered nothing through the endpoint while recovering three
files when called directly. The cause was not found. It is removed rather
than shipped: a feature that silently does nothing is worse than one that
is absent, and this session has already produced four of those.

The deeper reason it is hard is recorded in the comparison document: their
case registers containers as rows of their own, so there is something left
to open later. Ours keeps only the media that came out of them.

## v161

**The gallery was loading full-size originals into every tile.** A tile is
about 180 pixels across and a phone photograph is 4,000, so a page of fifty
asked for a few hundred megabytes and the browser discarded almost all of
it after scaling — and did it again on every redraw, because nothing was
kept. On a case of a hundred thousand files that is most of why scrolling
feels slow, and none of it is the carving an examiner usually blames.

Tile-sized copies are made once, kept in the case's `thumbs` folder at
about 3 KiB each, and reused. Measured on phone-sized photographs: **1,394
times less data for a gallery page.**

They are made in the background when a run finishes, at low priority, so
they are ready before anyone scrolls — otherwise the first visit to a large
gallery is a hundred thousand decodes spread across the examiner's
scrolling. A file that will not decode falls back to the original rather
than showing a broken tile.

## v160

**The stash filled up and was never read.** v152 added it, wired it to
fill as an examiner grades, and never consulted it — so grading a file in
one case and meeting it in the next still meant grading it twice, which is
the only reason the stash exists. The fourth unreachable feature found in
two days, and the same cause each time: the piece was tested, the path was
not.

Checking a case against the shared store now checks the stash in the same
pass. A stash match is labelled **"your stash"** and carries what the
examiner decided before; where the stash and the store disagree, both are
reported and neither category is applied. **Check against my own stash**
on the extraction panel turns it off for a run.

Tested the way it should have been the first time: two cases through a
running server, grade in one, meet it in the other.

## v159 — speed

Profiled a run rather than guessing, and found the scan was spending **77%
of its time in 3.4 million `bytes.find` calls**. A transport stream is
recognized by one byte repeating every 188 bytes; one byte matches once in
every 256 of anything, so the detector walked roughly 1.5 million false
positions per 400 MiB in a Python loop. Correct, and invisible, and slow.

The same test is now done with array operations in one pass: **3,387,489
find calls became 40,185**, and a 400 MiB run went from 16.6s to 13.7s.
The array version is checked against the walk it replaced, position for
position, because trading correctness for speed in a carver would be a
much worse bargain.

**The worker cap was 8, written for a single spinning disk.** One worker
scans about 30 MiB/s, so eight ask roughly 240 MiB/s of the disk — more
than one spinning disk can feed, and a quarter of what a lab RAID can. A
machine with 64 cores was running 8 workers and taking four times as long
as it needed to. The default is now up to 16, and the dropdown still lets
an examiner pick higher when they know what the evidence is sitting on.

**What was tried and rejected**, so nobody repeats it: a NumPy prefilter
on 2-byte and 4-byte prefixes (slower — `isin` costs more than the memchr
passes it replaces), zero-copy `memoryview` views (slower), and a single
compiled regex alternation (1.1x, not the 50x the old comment claimed, and
not worth the rewrite). The remaining cost is 52 passes over every byte,
which is memory bandwidth; the way past that is more cores, not cleverer
Python.

## v158

**Content search had no way to run it.** The engine, the model check and
the Settings field all shipped in v142; the endpoint that searches and the
button that calls it did not. The third unreachable feature found today,
and the same mistake each time: the piece was tested, the path was not.

It is now on the Apps tab as "Others like this", with a search endpoint
that describes the case's pictures once, keeps the descriptions with the
case, and orders the case against the picture chosen. Without a model it
refuses and says what is missing, rather than returning nothing.

**The comparison document's summary is rewritten**, because it still said
theirs was the stronger tool for a phone case on the strength of two
capabilities that have since been built. It now records what was verified
by running the program, what each tool still genuinely has over the other,
and a warning that this document has been wrong three times by being
reasoned about instead of tested.

## v157

**Media inside documents was already working, and the comparison document
said it was not.** Word, PowerPoint, Excel, OpenDocument and PDF are all
opened during an ordinary run and the pictures inside recovered, each
recorded against the document it came from — verified by running the
extraction over one of each rather than by reading the code.

**`docmedia.py` is deleted.** It was written in v141 to add this, before
anybody checked that the existing container scan already did it, and
nothing ever called it. Two implementations of one thing is a liability:
the one with no callers is the one that rots, and the one that gets fixed
is whichever the person looking happens to find.

Its test is replaced with one that runs the extraction over a folder of
documents and checks what comes out. A module that works and a program
that never calls it are not the same thing — a mistake made twice here now.

## v156

**Streamed video is joined by the vendored reader now**, not the older
built-in rejoiner. It resolves an item's name from the cache index instead
of leaving it numbered, and joins a DASH or HLS stream's segments in the
order the cached manifest lists them — which the older rejoiner, built for
contiguous pieces, left as fragments.

The older rejoiner is kept as the fallback rather than deleted: a cache the
vendored reader cannot read is no reason to recover nothing from it, and
both are exercised by the suite.

Still not done: separate video and audio streams are recovered as separate
files. The library can mux them into one playable file and this does not
yet use that.

Found while testing: a first version of the gap test removed the piece that
sorted to the middle **by name**, which is the last piece by position — so
it tested truncation, not a hole, and passed while proving nothing. Pieces
are ordered by position now.

## v155

**The phone modules are now actually used.** v141 and v150 added reading a
mobile extraction as the device, folding Android's mount-point views, video
locations and media inside documents — and wired none of it into the
extraction path. They had tests and nothing called them, so a real `.ufdr`
still recorded `files/Fs/1/...`, the vendor's packaging, as the path. Four
versions of a feature that existed only in its own test.

Now, from the ordinary run: every recovered file records **the path on the
phone** alongside the path inside the archive, and which tool's packaging
it was recognized as. Both go into the manifest, the exports and the
report, and the device's path is the one a report shows — it is the one an
examiner is asked about in evidence.

**One photograph seen through several Android mount points is one file**,
with the other spellings recorded against it rather than counted as three
more files and three "exact copies".

The comparison document is updated with what the mobile side does and, as
plainly, what it still does not: no DASH or HLS joining, no parsing of a
vendor's own report files, and no app artifacts beyond media — chat
databases and call logs are ALEAPP and iLEAPP's work, and better left to
them.

## v154

**A server started without `--store` used to invent an empty store and
serve it.** Every lookup against it would come back "not known" — the worst
way for a hash store to fail, because it looks exactly like a store that
works and simply holds nothing matching. It now refuses to start, and a
`--store` path that does not exist is an error naming the stores that *are*
in that folder, rather than a new empty file.

That is also where the stray `hash_store.db` in a data folder comes from:
the old default. The name itself means nothing — a store can be called
anything, nothing converts between names — and the setup guide now says so
instead of implying a required name.

## v153

**Content Credentials are now checked, not just read.** The COSE_Sign1
signature is verified over the claim, so an altered claim or a manifest
copied in from another file is caught — which was the main reason an
unverified credential meant so little.

Who signed it is reported separately, and deliberately. A valid signature
proves the claim is intact and was signed by the key in the file. It proves
nothing about whose key that is: anybody can make a certificate saying
Adobe, and its signature really does hold. So the result distinguishes
"signature valid, signer not trusted" from "signature valid, signer
trusted", and only the second follows from a certificate chaining to an
issuer in a trust list the examiner supplied. No trust list ships here —
one that arrives with a forensic tool and is never updated is worse than
none.

This is the one place the comparison document can now be amended: the other
tool reads a C2PA manifest and does not verify it.

## v152

**A personal hash stash**, beside the shared store rather than instead of
it. Every file an examiner categorizes goes in as they work, so the next
case meets it already known — without waiting for a contribution to the
shared store, which is a deliberate act made later and often after review.
It also works when the server does not.

**Where the stash and the store disagree, both are reported and neither is
applied.** Two sources that each say what a file is, with no sign of which
said what, is worse than one source; an examiner who graded something one
way last month and finds the store saying otherwise needs to know that, not
be handed one of the two answers. Every match names its source.

Found while building it: the first version stored nothing at all, because
the code referred to a variable that did not exist in that function and a
catch-all `except` swallowed the error. The handler now logs what it
caught. A grading is still never lost over the stash — but silently doing
nothing is not the same as not failing.

## v151

**Face-level search.** Everything else here works on whole files, and a
file is the wrong unit for the question asked after finding one picture
that matters: are there others of this child? A photograph can hold several
people, so the unit is one detected face. Detection is YuNet, description
is SFace, and neither model ships — Settings takes the paths. Both load on
OpenCV 4, unlike the content model.

The threshold is OpenCV's published 0.363, quoted rather than invented, and
every result carries its score. A score is not an identification: siblings
score highly, strangers who resemble each other score highly, two pictures
of one person years apart can score low, and the model was trained on
adults. It orders what to look at next and decides nothing.

**The report named a time zone and printed UTC underneath it.** A header
reading "Times shown in America/New_York" above timestamps four hours wrong
is a misleading line in a document that may go to a court, and it has been
there as long as the timezone field has. Times are now converted, with
their offset, and the header states what was actually done — including
saying plainly when a zone is one this machine does not know, rather than
claiming a zone that was never applied.

Daylight saving is handled by the zone database rather than a fixed offset:
New York in July is four hours from UTC and in December five, and an hour
is the difference between somebody being at work and being at home.

## v150

**Ctrl-B did almost nothing where it mattered most.** It toggled the review
blur, which is scoped to chosen categories — by default 1, 2, 3 and 9. An
examiner in Uncategorized pressing it got nothing blurred at all, which is
exactly the screen they most need hidden. Hovering a tile also lifted the
blur, and holding B revealed every blurred file on the page.

Hiding the screen is now its own thing: every picture and video, whatever
its category, lifted by nothing — not hover, not the B key, not selection —
with a banner saying so, and any playing video paused, because a blurred
clip still has sound.

**One photograph is not three photographs.** Android exposes the same
storage through `/storage/emulated/0`, `/data/media/0`, `/mnt/user/0/...`
and `/sdcard`, and an app's data through three more. Registering each
spelling multiplied a device's media by three or four. They are folded into
one file with the other paths recorded against it — except where credential-
and device-encrypted storage, different Android users, or views that do not
hold the same bytes mean they are genuinely different files.

**A video's location is read.** Phones keep it in the QuickTime structure,
not in EXIF, so until now videos had no coordinates at all and a case whose
pictures were placed and whose videos were not looked like a case whose
videos were taken somewhere unrecorded. Both layouts are read, all three
ISO 6709 notations, with the hemisphere signs parsed rather than guessed.

## v149

**An import stopped part way left a write-ahead log bigger than the store.**
A large import writes faster than SQLite folds its log in, so the log grows
during it and is folded in at the end — the same design the other tool
uses, for the same reason. The flaw was what happened when the program was
stopped before that last step: the log stayed, and every read afterwards
had to walk it. A store answering in minutes rather than milliseconds, with
nothing to say why.

Three changes. A checkpoint is **tried repeatedly** rather than abandoned at
the first refusal, since it fails exactly when another connection is busy —
the other tool retries ten times, and not doing so was my oversight. A long
import **folds its log in every two million rows**, so being interrupted
costs minutes rather than an afternoon. And **`store --checkpoint`** folds
in a log that was left behind, saying how much it moved and how long it
took, rather than looking hung.

## v148

**Every request to a shared store scanned the whole store.** Asking how
many hashes it holds ran `COUNT(*)`; asking what each set holds joined the
entire hashes table to the sources table and grouped it. Both are full
scans, and both run on every lookup, every admin page and every Verify — so
a store of tens of millions of hashes answered slowly, and on a
single-threaded server one slow request blocked everything behind it.

Both are now running totals, kept correct in the one place every write
passes through. Correcting them at each caller is how a path gets missed
and the store under-reports what it holds, which is worse than counting
slowly: the first attempt did exactly that, and the test caught it.

A store written before this fills its totals in the first time it is asked.

**The client waits five minutes rather than one**, and a timeout now says
what it means — the connection worked, the server was busy — instead of
"could not reach the hash store", which sends an examiner looking for a
network fault that is not there.

**And a port already in use says so.** Starting a second server printed a
traceback about sockets; it now names the port, explains it is almost
always this program already running, and gives the two commands to find and
stop it.

## v147

Three things from one run, and the third explains the other two.

**A run over a folder that holds an acquisition found nothing and said
nothing.** Pointing at `DC25-321 Image\` rather than at `DC25-321.E01` is
the commonest way to get an empty run, and "0 files over 0 B" looks like
the program failing rather than like the wrong thing being chosen. The run
now names the acquisition it found in that folder and says what to point at
instead.

**The ProjectVIC warning blamed the case number** when the real reason was
that nothing had been recovered — sending an examiner to fix the one thing
that was not wrong. It now says which it was.

**The image is let go of when a run finishes**, and **Release image** on
the File tab does it on demand: the choice, and anything still holding the
file open. An examiner finishing a case wants the drive back, and should
not have to close the program to get it.

## v146

**"New extraction" opened an empty screen.** The extraction form is the
left column of the workspace, not a panel of its own — filling it in and
watching the gallery fill up is one piece of work. v135 moved the workspace
into its own panel to fix having no way back to an open case, and left the
extraction panel empty behind the one button an examiner presses first.

It now goes to the workspace and puts the cursor in the source box.

**And the suite checks that no ribbon item can open an empty panel.** An
item that opens a blank screen is worse than one that is missing: it looks
like the program is broken. Restoring the empty panel makes the test name
it. It also fails if the script switches to a workspace that is not in the
markup at all.

## v145

**A build that runs with nothing installed.** `packaging/build.py` freezes
the program with PyInstaller, as a folder or as a single executable.

Two things decide whether a frozen build works, and neither is the code.
**Files the program reads at run time** must travel with it, or it starts
and then fails when somebody asks for the manual. **Modules imported by
name** must be declared, or the build silently loses QNX, E01 without
libewf, Content Credentials and streamed video — silently, because each of
those is written to carry on when its reader is absent.

Both are now checked against the repository by `tests/test_packaging.py`,
which finds the by-name imports in the code rather than from a list
somebody has to maintain. It also fails if a vendored reader ships without
its licence, which is the condition its code may be used under at all.

**`fme/resources.py`** finds shipped files whether the program was started
from a checkout, a one-folder build or a one-file build that unpacks itself
somewhere new on every start. Code that walked up from `__file__` worked in
the first case only, and failed late in the others.

**Not built or run here**: PyInstaller cannot be installed in this
environment, and a Windows executable is built on Windows regardless. What
is tested is everything that decides whether the build will work; the build
itself is yours to make.

## v144

**"database is locked" from a server that was never restarted.** The
waiting added in v127 is in the server, and a server keeps running the code
it started with — so a unit that updated the program folder but left the
service up still gets the old refusal. There was nothing to tell an
examiner that is what had happened.

The client now reports the server's version, says when it is older than the
program talking to it, and translates an old server's own words into what
they mean and what to do: the store is busy, nothing is lost, and the
server needs restarting from the updated folder. **Verify** shows the
version every time.

The setup guide gains a short section on restarting the service after an
update, because replacing the folder is not enough and nothing said so.

## v143

**File gains a "Set up once" group**, separating what outlives a case from
what belongs to one: the hash store, hash sets, the category scheme and the
agency logo. Keeping those apart from a case's own controls is the
difference between a setting and a decision, which is the idea worth taking
from how the other tool arranges its start screen.

**An agency logo** on every report header unless a case gives its own. A
file that does not open as an image is refused in the dialog rather than
discovered in a report, and a logo that has gone missing costs a line in
the header rather than the report.

**`doctor` reports the OpenCV version and what it enables** — edited-copy
matching needs 4.4, the content model needs 5.0 — and, where a model is
set, whether it will load on this machine. Two capabilities that an
examiner otherwise cannot tell apart from one line saying "cv2: yes".

## v142

**Settings takes the path to the image model**, checks it when it is set,
and says what is wrong in terms of what to do about it.

The model this was written against — DINOv2-small, 88,411,480 bytes,
Apache-2.0 — is recognized by its SHA-256, so a wrong or truncated file is
caught before a search returns nonsense. And the one failure worth naming
is named: **OpenCV 4 cannot load it.** It stops on a node the model uses
and raises "error in handleNode", which tells an examiner nothing; Settings
says it needs OpenCV 5.0 and gives the pip line.

Found by trying the real model file rather than assuming it would load.

## v141

All four phone-case gaps from the audit.

**Mobile extractions read as the device.** Cellebrite `.ufdr`, GrayKey,
Magnet, Android `adb` backups and tar. The path on the phone is worked out
from the vendor's packaging and is the one shown — a report citing
`files/Fs/1/...` is citing the tool, not the device. Members with no
extension are classified by their bytes, the vendor's own report folder is
left out, and members are streamed so a 60 GB extraction does not need
60 GB of scratch space first.

**Pictures inside documents.** Office and OpenDocument media recovered;
PDFs delegated to the reader this program already had. An earlier draft
rebuilt raw-pixel PDF images into PNGs — that was wrong and was removed:
the existing reader refuses to, because a rebuilt file's hashes belong to
this tool rather than to evidence.

**Video an app streamed**, rebuilt from ExoPlayer's fragments by the
vendored `exoprobe` (MIT, Alexis Brignoni, 0.2.1), which also joins DASH
and HLS segments in manifest order. Verified byte for byte against the
video that was cached. Short items are called short only on evidence of a
gap or a recorded length, never guessed.

**Search by what is in a picture**, which **needs a model this program does
not ship**. The engine refuses without one rather than pretending, and
Settings takes a path. A score is a rank, never an identification, and no
category is applied from it.

## v140

**Finding a picture after somebody edited it.** A perceptual hash describes
the whole frame, so a crop, a rotation, a caption bar or a paste into a
screenshot defeats it — and those are what happen to a picture as it is
traded. This matches the distinctive points inside two pictures and accepts
the match only when they agree on one transform.

Measured: **eleven of eleven edits found** — re-saved, resized, cropped,
rotated 7° and 90°, mirrored, bordered, captioned, pasted into a
screenshot, made grayscale, brightened — and **none of eight unrelated
pictures wrongly matched**.

The first threshold I chose accepted two of those eight. Rather than adjust
it by feel I measured where true and false matches sit: every true match
kept at least 58 points through the transform, the worst coincidence kept
17. The threshold is 25, in the gap, erring high — a missed copy is a file
an examiner still sees and still grades, a false match is a file they are
told is the same picture.

It finds the same picture, not the same subject. Two photographs of one
person are not a match here, and the interface says so where it reports
results.

## v139 — documentation only

**The comparison is re-done against the other tool's 2026.5.6 source**, read
module by module rather than from its release notes, and rewritten to say
plainly where each program is ahead.

The largest gap it records is theirs: content search by what is *in* a
picture, which finds other pictures of the same subject rather than copies
of the same picture, and near-duplicate matching that survives a crop or a
paste into a screenshot. Both are capabilities this program does not have.

The largest gaps the other way are recovery of what the file system no
longer lists — including resident data that carving cannot reach at all —
a shared hash store with per-user tokens and an audit trail, and putting a
case back from that log after a mistake.

## v138

**Close case**, on the File tab: writes a snapshot, clears the case and
returns to the list. Decisions are on disk as they are made, so the
snapshot is a marker rather than a rescue — but closing with no sign of
having saved leaves an examiner wondering, and wondering is the thing worth
removing.

**Content Credentials now use the vendored `c2pareader`** (MIT, Alexis
Brignoni, 0.2.0) in place of the parser written for v137. Theirs reads
seven container formats against v137's four, reads the XMP beside them, and
reports the stated signer, issuer and signing time.

It replaced the version written here for a reason worth recording: given a
hand-built manifest whose JUMBF type was not a real C2PA store, **theirs
refused it and v137's accepted it**. A reader that accepts not-quite-C2PA
will eventually call something a credential that is not one.

It also draws a distinction v137 could not: a file walked to its end with
no manifest is different from a file that could not be read to its end, and
only the first makes "no credentials" mean anything. Both are reported.

Vendored under the same terms as the other two readers, with its licence,
its upstream, and its SHA-256 recorded and checked by the suite.

## v137

**Content Credentials (C2PA).** Reads the manifest a file carries about
itself: what made it, what was done to it, and the field that matters most
here — whether it says it was made by a trained model. Every major image
generator writes that, so a picture somebody has called AI-generated can now
be asked directly.

Read from JPEG (APP11), PNG (caBX), HEIC, AVIF, MP4 and WebP, with a CBOR
decoder written for it rather than a dependency added: sixty lines against
an examination machine that cannot read credentials until somebody is
allowed to install something.

**What it will not claim.** The signature is not checked — that needs a
certificate chain and a trust list this program does not carry — so every
result says so, in the details pane, in the count, and in the record kept
with the case. A manifest can be copied between files, and a file without
one has not been shown to be anything.

Taken from the other tool's 2026.5.6 release, of which this was the one
feature worth having: their regional category schemes we largely have as
custom schemes, their sparse-copy fix does not apply here, and their
"clear category uses the case's own Uncategorized code" is already how this
program works.

## v136

**The full size viewer zooms.** Scroll to zoom where the pointer is, drag
to move, `+` `−` to step, `0` to fit, `1` for actual size, double-click to
toggle. The arithmetic keeps whatever is under the pointer under the
pointer, which was checked against the figures rather than by eye: a pixel
at the cursor stays at the cursor through 2x, 4x, 8x and back down.

**Ctrl-B blurs everything on screen** — gallery, details pane, and the
picture open full size — from anywhere, with no mouse. For when somebody
walks up to the desk. It hides the pictures from the room; it changes no
file and no recorded decision.

Added to the existing key handler rather than beside it, and the suite
checks the grading keys still work: a keyboard change has broken them twice
before.

**The comparison document no longer names the tool it compares against.**
A document an examiner may attach to a case gains nothing from naming a
competitor. The one remaining mention is inside the vendored reader, which
is third-party code whose hash is verified — changing a word of it would
break that check and misrepresent what its author published.

## v135

Everything reported against v134, fixed.

**There was no way back to an open case.** The gallery lived inside the
extraction panel, so opening the hash store hid the case and only opening it
again brought it back. It has a workspace of its own now, with **Back to the
case** on File, Home and Case Data, and opening a case goes straight to it.

**Hex view, Full size and Find similar did nothing from the ribbon.** All
three take the file; the ribbon handed them its path. Right-clicking the
same picture worked because the context menu passes the file itself.

**Project VIC, LAVA, C4All and KML looked like they did nothing.** They ran
and wrote their files; they reported where into the extraction panel, which
is hidden while you are on the Report tab. Results now appear in a line
under the ribbon that can be read from any tab. C4All and LAVA also had
their answers read from the wrong key, so there was nothing to show even
where you could see it.

**New extraction looked inert** when the case was already on screen; it now
says what to do next.

**Hide known software, Show containers and Stack similar say what they
did** — each changes a count somewhere off screen, and "something happened
and I am not sure what" is not a state to leave an examiner in.

**The comparison document is no longer named after another tool** in the
ribbon: it reads *Limits & comparison*.

## v134

**The ribbon had no styles at all.** The rules were written against an
anchor that did not match, so none of them reached the stylesheet — the
markup was right and the script was right, and the page came out as a
column of plain text: every tab and every item on its own line, nothing
clickable-looking, nothing laid out.

The rules are in `<style>` now, inserted by position rather than by
matching text that may have changed.

**And the suite checks it.** Every class the markup uses or the script adds
must have a rule in the stylesheet, barring the few that exist only for the
script to find elements by. Removing the ribbon rules again makes the test
list all twelve of them by name.

That is the third edit in this interface to miss its anchor and do nothing.
The checks now cover all three ways it shows up: an element that is never
filled, a function that is never defined, and now a class that is never
styled.

## v133

**Fixes v132, which did not work at all.** The ribbon was drawn before the
variables it reads were declared, so the script threw as the page loaded —
and a throw at the top level stops every line after it. The page still
rendered, because the markup is already there, and not one button worked:
Refresh, New extraction, Browse, Scan, all of them dead.

The ribbon is drawn last now, after everything it reads exists, and inside
a guard so it can never take the rest of the program down with it again.

**And the test that would have caught it now exists.** Everything else
about the interface was checked by reading it — elements present, functions
defined, handlers wired — and none of that notices a throw at load, because
the code is all there and all correct in isolation. `tests/test_gui_boot.py`
runs the page's script against a pretend browser and fails if it does not
reach the end. Restoring v132's bug makes it say "Cannot access
'selectedPath' before initialization", which is exactly what an examiner
would never have been told.

**A title bar**, with the program's name and version. In a browser tab the
window has no title bar of ours, and somebody reporting a problem has to be
able to read the version without hunting for it.

## v132

**A ribbon, in place of the tab bar and the drop-down menu.** Six tabs —
File, Home, Case Data, Report / Export, View, Apps — each holding groups of
what can be done, in the order the work happens. Items that need a case or
a selected file are dimmed until there is one.

Everything on it calls something that already existed; a ribbon offering
what the program cannot do would be worse than none. Four things it needed
were written for it: a **Settings** dialog for what this machine remembers,
**What is installed**, the **audit log** moved off the old menu, and
**exportAs** for the Project VIC, KML, C4All and LAVA formats.

All forty-seven items were checked against the code, and the fourteen
endpoints behind them against a live case: all fourteen answer.

The old drop-down menu is removed rather than kept alongside.

**The test for undefined functions missed three of these**, because the
ribbon refers to them (`go: saveCase`) rather than calling them. It now
resolves references as well as calls, and counts a function's own
parameters as defined — which had been a false alarm waiting to happen.

## v131

**Exported files are split by kind as well as category**:
`Child Abuse Material (CAM) Images` beside
`Child Abuse Material (CAM) Videos`, which is how a submission to NCMEC is
asked for. It also makes the counts checkable — a folder of 703 files should
be the 703 the report says. The split can be turned off.

**And folder names keep the punctuation Windows allows.** The old sanitizer
replaced anything that was not a letter, digit, dot, space, dash or
underscore, so "Child Abuse Material (CAM)" became "Child Abuse Material
_CAM_" — harder to read and harder to match against the report beside it.
Only what Windows actually refuses is removed now.

## v130

**The report's tables read in category order.** They were sorted by how
many files each category held, so the order changed from case to case and
the serious categories could appear below the trivial ones. They now follow
the unit's own order — the order of the category bar and of the keys an
examiner presses.

Non-Pertinent and Uncategorized are listed after the rest. They say what was
not a finding, and putting them first puts the answer below the things that
are not the answer.

## v129

**The report's category picker never appeared.** v125 added the option to
the scope list and the box to the page, but the code that fills the box with
categories was written against an anchor that no longer existed, so it was
silently not applied. Choosing "only the categories I choose" revealed an
empty space, which is worse than the option not being there: it looks like
the feature exists.

It is wired now, built both when the scope is chosen and when the dialog
opens, with each category's count beside it.

**The test suite now catches this shape of fault**: every container the
script fills with `innerHTML` must exist in the markup, and the picker in
particular must have something that builds it and something that sends the
choice. A control that exists but is never filled is the kind of thing that
passes every test and fails the first person who uses it.

## v128

**A protocol report**, the document a case is handed over with: the case
header, an introduction, the conclusion and video-length tables, counts per
device where there is more than one, counts per flag within each category,
a signature block, and appendices — representative images, definitions and
the file list. The shape follows what examiners in this field already read;
the wording is this program's own, and the introduction and definitions can
be replaced in the dialog.

**Exporting the files themselves**, from the menu, separate from the
report. One folder per category, every copy hashed as it is written and
checked against the hash taken at carve time, and a manifest beside them
recording what each file is. It refuses to write into a folder that already
holds files.

Word tables had to be built for this. They carry the column grid that Word
renders without and other readers refuse the document over — a report that
opens for the examiner and not for the prosecutor is worse than none.

## v127

**"database is locked" while a set imports.** SQLite allows one writer at a
time and an import of a national set holds it for as long as the import
runs — but a second write gave up instantly rather than waiting, so
contributing a case during an import failed with a message that reads as a
fault when it is a queue.

A store now waits for the writer: locally up to five minutes, which covers
an import. The server waits fifteen seconds and then answers that the store
is busy, because a request that hangs for minutes only moves the failure to
the other end, where it arrives as a timeout and says nothing. Either way
nothing is lost — the case is added when the import finishes.

The message says what is happening rather than what SQLite calls it, and
names the other possibility: something else holding the store open.

## v126

**Tiles say what they stand for.** A badge for identical copies found
elsewhere, and a separate one for files that merely look alike. Two badges
rather than one number, because they are different claims: the same bytes,
against a perceptual hash's judgment that two pictures resemble each other.

**Stack similar** collapses a group of look-alikes to a single tile, drawn
with another behind it. **Grading that tile grades every file in the
group** — a stacked tile that graded only the one shown would leave the rest
ungraded behind something that looks dealt with, which is worse than not
stacking at all.

Taken from how the other tool does it, including the part worth copying:
their collapsed tile also categorizes its whole group.

## v125

**A report can be limited to the categories you choose.** Scope → *Only the
categories I choose*, with every category listed and its count beside it. A
report that goes to somebody else should not carry the Non-Pertinent files,
and deleting them afterwards is not a workflow. The choice covers the files,
the thumbnails and every count; the front page records it.

**Every report now opens with a Conclusion table**: per category, images and
videos found, how many are different files, and the total — files and unique
files side by side, because twenty copies of one picture is twenty files and
one picture, and a report giving only one number invites the wrong reading
of both.

**And an Overview of video length**: longest, shortest and total per
category. Durations are not in the manifest — a carved clip's length is
found by decoding, not by reading a header — so they are measured once and
kept with the case. A clip that cannot be decoded has no length rather than
a length of zero, is left out of the totals, and is counted and explained
beneath the table.

Found while building it: the media kind was being overwritten by the
grading, both living in a field called `category`. Every video therefore had
no length at all. They are separate fields now.

## v124

**The store connects by itself at startup.** v123 remembered the settings
but still wanted a button pressed, which is a step that exists only because
the program forgot. The Hash Sets tab now opens the store this machine used
last time and reports what it found; a store that cannot be reached is said
plainly rather than thrown as an error, since it may not be needed for the
work in hand.

**Remembering the token is on by default**, which is what makes that
possible, and the box says what it costs: the token is readable by that
Windows account. On a shared machine, turn it off.

**And a real bug in v123's settings.** Saving anything else — the store path,
for instance — wiped the remembered token, because "no token given" was
written as an empty one. The setting survived a restart as a blank, so an
examiner who had ticked the box was asked for the token again anyway. Only a
token actually given replaces the stored one now.

## v123

**An examiner sets the hash store up once, not once per session.** The
address, the certificate path and the examiner's details were held in memory
only, so every restart of the program meant typing them again — which is not
security, it is friction, and friction is how a token ends up in a text file
on somebody's desktop.

The address and the certificate are now remembered on that machine: neither
is a secret. **The token is remembered only if the examiner ticks the box**,
because it is their identity on a store full of hashes of child abuse
material and writing it down is a real reduction in security that some units
will accept and others will not. Unticked, it is not written anywhere — the
test reads the file to confirm the token is not in it at all. The file says
what it holds, is readable by that account only, and **Forget** clears it.

Settings live with the examiner's per-user data, not in the program folder —
a program folder is shared, replaced on update, and sometimes on a share.

## v122

**`--check` now says that it stopped the server.** It starts one, talks to
it, reports, and shuts it down — but it ended on "Everything checked out",
and the next thing anybody does is open the admin page and find nothing
listening. The refusal was real: there was genuinely nothing there.

It now says the server it started has stopped, and prints the command to
start one properly, with the paths that were just checked filled in.

## v230 — step two: a page of files, served like theirs

`/api/files?job_id=&limit=&offset=&kind=&deleted=&q=` returns one page
and the true total, in the same shape the other tool uses:
`{total, offset, files, server_ms}`. Filters are applied where the
rows are, the limit is capped at 5,000 as theirs is, and an offset
past the end comes back to the last page rather than returning
nothing -- during a run the list grows under the examiner.

On 200,000 files: **a page of 500 in 77 KB and 2 ms**, against the
99 MB the old whole-list response cost.

**Step three is not done, and was deliberately backed out.**

The gallery's filters -- hiding known software, collapsing
containers, similar groups, flags -- are applied in the browser and
interlock. Paging through the server without moving that model too
gives a gallery that pages correctly and **filters wrongly**, which on
a case is worse than one that is merely slow: an examiner would be
looking at a page that quietly leaves files out.

The wiring was written, run, and removed. What is left is the
endpoint, tested, and a comment in `app.js` saying exactly why the
gallery does not use it yet and what has to move first.

Shipping half of it would have been the fourth avoidable fault in a
week, and this one would have hidden evidence rather than shown none.

## v229 — the gallery fills during a run

Reading the other tool's code settled what four versions of guessing
had not. Their gallery asks for one page at a time --
`LIMIT 500 OFFSET n`, filters as SQL, the total count cached because
counting is a pass over every row. Ours kept every file in the job
and shipped the whole list on every poll: **ninety-nine megabytes,
several times a second**, on a case the size of the examiner's.

Step one of three, and it uncovered the real fault.

**The live poll never filled the gallery at all.** That branch updated
the status line and polled again; `files` was only ever set when the
run finished or the examiner pressed Refresh. The gallery was not
empty because the files had not arrived -- nothing ever asked for
them. Hours of a long run showing nothing, reading as "the program is
finding nothing".

Both halves are fixed:

- a running job sends the **most recent 500** files rather than all of
  them, with `files_known` for the true total. 99 MB becomes 80 KB.
- the live poll now draws them, every three seconds rather than on
  every poll -- redrawing a gallery twice a second while sixteen
  workers carve helps nobody.

A finished job still hands over the whole list, which is what the
gallery browses once a case is open, so opening a case is untouched.

Measured on a 400 MiB carve: files appear eight seconds in, largest
response 67 KB.

Steps two and three -- a paged `/api/files`, and a gallery that asks
for the page it is showing -- are still to do, and are what make a
200,000-file case comfortable rather than merely possible.

## v228 — an empty gallery during a run: what was and was not found

The examiner reported 8,150 recovered and nothing in the gallery.
Four things were checked rather than assumed:

**Manifest rows are flushed as they are written** — the file on disk
is current, not buffered until the end.

**The job's folder and the manifest's folder are the same.** An
earlier suspicion that they differed is wrong.

**The page is given files during a run**, not only at the end. On a
400 MiB carve they arrived five seconds in. A shorter test showed
nothing only because the refresh is throttled to four seconds and the
run was over before the first one.

**An out-of-range page is already corrected** before anything is
drawn, so a held page cannot be the cause either.

So none of the four candidates is it, and **the cause is not yet
known.** What went in is one defensive change — a page index past the
end now falls back to the last page with something on it, which the
existing guard should already prevent — and one diagnostic:
`/api/status` returns **`files_known`**, the number of files the
program is holding for the page.

That single number separates the two possibilities in one step: if it
climbs while the gallery is empty, the program has the files and the
page is failing to draw them; if it stays at zero, the files are not
reaching the page at all. Guessing further without it would waste
another version, as it has twice this week.

## v227 — a saved results folder was saved and never read

Spotted by the examiner: **File → Settings** showed the central folder
they had set some time ago, and every run still went to the default
beside the program.

Settings took it and wrote it to the settings file. The next launch
restored the store path, the store token and the certificate from that
same file — and not this. It had never been read back.

Two bugs in two days about the same field, from different causes: v225
was a stale copy in the route modules, this is a setting that was
written and never loaded. The first made the field snap back while you
watched; this one let it look right and quietly do nothing, which is
worse.

Restored defensively: a folder on a drive that is not attached today
does not stop the program starting. It says which folder, why it
cannot be used, and where results will go instead.

    The saved results folder E:\Cases\results cannot be used
    (the system cannot find the path). Results will go to
    ...\media_out until it is changed in Settings.

`tests/test_settings.py` starts the program in a separate process with
a folder saved, and checks it is used — and that an unusable one is
survived and explained.

## v226 — auditing the rest of the routes split

The examiner asked what else the split broke. Three audits:

**Every route called.** 38 GET routes with no arguments and 54 POST
routes with an empty body — 92 in all. None returned a server error;
none broke on a missing name.

**Every borrowed name checked** against what media_gui actually holds,
and against the list of names media_gui *rebinds*. No stale borrows
left in the code.

**And a test for the class, not the instance.** The results-folder bug
returned **200 with the wrong value** — calling routes would never
have found it. The suite now reads media_gui for every `global`
statement, rebinds each name, and asks each route module what it sees.

That test immediately found more than the v225 fix did: the stale
copies were still sitting in every route module. v225 changed the
*references*; the copies remained, so a future edit using the bare
name would have silently picked up the old value again. The rebound
names are now excluded from the copy entirely, so a bare reference
raises NameError at once instead.

The list is derived from the code, so a value that becomes rebindable
later is caught without anyone remembering to add it.

One more thing the suite caught on the way: the import shim this
needed put a constant in each route file, which the "route files hold
routes and nothing else" rule refused. The rule was right; the
exception is named rather than the rule weakened.

## v225 — the results folder could not be changed

Reported by the examiner: load an image, scroll to **Results folder**,
type a central location, and the field snaps back to the default. It
could not be moved.

Reproduced exactly. `/api/output` accepted the new folder, called
`set_output_root`, and then answered with the **old** one — so the
page, which fills the field from the answer, put the old one back.

The cause is in how the routes were split out of `media_gui.py` in
v191 and v203. Each route module copies media_gui's namespace when it
loads. That is right for everything shared by reference -- the Flask
app, the job table -- and wrong for anything media_gui **rebinds**
afterwards: the copy keeps the value it had at import.

`OUT_ROOT` is rebound every time an examiner chooses a results folder.
So is `STORE_PATH`, and `DEFAULT_IMAGE_PATH`. All three are now read
through the module at the moment they are used, not from the copy.

Checked end to end: an examiner sets a folder, starts a run, and the
case is written inside it -- not only that the endpoint answers
correctly. A folder that cannot be written to is still refused.

An attempted fix that did not work is worth recording: a module-level
`__getattr__` proxying to media_gui. PEP 562 applies to attribute
access from outside a module, not to plain name lookups inside it, so
every route broke at import.

## v224 — a red mark that was not the program's fault

v223 added a fallback for an E01 libewf refuses. The examiner's run
shows it did not fire: libewf **accepts** the fixture this project
packs, reports a media size, and reads nothing useful out of it. No
fallback can see that at the moment a file is opened -- by the time
the run has recovered nothing, the reader is long since chosen.

So the fixture is malformed in a way the reference implementation
tolerates rather than rejects, and on a machine with libewf it proves
nothing either way. Reporting it as **FAILED** put a red mark in an
examiner's validation record about a program that reads their real
acquisitions perfectly well -- which is its own kind of wrong answer.

Where libewf is present it is now recorded as **not tested**, with
what to do instead. On a machine without libewf it stays a real
end-to-end check of the vendored reader.

Fixing the writer needs libewf to test against, which this environment
does not have, and guessing at it blind is how dead code gets written.

**Session B is now the only open question about E01**, and
`docs/EXAMINER_TASKS.md` says so where the task is described.

## v223 — one reader refusing is not the end of the evidence

The examiner's v221 run: **`test_recover.py` passes** — the Unix path
assumption was the cause, as thought. And the E01 check moved from
"not tested" to **FAILED, 0 of 4**, on a machine with libewf, while
reading 4 of 4 here without it.

The fixture is at fault, not the program: libewf is the reference
implementation and is stricter than the vendored reader, and the E01
this project packs from the format documentation is not conformant
enough for it. A real acquisition from that same lab read fine on
v180 — 79,980 files walked — so the libewf path works on what the
acquisition tools write.

**The useful part is what it exposed.** With libewf installed, an E01
went to libewf and nowhere else: if libewf refused it, the run ended
with nothing. Now the vendored reader is tried before giving up, and
the log says which reader was used. An examiner whose acquisition
libewf will not take should get whatever the other reader can give
them, and know which one gave it.

`verify.py` explains that result rather than leaving a bare FAILED,
and `docs/VALIDATION.md` says the E01 fixture is evidence about the
vendored reader only.

Still open, and now the clearest gap in this project: **nothing has
read a real acquisition as a recorded test.** That is Session B.

## v222 — validation records survived nothing

The examiner ran the protocol in the morning and the record was gone
by the afternoon. **They were written inside the program folder**, in
`docs/validation/` — the one place guaranteed to be destroyed, because
updating means replacing that folder.

A record whose whole value is that it persists, kept in the only
directory certain not to.

They now live outside the program:

- Windows: `%LOCALAPPDATA%\ForensicMediaExtractor\validation`
- Linux and macOS: `~/.local/share/forensic-media-extractor/validation`
- or wherever `FME_VALIDATION_DIR` points — a case share, or a folder
  the unit backs up.

**And `validate.py` rescues what older versions left behind.** On a
copy updated from v221 or earlier it moves any records still sitting
in `docs/` before an update can delete them, and says how many it
moved. Tested: two old records and an existing log carried across, the
log's earlier rows intact.

The log's links were also relative to the old layout and would have
pointed nowhere.

## v221 — the tests assumed Unix

The first validation run on the lab machine: **9 of 11 passed, 2
failed** — `test_known_answer.py` and `test_recover.py`. Both pass
here. The difference is the operating system.

**Thirty-five test files hardcoded `/tmp`.** On Windows that is not a
temporary folder: it is `\tmp\` on whatever drive happens to be
current, which may not exist, may not be writable, and is not where
anyone would look. Every one now uses the platform's own temporary
folder.

`tests/test_packaging.py` fails if a Unix-only path appears anywhere
in the package again.

**This is a guess until the examiner re-runs it.** The two failures
are consistent with the path assumption and I have not reproduced them
— I cannot, on this machine. The validation record written on that run
holds the tail of each failing test, which would settle it; it is at
`docs/validation/2026-11-02T132128Z-v218.md` on that machine.

Three self-inflicted faults while making the change: a bulk edit that
put `import tempfile` after its first use in five files, then after it
again in four, before placing it before the first import in all
thirty-five. The suite caught each round.

## v220 — Session A came back, and closed the oldest open question

The examiner ran `tools/verify.py` on the lab machine: **18 passed, 0
failed**, with every library present including pytsk3 4.15.0 and
libewf.

**The deleted-FAT question is answered.** Open since v164, when a run
here recovered a deleted entry and a run there did not:

    both file-system engines recover the same files
       -- TSK 8 file(s)/1 deleted, fat32_core 8/1

Two readers that share no code, the same volume, the same answer. That
is the cross-check this project could not run on its own.

**Two of the three "not run" lines were my fault, and are fixed.**

`verify.py` reported "no E01 fixture is built here" while
`tests/fixtures/known.E01` sat in the same tree — the check simply
never looked. On a machine with libewf installed, the format every
case arrives in came back as "not tested". It now reads the shipped
fixture, or builds one, and checks the pictures come back
byte-for-byte: 4 of 4.

The NTFS line now uses `tests/fixtures/ntfs-deleted.img` when it
exists, and otherwise says how to build it rather than implying that
TSK being installed is itself evidence.

Three faults of my own on the way: a boolean passed where the reporter
wanted "works", so a passing check printed `[True]`; a note appended
to a list that did not exist, which ended the run in a traceback after
all the work; and the notes themselves were collected and never
printed.

## v219 — the documents, inside the program

Ten documents shipped with this program and none could be opened from
it except the manual and the server guide. **A limitations statement
nobody opens is not a limitations statement**, and an examiner asked
in a hearing what the tool does not do should find the answer in the
tool rather than in a folder.

**File → Help** continues with: *What it does not do*
(`docs/VALIDATION.md`), *Checks to run here* (`docs/EXAMINER_TASKS.md`),
*Installing* (`docs/DEPLOYMENT.md`) and *What changed*
(`CHANGELOG.md`), alongside the manual and the store guide.

**`docs/AUDIT.md` is deliberately not offered.** It names another tool
and is a working document for whoever builds this; it has no place in
front of an examiner working a case, or a defence expert reading over
their shoulder. The suite fails if it is ever added.

The suite also checks every offered document opens and is shipped, and
the frozen-build spec now carries `docs/`, `BUILDING.md` and
`CLAUDE.md` — a build that left them behind would offer an examiner a
Help menu of error pages.

Found while wiring the test: the checks first ran **after** the
endpoint summary had already been printed, so a failure would have
been noted under the verdict rather than changing it.

## v218 — twelve functions defined twice

The crashed carve in v217 was not one mistake. `media_core.py` held
**two copies of the entire parallel-carve machinery** — twelve
definitions, the second shadowing the first. Found by looking for
duplicates across the whole package rather than by meeting another
one.

Eleven pairs were byte-identical. One was not: `_parallel_carve_body`
existed as a version that writes each stripe's files as soon as the
stripe before it finishes — so an examiner sees something during a
long carve — and as an older version that writes nothing until the
whole pass ends. **The older one was the live copy.** That is why a
long run shows an empty gallery for hours.

Both copies are gone and one remains. Two further things came out of
removing them, and the second is the uncomfortable one:

**The progressive version does not work.** Promoted to live, it
recovered **0 of 24** planted pictures. It had been dead code since it
was written, so nothing had ever run it. The working version is back
in place, with a comment saying what happened and that the improvement
needs writing again — against a test that carves with more than one
worker, which is the test that was missing when it was written.

**A run could crash at the very end**, after all the work: the v211
code that explains an unreadable volume assumed the reader always
names what it found, and `identify_fs` can return no name. Guarded.

`tests/test_packaging.py` now fails if any function or class in the
package is defined twice.

## v217 — parallel carving recovered nothing

**The most serious defect found in this project.**

An examiner ran an E01 through v180 and the gallery was empty. The
console was full of:

    TypeError: _worker_init() takes 4 positional arguments but 5 were given

`_worker_init` runs once in each carve worker to open the image and
build the signature list. The `artifacts` argument had been added to
the call **and to the body** and not to the signature, so every worker
process died the moment the pool started.

**The run did not fail.** The pool returned, the carve reported no
files, and that is indistinguishable from an image with nothing in it.
The default is sixteen workers, so this is the path nearly every real
run takes.

It went unnoticed because **every test here that exercises carving uses
one worker**, and with one worker the carve runs in-process and never
touches the initializer. Two copies of the function, both wrong.

`tests/test_resume.py` now runs the same image with one worker and with
four and requires the same files back, and fails if any worker process
crashed.

What an examiner should know: a run on a machine with more than one
worker, between the version that added `artifacts` and this one,
**carved nothing**. Files found by walking the file system and by
looking inside containers are unaffected — those are the 79,980 and
115,736 in the report — but anything that existed only in unallocated
space was not recovered. Those cases need re-running.

## v216 — the Windows Search claim, corrected and the real risk closed

v215's comparison said a truncated `Windows.edb` would hang our run.
**That was wrong.** We do not read ESE at all: we read the SQLite index
Windows 11 22H2 and later use, and a `Windows.edb` is recognised and
refused with an explanation. Checked, including one truncated to
nothing but its header.

The document is corrected. The gap is narrower than claimed and still
real: **most casework is Windows 10**, and on those exhibits a cached
thumbnail cannot be matched to a file name at all.

**One genuine risk was there and is now closed.** The SQLite index is
opened with `immutable=1`, which tells SQLite to trust the file rather
than check it — right for a read-only forensic copy, and it means a
damaged b-tree can send a query round a loop that does not end. The
read now carries a sixty-second limit through a progress handler. A
healthy index of any size is read in a fraction of that; a run that
hangs with no message is the worst way for this to fail, because an
examiner waits, kills it, and cannot say what stalled.

`tests/test_metadata.py` checks all four: ESE recognised, ESE refused,
a truncated ESE recognised, and a corrupt SQLite index returning
rather than running forever.

## v215 — the comparison, rewritten

`docs/AUDIT.md` was last rewritten at v139. Seventy-five versions on,
most of it was wrong — and it was being used to decide what to build,
which makes a wrong comparison worse than none.

Measured rather than remembered: theirs is 23,976 lines in 56 modules
with **92 test files and 40 fixtures**; ours is 31,227 in 65 with 39
test files and **4 fixtures**. That ratio is the honest gap behind
every other line in the document.

What they have that we do not, after checking module by module rather
than from memory: application artifacts, their own file-system readers
with no C extension, **offline basemaps**, **Project VIC record detail
beyond the category**, Windows Search read in an isolated worker so a
truncated database cannot hang a run, a similarity index built from
thumbnails already made, and signed builds.

What we have that they do not: C2PA signature verification, resume,
rebuilding an interrupted case, a shared hash store with accounts and
an integrity procedure, a FAT deleted-entry pass of its own, saying
when a volume cannot be read — and the validation documents, which are
the largest difference and are not a feature.

Three claims the old document made that were wrong are recorded as
wrong, including the one an examiner caught: that comparing against
The Sleuth Kit gave two independent readers, when `pytsk3` **is** The
Sleuth Kit.

## v214 — the recovery procedure, written down

v213 made `--integrity` print what to do when a store is damaged. The
examiner asked where that was written. It was not: the steps existed
only in the program's output, which means the first time anybody would
read them is the moment the store is already broken.

`SERVER_SETUP.md` Part 11 now carries the whole thing as one
procedure — **Backing it up**, **Checking it for damage**, **When it
is damaged** — in that order, because the recovery steps depend on a
backup existing and the backup instruction was five hundred lines away
in a different section.

Two things the backup section now says that it did not: keep the
**imported set files** as well, because they are what makes the store
rebuildable and a backup without them is half a backup; and `--move-to`
is safer than a file copy because it checks the result.

Two things the recovery steps now say that the program's output did
not: **check the restored copy before putting it in service**, and
**tell the other examiners from when** — a lookup that found nothing
during the damaged period found nothing because of the damage, not
because the file was unknown.

`MANUAL.md` gains a short section under the symptom an examiner would
actually meet — "the hash store stops matching things" — pointing at
the procedure.

The suite checks both documents still carry it.

## v213 — the admin page, and checking the store for damage

**The admin page reports what was asked for, with charts.** Hashes
contributed, cases fed back, lookups and active time, as inline SVG —
drawn rather than charted, because a server has no internet and a
charting library is one more thing to fail. The table gains hashes,
cases, days active, hours and the version each person's copy reported.

**"Hours used" is reported honestly.** The store sees requests, not
sessions: it knows when somebody looked a hash up, not when they
opened the program. What is shown is active time — the span of each
run of requests less than thirty minutes apart — and the page says it
is a floor, not a timesheet. An examiner grading for an hour without
touching the store is invisible to it.

**`--integrity` checks the store for damage.** A hash store is one
large SQLite file several people write to over a network on a machine
that can lose power mid-import, and when it goes wrong the symptom is
not an error: lookups quietly stop matching. The check is read-only,
reports whether the pages are sound, and when they are not says what
to do in order.

**It does not repair in place, deliberately.** A salvaged store is one
whose contents nobody can account for, and this store decides what an
examiner is shown about a file. Restoring the last good copy and
re-importing the sets is the right answer, because a reference store
is rebuildable: the sets are files you still have.

It also says what a clean result does *not* prove — structurally
intact is not the same as holding the right hashes.

## v212 — the guide, prioritised

`docs/EXAMINER_TASKS.md` had eleven tasks and no order of importance,
which makes a list easy to put off whole. It now opens with the three
worth an hour: the capability report, a real E01, and the NTFS and
exFAT fixtures. The rest are worth doing and none is urgent in the
same way, and saying so is more useful than listing them equally.

`CLAUDE.md`'s current focus updated: the three encryption and
file-system items are closed, and what is next is a
duplicate-definition sweep and the open question about the vendored
BitLocker unlocker.

## v211 — a volume that cannot be read says so

ReFS last, and the same pattern as BitLocker: before building a
fixture I ran a ReFS volume through the program to see what the
fixture would test. It reported **no files and nothing else** — the
same output as an empty disk.

The vendored reader knew perfectly well what it was looking at. It
had the version, the cluster size, and the exact reason the walk was
impossible, and none of it reached the examiner.

A run that recovers nothing now asks the reader what the volume is and
prints its answer:

    Nothing was recovered, and this is a REFS volume: that is not the
    same as an empty disk.
        version      ReFS 3.7 (boot sector)
        boot sector  its checksum does not hold
        walk         not possible: no superblock with a checksum that holds

The manifest records the file system. This covers every format the
reader recognises and cannot walk, not only ReFS.

**ReFS contents are still not read**, and `docs/VALIDATION.md` §5.14
says so: image a ReFS volume from a running Windows machine that has
it mounted.

Noted while there: `phase_done` is defined twice in `media_core.py`,
as `_stripe_tasks` was. Where there are two there are probably more.

## v210 — LUKS, told apart by version

v209 recognised LUKS but called everything "LUKS". The version matters
to the examiner who has to open it: LUKS2 headers cannot be read by
cryptsetup older than 2.0, and the two derive their keys differently.
The version is read from the header, the advice changes with it, and
the **UUID** is reported — that is how a volume is matched to a key
somebody else holds.

`tools/make_fixtures/make_luks.sh` builds the fixture on Linux or WSL,
and writes **two** images: the volume as it would be seized, and the
same volume after cryptsetup opened it. The pair is the point. The
locked one must be reported as LUKS and recover nothing; the unlocked
one must give back the pictures, whose hashes were recorded before the
volume was ever closed.

Root is needed, because mapping a LUKS device is a kernel operation.
The test branch was exercised against stand-ins before shipping, so it
does not reach the examiner unrun.

## v209 — a locked volume no longer looks like an empty one

Asked for a BitLocker fixture. Before building one, I ran a BitLocker
volume through the program to see what the fixture would be testing.

**It reported "0 file(s)" and said nothing else.** That is also what an
empty disk reports. An examiner reading that output would conclude
there was nothing on the drive, which about a locked drive is wrong in
the way that ends a case.

So the fixture was the wrong thing to build first. The program now
recognises BitLocker and LUKS from the volume's first sector, says so
in the log **before anything is carved**, records it in the manifest,
and says what to do: unlock it and image the unlocked volume.

It does not decrypt anything and the documentation says so plainly.

**Found while doing it: a vendored reader in this project can unlock
BitLocker, and nothing calls it.** The same pattern as the file-system
readers — a capability present and unreachable. Recorded in
`docs/VALIDATION.md` §5.13 as an open question rather than quietly
wired up, because taking a recovery key and opening a volume is a
larger claim to stand behind than reading one somebody else unlocked.

`tests/test_encrypted.py` checks both signatures are recognised, that
an ordinary FAT volume — whose first sector begins the same way — is
not falsely called encrypted, and that a run over a locked volume
explains itself.

`docs/EXAMINER_TASKS.md` gains the PowerShell to build a real
BitLocker VHDX and what the program should say about it.

## v208 — a debugging protocol in the handbook

From a piece on working with an AI as an engineering partner rather
than a code generator. Most of it this project already does; one part
it plainly does not.

**Five hypotheses before changing a line**, each with how to tell
whether it is true, cheapest first. The intermittent failure took nine
sessions because each session took its most plausible hypothesis,
fixed something real, and shipped — twice fixing a genuine bug that
was not the cause. The actual answer would have been on any five-item
list.

Two rules recorded with it: **instrument before fixing** (the probe
that cracked that bug did so by producing nothing, which ruled out
half the search space), and **a fix that does not change the symptom
was not the fix** — keep it if it corrects something real, say so,
and keep looking.

`CLAUDE.md` also gains a **Current focus** line, which the article is
right that a handbook needs: what the next session should pick up,
and what is still untested and why.

## v207 — an engineering handbook, and where custom signatures went

**`CLAUDE.md`** in the project root: what the program is for, the
architecture in three sentences, the entry points and how a case
flows, the conventions, and — the part worth having — the gotchas that
have actually bitten. Route modules losing a module-level constant.
The page being three files. The interface's controls existing once.
`pytsk3` being The Sleuth Kit. A format with no length field carving
one frame long.

The suite keeps it honest: every path it names must exist (fifteen
checked), and the gotchas section must not wither into a description.

**Custom signatures are not lost.** The screen, the endpoints and the
carve all work — verified end to end by defining a type the program
had never heard of and recovering it at its exact length, 1,041 bytes.

What is missing is a command-line flag: `extract` has no
`--signatures`, so only a run started from the interface can use them.
Now stated in `docs/VALIDATION.md` §5.12 rather than left to be
discovered mid-case.

## v206 — the nine-session mystery, solved

It was never concurrency.

AMR records no length anywhere; its end is found by walking frames
until one stops making sense. The test padded its image with
`os.urandom`, so the bytes after the file were being judged as a frame
— and random bytes pass that judgement **1.8% of the time**, measured
over four hundred trials. The carve takes one frame more, the hash
changes, and the test reports the file as lost.

Running ten copies at once made it look load-related. Load only meant
more rolls of the same dice.

Found by instrumenting the parser and getting **no output at all**:
`_p_amr` was never rejecting the file, so it was succeeding with a
different extent — which pointed at the padding rather than at the
reader, where I had been looking.

The test pads with zeros now, because a test has to be reproducible.
Ten copies at once: zero failures.

**The behaviour is real and is now stated** in `docs/VALIDATION.md`
§5.11: a format with no length field can carve one frame long, the
recovered hash then differs from the original, and it will not match a
hash set. AMR and SILK are affected; anything with a length field —
JPEG, PNG, MP4, most of a case — is not.

Two genuine faults were fixed on the way, in v205: twenty-seven tests
sharing fixed `/tmp` paths, and parallel carve stripes that abutted
with no overlap so a file straddling a boundary was recovered by
neither worker. That second one was evidence loss, and nothing to do
with this.

## v205 — the intermittent failure, reproduced

Nine sessions of "it failed in the batch and passed alone". It
reproduces: run `tests/test_containers.py` six times at once and about
one copy loses an audio file.

**It is not a flaky test.** The file is built from constant bytes. The
carver returned different results for identical input, which is a
different and much worse thing.

Two real faults found on the way, both fixed:

**Twenty-seven tests shared fixed `/tmp` paths** and each wipes its
directory on startup, so two overlapping runs had the second deleting
the first's working files. Each now uses a directory of its own.

**The parallel carve's stripes abutted exactly, with no overlap.** A
file whose signature header straddled a boundary belonged to no stripe
and was simply not recovered — and because the worker count follows the
machine's load, the boundaries move, so the same image could give
different results on different runs. Stripes now reach one signature
span past their end, and the duplicate that overlap can cause is
suppressed by digest as any other is.

**Neither was the cause.** The loss still reproduces. The hypothesis,
the symptom and the next diagnostic are written up in `docs/AUDIT.md`
under *OPEN: a carved file can be lost under load*, including what it
would mean for casework if confirmed: a small carved file silently
dropped on a loaded machine, with nothing in the manifest to show it.

Recorded rather than guessed at. I am not shipping a speculative change
to the carver.

## v204 — the rest of the interface, in a real browser

The harness covered opening a case, grading and the side pane. It now
also opens **every screen** — the workspace, the case list, the hash
store, custom signatures, similar media — and checks each draws
something and throws nothing; checks the six extraction options an
examiner sets at the start of every case are present, and that ticking
one **actually changes it** rather than moving a tick wired to nothing;
opens the export and Project VIC panels; and checks the hash-store
controls.

"Present in the page" was not good enough for the panels: both start
hidden, and a panel that is in the markup but never opens is a feature
nobody can reach. They are opened, and their contents counted.

Two of the control ids I wrote from memory were wrong — `engine-select`
and `store-url` do not exist; they are `mode` and `store-path`. The
test found them, which is the point, and I corrected the test rather
than loosening it.

What is still not covered, and now says so in `docs/VALIDATION.md`:
anything behind a native Browse dialog, which cannot be driven from the
page, and producing a report end to end through the interface rather
than from the engine.

## v203 — the rest of the routes

Thirty-three more routes moved out: `routes_case.py` (opening, listing
and reworking cases), `routes_media.py` (serving a file, a thumbnail, a
frame, its details) and `routes_setup.py` (signatures, the scheme,
settings, the logo, the stash, C4All).

**media_gui.py: 463,126 characters six versions ago, 75,643 now.**
Eighteen routes remain — the run itself: start, status, cancel, the job
table and the image. Those touch shared mutable state constantly and
are the ones worth leaving where that state lives.

**It broke again, in the same way wearing a different hat.** v191 swept
`CONVERTIBLE` and `THUMB_EDGE` out of sight; the guard added then looks
for `ast.Assign`. `REPORTS: dict = {}` is an `ast.AnnAssign` — a
different node — so it walked straight past, the dict left the page's
sight, and starting a report returned 500.

Both guards now catch annotated assignments: the one in the extraction
script and the one that ships in the suite. And the suite now also
checks each route module is actually **imported** by media_gui — a
build that ships a file it never imports starts fine and returns 404 on
a third of the program — and that the registered route count has not
dropped.

Caught by the endpoint test each time, not by reading the diff. That is
the argument for having it.

## v202 — every case says which build produced it

Nothing recorded it. A manifest said `tool: media_core.py` and no
version; a case processed under v150 and reopened under v201 left
nothing behind to say which did the work.

One provenance block now reaches everything: the manifest records the
version, the engine version, a SHA-256 fingerprint of the code, and the
Python and platform; the HTML, Word and PDF reports carry a **Produced
by** line taken from the manifest rather than from the program writing
the report, because those differ when a case is reopened later; and a
rebuilt manifest records the build that rebuilt it separately from the
one that made the run.

**The Griffeye export was reporting a version that has never existed.**
`SourceApplicationVersion` defaulted to the export module's own
`__version__` — "1.0.0" — so every ProjectVIC set this program has ever
written has told Griffeye something untrue about what produced it. It
now says `v202 (d342be84b426)`.

The suite checks all four outputs name the same build, so they cannot
drift apart again.

Found on the way: my own PDF check could not read reportlab's ASCII85
layer and reported the line missing when it was there. The test does it
properly now — a check that cannot see what it is checking is worse
than no check.

## v201 — a version floor, scoped deliberately

`--min-version N` on the hash server refuses **contributions, imports
and removals** from older clients. It does **not** refuse lookups.

That split is the whole design. Stopping an examiner part way through a
case because of a version number is a worse outcome than the drift it
prevents: their work is underway and the store is the same store. An
old client *writing* into a set everyone shares is different — that is
how a unit ends up with rows nobody can account for, and it can wait
until the update is done.

A refused write returns 426 and says which version is required, which
one the client reported, that lookups still work, and where the update
steps are.

On numbering: the single incrementing integer stays. It is monotonic
and compares numerically, which is exactly what this floor needs, and a
`1.4.2` would advertise a compatibility promise this project does not
make. The fingerprint, not the number, identifies a build exactly.

The deployment guide now says to set the floor to a build the unit has
**approved**, not to the newest one — a version existing is not the
same as a version having been validated here.

## v200 — deployment, for a unit of seven

**`python -m fme version`** reports the version and a SHA-256
fingerprint of every code file present. Same version plus same
fingerprint means the same program; same version with a different
fingerprint means one install was edited or did not copy cleanly.
Checked three ways: stable across runs, changes when a file is added,
returns to the original when it is removed.

**Every request to the hash store now declares the client's version,
and the server records it.** An admin can list who is on what:

    tankelewicz        v199   2026-11-01T02:50:56
    second_examiner    v178   2026-11-01T02:50:56

Recorded, never refused — refusing would stop an examiner mid-case over
a version number, while recording lets somebody notice the drift and
fix it deliberately. Proved against a running server with two clients
declaring different versions.

**`docs/DEPLOYMENT.md`**: installing, knowing what you are running,
seeing what the unit is running, updating in the right order (server
first), the three account roles and what each may do, and what a new
examiner should be given.

Every command in that document was run as written before it shipped —
which caught one that did not exist: it told an admin to call
`Accounts.all()`, and the method is `users()`.

## v199 — the guide, rewritten as sessions

`docs/EXAMINER_TASKS.md` was a list of jobs. It is now six ordered
sessions, each with what to run, what it proves, what to send back and
what a failure means, plus a results table to fill in — because the
table is the part that is worth keeping.

The order changed on reflection: reading **a real E01** moved to second,
ahead of building fixtures. Every case arrives as one, and the fixture
this project writes and reads proves only that we are self-consistent.

It also now names what none of the sessions cover, including one thing
that has never been on any list: **there is no deployment procedure.**
A second examiner has no documented way to install this, update it, or
confirm which version they are running — and version drift has already
cost this project a day on the hash server.

## v198 — APFS and HFS+

`tools/make_fixtures/make_mac_deleted.sh` builds either on a Mac, with
nothing to install: hdiutil and diskutil are part of the system. It
writes pictures, hashes them, deletes two, and converts the result to a
raw image.

**APFS is reported honestly rather than optimistically.** It is
copy-on-write and trims aggressively, so a deleted file's blocks may be
released before an image is taken. Those rows are marked
`deleted-may-be-gone`: if they do not come back the test says so and
does not fail, because that is the file system behaving as it does and
not evidence about the reader. On HFS+, which is not copy-on-write, the
deleted files are expected and the test fails without them.

Two things checked here rather than assumed: the script's embedded
picture writer was run and its output decoded (a Mac has no Pillow, so
it writes a BMP by hand), and the carver was confirmed to recognise BMP
at all — a fixture full of pictures the program does not carve would
have tested nothing.

Both branches were exercised against stand-ins before shipping,
including the APFS soft-fail path, so neither reaches the Mac unrun.

## v197 — exFAT, and a log that keeps its history

**The validation log was being overwritten.** `validate.py` wrote
`validation-record.md` and replaced it each run, which answers "does it
work now" and nothing else. What a validation history is for is the
other question — what was true on the day of the case.

Each run now keeps its own dated record under `docs/validation/` and
appends a row to `docs/VALIDATION_LOG.md`. Rows are never edited, and a
failed run stays: a log that records only successes is not a log.

**exFAT**: `tools/make_fixtures/make_exfat_deleted.sh` builds the
fixture on Linux. It carries a large share of this unit's evidence — SD
cards and most phone storage above 32 GB — and deletion there is not
FAT32's: the directory entry set survives with its name intact, so a
reader correct on FAT32 can still be wrong here.

The test checks one thing beyond the bytes: a deleted file with a long
name must **keep its name**. exFAT spreads a name across several
directory entries, and a reader that reassembles them wrongly returns
the right bytes under the wrong name — worse than failing, because
nothing looks broken.

Exercised against a stand-in before shipping, as with NTFS. The
long-name check failed against it, which is correct: the stand-in is a
FAT32 volume, where the first character of a deleted name is gone.

## v196 — an E01 that a test actually reads

Every case this unit works arrives as an E01, and no test had ever read
one. Nothing on PyPI writes EWF and the reference implementation is
LGPL, so `tools/make_fixtures/make_e01.py` packs one from the format
documentation: header sections, a volume section, zlib-compressed chunks
with a chunk table, and the digests at the end.

Four pictures, hashed before packing, all four returned byte-for-byte
through the full extraction path.

**What it establishes, stated carefully.** The writer packs from the
specification and the reader parses independently, but both live in this
project: it is a round-trip, not an independent check. `docs/VALIDATION.md`
§5.10 says so, and `docs/EXAMINER_TASKS.md` asks for the thing that would
close it — pointing the program at a real acquisition made by FTK Imager
or `ewfacquire`.

The first attempt was rejected by our own reader: the file header is
thirteen bytes — signature, a constant, the segment number and two
zeroes — and I had written ten.

**`docs/EXAMINER_TASKS.md`** is new: a running list of what can only be
done on a machine with the right hardware or libraries, with what each
task proves and what to send back. Items stay on it once done, with the
date and result.

## v195 — the NTFS fixture, finished

The builder written in v192 assumed the small picture would end up
resident. If NTFS gave it a cluster after all — a different cluster
size, a longer name, one more attribute — the fixture would still build,
still pass, and **silently stop testing the one thing it exists for**: a
file no carver can reach.

It now checks, with sleuthkit's `ifind` and `istat`, and **fails the
build** if the file is not resident, saying what to change. Where
sleuthkit is absent it records `resident_confirmed=unknown`, and the
test repeats that rather than assuming.

The test side was strengthened to match. A recovered resident file must
not have come from the carver — that is the assertion a carve cannot
satisfy by luck — and the engine that did supply it is named in the
output, so a reader of the validation record sees it rather than taking
it on trust. Both readers are run over the NTFS fixture as well, since
`pytsk3` is The Sleuth Kit and the vendored reader is not.

The NTFS branch cannot run in the development environment, so it was
exercised against a stand-in volume to prove the branch itself works
before shipping — rather than shipping a code path nobody had executed,
which is how five features reached this examiner unwired.

Still to build, in order: exFAT deleted, E01, then APFS and HFS+.

## v194 — a claim about independence, corrected

v193's validation document said a comparison against The Sleuth Kit's
`icat` gave "two independent readers agreeing on a known input". For this
program that is wrong, and it was wrong in a document written to be
disclosed.

**`pytsk3` is The Sleuth Kit.** NTFS, exFAT, APFS, HFS+ and ext are read
through it, so comparing that output against `icat` is the same library
answering twice. It shows we call TSK consistently. It shows nothing
about whether TSK is right.

The independent pair is already here: `pytsk3` and the vendored reader,
which reads the same file systems with no C extension and shares no code
with it. The known-answer test now runs the fixture through **both**
(`FME_NO_TSK=1` selects the vendored path) and compares each against the
hashes recorded before deletion. On the FAT32 fixture: both readers
return all three files byte-for-byte and agree with each other.

The `icat` comparison stays, because it is meaningful for the vendored
reader. The validation document and the run record now say where it is
not independent, and the limitations section says it too.

Caught by the examiner reading the document, not by me writing it.

## v193 — validation protocol and known limitations

`docs/VALIDATION.md` is written to be handed to opposing counsel: what
the program does, how it has been tested, and — at equal length, which
the suite now enforces — what it does not do and what has not been
tested. It says in its own words that it is not an independent
assessment and that no error rate has been established.

Its limitations section covers what carving cannot know, why a recovered
deleted file may be incomplete, what a hash-set match does and does not
mean, that scores are not findings, that a signature does not make a
claim true, and a plain list of what the developers have not tested:
NTFS, exFAT, APFS, HFS+, ext and E01 through libewf, no cross-check
against The Sleuth Kit, half the interface, and no independent review.

`tools/validate.py` runs the protocol and writes `validation-record.md`
— version, a SHA-256 fingerprint of the code that ran, the machine, the
libraries present, every test with what it asserts, and **every check
that did not run**. The first run: 11 of 11 passed, 3 checks did not run,
and the record names all three rather than reporting a clean sweep.

The suite now guards the document: it must state what has not been
tested, must say no error rate is established, and its limitations must
not be shorter than its claims — the failure mode being a limitations
section that withers while the capability list grows.

## v192 — known-answer testing

Every test here until now asked whether this program behaves as I
expected. That catches regressions and nothing else: a reader that has
always been wrong passes every time, and two of our own readers agreeing
proves only that they share an assumption.

`tests/test_known_answer.py` asks something different. A fixture is built
by writing known files to a volume and hashing them **before** anything
is deleted, so the expected answer comes from what went in. On the FAT32
fixture: three files, two of them deleted, all three returned
byte-for-byte, and the copies on disk hash to the same values.

`tools/make_fixtures/make_fat_deleted.py` builds that fixture here.
`tools/make_fixtures/make_ntfs_deleted.sh` builds the NTFS one on a
machine with ntfsprogs — including a **resident** file, held inside its
MFT record, which no carver can ever reach, so recovering it proves the
MFT path rather than the carver.

Where The Sleuth Kit is installed, both the builder and the test compare
each file against what `icat` returns from the same record: two
independent readers agreeing on a known input. Neither is installed here,
so this run cross-checked nothing and says so — "not checked" is printed
rather than passed.

The approach is taken from the other tool in this space, whose fixtures
record hashes before deletion and cross-check against The Sleuth Kit at
build time. Theirs: 92 test files, 40 fixtures. Ours: 38 and 1. The gap
is worth stating plainly, and it is the next thing to close.

This is not a validation report and the documentation says so where it
describes it.

## v191 — routes out of media_gui

Thirty-four of the eighty-five routes now live in files of their own:
`routes_store.py` (the hash store and hash sets), `routes_report.py`
(reports and exports) and `routes_apps.py` (the per-file tools). The
bodies are unchanged — what they lean on is bound from media_gui when
each module is imported at the end of that file, so every name resolves
as it did inside it.

**media_gui.py: 463,126 characters four versions ago, 113,035 now.**

**It broke the program on the way, exactly where I said it would.** Two
module-level constants, `CONVERTIBLE` and `THUMB_EDGE`, sat between
routes and moved with them — out of sight of the page that uses them.
Opening a case then returned 500. Caught by the endpoint test, not by
reading the diff.

So the endpoint test now refuses a route file that holds anything but
routes: a stray constant, a plain function or a class fails the build.
The same mistake cannot be made quietly twice.

The remaining fifty-one routes are the page, the case, the run and the
scheme — the ones that touch the job table most, and the ones worth
moving last.

## v190 — the browser harness drives a case

v189 opened on the case list and checked the ribbon around an empty
screen. It now does what an examiner does: opens a case from the list,
clicks a tile, grades it from the keyboard, and closes the pane.

Everything it now covers passes, including the two faults reported from
real use:

- the ✕ closes the side pane **and the ribbon stops showing it lit** —
  the exact thing that could only be cleared by reopening the pane;
- a keyboard key grades the selected file, **the case on disk records
  it**, and Ctrl-Z takes it back.

It also checks something only a rendered page can show: that nothing is
sitting on top of a control. A notification landing over the pane's tabs
is invisible to the markup and to a shim — both report the tabs are
there.

## v189 — a real browser

`tests/test_browser.py` starts the program, opens the page in Chromium and
presses things. Every other interface test reads the page as text or runs
its script against a shim that does what it was taught to do; this one
watches what the browser actually does.

**It found a serious bug on its first run.** The Display tab's controls
are the page's only copies, moved into the ribbon as it draws — and the
ribbon redraws with `innerHTML`, which destroys whatever is inside. So
switching away from Display **threw the tile slider, both dropdowns and
the toggles away permanently**, and everything that looked them up
afterwards got null. The page then threw on the next redraw. They are
moved somewhere hidden before each redraw now.

It also settled a question I had to ask the examiner to check by hand:
moving the tile slider does resize the tiles —
`minmax(170…)` becomes `minmax(420…)`.

**What it does not yet cover, and says so rather than passing:** it opens
on the case list, so the gallery and the side pane are not exercised —
including closing the pane with its own ✕, which is the fault that
prompted this. The first version of the gallery check passed on hidden
tiles, which is the shim's mistake in a new costume; it counts visible
ones now and reports the gap instead.

## v188 — the interface is three files

`media_gui.py` held a 307,000-character string containing the markup, the
stylesheet, the script and, around it, eighty-five routes. Everything was
adjacent to everything, which is where most of this week's interface bugs
came from: an empty extract panel, three classes with no styles, a ribbon
that stayed lit, a button that ran the wrong thing.

The page is now `fme/web/ui/page.html`, `styles.css` and `app.js`, read and
assembled at import. **media_gui.py went from 463,126 characters to
157,112**, and what the browser receives is unchanged — checked by
hashing the assembled page before and after: `85a5bf28…` both times.

What it buys, immediately:

- `app.js` is valid JavaScript on its own, so `node --check` reads it
  directly rather than it being dug out of a Python string.
- The suite now looks for a function defined twice (98 functions, none
  repeated) and for a class styled in more than two places — the check
  that would have found the two identical copies of `_stripe_tasks` on
  the Python side.
- The spec ships the folder and the packaging test fails if any of the
  three is missing or if the assembled page still has a placeholder in
  it: a build that leaves them behind would start and serve a blank page.

The routes are still in `media_gui.py`. Splitting those is the next step
and a larger one.

## v187

**The resume did not work on the path most runs take.** v182 and v183
were tested with one worker. A real machine carves in parallel — the
default is sixteen — and that path builds stripes rather than walking
chunks, and ignored the resume offset entirely. No file came back twice,
because the digests are known, but the hours a resume exists to save were
spent re-reading the image.

Stripes that finished are dropped now, backed off by one signature's span
so a file straddling the stripe the run died in is still found. On a 300
MiB image resumed at 200: sixteen stripes become six, the first begins at
188 MiB, and the last still reaches the end. The suite checks all three.

Found by auditing rather than by it failing: the tests passed because
they used one worker, which is not what anyone runs.

## v186

**The Display tab holds the controls, not buttons that open a box.**
Pressing "Time zone" opened a dialog containing per page, tile size, time
zone, blur, hold-the-page and the blur scope — six settings behind a
button named after one of them.

The tile-size slider, the per-page and time-zone dropdowns and the
toggles are now on the ribbon itself. A ribbon item can be a control as
well as a button, and the control put there is **the one the page already
has**, moved into its slot as the ribbon draws rather than rebuilt — so a
dropdown on the ribbon and the setting it changes cannot drift apart.

Rendered and looked at rather than assumed: the first draw truncated the
zone name to "America/New_York (this ma…", which is the one thing that
control exists to show.

## v185

The contents ring broke a promise the reports make: **a report built
without images must hold no image**, which is what makes it safe to
disclose, and a chart is still an image. The ring is left out of those
reports; the numbers stay. Caught by the existing test, which asserted
exactly that and was right to fail.

## v184 — what a report covers

Every report now opens with a **Report contents** block and a ring of the
categories: files split into images, videos and other; each category with
its count and share; each flag; hash-set matches; and the case's total
media beside the report's, so a reader can see what share of the case they
are holding.

**Counted once for all three documents.** `report_contents` does the
counting and the HTML, Word and PDF writers lay out the same numbers —
checked by building all three from one case and comparing what they claim.
Three documents disagreeing about how many CAM pictures a case holds turns
a hearing about pictures into a hearing about the tool.

The ring uses the gallery's own colours, drawn with Pillow rather than a
charting library: a report that will not build because a charting library
is missing is worse than one without a picture.

Found while building it: the first version read a row's grading code as if
it were its kind, because a row calls the first "category" and the second
"kind". It fell over on the first real case rather than on a fixture,
which is the right way round.

## v183 — resume, properly

v182 resumed the carve. A run that died anywhere else still redid the
file-system walk, the MFT recovery and the deleted-entry pass to reach the
same place.

Each phase now records that it finished, in the same note as the carve's
position, and a resume skips what is already done. Killed eight seconds
into a run: the note said `filesystem, mft, fat-deleted` finished and the
carve at 16 MiB of 300; the resume skipped all three, finished the carve,
and found **48 files — the same as a run that was never interrupted.**

Found while building it: the carve's checkpoint wrote a fresh note every
few seconds and threw away the record of finished phases, so a resume
would have redone them all while believing it had not. The note is merged
now, and the suite checks both survive together.

The other tool has no resume of any kind -- checked rather than assumed.

## v182 — the carve resumes

**A crash used to cost the whole run.** The recovered files were on disk,
but nothing recorded where the scan had reached, so the only way forward
was to start again — most of a working day on a large image.

A run now writes its position every few seconds, beside the case and
renamed into place so it is never found half-written. `--resume` reopens
that case, reads back the rows already written so nothing is recovered
twice, and starts from the recorded offset, backed off by one signature's
span in case a file straddled the point where it died.

The test is not that a resume finishes. It is that it finds the same
files: on a 320 MiB image with 48 pictures, killed after six seconds with
24 recovered, the resumed run found **48 — the same as a run that was
never interrupted, nothing missed, nothing twice.** A resume that quietly
skipped evidence would be worse than no resume.

## v181

**"Folder missing" on a folder that was there.** The case list judged a
case present by whether `manifest.json` existed, which is also what an
interrupted run leaves behind — so a crashed case, with every recovered
file and the row-by-row list still on disk, was reported as missing. An
examiner told that has no reason to look inside.

Three states are told apart now: the folder is gone, the folder is there
with nothing recoverable, or **the run was interrupted** — and the last
offers to rebuild the manifest from `manifest.csv`, which a run writes as
it goes. Tested on a case with its manifest deleted: six files recovered,
and the rebuilt manifest says it is partial and why.

**An empty manifest is not a manifest.** A run killed while writing one
leaves a zero-byte file, which counted as "this case is fine" — the worst
answer, because the recoverable rows are right there. It counts as missing
now. Found by causing it: my own first version of the rebuild failed
mid-write and left exactly that, so the rebuild writes beside and renames
into place.

## v180

**Display is a ribbon tab of its own**, with real controls rather than a
dialog: Tiles (smaller, larger, reset), Per page (200, 500, 1500, All,
with the one in use lit), Blur (the review blur, what it covers, and hide
the screen), Times, and Hold the page. It sits between View and Apps, and
nothing on it changes a file, a grading or a report.

They drive the same controls the toolbar held rather than new copies of
them, so there is still one of each.

**Setting a control from code does not tell the page.** The handlers
listen for the event a person typing would cause, so moving a slider or a
dropdown from the ribbon has to send it — otherwise the control moves and
nothing follows. Found because the test shim could not dispatch events and
reported eight items throwing; the shim can now, and all 75 ribbon items
across all seven tabs run cleanly.

## v179

**Details, Classification and Filters stayed lit after the pane was
closed.** The ribbon lights them from the pane's state and was told when a
ribbon item was pressed, not when the pane was closed with its own ✕ or
switched from inside. It is told in both places now, so the only way to
clear them is no longer to open the pane again.

**"Hash store" on Case Data ran a check instead of opening the store**,
which is why a button with that label complained that the job had no
manifest. It opens the store. Checking a case against it is its own
button, on Home, where it was already.

**Importing a set into a shared store said only "No such file on the
server".** True, and useless: the file is read from a path on the server,
not from the machine the examiner is sitting at, because a national set is
gigabytes and is not sent over the connection. The message now says that,
and gives the command to import it on the server itself. The setup guide
says it too.

Checked while there: both forms of an NSRL RDS database are read — the
modern `METADATA` view and the older `FILE` table — along with
`NSRLFile.txt` and Project VIC JSON. The refusal was never about the
format.

## v178

**The gallery toolbar is cleaned up.** It held eleven filter chips and
nine controls in one band. The chips and the search stay — they are what
is touched while grading. The rest moved: tile size, per page, time zone,
blur and its scope, and hold-the-page into **View → Display options**;
Gallery and Table onto the View tab; Refresh, Undo and Side pane onto
Home.

**Nothing was rebuilt in its new place.** The controls are moved into the
dialog, and the ribbon drives the originals by pressing them, so there is
one of each. Rebuilding them would have meant two of every control and the
second one quietly not working — and the suite now fails if any of them
exists twice or stops existing.

Writing it, my own check caught a tip of 24 characters that restated its
label. Rewritten.

## v177

**Clearing the filters left the ribbon lit.** "Hide known software" and
"Show containers" went on showing as active over a gallery that was
showing everything. The ribbon lights its toggles from live state and was
only redrawn when a ribbon item was pressed, so anything that changed that
state elsewhere left it stale. It is now redrawn whenever the gallery is,
which covers every case rather than the two that were noticed.

**A run says what it has found, not only what it is doing.** Walked,
carved, from databases, embedded, deleted and unreadable, updating as the
run goes — the numbers the finished summary already reported, shown while
they are still moving. Every progress report carries them, through one
wrapper rather than twenty-odd call sites each remembering to.

## v176

**"No files match this filter" while the count said 112,995.** Both cannot
be true. During a run the page is held, so arriving files do not shuffle
what is being read — which eventually leaves an examiner standing on a page
past the end while the count climbs behind them. The message blamed the
filter.

It now distinguishes the three cases: nothing extracted yet, nothing
matching the filter, or a page that is empty because it is held — and for
the last it says to press First or untick "Hold the page" to follow the
run.

**Carve alignment measured rather than argued about.** On a 250 MiB image
with 40 pictures, one worker: every byte took 9.3s and found 40 of 40;
sector alignment took 7.6s and found **1 of 40**. 22% faster and
effectively blind, because a picture inside a document or a web cache is
almost never on a sector boundary. The default stays.

## v175

**An empty Classification pane said "Nothing extracted yet" or nothing at
all**, which reads as a program with no categories in it and sent an
examiner looking for a scheme to set up that was already there.

It now says which of three things it is: no case open, no files in the
case yet, or the case's own file did not load. Only the third is a problem,
and it says what to do about it.

Checked rather than assumed, because the question was whether categories
are seeded per case: a fresh case returns ten categories and six flags, all
locked, and the same ten after the case is closed and reopened. The manual
now lists them with their keys and says plainly that an examiner does not
set them up.

## v174

**All 61 ribbon items now explain themselves**, not the 14 from v173. A
button an examiner cannot ask about is one they will either avoid or press
blind, and both are worse than a sentence.

The suite requires it: every item must carry an explanation, and none may
be short enough to be a restated label. Three were written across two
lines and slipped a first pass that matched single-line items — caught by
counting what the page actually produces rather than how many edits were
made.

## v173

**The ribbon buttons say what they do.** Every item already had hover
text; it repeated the label, which tells an examiner nothing they cannot
already read. The fourteen that do real work to a case now carry a
sentence instead, and the manual sets them out in full.

Writing them made a duplication plain, and the manual says so rather than
tidying it away: **Find similar** on Apps and **Similar images** on Case
Data are the same search — one started from the selected file, the other
run over the whole case. And three buttons answer questions that are easy
to confuse, so they are compared directly: Find similar and Find edited
copies both look for *this picture again*; **Others like this** is the
only one that looks for *a different picture of the same thing*.

The View buttons are grouped with a line saying what matters most about
them: none of them changes a file, a grading or a report.

## v172

**The 4,096-byte truncation was mine, not a disagreement between
engines.** The fixture's deleted picture is 9,276 bytes and both readers
report that size correctly. The v167 recovery pass followed the FAT chain,
which deleting a file frees, so it stopped after one cluster. It now reads
the recorded size straight through when the chain is short, and says in
the file's note that this assumes the file was not fragmented.

The main engine was never affected: it recovers that file whole, named,
and marked deleted, with no TSK installed. The test now checks that -- the
file comes back whole and named, by whichever engine reaches it -- rather
than checking that one particular pass did it, which is what made a
correct suppression look like a failure.

**A carved file can now take a name from a directory entry.** Carving
finds content and loses everything else; where the same bytes arrive again
from a deleted entry or an MFT record, the name, the path and the deleted
flag are added to the file that was kept. The bytes are not stored twice.

**"Limits & comparison" is gone from the program.** It is a working
document about another tool, written for the people building this one. It
does not belong in front of an examiner working a case, or in a program a
defence expert may be shown. It ships as `docs/AUDIT.md` in the release.

## v171

**The second file-system engine is no longer restricted to rare formats.**
The vendored reader in this tree reads NTFS, exFAT, FAT, HFS+, APFS and
ext2/3/4, and was limited to QNX, F2FS, SquashFS, YAFFS and similar on the
grounds that "the engine reads those" — true only where `pytsk3` is
installed. A machine without it could read a car system and not a Windows
laptop, while carrying a reader for both.

Measured on the FAT32 fixture, with no TSK: **6 files through the vendored
reader, 0 under the old restriction.** `tools/verify.py` now reports it, so
a machine that is quietly blind to NTFS says so.

**An open discrepancy, recorded rather than papered over.** The two engines
disagree about the same deleted FAT entry: ours reads its size as 4,096,
the vendored reader as 9,276. One is misreading the directory entry and it
is not established which. A file recovered at 4,096 bytes when it is 9,276
is a truncated picture, so deleted FAT recovery should be treated as
recovering something from the right place rather than the whole file until
this is settled. It is the next thing to resolve.

## v170

**A C4All index can be read, not only written.** The exchange was one-way:
this program exported C4All and could not take one back, so a set from
another unit was unusable.

Three decisions in it, each of which would be a real harm to get wrong.
C4All's `0` means "nobody categorized this", and reading it as category 0
would mark a whole case Non-Pertinent on a decision nobody made. Matching
is on MD5 and SHA-1, never on name, because two cases full of
`IMG_0001.JPG` collide instantly. And an existing grading in this case is
kept: where the index disagrees, the difference is reported and the
examiner's own decision stands.

The index is summarized before anything is taken, because its numbers mean
whatever the scheme of the unit that wrote it meant.

Written against the format as the other tool's reader documents it, rather
than copied: theirs speaks to their case database and this has to speak to
ours.

## v169 — documentation

**The setup guide and the manual said `hash_store.db` throughout.** That
was only ever the name in my examples, and this lab's store is `hashes.db`.
Sixteen places in the guide and three in the manual now use the real one,
and the note explaining that the name is the examiner's own choice stays.

**And the guide told you to restart a task called "Hash store".** It never
had that name here. It now tells you to look the name up —

```
schtasks /query /fo table | findstr /i "hash fme"
```

— before using it, gives this lab's (`Start_FME_Hash`), and says plainly
that `schtasks /end` stops only a server the task started: one started by
hand in a Command Prompt needs Ctrl-C or taskkill, which is the exact
confusion that cost an hour.

The part of the guide that creates the task now says to name it something
recognizable and write the name down, because nothing else in the program
knows it.

## v168

**Manage store listed every source, including every case.** A unit
contributing a few hundred cases a year turns the one place an examiner
looks at the reference sets into pages of case numbers. The imported sets
are listed; the cases collapse to one line -- "900 hash(es) contributed by
300 case(s)" -- with a button to show them when somebody actually wants
one. The server's admin pages still list every one, which is where a
per-case view belongs.

**Their `archive.py` was evaluated and is not being vendored.** It imports
standalone and is MIT, but its public functions are bound to their own case
model -- 120 references to `case.`, 110 to `case.db` -- so taking it means
forking it.

What the evaluation actually found is better: **the file systems are not
read in `archive.py` at all.** They are read in `qnxprobe.py`, which this
program already vendors. Our copy identifies FAT32, exFAT, NTFS, APFS and
HFS+, returns a walker for each, and that walker has a `deleted_files`
method -- checked against our own fixture, not read. We restrict it to the
exotic file systems and leave everything mainstream to TSK.

The second engine is already in the tree and unused. Wiring it up is now
the first item on the list.

## v167

**A FAT deleted-entry pass, which runs whether or not TSK is installed.**
It reads the directory entries directly, where a deleted file keeps its
name, first cluster and size — the same thing the MFT pass does for NTFS,
and the thing we had no equivalent of for FAT.

This is the fix for a machine with `pytsk3` finding **fewer** deleted
files than one without. TSK's entry for a deleted FAT file can carry no
size, so its walk has nothing to read; our own reader reads the entry
itself. The better-equipped machine was the one finding less, which is the
worst shape that kind of difference can take.

Measured on the fixture: one deleted entry seen, one recovered, marked
deleted, with its path and dates.

**And the name is shown honestly.** FAT overwrites the first character of
a deleted name, so it is gone. It used to reach the manifest as a
replacement character — mojibake in a report. It is now `?`, and the file
carries a note saying FAT overwrote it and it is not recoverable.

Not vendored: the other tool's 4,700-line file-system reader. It is MIT
and it does import standalone, which was checked rather than assumed, and
it remains the right next thing to evaluate against real NTFS and APFS
images. It was not the fix for this.

## v166

**Deleted entries a file system no longer sizes are counted, not dropped
silently.** The TSK walk skips any entry whose size is zero, which is what
a deleted FAT entry often looks like once its first cluster is cleared.
That skip was invisible: a volume could report nothing deleted while an
engine reading directory entries directly found files. The count now
reaches the summary, and `tools/verify.py` names it as the likely cause
when a deleted entry is not recovered.

This is a diagnosis, not a fix: whether TSK or `fat32_core` is right about
a given volume is not yet established, and the comparison document now
says so rather than implying deleted recovery is proven everywhere.

Recorded in the comparison: the other tool uses **no TSK at all** -- FAT,
exFAT, NTFS, HFS+, HFSX and APFS are read by their own code. The trade is
real in both directions and is written out rather than scored.

## v165

Three changes, all from reading a real machine's verify output rather than
my own.

**The FAT32 check counted files and called deleted recovery proven.** It
reported "8 file(s), 0 of them deleted" as a pass. The fixture contains a
deleted entry by construction, so none found is a failure. It is now its
own check and fails when nothing deleted comes back — which is what a
machine with pytsk3 installed reported, and what nothing flagged.

**The two file-system engines can now be compared on the same volume.**
`FME_NO_TSK=1` ignores pytsk3 and takes the fat32_core path, and verify
runs both where both exist and fails if they disagree about what a volume
holds. An examiner should be able to answer "do my two engines recover
the same files" about their own tool.

**A missing library now states its consequence**, not just its absence.
"NO cryptography" in a list is easy to read past; "on this machine, C2PA
signatures cannot be checked and this machine cannot make a server
certificate — pip install cryptography" is not.

## v164 — verification

**`tools/verify.py`**: builds its own evidence, runs the real extraction
over it, and reports **works**, **FAILED** or **not tested** for each
claim. The third state is the point — a missing library makes a class of
evidence unreadable while every test still passes, and nothing else says
so.

Fifteen checks pass here: a folder, a raw carve, a FAT32 volume with its
deleted entries, a Cellebrite-shaped `.ufdr`, a GrayKey-shaped tar, a
fixed VHD, hashes matching the originals and the manifest matching what
was written, device paths, a report, gallery tiles, and four on the
interface.

**Two things it cannot check here, and says so**: `pytsk3` is absent, so
NTFS, exFAT, HFS+, APFS and ext2/3/4 are unreadable and untested in this
sandbox; `libewf-python` is absent, so neither E01 path was exercised.
That was true for every version before this one and nothing reported it.

Three faults came out of writing it:

**An acquisition that will not open gave a Python traceback and no
manifest.** It now says which file, what the reader objected to, that a
truncated copy is the usual cause, and how to carve it as plain bytes
instead.

**Three classes the page uses had no style rule** — `rp-cat`, `xf-cat`,
`bulknote` — so they inherited whatever was around them. The existing
markup test missed them because it only checks classes the script creates.

**And three of the harness's first four failures were the harness.** Its
fixtures were JPEG-shaped bytes rather than decodable pictures, its VHD
footer had no checksum and wrong field offsets, and two of its regular
expressions matched things that were not names. Fixed, and worth recording:
a verification tool that cries wolf is worse than none.

## v163

The three things the comparison document said to do next.

**Containers are kept by default.** Without them a finished case has
nothing left to open: the archives and databases are in the evidence
rather than in the case. This is what makes a rerun possible at all, and
it was off.

**`expand-containers` works.** The v162 note said the cause was not found;
it was `out_dir`, which I had deleted along with the block that set it, and
a broad `except` turned the NameError into a quiet zero. Two more faults
came out of testing it properly: resume seeded the dedupe map but not the
set dedupe actually consults, so a second run **added every file again**
and the case quietly claimed to hold more than it did; and closing the
shared case handle left every later call working on a closed database.
First run recovers, second run adds nothing, the original manifest
survives.

**Failures record why.** `note_error` keeps which file failed and what the
error said, capped at 500, and the reasons travel in the summary. A count
told an examiner that eleven files could not be read and nothing else,
which made "try those again" impossible to offer.

Honest limit: the recorder and the summary passthrough are tested
directly, and one call site — a file that cannot be read during a folder
walk — is converted. The other error paths still only count. Converting
them is mechanical and has not been done.

## v162

Read their code module by module, looking for techniques rather than
features. The findings are in the comparison document; two things came
out of it in code.

**`OutputManager` can resume.** It opens a finished case's manifest for
appending instead of truncating it, reads back the digests so nothing is
recovered twice, and continues the file numbering. Before this, anything
that reopened a finished case's output destroyed the manifest of a case
somebody may have spent a week grading. Verified by reopening a finished
case and checking the rows survived.

**A reprocess endpoint, with one reason that works.** `rebuild-thumbs`
throws away the gallery tiles and makes them again, and writes why to the
case's audit log.

**And one that is not shipped.** `expand-containers` — open the archives
and documents in a finished case without carving the source again — was
written, and recovered nothing through the endpoint while recovering three
files when called directly. The cause was not found. It is removed rather
than shipped: a feature that silently does nothing is worse than one that
is absent, and this session has already produced four of those.

The deeper reason it is hard is recorded in the comparison document: their
case registers containers as rows of their own, so there is something left
to open later. Ours keeps only the media that came out of them.

## v161

**The gallery was loading full-size originals into every tile.** A tile is
about 180 pixels across and a phone photograph is 4,000, so a page of fifty
asked for a few hundred megabytes and the browser discarded almost all of
it after scaling — and did it again on every redraw, because nothing was
kept. On a case of a hundred thousand files that is most of why scrolling
feels slow, and none of it is the carving an examiner usually blames.

Tile-sized copies are made once, kept in the case's `thumbs` folder at
about 3 KiB each, and reused. Measured on phone-sized photographs: **1,394
times less data for a gallery page.**

They are made in the background when a run finishes, at low priority, so
they are ready before anyone scrolls — otherwise the first visit to a large
gallery is a hundred thousand decodes spread across the examiner's
scrolling. A file that will not decode falls back to the original rather
than showing a broken tile.

## v160

**The stash filled up and was never read.** v152 added it, wired it to
fill as an examiner grades, and never consulted it — so grading a file in
one case and meeting it in the next still meant grading it twice, which is
the only reason the stash exists. The fourth unreachable feature found in
two days, and the same cause each time: the piece was tested, the path was
not.

Checking a case against the shared store now checks the stash in the same
pass. A stash match is labelled **"your stash"** and carries what the
examiner decided before; where the stash and the store disagree, both are
reported and neither category is applied. **Check against my own stash**
on the extraction panel turns it off for a run.

Tested the way it should have been the first time: two cases through a
running server, grade in one, meet it in the other.

## v159 — speed

Profiled a run rather than guessing, and found the scan was spending **77%
of its time in 3.4 million `bytes.find` calls**. A transport stream is
recognized by one byte repeating every 188 bytes; one byte matches once in
every 256 of anything, so the detector walked roughly 1.5 million false
positions per 400 MiB in a Python loop. Correct, and invisible, and slow.

The same test is now done with array operations in one pass: **3,387,489
find calls became 40,185**, and a 400 MiB run went from 16.6s to 13.7s.
The array version is checked against the walk it replaced, position for
position, because trading correctness for speed in a carver would be a
much worse bargain.

**The worker cap was 8, written for a single spinning disk.** One worker
scans about 30 MiB/s, so eight ask roughly 240 MiB/s of the disk — more
than one spinning disk can feed, and a quarter of what a lab RAID can. A
machine with 64 cores was running 8 workers and taking four times as long
as it needed to. The default is now up to 16, and the dropdown still lets
an examiner pick higher when they know what the evidence is sitting on.

**What was tried and rejected**, so nobody repeats it: a NumPy prefilter
on 2-byte and 4-byte prefixes (slower — `isin` costs more than the memchr
passes it replaces), zero-copy `memoryview` views (slower), and a single
compiled regex alternation (1.1x, not the 50x the old comment claimed, and
not worth the rewrite). The remaining cost is 52 passes over every byte,
which is memory bandwidth; the way past that is more cores, not cleverer
Python.

## v158

**Content search had no way to run it.** The engine, the model check and
the Settings field all shipped in v142; the endpoint that searches and the
button that calls it did not. The third unreachable feature found today,
and the same mistake each time: the piece was tested, the path was not.

It is now on the Apps tab as "Others like this", with a search endpoint
that describes the case's pictures once, keeps the descriptions with the
case, and orders the case against the picture chosen. Without a model it
refuses and says what is missing, rather than returning nothing.

**The comparison document's summary is rewritten**, because it still said
theirs was the stronger tool for a phone case on the strength of two
capabilities that have since been built. It now records what was verified
by running the program, what each tool still genuinely has over the other,
and a warning that this document has been wrong three times by being
reasoned about instead of tested.

## v157

**Media inside documents was already working, and the comparison document
said it was not.** Word, PowerPoint, Excel, OpenDocument and PDF are all
opened during an ordinary run and the pictures inside recovered, each
recorded against the document it came from — verified by running the
extraction over one of each rather than by reading the code.

**`docmedia.py` is deleted.** It was written in v141 to add this, before
anybody checked that the existing container scan already did it, and
nothing ever called it. Two implementations of one thing is a liability:
the one with no callers is the one that rots, and the one that gets fixed
is whichever the person looking happens to find.

Its test is replaced with one that runs the extraction over a folder of
documents and checks what comes out. A module that works and a program
that never calls it are not the same thing — a mistake made twice here now.

## v156

**Streamed video is joined by the vendored reader now**, not the older
built-in rejoiner. It resolves an item's name from the cache index instead
of leaving it numbered, and joins a DASH or HLS stream's segments in the
order the cached manifest lists them — which the older rejoiner, built for
contiguous pieces, left as fragments.

The older rejoiner is kept as the fallback rather than deleted: a cache the
vendored reader cannot read is no reason to recover nothing from it, and
both are exercised by the suite.

Still not done: separate video and audio streams are recovered as separate
files. The library can mux them into one playable file and this does not
yet use that.

Found while testing: a first version of the gap test removed the piece that
sorted to the middle **by name**, which is the last piece by position — so
it tested truncation, not a hole, and passed while proving nothing. Pieces
are ordered by position now.

## v155

**The phone modules are now actually used.** v141 and v150 added reading a
mobile extraction as the device, folding Android's mount-point views, video
locations and media inside documents — and wired none of it into the
extraction path. They had tests and nothing called them, so a real `.ufdr`
still recorded `files/Fs/1/...`, the vendor's packaging, as the path. Four
versions of a feature that existed only in its own test.

Now, from the ordinary run: every recovered file records **the path on the
phone** alongside the path inside the archive, and which tool's packaging
it was recognized as. Both go into the manifest, the exports and the
report, and the device's path is the one a report shows — it is the one an
examiner is asked about in evidence.

**One photograph seen through several Android mount points is one file**,
with the other spellings recorded against it rather than counted as three
more files and three "exact copies".

The comparison document is updated with what the mobile side does and, as
plainly, what it still does not: no DASH or HLS joining, no parsing of a
vendor's own report files, and no app artifacts beyond media — chat
databases and call logs are ALEAPP and iLEAPP's work, and better left to
them.

## v154

**A server started without `--store` used to invent an empty store and
serve it.** Every lookup against it would come back "not known" — the worst
way for a hash store to fail, because it looks exactly like a store that
works and simply holds nothing matching. It now refuses to start, and a
`--store` path that does not exist is an error naming the stores that *are*
in that folder, rather than a new empty file.

That is also where the stray `hash_store.db` in a data folder comes from:
the old default. The name itself means nothing — a store can be called
anything, nothing converts between names — and the setup guide now says so
instead of implying a required name.

## v153

**Content Credentials are now checked, not just read.** The COSE_Sign1
signature is verified over the claim, so an altered claim or a manifest
copied in from another file is caught — which was the main reason an
unverified credential meant so little.

Who signed it is reported separately, and deliberately. A valid signature
proves the claim is intact and was signed by the key in the file. It proves
nothing about whose key that is: anybody can make a certificate saying
Adobe, and its signature really does hold. So the result distinguishes
"signature valid, signer not trusted" from "signature valid, signer
trusted", and only the second follows from a certificate chaining to an
issuer in a trust list the examiner supplied. No trust list ships here —
one that arrives with a forensic tool and is never updated is worse than
none.

This is the one place the comparison document can now be amended: the other
tool reads a C2PA manifest and does not verify it.

## v152

**A personal hash stash**, beside the shared store rather than instead of
it. Every file an examiner categorizes goes in as they work, so the next
case meets it already known — without waiting for a contribution to the
shared store, which is a deliberate act made later and often after review.
It also works when the server does not.

**Where the stash and the store disagree, both are reported and neither is
applied.** Two sources that each say what a file is, with no sign of which
said what, is worse than one source; an examiner who graded something one
way last month and finds the store saying otherwise needs to know that, not
be handed one of the two answers. Every match names its source.

Found while building it: the first version stored nothing at all, because
the code referred to a variable that did not exist in that function and a
catch-all `except` swallowed the error. The handler now logs what it
caught. A grading is still never lost over the stash — but silently doing
nothing is not the same as not failing.

## v151

**Face-level search.** Everything else here works on whole files, and a
file is the wrong unit for the question asked after finding one picture
that matters: are there others of this child? A photograph can hold several
people, so the unit is one detected face. Detection is YuNet, description
is SFace, and neither model ships — Settings takes the paths. Both load on
OpenCV 4, unlike the content model.

The threshold is OpenCV's published 0.363, quoted rather than invented, and
every result carries its score. A score is not an identification: siblings
score highly, strangers who resemble each other score highly, two pictures
of one person years apart can score low, and the model was trained on
adults. It orders what to look at next and decides nothing.

**The report named a time zone and printed UTC underneath it.** A header
reading "Times shown in America/New_York" above timestamps four hours wrong
is a misleading line in a document that may go to a court, and it has been
there as long as the timezone field has. Times are now converted, with
their offset, and the header states what was actually done — including
saying plainly when a zone is one this machine does not know, rather than
claiming a zone that was never applied.

Daylight saving is handled by the zone database rather than a fixed offset:
New York in July is four hours from UTC and in December five, and an hour
is the difference between somebody being at work and being at home.

## v150

**Ctrl-B did almost nothing where it mattered most.** It toggled the review
blur, which is scoped to chosen categories — by default 1, 2, 3 and 9. An
examiner in Uncategorized pressing it got nothing blurred at all, which is
exactly the screen they most need hidden. Hovering a tile also lifted the
blur, and holding B revealed every blurred file on the page.

Hiding the screen is now its own thing: every picture and video, whatever
its category, lifted by nothing — not hover, not the B key, not selection —
with a banner saying so, and any playing video paused, because a blurred
clip still has sound.

**One photograph is not three photographs.** Android exposes the same
storage through `/storage/emulated/0`, `/data/media/0`, `/mnt/user/0/...`
and `/sdcard`, and an app's data through three more. Registering each
spelling multiplied a device's media by three or four. They are folded into
one file with the other paths recorded against it — except where credential-
and device-encrypted storage, different Android users, or views that do not
hold the same bytes mean they are genuinely different files.

**A video's location is read.** Phones keep it in the QuickTime structure,
not in EXIF, so until now videos had no coordinates at all and a case whose
pictures were placed and whose videos were not looked like a case whose
videos were taken somewhere unrecorded. Both layouts are read, all three
ISO 6709 notations, with the hemisphere signs parsed rather than guessed.

## v149

**An import stopped part way left a write-ahead log bigger than the store.**
A large import writes faster than SQLite folds its log in, so the log grows
during it and is folded in at the end — the same design the other tool
uses, for the same reason. The flaw was what happened when the program was
stopped before that last step: the log stayed, and every read afterwards
had to walk it. A store answering in minutes rather than milliseconds, with
nothing to say why.

Three changes. A checkpoint is **tried repeatedly** rather than abandoned at
the first refusal, since it fails exactly when another connection is busy —
the other tool retries ten times, and not doing so was my oversight. A long
import **folds its log in every two million rows**, so being interrupted
costs minutes rather than an afternoon. And **`store --checkpoint`** folds
in a log that was left behind, saying how much it moved and how long it
took, rather than looking hung.

## v148

**Every request to a shared store scanned the whole store.** Asking how
many hashes it holds ran `COUNT(*)`; asking what each set holds joined the
entire hashes table to the sources table and grouped it. Both are full
scans, and both run on every lookup, every admin page and every Verify — so
a store of tens of millions of hashes answered slowly, and on a
single-threaded server one slow request blocked everything behind it.

Both are now running totals, kept correct in the one place every write
passes through. Correcting them at each caller is how a path gets missed
and the store under-reports what it holds, which is worse than counting
slowly: the first attempt did exactly that, and the test caught it.

A store written before this fills its totals in the first time it is asked.

**The client waits five minutes rather than one**, and a timeout now says
what it means — the connection worked, the server was busy — instead of
"could not reach the hash store", which sends an examiner looking for a
network fault that is not there.

**And a port already in use says so.** Starting a second server printed a
traceback about sockets; it now names the port, explains it is almost
always this program already running, and gives the two commands to find and
stop it.

## v147

Three things from one run, and the third explains the other two.

**A run over a folder that holds an acquisition found nothing and said
nothing.** Pointing at `DC25-321 Image\` rather than at `DC25-321.E01` is
the commonest way to get an empty run, and "0 files over 0 B" looks like
the program failing rather than like the wrong thing being chosen. The run
now names the acquisition it found in that folder and says what to point at
instead.

**The ProjectVIC warning blamed the case number** when the real reason was
that nothing had been recovered — sending an examiner to fix the one thing
that was not wrong. It now says which it was.

**The image is let go of when a run finishes**, and **Release image** on
the File tab does it on demand: the choice, and anything still holding the
file open. An examiner finishing a case wants the drive back, and should
not have to close the program to get it.

## v146

**"New extraction" opened an empty screen.** The extraction form is the
left column of the workspace, not a panel of its own — filling it in and
watching the gallery fill up is one piece of work. v135 moved the workspace
into its own panel to fix having no way back to an open case, and left the
extraction panel empty behind the one button an examiner presses first.

It now goes to the workspace and puts the cursor in the source box.

**And the suite checks that no ribbon item can open an empty panel.** An
item that opens a blank screen is worse than one that is missing: it looks
like the program is broken. Restoring the empty panel makes the test name
it. It also fails if the script switches to a workspace that is not in the
markup at all.

## v145

**A build that runs with nothing installed.** `packaging/build.py` freezes
the program with PyInstaller, as a folder or as a single executable.

Two things decide whether a frozen build works, and neither is the code.
**Files the program reads at run time** must travel with it, or it starts
and then fails when somebody asks for the manual. **Modules imported by
name** must be declared, or the build silently loses QNX, E01 without
libewf, Content Credentials and streamed video — silently, because each of
those is written to carry on when its reader is absent.

Both are now checked against the repository by `tests/test_packaging.py`,
which finds the by-name imports in the code rather than from a list
somebody has to maintain. It also fails if a vendored reader ships without
its licence, which is the condition its code may be used under at all.

**`fme/resources.py`** finds shipped files whether the program was started
from a checkout, a one-folder build or a one-file build that unpacks itself
somewhere new on every start. Code that walked up from `__file__` worked in
the first case only, and failed late in the others.

**Not built or run here**: PyInstaller cannot be installed in this
environment, and a Windows executable is built on Windows regardless. What
is tested is everything that decides whether the build will work; the build
itself is yours to make.

## v144

**"database is locked" from a server that was never restarted.** The
waiting added in v127 is in the server, and a server keeps running the code
it started with — so a unit that updated the program folder but left the
service up still gets the old refusal. There was nothing to tell an
examiner that is what had happened.

The client now reports the server's version, says when it is older than the
program talking to it, and translates an old server's own words into what
they mean and what to do: the store is busy, nothing is lost, and the
server needs restarting from the updated folder. **Verify** shows the
version every time.

The setup guide gains a short section on restarting the service after an
update, because replacing the folder is not enough and nothing said so.

## v143

**File gains a "Set up once" group**, separating what outlives a case from
what belongs to one: the hash store, hash sets, the category scheme and the
agency logo. Keeping those apart from a case's own controls is the
difference between a setting and a decision, which is the idea worth taking
from how the other tool arranges its start screen.

**An agency logo** on every report header unless a case gives its own. A
file that does not open as an image is refused in the dialog rather than
discovered in a report, and a logo that has gone missing costs a line in
the header rather than the report.

**`doctor` reports the OpenCV version and what it enables** — edited-copy
matching needs 4.4, the content model needs 5.0 — and, where a model is
set, whether it will load on this machine. Two capabilities that an
examiner otherwise cannot tell apart from one line saying "cv2: yes".

## v142

**Settings takes the path to the image model**, checks it when it is set,
and says what is wrong in terms of what to do about it.

The model this was written against — DINOv2-small, 88,411,480 bytes,
Apache-2.0 — is recognized by its SHA-256, so a wrong or truncated file is
caught before a search returns nonsense. And the one failure worth naming
is named: **OpenCV 4 cannot load it.** It stops on a node the model uses
and raises "error in handleNode", which tells an examiner nothing; Settings
says it needs OpenCV 5.0 and gives the pip line.

Found by trying the real model file rather than assuming it would load.

## v141

All four phone-case gaps from the audit.

**Mobile extractions read as the device.** Cellebrite `.ufdr`, GrayKey,
Magnet, Android `adb` backups and tar. The path on the phone is worked out
from the vendor's packaging and is the one shown — a report citing
`files/Fs/1/...` is citing the tool, not the device. Members with no
extension are classified by their bytes, the vendor's own report folder is
left out, and members are streamed so a 60 GB extraction does not need
60 GB of scratch space first.

**Pictures inside documents.** Office and OpenDocument media recovered;
PDFs delegated to the reader this program already had. An earlier draft
rebuilt raw-pixel PDF images into PNGs — that was wrong and was removed:
the existing reader refuses to, because a rebuilt file's hashes belong to
this tool rather than to evidence.

**Video an app streamed**, rebuilt from ExoPlayer's fragments by the
vendored `exoprobe` (MIT, Alexis Brignoni, 0.2.1), which also joins DASH
and HLS segments in manifest order. Verified byte for byte against the
video that was cached. Short items are called short only on evidence of a
gap or a recorded length, never guessed.

**Search by what is in a picture**, which **needs a model this program does
not ship**. The engine refuses without one rather than pretending, and
Settings takes a path. A score is a rank, never an identification, and no
category is applied from it.

## v140

**Finding a picture after somebody edited it.** A perceptual hash describes
the whole frame, so a crop, a rotation, a caption bar or a paste into a
screenshot defeats it — and those are what happen to a picture as it is
traded. This matches the distinctive points inside two pictures and accepts
the match only when they agree on one transform.

Measured: **eleven of eleven edits found** — re-saved, resized, cropped,
rotated 7° and 90°, mirrored, bordered, captioned, pasted into a
screenshot, made grayscale, brightened — and **none of eight unrelated
pictures wrongly matched**.

The first threshold I chose accepted two of those eight. Rather than adjust
it by feel I measured where true and false matches sit: every true match
kept at least 58 points through the transform, the worst coincidence kept
17. The threshold is 25, in the gap, erring high — a missed copy is a file
an examiner still sees and still grades, a false match is a file they are
told is the same picture.

It finds the same picture, not the same subject. Two photographs of one
person are not a match here, and the interface says so where it reports
results.

## v139 — documentation only

**The comparison is re-done against the other tool's 2026.5.6 source**, read
module by module rather than from its release notes, and rewritten to say
plainly where each program is ahead.

The largest gap it records is theirs: content search by what is *in* a
picture, which finds other pictures of the same subject rather than copies
of the same picture, and near-duplicate matching that survives a crop or a
paste into a screenshot. Both are capabilities this program does not have.

The largest gaps the other way are recovery of what the file system no
longer lists — including resident data that carving cannot reach at all —
a shared hash store with per-user tokens and an audit trail, and putting a
case back from that log after a mistake.

## v138

**Close case**, on the File tab: writes a snapshot, clears the case and
returns to the list. Decisions are on disk as they are made, so the
snapshot is a marker rather than a rescue — but closing with no sign of
having saved leaves an examiner wondering, and wondering is the thing worth
removing.

**Content Credentials now use the vendored `c2pareader`** (MIT, Alexis
Brignoni, 0.2.0) in place of the parser written for v137. Theirs reads
seven container formats against v137's four, reads the XMP beside them, and
reports the stated signer, issuer and signing time.

It replaced the version written here for a reason worth recording: given a
hand-built manifest whose JUMBF type was not a real C2PA store, **theirs
refused it and v137's accepted it**. A reader that accepts not-quite-C2PA
will eventually call something a credential that is not one.

It also draws a distinction v137 could not: a file walked to its end with
no manifest is different from a file that could not be read to its end, and
only the first makes "no credentials" mean anything. Both are reported.

Vendored under the same terms as the other two readers, with its licence,
its upstream, and its SHA-256 recorded and checked by the suite.

## v137

**Content Credentials (C2PA).** Reads the manifest a file carries about
itself: what made it, what was done to it, and the field that matters most
here — whether it says it was made by a trained model. Every major image
generator writes that, so a picture somebody has called AI-generated can now
be asked directly.

Read from JPEG (APP11), PNG (caBX), HEIC, AVIF, MP4 and WebP, with a CBOR
decoder written for it rather than a dependency added: sixty lines against
an examination machine that cannot read credentials until somebody is
allowed to install something.

**What it will not claim.** The signature is not checked — that needs a
certificate chain and a trust list this program does not carry — so every
result says so, in the details pane, in the count, and in the record kept
with the case. A manifest can be copied between files, and a file without
one has not been shown to be anything.

Taken from the other tool's 2026.5.6 release, of which this was the one
feature worth having: their regional category schemes we largely have as
custom schemes, their sparse-copy fix does not apply here, and their
"clear category uses the case's own Uncategorized code" is already how this
program works.

## v136

**The full size viewer zooms.** Scroll to zoom where the pointer is, drag
to move, `+` `−` to step, `0` to fit, `1` for actual size, double-click to
toggle. The arithmetic keeps whatever is under the pointer under the
pointer, which was checked against the figures rather than by eye: a pixel
at the cursor stays at the cursor through 2x, 4x, 8x and back down.

**Ctrl-B blurs everything on screen** — gallery, details pane, and the
picture open full size — from anywhere, with no mouse. For when somebody
walks up to the desk. It hides the pictures from the room; it changes no
file and no recorded decision.

Added to the existing key handler rather than beside it, and the suite
checks the grading keys still work: a keyboard change has broken them twice
before.

**The comparison document no longer names the tool it compares against.**
A document an examiner may attach to a case gains nothing from naming a
competitor. The one remaining mention is inside the vendored reader, which
is third-party code whose hash is verified — changing a word of it would
break that check and misrepresent what its author published.

## v135

Everything reported against v134, fixed.

**There was no way back to an open case.** The gallery lived inside the
extraction panel, so opening the hash store hid the case and only opening it
again brought it back. It has a workspace of its own now, with **Back to the
case** on File, Home and Case Data, and opening a case goes straight to it.

**Hex view, Full size and Find similar did nothing from the ribbon.** All
three take the file; the ribbon handed them its path. Right-clicking the
same picture worked because the context menu passes the file itself.

**Project VIC, LAVA, C4All and KML looked like they did nothing.** They ran
and wrote their files; they reported where into the extraction panel, which
is hidden while you are on the Report tab. Results now appear in a line
under the ribbon that can be read from any tab. C4All and LAVA also had
their answers read from the wrong key, so there was nothing to show even
where you could see it.

**New extraction looked inert** when the case was already on screen; it now
says what to do next.

**Hide known software, Show containers and Stack similar say what they
did** — each changes a count somewhere off screen, and "something happened
and I am not sure what" is not a state to leave an examiner in.

**The comparison document is no longer named after another tool** in the
ribbon: it reads *Limits & comparison*.

## v134

**The ribbon had no styles at all.** The rules were written against an
anchor that did not match, so none of them reached the stylesheet — the
markup was right and the script was right, and the page came out as a
column of plain text: every tab and every item on its own line, nothing
clickable-looking, nothing laid out.

The rules are in `<style>` now, inserted by position rather than by
matching text that may have changed.

**And the suite checks it.** Every class the markup uses or the script adds
must have a rule in the stylesheet, barring the few that exist only for the
script to find elements by. Removing the ribbon rules again makes the test
list all twelve of them by name.

That is the third edit in this interface to miss its anchor and do nothing.
The checks now cover all three ways it shows up: an element that is never
filled, a function that is never defined, and now a class that is never
styled.

## v133

**Fixes v132, which did not work at all.** The ribbon was drawn before the
variables it reads were declared, so the script threw as the page loaded —
and a throw at the top level stops every line after it. The page still
rendered, because the markup is already there, and not one button worked:
Refresh, New extraction, Browse, Scan, all of them dead.

The ribbon is drawn last now, after everything it reads exists, and inside
a guard so it can never take the rest of the program down with it again.

**And the test that would have caught it now exists.** Everything else
about the interface was checked by reading it — elements present, functions
defined, handlers wired — and none of that notices a throw at load, because
the code is all there and all correct in isolation. `tests/test_gui_boot.py`
runs the page's script against a pretend browser and fails if it does not
reach the end. Restoring v132's bug makes it say "Cannot access
'selectedPath' before initialization", which is exactly what an examiner
would never have been told.

**A title bar**, with the program's name and version. In a browser tab the
window has no title bar of ours, and somebody reporting a problem has to be
able to read the version without hunting for it.

## v132

**A ribbon, in place of the tab bar and the drop-down menu.** Six tabs —
File, Home, Case Data, Report / Export, View, Apps — each holding groups of
what can be done, in the order the work happens. Items that need a case or
a selected file are dimmed until there is one.

Everything on it calls something that already existed; a ribbon offering
what the program cannot do would be worse than none. Four things it needed
were written for it: a **Settings** dialog for what this machine remembers,
**What is installed**, the **audit log** moved off the old menu, and
**exportAs** for the Project VIC, KML, C4All and LAVA formats.

All forty-seven items were checked against the code, and the fourteen
endpoints behind them against a live case: all fourteen answer.

The old drop-down menu is removed rather than kept alongside.

**The test for undefined functions missed three of these**, because the
ribbon refers to them (`go: saveCase`) rather than calling them. It now
resolves references as well as calls, and counts a function's own
parameters as defined — which had been a false alarm waiting to happen.

## v131

**Exported files are split by kind as well as category**:
`Child Abuse Material (CAM) Images` beside
`Child Abuse Material (CAM) Videos`, which is how a submission to NCMEC is
asked for. It also makes the counts checkable — a folder of 703 files should
be the 703 the report says. The split can be turned off.

**And folder names keep the punctuation Windows allows.** The old sanitizer
replaced anything that was not a letter, digit, dot, space, dash or
underscore, so "Child Abuse Material (CAM)" became "Child Abuse Material
_CAM_" — harder to read and harder to match against the report beside it.
Only what Windows actually refuses is removed now.

## v130

**The report's tables read in category order.** They were sorted by how
many files each category held, so the order changed from case to case and
the serious categories could appear below the trivial ones. They now follow
the unit's own order — the order of the category bar and of the keys an
examiner presses.

Non-Pertinent and Uncategorized are listed after the rest. They say what was
not a finding, and putting them first puts the answer below the things that
are not the answer.

## v129

**The report's category picker never appeared.** v125 added the option to
the scope list and the box to the page, but the code that fills the box with
categories was written against an anchor that no longer existed, so it was
silently not applied. Choosing "only the categories I choose" revealed an
empty space, which is worse than the option not being there: it looks like
the feature exists.

It is wired now, built both when the scope is chosen and when the dialog
opens, with each category's count beside it.

**The test suite now catches this shape of fault**: every container the
script fills with `innerHTML` must exist in the markup, and the picker in
particular must have something that builds it and something that sends the
choice. A control that exists but is never filled is the kind of thing that
passes every test and fails the first person who uses it.

## v128

**A protocol report**, the document a case is handed over with: the case
header, an introduction, the conclusion and video-length tables, counts per
device where there is more than one, counts per flag within each category,
a signature block, and appendices — representative images, definitions and
the file list. The shape follows what examiners in this field already read;
the wording is this program's own, and the introduction and definitions can
be replaced in the dialog.

**Exporting the files themselves**, from the menu, separate from the
report. One folder per category, every copy hashed as it is written and
checked against the hash taken at carve time, and a manifest beside them
recording what each file is. It refuses to write into a folder that already
holds files.

Word tables had to be built for this. They carry the column grid that Word
renders without and other readers refuse the document over — a report that
opens for the examiner and not for the prosecutor is worse than none.

## v127

**"database is locked" while a set imports.** SQLite allows one writer at a
time and an import of a national set holds it for as long as the import
runs — but a second write gave up instantly rather than waiting, so
contributing a case during an import failed with a message that reads as a
fault when it is a queue.

A store now waits for the writer: locally up to five minutes, which covers
an import. The server waits fifteen seconds and then answers that the store
is busy, because a request that hangs for minutes only moves the failure to
the other end, where it arrives as a timeout and says nothing. Either way
nothing is lost — the case is added when the import finishes.

The message says what is happening rather than what SQLite calls it, and
names the other possibility: something else holding the store open.

## v126

**Tiles say what they stand for.** A badge for identical copies found
elsewhere, and a separate one for files that merely look alike. Two badges
rather than one number, because they are different claims: the same bytes,
against a perceptual hash's judgment that two pictures resemble each other.

**Stack similar** collapses a group of look-alikes to a single tile, drawn
with another behind it. **Grading that tile grades every file in the
group** — a stacked tile that graded only the one shown would leave the rest
ungraded behind something that looks dealt with, which is worse than not
stacking at all.

Taken from how the other tool does it, including the part worth copying:
their collapsed tile also categorizes its whole group.

## v125

**A report can be limited to the categories you choose.** Scope → *Only the
categories I choose*, with every category listed and its count beside it. A
report that goes to somebody else should not carry the Non-Pertinent files,
and deleting them afterwards is not a workflow. The choice covers the files,
the thumbnails and every count; the front page records it.

**Every report now opens with a Conclusion table**: per category, images and
videos found, how many are different files, and the total — files and unique
files side by side, because twenty copies of one picture is twenty files and
one picture, and a report giving only one number invites the wrong reading
of both.

**And an Overview of video length**: longest, shortest and total per
category. Durations are not in the manifest — a carved clip's length is
found by decoding, not by reading a header — so they are measured once and
kept with the case. A clip that cannot be decoded has no length rather than
a length of zero, is left out of the totals, and is counted and explained
beneath the table.

Found while building it: the media kind was being overwritten by the
grading, both living in a field called `category`. Every video therefore had
no length at all. They are separate fields now.

## v124

**The store connects by itself at startup.** v123 remembered the settings
but still wanted a button pressed, which is a step that exists only because
the program forgot. The Hash Sets tab now opens the store this machine used
last time and reports what it found; a store that cannot be reached is said
plainly rather than thrown as an error, since it may not be needed for the
work in hand.

**Remembering the token is on by default**, which is what makes that
possible, and the box says what it costs: the token is readable by that
Windows account. On a shared machine, turn it off.

**And a real bug in v123's settings.** Saving anything else — the store path,
for instance — wiped the remembered token, because "no token given" was
written as an empty one. The setting survived a restart as a blank, so an
examiner who had ticked the box was asked for the token again anyway. Only a
token actually given replaces the stored one now.

## v123

**An examiner sets the hash store up once, not once per session.** The
address, the certificate path and the examiner's details were held in memory
only, so every restart of the program meant typing them again — which is not
security, it is friction, and friction is how a token ends up in a text file
on somebody's desktop.

The address and the certificate are now remembered on that machine: neither
is a secret. **The token is remembered only if the examiner ticks the box**,
because it is their identity on a store full of hashes of child abuse
material and writing it down is a real reduction in security that some units
will accept and others will not. Unticked, it is not written anywhere — the
test reads the file to confirm the token is not in it at all. The file says
what it holds, is readable by that account only, and **Forget** clears it.

Settings live with the examiner's per-user data, not in the program folder —
a program folder is shared, replaced on update, and sometimes on a share.

## v122

**The hash store server runs on cheroot when it is installed**, which
serves TLS from a pool of threads. Until now a certificate meant falling
back to Flask's own server — the one that prints "This is a development
server" — because waitress cannot terminate TLS. That server is
single-threaded: one examiner scanning a large case made every other lookup
wait behind it.

`pip install cheroot`, and the warning goes with it. Without it everything
still works over TLS, on the old server, and both startup and `--check` say
which is in use and what to install.

`doctor` reports it too.

## v121

**`doctor` checks for the certificate library.** It listed everything
needed for examining and nothing needed for serving, so a machine missing
`cryptography` looked ready and then failed at the step that makes the
certificate — several steps later, with the reason far from the cause.

The setup guide now also says which lines matter on a server: flask,
waitress and cryptography. The rest are for reviewing.

## v120 — documentation only

No code changed. The setup guide gains **Part 10b — Moving an existing
setup to a local disk**: twelve steps for a store already running from a
mapped drive, in the order they have to happen, with the two checks that
decide whether to carry on or stop. Nothing is deleted until the new setup
has served a case.

It also says what does *not* move: evidence and case output belong on the
RAID. A case folder is read and written by one examiner at a time, which a
share handles. A hash store is written by several machines at once, which a
share cannot do.

## v119

**`store --move-to` gets a store off a network share safely.** Copying the
file in Explorer is how a store arrives missing its most recent imports:
SQLite keeps recent writes in a journal beside the database, and a copy
taken without it is older than it looks, with nothing to show anything is
missing. This copies through SQLite itself, counts both, and refuses to call
it done unless they agree.

It leaves the original where it is — moving evidence is done by hand, after
the copy has been seen to be good — refuses to overwrite anything, and warns
if the destination is on a share as well.

Tested the case that matters: a store with six thousand hashes still in its
journal, copied while open. All six thousand arrived.

## v118

**The server now says what to open, and why the browser refused.** Starting
it prints the exact address including the scheme, and says plainly that it
must be `https` and not `http` when a certificate was given. A browser
reports "refused to connect" whether the server is absent, on another port,
or simply speaking the other protocol — it cannot tell them apart, so the
server does.

**`--check`** starts the server, talks to it, and reports what is wrong: the
files it needs, whether the store opens, whether the port is free, whether
it answers, and what it said if it did not.

**And a warning about mapped drives at startup.** A drive letter mapped to a
share belongs to a logged-in session, so a scheduled task starting the
server at boot has no such drive and the server exits at once — which looks
exactly like the task not running. The setup guide now says how to tell a
mapped drive from a local disk (`net use`), to use a full `\\server\share`
path instead, to set **Start in** on the task, and to log the batch file's
output so a silent failure is not silent.

## v117

**Opening a store no longer invents one.** A path holding no store had an
empty store created there and reported as the store, so an examiner who
typed `hashes.db` where the import had written `hash_store.db` was told
"nothing in store yet" and went looking for a fault in a store that was
never theirs.

A path with no store is now refused — and the refusal **names the stores
that are in that folder**, with their sizes, which is the answer rather than
the dead end. A database that is not one of ours is not offered: the file is
checked for this program's own tables.

**The intermittent test failure is found.** Asking what the store is — the
status the Hash Sets tab reads when it opens — went through the same
refusal as choosing one, so with no store file present it answered 404 and
the audit failed. Whether a store file was present depended on what an
earlier test had left behind, which is why it failed in a batch and passed
alone every time.

"No store yet" is a state the tab has to be able to show, so the status call
answers it; choosing a store that is not there is still refused, because
that is a deliberate act with a mistake in it.

Two things found on the way: `test_endpoints.py` bound a fixed port, so
anything already on it failed the test for a reason unrelated to what it
tests, and its wait for the server to start was short enough that a busy
machine could time it out. It takes a free port now, waits properly, and
says why if the server never answers.

## v116

**"disk I/O error" now says what it is.** A store on a mapped drive or a UNC
path is a SQLite database on a network share, and SQLite needs file locking
that shares implement badly or not at all. The error said nothing an
examiner could act on; it now names the cause and the way out — keep the
store on a disk in the machine that holds it and reach it with the server,
which exists for exactly this.

A share is recognised before that happens, too: pointing the store at one
warns immediately rather than after the first failure. And a store that is
on a share opens without write-ahead logging, which is the specific thing
shares cannot do — slower, but it will not corrupt quietly.

**The setup guide said `hashes.db` where the program defaults to
`hash_store.db`.** The name is whatever is passed to `--store`, but a guide
that disagrees with the program is a guide that wastes an afternoon. Both
say `hash_store.db` now, and the troubleshooting table explains the rule.

## v115

**Verify works on a server.** It opened the store's path as a file either
way; for a server that path is a URL, so the report came back without the
fields the page reads and the examiner got "Cannot read properties of
undefined (reading 'toLocaleString')" — a message about this program's
internals in place of the answer they asked for.

It now asks the server, and reports what the store holds, who you are signed
in as and what that role may do: one press confirms the address, the token
and the certificate together.

**And no count is formatted without checking it is there.** One shared
helper does it, and the suite now fails if a response field is formatted
directly. It found a second site waiting to do the same thing in the
Project VIC panel.

## v114

**There was nowhere to give the server's certificate.** The setup guide said
to enter one and the client had always accepted one, but the Hash Sets tab
had no box for it — so a lab with its own certificate could not connect at
all, and "Use this store" appeared to do nothing. There is a box, with a
Browse beside it, and a path that does not exist is refused by name rather
than silently.

A server address with no token is now caught here rather than as a refusal
from the far end.

**The Hash Sets tab is three numbered steps**: connect to the store, check
this case against it, add this case to it. The similarity tools were
interleaved with those, which is why examiners could not tell which control
did what — they are on a **Similar Media** tab of their own now, with the
still-to-video matching and the screening, because they belong to reviewing
rather than to hashes.

A markup fault went with it: the token field sat outside its row, so the
first section never lined up.

## v113

**Fixes the broken video tiles from v110.** The video made on hover had no
style rule, so the browser laid it out as an ordinary inline element:
inserted after the picture rather than over it. The tile showed the frame,
then the filename row, then the video below — unusable for judging a clip,
which is the whole point of scrubbing.

The rule exists now, and it positions the video inside the **thumbnail**,
not the card: inside the card it would cover the filename row as well. Both
are framed the same way, so nothing jumps when the cursor arrives.

**The same fault was in the details pane**, found by a new check rather than
by using it: every class the script creates is now resolved against the
style sheet, and an unstyled one fails the suite. `.scrubshot` had no rule
either and would have stacked in exactly the same way.

## v112

**The top bar reads as one thing now.** The program's name and version sit
in the middle of it; the menu and the four tabs are on the left; what is
running is on the right.

**Save case and Export report moved into the menu.** Beside the tabs they
read as two more tabs, which they are not: the tabs are places to be, and
those two are things done to a case occasionally. They are the first two
entries in the menu, above where-to-go-next, because they are why somebody
opens it while working.

**The menu is no longer a button.** It opens a list, so it looks like
something that opens a list: text and a caret, lit while the list is open.

## v111

**One bar across the top.** The title and version had a row of their own
above the tabs, spending a line of screen on something nobody reads twice.
That row is gone: the menu, the four tabs, Save case and Export report now
sit on one line, with the version on the right — where somebody reporting a
problem can still read it.

## v110

**A video tile no longer contains a video.** Every video tile carried a
`<video preload="metadata">`, so a page of five hundred clips was five
hundred media elements, each fetching its header and holding decoder
resources. Browsers cap how many they keep and start tearing them down — a
large part of why a page of video became unresponsive. The tile is a picture
now; a video element is created when the cursor arrives and released when it
leaves, one at a time.

This came from reading how the other tool does it, which creates its video
on hover for the same reason. Where it seeks the real video and falls back
to key frames, this does both in the other order: the frames answer
immediately, and the video takes over for precision once its header has
loaded — so there is no wait at the start, and no limit to twelve positions
once there is a video to seek. A codec the browser cannot decode still
scrubs on the frames.

## v109

**`gui --app` opens the interface in a window of its own**: Chrome or Edge
with a separate profile, no extensions, no tabs and no address bar.

This is not a different rendering engine, and there is no honest way to
offer one — the tools that look like native applications are the same engine
with the window furniture removed. What it removes is everything else that
shares the page: extensions injecting script into a gallery of thousands of
tiles, other tabs competing for memory, and the cost of a crash taking out a
day's browsing along with the review.

## v108

**Frames are fetched before the cursor reaches a clip**, so the first hover
no longer waits. Only for tiles actually on screen, two at a time, asked for
once each, and the queue is abandoned when the page changes — scrolling past
a hundred videos must not queue a hundred decodes.

**And the decoding runs at a lower priority than this program.** The first
measurement of the prefetch showed grading slowing to 80 ms a keystroke,
worst case 800 ms: frames for clips nobody had hovered were competing with
the work an examiner was actually doing. Started below normal priority, the
same measurement gives 6 ms average and 12 ms worst — and the prefetch
finished slightly sooner, because it stopped fighting for the same cores.

## v107

**Scrubbing a video no longer hangs the browser.** It set the video's
`currentTime` on every mouse move: dozens of seeks a second, each a range
request to this program and a decode in the browser. Carved video is
malformed by nature — a carve rarely recovers a clean container — so the
decoder falls behind, the queue grows, and the tab stops responding.

The video is no longer seeked at all. The frames this program already
extracts are fetched once per clip, cached on disk, and swapped under the
cursor: an image change and nothing more. A clip with no frames still falls
back to seeking, but throttled to one update per drawn frame and never more
than one seek outstanding.

## v106

**`doctor` stopped telling people to install things they no longer need.**
It listed pyewf, pyvmdk, pyvhdi and pyqcow as absent, against the formats
they read — all of which the vendored reader now opens with nothing
installed. Each of those lines says so, and a new block lists what the
vendored readers cover: the acquisition formats, the file systems, and the
version and licence they are used under. If `fme/vendor/` is missing, it
says that instead, and what is lost.

## v105

**Every iPhone photograph was being classified as a video.** mp4, HEIC, AVIF
and M4A all begin with the same `ftyp` box, and the brand inside it says
which. The carver read that brand; every other path that classifies a file
did not -- the file system engine, the folder engine, a zip's members, a
deleted MFT record -- so a HEIC photograph went to `videos/`, was filtered
as a video, scanned for frames and reported as a clip. HEIC, HEIF and AVIF
are images now, M4A is audio, and mp4, mov and 3gp are unchanged.

**MIDI is recognized**, which nothing here did before.

Both were found by comparing this program's carver against `mediacarve`,
the third of the probes -- **which is not vendored**. It carves nothing this
program does not, and this program carves 28 formats it does not, so
vendoring it would mean two carvers answering one question and a third set
of licence obligations for no capability. The comparison was worth doing;
the code was not worth taking.

## v104

**The vendored acquisition reader is now used for far more than `.E01`.**
The copy sent was identical to the one vendored in v103 — same version,
same hash, byte for byte — so nothing needed re-vendoring. What was missing
was using it.

It now opens AFF, AFM, AFD, AFF4, Apple `.dmg`, `.sparseimage` and sparse
bundles, FTK `.ad1`, and VHD, VHDX, VMDK and QCOW virtual disks — none of
which this program could open at all. And where a libyal C extension is
absent, which on a Windows examination machine is usual, an `.E01` opens
through it instead of refusing. Where libewf is installed it is still used:
it is the reference implementation.

Verified by building a fixed VHD around the test image: it opens through the
vendored reader, reads the disk byte for byte, matches on spot reads at
offsets, and a full run over it recovers the same files with their file
system paths.

## v103

**File systems this program could not read, now read.** `qnxprobe` and
`ewfprobe`, written by Alexis Brignoni and used under MIT, are vendored
verbatim under `fme/vendor/` with their licence texts and a provenance
record — version, upstream, SHA-256.

`qnxprobe` adds QNX6, QNX4, ETFS, EFS and QNX IFS (vehicle systems), F2FS
(Android from about 2019), and SquashFS, JFFS2, UBI/UBIFS and YAFFS. On any
of those this program previously found nothing and carving recovered the
pictures with no name and no date. It runs only for the kinds nothing here
reads: a FAT32 or NTFS volume is still read by the engine, because two
readers for one volume is two answers to the same question. `ewfprobe` makes
an `.E01` readable without libewf, which on Windows rarely installs.

**This is the first time this program names another project**, which is a
deliberate trade: MIT permits the use and asks that the copyright notice and
permission text travel with the code. They do, in `fme/vendor/`, in the
manual's new "Code from other people", and in what the program reports about
itself. `tests/test_vendor.py` fails if a licence file disappears, a
copyright notice is stripped, or a vendored file stops matching its recorded
hash — so a release cannot quietly breach the terms.

## v102

**Video is decoded in a process of its own.** The decoder is FFmpeg under
OpenCV, in C: a malformed stream can crash it outright, and carved video is
exactly the input that provokes that. One bad clip could take down the
server, the run and the open review. Now a crash is a fact about the file —
recorded, with the signal that killed it — and everything else carries on. A
hang is bounded at two minutes.

Built with a subprocess rather than multiprocessing, after the first attempt
broke frame matching: "spawn" re-imports the caller's main module inside the
child, so any script calling this without an if-__main__ guard runs itself
again. That is a trap to leave for whoever uses this next.

**The gallery leaves out containers.** A zip or cache that other media came
out of is not a picture, and its contents are already listed one by one. It
keeps its hashes and stays in the manifest and reports; **Show containers**
brings the tiles back. Fixing this also fixed a real gap: a stored archive
was not being opened at all when artifacts were being kept, so its pictures
were never recovered.

**Hide known software**, one click, for files matching a known-software list
such as the NSRL. The wording is deliberate: a hit means recognized, not
irrelevant.

## v101

**A Save case button**, beside Export report. Everything already commits as
it happens, so there is nothing waiting to be written — but the button does
something real rather than reassuring: it writes a snapshot of the case as
it stands and names the file, so finishing for the day leaves a point to go
back to. A button that only said "saved" would teach people to trust a
no-op, which is worse than having none.

**The list of file systems has left the header.** It sat beside the report
button, where it told an examiner mid-review nothing they could act on. It
now sits in the extraction panel, next to where evidence is chosen, which is
the moment it matters.

## v100

**Stills can find the videos they came from.** Each video's key frames are
perceptually hashed and the run's stills matched against them, so a
screenshot finds its source — down to which frame — and a video shows what
was taken from it. Both directions appear in the details pane; the result is
kept with the case.

A picture and the video it came from share no bytes, so nothing else in this
program could relate them. Verified on a frame saved as a JPEG and on the
same frame resized and re-compressed, which is what sharing does to a
picture: both matched at 4 bits, well inside the strict threshold, and an
unrelated picture did not match at all. Featureless frames are excluded, for
the same reason featureless stills are: a flat frame matches every other
flat frame, and a tool that answers yes to everything has said nothing.

The caution travels with the result: a match is the same picture to the eye,
not the same file, and a video is a dozen frames out of thousands — so a
miss means far less than a hit.

## v99

**Cases can be moved, and the list keeps up.** Move a case folder anywhere —
a share, a central folder, another drive — and **Scan** that folder, or
`fme cases --relink`. A case that has moved is recognized rather than added
twice: it is identified by what the run was taken from, its file count and
the hashes at each end of its manifest, so the existing row is pointed at
the new place and its case number, examiner, gradings and history travel
with it.

A different case found in the same folder is added as a new one. Cases from
any earlier version work, since the identity comes from the manifest.

## v98

**Deleted files from the NTFS master file table.** A pass of its own, run on
any NTFS volume unless deleted entries are being skipped. A record marked
unused still carries the name, the path, the dates and the size, so a file
recovered this way is something a person can be asked about — where a carved
file is content with nothing attached.

It reaches two things carving cannot. **Resident data**: a file small enough
to live inside its own record never occupied a cluster, so free space has
never held it. And **orphaned records**: a file whose parent folder was
itself deleted is unreachable by walking directories, while the record still
has its name.

Written from the format rather than through a library: boot sector geometry,
update-sequence fixups (without which the last two bytes of every sector are
wrong, and resident data is exactly what straddles those boundaries),
attribute parsing, data runs including sparse ones, and parent-reference path
reconstruction.

Each recovered file records **how** it was recovered, because that decides
how much the content can be relied on: resident data is exact, cluster data
is what is there now.

`tests/make_ntfs_test_image.py` builds an NTFS volume byte by byte — a
deleted resident file, a deleted clustered file, and a live file — so what
should come back is known exactly. Both deleted files come back byte-exact
with their paths and dates; the live file is not reported; and the carver
finds neither of them, which is the point.

## v97

**The side pane lists categories and flags in full.** They were capped at a
fixed height with a scrollbar, so ten rows scrolled inside a pane with room
for forty. Short, known lists — categories, flags, file types, size bands —
are shown whole; only the open-ended ones, where a run can produce hundreds
of camera models or extensions, scroll, and only once they are long enough
to need it.

**An audit against the other tool in this space**, `docs/AUDIT.md`, readable
from the menu: every capability checked against our own code rather than
remembered, what each gap would cost, and what I would do in which order.
Also what we have that they do not.

## v96 — documentation only

No code changed in this release. The manual gains a section of its own,
**7. Undoing a mistake**, covering undo and ctrl-Z, the guards that come
before them, `fme recover` for a grading made before undo existed, which
folder to give it — `cases.db` is the list of cases, `case.db` is one
case — and the snapshots in `backups/` if neither reaches it.

It was previously a few paragraphs buried in "Selecting many", which is not
where anyone looks when they have just graded the wrong files. The later
sections are renumbered to make room, and the troubleshooting table now
points at it.

`tests/test_manual.py` checks the section exists, that every `recover`
option it tells someone to type is one the program accepts, and that the
`cases.db` against `case.db` distinction is spelled out.

## v95

**Recovery takes a case number, and explains itself when pointed at the
wrong folder.** Two files in this program have names a keystroke apart:
`cases.db` is the list of cases and sits beside the program, `case.db` is
one case's review and sits in that run's output folder. "No case.db in that
folder" is not an answer to somebody looking at a folder with `cases.db` in
it.

It now says which file is which, lists the cases it knows about with their
folders, looks one level down for cases inside the folder given, and prints
the command to run next. `--list-cases` shows them all, and `--case` accepts
a case number as well as a path.

A case is offered for recovery on the strength of its `case.db` alone: a
case whose media has been moved away still holds its decisions, and those
are what is being put back.

## v94

**Recovery for gradings made before undo existed.** v93's undo only knows
about gradings it recorded, which is no help to a case where the mistake was
made in an earlier version. The audit log has always recorded every
grading — file, category, who, when — so the state before any event can be
rebuilt by replaying what came before it.

`fme recover --case <folder>` lists the history as the acts that made it:
one keystroke is one event however many files it touched, so a bulk mistake
is a single line. `--undo <n>` takes it back, returning every file to what
it was — including back to having no decision at all, which is a different
claim from "uncategorized" and is restored as such. A snapshot is written
afterwards, and the recovery is itself in the audit log.

Tested on a case of 160,000 files with the undo history deleted, as an older
version would leave it: restored exactly, in under six seconds.

## v93

**Undo.** Every grading now records what it replaced, so it can be put back
— the button in the toolbar, or ctrl-Z. Each file returns to the category it
held before, including back to having no decision at all, which is the case
that matters when a key was pressed with the wrong files selected. The last
fifty gradings can be taken back, a step at a time.

**Ctrl-A selects the page, not the whole case.** In a gallery it means
"everything I can see" to anyone who has used one, and it meant every file
in the case — so one keystroke afterwards could overwrite a morning's work.
Selecting everything the filter shows is still available as a button, which
says how many it is selecting.

**An action covering more files than the page asks first**, naming the count
and the category. Within the page it does not ask: that is the normal work,
and a dialog on every keystroke is its own kind of harm.

## v92

**The cursor moves on after grading a selection**, as it already did after
grading a single file. Grading fifteen files and grading one are the same
act at different sizes; the cursor now lands on the first file past the
selection that was not part of it, ready for the next decision. A selection
that reaches the end of the list leaves the cursor where it is rather than
jumping somewhere arbitrary.

## v91

**Fixes the browser hanging while grading.** Every grading called a full
render, which rewrites the markup of the whole page — up to five thousand
tiles, each an image or video element the browser then lays out and fetches
again. At speed that is seconds of blocked script per keystroke, and Chrome
eventually stops the page with `RESULT_CODE_HUNG`.

Nothing about one file changing requires the rest of the page to be rebuilt.
A grading now repaints just the tiles that changed and updates the counts;
the page is rebuilt only when the next file is genuinely on another page.
Flags work the same way.

**A cap on how many tiles are built at once.** The per-page control offers
"all", which is sensible for a few hundred files and a way to hang a
browser on a hundred and sixty thousand. No more than five thousand are
built at a time, and the toolbar says so when it applies.

## v90

**An audit of every feature, now part of the suite.** `tests/test_endpoints.py`
builds its own case and drives all forty-three endpoints the way the browser
does: opening a case, grading one file and many, flags, notes, categories,
snapshots, the audit log, similarity, screening, previews, frames, hex view,
hash sets and the store, the report in several formats, the C4All and LAVA
exports, the contact sheet, and saving and applying a scheme.

Unit tests did not catch the faults of the last few releases, because each
piece still worked on its own — what broke was whether a feature could still
be reached. This drives the whole path.

It found one real fault: **a format that wrote its file was reported with no
path.** Each exporter names its result differently — `path`, `output`,
`out_path` — and Project VIC's was never shown, so the file was written and
the dialog showed a blank line. Results are normalized now, and the audit
asserts that anything written says where it went.

## v89

**The counts above the gallery follow the grading again.** They were drawn
from a figure the server sent when the case was opened, and redrawn only
when the whole case was re-read — which v83 stopped doing after every
keystroke, because re-reading it was what made grading slow. So the bar sat
showing the numbers from load time: grade five files and neither the
category's count nor the uncategorized count moved.

They are now counted from the decisions the page already holds, and redrawn
whenever the gallery is. Counting twenty thousand files takes about a
millisecond, so this costs nothing per keystroke. The Classification pane
and the active-filter bar were already live.

## v88

**The video decoder is quiet.** FFmpeg, which OpenCV decodes with, writes
its complaints straight to the terminal underneath Python — so the warning
capture added in v75 could not reach them. A carved clip usually starts
mid-stream with no header describing how to decode it, producing
"non-existing PPS 0 referenced" and "decode_slice_header error" several
times per file; a run with a few thousand such clips fills the window with
them.

OpenCV's own logging is now set to silent before it loads, and the decoder's
output is redirected for the duration of each read. What it was reporting is
already said properly where it belongs: "no frame could be decoded".

## v87

**The keyboard grading handler was gone.** Pressing 0 or 5 did nothing
because nothing was listening: an edit aimed at something else had removed
the handler that maps a key to a category, and three unrelated keydown
handlers remained to make the page look normal. The buttons in the details
pane still worked, which is why this looked like a server problem through
two releases — and in v83 and v84 the details pane was itself broken, so
both routes were down at once for different reasons.

Restored, and the gap that hid it is closed: `tests/test_gui_markup.py` now
checks the whole grading path — that a keyboard handler exists which maps
keys to categories and flags and knows which file is selected, that the
details pane's buttons are wired, and that the function they both call still
posts to the server. Removing the handler is now a failing test, which was
confirmed by removing it.

## v86

**More server threads.** Eight was enough for the browser's own
six-connection limit and not enough for the slow work that overlaps it —
converting a HEIC, decoding video frames, writing a report — so requests
queued and waitress said so ("Task queue depth"). It now runs four threads
per processor, between sixteen and forty-eight. Sixty files fetched at once
on a four-core machine: no queueing.

## v85

**Fixes categorizing, properly this time.** The case database shares one
SQLite connection between the server's request threads, and its reads were
taken outside the lock that guards its writes. That was harmless until v83
moved the periodic snapshot onto a background thread: SQLite's backup takes
over the connection it reads from, so anything else using that connection at
that moment fails with `bad parameter or other API misuse` — an error that
says nothing about backups, and which showed up as the interface refusing to
categorize.

Reproduced on a twenty-three-megabyte case database with readers running
during a backup, which is why it only appeared on a large case. Every read
now takes the same lock as every write, and a snapshot is taken through its
own read-only connection, so it neither disturbs the shared one nor makes
grading wait behind it: 42 ms was the longest a grading waited while
snapshots ran.

`tests/test_casedb.py` now runs readers, a writer and snapshots against one
case at once.

## v84

**Fixes categorizing, which v83 broke entirely.** Rewriting the grading
path, the edit replaced everything between two functions and took
`advanceSelection` with it, and the replacements that called `nextAfter` and
`moveTo` were never added. Every grading threw before it reached the server,
so nothing saved and nothing appeared to happen — one file or fifty-five.

The three functions are restored and both paths verified through the real
endpoints: one file graded, fifty-five graded, counts correct.

**And the test that should have caught it now exists.** The markup test
checked that every element the script touches is in the page, but nothing
checked that every *function* it calls exists. A syntax check passes happily
on a call to a function that was deleted; the failure only shows when
somebody presses the key. The script's own calls are now resolved against
its own definitions — with strings and comments stripped first, so ordinary
prose like "the source (as recorded)" is not mistaken for a call. Deleting
`moveTo` or `nextAfter` is now caught, which was confirmed by deleting each
of them.

## v83

**Grading is immediate again.** Measured on a case of 160,000 files, each
keystroke was costing **576 ms**: 494 of them re-reading a sixty-megabyte
manifest to refresh a count on the Cases screen, 81 reloading every decision
in the case and sending nearly six megabytes of it back to the page, and 1
actually writing the decision.

The count refresh and the periodic snapshot now run on a thread at most
every ninety seconds, off the path a keystroke takes. The decision is shown
in the gallery at once and written behind you, and the page patches its own
copy from the reply instead of asking for the whole case. **576 ms to 1 ms.**
If a write fails, the file is put back as it was and the reason is shown.

**And the cursor no longer skips a file.** Where to go next was being worked
out *after* the grading had already removed the current file from the
filtered list — so the cursor either stalled or landed one file further on.
It is now worked out before the list changes.

Flagging got the same treatment.

## v82

**Reports no longer hang on a large case.** Three things were wrong at once:
the whole report was written inside the request, so the browser sat on
"Writing…" with no way to tell a working report from a stuck one; every
thumbnail was embedded in a single document, so six hundred files made a
twelve-megabyte page and a hundred and sixty thousand would have made a
multi-gigabyte one that no browser opens; and there was no way to stop it.

Now: the report runs as a background job with a stage, a count and a **Stop**
button; past four hundred files it is split into numbered pages with
thumbnails written beside it rather than inside it — twenty-four times
smaller on a six-hundred-file case — and thumbnails are made several at a
time.

**Word and PDF.** Word is written directly, without a Word installation or
any library, with pictures embedded, and verified to open in a real Word
reader. PDF uses ReportLab where it is installed; where it is not, it says
what to install and points out that printing the HTML report from a browser
gives a better result anyway.

## v81

**A step-by-step setup guide for the shared store**, `SERVER_SETUP.md`, at
**Menu → Setting up a shared hash store**. Written for someone who has not
run a server before: choosing the machine, installing Python, creating the
store, making the certificate, issuing tokens, opening the firewall port,
connecting each examiner, starting it automatically, backups, and a table of
what each error message means.

Walking through it as written found a real trap: a certificate made with
`--cert-name` for the machine's network name did not cover `localhost`, so
Part 7's own instruction to open the admin pages on the server produced a
browser warning. The certificate now always covers `localhost` as well as
the names given.

`tests/test_manual.py` checks every `hashserver` option the guide tells
someone to type against what the program actually accepts, so the guide
cannot drift into telling people to type flags that do not exist.

## v80

**Admin pages in a browser.** Sign in at the server's address with an admin
token: an overview with per-person use (lookups, contributions, and the
number of distinct cases each person has contributed under), people (add
someone and see their token once, disable anyone), sets (import from a path
on the server, remove a source) and the full activity record. The pages
enforce the same roles as the API — a reader who finds the URL gets the
overview and a refusal.

**The server makes its own certificate**: `--make-cert`, with every name the
server is reached by. For a closed lab with no certificate authority to ask.
The key is written with owner-only permissions and stays on that machine.

**Clients verify it rather than skipping verification.** A copy of the
certificate is given to the program, and that one certificate is what the
connection will accept — a stronger position than trusting the hundred-odd
authorities a machine ships with. Verification is never simply switched off.
A certificate that is not trusted produces a message naming the fix, instead
of a network error that sends someone to check cabling.

## v79

**The hash store can be shared across a unit.** `fme hashserver` holds one
store on one machine and serves it; the program points at a URL instead of a
path and everything else works unchanged — scanning a run, contributing a
case back, listing sources.

This exists because a store on a file share does not work: SQLite's locking
across SMB is how a database gets corrupted by two contributors at once.

Given what the store holds, access is taken seriously. Three roles — read,
contribute, admin — so an examiner who checks hashes does not thereby get to
add to the store. No shared password: each person has their own token,
stored hashed and shown once, and **every lookup and contribution is
recorded against them** with the time and address. A disabled person is
refused even holding their token.

A lookup sends digests and receives only matches, so the store's contents
never cross the network. Importing a set is server-side, on a path the
server can see. Without TLS the server warns when bound to a real interface.
An unreachable store raises a plain error rather than quietly returning no
matches, because a check that silently did not happen is worse than one that
failed.

## v78

**Screening, to order a backlog**: each image is scored by the proportion of
it inside a broad skin-tone colour band, and faces are counted where OpenCV
is installed. Two new filters — Skin-tone proportion and Faces found — and a
`screening.csv` beside the run.

**It is not a nudity classifier, and the interface says so wherever it
appears.** Griffeye's nudity levels come from a trained classifier; this is
arithmetic over pixel colours. The test suite pins down the failure modes as
deliberately as the successes: sand, wood and brick are asserted to score as
high as skin, so that nobody can later mistake the number for a judgement
about the picture. An image that cannot be decoded reports *Could not be
read* rather than zero, which would have put it in the lowest band and read
as "nothing here".

Nothing is ever categorized by it.

## v77

**The side pane has tabs.** Details, Classification and Filters were chips in
the toolbar, where they read as three separate buttons rather than as one
pane with three views. They are now tabs across the top of the pane itself,
and the open one carries the pane's border so the tab and the panel below it
read as one surface.

A tab now only switches; it never closes the pane. Clicking the tab already
open used to close the column, which is a toggle wearing a tab's clothes.
Closing is the ✕, and **Side pane** in the toolbar reopens it on whichever
tab was last open.

## v76

**Grading a whole page at once now moves on.** Holding a page, added in v71,
stops tiles shifting while an examiner works down it one file at a time — but
it applied equally to a selection that graded the entire page, which left the
view showing five hundred files that no longer matched the filter.

A bulk grade now checks whether anything on the held page still matches: if
nothing does, the view moves to the files that follow; if some does — a
selection covering only part of the page — the page is held, because the rest
is still to be graded.

## v75

**The image decoder's warnings no longer print to the terminal.** A carved
JPEG whose EXIF block was cut off mid-field makes Pillow print `Corrupt EXIF
data`, once per file — and a run that recovers a few thousand of them fills
the window with noise that hides anything that matters. Every decode now goes
through one module which captures those warnings, counts them by kind, and
reports the counts with the pass that caused them.

The same module registers HEIC in one place rather than three, and tells
Pillow to decode truncated files as far as they go — a carved image missing
its last rows is still the picture, and still worth a thumbnail and a hash.

## v74

**Your own categories and flags carry between cases.** Menu → Save my
categories and flags, and every new case starts with them. Additive and
idempotent: nothing renamed, nothing removed, applying twice changes nothing.
The locked categories are not carried, because they mean the same thing in
every case and the case database refuses to rename them anyway.

**Cached thumbnails can be named.** A thumbcache entry records a 64-bit
identifier and no path; Windows Search stores that identifier against the
item's real path. `fme winsearch -o <run> --index Windows.db` joins them,
writes `thumbnail_sources.csv`, and the details pane then says what the
thumbnail was. The identifier is now kept on every carved thumbnail so the
join is possible after the fact.

Windows 11's SQLite index is read; Windows 10's ESE `Windows.edb` is refused
with a reason rather than guessed at. A thumbnail the index does not know
stays unnamed.

## v73

**The report's front page now carries the shape of the case.** Highlights
first — category 1–3 counts, what is flagged and with what, how many carry
coordinates, cameras and camera bodies seen, the range of capture dates,
deleted and carved counts, hash-set matches — then a category donut with
counts and percentages, the same for flags, bar charts by file kind, size
band and year taken, and a plot of where geolocated media sits.

Everything is SVG drawn by hand: no script, no library, no network. Category
colors come from the case, so the report and the gallery agree. The location
plot shows spread and clustering and says plainly that it is not a map,
because no tiles are shipped and inventing coastlines would be worse than
drawing none.

Two drawing faults found and fixed while building it: a chart left to stretch
to its panel scaled its labels with it, and a bar using the full height
clipped the number above it.

## v72

**Audio formats a phone actually produces**: AMR and AMR-WB (calls and voice
notes), SILK (Skype and some messengers), MP3, AIFF and CAF. None of AMR,
SILK or MP3 records its own length, so each is recovered by walking its
frames. Two bugs found doing it: the AMR frame-size table already counts the
header byte, which was being added again and walking the file at the wrong
stride; and a random byte after the end of an AMR file passes the
frame-header test often enough to swallow one more frame, which is now
caught by requiring the mode to stay consistent. Either would have produced a
carved file differing from the original and losing a hash-set match.

**Artifacts on request** (`-k`, or the checkbox): SQLite databases, binary
property lists, zip archives — APK, IPA, JAR, Office and OpenDocument among
them — and PDFs, each sized from its own structure. Off by default: a disk
holds far more of these than it holds pictures.

**Blurring in the gallery**, with a scope: the categories chosen, every
image, or anything not yet graded. Hover a tile to see it, or hold **B** to
see the page. For reviewing beside other people — not a safeguard, and the
interface does not pretend otherwise.

## v71

**A page of results now holds still while it is graded.** Filtered to
Uncategorized, grading a file removed it from the list and shifted every tile
after it, so the next click landed on something else. A page is now built
once and kept until the examiner leaves it, whatever categories get applied
to the files on it — the color strip updates, the tile stays. **Hold the
page** in the toolbar turns it off.

**Next lands on the files that follow**, not on the next page number. Once a
held page's files have been graded out of the filter, the list beneath has
shrunk and the next batch has moved up; advancing the page number would skip
a whole batch. The first file that still matches and comes after the held
page is found instead.

## v70

**NSRL RDS databases are read properly.** The reader took the first table
with hash-shaped column names, searched tables but not views, and never
checked that a column held anything. Three consequences, all fixed:

- The modern RDS ships raw rows in `FILE` and a joined view in `METADATA`.
  `METADATA` is now preferred, which also brings the product name with each
  hash.
- Releases that ship an empty `sha256` column no longer have it chosen; each
  candidate column is sampled first.
- Distributions that present their hashes in a view rather than a table are
  found at all.

The import now reports which table and columns it used, so it can be checked
instead of trusted, and an RDS hit lands as **Non-Pertinent recorded as the
set's decision**, which is what a known-good set is for.

## v69

**The full Project VIC download now imports properly.** The reader looked for
a `Media` array, which is where a *case export* keeps its records. The
national download has no cases in it: its records sit directly in `value`.
So a 70-million-hash import found nothing structurally, fell back to scanning
the file for anything hash-shaped, and kept the digests while losing every
category, MediaID, tag and flag — the whole reason for importing the set.

All three shapes are read now: records in `value`, records under `Media`
inside a case, and a bare top-level array. A case is recognized by having a
`Media` array rather than by its other fields, so a record that happens to
carry a case number is still read as a record.

Streaming is unchanged and holds: a 1 GB single-line file reads at about
280,000 records a second in under 50 MB of memory.

**Re-import any Project VIC set whose import reported a fallback.** Remove
the source in the Hash Sets tab first.

## v68

**Carved files now appear during a run instead of at the end.** Two things
caused the wait, and both are fixed:

- The parallel carver merged every stripe before writing anything, because a
  file may have started in the stripe before. It now releases results as the
  leading edge advances: a stripe is written as soon as its predecessors are
  done, which preserves the merge rule exactly. Verified to produce identical
  output on a 2 GB image at the same speed.
- The interface only read the file list when the run finished. It now re-reads
  the manifest as it grows, a few seconds apart.

On a 2 GB test image the first file now appears after 9 seconds of a 52
second run rather than at the end. On a 128 GB exhibit that is the difference
between watching a run work and wondering whether it has hung.

## v67

**Fixed: a 500 and two tracebacks on every page load.** The error handler
added in v66 re-raised non-API exceptions from inside itself, so the
browser's routine request for `/favicon.ico` turned a 404 into an internal
error. An HTTP error now returns its own response, and there is a favicon to
ask for.

**HEIC, TIFF, camera raw and PSD show as pictures.** They are rendered to
JPEG on request and cached; HEIC needs `pillow-heif`, without which an
iPhone's camera roll has no thumbnail, preview or perceptual hash at all.
The rendering is for display only — never hashed, never counted, never
written among the recovered files.

**A window of its own**: `--window`, with `pywebview` installed. No address
bar, no tab to close by accident during a run. Without it the program says so
and opens a browser.

**A portable build**: `python build_exe.py --portable` writes a folder — the
executable beside `_internal/` — which starts immediately rather than
unpacking itself on every launch. The single-file build is still there.

## v66

**An API failure now answers in JSON.** A route that threw returned Flask's
HTML error page, which reached the interface as "Unexpected token '<'" — a
message about JSON parsing that says nothing about the fault. Every `/api/`
failure now returns the real error, and the page reports the status and what
came back when a response is not data at all.

**A mixed install is detected and reported.** The release number ships at the
source root and inside the package; when they disagree, some files landed and
others did not, which is what copying a new version over an old one does when
it goes wrong. Compiled files left behind with no source beside them are
reported the same way. The warning appears at startup, in `doctor`, and in
**Menu → What is installed**.

**`/api/health`** reports the release, the engine version, the Python version
and which file is serving — enough to tell a running program from a confused
one.

## v65

**Hash sets now categorize what nobody has looked at.** Importing a set and
scanning a run used to leave every file uncategorized, whatever the set said.
Four rules now apply: only an uncategorized file is touched, so a set never
overrides an examiner; a set that asserts a category beats one that does not,
and the most severe wins; a known-good (NSRL) hit means Non-Pertinent only
when nothing notable flags the file as well; and the decision is recorded as
the set's, never as a person's.

That last point runs through everything: the tile carries a striped marker,
the details pane names the set, **Set by a hash set** and **Not yet
reviewed** filter on it, the audit log records it, and the Project VIC export
writes the set as `PrecategorizationSource` instead of claiming an examiner
decided.

**Grading a whole page now returns to the top of the next one.** It changed
the page but left the view where the last one ended, so every page started
halfway down. The same happens whenever a filter shrinks past the current
page.

## v64

**LAVA previews fixed, and the cause found.** The viewer resolves a media
item's recorded path against the report folder, arrives at a path no report
contains, and falls through to `_HTML/<extraction_path>` — the copy it
actually serves; its "open externally" handler resolves only against
`_HTML/`. Every media file is now written twice, at `media/` and under
`_HTML/media/`, hard-linked where possible. `--validate` checks for the
served copy and explains what a missing one means. Projects exported before
this version need re-exporting.

**The two pages LAVA shows as tabs** are written: `_HTML/_Script_Logs/
DeviceInfo.html` (source, container, file systems, examiner, elapsed) and
`Screen_Output.html` (what the run found).

**NSRL imports keep their record**, as Project VIC imports have since v62:
file name, size, CRC32, product and operating system codes from
`NSRLFile.txt`, and the equivalent columns from an RDS SQLite database. A
match now says "msvcrt.dll, 348160 bytes, Windows 10" rather than "known
software".

`tests/test_lava.py` checks that every media cell resolves both at `media/`
and under `_HTML/`, that the two log pages exist, and that removing `_HTML`
is rejected with a reason.

## v63

**A manual**, `MANUAL.md`, covering everything: loading evidence and whether
to mount it, every option, the results screen, searching and filtering,
reviewing with its categories, flags, keys and selection, hash sets and the
store, cases, reports, every file a run produces, the command line, what to
install, troubleshooting, and what the program does not do.

**Menu → Manual** renders it in a tab. The renderer is a small Markdown
subset written for the purpose, so the manual stays a readable text file and
the program still needs only Flask.

`tests/test_manual.py` compares the manual's claims with the code: the
category codes and keys, the seeded flags, the command-line tools it shows
and the files it says a run writes.

## v62

**A Project VIC import keeps the whole record.** The store was keeping the
digest, the category and a note, and discarding everything else the set
carried — MediaID, MediaSize, DateUpdated, Tags, Series, the
precategorization fields and the victim, offender and distribution flags. A
match that says only "this hash is in the set" cannot be acted on. All of it
is now stored with the hash and shown in the details pane, the report's hash
set column and the CSV and JSON exports.

Hashes are read from `AlternativeHashes` as well as the record's own MD5,
SHA-1 and SHA-256, so any of them finds the record.

Matches made against the store are now saved to `case.db` like the
per-session ones, so they survive a restart and reach a report written later.

An existing store gains the new column rather than needing a rebuild, but the
records only arrive on re-import: **re-import any Project VIC set imported
before this version.**

## v61

**Review results survive a restart.** Similarity hashes and hash-set matches
are written to `case.db` as they are produced and restored when a case is
reopened, so a quarter of an hour of hashing is not repeated after every
restart. Verified by killing the program outright mid-review. A snapshot is
taken automatically when half an hour of grading has passed, as the case is
worked rather than on a timer.

**A report**, from the button top right or the menu: header (agency, case
number, evidence item, examiner, logo, notes), scope (everything, the current
filters, the selection, or categorized only), formats (HTML, CSV, JSON, MD5,
KML, Project VIC), chosen columns and an optional section per flag, with
counts by category and flag on the front page. The scope is stated on the
front page; thumbnails can be omitted entirely with the header saying so; and
blurring says plainly that the image is still in the file.

**A menu**, top left: cases, new extraction, hash sets, signatures, report,
results folder, open the output folder, snapshot, audit log, and what is
installed.

**Paging forward lands at the top.** Advancing past the last file of a page
moved to the next page but left the view where the previous page ended, which
meant scrolling up at every page boundary.

**The confirmation on bulk categorizing is gone.** A review is thousands of
these, and a dialog on each costs more than the mistake it prevents — nothing
is destroyed, the previous category is in the audit log, and regrading is one
keystroke.

## v60

**The gallery has its own scrollbar.** The results were growing the page
instead of scrolling inside their own column, so with the settings and
details columns pinned there was no scrollbar beside the tiles — only the
window's, at the edge of the screen. The results column is now bounded like
the other two: the toolbar, the category bar and the active-filter bar stay
put, and the tiles scroll between them with a visible scrollbar. Paging
returns to the first tile rather than moving the whole window.

Below 900px everything stacks and the page scrolls again, as before.

`tests/test_gui_markup.py` now checks that the gallery and the pager sit
inside the scrolling area and the toolbars sit outside it.

## v59

**A right-click menu** on any file: find similar images, show details, view
full size, hex view, every category and flag with its key and a tick where it
applies, clear category, export MD5s, open the original, show in folder.
Right-clicking inside a selection acts on all of it and says so; right-
clicking outside one selects that file first.

**Hex view** and **View full size** as modals, and **Export MD5** of one file
or a whole selection as a downloaded CSV.

**Frames for videos the browser cannot play**, with OpenCV: most AVI, some
MOV codecs, MPEG-2 and much of what a DVR writes. The details pane shows a
filmstrip, decoded on demand and cached, instead of a black rectangle; the
browser's own scrubbing still handles everything it can play, at no cost.
Frames are generated images and are written to `_frames/`, outside the
recovered media, because their hashes are this tool's and not the evidence's.

## v58

**ExoPlayer caches rejoined.** A video an Android app streamed is cached as
pieces named for where they start, across ten subfolders, with the name in a
separate index — so carving recovers the first piece and nothing else. A
folder scan now groups the pieces, reads the name from the SQLite index or
the legacy `.exi`, and joins from the start until the first gap. Joining
across a gap would make a file that looks whole and plays wrongly, so it
stops and says so; how many pieces went in and whether the result is complete
is recorded on every rejoined file. Pieces are no longer classified as videos
themselves.

**Snapchat `LZC` content bundles** are recognized and unpacked where a
Zstandard decompressor is installed, and counted and reported where one is
not.

**Video scrubbing on hover.** Moving across a video tile seeks the clip, so
it can be judged from frames without being opened; the details pane does the
same with the controls still available. Only the header is fetched up front.

## v57

**Windows thumbnail caches, PDFs and saved web pages** are now opened by the
compound-file pass.

- `thumbcache_*.db`: Explorer's cached thumbnails, which outlive the pictures
  they were made from. Walked by header offsets rather than by scanning for
  `CMMM`, since a thumbnail's own bytes can contain that signature; a
  truncated record stops the walk instead of yielding half an image.
- PDF image XObjects: JPEG and JPEG 2000 streams come out verbatim, so their
  hashes can match a set. Images stored as raw pixels are counted and not
  rebuilt — reconstructed bytes would carry this tool's hashes, not the
  evidence's. Embedded attachments come out too.
- `.mht` / `.mhtml` parts and `data:` URIs in HTML, verbatim.

Folder mode now runs the same compound pass the file system engine does: a
thumbnail cache on a mounted volume holds what it holds on an image.

## v56

**A case list, and the program opens on it.** Every case processed here, by
case number, evidence item and examiner, with file and graded counts. Click
one to open it: the manifest and `case.db` are read back and the gallery,
the review and the exports are all there, with nothing carved or hashed
again — measured at hundredths of a second for a case that took hours to
produce.

Runs register themselves as they start, so a cancelled run still appears
with whatever it made. Counts are re-read from each case's own files rather
than remembered.

The list is a table of contents, not a copy: it holds no evidence and no
decisions. A folder that has moved shows as missing rather than being
offered, and a lost list is rebuilt by **Scan**, which finds output folders
by their manifests and reads the case number and examiner back out of each.

## v55

**More filters**: file type (still image, animated image, video, audio),
file extension, file size by band, deleted or not, metadata (has metadata,
has GPS, none), date taken by year, and file created, last written and last
accessed by year — alongside the camera and place facets.

**Animated images are detected by reading the file**, not by its extension:
GIF by counting image descriptors, APNG by its `acTL` chunk, WebP by its
`ANIM` chunk. Most GIFs on a disk are static. The first attempt missed every
animated GIF because the frame walk started at a fixed offset and landed in
the middle of the global color table.

A facet can now put a file under several values at once, which is how a
photograph with coordinates is found under both **Has metadata** and **Has
GPS**, and fixed-order facets read in a sensible sequence rather than by
popularity — size bands ascending, "Not deleted" before "Deleted".

## v54

**Three panes in the right-hand column** — Details, Classification, Filters —
switched by chips in the toolbar.

**Classification**: every category with its count, every tag with its count,
both clickable. Tag counts are computed within the selected category, so
"category 1 with the AI tag" is two clicks and the number is right before the
second one.

**Filters**: facets built from each file's own metadata — camera author,
make, model, body serial number, city, state, country. Values within a facet
are alternatives, facets are cumulative, and every count is computed against
the other filters so it describes the result of clicking it.

**An active-filter bar** at the bottom of the results: what is narrowing the
view, an ✕ on each, and a button to clear them all.

**More metadata to filter on.** EXIF `Artist` and `BodySerialNumber` were not
being read, and place names were not read at all. IPTC (Photoshop APP13) and
XMP are now parsed for city, state, country, creator and serial. A camera
writes none of the place fields -- they are there because a person or a
cataloguing tool put them there, which is exactly why they are worth having.

## v53

**Grading many files at once.** Ctrl-click to add, shift-click for a range,
ctrl-A or **Select all matching** for everything the filter and search match,
**Select this page** for what is shown, Escape to clear. With several
selected, a category key or button grades all of them and a flag applies to
all of them. Over fifty it confirms first, naming the category and the count.

Selected tiles get an outline and a tick, louder than the "open in the
details pane" highlight, and the details pane states what an action will
cover. After a bulk action the cursor stays put rather than advancing, since
what matters then is what is left rather than the next tile.

The flag endpoint takes a list as well as one file; the categorize endpoint
already did.

## v52

**`launch.py` now runs every tool, not just the interface.** With no tool
named it opens the interface as before; with one it dispatches, leaving the
working directory alone because the arguments are relative to wherever the
examiner is standing.

This is the answer to `No module named fme`: `python -m fme` only works from
the project root, because that is where Python looks for the package. The
launcher puts the package on the path itself, so it works from any directory
with nothing installed. `doctor` and `--help` now say so rather than leaving
it to be discovered.

## v51

**The store file is self-contained after every write.** SQLite was leaving
recent imports in a `-wal` file beside the database until it chose to fold
them in — fine for the running program, wrong for a file people copy to a
share or open in a viewer, where a `.db` taken without its `-wal` is missing
whatever had not been folded in. That looks exactly like an import that did
nothing. The log is now checkpointed after each import, contribution and
removal, and on close.

**`--verify`**, and a **Verify** button: opens the store read-only and
reports the total, the split by algorithm and the count per source — what the
file holds, as a colleague opening it would see, rather than what the running
program believes.

## v50

**An import that finds nothing now says why.** It reports rows read, rows
skipped and the reason, and — when the structured reader comes back
empty — scans the file for anything hash-shaped and imports what it finds,
saying that is what happened. A Project VIC variant whose records are not
where the reader expects therefore imports rather than reporting zero.

A source that ends up with no hashes is removed instead of being left in the
list looking like a set that happens to be empty.

**`--inspect`**, and a **What is this file?** button: the size, the detected
kind, whether there is a byte order mark, what the structured reader reads,
what a scan finds, and for a VICS file that reads as empty, whether a
`Media` array is where it should be and which keys are there instead.

## v49

**A category bar above the results**, replacing the dropdown: one chip per
category, colored as the category is, showing its key and how many files are
in it. Clicking filters to that category; clicking the same chip again clears
the filter. Empty categories are dimmed rather than hidden, so the bar keeps
its shape and a category stays where the eye learned to find it.

## v48

**Fixed: Browse did nothing in the Hash Sets tab.** The hash store panel's
markup was never inserted in v47 while its handlers were, so
`$("store-browse")` returned null, the `addEventListener` on it threw, and
**every handler registered after that line silently never bound** — including
the Browse button several screens away, which is where it was noticed. The
markup is in place and all of them work again.

`tests/test_gui_markup.py` now checks that every element the script reaches
for exists in the page, and that anything built at run time is reached
through a guard rather than directly. Removing the markup again makes it
fail, which is the point: an interface is not exercised by importing it.

The file picker also says what is being chosen — a hash store, a set to
import, a results folder — rather than "Choose an image file" everywhere.

## v47

**A search box** over the results: file name, source path, all three hashes,
camera and your note, narrowing as you type, with `name:`, `path:`, `md5:`,
`sha1:`, `sha256:`, `cam:` and `note:` prefixes to restrict a term to one
field. Hashes match on a prefix, so eight characters from a report find the
file. Several terms all have to match.

**A hash store kept between cases** (`fme/review/hashstore.py`): one SQLite
file holding Project VIC, NSRL and your own sets, imported once and consulted
by every case afterward. Sets stream in rather than loading into memory, so a
national set is a question of time and disk rather than whether it fits. The
store can live on a share for a whole office.

**Contributing a case back into the store.** Files an examiner graded are
added with the grading attached, so a later case matching one is told what
was decided and in which case, not just that it was seen. Uncategorized files
are not contributed — an ungraded hash teaches the next case nothing.

A file matching one source by several algorithms is now reported once, with
which algorithms agreed, rather than once per algorithm.

## v46

**AI-generated is a flag now, not a category.** A picture is CAM or it is
not; separately, it was generated or it was not. As categories the two had to
be answered with one keystroke, and a file could not be both AI CSAM and, say,
Infant/Toddler. As flags they combine freely and several can apply.

`AI CSAM`, `AI Age Difficult` and `AI Adult` are seeded flags keeping the
keys they had as categories — `-`, `.` and `*` — so the muscle memory
carries over. Flags now take keyboard shortcuts generally: a flag key
toggles and leaves the cursor where it is, a category key grades and moves
on. Examiner categories start at 10 again, the codes the AI categories used.

**Files already graded as an AI category are migrated to both halves**, not
dropped: AI CSAM becomes category 1 with the AI CSAM flag, AI Age Difficult
becomes 2 with its flag, AI Adult becomes 5 with its flag. Each is recorded
in the audit log. Dropping them back to uncategorized would have discarded a
decision an examiner actually made.

This also fixes the Project VIC export for that material: an AI CSAM file now
exports as category 1, which is what VICS defines, with the AI flag recorded
in this tool's own reports. Before, it exported with no category at all
because VICS has no code for it.

## v45

**Four flags in every new case**: S&M Violence, Infant/Toddler, Bestiality,
AI CSAM. Seeded once when the case is created rather than on every open, so a
flag an examiner deletes stays deleted, and kept in the order given rather
than sorted alphabetically. Unlike the categories they are not locked: delete
what a case does not need, add what it does.

Flags now also appear on the gallery tile, so a page of decisions reads
without opening each file.

## v44

**The grading scheme, corrected.** Thirteen categories with the keys that
apply them: 0-9 on the number keys, and `-`, `.` and `*` for AI CSAM, AI Age
Difficult and AI Adult. Locked, because the codes have to mean the same thing
in every case; your own categories are added above them from 13.

**Uncategorized moved from 0 to 8**, which is the part that needed care. In
this scheme 0 means Non-Pertinent — a decision somebody made — so a file
nobody has looked at must not land there. Nothing assumes 0 any more: the
default, the backlog filter, the tile strips and the result of deleting a
category all read the code from the case.

**Cases graded under the previous numbering are migrated, not reinterpreted.**
Renaming the rows alone would have turned every untouched file into a
positive finding of "not pertinent". On opening an older case, decisions move
(0 to 8, 5 to 0; 1-4 mean the same in both) and the remapping is recorded in
the audit log.

**Flags have their own labeled section** in the details pane, with a count
per file, a delete control on each, and a line saying they are the
examiner's to define.

**The Project VIC export maps rather than assumes.** Only the codes VICS
defines are written; Adult Pornography, Abnormal, Notable, Suspect, the AI
categories and your own export with no category rather than a guess, and the
export reports how many.

## v43

**Tile size, paging and time zone** in the results toolbar, each remembered
per browser.

- **Tile size** slider: the grid takes its column width from a CSS variable,
  so resizing reflows without re-fetching thumbnails.
- **Per page** (200 / 500 / 1500 / 5000 / all) with first, previous, next and
  last. Building every tile at once is what makes a large gallery unusable.
- **Time zone** applied to every timestamp that carries one, converted by the
  browser so daylight saving is handled per date rather than per setting:
  the same zone shows EST in January and EDT in July.

Timestamps without a zone — EXIF capture time, and FAT times — are shown as
recorded and labeled, not shifted. Converting them would mean inventing the
zone they were written in.

**FAT32 timestamps are read at last.** Without pytsk3 the fallback recovered
names, paths and data but set no times at all, because it never parsed the
date fields a FAT directory entry carries. Created, written and accessed now
come through, with the caveat above: FAT records no time zone.

Archive entry times were being read as local time while every other file
system timestamp was UTC; they are UTC now, so the interface can convert
them.

## v42

**`launch.py` at the top of the source tree, for double-clicking.** The
restructure left the interface at `fme/web/media_gui.py`, which still starts
when double-clicked but writes `media_out/` into `fme/web/` — a double-clicked
file runs with its own folder as the working directory. The launcher sits at
the project root so results land beside the project, and it carries the
multiprocessing guard so carving workers do not each open a browser window.

**A double-clicked `media_core.py` now explains itself.** It is the engine,
not the interface: with no arguments argparse printed usage and exited, and on
Windows the console closed before anyone could read it. With a console
attached and no arguments it now says what the file is, points at
`launch.py`, and waits for a keypress. Piped or scripted use is unchanged,
so nothing that calls it programmatically is affected.

## v41

**Running a module file directly works again.** The restructure broke it:
`python fme/engines/media_core.py` failed with "No module named 'fme'"
unless the package happened to be installed, because the module's own
imports could not resolve. Every module with a command line now puts the
project root on the path when it finds itself running without a package
context, so all of these work, installed or not, from any directory:

```bash
python -m fme extract -i case.E01 -o ./out
python fme/engines/media_core.py -i case.E01 -o ./out
python fme/review/hashsets.py --scan out/manifest.json --nsrl NSRLFile.txt
python fme/web/media_gui.py --port 5001
fme extract -i case.E01 -o ./out        # when installed
```

The first attempt at the fix put the path bootstrap above
`from __future__ import annotations`, which is a syntax error — caught
immediately, since nothing would import at all.

## v40

**Restructured into a package.** Twenty modules in one folder became
`fme.engines`, `fme.exports`, `fme.review` and `fme.web`, with the tests and
their fixtures in `tests/`. Every cross-import was rewritten; nothing else
changed.

- `pyproject.toml`, so `pip install .` works and `fme` becomes a command.
  Flask is the only required dependency; the rest are extras (`.[full]`,
  `.[vm]`) because the code reports what is missing instead of failing.
- `python -m fme` from a checkout, `fme` once installed, and the executable
  all go through the same entry point.
- Importable as a library: `from fme.engines import media_core`.

Three things the move broke, found by the tests and fixed:

- The release number is read from `VERSION`, which was no longer beside the
  engine. It now ships inside the package as well as at the source root.
- Results defaulted to the folder holding the code, which after installation
  is site-packages — no place for evidence. From source they now default to
  the working directory; frozen, to the folder holding the executable.
- The console entry point called straight into the dispatcher, skipping the
  multiprocessing guard that `python -m fme` gets from `__main__`. Both paths
  now share it.

## v39

**No third-party project names in the documentation** beyond the formats and
tools this one reads or writes — Project VIC, NSRL, LAVA, Griffeye, C4All,
Cellebrite, EnCase, The Sleuth Kit. Comparisons that named other software
were rewritten to describe the behavior instead, which is the part that
matters anyway: what a carver misses when it aligns to sectors does not
depend on whose carver it is.

## v38

**The development-server warning is gone** where `waitress` is installed —
it is used automatically, and it is also threaded, so a gallery fetching many
thumbnails at once no longer stalls behind one request. Without it Flask's
server still runs and the console explains what the warning means rather than
leaving it unexplained.

**A self-contained executable, again** — `fme.py`, `build_exe.py` and the
PyInstaller spec, with the two things that break a frozen build already
handled: output goes beside the executable rather than into the temporary
unpack directory, and `multiprocessing.freeze_support()` stops each carving
worker from relaunching the whole program. `build_exe.py --check` reports what
would be bundled before the build, and `doctor` reports it afterward.

**Documented which file systems are actually supported**, and which are not —
the answer is TSK's list, not a wish list. Carving still works on the rest.

## v37

Four gaps closed, from a review of what comparable tools offer.

**Review workflow (`casedb.py`)** — the largest one. Categories, flags, notes
and an audit log, in a `case.db` beside the recovered files. Every change
commits immediately (SQLite WAL), so a hard kill loses at most the last
action. Decisions are keyed by **SHA-256, not by path**, so re-running into a
fresh folder keeps them, a file found in three places is categorized once,
and moving the output loses nothing. The Project VIC 2.0 (US) codes 0-5 are
seeded and locked — they mean the same thing in every case and in the
exchange format — and an examiner's own categories start at 6. Snapshots on
demand. In the gallery: a category bar and flag buttons in the details pane,
autosaving notes, number keys to categorize, the cursor advancing to the next
file, a color strip on each tile and a category filter.

**Project VIC export now carries categories.** It always wrote `null`,
because there was nowhere to read a decision from. Where a review has
happened, `Category`, `IsPrecategorized` and `PrecategorizationSource` are
filled in from `case.db`. The tool still classifies nothing itself.

**Three similarity tiers, not two (`similarity.py`)** — a **visual stack**
now sits between exact duplicates and the browsable similar group: both the
difference hash *and* a new DCT perceptual hash must agree. One hash alone is
easy to fool. Stacks are computed inside each similar group, since a stack is
by definition a subset of one. **Featureless images** — flat colors and
gradients — are excluded from grouping entirely: they look alike to any
perceptual hash and would otherwise clump together.

**More containers** — bzip2 and xz streams are decompressed and scanned, and
7z archives are opened when `py7zr` is installed and counted as skipped when
it is not. RAR is still detected and not opened.

**HTML contact sheet and MD5 list (`report_html.py`)** — one self-contained
HTML file with embedded thumbnails, the metadata, hash-set hits and tags per
tile; and a distinct-hash CSV. Without Pillow the pictures are linked instead
of embedded, and the report says so rather than looking broken.

## v36

**American English throughout.** Every British spelling in the code,
comments, interface text and documentation is now American: unrecognized,
analyze, color, behavior, categorized, canceled, judgment, summarize,
neighbor, normalized, totaling, recognizable.

One of those was more than a spelling. `cancelled` was a job status and a
key in `manifest.json`, not just prose, so renaming it to `canceled` would
have made a run stopped by an earlier version look complete. Both spellings
are accepted when reading a manifest, and only the American one is written.
`similarity.analyse()` is likewise now `analyze()`.

## v35

**Layout**
- The settings column is **sticky and scrolls on its own**, with a visible
  scrollbar. Scrolling a gallery of fifty thousand thumbnails no longer
  carries the settings off the screen, and a long options list scrolls within
  its own column rather than moving the results.
- The **details pane is a column on the right**, not a block above the
  results. It opens when a file is clicked, takes grid space only while it is
  open, and has its own close button. The selected file is highlighted so it
  is obvious which one the pane is describing.
- The tab bar is sticky, so switching tabs never means scrolling back up.
- Under 1400px the details pane drops below the results rather than squeezing
  them; under 900px everything stacks into one column.

## v34

From a review of what comparable tools offer.

**Added**
- **File metadata**: EXIF capture time, camera make and model, GPS and pixel
  dimensions from JPEG/TIFF/raw and HEIC; creation time and location atoms
  from MP4/MOV. No dependency: the formats are read as bytes, and malformed
  input returns nothing rather than raising.
- **`locations.kml`** written by any run with geolocated media, and
  `kml_export.py` for KMZ with the pictures embedded.
- Capture time, camera and coordinates as their own columns in the LAVA
  export, a **Has GPS** filter in the gallery, and the fields in the details
  pane with a map link.
- Hash sets now exclude the hashes of a zero-byte file. Every empty file has
  the same MD5, SHA-1 and SHA-256, so a set carrying one would flag every
  empty file in a run.

**Fixed**
- The EXIF block inside a JPEG was being carved as a tiny separate `.tif`.
  It is the photograph's own camera tags, not an image.

**Considered and not taken**: video key-frame extraction and face or
skin-tone screening. Both need OpenCV and a model, which is a much larger
dependency than Pillow, and screening in particular is a triage aid whose
false positives cost an examiner more than it saves.

## v33

First numbered release. Everything below was already present; it is recorded
here so later versions have something to be compared against.

**Recovery**
- File system, carving, SQLite blobs, compound files, packages, folders and
  devices; raw/dd, split raw, E01/Ex01, VMDK/VHD/QCOW, `.ufd` chunk sets,
  UFDR, zip and tar.
- Byte-level carving by default — sector alignment misses media embedded in
  other files.
- Base64 and hex-encoded images, which no byte-level signature can match.
- Compound files opened by default: documents, archives, gzip streams,
  nested containers, EXIF thumbnails.

**Review**
- Details pane: source, type, paths, size, MAC times, hashes, engine and
  signature, exact copies, hash-set matches, similar group.
- Hash Sets tab: Project VIC, NSRL (text or RDS SQLite) and custom lists.
- Visual similarity, optional, needs Pillow.
- Open with… and Show, handing files to the desktop.
- Refresh, which re-reads a run without restarting it.

**Reporting**
- Project VIC (VICS 2.0), C4All XML, and LAVA output sets, each listing one
  record per occurrence.
- `manifest.csv`, `manifest.json`, `duplicates.csv`, `hash_matches.csv`,
  `similar_groups.csv`, and a plain-language `WHICH_FILE_IS_WHICH.txt`.
