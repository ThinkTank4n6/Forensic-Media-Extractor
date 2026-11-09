# What the examiner needs to run

Everything here needs hardware, an operating system or a library the
development environment does not have. **Until somebody runs these,
they are untested by the people who wrote them** — that is the plain
meaning of `docs/VALIDATION.md` §5.10.

Work through the sessions in order. Each says what to run, what it
proves, what to send back, and what a failure means. Fill in the
results table at the end as you go; that table is the thing worth
keeping.

**What changed in v2026.11.306.** The development environment now builds
The Sleuth Kit (pytsk3 20260715, TSK 4.15.0) and libewf (20240506) from
source, and the suite runs with them. That is not an examiner's session --
it is the same author's machine -- but four things that used to be "never
run by anyone" now run on every build:

- **NTFS written by Windows 11 itself**, and exFAT, FAT32 and NTFS
  deletion volumes written by Linux's own drivers, are read by TSK and by
  this program's reader and checked against the hashes the operating system
  recorded (`tests/test_real_volumes.py`). Running it this way found four
  defects the earlier suite could not see (CHANGELOG v2026.11.306).
- **An E01 that libewf's own `ewfacquire` wrote** is read by libewf and by
  the vendored reader, both checked against the files recorded before
  acquisition (`tests/test_libewf_acquisition.py`).
- **TSK's command-line tools** (`fls`, `icat`) cross-check the FAT fixture.
- **APFS on a machine with pytsk3**: the published pytsk3 cannot open an
  APFS container; the vendored reader now reads it there instead.

Sessions B, C and D below still matter: a real acquisition from FTK Imager
or a write-blocked drive, and a Mac-made APFS volume **with pictures on
it**, are evidence a development machine cannot manufacture.

---

## If you have one hour, do these three

In order. Together they answer more than everything else here
combined.

1. **Session A** (5 min) — what the lab machine can actually do, and
   the first entry in the validation log.
2. **Session B** (10 min) — a real E01 from a closed case. We can read
   the one this project wrote; nobody has confirmed we can read what
   FTK Imager produces.
3. **Session C** (30 min) — NTFS and exFAT known-answer fixtures. Most
   of your computer evidence and most of your card and phone storage,
   neither ever tested by the people who wrote the readers.

Everything below that is worth doing and none of it is urgent in the
same way.

---

## Session A — Windows, five minutes

Do this first. It costs nothing and it tells us what the lab machine
can actually do.

```
pip install -r requirements.txt
python tools/verify.py
```

**Send back:** the whole output.

**The two lines that matter:**

- `a deleted FAT32 entry is recovered` — if it says `0 recovered`,
  deleted recovery is not working on a machine with TSK installed, and
  that is the most serious open question in this project.
- `both file-system engines recover the same files` — if the two
  disagree, one of them is wrong about a volume, and we need to know
  which before either is relied on.

Then:

```
python tools/validate.py --examiner "<your name>" --agency "<agency>" ^
                         --note "first record on the lab machine"
```

This appends to `docs/VALIDATION_LOG.md` and keeps the full record
under `docs/validation/`. Your machine has `pytsk3` and `pyewf`; mine
has neither, so **your record covers file systems mine cannot reach.**

**And one device, added in v2026.11.272.** From an administrator
prompt, with a write-blocked USB stick or disk attached as drive 1:

```
python -c "from fme.engines import media_core as m; r = m.open_image(r'\\.\PhysicalDrive1'); print(r.size, r.sector)"
```

**Send back:** the two numbers, and the size Disk Management shows for
that disk. Before this version a physical drive could not be sized at
all; the code that sizes it now has only been tested against a stand-in.

---

## Session B — a real E01, ten minutes

**This is now the only open question about the format every case
arrives in.** The fixture this project packs is read by the vendored
reader and **mis-read by libewf** — libewf accepts it, reports a size
and returns nothing useful, so on a machine with libewf the fixture
proves nothing either way. Only a real acquisition settles it.

The single most valuable thing on this list.

Every case arrives as an E01. There is a fixture, but this project
wrote it and this project reads it — a round-trip, not proof we can
read what FTK Imager produces.

Take any small retired acquisition. It does not need to be
interesting; it needs to be real.

```
python -m fme extract -i "<path to a real .E01>" -o "<a scratch folder>" --mode carve
```

**Send back:** the file count, how long it took, and anything that
failed.

**If it will not open**, send the error. That is a fault in the reader
on real-world acquisitions, and it outranks everything else here.

---

## Session C — WSL2, half an hour

Two known-answer fixtures: NTFS and exFAT. These close the largest
hole, because NTFS is most of your computer evidence and exFAT is most
of your card and phone storage.

**Once only**, in PowerShell as administrator:

```
wsl --install -d Ubuntu
```

Reboot, let it make your Linux user, then in the Ubuntu window:

```
sudo apt update
sudo apt install ntfs-3g ntfsprogs exfatprogs exfat-fuse fuse3 \
                 python3-pil sleuthkit
```

Build in the Linux home directory, **not** on `/mnt/g` — these mount
through FUSE and that is unreliable on a Windows drive:

```
cd ~
cp /mnt/g/forensic_media_extractor/tools/make_fixtures/make_ntfs_deleted.sh .
cp /mnt/g/forensic_media_extractor/tools/make_fixtures/make_exfat_deleted.sh .
bash make_ntfs_deleted.sh ntfs-deleted.img
bash make_exfat_deleted.sh exfat-deleted.img
cp ntfs-deleted.img ntfs-deleted.intended \
   exfat-deleted.img exfat-deleted.intended \
   /mnt/g/forensic_media_extractor/tests/fixtures/
```

