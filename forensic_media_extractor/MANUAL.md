# Forensic Media Extractor — Manual

Recovers images, video and audio from forensic images, devices, folders,
report packages and databases; records where each file came from; lets an
examiner grade what was found; and writes the reports and exchange files that
go out of the lab.

Open it with **Menu → Manual** at any time.

---

## 1. Quick start

### The ribbon

Seven tabs across the top, in the order the work happens:

| | |
| --- | --- |
| **File** | new extraction, open a case, back to the case, save, close the case; **Set up once** — hash store, hash sets, categories, agency logo; settings, what is installed, the manuals |
| **Home** | what you do to the case in hand: save, refresh, undo (gradings, flags and notes), the hash store, and the three review toggles |
| **Case data** | getting data in, and what to work out from it: hash sets, similar images, stills to videos, screening |
| **Report / Export** | the report, the files themselves, and the other tools' formats |
| **View** | which pane is open and what the gallery shows |
| **Display** | how the gallery looks: tile size, files per page, time zone, the review blur and which categories it covers, grayscale, hiding the screen |
| **Apps** | tools for the file in hand: hex view, full size, open with, show in folder, and the case's audit log |

**Back to the case** is on File, Home and Case Data whenever a case is
open: going to the hash store or anywhere else never means opening the case
again to get back to it.

An item that needs a case or a selected file is dimmed until there is one.
What an action did is said in a line under the ribbon, readable from
whichever tab you are on.
The drop-down menu is gone — everything it held is on one of these tabs,
because two ways into one thing is two things to keep working.

1. Double-click **launch.py**, or run `python -m fme` from the project
   folder. A browser window opens. With `pywebview` installed, `--window`
   opens it in a window of its own instead.
2. The program opens on **Cases**. Click **New extraction**.
3. Put the evidence in the **Image** box — type a path, click **Browse…**, or
   pick from **Attached drives and folders…**.
4. Click **Load / Inspect**. Read what it says the evidence is before running
   anything.
5. Set a **Results folder** with room. A run can produce more data than the
   evidence itself.
6. Fill in **Case number**, **Examiner** and **Evidence item**. With a case
   number the run also writes `ProjectVIC.json` and a LAVA set.
7. Click **Start Extraction**.
8. When it finishes, grade the results, then **Export report**.

---

### Running it in a window of its own

```bash
python launch.py gui --app
```

**The flag is what does it.** `python launch.py gui` on its own opens your
ordinary browser, address bar, tabs, extensions and all. `--app` is what
asks for a window of its own.

Chrome or Edge opens with no address bar, no tabs, **a profile of its own
and no extensions**. It is the same engine the ordinary browser uses — there
is no other kind of browser to switch to, and the tools that look like
native applications are the same engine with the window furniture removed.
What changes is what else is in the page:

- **No extensions.** An ad blocker or a corporate policy extension injecting
  script into a gallery of thousands of tiles is a common reason a renderer
  is killed, and nothing this program does from inside the page can defend
  against it.
- **Nothing else competing** for memory or cores. No other tabs.
- **A crash costs this window only**, not everything else that was open.

Nothing is ever lost to a crash either way: the run is on disk and every
decision is written as it is made. A window dying costs the time it takes to
reopen, which is seconds.

`--window` instead uses pywebview if it is installed, which gives a window
with no browser furniture at all. It is the same engine again.

## License and warranty

This program is released under the MIT License (`LICENSE`), Copyright (c)
2026 Christopher A. Tankelewicz (ThinkTank4n6). It is provided **as is, without warranty of
any kind**: what it reports is for the examiner to verify, and
`docs/VALIDATION.md` says what it has and has not been tested against.
**File → Licenses** shows the notices for everything it includes -- The Sleuth
Kit (Copyright (c) 1997,1998,1999, International Business Machines
Corporation and others), libewf, the image models and the map assets -- and
where each one's source code can be had. `fme version` prints the same notice.

## Times, and which zone they are in

Every timestamp is **stored with the zone it was recorded in** and **shown
in the zone the case chooses** — set it in Settings. The stored value never
changes. A time recorded with no zone — a FAT volume's file times, a
camera's capture time without an offset — is stored without one, and is
shown as recorded rather than converted (see *Timestamps that cannot be
converted*).

Daylight saving is the reason this matters. A picture taken in New York in
July is four hours from UTC and one taken in December is five. A fixed
offset puts half the year an hour out, and an hour is the difference
between somebody being at work and being at home. The zone database is used,
so the rules and their history are right.

**Every time carries its offset**, and a report states in its header which
zone it used — including when that is UTC, because "UTC" written down is a
fact and a bare time is an assumption. If the zone is one this machine does
not know, the header says so and the times are UTC, rather than naming a
zone that was never applied.

## Taking in a C4All index

**Case Data → Import C4All index.** This program could write a C4All index
and not read one, so the exchange was one-way: a set handed over by another
unit, or one you made years ago, could not be brought back in.

Files are matched **by hash**, never by name — two cases full of
`IMG_0001.JPG` would collide immediately.

**Your own gradings win.** Where a file is graded here and graded
differently there, yours stands and the difference is listed. An import
that quietly replaces an examiner's decisions with another unit's is not an
import, it is a loss.

**C4All writes 0 for "nobody categorized this"**, which is read as ungraded
rather than as Non-Pertinent — otherwise an import would mark a whole case
Non-Pertinent on a decision nobody made.

The index is summarized before anything is taken, because the numbers in it
mean whatever the scheme of the unit that wrote it meant, and you should
see whose you are about to accept.

## Two file-system engines

This program carries two readers. **TSK** (`pytsk3`), which most of the
field uses, and a **vendored reader** already in the tree that reads NTFS,
exFAT, FAT, HFS+, APFS and ext2/3/4 with no C extension installed at all.

Until v171 the vendored one was restricted to the unusual file systems —
QNX, F2FS, SquashFS, YAFFS and the rest — so a machine without `pytsk3`
could read a car's infotainment system and not a Windows laptop, while
carrying a reader for both. It now covers the mainstream volumes too
whenever TSK is absent.

Run `python tools/verify.py` and look for **"a second engine can read a
mainstream volume"**. That line is what tells you this machine is not
quietly blind to NTFS.

**APFS is read by the vendored reader on every machine.** The `pytsk3` on
PyPI cannot open an APFS container, so a Mac volume is never TSK's; since
v2026.11.306 it is the vendored reader's whether or not `pytsk3` is installed
(before, with `pytsk3` installed, nobody read it). `FME_NO_TSK=1` switches TSK
off everywhere, and the run's summary says so.

## What a report covers

Every report opens with **Report contents**: how many files it covers,
split into images, videos and other; how many in each category with the
share of the report each is; how many carry each flag; how many matched a
hash set; and how much media the case holds in total, so a reader can see
what share of it the report is.

Beside it is a ring of the categories, in **the same colours the gallery
uses**, so somebody who has seen the screen does not have to learn a
second colour scheme to read the paper.

**The HTML, the Word document and the PDF are counted once.** They are
laid out separately but they read the same numbers, because an HTML saying
ninety-six and a Word document saying ninety-five turns a hearing about
pictures into a hearing about the tool.

## A run that was interrupted

**Continue it rather than starting again:**

```
python -m fme extract -i <the image> -o <the case folder> --resume
```

Point `-o` at **the case's own folder** — the one holding `manifest.csv`,
not the folder above it — or a new case is started beside it.

A run writes where it has reached every few seconds, **and which phases it
has finished**. Resuming reopens that case, reads back the rows already
written so nothing is recovered twice, skips the phases that completed —
the file-system walk, the MFT recovery, the deleted-entry pass — and
restarts the scan from the recorded offset, backed off by one signature's
span in case a file straddled the moment it stopped.

So a run that died during the carve does not redo the walk, and a run that
died after the carve does not redo any of it.

Measured on a 320 MiB image with 48 pictures, killed part way: the
resumed run found **48, the same as a run that was never interrupted,
with nothing recovered twice.**



`manifest.json` is written when a run finishes. `manifest.csv` is written
**row by row as it goes**. So a crash, a power cut or a killed process
leaves the recovered files on disk and listed, with only the summary
missing.

The case list used to call that **folder missing**, which is what it also
says when a folder has actually gone — so an examiner was told their work
was lost while it sat on the disk in front of them.

Such a case now reads **"unfinished — rebuild its list"**. Pressing it
writes the manifest back from the rows the run had already written. The
files are the ones on disk and the hashes are the ones recorded as each
was written.

**The case is partial**: whatever the run had not reached is not in it, and
the rebuilt manifest says so in its own summary. Run the source again if
completeness matters.

## The gallery toolbar

The band above the gallery holds what an examiner touches while grading:
the filter chips and the search box. Everything else moved.

**The Display tab** holds the controls themselves — the slider, the
dropdowns and the toggles are on the ribbon, not behind a button:

| group | what is there |
| --- | --- |
| **Tiles** | the tile-size slider |
| **Page** | how many per page, and whether the page is held while a run adds to it |
| **Times** | which zone times are read in |
| **Blur** | the review blur, what it covers, and Hide the screen (the same as Ctrl-B) |

These are the same controls the toolbar used to hold, moved onto the
ribbon rather than rebuilt there, so there is one of each.

**Nothing on that tab changes a file, a grading or a report.**

**View → Gallery / Table** switches how the case is shown.
**Home → Refresh, Undo, Side pane.**

Nothing was rebuilt in its new place: the ribbon drives the same controls
by pressing them, so there is one of each rather than two that can drift
apart.

## Watching a run

Under the progress bar, a run shows what it has actually found so far:

```
walking file systems — 43,570 recovered
145,586 walked · 19,327 carved · 20 from databases · 3 unreadable
```

A phase and a percentage say what the program is doing; these say whether
it is finding anything, which is the question during a long run.

## If the hash store stops matching things

A store that has been damaged does not announce it. Lookups quietly stop
matching files they used to match, which inside a case looks exactly like
the file not being in any set.

If that is suspected, an admin runs this on the server:

```
python -m fme store --store <the store> --integrity
```

It is read-only and changes nothing. `SERVER_SETUP.md` has the full
procedure, including what to do if damage is found — and the backup
routine that makes recovery possible, which is worth reading **before**
it is needed.

A clean result does not mean the hashes are the right hashes. It means
the file is intact.

## The documents, from inside the program

**File → Help** opens every document that ships with the program:

| | |
| --- | --- |
| **Manual** | this document |
| **What it does not do** | what the program does, how it was tested, and at equal length what it does not do. Written to be handed over as it stands |
| **Checks to run here** | the checks only this machine can run, and what each proves |
| **Installing** | installing elsewhere, keeping copies in step, who holds which store account |
| **What changed** | every change and why — the answer to "which version produced this report" |
| **Hash store setup** | setting up the shared store and its server |

They were previously only files in a folder. A limitations statement
nobody opens is not a limitations statement, and an examiner asked in
a hearing what this tool does not do should be able to find the answer
in the tool.

## The categories

**You do not set these up.** Every case is created with the scheme already
in it — ten categories, each with its keyboard key — and they are
**locked**, so they cannot drift between cases or between examiners:

| key | category |
| --- | --- |
| 0 | Non-Pertinent |
| 1 | Child Abuse Material (CAM) |
| 2 | Child Exploitive (non-CAM) / Age Difficult |
| 3 | CGI Animation – Child Exploitive |
| 4 | Comparison Images |
| 5 | Adult Pornography |
| 6 | Abnormal |
| 7 | Notable |
| 8 | Uncategorized |
| 9 | Suspect |

They are on the **Classification** pane (View → Classification), with the
count of files in each, and the key beside each name is what grades the
selected file.

**If that pane is empty**, it now says which of three things it is: no case
is open, the case has no files yet, or the case's own file did not load.
The last one is worth acting on — press Refresh on the Home tab, and if it
stays empty the case folder has probably moved.

Your unit can add categories beyond these in the **Classification** pane,
under *Your own categories* (View → Classification). The ten above cannot be removed or renamed, because a
report that cites category 1 has to mean the same thing in every case.

**Outside the United States, choose your region's scheme** (v2026.11.317).
**File → Settings → Grading scheme for new cases** lists seventeen: the
United States (Project VIC, above), Australia, Austria, Canada (English and
French), Denmark, Estonia, Europol, France, Ireland, the Netherlands,
Norway, Romania, South Korea, Sweden, Switzerland and the United Kingdom,
each with its own codes and keys as the agencies distribute them. A case
is made under the scheme chosen then and **keeps it for life** — the codes
on its files mean something only in that scheme — and its name appears
beside *Category* in the details pane and as *Graded under* in every report.
A case from before v2026.11.317 is a United States case.

What changes with the scheme, and why it matters:

* **What a file starts as.** The UK's 0 is *Uncategorised*; the US 0 is
  *Non-Pertinent*. Each case starts its files at its own.
