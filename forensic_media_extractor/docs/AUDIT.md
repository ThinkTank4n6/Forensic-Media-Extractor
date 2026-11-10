# Comparison with the other tool in this space

**This program at v2026.11.320, against that tool at 2026.5.7.**

Last rewritten at v139; the table below re-measured at v2026.11.294 (it
had said v214 against 2026.5.6 for eighty releases). A comparison that is
wrong is worse than none: this one is used to decide what to build next.

This is an internal working document. It names another tool and should
not be handed out with the rest — `docs/VALIDATION.md` is the one
written for disclosure.

---

## The two, by size

Measured the same way for both: Python outside `vendor/` for own code;
`vendor/*.py` for vendored; the page's HTML, CSS and script for the
interface; `tests/test_*.py` for tests; files under `tests/fixtures/`.

| | theirs (2026.5.7) | ours (v2026.11.318) |
| --- | --- | --- |
| own code | 27,720 lines in 59 modules | 48,606 lines |
| vendored | 53,918 lines in 6 files | 53,918 lines in 6 files — **the same six** |
| interface | 7,688 lines of page and script | 7,970 lines of page, stylesheet and script |
| tests | 99 files, 25,337 lines (985 pytest tests) | 195 files, 30,959 lines |
| test fixtures | **69** | 73 (34 of them theirs, byte-identical) |
| documentation | 3,096 lines: a README and a manual | 16,871 lines across ten documents |

Re-measured at v2026.11.318 (the table had said v2026.11.294). Ours does the
overlapping work in nearly twice their code; part of that is what they do
not do (logical evidence, Word and PDF reports, the hash server, C2PA
verification), and part is not.

Since v214: the vendored readers are now identical on both sides (ESE was
the last one ours lacked); our test files went from 39 to 131 and passed
theirs, while their fixtures went from 40 to 69. Ours, counted the same way
(files under `tests/fixtures/`) at v2026.11.313, are 35: the "4" this table
carried for eighty releases was the count before the encrypted volumes,
the E01s, the DASH set and the Windows NTFS image were added. Still the gap
that matters most.

## What they do that we do not

**Application artifacts -- corrected at v2026.11.318: they do not.** An
outside review found no chat, SMS or call-log parser in their code; the
paragraph below, kept as history, was wrong.

*Formerly:* **Application artifacts.** Chat databases, call logs, account records,
browser history. We recover media and stop. This is the largest
difference by volume of code and it is deliberate on our side: that is
ALEAPP and iLEAPP territory, and a media tool that half-parses a chat
database is worse than one that does not try.

**Their own file-system readers, with no C extension.** No `pytsk3`
anywhere: FAT, exFAT, NTFS, HFS+, HFSX and APFS are read by their own
code, so a machine can never be quietly missing file-system support —
which is exactly the hole `tools/verify.py` exists to find on ours.
**We carry the same capability and use a fraction of it:** our vendored
reader identifies and walks those file systems too, and until v171 we
restricted it to the exotic ones. It now covers the mainstream ones
where TSK is absent, but TSK is still the primary path.

**Offline basemaps** (`basemaps.py`, `staticmap.py`, `mvt.py`). *Adopted
in v2026.11.306 for the report and the details pane, and in v2026.11.313
for an interactive case map (View → Case map): their MapLibre, pmtiles.js,
layer styles, fonts and sprites, vendored unchanged under
`fme/web/ui/maps/`.* Ours follows the gallery's filter on the server, so it
works on a paged case of any size, and puts no thumbnail on the map.

**Project VIC record detail** (`vicdetails.py`). They keep what a VIC
record says beyond the category — MediaID, Series, the five flags,
Tags, the source tool's Exif reading — and show it against a matched
file. Since v2026.11.280 we do too, whether the match came from the
shared store or a set loaded for the session, in the details pane and
both match CSVs; before, only the store kept it. The source tool's Exif
reading is the part we still do not keep.

**Windows Search on Windows 10 and earlier.** *Closed in
v2026.11.293: ours now reads `Windows.edb` too, with the same vendored
reader, in a separate process (VALIDATION.md 5.15).* Theirs reads
`Windows.edb`, the ESE database, in an isolated worker process because
the ESE reader can loop forever on a truncated one.