Then run the test **from inside WSL**, so the Sleuth Kit comparison
runs:

```
cd /mnt/g/forensic_media_extractor
python3 tests/test_known_answer.py
```

**Send back:** the whole output, and the two `.intended` files.

**What each proves:**

- NTFS: deleted files come back byte-for-byte, and a **resident** file
  — one held inside its MFT record, which no carver can ever reach —
  comes back from the MFT.
- exFAT: deleted files come back, and one with a long name **keeps its
  name**. exFAT spreads a name across several directory entries; a
  reader that reassembles them wrongly returns the right bytes under
  the wrong name, which is worse than failing because nothing looks
  broken.

**If the NTFS build stops, saying the file was given clusters of its
own**, that is the script working as intended. Send the message.

---

## Session D — the Mac, fifteen minutes

Nothing to install; `hdiutil` is part of macOS.

```
bash tools/make_fixtures/make_mac_deleted.sh hfs
bash tools/make_fixtures/make_mac_deleted.sh apfs
```

Copy the four files it writes into `tests/fixtures/` on the Windows
machine, then run `python tests\test_known_answer.py`.

**What to expect.** HFS+ behaves much like FAT: the deleted files are
expected back, and the test fails without them.

**APFS is different and the fixture says so.** It is copy-on-write and
trims aggressively, so a deleted file's blocks may be gone before the
image is taken. Those rows are marked `deleted-may-be-gone`: if they do
not come back the test reports it without failing, because that is the
file system behaving as it does rather than evidence about the reader.
On APFS the **live** file is the real test.

**If the deleted APFS files do come back**, say so — it means deleted
recovery on APFS is better than this project claims for it, and the
documentation should be corrected upward.

**And one device, added in v2026.11.272.** With a USB stick attached
(find it with `diskutil list`, say `/dev/disk4`), unmounted but not
ejected (`diskutil unmountDisk /dev/disk4`):

```
sudo python3 -c "from fme.engines import media_core as m; r = m.open_image('/dev/rdisk4'); print(r.size, r.sector)"
```

**Send back:** the two numbers and what `diskutil info /dev/disk4` gives
for *Disk Size* and *Device Block Size*. macOS used to be read with
512-byte sectors whatever the disk had.

---

## Session E — the interface, ten minutes

```
pip install playwright
playwright install chromium
python tests/test_browser.py
```

Drives the real program in a real browser and reports anything the page
throws while being used. About half the interface is covered.

Then confirm two things by hand, which no test watches:

- **Display tab:** the tile slider resizes the tiles, and 200 / 500 /
  1500 changes the page.
- **The crashed case:** press *rebuild its list*, then resume it with
  `--resume` pointed at that case folder. Tell me whether the 114,041
  files come back.

---

## Session G — Windows Search and iOS snapshots, fifteen minutes

Added in v2026.11.293. Both readers were tested only against stand-ins,
because this environment cannot produce the real files.

**A real `Windows.edb`.** From any Windows 10 exhibit already processed
here (or the lab machine's own, copied out with `esentutl /y`):

```
pip install -r requirements.txt
python launch.py winsearch -o <that case's output folder> --index <copy of Windows.edb>
```

**Send back:** the whole output. The lines that matter are `ESE table`
(it should name `SystemIndex_PropertyStore` on Windows 8 and later),
`Unreadable rows` (any number above zero), and `Named`. Then open two of
the named thumbnails' paths in `thumbnail_sources.csv` and confirm by eye
that each thumbnail is a picture of the file the index names. A count
alone would not show that the byte order is right; a matching picture
does.

**Real iOS snapshot textures.** From a full-file-system iOS extraction,
any folder under `.../SplashBoard/Snapshots/`:

```
python launch.py extract -i <that folder> -o <somewhere new>
python launch.py gui
```

Open the run. **Send back:** how many `.ktx` files it lists, and whether
the gallery tiles show the app screens or a reason. A reason naming
`texture2ddecoder` or `pyliblzfse` means that package did not install;
any other reason, or a blank tile, is a fault to report with the file.

---

## Session F — not a computer task

Give `docs/VALIDATION.md` §5 to somebody in the unit who did **not**
build this tool, and ask what is missing or overstated.

The one error found in it so far — a false claim that comparing against
The Sleuth Kit was an independent check, when `pytsk3` *is* The Sleuth
Kit — was found by an examiner reading it, not by its author.

---

## Results

Fill this in as each session is done. A run that failed stays in the
table: a record that keeps only successes is not a record.

| session | date | machine | result | sent back? |
| --- | --- | --- | --- | --- |
| A — verify and record | | | | |
| B — a real E01 | | | | |
| C — NTFS and exFAT | | | | |
| D — APFS and HFS+ | | | | |
| E — the interface | | | | |
| G — Windows Search and iOS snapshots | | | | |
| F — someone else reads §5 | | | | |

---

## What none of the above covers

Said here so it is not mistaken for done:

- About half the interface — the extraction options, the report and
  export screens, the hash-store screens, and anything behind a file
  dialog.
- BitLocker, LUKS, ReFS, btrfs, LVM. Build fixtures only if your cases
  meet them.
- Deployment: there is no procedure for a second examiner to install,
  update, or confirm which version they are on.
- No independent party has assessed this tool, and no error rate has
  been established.
