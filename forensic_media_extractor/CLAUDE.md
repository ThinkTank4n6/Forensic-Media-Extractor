# CLAUDE.md — engineering handbook

Read this before changing anything. It is written for whoever works on
this next, which is usually a new session with no memory of the last
one.

**Dead code is not safe code.** `media_core.py` held two copies of the
parallel-carve machinery; the shadowed copy of `_parallel_carve_body`
was an improvement nobody had ever run, and promoting it recovered
nothing at all. If a function is unreachable, it is untested, and
"we already wrote that" is not a reason to trust it. The suite now
refuses duplicate definitions.

**Current focus (v2026.11.322).** v2026.11.322: the GitHub run (pytsk3, libewf, pycryptodome installed) found that a volume inside LVM or unlocked LUKS was read by nothing when TSK was present -- **a volume the vendored reader finds inside a container (`vendor_fs.inside_container`) is read by it whatever its kind**; run the suite with pytsk3 and pycryptodome before calling a reader change done. v2026.11.321 released FME under the MIT License. **Anything new that is bundled or vendored gets a line in `THIRD_PARTY_NOTICES.md`, and its license text in `packaging/licenses/` if the notices cite one**; the spec copies those and every bundled package's own license files (`packaging/notices.py`), and `tests/test_licenses.py` checks it. pillow-heif's wheels carry GPL-2.0 x265: a reason to look again before adding another binary codec. The project the vendored readers came from is named only in the README's Acknowledgments and in THIRD_PARTY_NOTICES.md; every other document still refers to it as "the other tool" (`tests/test_manual.py`). v2026.11.320 fixed what a review of .319 found (CHANGELOG, docs/AUDIT.md): the worst was a race -- opening case A then B before A answered graded A's files into B. Rules from it: **anything the page awaits and then applies takes `claimPage()` and drops the answer if `ticket !== pageTicket`**; **every route that writes by hash calls `_refuse_foreign(job, digests)` first**; machine gradings (hash sets, store, stash, C4All) call `set_category(..., only_if_undecided=True)`; grading and undo keep the stash in step through `_sync_stash`; a reader that can fail part way stores what it read through `_read_marked` (bounded "short", counted `read_short`); a progress note carries `run` (`_run_identity`) and a resume of a different run is refused; `_case_for` checks the case.db is still the same file. The page: `body.reviewing` (enterReview/leaveReview) hides the form and panels once a case is open; Display controls are homed in the stash at load (`homeDisplayControls`) -- still one copy each; `rememberAction` feeds Undo for flags and notes; `toast()` keeps a history. `tests/test_review_layout.py` checks the layout in Chromium at 1280 x 720. v2026.11.319 retired the old FAT32 browser (its `app.py`): its runs never became cases. There is one interface, `media_gui.py`; anything that starts a run must go through it, so the run is registered, gets a case.db and is checked against the hash store. `fat32_core` stays as the engine's FAT32 fallback. v2026.11.318 v2026.11.318 fixed 28 of 29 defects an outside review of .317 found (CHANGELOG, AUDIT.md). Three rules came out of it. **A grade is a number in one scheme: anything that carries a category from one place to another -- the stash (`region` column), a store match (`region`), a C4All index (`index_region`) -- carries the scheme too, and `media_gui._match_region` returning None means it is shown and never applied.** **Test the route, not the function under it**: the scheme leak passed a test that called `_apply_hash_categories` directly. **Every `CaseDB` method runs under `self._guard()`**, which rolls back a write that failed part way; never `with self._lock:` there. Also: a GET naming a path (`?path=`) needs the page's header like a write (`request_guard`); content and face descriptions are kept per model (`content_search.cache_name`, `faces_<pair>`); a non-US case's locked categories are `locked=2` and it records `min_reader_build`; storyboards are decoded into `.part` folders and renamed into place; LUKS opens through `unlock.open_vendored`. v2026.11.317 v2026.11.317 carves each assembled RAID array (`carving.run_raid_carve`, finds marked `partition="raid:<label>"` and kept out of the image's resume state) and hashes every member with Hash the source; adds regional category schemes (`fme/review/regions.py`) -- **a case's codes mean something only under its scheme: ask the case (`case.uncategorized`, `case.non_pertinent`, `case.severity_rank`, `case.scheme.vic_in/vic_out`), never `casedb.UNCATEGORIZED`, about an open case**, and a hash store's codes are in `store.region`; grayscale, masks (`case.set_masks`, viewing only) and storyboards (`video_frames` board, `/api/storyboard`); and ships the three image models in `fme/models/`, resolved through `fme/engines/models.py` (Settings path first, then the bundled file if its SHA-256 matches) -- every result carries `models.LEAD`. Open: multi-device Btrfs; face search untested on real faces. v2026.11.316 v2026.11.316 fixed four more defects real fixtures found: a file the vendored reader refuses (LZBITMAP) was dropped silently -- `vendor_unread_files` now; the run's credentials never reached `VendorImageReader` -- `open_image(path, credentials)` everywhere an image is opened, carve workers included; logical evidence is read entry by entry (`sources.run_logical_entries`); RAID and multi-disk LVM sets are assembled from their members (`vendor_fs.raid_members`, `walk_raid`; `--member`, `--find-members`). A stand-in for open_image or VendorImageReader in a test must take `credentials`. v2026.11.315 v2026.11.315 added thirteen real-evidence fixtures (`tests/test_real_evidence_fixtures.py`, provenance in `tests/fixtures/FIXTURES-PROVENANCE.md`) and fixed the five defects they found: an AFF4 (a ZIP) was read as a phone package -- test `image_readers.is_aff4` before treating any ZIP as a package; LVM read by the vendored reader was reported "not opened" (`_reconcile_containers`); "could not be read" was said of volumes that were read; the vendored reader's reasons were dropped (`vendor_fs.walk` now returns `walked`/`not_walked` with notes). Open: a run takes one image, so a RAID or multi-device volume is read from one member only. v2026.11.314 v2026.11.314 split media_core.py (8,829 lines) into eleven layered modules -- engine_common, image_readers, signatures, length_parsers, carve_judging, embedded, fs_walk, deleted_recovery, carving, volume_survey, sources -- each importing only the ones before it; media_core keeps the options, run_extraction and the CLI (about 1,700 lines) and re-exports every name. The split moved statements without changing them. **Two rules come with it:** a new function goes in the lowest module whose names it uses, never back into media_core by habit; and a test replaces an engine name with `tests/_engine.py`, never `media_core.X = ...`, which does not reach the module that calls X (`tests/test_patch_targets.py` refuses it -- it found sixteen such replacements when the split was made). v2026.11.313 v2026.11.313 fixed what a third outside review found (CHANGELOG): the hash server returns only `hashsets.VIC_SHAREABLE_FIELDS` of a record, refuses plain HTTP off loopback without `--insecure`, and logs unrecognized tokens; every stored name goes through `OutputManager._dest`, which now cleans it and refuses a path outside the case -- **keep every new stem going through store()**; a run records the acquisition's own hashes and, with `hash_source`, verifies the source (`fme/engines/evidence_hash.py`), and `manifest.json.sha256` sits beside every manifest; read-only SQLite opens go through `fme.sqlite_uri.ro_uri`, never `f"file:{path}"` (`tests/test_sqlite_uri.py` refuses one); one folder-walk depth limit (`WALK_DEPTH_LIMIT`). New: View → Case map (MapLibre, assets in `fme/web/ui/maps/`). v2026.11.312 v2026.11.312 gave the hash server's page an NSRL and a Project VIC section with counts by category, and fixed three ways a Project VIC set lost its categories on import -- read a VICS Category through `hashsets.vic_category`, never `isinstance(int)`. v2026.11.311 closed the two items v2026.11.310 left open (reprocess buttons; C4All stale disc folders). v2026.11.310 fixed what a second outside review found (CHANGELOG); a file read short or across zero-filled sectors is now bounded "short" wherever it is stored -- keep that true of any new reader. Before that (v2026.11.309): The gallery pages from the server for a
case past 20,000 files; AUDIT.md "Open" 3 is what a very large case still
costs. Before that: BitLocker, LUKS and ReFS are done — each turned out
to be "the volume reports nothing and an examiner cannot tell that
from an empty disk" rather than a missing fixture. The
duplicate-definition sweep is done: an AST check of every module
outside `fme/vendor/` finds no name defined twice. v2026.11.272
fixed what an outside review found (CHANGELOG); the parallel carve now
shares one judge of a header (`_carve_candidate`) and one scan
(`_detect_range`) with the serial carve -- **keep it that way**, since
the copies drifting apart is how the parallel carve came to lose files,
and `tests/test_parallel_merge.py` is the check. Next: the open question
of whether to wire up the vendored BitLocker unlocker. The
six examiner sessions in `docs/EXAMINER_TASKS.md` are waiting on
hardware this environment does not have; until they are run, NTFS,
exFAT, APFS, HFS+ and E01-through-libewf are untested by the people
who wrote them.