*Before v2026.11.293* we read only the SQLite index Windows 11 22H2 and
later use, and refused a `Windows.edb` with an explanation, so Windows 10
exhibits' thumbnail caches could not be named. That paragraph is kept as
history; the gap is closed, but against a stand-in reader only until
EXAMINER_TASKS.md Session G is run on a real index.

**A two-stage similarity index** built from thumbnails already made.
*Re-examined at v2026.11.294, and narrowed.* The hours were not in the
grouping -- which was also missing up to 68% of pairs, fixed in .293 -- but
in the hashing: every scan decoded each picture four times and kept
nothing. Ours now decodes three times on the first scan and not at all on a
rescan (hashes kept in case.db by content, bit-identical to before). What
their index still does that ours does not: find a picture inside another
(a crop, a screenshot of it) with SIFT points. That is a capability, not a
speed problem, and is not planned.

**Times kept by kind.** *Adopted in v2026.11.298.* Theirs stores instants
and the zone-less readings FAT and exFAT record in separate columns, so code
cannot mistake one for the other. Ours kept both in one field, and three
releases (.295-.297) each fixed a reader that cut the string and lost the
difference. Ours now does the same (timeutil.settle and its accessors), with
a test that refuses a reader going around them.

**Signed, notarised builds on three platforms.** Ours is a PyInstaller
spec and a zip.

**Regional category schemes.** *Adopted in v2026.11.317.* Theirs offers
seventeen countries' and agencies' schemes; ours offered only Project VIC
(US). Ours now takes the same seventeen, a case keeps its scheme for life,
and -- going further than theirs -- a hash store holds one scheme and
refuses a case graded under another, and a Project VIC export of a
non-US case carries only Non-Pertinent rather than equating, say, the UK's
SC Category A with VICS 1.

**Bundled image models.** *Adopted in v2026.11.317.* Theirs ships
DINOv2-small, YuNet and SFace; ours asked for paths nobody had set. Ours
now ships the same three files with their licences, checks each by SHA-256
before use, and labels every result an investigative lead only.

**From the other review tool in this space: grayscale, region masks and
video storyboards.** *Adopted in v2026.11.317.* Review aids for an examiner
looking at material all day: every picture in grey, part of a picture
covered wherever it is shown (kept with the case, audited, never applied to
the file), and a video as 48 timed frames that open it at that moment.

## What we do that they do not

**C2PA signature verification.** They read Content Credentials; we
verify the COSE signature and report three outcomes — invalid, valid
but untrusted, valid and trusted against a trust list the examiner
supplies. We ship no trust list.

**Resume.** They have none. A run of ours that is interrupted records
which phases finished and how far the carve reached, and continues from
there; a resumed run returns the same files as one that was never
interrupted, which the suite checks.

**Rebuilding an interrupted case.** `manifest.csv` is written row by
row, so a case whose run died can be rebuilt from the rows already
written, and says in its own summary that it is partial.

**A validation protocol and a known-limitations statement written to be
disclosed**, a validation log that is appended and never edited, and a
tool that runs the protocol and records what did *not* run. They have
none of this. It is the largest difference in favour of this program
and it is not a feature — it is the difference between a tool an
examiner uses and one a unit can stand behind.

**Known-answer fixtures**, with the expected result recorded before
deletion, and two independent readers compared on the same volume
(`FME_NO_TSK=1`). Their fixtures record hashes before deletion too —
that approach is theirs and we took it — but their cross-check is
against The Sleuth Kit, which for us would be the same library
answering twice.

**A shared hash store** with accounts, roles, an audit of who looked up
and contributed what, a client version floor, and an integrity check
with a written recovery procedure. Theirs is local.

**Deleted FAT entries recovered by a pass of their own**, separate from
the file-system walk, because TSK's entry for a deleted FAT file can
carry no size and its walk then finds nothing.

**Saying when a volume cannot be read.** A BitLocker, LUKS or ReFS
volume reports no files — which is also what an empty disk reports.
Ours names the encryption or the file system and why the walk failed,
in the log and in the manifest.

## Where the old version of this document was wrong

Recorded because it was used to decide what to build, and it was wrong
in both directions.

- It said they were stronger at phone extractions. True when written;
  after v141–v156 it is no longer a useful summary. We read Cellebrite,
  GrayKey, Magnet and Android backups, fold Android mount points,
  rejoin DASH and HLS, and record the path on the device.
