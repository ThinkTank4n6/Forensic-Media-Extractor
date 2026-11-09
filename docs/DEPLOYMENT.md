# Installing, updating, and knowing what you are running

**On version numbers.** Releases are numbered **Year.Month.Build**:
v2026.11.271 is build 271, made in November 2026. The build number carries
on from the single integers used up to v270 and never resets, so it is
what orders releases and what the version floor below compares --
v2026.11.271 and an older client reporting 271 are the same build to it.
The year and month say when a build was made and nothing more: there is
no major or minor part, and the number makes no promise about
compatibility. The fingerprint, not the number, identifies a build
exactly.

Written for a unit with several examiners sharing one hash store.

The question this exists to answer is not "how do I install it" — a
folder and Python do that. It is **"are we all running the same
program, and can we say which one produced this report?"** With seven
accounts on the store and three of them able to remove sources, that
stops being a tidiness question.

---

## 1. Installing on a new machine

```
python --version            # 3.9 or newer; 3.11 or newer recommended
pip install -r requirements.txt
```

That installs everything the program can use, and `requirements.txt`
says, package by package, what each adds and what is lost without it.
Two matter most: `pytsk3` makes NTFS, exFAT, HFS+, APFS and ext
readable — **without it the program cannot read a Windows volume at
all**, and will say so rather than failing quietly — and
`libewf-python` opens E01. **APFS is the exception:** the pytsk3 on
PyPI cannot open an APFS container, so APFS is read by the program's own
(vendored) reader whether or not pytsk3 is installed, and `fme doctor`
says which reads it. If either will not build, install
`requirements-minimal.txt` to get working, then add the rest; nothing
needs reinstalling. `pycryptodome` and `argon2-cffi` are what open
encrypted acquisitions, BitLocker and LUKS2; without them those images
open as unreadable.

Unpack the program folder anywhere the examiner can write to, then:

```
python -m fme version
python tools/verify.py
```

The first says what is installed. The second says what this machine can
actually do, and is the thing to keep.

---

## 2. Knowing what you are running

```
python -m fme version
```

```
Forensic Media Extractor v199
  code fingerprint  2d3179d6bb78d3600b8d45f455c35ae8df029841043cd018296af3c04137e6fb
  installed at      G:\forensic_media_extractor
  Python            3.12.3 on Windows 11
```

**The fingerprint is a SHA-256 over every code file actually present.**
Two machines reporting the same version *and* the same fingerprint are
running the same program. The same version with a different fingerprint
means one has been edited, or did not copy cleanly — which is worth
finding before a case depends on it rather than after.

Record the version and fingerprint in the case notes for any case whose
output will be relied on. Nothing else records which build produced a
report.

---

## 3. Seeing what the unit is running

Every request to the hash store now carries the client's version, and
the server records it. An admin can list who is on what:

```python
from fme.review.hashserver import Accounts
for row in Accounts(r"C:\hashstore\data\hash_users.db").client_versions():
    print(row["who"], "v" + row["version"], row["last_seen"])
```

```
tankelewicz        v199   2026-11-01T02:50:56
second_examiner    v178   2026-11-01T02:50:56
```

An old client is **not refused** — it is recorded. Refusing would stop
an examiner mid-case over a version number; recording lets somebody
notice the drift and fix it deliberately.

---

## Updating the server first

From v2026.11.303 a remote scan's lookups are answered per hash by the
server; a server older than that reports most files of a matched set as
unmatched, whatever the client. Update the server before the workstations.
The server accepts requests of up to 128 MiB and 500,000 graded files, so a
workstation still on an older build can contribute a large case in one
request; v2026.11.303 and later send 5,000 at a time.

**Before any case is graded under a scheme other than the United States**
(Settings → Grading scheme, since v2026.11.317), update the server and every
workstation that will open such a case to v2026.11.318 or newer. A build
before that reads the case's codes as US ones in its reports and exports;
v2026.11.318 refuses to send a non-US case to a server older than 1.2.0, and
records in each such case the build it needs. The server's store holds one
scheme: set it while the store is empty (`python -m fme.review.hashstore
--store <db> --region UK`).

## 3a. Requiring a version

An admin can set a floor on the server:

```
python -m fme hashserver --store <store> --accounts <accounts> ^
       --host 0.0.0.0 --port 8787 --cert server.crt --key server.key ^
       --min-version 2026.11.271
```

**It applies to contributing, importing and removing. It never applies
to looking hashes up.** An examiner on an old build can still check a
case against the store and finish what they are doing; what they cannot
do is write into a set everyone shares. Stopping a lookup part way
through a case over a version number is a worse outcome than the drift
it would prevent.