**The carve has one judge of length, and it is mediacarve** (v2026.11.274,
`fme/engines/carve_judge.py`), reached only through `_carve_candidate`.
Since v2026.11.275 the embedded scan calls `_carve_candidate` too, over a
`BytesReader`, rather than its own copy of the logic -- so there is no
second judgment anywhere a length is decided; keep it that way. Every
carved and embedded record carries `bounded` (header / parsed / capped).
**A volume TSK refuses goes to `fat32_core`** (v2026.11.276): TSK follows the FAT specification's cluster-count rule and refuses FAT32 volumes under 65,525 clusters, which includes both committed FAT fixtures -- so on a machine with pytsk3 those fixtures test `fat32_core`, not TSK. `make_media_test_image.py --spec-fat32` builds one that is FAT32 by the specification (v2026.11.277); `tools/verify.py` uses it to compare TSK and `fat32_core` file by file. Checks that count files are how three FAT checks passed while TSK read nothing: name the files and the reader.
**There is one PyInstaller spec, `packaging/fme.spec`**, built by
`build_exe.py` (the documented command) and `packaging/build.py`. A second
spec at the root drifted until its builds had no interface page, no
vendored readers and no OpenCV; `tests/test_packaging.py` now refuses a
second spec and checks that every by-name import resolves on the spec's
pathex, not merely that its name is written there. Test fixtures must be
media that decodes: `make_jpeg` was header stubs, and the carve passed every
check on them while being wrong about real JPEGs.