- It said "we open archives but not documents". That was never true.
- It claimed comparing against The Sleuth Kit gave two independent
  readers. `pytsk3` **is** The Sleuth Kit. Caught by the examiner
  reading the validation document, not by its author.

## SETTLED: the intermittent failure was never concurrency

Nine sessions of "it failed in the batch and passed alone". The test
padded its image with `os.urandom`, and AMR has no length field, so its
end is found by walking frames. Random bytes after the file pass the
frame test about 1.8% of the time; the carve takes one more frame; the
hash changes; the test reports the file as lost. Load only meant more
rolls of the same dice.

Two real faults were found chasing it and are fixed: twenty-seven tests
shared fixed `/tmp` paths and wiped each other's working files, and the
parallel carve's stripes abutted with no overlap, so a file whose
header straddled a boundary was recovered by neither worker. The second
was genuine evidence loss, unrelated to the symptom. The behaviour
itself is now a stated limitation, `docs/VALIDATION.md` §5.11.

## An outside review of the concurrency paths (v2026.11.278)

Sixteen findings across the parallel carve's merge, resume, the progress
note, the GUI's job table and the hash store, all fixed in v2026.11.278 with
a test each; CHANGELOG has the list. The two that could lose evidence: a
cancel during the merge wrote a resume point past files never stored, and a
resume rewrote the manifest from whatever part of it had read before an
error. Not covered by that review: the vendored readers, the exports, and
the page's JavaScript. One more finding from the same work -- a carved file
cut short by an unreadable stretch was stored without a word -- was fixed in
v2026.11.279.

## A review of the exports, the page and the vendored readers' output (v2026.11.281)

The part of the program the v2026.11.278 review left out. `export_files`
overwriting a file of the same name was fixed in v2026.11.282, and the
Project VIC export's totals and duplicates in v2026.11.283. The finding
fixed in v2026.11.281: times the evidence recorded with no zone -- FAT file
times, camera capture times -- were given one on the way out. The report
read them as UTC and converted them, the Project VIC export wrote them as
UTC, and C4All and LAVA read them in the examining machine's zone; the
page alone showed them as recorded. NTFS and MP4 times, which are UTC,
were stored with the zone stripped, so nothing downstream could tell the
two apart.

Coverage, honestly: `export_files`, `projectvic_export`, `c4all_export`,
`lava_export`, `kml_export` and the report's time handling were read; the
rest of `report.py` and `protocol.py` were not read line by line. In
`app.js`, the places evidence-derived text reaches HTML were spot-checked
and escaped; the file was not reviewed whole. The vendored readers were
reviewed only for what the calling code does with their output: their
time strings pass through `_vendor_time` unchanged, so they are covered by
the same rule, but whether each one returns UTC, local or zone-less times
was not established.

## A code review of v2026.11.302

Fixed in v2026.11.303; the CHANGELOG lists each finding. The worst was in
the shared hash server: a remote scan merged matches per set across a whole
request, so most files of a set matched through the server were reported
unmatched while a local scan found them all. Coverage: `hashstore`,
`hashserver`, `remotestore`, `hashsets`, `casedb`, `registry`; the web
routes and `cli.py`; in `media_core` the output manager, resume and the
parallel carve; `carve_judge`, `recover`, `fat32_core`, `ntfs_mft`,
`containers`, `unlock`, `timeutil` and the file, Project VIC and C4All
exports. Not addressed and still open: a second media extraction of the
same image can be started from the FAT32 tool's page (closed in
v2026.11.319: that page was retired), and releasing the
accepted image while an extraction is starting can tell the examiner it was
released while the run goes on.

## A second code review (v2026.11.303)

Fixed in v2026.11.304 (CHANGELOG). It covered the v2026.11.303 patch
itself, the exports not reviewed before (report, protocol, LAVA, KML, VIC
and C4All import), the engine modules outside the carve and file systems,
and the review, settings, registry and packaging code. The most serious
finding was auto-grading's severity order putting Non-Pertinent first.
Not done: the shared store's `/admin` counts each 5,000-row batch from a
v2026.11.303 client as one contribution, and a contribution that fails part
way leaves its earlier batches stored (sending it again is harmless).
Content Credentials bound by `c2pa.hash.bmff` (video) or `c2pa.hash.boxes`
are reported as not checked.

## A comparison review (v2026.11.304)