A refused write says which version is required and which one the client
reported, and points at this document.

**Set the floor to a build the unit has actually approved**, not simply
to the newest. A version existing is not the same as a version having
been validated here — see `docs/VALIDATION.md` and the log.

## 4. Updating

Order matters: the server first, then the examiners.

**The server:**

1. Stop it. Look the task name up rather than assuming —
   `schtasks /query /fo table | findstr /i "hash fme"` — then
   `schtasks /end /tn "Start_FME_Hash"`. If it was started by hand in a
   Command Prompt, `schtasks /end` will not touch it: use Ctrl-C in
   that window, or `netstat -ano | findstr :8787` and `taskkill /pid
   <pid> /f`.
2. Replace the program folder. Leave `C:\hashstore\data` alone — the
   store, the accounts and the certificate live there.
3. Start it again and confirm: `python -m fme version` on the server,
   then **Verify** on an examiner's Hash Sets tab, which reports the
   server's version.

**Each examiner:**

1. Replace the program folder. Case output lives elsewhere and is not
   touched.
2. `python -m fme version` — check the version *and* the fingerprint
   match what the server machine reports.
3. `python tools/verify.py` — libraries can be lost in an upgrade, and
   a machine that silently stopped being able to read NTFS looks
   exactly like one that can.

**Where the records live.** Validation records are kept **outside the
program folder**, because that folder is replaced on every update:

| | |
| --- | --- |
| Windows | `%LOCALAPPDATA%\ForensicMediaExtractor\validation` |
| Linux and macOS | `~/.local/share/forensic-media-extractor/validation` |

Set `FME_VALIDATION_DIR` to put them somewhere else — a case share, or
a folder your unit backs up. They are the record of what was checked
and when, so they belong wherever your unit keeps things it must be
able to produce later.

Up to v221 they were written inside `docs/` in the program folder and
an update deleted them. `validate.py` now moves any it finds there
before that can happen, so a copy updated from an older version keeps
its history — but only if it is run once before the old folder is
thrown away.

**After any update, before the next case:**

```
python tools/validate.py --examiner "<name>" --agency "<agency>" ^
                         --note "after updating to vNNN"
```

That appends a row to `docs/VALIDATION_LOG.md` and keeps the full
record. It is the evidence that this build was checked on this machine
on that date.

---

## 5. Accounts on the store

Three roles, and the difference matters:

| role | may |
| --- | --- |
| `read` | look hashes up, see what the store holds |
| `contribute` | that, and add a case's hashes |
| `admin` | that, and **import sets, remove sources, manage users, read the audit** |

```
python -m fme hashserver --store <store> --add-user jsmith --role read
```

**An admin can remove a source and every hash in it.** Give the role to
as few people as the work allows, and know who has it. To see:

```python
from fme.review.hashserver import Accounts
for person in Accounts(r"C:\hashstore\data\hash_users.db").users():
    print(person["name"], person["role"])
```

**When somebody leaves**, disable the account rather than leaving it
dormant — `Accounts.disable(name)` — because its token keeps working
until you do. Their past
contributions stay in the store and in its audit, which is correct —
the record of who contributed what should not change because somebody
changed jobs.

---

### Who owns a case in the store (v2026.11.310)

A case source belongs to the person whose contribution created it. A
contribution naming another examiner's case is refused (the page shows
the server's message) unless it comes from an administrator, so one
account cannot regrade another examiner's case. Every grade a contribution
changes is recorded old to new, in the store's `grade_changes` table and in
the server's access log.

Case sources made before v2026.11.310 belong to the person their
"contributed by" note names. Any other older case source can be added to
only by an administrator. Update the server first (above): an older server
does not check ownership.

### Recovering decisions (`fme recover --undo`)

Recovery replays each file's whole history -- gradings, undos, clears,
deleted categories and scheme migrations -- applies the result all at
once, and keeps the original examiner and time on each restored decision.
An undo made by a version before v2026.11.310 did not record which files it
changed. Recovery matches it to the case's own undo record when exactly
one fits, and otherwise refuses rather than guess. Add
`--accept-unattributed-undo` only after reading what it reports.

## 6. What a new examiner should be given

- The program folder, and this document.
- Their own store account and token. **Not a shared one**: the store's
  audit attributes work by account, and a shared account makes that
  record worthless.
- `docs/VALIDATION.md`, particularly §5, before they rely on output.
- `MANUAL.md`.

Ask them to run `python tools/verify.py` and send the result on their
first day. It takes a minute and it is the only way to find out that a
library is missing before a case does.