* **Project VIC sets and exports.** Only the US scheme is Project VIC's own.
  In any other case a VICS category is not taken for one of yours — VICS
  "Child Abuse Material" and the UK's "SC Category A" are not the same legal
  test — except Non-Pertinent, where your scheme has one (Europol's 3, South
  Korea's 0). A Project VIC export of such a case carries only that, and
  every other grading with no category.
* **The NSRL** grades a file only into a scheme's Non-Pertinent, so in a
  scheme without one an NSRL hit is shown and left for you.
* **The hash store holds one scheme.** A store is a United States store
  unless it is set otherwise while it is empty (`python -m fme.review.hashstore
  --store <db> --region UK`). A case under another scheme is not contributed
  to it and its codes are not applied to that case: the same number means
  different things in the two.

## What each button does

**Every button in the ribbon — all 61 — says what it does when you rest
the pointer on it.** The ones that do real work to a case are set out
here as well.

### Case Data

| | |
| --- | --- |
| **Similar images** | Groups pictures that look alike across the whole case — the same photograph re-saved, resized or lightly edited. It finds copies of a picture, **not other pictures of the same subject**. |
| **Stills to videos** | Matches still pictures against the frames of this case's videos, so a screenshot or a saved frame is tied back to the clip it came from. |
| **Screening** | Measures skin tone and detects faces across the case so what most likely needs looking at sorts to the front. It measures; it never categorizes, and a high score is not a finding. |
| **Content Credentials** | Reads what a file states about itself — what made it, whether it says it was AI-generated — and checks the signature where you have set a trust list. A signature proves the claim is intact, never who made the picture. |

### View

These change what the gallery shows. **None of them changes a file, a
grading or a report.**

| | |
| --- | --- |
| **Stack similar** | One tile per group of near-identical pictures instead of one per file. Grading the stack grades every file in it. |
| **Show containers** | Shows the archives, documents and databases the media came out of. Hidden by default: a zip whose pictures are already listed one by one is a tile showing nothing. |
| **Hide known software** | Hides files an imported set says are known software — NSRL and the like — so the gallery holds what somebody put on the device rather than what Windows did. |
| **Blur** | The **review** blur: blurs the categories you chose, and lifts when you hover a tile, because that is how a blurred page is worked. To hide the screen from the room use **Ctrl-B**, which blurs everything and lifts for nothing. |

### Apps

These act on the **one file you have selected**, except Find faces.

| | |
| --- | --- |
| **Hex view** | The raw bytes of the selected file with its signature, for when what a file claims to be and what it is may differ. |
| **Full size** | Opens the picture full size: scroll to zoom, drag to pan, `+` `-` `0` `1` on the keyboard. |
| **Find similar** | Pictures that look like this one. **The same search as Similar images** on Case Data — that one runs over the whole case, this one starts from the file in front of you. |
| **Find faces** | Finds the faces in the case so one can be searched for. Needs the two face models. A score is never an identification. |
| **Others like this** | Orders the case by **what is in** the picture rather than which file it is — other pictures of the same room or subject. Needs an image model and OpenCV 5. |
| **Find edited copies** | The same picture after somebody cropped, rotated, mirrored, captioned or pasted it into a screenshot. The same **picture**, never the same subject. |

**Three of these answer different questions and are easy to confuse:**

- **Find similar** and **Find edited copies** both look for *this picture
  again*. The first finds it re-saved or resized; the second finds it after
  real editing, and is slower and stricter.
- **Others like this** is the only one that looks for *a different
  picture of the same thing*.

## Deleted files

Three passes reach deleted media, and they reach different things:

- **The file system walk** lists what the volume still claims.
- **The MFT pass** (NTFS) reads deleted records, including files small
  enough to have lived inside the record itself — which carving cannot
  find, because they never occupied a cluster.
- **The FAT deleted-entry pass** reads the directory entries of FAT32 and
  exFAT volumes, where a deleted file keeps its name, first cluster and
  size until the entry is reused.

The FAT pass runs **whether or not pytsk3 is installed**. That is
deliberate: TSK's entry for a deleted FAT file can carry no size at all,
leaving its walk nothing to read — so a machine with TSK installed was
finding fewer deleted files than one without, and nothing said so.

**A deleted FAT name is missing its first character.** FAT overwrites it
when the file is deleted, so it is genuinely gone. It is shown as `?` —
`/?MG_0009.JPG` — and every such file carries a note saying why, because
a name in a report has to be defensible.

## Doing more to a case without carving it again

Changing one option used to mean carving the whole image from the start —
hours, to answer a question about files already recovered. Two things can
now be asked of a finished case:

- **Open up the containers.** The archives, documents and databases the
  case kept are opened and whatever media is inside them is added. Nothing
  already recovered is removed or changed, and running it twice adds
  nothing the second time.
- **Make the gallery tiles again**, if they were interrupted or look wrong.

Both are under **Do more with this case**, beside the C4All export on the
Review screen: **Look inside recovered archives and documents** and
**Rebuild gallery tiles**. (Until v2026.11.311 these existed but no button
reached them.)

Both are written to the case's audit log with why they happened, so a case
can show what was done to it and when.

**Keeping the containers is now on by default.** Without them a finished
case cannot be opened up again: the archives are in the evidence rather
than in the case, and "look inside these" means carving the image a second
time. They cost disk, which is why it used to be off, and the trade is
worth it.

## Why the gallery is quick now

A gallery tile is about 180 pixels across; a phone photograph is 4,000. The
gallery used to load the **original** into every tile and let the browser
scale it, so a page of fifty asked for a few hundred megabytes and threw
almost all of it away — every time the page was drawn.

Tile-sized copies are now made once and kept in the case's own `thumbs`
folder, about 3 KiB each. Measured on phone-sized photographs: **1,394
times less data per page.** They are made in the background, at low
priority and on several workers, when a run finishes and when a case is
opened from the list -- the page after the one you are looking at first, then
the rest of the case in order -- so they are usually ready before you get
there. They are thrown away with the case. On a case of a few hundred
thousand pictures the first pass takes a while; leave the case open and it
gets quicker as you go.

A picture that will not decode falls back to the original rather than
showing a broken tile. A missing thumbnail is a speed problem, not a reason
to hide evidence.

## 2. Loading evidence

**Point this at the image, not at the folder it lives in.** A folder scan
looks for media among the files in the folder, and the segments of a disk
image are not media — so a run over `DC25-321 Image\` finds nothing, while
a run over `DC25-321.E01` reads the file systems inside it, including what
was deleted. A run that finds nothing in a folder holding an acquisition
now says so and names the file.

**Release image** (File tab) hands the drive back without closing the
program. A finished run lets go by itself; this is for the times it did
not, or for letting go without running anything. If the drive still will
not detach afterwards, something else has it open — Windows' own indexer
and antivirus both do this.

### What it reads

| Kind | Notes |
| --- | --- |
| Raw images | `.dd`, `.img`, `.raw`, `.bin`, and numbered split sets |
| EnCase | `.E01`, `.Ex01` and their segments — needs `libewf-python` |
| Virtual disks | VMDK, VHD/VHDX, QCOW2 — need the matching libyal library |
| Phone extractions | `.ufdr`, `.ufd` chunk sets, Android `.ab` backups (unencrypted), GrayKey packages. XRY's own format is not read. |
| Archives | `.zip`, `.tar`, `.tar.gz`, `.tgz`, `.bz2`, `.xz`, `.7z` |
| Folders | a mounted volume, a network share, a logical export |
| Devices | `\\.\PhysicalDrive2`, `/dev/sdb1` — read-only |
| Databases | a SQLite file on its own |

### Load / Inspect first

Inspection says what the container is, which file systems were found, how
large it is, and warns about anything that will limit the run. It costs
seconds and prevents a wasted afternoon — an image whose file systems do not
mount is worth knowing about before a six-hour carve, not after.

### Should you mount the image first?

Generally no. Mounting gives you the operating system's view: **live files
only**. No unallocated space, no slack, no deleted entries — which is most of
what a recovery is for. Measured on a test volume: 8 files from the image,
5 from the same volume mounted.

Mount when the alternative is not reading it at all: encrypted volumes
(BitLocker, FileVault, VeraCrypt), file systems your TSK build cannot parse,
RAID or LVM sets, or Volume Shadow Copies. Then prefer a mounter that
presents a **disk device** rather than a drive letter — Arsenal Image
Mounter, `xmount`, `qemu-nbd` — and point the tool at the device. A drive
letter gives you the files; a device gives you the volume.

### Taking in a C4All index

**Case Data → Import C4All index.** This program could write a C4All index
and not read one, so the exchange was one-way: a set handed over by another
unit, or one you made years ago, could not be brought back in.

Files are matched **by hash**, never by name — two cases full of
`IMG_0001.JPG` would collide immediately.

**Your own gradings win.** Where a file is graded here and graded
differently there, yours stands and the difference is listed. An import
that quietly replaces an examiner's decisions with another unit's is not an
import, it is a loss.

**C4All writes 0 for "nobody categorized this"**, which is read as ungraded
rather than as Non-Pertinent — otherwise an import would mark a whole case
Non-Pertinent on a decision nobody made.

The index is summarized before anything is taken, because the numbers in it
mean whatever the scheme of the unit that wrote it meant, and you should
see whose you are about to accept.

## Deleted files on NTFS

Between listing what a volume still has and carving its free space sits a
third thing: the **master file table**. Deleting a file marks its record
unused rather than erasing it, so until that record is reused the name, the
path, the dates and the size are all still there — and so is the content,
for as long as its clusters stay free.

This runs automatically on any NTFS volume unless **Skip deleted entries**
is ticked. It reaches two things carving cannot:

- **Resident data.** A file small enough to live inside its own record —
  roughly 700 bytes, so a thumbnail, an icon, a short note — never occupied
  a cluster. Free space has never held it, and a carver will never see it.
- **Orphaned records.** A file whose parent folder was itself deleted and
  reused cannot be found by walking directories. Walking the table finds it
  with its name intact.

**Every file recovered this way says how it was recovered**, because that
decides how far the content can be relied on:

| | |
| --- | --- |
| *resident in the record* | exact. Nothing can have overwritten it: it was never on the disk as such |
| *read from clusters the file system had released* | what is there now. Anything may have been written there since; a file that still decodes is probably intact, one that does not proves nothing |

### File systems

The engine is The Sleuth Kit through `pytsk3`:

- **Walked with names, paths and dates:** ext2/3/4, FAT12/16/32, exFAT, NTFS,
  HFS+/HFSX, APFS (TSK 4.7+), UFS/FFS, ISO 9660, YAFFS2.
- **Not supported:** F2FS, QNX, SquashFS, JFFS2, UBI/UBIFS, YAFFS1.

Carving is file system independent, so media on an unsupported volume is
still recovered — with hashes, offsets and EXIF, but without the file name,
path or file system timestamps.

---

## 3. Options

| Option | What it does |
| --- | --- |
| **Engines** | File system, carving, or both. Both is the default |
| **Case number / Examiner / Evidence item** | Recorded in the case and in every export; a case number also writes ProjectVIC and LAVA automatically |
| **Parallel workers** | Carving across cores. Auto leaves one core free |
| **Carve alignment** | **Every byte** (default) or 512-byte sectors. Sector alignment is about three times faster and misses every picture inside a document, every base64 image in a cache, and anything not starting on a boundary |
| **Images only / Videos only** | Narrows what is kept |
| **Extract media from SQLite databases** | Walks b-trees, reassembles overflow chains, recovers from free space |
| **Look inside compound files** | Opens documents, archives, gzip/bzip2/xz streams, PDFs, saved pages, thumbnail caches, EXIF thumbnails, and decodes base64 and hex-encoded images |
| **Folder scan: look inside non-media files** | For folder sources |
| **Skip deleted entries** | Live files only |
| **Include NTFS `$` system files** | Normally skipped |
| **Encrypted volumes** | A password, recovery password or recovery key, and a BitLocker startup key file, to open BitLocker and FileVault volumes. For this run only: see *Encrypted volumes* below |
| **Read a RAID or LVM set with its other members** | On by default in the interface; `--find-members` or `--member IMAGE` on the command line. Finds the other members of an md array or an LVM volume group in the image's folder by what each file records about itself (never its name), assembles the set -- rebuilding a missing RAID4/5/6 member from parity -- and walks its volumes. The report lists every file used |
| **Hash the source** | On by default in the interface; `--hash-source` on the command line. Before the run, hashes the whole source (MD5, SHA-1, SHA-256) and compares it with the hash the acquisition recorded (an E01's MD5 and SHA-1). See *The evidence, by hash* below |

### The evidence, by hash

Every run records the hashes the acquisition stored about itself, read from
its headers at no cost. With **Hash the source** it also reads the source
end to end before the run, hashes it, and says whether it matches:
*verified*, *does not match*, or *nothing to compare with* (a raw image or a
device records no hash of its own; the digests then identify the source as
this run read it). A source that does not match is stated in every report as
a limit, in capitals: nothing in the case should be relied on until that is
explained. The hashes are of the evidence as acquired, before any encrypted
volume is opened.

`manifest.json`, the run's record, gets its SHA-256 written beside it as
`manifest.json.sha256`, in the form `sha256sum -c` checks. A report says
whether the record is unchanged since the run wrote it.

### What a report covers

Every report opens with **Report contents**: how many files it covers,
split into images, videos and other; how many in each category with the
share of the report each is; how many carry each flag; how many matched a
hash set; and how much media the case holds in total, so a reader can see
what share of it the report is.

Beside it is a ring of the categories, in **the same colours the gallery
uses**, so somebody who has seen the screen does not have to learn a
second colour scheme to read the paper.

**The HTML, the Word document and the PDF are counted once.** They are
laid out separately but they read the same numbers, because an HTML saying
ninety-six and a Word document saying ninety-five turns a hearing about
pictures into a hearing about the tool.

### Encrypted volumes

**What opens.** A BitLocker volume, with its password, its 48-digit
recovery password, or its startup key (`.BEK`) file. An APFS volume
FileVault encrypted, with the user's password or the personal recovery
key. A LUKS1 or LUKS2 container, with its passphrase (since v2026.11.318;
a LUKS2 key slot made with Argon2 also needs the `argon2-cffi` package, and
says so when it is missing). Several can be given at once; each locked
volume is tried with every one. Not opened: a BitLocker volume protected
only by the computer's TPM (the key never leaves the chip), and anything
else. A volume no key opened is named as locked.

**Giving the key.** In the window, the *Encrypted volumes* fields under
the case details: one password or recovery key, and the full path of a
`.BEK` file. On the command line:

    python -m fme extract -i laptop.E01 -o out --ask-password
    python -m fme extract -i laptop.E01 -o out --password-file keys.txt --key-file F.BEK

`--ask-password` takes what you type without showing it, a blank line to
finish; `--password-file` reads one per line. **There is no `--password
VALUE`**, and typing one is refused: a password on the command line stays
in the shell's history and shows in the process list to anyone on the
machine.

**What is kept, and what is not.** The password is used for the run and
held nowhere else: not in the case folder, its manifest or progress note,
the log, `case.db`, a report, or anything the window shows back. A key
file is recorded by its name and SHA-256, which identify it without being
it. What *is* recorded is which volume was opened and with what kind of
key; every report says so under **Encrypted volumes opened**, because the
files from that volume were read through a decryption with a key you
supplied.

**What a decrypted volume gives.** A BitLocker volume is decrypted
underneath everything, so the file-system walk, deleted-entry recovery and
the carve all read it as if it had never been encrypted. A FileVault volume
is different: each APFS volume has its own key inside a container it
shares with others, so only the APFS reader can decrypt it. Its files come
back with their names, folders and dates; its **free space is not carved
decrypted**, so nothing deleted from it is recovered by carving, and the
report says that too.

**A key that opens nothing.** The volume is reported locked, as it would
be with no key, and the report adds that the keys given did not open it.
A FileVault volume's passphrase hint, which the volume stores, is shown as
stored.

**Resuming.** A run that read decrypted volumes asks for the key again on
resume -- it was never stored -- and refuses to continue without it rather
than carve the rest of the volume as ciphertext.

**This needs pycryptodome** (in `requirements.txt`). `python -m fme doctor`
says whether it is present. Without it, a run given a key stops before
reading anything and says why.

Tested against a BitLocker volume and a FileVault volume made by Windows
and macOS (`tests/fixtures/`). Not tested: a whole Mac disk, a BitLocker
volume inside an E01 of a real laptop, or a recovery key (rather than a
password) on FileVault. An image of a T2 or Apple-silicon Mac's internal
storage made below the Mac's own hardware encryption is not something a
password opens here.

## Watching a run: how long, and cancelling

Files appear in the gallery **as they are recovered**, not at the end. The
progress bar shows bytes examined, the phase shows which engine is working,
and the count rises as files are written.

A run on a large exhibit takes hours: carving reads every byte, and the file
system pass opens every file when compound scanning is on. Roughly, carving
manages a couple of gigabytes a minute per machine, so a 128 GB image is
around an hour of carving plus whatever the file system pass costs.

If the count is not moving but the byte counter is, the engine is reading
through a region with nothing in it — which is normal, and common in the
middle of a large disk.

### Cancelling

**Cancel** stops at the next chunk boundary. Everything recovered so far is
kept, the manifest is written, and the run is recorded as canceled.

---

## 4. The results screen

While a run is set up and running there are three columns, each with its own
scrollbar: settings on the left, results in the middle, a pane on the right.
In the results column the toolbar, the category bar and the filter bar stay
put while the tiles scroll.

**Reviewing a case.** Once a run finishes with files, or a case is opened
from the list, the gallery takes the screen: the settings column and the
Project VIC and C4All panels step aside behind one line — **Show extraction
settings**, **Project VIC & C4All panels**, **New extraction** — and the run
summary shrinks to its first line, with a **Run summary** button for the rest. The pane on the right
stays where it is from the start, at 1280 pixels wide too, so picking the
first file does not move every tile.

The first time a case is reviewed in a browser, a card above the gallery
asks how pictures should be shown — blur what is not yet graded (the
default), the most serious categories, every picture, or nothing, and
whether in grey. It stops nothing; grading works while it is there.
**Display → Blurred categories** chooses which categories "the categories
ticked" covers, separately for each regional scheme.

**Messages** appear bottom right, clear of the ribbon. ✕ closes one;
**Earlier** shows the last ten. An error stays until it is closed.

**Save…** on a tile asks before putting a copy of the file in the browser's
downloads folder; that copy is not logged or hash-checked — **Export files**
makes one that is. **Show in folder** opens the folder holding the file.
**✕** beside a case in the Cases list takes it off the list (its folder is
untouched), and **Undo** in the message that follows puts it back. The badge
at the top right names the open case by its case number, or its folder.

**From the keyboard**: Tab reaches every ribbon item, ribbon tab and filter
chip, and Enter or Space presses it; "Skip to the gallery" is the first
thing Tab reaches on the page.

### Views and controls

- **Gallery** / **Table** — thumbnails, or a row per file.
- **Tile size** — a slider; the grid reflows without re-fetching anything.
- **Per page** — 200 to 5000, or all, with first/previous/next/last.
  Building fifty thousand tiles at once is what makes a gallery unusable.
- **Time zone** — converts every timestamp that carries one. Daylight saving
  is handled per date, so one setting gives EST in January and EDT in July.
- **Refresh** — re-reads the run without restarting anything.
- **Details / Classification / Filters** — which pane the right column shows.

### Timestamps that cannot be converted

| | |
| --- | --- |
| File system times | recorded in UTC, converted exactly |
| EXIF capture time | wall-clock as the camera recorded it, no zone |
| FAT times | local to whatever machine wrote them, no zone |

The last two are shown **(as recorded)** rather than shifted. Converting them
would mean inventing the zone they were written in. A camera that wrote
EXIF's `OffsetTimeOriginal` — most phones do — did record the zone, and that
capture time carries it and converts like any other.

The same rule holds everywhere a time leaves the program (since
v2026.11.281):

| | a time with a zone | a time with none |
| --- | --- | --- |
| report (HTML, Word, PDF) | converted to the case's zone, offset shown | as recorded, marked *device clock; zone not recorded* |
| report (CSV) | ISO 8601 with its offset | ISO 8601 with none |
| Project VIC | written with its offset | written as VICS's unknown (`0001-01-01T00:00:00+00:00`); the export's summary counts them |
| C4All, LAVA | epoch seconds | no epoch (an empty value) |
| KML | on the time slider | in the description only |

Before v2026.11.281 the report read these as UTC and converted them, the
Project VIC export wrote them as UTC, and C4All and LAVA read them in the
examining computer's own zone. NTFS and MP4 times, which are UTC, were
stored without saying so; in a case made by an earlier version they are
shown as *zone not recorded* until the image is run again.

Until v2026.11.298 the Word and PDF reports printed each time exactly as it
was stored: an NTFS time in UTC rather than the case's zone, and a FAT time
with no mark, beneath a header naming the case's zone. The HTML report beside
them was right.

**How the case folder keeps them** (since v2026.11.298). In `manifest.csv`
and `manifest.json`, the `captured`, `crtime`, `mtime`, `atime` and `ctime`
columns hold only times that say their zone. A time the evidence recorded
with none is in the `recorded_times` column instead, as JSON naming the
field: `{"mtime": "2024-07-04T18:00:00"}`. Kept apart, the two cannot be
mistaken for each other by a script or a spreadsheet that reads the columns.
Anything you have built on `manifest.csv` that reads FAT or camera-clock
times from the `mtime` or `captured` columns must read `recorded_times` for
them. Cases made by earlier versions keep both kinds in the time columns,
and this program reads them either way.

### Video decoding, and why it happens elsewhere

The video decoder is FFmpeg underneath OpenCV, written in C. A malformed
stream can crash it outright — not raise an error, crash — and carved video
is exactly the malformed input that provokes that. Decoding inside this
program would mean one bad clip taking down the server, the run in progress
and whatever review was open.

So each video is decoded in a process of its own. If it dies, this program
sees that it died, records that the file could not be read, and carries on.
A decoder stuck in a loop is bounded by a two-minute limit, because a hang
is as bad as a crash and harder to notice.

The cost is a fraction of a second per video, and frames already extracted
are reused rather than decoded again. In a frozen build there is no
interpreter to call, so the decode happens in-process: the isolation is lost
there, not the frames.

### Video

Hovering a video tile seeks it — move across the tile to move through the
clip. Only the header is fetched up front.

Formats the browser cannot play (most AVI, some MOV, MPEG-2, much of what a
DVR writes) are decoded by OpenCV if `opencv-python-headless` is installed:
the details pane shows a filmstrip. Frames are **generated images, not
evidence** — their hashes are this tool's, not the file's — so they are
written to `_frames/`, outside the recovered media.

---

## 5. Searching and filtering

### Search

Matches the file name, source path, all three hashes, the camera and your
note, narrowing as you type. Hashes match on a prefix, so eight characters
from a report find the file.

```
img_0042               anywhere
91a3b5                 a hash prefix
md5:91a3b5             that hash only
path:100apple          the source path only
cam:iphone note:dc24   both, each in its own field
```

Fields: `name:`, `path:`, `md5:`, `sha1:`, `sha256:`, `cam:`, `note:`.
Escape clears the box.

### Exporting the files themselves

**Menu → Export the files themselves…** copies the recovered files into a
folder per category, with **images and videos apart** —
`Child Abuse Material (CAM) Images` beside
`Child Abuse Material (CAM) Videos`. That is how a submission to NCMEC is
asked for, and it makes the counts checkable besides: a folder of 703 files
should be the 703 the report says. Untick **Images and videos apart** to put
a category back in one folder. Separate from the report on purpose: handing
somebody the material is a different act from describing it, and should not
require writing a report.

Every copy is **hashed as it is written** and checked against the hash
recorded when the file was carved; anything that does not match is reported
rather than quietly handed over. A manifest, `exported_files.csv`, goes in
beside them with each file's name, category, flags, size, MD5, SHA-1,
SHA-256 and where it came from — a folder of pictures with no record of what
they are is not evidence anybody can work with.

A file with no hash recorded when it was carved cannot be checked: its
`hash_verified` is left blank rather than written as True, and the page says
how many there were.

It will not write into a folder that already holds files: two exports mixed
together cannot be told apart afterwards. Within an export nothing is
overwritten either: two files of one name both go in, the second as
`<name>_<first 8 of its SHA-256>` (then `_2`, `_3` ...), and each row of the
manifest names its own file. With thumbnails, the manifest's `thumbnail`
column says which picture in `_thumbnails` belongs to which file.

### Looking closely, and hiding what is on screen

**Full size is a review mode.** Double-click a tile, or press Enter with a
file selected, to open it. ← and → (or ‹ and › beside its name) move through
the gallery in the order shown; the header says what the file is graded as
and which flags it carries, and confirms each grading for a moment before
the viewer moves on. M switches the mask tool on and off, unless M grades
something in this case. Escape closes it.

**Full size zooms.** Scroll to zoom where the pointer is, drag to move
around, `+` and `−` to step, `0` to fit the window, `1` for actual size, or
double-click to toggle between fit and actual. Deciding what a picture is
often turns on a detail a few pixels wide on a tile.

**Ctrl-B hides the screen.** Every picture and video on the page is
blurred, whatever its category and whatever you are looking at — the
gallery, the details pane, the picture open full size. A banner says so.
Press it again to show.

**Nothing lifts it.** Not hovering a tile, not the B key, not selecting a
file. That is the difference between this and the blur below, which is per
category and lifts on hover because that is how a blurred page is worked.
Neither of those is any use when somebody walks up to the desk: an examiner
sitting in Uncategorized has, by definition, files nobody has looked at
yet.

Any video playing is paused as well — a blurred clip still has sound, and
sound carries further than a screen does.

It hides the pictures **from the room**. It changes no file, nothing in a
report, and no decision already recorded.

### Finding a picture after it was edited

**Apps → Find edited copies**, with a picture selected. Visual similarity
finds a re-saved or resized copy; this finds one that was **cropped,
rotated, mirrored, captioned, bordered or pasted into a screenshot**, which
is what happens to a picture as it is traded.

It works by matching distinctive points inside the two pictures and
accepting the match only when those points agree on one transform — the
same move, scale and rotation for all of them. Unrelated pictures produce
matches that scatter, and scattered points fit no transform, which is what
makes it refuse rather than guess.

Measured on eleven edits of one picture and eight unrelated pictures:
**eleven of eleven found, none of the eight wrongly matched.** The
threshold sits in the measured gap — every true match kept at least 58
points, the worst coincidence kept 17 — and errs high on purpose. A missed
copy is a file you still see and still grade; a false match is a file you
are told is the same picture.

**Same picture, not same subject.** Two different photographs of one person
are not a match here, and should not be: that is a judgment about people,
not about pixels.

The first search fingerprints the case, which takes a little time; later
searches cost only the comparisons.

### Phones: one photograph is not three photographs

Android exposes the same storage through several paths, and a full
extraction records all of them:

```
/storage/emulated/0/DCIM/Camera/IMG_0001.jpg
/data/media/0/DCIM/Camera/IMG_0001.jpg
/mnt/user/0/emulated/0/DCIM/Camera/IMG_0001.jpg
/sdcard/DCIM/Camera/IMG_0001.jpg
```

Those are one file. Registering each spelling multiplies a device's media
by three or four — in the counts, in the duplicate groups, and in a report
somebody has to explain in evidence. They are reported once, with the other
paths recorded against the file.

**Three things are never folded together**: credential-encrypted and
device-encrypted storage, because they hold different files; different
Android users, because a work profile's photographs are not the owner's;
and views that do not hold the same bytes, which happens when a file was
being written while the extraction ran.

### A video's location

A photograph carries its location in EXIF; a video keeps it in the
QuickTime structure instead — `moov/udta/©xyz` on Android and most cameras,
an Apple key table on iPhones. Both are read now, so videos appear in the
KML export and on the map beside the pictures. Until this, a case whose
pictures were placed and whose videos were not looked like a case whose
videos were taken somewhere unrecorded.

### The case map

**View → Case map** puts every file the gallery's filter shows that carries
a location on the offline basemap imported under Settings — nothing is
fetched from the internet. Points close together are drawn as one circle
with a count; click it to zoom in. A red point is a file not yet graded, an
amber one a graded file. Click a point, then **Go to this file**, and the
map closes on that file in the gallery.

The map follows the filter: narrow the gallery to *Has GPS* and *Deleted*,
and the map shows those. No picture is drawn on the map — a name and a
position only — so panning a map of a case is not a way round the blur. A
position outside the range of latitude and longitude (a corrupt GPS tag) is
counted in the header and not drawn.

A point is where a file's own metadata says it was taken. It is not where
the device was, and it is not a verified location.

### Phones: Cellebrite, GrayKey, Android backups

A mobile extraction is read **as the device**, not as an archive. The path
in the archive is the vendor's packaging; the path on the phone is what an
examiner cites. Both are kept, and the phone's is the one shown:

```
in the archive  files/Fs/1/private/var/mobile/Media/DCIM/100APPLE/IMG_0423.JPG
on the phone    /private/var/mobile/Media/DCIM/100APPLE/IMG_0423.JPG
```

Cellebrite (`.ufdr` and its zip), GrayKey, Magnet exports, Android `adb`
backups and plain tar are recognized. A member **with no extension** — most
of an app's cache on both platforms — is classified by its first bytes, not
its name. The vendor's own report folder is left out: that is their
analysis, not the device.

Members are read as a stream, so a 60 GB extraction does not need 60 GB of
scratch space before anything can be looked at.

### Pictures inside documents

A picture inside a Word file, a PowerPoint or a PDF is invisible to a hash
sweep: the document is one file with one hash, and no set holds hashes of
documents somebody assembled. Dropping twenty pictures into a `.docx` takes
a minute and defeats a hash-set sweep completely.

Office and OpenDocument files are opened and their media recovered. PDFs go
through the reader this program already had, which takes a JPEG or JPEG
2000 out **verbatim** — the file as its producer wrote it, whose hashes can
match a set — and refuses to rebuild images stored as raw pixels, because a
rebuilt file's hashes belong to this tool rather than to any evidence.
Those are counted and reported as present.

### Video an app streamed

Streamed video does not arrive as a file. An Android app caches it as
numbered pieces, and a modern stream arrives as **separate video and audio
streams of many short segments** with the order held in a cached manifest.
A carve over that folder recovers fragments that will not open.

These are joined during an ordinary scan — no separate step. The vendored
reader is used, which resolves an item's name from the cache index and
joins a DASH or HLS stream's segments in the order its manifest lists them;
the older built-in rejoiner stays as a fallback, because a cache the
vendored reader cannot read is no reason to recover nothing from it.

Where the segments have a hole, **the join stops there and the file is
reported short**. A cache holds what was watched, and presenting part of a
video as the whole of it is a claim about what somebody saw.

Separate video and audio streams are recovered as separate files. Putting
them into one playable file is something the library can do and this does
not yet use.



An Android app that plays video over the network keeps it as numbered
fragments with the name in a separate index — unplayable and nameless to a
carve. These are joined back into the video that was watched, with the
address it came from, and DASH or HLS segments are joined in the order the
cached manifest lists them. Nothing is re-encoded.

**A cache holds what was watched, which is often not the whole file.** An
item is called short only where the fragments have a gap or fall below a
length the cache recorded; where no length was recorded, whether it is
whole is not known either way, and it says so.

### Your own stash

The shared store holds what the unit knows: Project VIC, NSRL, and every
case anyone has contributed. **Your stash holds what you know**, on this
machine, and the two are not the same thing.

Every file you put in a category goes in as you work. Contributing to the
shared store is a deliberate act made later, usually when a case is
finished — until then your gradings help nobody, including you: grade a
file in one case on Monday, meet it again on Thursday, grade it twice.

It also works when the server does not, which is the other reason to have
it.

**Where the two disagree, you are told.** A file you graded one way and the
shared store another is reported as a disagreement and **neither category
is applied** — one of two answers handed over quietly is how an examiner
comes to rely on a category nobody chose. Every match says which source it
came from.

Checking a case against the store checks your stash in the same pass, and
**Check against my own stash** on the extraction panel turns that off for
a run. A stash match is labelled as yours, never as the unit's.

**File → Set up once → My stash** shows what it holds and where it is. It
holds hashes and categories: never a picture, never a path, never anything
about a case.

### Finding the same face

**Case Data → Find faces**, then open a picture and search from one of the
faces in it. The unit is a face, not a file, because a photograph can hold
several people and the question is usually about one of them.

**The two models ship with the program** (since v2026.11.317, in
`fme/models/` with their licences): a YuNet detector (about 230 KB, MIT) and
an SFace recognizer (about 38 MB, Apache-2.0). Each is checked against its
published SHA-256 before it is used. A path in Settings is used instead.
Unlike the content model, these load on OpenCV 4. Every result is labelled
**Investigative lead only**.

**A score is not an identification, and must never be written down as one.**
Siblings score highly. So do strangers who happen to resemble each other.
Two photographs of one person ten years apart can score below the
threshold, so nothing coming back is not evidence that there is nothing.
The model was trained on adults, which is worth knowing given what it will
mostly be pointed at here.

The threshold is **0.363**, which is OpenCV's own published figure for
SFace — quoted, not invented here — and every result shows its score so you
can see how close the match actually was rather than only that it came
back. Nothing is categorized from it. It orders what to look at next.

### Searching by what is in a picture

Ranks the case by how alike a model finds each picture and the one you
searched — other pictures of the same room or the same subject, not copies
of the same file.

**The model ships with the program** (since v2026.11.317): DINOv2-small
exported to ONNX, 88,411,480 bytes, Apache-2.0, in `fme/models/` with its
licence, and checked against its published SHA-256 before it is used.
Leave the Settings path blank to use it, or give the path of another.
Every result is labelled **Investigative lead only**.

**It needs OpenCV 5.0 or newer.** OpenCV 4 stops on a node this model uses
and raises an error about node handling, which says nothing useful.
Settings checks the model when you set it and tells you plainly:

```
DINOv2-small (Meta AI) needs OpenCV 5.0 or newer to load;
this machine has 4.13.0.
pip install --upgrade "opencv-python-headless>=5.0"
```

The model is recognized by its SHA-256, so a wrong or truncated file is
caught before a search returns nonsense rather than after. Without a model
the engine refuses rather than returning nothing.

**A score is not an identification.** The model finding two pictures alike
is not a statement about a person, a place or an object: two children who
resemble each other score highly, and so do two rooms with the same
furniture. It puts a hundred thousand files in an order worth looking at.
The looking is still yours, and no category is ever applied from it.

### Content Credentials

**Case Data → Content Credentials** reads what each file says about itself.
A growing number of cameras, phones and editors write a signed record into
the file saying what made it; **every major image generator writes one too**,
and says so in a field of its own — a generated picture carries
`trainedAlgorithmicMedia` where a photograph carries `digitalCapture`.

It is reported per file in the details pane, and as a count: how many files
carry credentials, and how many say they were made by a trained model.

**The signature can now be checked.** Settings takes the path to a trust
list (a PEM bundle; C2PA publishes one). There are three different answers
and they matter:

| | |
| --- | --- |
| signature invalid | the file has been altered since it was signed, or the manifest was copied in from another file |
| signature valid, signer not trusted | the claim is intact and was signed by the key in the file — but who holds that key is unknown |
| signature valid, signer trusted | the claim is intact and the certificate chains to an issuer in your trust list |

**Only the third makes a signer's name worth anything**, and even then it
means "this certificate chains to something you chose to trust", not "what
it says is true". Anybody can make a certificate saying Adobe and sign a
manifest with it, and that signature really does hold — which is exactly
why the middle row exists and why it is reported separately.

No trust list ships with this program: one that arrives with a forensic
tool and is never updated is worse than none.

**Three things it is not.** The signature is **not checked** — that needs a
certificate chain and a trust list this program does not carry, so
everything read is what the file *states*, the signer included: a name in
the file is not a proven identity. A manifest **can be copied** from one
file into another by anyone who can write bytes. And a file with **no**
manifest usually means nothing — most files have none — though the reader
does distinguish "walked the whole file and found none" from "could not
read it to the end", and says which.

So this is evidence about a judgment, not the judgment. It is worth having
because an examiner deciding whether something is AI-generated is otherwise
doing it entirely by eye.

### Stacks

A tile can stand for more than one file, and says so in the corner:

| | |
| --- | --- |
| **⧉ 4** | four identical copies of this file were found, in different places |
| **≈ 6** | six files look like this one |

They are different claims and are counted apart. Identical means the same
bytes and the same hash; alike means a perceptual hash put them together,
which is a judgment — see **Visually similar images**.

**Stack similar** in the toolbar shows **one tile per group** instead of one
per file, and the tile is drawn with another behind it. **Grading a stacked
tile grades every file in the group**, because leaving the rest ungraded
behind a tile that looks dealt with is the worst of both. Turn it off and
every file has its own tile again.

Exact copies are always one tile: the run records where each copy was found
and shows them in the details pane, rather than carving the same picture
twenty times.

### Two things the gallery leaves out

**Containers.** A zip, database or cache that other media came out of is not
a picture; its contents are already listed one by one. It stays in the
manifest and the reports with its hashes — it is evidence — but it gets no
tile. **Show containers** in the toolbar brings them in.

**Known software.** **Hide known software** leaves out files whose hash is
in a known-software list such as the NSRL. A hit means the file is
*recognized*, not that it is irrelevant: it is a lead about what to look at
first, and nothing more.

### The category bar

A chip per category with its key and count. Click to see only that category;
click again for everything. Empty categories are dimmed rather than hidden,
so a category stays where you learned to find it.

### The Filters pane

Facets built from each file's own metadata and from the file system. Values
within a facet are alternatives; facets are cumulative, so "this camera body,
in this city" is a question you can ask. Every count is computed against the
other active filters, so it describes the result of clicking it.

File type · File extension · File size · Deleted · Metadata (has metadata,
has GPS, none) · Date taken · File created / written / accessed · Camera
author · Camera make · Camera model · Camera serial number · City · State ·
Country.

Files carrying nothing for a facet group as **(not recorded)**.

### The Classification pane

Every category with its count, every tag with its count. Tag counts are
computed **within** the selected category, so with category 1 showing, the
AI CSAM row tells you how many of those are also AI before you click.

### The filter bar

At the foot of the results: everything currently narrowing the view, an ✕ on
each, a count of shown against total, and **Clear all filters**.

---

## 6. Reviewing

### Categories

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

Press the key to grade the selected file; the cursor advances **at once**.
The decision is shown immediately and written behind you, so grading keeps
up with how fast you can press keys. If a write ever fails, the file is put
back as it was and the reason is shown — you are never left believing
something was graded when it was not. Crossing into
the next page shows it from the top.

**Uncategorized is 8, not 0.** 0 means Non-Pertinent, which is a decision
somebody made; a file nobody has looked at must not land there.

The scheme is locked — these codes mean the same thing in every case. Your
own categories are added from 10 up, and can be renamed or deleted; deleting
one returns its files to uncategorized.

### Your own categories and flags, in every case

**Menu → Save my categories and flags** stores the categories and flags you
added to this case, and **every new case starts with them**. Applying is
additive and idempotent: nothing is renamed, nothing is removed, and applying
twice changes nothing — a category holding decisions must never vanish
because a saved set does not mention it.

The locked categories are deliberately not carried. Category 1 means Child
Abuse Material here and in the next case, which is what makes grading
comparable across cases at all.

```bash
python -m fme scheme --save <case folder>
python -m fme scheme --show
```

### Flags

Independent of category, any number per file. Every case starts with
**S&M Violence, Infant/Toddler, Bestiality, AI CSAM (`-`), AI Age Difficult
(`.`), AI Adult (`*`)**, and you add or delete whatever a case needs. They
are seeded once, so a flag you delete stays deleted.

A flag key toggles and leaves the cursor where it is; a category key grades
and moves on — a file has one grading and any number of flags.

Whether something was AI-generated is a flag rather than a category: a
picture is CAM or it is not, and separately it was generated or it was not.

### Notes

Per file, saved about a second after you stop typing.

### Blurring

**Blur** in the toolbar, with a scope beside it: the categories you choose,
every image, or anything not yet graded. Hovering a tile shows it clearly,
and holding **B** shows everything on the page for as long as it is held —
which is how a blurred page is actually worked: glance, decide, move on.

It is for reviewing beside other people, or at a screen that faces a door.
It is **not a safeguard**: the picture is in the file and one hover away.
The report has its own blur option, with the same caveat stated in its
header.

### Working a page

**Hold the page** (on by default, in the toolbar) keeps a page of results as
it was built while you grade it. Without it, filtering to Uncategorized and
grading a file removes it from the list, every tile after it shifts up, and
the next click lands on something else — which turns a page of five hundred
into five hundred small corrections.

So a page can be worked straight down, giving each file whatever category it
deserves, mixing Non-Pertinent and Adult Pornography and anything else,
without the page changing underneath. The tile's color strip updates as you
go, so what has been graded is still visible.

**Grading a whole page at once moves on.** Holding a page is for working
down it one file at a time; when a selection grades everything on it, the
work on those files is finished and the view moves to the files that follow.
If a selection covered only part of the page, the page is held — the rest
still needs grading.

**Next** then lands on the files that follow — not on the next page number.
Once a page's files have been graded out of the filter, the list beneath has
shrunk, and the next batch has moved up to take its place. Nothing is
skipped.

Turning the setting off restores the older behavior: files leave the page as
soon as they no longer match the filter.

### Selecting many

| | |
| --- | --- |
| click | select one and show it |
| **ctrl**-click | add or remove one |
| **shift**-click | a range |
| **ctrl**-**A** or **Select all matching** | everything the filters show |
| **Select this page** | only what is shown |
| **Esc** or **Clear** | clear it |

**Save case**, in the menu, writes a snapshot of the case as it stands and
tells you where it is. Nothing is waiting to be saved when you press it —
every decision is written the moment you make it — but finishing for the day
by pressing something and being told where the case is, is a reasonable
thing to want, and the snapshot it leaves is a real point to go back to.

**Undo** takes back the last grading — the button in the toolbar, or
**ctrl**-**Z**. An action covering more files than are on the page asks
first. Both are in **Undoing a mistake** below, along with what to do about
a grading made before undo existed.

With several selected, a category key or a flag applies to all of them, and
the cursor moves to the first file after the selection — the same as grading
one file, so a page can be worked in runs as easily as one at a time. No
confirmation: a review is thousands of these, nothing is destroyed, the
previous category is in the audit log, and regrading is one keystroke.

Every copy of a file is covered — decisions are keyed by hash, so grading one
grades its duplicates elsewhere on the exhibit.

### Right-click

Find similar images · Show details · View full size · Hex view · every
category and flag with its key and a tick where it applies · Clear category ·
Export MD5 · Open original file · Show in folder.

Right-clicking inside a selection acts on **all of it** and says so.

### What is saved, and when

| | |
| --- | --- |
| Category, flags | the moment you press the key or click |
| Notes | about a second after you stop typing |
| Audit log | with every one of those |
| Similarity and hash-set results | when the pass finishes |
| Snapshot | on demand, and automatically once half an hour of grading has passed |

Everything commits immediately, so a crash loses at most the last action.
`case.db` **is** the case — back up the whole output folder, and take the
`-wal` file too if you copy while the program is running.

---

## 7. Undoing a mistake

Grading is thousands of small decisions made quickly, so the wrong one gets
made sometimes — usually a key pressed with more files selected than
intended. Nothing here is irreversible.

### Undo, while you are working

**Undo** in the toolbar, or **ctrl**-**Z**, takes back the last change —
a grading, a flag set or cleared, or a note — newest first, and says which
file went back to what. (Flags and notes are taken back from what the page
remembers, so after the page is reloaded Undo reaches gradings only.)
For a grading:
Every file in it goes back to what it was, including back to **having no
decision at all** — which is not the same as being set to Uncategorized, and
is restored as the first rather than the second. The last fifty gradings can
be taken back, one step at a time.

### The guards that come before it

- **ctrl**-**A** selects **this page**, not the whole case. Selecting
  everything the filter shows is **Select all matching**, which says how
  many that is.
- An action covering **more files than are on the page** asks first, naming
  the count and the category. Anything within the page does not ask: that
  is the normal work, and a dialog on every keystroke is its own kind of
  harm.
- A snapshot of the case is written automatically as it is worked, into
  `backups/` beside it.

### A grading made before undo existed

Undo only knows about gradings it recorded, which is no help if the mistake
was made in an earlier version. The **audit log has recorded every grading
since the beginning** — which file, which category, by whom, when — so the
state before any event can be rebuilt by replaying what came before it.
Nothing is guessed: a file whose only entry is the mistaken one had no
decision before it, and gets none back.

Start by listing what the program has run:

```bash
python -m fme recover --list-cases
```

Then look at one case's history. This changes nothing:

```bash
python -m fme recover --case DC26-253
python -m fme recover --case "D:\cases\DC26-253"     # or by folder
```

Each act of grading is **one event**, however many files it touched, so a
bulk mistake is a single line. The listing names the largest event and
prints the command to take it back:

```bash
python -m fme recover --case DC26-253 --undo 3
```

Every file returns to what it was, a snapshot is written afterwards, and the
recovery is itself recorded in the audit log. A case of 160,000 files took
six seconds.

### Which folder to give it

**Two files have names a keystroke apart:**

| | |
| --- | --- |
| `cases.db` | the **list** of cases, beside the program |
| `case.db` | **one case's** review, in that run's output folder |

Recovery wants a case. Give it the case number, or the folder holding
`case.db`, `manifest.json` and the recovered media. Pointed at the wrong
one, it says which file is which, lists the cases it can see, and prints
what to run next.

A case is offered on the strength of its `case.db` alone: if the media was
moved or cleaned up, the decisions are still there, and those are what is
being put back.

### If none of that reaches it

`backups/` beside the case holds snapshots of `case.db` taken as the case
was worked. Copying one back over `case.db` returns the case to that moment
— losing anything graded since, so copy the current `case.db` aside first.

## 8. Hash sets

**Hash Sets** tab. Two kinds: the store that outlives cases, and sets loaded
for one session.

### The store

Import a set once and every case is checked against it. Project VIC JSON,
NSRL (`NSRLFile.txt` or an RDS SQLite database) and your own lists all
import, identified by looking inside the file. Sets are streamed, so size is
a question of time and disk rather than whether it fits. Put the store on a
share to use one across an office.

**An NSRL RDS database** is read from whichever table or view actually holds
the hashes. The modern release ships the raw rows in `FILE` and a joined view
in `METADATA`; the view is preferred because it carries the product name as
well. A hash column that exists but is empty throughout — which some releases
ship — is skipped rather than chosen, since choosing it would import a set of
nothing. The import says which table and columns it read, so it can be
checked rather than trusted:

```
read from "METADATA" using md5, sha1, sha256; also kept file_name,
file_size, product_name
```

A match then reads *"splash.jpg, 14396 bytes, Acme Installer"*, and the file
is categorized **Non-Pertinent by the set, not by you** — striped on the
tile, filterable under **Not yet reviewed**.

Project VIC files come in more than one shape and all of them are read: the
national download, whose records sit directly in `value`; a case export,
whose records sit under `Media` inside a case; and a bare array of records.
They are streamed, so a multi-gigabyte set on one line imports in constant
memory — about 280,000 records a second at under 50 MB.

A **Project VIC import keeps the whole record** — MediaID, Category,
MediaSize, DateUpdated, Tags, Series, IsPrecategorized,
PrecategorizationSource, VictimIdentified, OffenderIdentified, IsDistributed
— and shows it against any file that matches. Hashes are read from
`AlternativeHashes` as well, so any of them finds the record.

A file matching one source by several algorithms is one match, reported with
which algorithms agreed. A digest in two sources is two matches, both
reported.

### What a match does to a file

A hash set can categorize a file **nobody has looked at yet**, on four rules:

- **Only an uncategorized file.** A set never overrides an examiner.
- **A set that asserts a category wins** over one that does not, and where
  several assert, the **most severe** is taken.
- **A known-good hit (the NSRL) moves a file to Non-Pertinent only when
  nothing notable flags it too.** Software is in the NSRL; so is a picture
  somebody shipped inside an installer, and if a Project VIC set names it,
  that is the finding.
- **It is recorded as the set's decision, not yours.** The details pane says
  which set, the tile carries a striped marker, **Set by a hash set** filters
  to them, and the Project VIC export writes that set as
  `PrecategorizationSource` rather than claiming an examiner.

**Not yet reviewed** is the filter that matters for working a backlog: it
shows everything a person has not looked at, *including* what a set
categorized. A hash-set category is a lead, not a review.

### One store, shared across a unit

**For setting one up, read `SERVER_SETUP.md`** — **Menu → Setting up a
shared hash store**. It is a step-by-step guide written for someone who has
not done this before: choosing the machine, installing Python, creating the
store, making the certificate, issuing tokens, opening the firewall port,
connecting each examiner, making it start by itself, and what to do when
something goes wrong. The summary below is for someone who already knows the
shape of it.

A store on a file share does not work: SQLite takes file locks that network
file systems implement badly, and two examiners contributing at once across
SMB is how a database gets corrupted. One machine owns the file and the rest
talk to it.

On that machine:

```bash
python -m fme hashserver --store hashes.db --add-user jsmith --role read
python -m fme hashserver --store hashes.db --host 0.0.0.0 --port 8787 \
    --cert server.crt --key server.key
```

On any address but this machine's own, the server **will not start
without `--cert` and `--key`** (or `--insecure`, if a reverse proxy in front
of it handles TLS): every token and every hash looked up would otherwise
cross the network in the clear. A lookup answers with the grading fields of
a matched Project VIC record — MediaID, Category, Series, Tags and the
victim, offender and distribution flags — and never with the record's other
hashes, its PhotoDNA or its free text. An unrecognized token is written to
the access log (never the token itself), and after ten from one address in
five minutes the next are refused without filling the log; a known token is
always answered.

In the program, open **Hash Sets → 1 Connect to the hash store** and fill
in the address (`https://hashes.example:8787`), your token, and the path to
the lab's `server.crt`. All three are needed for a server with its own
certificate; a store that is a file on this machine needs only the first. Everything
else works as before — scanning a run, contributing a case back, listing
sources.

**Roles.** `read` may look up and list; `contribute` may also send a case's
gradings back; `admin` may import sets, remove sources, manage people and
read the access log. An examiner who should check hashes does not thereby
get to add to the store.

**Everyone is named.** There is no shared password: each person has their
own token, and every lookup and contribution is recorded against them with
the time and address. A store that cannot say who queried it is not one this
data should sit in. A token is stored hashed and shown once; issue a new one
if it goes astray, or disable the person.

**What crosses the network is small.** A lookup sends digests and gets back
only the matches, so the store's contents never travel — the server answers
about the digests it was sent and says nothing about the ones it holds.
Importing a national set is a server-side operation on a path the *server*
can see; gigabytes do not go over this connection.

**The admin pages.** Open the server's address in a browser and sign in with
an admin token: **Overview** (how many hashes, how many sets, who has used
it — lookups, contributions and the number of distinct cases each person has
contributed under), **People** (add someone and see their token once,
disable anyone), **Hash sets** (import the NSRL or Project VIC from a path on
the server, each in its own section, with the files and hashes imported
counted by category; remove a source), **Activity** (the full record of who asked for what, and from
where). The pages enforce the same roles the program does, so a reader who
finds the URL sees the overview and nothing else.

**TLS, in a closed lab.** The server will make its own certificate:

```bash
python -m fme hashserver --store hashes.db --make-cert \
    --cert-name hashes.lab.local --cert-name 192.168.1.50
```

Put in every name the server is reached by — a certificate is checked
against the name used to reach it. Then start with `--cert server.crt --key
server.key`. The key stays on that machine and is copied nowhere.

Because it is self-signed, each examiner's machine has to be told to trust
that exact certificate: copy `server.crt` to them and give it in the Hash
Sets tab. The program never simply skips verification — an encrypted
connection to whoever answered is not the same as an encrypted connection to
the hash store, and that difference is the whole reason for doing this. If
the certificate is not trusted, the program says so and names the fix rather
than reporting a network error.

Without `--cert` and `--key` the server warns when bound to a real
interface, because tokens and the hashes being looked up would cross the
network in the clear.

**A failure is reported, never swallowed.** If the server is unreachable the
program says so; an examiner who thinks they checked a run against the store
and did not is worse off than one who knows it is down.

Your token is kept for the session only and never written to disk.

### Contributing a case back

Files you **graded** are added to the store with the grading attached, so a
later case matching one is told *"Child Abuse Material (CAM), from
DC26-253"* rather than a bare "seen before". Uncategorized files are not
contributed — an ungraded hash teaches the next case nothing.

### When an import finds nothing

The import says what it saw: rows read, rows skipped and why. If the
structured reader comes back empty it scans the file for anything
hash-shaped and imports what it finds, saying so. **What is this file?**
inspects a set without importing it; **Verify** reads the store back from
the file itself.

### Stills and the videos they came from

A picture and the video it was taken from share no bytes, so no hash in this
program relates them. A *frame* of that video and the picture, though, are
the same image — which a perceptual hash can say.

**Hash Sets → Match stills to videos**, or `python -m fme framematch -o <run
folder>`. Each video's key frames are hashed and the stills matched against
them, so:

- a screenshot finds the video it came from, and the details pane says which
  frame;
- a video shows what was taken from it;
- a still that matches a frame of a video you have already graded tells you
  what you are looking at before you open it.

Tested on a frame saved as a JPEG and on the same frame resized and
re-compressed — what sharing does to a picture. Both found their video at 4
bits of difference; an unrelated picture did not match.

Two things the result means, and does not:

- **A match is the same picture to the eye, not the same file.** That is the
  point — a re-encoded, cropped or resized frame still matches. It also means
  two photographs of the same blank wall match, so featureless frames are
  excluded.
- **A video is a dozen frames out of thousands.** A still from a moment
  between them will not match at all. **A miss here means far less than a
  hit.**

Needs Pillow and OpenCV.

### Visual similarity

Needs Pillow. Three tiers:

- **Exact copies** — identical bytes, collapsed at extraction, every location
  recorded in `duplicates.csv`.
- **Visual stacks** — the same picture: a difference hash and a DCT
  perceptual hash must both agree.
- **Similar groups** — looser, browsable, with the distance shown for every
  match so the judgement stays with you.

Featureless images (flat colors, gradients) are excluded from grouping: they
look alike to any perceptual hash.

---

## 9. Cases

The program opens on **Cases**: every case processed here, with file and
graded counts. Click one to open it — nothing is carved or hashed again, and
the review comes back with it.

**Moving cases to a central folder** is ordinary housekeeping and the list
keeps up with it. Move the folders wherever you want them, then **Scan**
that folder — or:

```bash
python -m fme cases --scan "\\\\server\\cases"
python -m fme cases --relink DC26-253 "\\\\server\\cases\\DC26-253"
```

A case that has moved is **recognized, not added twice**: it is identified by
what the run was taken from, how many files it holds and the hashes at each
end of its manifest, so the existing entry is pointed at the new place and
its case number, examiner, gradings and history come along. A different case
in the same folder is added as a new one, not mistaken for it.

This works on cases made by any earlier version — the list is read from the
manifests, which have always been there.

The list is a table of contents, not a copy. A folder that has moved shows as
**missing** rather than being offered. Losing the list costs the list and
nothing else: **Scan** a drive and it finds the cases again by their
manifests.

---

## 10. Reports

**Export report**, top right, or **Menu → Export report**.

| | |
| --- | --- |
| Header | agency, case number, evidence item, examiner, logo, notes, the source image, when generated and in which time zone |
| Scope | everything · what the filters show · the selection · categorized only |
| Formats | HTML, **Word (.docx)**, **PDF**, CSV, JSON, MD5 list, KML, Project VIC |
| Scope | everything, what the filters show, the selection, categorized only, or **only the categories you choose** |
| Layout | choose the columns; optionally a section per flag |
| Front page | highlights, a category donut, counts with percentages, bar charts by file kind, size and year, and a plot of where geolocated media sits |

One export writes every chosen format over **one** scope, so the CSV cannot
disagree with the HTML beside it.

**Maps without the internet.** Nothing on this program fetches a map. Import
a basemap once -- a `.pmtiles` region cut from the Protomaps build with
`pmtiles extract`, or a raster `.mbtiles` from QGIS or a GIS office -- in
**Settings → Offline basemap → Import**, or on the command line:

```
python launch.py basemap import D:\maps\county.pmtiles
python launch.py basemap list
```

It is copied in and hashed. From then on the report's front page draws the
located files on a map instead of a plot of dots, each located card carries a
small locator map, and the details pane shows one beside the coordinates.
Every map names the basemap by its SHA-256, so it can be drawn again the same
way. A file outside the basemap gets no locator rather than a marker on an
empty square.

**The protocol report** (`Protocol (.docx)`, ticked by default) is the
document a case is handed over with: who examined what, the conclusion and
video-length tables, counts per device where a case has more than one, counts
per flag within each category, a signature block, and appendices — the
representative images if thumbnails are on, the definitions, and the file
list. It follows the shape examiners in this field already read, so a
prosecutor does not have to be taught a new document.

Its wording is yours to set: **Introduction** and **Definitions** in the
dialog replace the defaults.

**Choosing categories.** Scope → *Only the categories I choose* lists every
category with its count, ticked for the ones in use. A report sent to
somebody else has no business carrying the Non-Pertinent files, and this is
how they stay out — of the files, the thumbnails and every count in it. The
front page says which were chosen.

**The tables read in your category order** — the order of the category bar
and of the keys you press — not by how many files landed in each. A table
that reorders itself from case to case makes two reports hard to compare.
Non-Pertinent and Uncategorized are listed last: they say what was *not* a
finding, and a reader is looking for what was.

**Every report opens with two tables.** *Conclusion* gives, per category,
how many images and videos were found, how many of those are different
files, and the total. Files and unique files answer different questions —
twenty copies of one picture is twenty files and one picture — so both are
there, because a report giving only one invites the wrong reading of both.

*Overview of video length* gives the longest, shortest and total running
time per category. A clip that cannot be decoded has **no** length rather
than a length of zero, is left out of the totals, and is counted and
explained beneath the table: carved video often starts mid-stream, which is
enough to recover the content and not enough to measure it.

**A large case is written in pages.** Past about four hundred files the
contact sheet is split into numbered pages and thumbnails are written beside
the report rather than embedded in it. One document per case stops being
readable around a thousand files and stops opening at all well before a
hundred thousand — a run of that size produced a single multi-gigabyte page
no browser would open.

**It runs in the background, and you can stop it.** The dialog shows which
stage it is on and how far through; thumbnails are the whole cost and are
made several at a time. A report of a hundred and sixty thousand files is
still a matter of minutes, but you can see it working and stop it.

**Word** is written directly — no Word installation and no library needed —
with the pictures embedded. **PDF** is written with ReportLab
(`pip install reportlab`); without it, open the HTML report in a browser and
print it to PDF, which is laid out for printing and usually looks better.

**The front page carries the shape of the case** before any of it is shown:
how many files are in categories 1–3, what is flagged and with what, how many
carry coordinates, which cameras and camera bodies appear, the range of
capture dates, how many were deleted or carved, and how many matched a hash
set. Then a category donut with counts and percentages, the same for flags,
and bars by file kind, size band and year taken.

Charts are **SVG drawn by hand, with no script and no library**, so a report
opens years from now on a machine with no network and nothing but a browser.
Colors come from the case's own categories, so a chart and the gallery agree.

The plot of geolocated media shows **spread and clustering, not a map**. No
tiles are shipped and none are fetched, so drawing coastlines would be
inventing them; each file's coordinates link out to a map of your choosing.

Three things the report says about itself:

- **Scope is on the front page.** A report covering one category states that
  it does not cover the rest.
- **Thumbnails can be left out entirely**, and the header then says the
  report holds no image of any file. That is the report for someone who must
  not be shown the material, and it has to be distinguishable from one whose
  images failed to load.
- **Blurring is a reviewing aid, not a safeguard.** The image is still in the
  file, and the header says so.

### Other exports

| | |
| --- | --- |
| **Project VIC** (VICS 2.0) | for Griffeye. Carries the categories you assigned, mapping local codes 0–4 onto the codes VICS defines; 5 and above export with no category rather than a guess |
| **LAVA** | a project the LEAPP viewer opens. The media is written twice — `media/` and `_HTML/media/` — because the viewer serves the second copy; a project without it shows every image as a broken icon while every path in it is correct. `--validate` checks both, the two log pages, and every row, column and media reference |
| **C4All** | `C4M_Index.xml`, `C4P_Index.xml` and the reports, in the layout Griffeye and the C4All viewer ingest. Exporting again into the same folder removes the earlier export's disc-folder files that the new one does not list — except a file an earlier export *moved* there, which is the case's only copy: that one is left and named on screen, to be moved out before the folder is burned or loaded. `C4All_placed.json` records how each file was placed, for the next export |
| **KML / KMZ** | geolocated media for Google Earth, with capture times so it can be played in order |

---

## 11. Files a run produces

| File | What it is |
| --- | --- |
| `images/`, `videos/`, `audio/`, `other/` | the recovered files, by type |
| `manifest.csv`, `manifest.json` | every file with hashes, provenance and metadata |
| `duplicates.csv` | every other place each file was found |
| `hash_matches.csv`, `hash_store_matches.csv` | what the sets said |
| `similar_groups.csv` | look-alike grouping |
| `locations.kml` | anything carrying coordinates |
| `case.db` | categories, flags, notes, audit log — **the review** |
| `backups/` | snapshots of `case.db` |
| `_frames/` | decoded video frames; generated, not evidence |
| `ProjectVIC.json`, `lava/`, `report/` | exports |
| `WHICH_FILE_IS_WHICH.txt` | a plain-language guide to the above |

---

## 12. The command line

Every tool runs from `launch.py`, from any directory, with nothing installed:

```bash
python launch.py                                   the interface
python launch.py extract -i case.E01 -o D:\out --vic DC26-253
python launch.py inspect -i case.E01
python launch.py cases --list
python launch.py store --store D:\hashes\store.db --import NSRLFile.txt
python launch.py store --store D:\hashes\store.db --verify
python launch.py report -o D:\out -d D:\reports --case DC26-253 --format html
python launch.py frames -o D:\out\manifest.json
python launch.py triage -m D:\out\manifest.json
python launch.py recover --list-cases
python launch.py recover --case DC26-253
python launch.py doctor
```

`python -m fme <tool>` does the same but only from the project folder.
`pip install .` gives an `fme` command that works anywhere.

---

## 13. What needs installing

Only **Flask** is required. Everything else is optional and the program says
what is missing rather than failing:

| Install | Gives you |
| --- | --- |
| `pytsk3` | exFAT, NTFS, HFS+, APFS, ext2/3/4 |
| `libewf-python` | EnCase E01/Ex01 |
| `pillow` | visual similarity, report thumbnails |
| `opencv-python-headless` | frames from video a browser cannot play |
| `zstandard` | Snapchat content bundles |
| `pillow-heif` | **HEIC photographs, which iPhones write.** Without it they have no thumbnail, no preview and no perceptual hash |
| `pywebview` | a window of its own rather than a browser tab (`--window`) |
| `py7zr` | media inside 7z archives |
| `waitress` | a threaded server, and no development-server warning |
| `libvmdk-python`, `libvhdi-python`, `libqcow-python` | VMDK, VHD/VHDX, QCOW2 |

**Menu → What is installed** reports what this copy has.

---

## 14. Troubleshooting

**The page stops responding while scrubbing videos.** Fixed in v107.
Scrubbing used to seek the video on every mouse move — dozens of seeks a
second, each one a request to this program and a decode in the browser. On
carved video, which is malformed by nature, the browser falls behind and the
tab dies. It now swaps the frames this program already extracted: one
request per clip, then image changes, and no decoding in the browser at all.
A clip with no frames falls back to seeking, throttled to one at a time.

**A video tile is a picture, not a video.** The video element is made when
the cursor arrives and released when it leaves, so a page of five hundred
clips holds one at most — not five hundred, each holding decoder resources
the browser will eventually start tearing down.

What you get while scrubbing depends on what is ready. The extracted frames
answer immediately, and once the browser has the clip's header it takes over
and scrubs to **any** position rather than the dozen the frames cover. A
codec the browser cannot decode at all still scrubs, on the frames, because
those came from this program's own decoder.

Frames are fetched **before** you hover, for the tiles on screen, two clips
at a time, abandoning the queue when you change page. The decoding runs at a
lower priority than this program, so it never makes a keystroke wait: grading
during a full prefetch measured 6 ms, against 80 ms when the decoding
competed on equal terms.

**The browser says the page has stopped responding (`RESULT_CODE_HUNG`).**
Fixed in v91. Grading a file used to rebuild every tile on the page, which
on a page of thousands meant the browser destroying and re-fetching that
many images for each keystroke. Only the tiles that changed are repainted
now, and no more than five thousand tiles are built at once however the
page size is set — **all** on a large case was a way to hang the browser.
If you see it on v91 or later, say what you were doing: it means something
else is doing too much at once.

**"Task queue depth is 2" from waitress.** Not an error: it means two
requests were waiting for a free thread for a moment. A gallery asks for
hundreds of thumbnails at once and some requests are slow on purpose —
converting a HEIC, decoding video frames, reading from an evidence drive.
From v86 the server runs four threads per processor instead of a fixed
eight, so it is rarer; if you still see it while a report or a frame
extraction is running, it is the queue doing its job.

**"This is a development server."** Flask's own server. Bound to this machine
it is not a security problem, but it is single-threaded. `pip install
waitress` removes the warning and loads galleries faster.

**LAVA shows a broken image.** If *nothing* previews, the project was
written before v64 and has no `_HTML/media/` copy, which is where the viewer
reads. Re-export it; `--validate` now says so explicitly. If only some
formats are blank, those are the ones a browser cannot draw (EMF, WMF, TIFF,
PSD, camera raw, most video) — the file is fine, open it natively.

**Rows sourced from system binaries with `.emf` or `.ico` extensions.** Noise
from a tool version before v57. Re-run.

**An import added hashes but no categories.** The note said it fell back to
scanning the file for anything hash-shaped. That fallback keeps digests and
loses everything else, so a set imported that way asserts no category.
Before v69 the national download did this, because only the case shape was
read. Remove the source and import it again.

**An import added 0 hashes.** Use **What is this file?** and **Verify**. If
the store looks empty after an import, check for a `-wal` file beside it —
fixed in v51, which checkpoints after every write.

**"Unexpected token '<'" or "is not valid JSON".** Something answered a data
request with a web page — almost always a route that failed. From v66 the
interface shows the real reason instead; before that it showed only the
parse error. Check, in order:

1. **Upgrade by unpacking into an empty folder**, not by copying over an
   older one. A copy that misses a file leaves two releases mixed together.
   **Menu → What is installed** and `doctor` both warn when the files on disk
   disagree about their version, or when compiled files are left from a
   version that no longer has the source beside them.
2. **Delete every `__pycache__` folder** under the program and start again.
3. Open `/api/health` in the browser: it reports the release, the engine
   version, the Python version and which file is serving.
4. The console window the program was started from holds the full traceback.

**No module named fme.** `python -m fme` only works from the project folder.
Use `python launch.py <tool>` from anywhere.

**I graded the wrong files.** Nothing here is irreversible — see
**Undoing a mistake**. Undo or ctrl-Z while you are working; `fme recover`
for a grading made in an earlier version; `backups/` beside the case if
neither reaches it.

**"No case.db in that folder" from `fme recover`.** It was given the folder
holding `cases.db`, the list of cases, rather than a case. Run
`python -m fme recover --list-cases` to see the case folders, or give it a
case number.

**"Corrupt EXIF data" or "Truncated File Read" in the terminal.** Fixed in
v75: those are the image decoder's complaints about files that stop
mid-image, which is normal for carved media and was never a fault in the run.
They are now counted and reported with the pass that caused them — in the
similarity panel, and in a run's log line — instead of being printed once per
file into whatever window the program was started from.

**A photograph shows as a grey rectangle.** Formats a browser cannot draw —
HEIC, TIFF, camera raw, PSD — are rendered to JPEG on request and cached, so
they appear as pictures. HEIC needs `pillow-heif`; without it an iPhone's
camera roll has no thumbnails at all. The rendering is for display only: it
is never hashed, never counted and never written among the recovered files.

**"non-existing PPS 0 referenced", "decode_slice_header error", "no frame!"
in the terminal.** The video decoder complaining about a clip that is
missing the header describing how to decode it — normal for a carved video,
which often starts mid-stream. It means no frames could be read from that
file, not that anything is wrong with the run.

**Frames show nothing for a video.** The browser cannot play it and OpenCV is
not installed, or the clip is damaged. **Open with…** hands it to a real
player.

---

## 15. Code from other people

Several readers in this program were written by somebody else and are used
under their own licences. They are copied in verbatim under `fme/vendor/`, with their
licence texts beside them and their versions and SHA-256 hashes recorded in
`fme/vendor/vendored.json` — so an examiner asked in court what read the
evidence can say exactly which version of what, and show the file has not
been altered.

- **qnxprobe 1.77** — Copyright (c) 2026 Alexis Brignoni, MIT. https://github.com/abrignoni/qnxprobe
  Copied verbatim into `fme/vendor/`, with its licence at `fme/vendor/LICENSE-qnxprobe`. SHA-256 `5a12eae42ac77ea0c2563187605c4ae6…`, recorded in `fme/vendor/vendored.json`.

- **ewfprobe 0.15.0** — Copyright (c) 2026 Alexis Brignoni, MIT. https://github.com/abrignoni/ewfprobe
  Copied verbatim into `fme/vendor/`, with its licence at `fme/vendor/LICENSE-ewfprobe`. SHA-256 `4169e397ee125591f4245791b5d7427e…`, recorded in `fme/vendor/vendored.json`.

- **qnxmount (specifications only)** — Apache-2.0. https://github.com/NetherlandsForensicInstitute/qnxmount
  No code from qnxmount is here. qnxprobe transcribes its Kaitai byte-layout specifications for QNX ETFS and EFS, and its licence travels with that work. Licence at `fme/vendor/LICENSE-qnxmount`.

- **impacket_ese** (ESE database reader) — Adapted from Fortra impacket 0.13.1 by DLEAPP; where the copy came from is recorded in `fme/vendor/vendored.json`. Apache Software License 1.1, licence at `fme/vendor/LICENSE-impacket_ese`. SHA-256 `494b4fe4b61006dc882d4271f675f4f9…`, recorded in `fme/vendor/vendored.json`. Reads `Windows.edb` for the Windows Search index, always in a separate process (section 15b).
  **Acknowledgment required by its licence:** *This product includes software developed by SecureAuth Corporation (https://www.secureauth.com/) and Fortra (https://www.fortra.com).*

- **Adapted code, MIT** — Copyright (c) 2026 charpy4n6, Alexis Brignoni. Not copied as a file: parts of an MIT-licensed project were adapted into this program's own code -- reading `Windows.edb`, Apple CgBI PNGs and KTX GPU textures, starting a reader in a separate process, and the banding used to group similar pictures. The licence and notice are at `fme/vendor/LICENSE-gleapp`; the project and version are recorded in `fme/vendor/vendored.json`.

**What the licence asks, and what this does about it.** MIT permits using,
copying and shipping the code, including inside something sold, on one
condition: the copyright notice and the permission text travel with it. They
do — in `fme/vendor/`, in this section, and in what the program reports about
itself. `tests/test_vendor.py` fails if a licence file goes missing, if a
copyright notice is stripped, or if a vendored file stops matching the hash
recorded for it, so a release cannot quietly breach those terms.

**Do not edit the files in `fme/vendor/`.** A change there is lost the next
time the reader is updated, and it breaks the hash that proves the copy is
unaltered. Fix it upstream and vendor the new version.

**What they add.** `qnxprobe` reads the file systems this program has no
reader for: QNX6, QNX4, ETFS, EFS and QNX IFS (vehicle systems), F2FS
(Android from about 2019), and the flash file systems SquashFS, JFFS2,
UBI/UBIFS and YAFFS. It is used only for those — a FAT32 or NTFS volume is
read as before, because two readers for one volume is two answers to the same
question. `ewfprobe` opens acquisitions, and does two things. It reads the common
formats **without the C extensions they normally need** — libewf and the
rest, which on a Windows examination machine frequently will not build, and
an examiner who cannot open an E01 cannot start. And it reads containers
this program has no other reader for at all:

| | |
| --- | --- |
| EnCase / EWF | `.E01`, `.Ex01`, `.s01`, and L01 logical evidence |
| Advanced Forensic Format | `.aff`, `.afm`, AFD folders, `.aff4` including striped |
| Apple | `.dmg` (UDIF, segmented), `.sparseimage`, sparse bundles |
| FTK | `.ad1` logical evidence, AD-encrypted E01 / SMART / raw |
| Virtual disks | VHD, VHDX, VMDK, QCOW 1–3, including differencing disks |

Where libewf **is** installed it is still used for `.E01`: it is the
reference implementation, and swapping a working reader for another one is
not an improvement.

## 15a. What it does not do

`docs/AUDIT.md` — **Menu → What this program does not do yet** — compares
this program feature by feature with the other open-source tool in this
space, with what each gap would cost to close. The list below is the short
form.

- **RAR archives** are detected and counted, not opened.
- **PST/OST and registry hives** are not parsed. `Windows.edb` is read only to
  name cached thumbnails (`fme winsearch`), not as a general ESE database.
- **WAL and journal side files** of SQLite databases are not merged.
- **Encrypted content** is not decrypted.
- **No reverse geocoding.** A city appears only because a person or a tool
  wrote it into the file.
- **Hash sets classify; nothing else does.** When a run finishes in the
  program and a hash store is set up, the run is checked against it, and so
  is any run when *Check this case against the store* is pressed. A file
  nobody has graded takes the category its set asserts (the most severe, if
  several do), or Non-Pertinent if only the NSRL knows it -- and is marked as
  **set by a hash set**, with the set's name, never as an examiner's grading.
  A file an examiner has graded is never changed. Nothing else assigns a
  category: screening, similarity and content search measure and sort only.
  A command-line run (`python -m fme extract`) consults no store: open its
  output as a case and press *Check this case against the store*. **A file matching nothing is unknown,
  which is not the same as cleared.**