Fixed in v2026.11.305 (CHANGELOG). The most serious finding: a write that
failed -- a full output drive above all -- dropped the file with one log
warning and no count, and the run reported success; an evidence read error
was reported as a write error. Also fixed: a file a crash left with no
manifest row was in no export and was stored a second time by the resume;
qnxprobe was a release behind the comparison tool's; and output management moved out of
`media_core.py` into `fme/engines/output.py`.
Then deferred, and done in v2026.11.306: the SQLite case record.

## Run with The Sleuth Kit and libewf (v2026.11.306)

The first suite run with TSK and libewf installed -- built from source,
since this environment has no PyPI -- found four defects that only show on
a machine that has them, which is every lab machine:

1. `FME_NO_TSK=1` did not switch TSK off in the file-system walk, so the
   cross-check between the two readers compared TSK with itself. One
   decision now (`tsk_enabled`); every run's summary says which it was.
2. The pytsk3 published today has no `Pool_Info`, so TSK cannot open
   APFS; and with pytsk3 installed the vendored reader was not asked for
   APFS. A Mac's APFS volume was read by nobody. `tsk_kinds` now leaves
   whatever TSK cannot read to the vendored reader.
3. TSK lists the gap before the first partition as a partition at offset
   0; tried first, it credited the first FAT partition's files to "p0".
4. Both readers stored a volume's upcase table ($UpCase, $UPCASE_TABLE)
   as a picture: it begins 00 00 01 00, as an icon does.

Also: a file deleted with its exFAT folder now keeps the folder's name, and
FAT short names written with case bits read as the operating system shows
them. Volumes written by Windows 11 and by Linux's drivers, and an E01
written by ewfacquire, are now read both ways in the suite.