**What this program is for.** A digital forensic examiner recovers
media from seized devices and grades it, and the output goes into
criminal proceedings. A silent wrong answer here is worse than a loud
failure, and "the tests pass" is not the same as "it works". Several
features in this repository were once shipped complete, tested, and
unreachable from the program.

---

## 1. Architecture

A Python package with one engine and several faces. `fme/engines/`
reads evidence — file systems, carving, phone extractions, containers,
databases — and writes a manifest; `fme/review/` holds what an examiner
decides about it, in a per-case SQLite file, plus the shared hash store
and its server; `fme/exports/` turns a finished case into reports and
into the formats other tools read; `fme/web/` is a Flask application
serving a single-page interface that drives the engine over HTTP.

Nothing in `engines/` knows about the web, and nothing in `exports/`
changes a case. The dependency arrow points one way: web → exports →
review → engines.

## 2. Entry points and what flows where

| you run | what happens |
| --- | --- |
| `python -m fme gui` | the Flask app; everything an examiner does |
| `python -m fme extract -i <source> -o <dir>` | one run, no interface |
| `python -m fme version` | version and a fingerprint of the code present |
| `python -m fme doctor` | what is installed on this machine |
| `python -m fme hashserver --store <db>` | the shared hash store |
| `python tools/verify.py` | what this machine can actually read |
| `python tools/validate.py` | the validation protocol, writes a record |

**The flow of a case.** A source is opened by a reader
(`ImageReader`, the vendored acquisition readers, or a folder) →
`run_extraction` walks file systems, recovers deleted entries, carves
unallocated space and looks inside containers → every recovered file
goes through `OutputManager.store` (`fme/engines/output.py`, re-exported
by `media_core`), which hashes it as it writes and commits its record to
`manifest.db` (one transaction per file, or per duplicate together with
what it taught the kept file) and then appends the same row to
`manifest.csv` → `manifest.json` is written at the end
→ the examiner's decisions live in `case.db` beside it, never in the
manifest → reports and exports read both.

**The files that matter most.** `manifest.db` is the record
(`fme/engines/manifestdb.py`, since v2026.11.306): resume and the rebuild
of an interrupted run (`fme/review/recover.py`) read it. `manifest.csv` is
written row by row beside it for people, Excel and the live gallery, and
`manifest.json` at the end; a case from before v2026.11.306 has only the
CSV, and both readers still accept that. A case folder with no
`manifest.json` is not an empty case.

## 3. Conventions

- **Comments say why, not what.** A comment explaining what the line
  below does is noise; one explaining why it is that way, or what went
  wrong when it was not, is the only documentation a later session
  gets.
- **American English** in everything the examiner reads.
- **A key is never written down.** Passwords and key files for encrypted
  volumes live in an `unlock.Credentials` for one run: a parameter of
  `run_extraction`, never an option, never in stats, the summary, the
  progress note, the log, `JOBS` or a report. Record which volume was
  opened and with what kind of key (`unlock.opened_line`), never the
  secret. `tests/test_unlock.py` searches every file a run leaves for the
  password; a change that fails it has leaked one.