**TSK stays.** Replacing it with the comparison tool's approach -- its own
readers only -- was considered and rejected on the evidence: on every
operating-system-written volume here the two readers now return the same
files, and the agreement of two readers that share no code is the
strongest claim this program can make about a file system. Removing one
removes the claim. (mediacarve, which was suggested in its place, is not a
file-system reader: it judges where a carved file ends, and has been this
program's only judge of that since v2026.11.274.)

**manifest.db.** The case record is SQLite since v2026.11.306, one
transaction per stored file and per duplicate with what it taught the kept
file; `manifest.csv` and `manifest.json` are made from it.

**Offline basemaps** are adopted (`fme/engines/basemaps.py`, `staticmap.py`,
`mvt.py`): the report's overview and each located card, and the details
pane, draw from an imported `.pmtiles` or `.mbtiles` file and name it by
SHA-256. The comparison tool's interactive gallery map is not carried over.

## Run again with TSK (v2026.11.307)

1. **Partitions TSK refused were read as the start of the image.** They
   were handed to the FAT reader as (offset, label) tuples and it read only
   dicts, so every one became offset 0: the first FAT volume was credited
   to "p0" and every other refused partition was never walked -- its
   pictures came back carved, unnamed. TSK refuses FAT32 under 65,525
   clusters: small cards, and the EFI partition of many Windows disks.
2. **A partition's label depended on which library listed it** -- "p1"
   from the table, "p2(Win95 FAT32 (0x0c))" from TSK, whose numbering
   counts the table and every gap. One label now, from the table's slot.
3. **A deleted FAT name's lost letter** was "_" from TSK and from the FAT
   walk, "?" from the deleted pass -- and "_" is a letter a name can begin
   with. "?" everywhere now, and the real-volume test requires both readers
   to name every file the same way.
4. **macOS `._` sidecars** on cards a Mac has touched were stored as
   pictures by their extension.

## A sweep over other containers (v2026.11.308)

Every acquisition format and media layout in the comparison tool's fixtures
was run through the program. The encrypted AFF and the certificate-sealed
AD1 stop with a clear message asking for the key, as intended. Two defects:
an MPEG transport stream running to the end of an image was never carved,
and a folder of DASH segments yielded only its unplayable initialization
segments. Both fixed in v2026.11.308.

## An outside review of v2026.11.317 (v2026.11.318)

Four reviewers, each given only the code and a scope -- the evidence engine,
the case and hash-store layer, the web interface, and a comparison with the
other tool -- found 29 defects in v2026.11.317, none caught by its 191 tests.
Three could put a grade on a file nobody chose: grades crossing regional
schemes through the stash and the store scan; the full-size viewer grading a
file it was not showing; and a C4All import recorded as the examiner's own.
All but one are fixed in v2026.11.318 (CHANGELOG); the one left is the
checked-then-opened gap in the bundled models (VALIDATION 5.6c). Two lessons
worth keeping: the top-ranked defect passed `tests/test_regions.py` because
that test called the function under the route rather than the route, and
the face search had been written, tested and shipped with nothing in the
page that could start it -- the "unreachable feature" of section 5 in
CLAUDE.md, again.

From the comparison, closed here: LUKS now opens with its passphrase (the
vendored reader always could), and Android backups are read. Still theirs:
signed builds for three platforms, a persistent crop-finding index measured
on a real case, NSRL delta merge and CAID import, and reading large phone
extractions in place.

## A review of v2026.11.319: races, edge cases and the page (v2026.11.320)

A senior-engineer review of v2026.11.319 looked for bugs, edge cases and
race conditions, and at the interface as an examiner uses it. It found
twelve serious defects, seven smaller ones and twelve usability problems;
the suite passed over all of them. The worst was a race in the page:
opening one case and then another before the first's file list arrived put
the first case's files under the second's job, and a grading then landed in
the wrong case.db (`repro_case_open_race.py`). The others: a hash-set scan
overwriting a grade made while it ran; the stash keeping undone grades;
custom category codes carried between cases; truncated package and folder
reads stored as whole; a replaced case folder still written through an old
handle; gradings credited to the run's examiner; a resume taking another
run's progress note. All are fixed in v2026.11.320 (CHANGELOG), each with a
test that drives the route or the reader, and the page's review layout is
checked in Chromium at 1280 x 720 (`tests/test_review_layout.py`).

Lesson worth keeping: every defect of the first kind was an asynchronous
answer applied to whatever was current when it arrived. **Anything the page
awaits and then applies takes a ticket (`claimPage()`) and drops the answer
if the ticket is stale; anything the server writes by hash checks the hash
is in the case named (`_refuse_foreign`).**

## Open, in the order I would take them

*Checked item by item against the code at v2026.11.300.* The list had said
"open" for two items closed long before -- one since v218 -- and this is the
list used to decide what to build next. Closed items are kept below, with
what closed them, so they are not reopened by mistake.

1. **The examiner sessions.** Until `docs/EXAMINER_TASKS.md` is worked
   through, NTFS, exFAT, APFS, HFS+ and E01-through-libewf are untested by
   anyone who wrote them. Nothing else here matters as much. Its Results
   table is still empty, and had no row for Session G (Windows Search, the
   only test of the ESE reader against a real index); one is added. Since
   v2026.11.298 zone-less times are in `manifest.csv`'s `recorded_times`
   column: Session D (APFS and HFS+) is where a real Mac case would show a
   reader that put a time in the wrong place.
2. **Fixtures: 69 against 68** (v2026.11.316 added nineteen more of theirs --
   RAID member sets, encrypted AFF and AD1, APFS -- which found four more
   defects; v2026.11.315 added thirteen of theirs --
   AFF4, QCOW2, VMDK, VHDX, LVM2, md RAID1, Btrfs RAID1, ReFS -- each checked
   against an answer recorded outside this program, and they found five
   defects; this item said 35 at v2026.11.313, 13 before that, and the
   table above said 4). Theirs has 69 files under `tests/fixtures/`;
   ours had 4 (two images and their `.intended` answers) and since
   v2026.11.301 has 12: four real encrypted volumes taken from theirs --
   BitLocker, LUKS1, LUKS2 and a FileVault APFS volume made on a Mac, the
   first of ours made by the operating system that writes the format --
   and since v2026.11.302 13, with the BitLocker volume's startup key. Our test files
   outnumber theirs (136 to 99), but ours mostly build what they test in
   code, and code that builds a volume shares the author's assumptions with
   the code that reads it. Their approach -- real images, the answer
   recorded before deletion -- is already ours for the two we have
   (`tools/make_fixtures/`); it is the count that differs. The honest gap
   behind every other line in this document.
3. **What a very large case still costs.** The gallery pages from the
   server since v2026.11.309 (Closed, below). Measured on a synthetic
   million-file case: a page 3 ms once the filter is cached, a new filter
   0.4 s, the category and Classify counts 1 s, a search 3 s, and the
   Filters pane's facet counts 11 s (drawn after the gallery, with
   "Counting…" meanwhile). Two things are still whole-case in the browser:
   the decisions map (one entry per graded or flagged file, so it grows
   with the review, not the case) and, during a run below the threshold,
   the live list. Neither has been measured on a real review of that size.