- **Times go through `timeutil`, in and out.** A record's `captured`,
  `crtime`, `mtime`, `atime` and `ctime` hold instants only; a zone-less
  reading (FAT, a camera clock) lives in `recorded_times`.
  `timeutil.settle()` sorts them in `OutputManager.store`; read them with
  `timeutil.stored()`, `instant()` or `reading()`, never `rec["mtime"]` or
  `rec.get("captured")`. Three releases running fixed a reader that cut a
  time's string and lost or misused its zone; `tests/test_time_model.py`
  now refuses any reader in `exports/`, `review/` or `web/` that goes around
  the accessors.
- **Tests are scripts, not a framework.** `python tests/test_x.py`,
  prints what it checked, exits non-zero on failure. Each is readable
  top to bottom as a statement of what is true.
- **Every test uses its own directory**, `"/tmp/name-" + str(os.getpid())`.
  Fixed paths let concurrent runs delete each other's work, which cost
  nine sessions of a phantom bug.
- **The version is one incrementing integer.** No major or minor part:
  the numbers must compare numerically for the hash store's version
  floor, and a `1.4.2` would advertise a compatibility promise this
  project does not make.
- **Bump `VERSION` and `fme/VERSION`, add a CHANGELOG entry, and
  repackage** for every change that ships. Releases are numbered
  Year.Month.Build (`2026.11.271`): the year and month of the day the
  release is made, and a build counter that carries on from the old single
  integers and never resets. `pyproject.toml` carries the same string. Code
  that compares releases uses `fme.build_number()`, never the first number.
- **Vendored code is never edited.** `fme/vendor/` is third-party and
  must stay byte-identical to what was published; `tests/test_vendor.py`
  enforces it.

## 4. Before you call anything done

```
python tests/test_<the one you touched>.py     # while working
for t in tests/test_*.py; do python3 "$t"; done # before packaging
python tests/test_browser.py                    # if you touched the UI
python tools/verify.py                          # if you touched a reader
```

Then unpack the zip somewhere clean and run the suite again from
there. A test that passes in the working directory and fails from the
package has found a file that was never shipped, and that has happened
here.

## 5. Gotchas — the things that have actually bitten

**Shipping something unreachable.** Five features were written, tested
and never wired to anything an examiner could press. Before claiming a
feature exists, press the button or call the endpoint. `grep` for the
function name from the interface.

**media_core re-exports; it does not own.** Since v2026.11.314 most of the
engine lives in the modules media_core imports from. `media_core.X` reads
the right object, but `media_core.X = stand_in` rebinds only media_core's
name: carving.py and the rest still call the real X, and a test that did
that passes for a reason other than the one it states. Use
`tests/_engine.py` (`replace`, `restore`, `replaced`), which rebinds X in
every engine module that binds it.

**Route modules copy, they do not reference.** `fme/web/routes_*.py`
copy media_gui's namespace at import. Shared mutable things -- the
app, the job table -- work. Anything media_gui **rebinds** does not:
the copy keeps its import-time value. `OUT_ROOT`, `STORE_PATH` and
`DEFAULT_IMAGE_PATH` are rebound at run time and must be read as
`_core.NAME`. The examiner found this as "the results folder cannot
be changed".

**Moving code out of `media_gui.py`.** The route modules
(`fme/web/routes_*.py`) are imported at the end of `media_gui.py`, and
each binds the names it needs from there. A module-level constant that
travels with a route leaves the page's sight and the page 500s —
`CONVERTIBLE` and `THUMB_EDGE` did, then `REPORTS: dict = {}` did,
because the guard checked `ast.Assign` and that is an `ast.AnnAssign`.
`tests/test_endpoints.py` now refuses a route file containing anything
but routes.

**A large case is never on the page.** Past `PAGED_THRESHOLD` (media_gui)
`files` is empty and `paged` is true: the gallery is `pv.files`, the page the
server sent, and `visible()` returns only it. Anything new that needs more
than the page -- a count, every match, a group -- asks `/api/gallery/query`,
and the server's answer must be the page's own (`tests/test_gallery_parity.py`).
Use `caseSize()` and `lookup()`, not `files.length` and `files.find`. Run
the browser tests with `FME_PAGED_THRESHOLD=0` as well as without:
`tests/test_gallery_paged.py` is the paged path's own check.

**The page is three files.** `fme/web/ui/page.html`, `styles.css`,
`app.js`, assembled at import. `app.js` is real JavaScript — run
`node --check` on it.

**The interface's controls exist once.** The Display tab moves the real
controls into the ribbon rather than rebuilding them; the ribbon
redraws with `innerHTML`, so they are rescued into a hidden holder
first (and put there at load, `homeDisplayControls`, so the toolbar does
not change when the tab is first opened). Two copies of a control means
one stops working.