4. **A Btrfs volume across several devices** is read from the one device
   given, and the report says so in the reader's words (v2026.11.317 added
   single-device Btrfs fixtures, all compression methods). The vendored
   reader's Btrfs walker is single-device; no multi-device fixture, or tool
   here to make one, was available.
5. **Face search on real faces.** The models ship since v2026.11.317, but
   the suite has no photograph of a person: YuNet and SFace are checked to
   load and run, not to find or match a face.
6. *(closed in v2026.11.313: the case map, see "Offline basemaps" above.)*
   Still open beside it: the map has been drawn in Chromium over a raster
   PMTiles fixture only; the vector style is checked as JSON, not drawn,
   because no vector basemap is among the fixtures.

### Closed

- **The gallery holds a page, not the case** -- *closed in v2026.11.309.*
  Past `PAGED_THRESHOLD` files (20,000; `FME_PAGED_THRESHOLD` sets it) the
  server sends no list: the page asks `/api/gallery/query` for the page it
  shows, the counts around it, every file a filter matches (Select all
  matching, a report of what is shown), every file a stacked tile stands
  for, which held files still match, and where the next page starts.
  Below the threshold the page works as before. `tests/test_gallery_paged.py`
  drives a 30,000-file case in Chromium -- paging, filtering, grading one
  file and a selection, select-all across pages, a stacked tile, running
  off a page, a held page graded whole -- and `tests/test_gallery_parity.py`
  now also holds the server's counts to the page's. The browser tests pass
  with `FME_PAGED_THRESHOLD=0`, which pages every case.

- **Encrypted volumes: detected, and opened with the examiner's key** --
  *closed in v2026.11.301 and v2026.11.302.* A FileVault-locked APFS
  volume was recorded as read and empty (fixed in .301: named wherever
  BitLocker and LUKS are). The vendored unlockers were present and
  unreachable; the unit decided it wants them, and since .302 a run takes
  a password, recovery password or key, or BitLocker startup key file,
  opens BitLocker beneath every engine and FileVault for its file walk,
  states in every report which volume was opened and with what kind of
  key, and keeps the key nowhere (`fme/engines/unlock.py`,
  `tests/test_unlock.py`, which searches a finished case for it). Not
  opened: TPM-only BitLocker, LUKS. FileVault free space is not carved
  decrypted, and the report says so.
- **Duplicate definitions** -- *closed in v218.* Twelve functions in
  `media_core.py` were defined twice, `phase_done` and `_stripe_tasks`
  among them; all were removed, and `tests/test_packaging.py` has since
  refused any function or class defined twice in the package. Rechecked at
  v2026.11.300 with an AST scan of every module outside `fme/vendor/`: no
  name is defined twice.
- **Reading ESE (`Windows.edb`)** -- *closed in v2026.11.293*, tested
  against a stand-in reader only; a real index is Session G (item 1).
- **Tests that carve random bytes and count what they find** -- *closed;
  verified at v2026.11.300.* `test_fs_walk_failure` was seeded in
  v2026.11.282. Of the three this item named, `test_carve_worker_stall` is
  seeded and finds its pictures by hash rather than counting;
  `test_containers` pads with zeros (0 failures in 160 runs); and
  `test_sized_files` carves nothing -- its random bytes are a file's
  content, stored directly. The other tests that use `os.urandom` either
  feed it to a parser that must not crash (`test_metadata`,
  `test_video_isolation`), store it without carving (`test_gui_live_gallery`),
  replace the carve with a stand-in (`test_nothing_recovered_warning`, 0
  failures in 40 runs), or use it as an image slow enough to still be
  running and check only the run's state (`test_gui_one_run`,
  `test_gui_active_and_root`, `test_carve_worker_heartbeat`). None counts
  what random bytes carve.
- **Times kept by kind** -- *closed in v2026.11.298* (see "What they do
  that we do not").
- **`test_resume` depended on the machine's load** -- *closed in
  v2026.11.299.* It killed its runs after a fixed 6 or 8 seconds and printed
  one result as a fixed "True"; it now waits for the state each check
  needs, and passed with the machine's core oversubscribed two to one,
  where the previous version failed.