**`pytsk3` is The Sleuth Kit.** Comparing our NTFS output against
`icat` is the same library answering twice. The independent pair is
`pytsk3` versus the vendored reader (`FME_NO_TSK=1`).

**Ask `tsk_enabled()` and `tsk_kinds()`, never `pytsk3 is None`.** The
decision "does TSK read this run" was made in five places and only two
honored `FME_NO_TSK`, so on every machine with pytsk3 the cross-check
compared TSK with itself (v2026.11.306). And "TSK is installed" is not "TSK
reads APFS": the pytsk3 on PyPI has no `Pool_Info`, so `tsk_kinds()` leaves
APFS to the vendored reader. `tests/test_tsk_switch.py` and
`tests/test_real_volumes.py` are the checks.

**This environment can have TSK and libewf.** PyPI is not reachable, but
GitHub is: libewf from its release tarball (`./configure --enable-python`),
pytsk3 from the py4n6/pytsk repository with sleuthkit cloned at the tag
its update script names, and TSK's own tools from the sleuthkit
release tarball. Run the suite with them and without them (a
`PYTHONPATH` directory holding a `pytsk3.py` and `pyewf.py` that raise
ImportError hides them): the four defects in v2026.11.306 were visible
only with them.

**A format with no length field can carve one frame long.** AMR and
SILK have no length anywhere, so the end is found by walking frames,
and unrelated bytes after the file pass that test about 1.8% of the
time. `docs/VALIDATION.md` §5.11.

**Custom signatures are interface-only.** They are saved through
`/api/signature/save` and applied to a run started from the page. There
is no `--signatures` flag on `extract`, so a command-line run cannot
use them.

**Claims in documentation are checked too.** `tests/test_manual.py`
fails if the validation document stops saying what has not been
tested, or if its limitations section becomes shorter than its claims.

**A resume point is the first thing not yet stored, not the first thing
not yet scanned.** The parallel carve's note once said the first
unfinished stripe while the merge still held records below it; a cancel
there lost them to both runs (v2026.11.278). Anything that writes
`run_state.json` must keep every unstored find in front of the offset.

**Never rewrite a case file from a partial read.** `OutputManager` with
`resume=True` rewrites manifest.csv and duplicates.csv from what it read;
a read that stops early must refuse (`ManifestUnreadable`), not return.

**Hash store writes roll back on error** (`_rolls_back`). Each thread has
its own connection and the servers keep their threads, so a write left open
by an exception holds SQLite's only write lock until that thread commits
again -- every other writer meanwhile gets "database is locked".

**A reader that can hang or crash runs through `fme/engines/isolated.py`.**
The ESE reader loops forever on a truncated file; the texture decoder can
segfault. In-process, either ends the examiner's session. Add a worker to
`isolated.WORKERS`, never a bare subprocess: that is what makes it work in a
frozen build, where there is no interpreter to call.

**An SQLite connection that is only read from must not hold a transaction.**
`fme/review/fileindex.py` first leaked Python's implicit transaction from an
`INSERT`, which pinned a snapshot of the attached case.db: gradings stopped
reaching the page and nothing errored. Connections there use
`isolation_level=None` and explicit `BEGIN`/`COMMIT`.

## 6. Debugging: hypotheses before changes

When something fails and the cause is not obvious, **write down five
hypotheses before changing a line**, each with how to tell whether it
is true, and test the cheapest one first.

This is not ceremony. The intermittent test failure in §5 took nine
sessions because each session took its most plausible hypothesis,
fixed something real, and shipped — twice fixing a genuine bug that
was not the cause. The actual answer, that the test's own random
padding was being read as an audio frame, would have been on any
five-item list, and the test for it was thirty seconds of arithmetic.

Two rules that came out of that:

- **Instrument before fixing.** Add the counter, reproduce, read it.
  The probe that cracked the AMR bug did so by producing *nothing* —
  which ruled out the half of the search space I had been working in.
- **A fix that does not change the symptom was not the fix.** Keep it
  if it corrects something real, say so plainly, and keep looking.

## 7. Where the honest state of things is written

- `docs/VALIDATION.md` — what the program does, how it was tested,
  and at equal length what it does not do. Written to be disclosed.
- `docs/EXAMINER_TASKS.md` — what only the examiner can run, because
  this environment lacks the hardware or libraries.
- `docs/AUDIT.md` — comparison with the other tool in this space, and
  open findings.
- `docs/DEPLOYMENT.md` — installing, versions, the shared store.
- `CHANGELOG.md` — every change, including the ones that were wrong.

When you find that something does not work, write it in the relevant
document in the same change. A known limitation is a feature; an
unknown one is a liability.
