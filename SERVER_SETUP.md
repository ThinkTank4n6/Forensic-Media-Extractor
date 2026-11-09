# Setting up the shared hash store

A step-by-step guide. It assumes nothing: if you can install a program and
copy a file, you can do this. Every command is written out in full — type
it, or copy it, exactly as shown except where it says to put your own
details in.

**What you are building.** One computer in your lab holds the hash store and
shares it. Everyone else's copy of Forensic Media Extractor asks that
computer instead of keeping its own store. Imports happen once, in one
place; a case graded by one examiner informs the next examiner's case; and
the store can say who looked up what.

**Why not just put the file on a shared drive.** Because it breaks. The
store is a SQLite database, and SQLite relies on file locks that network
drives handle badly or not at all. Two people writing to it across a share
is how the file gets corrupted — usually silently, and usually noticed weeks
later. One computer owning the file is the fix.

Set aside about forty minutes for the first time.

---

> **About the name `hashes.db`.** It is only the name used in these
> examples. The store can be called anything — `hashes.db` is just as
> good — and nothing converts between them: the name is not part of the
> format. What matters is that **every command and the scheduled task name
> the same file**. A server started without `--store` now refuses to start
> rather than inventing an empty one, because an empty store answers every
> lookup with "not known" and looks like it is working.

> **Importing a set into a shared store.** The file is read **from a path
> on the server**, not from the machine you are sitting at — a national set
> is gigabytes and is not sent over the connection. Copy it to the server
> first and give the path as the server sees it, or import it on the server
> itself:
>
> ```
> python launch.py store --store C:\hashstore\data\hashes.db --import D:\sets\RDS.db
> ```
>
> Both forms of an NSRL RDS database are read — the modern one with a
> `METADATA` view and the older `FILE` table — as are `NSRLFile.txt` and a
> Project VIC `.json`.

> **Checking the store for damage.** A store is one large SQLite file
> that several people write to over a network, on a machine that can
> lose power mid-import. When it goes wrong the symptom is not an
> error: lookups stop matching things they used to match, which in a
> case looks like the file not being in the set.
>
> ```
> python -m fme store --store C:\hashstore\data\hashes.db --integrity
> ```
>
> Read-only. It reports whether the pages are sound and, if they are
> not, what to do in order — stop the server, preserve the file, restore
> the last good copy and re-import the sets, then re-contribute the
> cases. **It does not repair in place**, deliberately: a salvaged store
> is one whose contents nobody can account for, and this store decides
> what an examiner is shown about a file.
>
> Worth running monthly, and after any power loss or interrupted
> import.

## Part 1 — Choosing the computer

It does not need to be powerful. It needs to be:

- **On and reachable** whenever examiners are working. A desktop that gets
  shut down at night is a poor choice; a machine that stays on is a good one.
- **Backed up**, or somewhere you can back up. The store is the accumulated
  work of your unit.
- **Inside your lab network**, not exposed to the internet.

A spare workstation is fine. So is a small server if your unit has one.

Write down two things about it now, because you will need them repeatedly:

- Its **name** on the network (on Windows: open Command Prompt and type
  `hostname`).
- Its **IP address** (Command Prompt: `ipconfig`, and look for *IPv4
  Address* — something like `192.168.1.50`).

---

## Part 2 — Installing what is needed

On that computer:

**1. Install Python.** Download it from python.org. During installation,
tick **"Add Python to PATH"** on the first screen — it is easy to miss, and
nothing else works without it.

**2. Copy the program there.** Unzip Forensic Media Extractor into a folder
you will not accidentally delete. Something like:

```
C:\hashstore\forensic_media_extractor
```

**3. Install its two helpers.** Open Command Prompt, and type:

```
cd C:\hashstore\forensic_media_extractor
pip install flask waitress cryptography cheroot
```

`flask` runs the program; `cheroot` serves it over TLS from a pool of
threads, so one examiner scanning a large case does not make everybody else
wait; `waitress` does the same where there is no certificate; `cryptography`
makes the certificate in Part 4.

Without `cheroot` the server still works and still uses TLS, but on Flask's
own single-threaded server — it will say so at startup, and one slow lookup
will block the others behind it. If `pip` is not recognized, Python was installed
without "Add Python to PATH" — reinstall it and tick the box.

**4. Check it works:**

```
python launch.py doctor
```

You should see a version number and a list of what is installed. On a
machine that only serves the hash store, most of those lines can say no:
**flask**, **waitress** and **cryptography** are the ones that matter here.
`cv2` is for reviewing video and `LAVA` is an examiner's viewer — neither is
used by a server. If you see
`No module named fme`, you are in the wrong folder — `cd` to the one you
unzipped.

---

## Part 3 — Creating the store

Decide where the store file lives. Two rules about where:

**On a disk in this machine, not on a share.** A drive letter is not proof
either way: `W:` may be a disk inside the machine, which is fine, or a
mapped network drive, which is not. On the server, open a Command Prompt and
type `net use` — if `W:` is listed, it is a share.
 A store is a SQLite database
and needs file locking that network shares implement badly or not at all. On
a mapped drive it is slow, it fails with "disk I/O error", and two machines
writing to it can corrupt it quietly. Sharing it is what the server in this
guide is for — that is the whole point of it.

**Separate from the program**, so that replacing the program never touches
it:

```
C:\hashstore\data
```

Create that folder. The store file itself is created automatically the first
time something is imported into it.

**Import your first set now**, so there is something in it. Put the file —
your Project VIC download, or the NSRL — on this computer, then:

```
python launch.py store --store C:\hashstore\data\hashes.db --import C:\hashstore\data\VIC_US.json
```

This takes a while for a national set. When it finishes it will say how many
hashes were stored. Check that number looks right: a Project VIC download
should produce millions, not thousands. If it mentions falling back to
scanning the file, stop and read that message — it means the categories were
not read.

---

## Part 4 — Making the security certificate

Traffic between the examiners' computers and this one should be encrypted.
Without it, anyone who can see traffic on your network can read the tokens
and every hash being looked up.

In a closed lab there is no certificate authority to ask, so the server
makes its own:

```
python launch.py hashserver --store C:\hashstore\data\hashes.db --make-cert --cert C:\hashstore\data\server.crt --key C:\hashstore\data\server.key --cert-name YOUR-COMPUTER-NAME --cert-name 192.168.1.50
```

Replace `YOUR-COMPUTER-NAME` and the IP address with the two things you wrote
down in Part 1. **Put in every name the server will be reached by.** A
certificate is checked against the name typed to reach it, so if you only
list the computer name, anyone who types the IP address gets a warning
instead of a connection.

Two files appear: `server.crt` and `server.key`.

- **`server.key` is the secret.** It never leaves this computer. Do not email
  it, do not put it on a share.
- **`server.crt` is the public half.** You will copy this one to every
  examiner's computer in Part 8.

---

## Part 5 — Creating yourself as administrator

```
python launch.py hashserver --store C:\hashstore\data\hashes.db --add-user ctankelewicz --role admin
```

Use your own name or badge number in place of `ctankelewicz`.

It prints a long string of random characters. **That is your token.** It is
shown once and never again: it is stored scrambled, so it cannot be looked
up later. Copy it somewhere safe now — your password manager, if your unit
uses one.

> **What a token is.** It is a password the computer invents for one person.
> You never choose it and never type it twice: you paste it once into the
> program, or into the sign-in page, and it is remembered for that session.
> Everything that person does is recorded against it, which is what makes
> the activity record worth anything.

If a token is ever lost or exposed, run the same `--add-user` command again
for that person. It issues a new token and the old one stops working.

---

## Part 6 — Starting the server

```
python launch.py hashserver --store C:\hashstore\data\hashes.db --host 0.0.0.0 --port 8787 --cert C:\hashstore\data\server.crt --key C:\hashstore\data\server.key
```

`--host 0.0.0.0` means "accept connections from other computers". Without
it, only this computer can reach the store.

Since v2026.11.313 the server **will not start** on `0.0.0.0` (or any
address other than this computer's own) without `--cert` and `--key`.
Without them every examiner's token and every hash they look up would cross
the network in the clear. If TLS is handled by a reverse proxy in front of
the server, add `--insecure` to say so deliberately.

Leave that Command Prompt window open — closing it stops the server. Making
it start automatically is Part 10.

**Windows will probably ask whether to allow it through the firewall.** Say
yes, for **private networks**. If no prompt appears and examiners cannot
connect, open the port by hand — in a Command Prompt **run as
administrator**:

```
netsh advfirewall firewall add rule name="Hash store" dir=in action=allow protocol=TCP localport=8787
```

---

## Part 6a — If the browser says it cannot connect

Run this on the server. It starts the server, talks to it, and reports what
is wrong rather than leaving you to guess:

```
python launch.py hashserver --store C:\hashstore\data\hashes.db --cert C:\hashstore\data\server.crt --key C:\hashstore\data\server.key --check
```

It checks the files exist, the store opens, the port is free, and that the
server answers — and prints the exact address to type.

**It is a test, not a start.** The check starts a server, talks to it, and
stops it again, so nothing is listening when it finishes. Opening the admin
page straight afterwards gives "refused to connect" because there is
genuinely nothing there. Start the server with the Part 6 command, in a
window you leave open, and then open the page.

**The most common cause by far is `http` where it should be `https`.** With
`--cert` and `--key` the server speaks TLS only, and a browser given
`http://` is refused with no explanation at all. The browser says the same
thing — "refused to connect" — whether the server is absent, on another
port, or simply speaking the other protocol.

## Part 7 — The admin pages

On the server computer, open a browser and go to:

```
https://localhost:8787/admin
```

Your browser will warn that the certificate is not trusted. On this machine
that is expected — choose **Advanced** and continue. (The examiners'
computers will not see this warning once you have done Part 8.)

Paste your admin token to sign in. You will see four pages:

| Page | What it is for |
| --- | --- |
| **Overview** | how many hashes, how many sets, and who has used the store — lookups, contributions, and how many distinct cases each person has contributed |
| **People** | add someone, see their token once, disable anyone who leaves |
| **Hash sets** | import the NSRL, a Project VIC set or a list of your own from a path on this computer, see what each holds by category, or remove one |
| **Activity** | the full record of who asked for what, when, and from which address |

**Hash sets** has a section for each kind. **NSRL** takes the RDS SQLite
database or `NSRLFile.txt`; every hash in it counts as **0 Non-Pertinent**.
**Project VIC** takes a VICS JSON file; each hash keeps the category the set
gives it (0 Non-Pertinent, 1 CAM, 2 Child Exploitive / Age Difficult, 3 CGI /
Animation, 4 Comparison, 5 Uncategorized). The section decides how the file
is read: a Project VIC file put in the NSRL section is refused, because read
as the NSRL its CAM hashes would count as known software. The import runs in
the background and the page refreshes until it is done, then says how many
files and hashes came in -- "212,004 file(s), 636,012 hash(es) from the NSRL,
every one Non-Pertinent (0)", or "829 file(s) ... 33 of them CAM" -- with a
table by category saying what a match does to a file in the gallery. The same
counts go in the Activity record.

A match grades a file in an examiner's gallery only if nobody has graded it
yet. A Project VIC category beats the NSRL (a picture shipped in an installer
is still CAM if Project VIC says so); VICS 5 (Uncategorized) grades nothing.

**To add an examiner**: People → type their name → choose a role → **Add**.
Their token appears once, in a black box. Copy it and give it to them.

**The three roles:**

| Role | Can |
| --- | --- |
| **read** | look up hashes, see what sets are loaded |
| **contribute** | all of the above, plus send their graded cases back into the store |
| **admin** | all of the above, plus import sets, remove sources, add and disable people, and read the activity record |

Most examiners want **contribute**. Give **admin** to as few people as
possible — it can empty the store.

---

## Part 8 — Connecting an examiner's computer

For each person, once:

**1. Copy `server.crt` to their computer.** Only that file. A shared folder,
a USB stick, an email — it is not a secret. Put it somewhere permanent, like
`C:\hashstore\server.crt`.

**2. In their copy of the program**, open the **Hash Sets** tab. The first
section, *Connect to the hash store*, has three boxes:

- **Store** — `https://YOUR-COMPUTER-NAME:8787`
- **Token** — the one you issued them
- **Certificate** — the path to the `server.crt` you just copied, or
  **Browse…** to find it

**3. Tick "Remember the token on this machine" if your unit allows it.**
The address and the certificate are remembered anyway — neither is a secret.
The token is not, unless this is ticked: it is written to a file that
Windows account can read, and anyone who reads it can use the store as that
examiner. Left unticked, they paste it once each time the program starts.

Either way, **this is set up once, not once per case.** A new case uses the
store with no further work.

**4. Click "Use this store."** It should report the number of hashes and the
sets that are loaded. If it does, that machine is done — the token is
remembered for the session, and they use the store exactly as they used a
local one.

---

## Part 9 — Checking it actually works

From an examiner's machine, in order:

1. The Hash Sets tab shows the right number of hashes. *(If not, they are
   not reaching the server.)*
2. Run a small case and click **Check this run against the store**. It
   should report matches.
3. On the server's **Activity** page, that lookup should appear, against
   that person's name, from their address.

If all three hold, the system is working end to end.

---

## Part 10 — Making it start by itself

So that a restart does not take the store offline without anyone noticing.

Create a file called `start-hashstore.bat` in `C:\hashstore` containing one
line (all on one line):

```
python C:\hashstore\forensic_media_extractor\launch.py hashserver --store C:\hashstore\data\hashes.db --host 0.0.0.0 --port 8787 --cert C:\hashstore\data\server.crt --key C:\hashstore\data\server.key
```

Give the task a name you will recognize months from now — this lab uses
`Start_FME_Hash` — and write it down, because every instruction to stop or
start the server needs it and nothing else in the program knows it.

Then: **Task Scheduler** → *Create Task* → General: tick **Run whether user
is logged on or not** → Triggers: *New* → **At startup** → Actions: *New* →
Program: `C:\hashstore\start-hashstore.bat` → OK.

**Two things stop this working, and both look like the task silently not
running:**

**A mapped drive letter does not exist at boot.** `W:` is a drive *your
logged-in session* has; a task that runs before anyone logs in has no such
drive, so every path in the batch file is wrong and the server exits at
once. Use the full `\\server\share\...` path instead, or — better — keep
the store on a disk inside the machine. A drive letter that is a local disk
is fine.

**The task needs a starting folder.** In the action, set **Start in** to
`C:\hashstore`. Without it the task starts somewhere else and relative
paths in the batch file do not resolve.

To see what actually happened, have the batch file keep a log. Change the
line to end with:

```
 >> C:\hashstore\server-log.txt 2>&1
```

Then restart the machine and read that file. A task that "did not run"
usually did run, and said why.

Restart the computer once and check Part 9 again, to be sure it came back.

---

## Part 10a — Moving a store off a network share

If the store is on a mapped drive or a `\\server\share` path, it has to
move: a store needs file locking that shares do not provide, so it is slow,
it fails with "disk I/O error", and two machines writing to it can corrupt
it quietly. A scheduled task cannot see a mapped drive at boot either.

Do not copy the file in Explorer. SQLite keeps recent writes in a journal
beside the database, and a copy taken without it is **older than it looks**,
with no sign that anything is missing. Use:

```
python launch.py store --store W:\hashstore\data\hashes.db --move-to C:\hashstore\data\hashes.db
```

That copies through SQLite, counts both, and refuses to call it done unless
they agree. It leaves the original alone — delete it yourself once the copy
has served a case or two.

Then point the server at the new path, and move `server.crt`, `server.key`
and `hash_users.db` beside it.

**If the share is on another machine**, the question is which machine should
run the server: the one holding the data. Run it there, and examiners reach
it by its address.

## Part 10b — Moving an existing setup to a local disk

For a store that is already running from a mapped drive. Half an hour, and
nothing is deleted until you are satisfied.

**Before you start**, on the server, note which letters are mapped:

```
net use
```

Anything listed there is a share. Everything in the steps below moves to
`C:`, which is not.

---

**1. Stop the server.** Close the Command Prompt window it runs in, or end
the scheduled task in Task Scheduler. Nothing should be writing to the store
while it is copied.

**2. Make the folders:**

```
mkdir C:\hashstore
mkdir C:\hashstore\data
```

**3. Copy the program.** This is ordinary files, so Explorer is fine:

```
xcopy /E /I W:\Hashstore\forensic_media_extractor C:\hashstore\forensic_media_extractor
```

**4. Move the store — not with Explorer.** The store is a database with
recent writes in a journal beside it; a file copy is older than it looks and
nothing says so:

```
cd C:\hashstore\forensic_media_extractor
python launch.py store --store W:\Hashstore\data\hashes.db --move-to C:\hashstore\data\hashes.db
```

It prints how many hashes arrived and whether that matches the original.
**If the counts disagree, stop** — something was still writing. Start again
with the server definitely stopped.

**5. Copy the other three files.** With the server stopped these are
ordinary files. Copy `hash_users.db` **and anything beside it** whose name
starts the same way — a `-wal` or `-shm` file holds recent changes:

```
copy W:\Hashstore\data\hash_users.db* C:\hashstore\data\
copy W:\Hashstore\data\server.crt C:\hashstore\data\
copy W:\Hashstore\data\server.key C:\hashstore\data\
```

**6. Check the copy before trusting it:**

```
python launch.py store --store C:\hashstore\data\hashes.db --verify
python launch.py hashserver --store C:\hashstore\data\hashes.db --accounts C:\hashstore\data\hash_users.db --list-users
```

The first should report the hash count you expect. The second should list
the people you have issued tokens to. **If either is empty or wrong, stop**
— the old copy is still there, untouched.

**7. Check the server comes up from the new paths:**

```
python launch.py hashserver --store C:\hashstore\data\hashes.db --accounts C:\hashstore\data\hash_users.db --cert C:\hashstore\data\server.crt --key C:\hashstore\data\server.key --port 8787 --check
```

**8. Rewrite the batch file.** `C:\hashstore\start-hashstore.bat`, one
line, with the logging on the end so a failure is never silent:

```
python C:\hashstore\forensic_media_extractor\launch.py hashserver --store C:\hashstore\data\hashes.db --accounts C:\hashstore\data\hash_users.db --host 0.0.0.0 --port 8787 --cert C:\hashstore\data\server.crt --key C:\hashstore\data\server.key >> C:\hashstore\server-log.txt 2>&1
```

**9. Fix the scheduled task.** Task Scheduler → your task → Properties →
Actions → Edit:

- **Program**: `C:\hashstore\start-hashstore.bat`
- **Start in**: `C:\hashstore`  ← this was probably blank, and it matters

General tab: **Run whether user is logged on or not**.

**10. Restart the server machine.** Then read
`C:\hashstore\server-log.txt` — it will say either that the server started,
or exactly why it did not.

**11. From an examiner's machine**, check nothing changed for them: the
Hash Sets tab still points at `https://LABSERVER2025:8787`, the same token,
the same `server.crt`. Press **Verify**. The address is what they use, not a
drive letter, so the move is invisible to them.

**12. Leave `W:\Hashstore` alone for a week or two.** Once a few cases have
run against the new store, delete it.

**One thing that does not move:** your evidence and case output stay on the
RAID. It is the right place for them — a case folder is read and written by
one examiner at a time, which a share handles. The hash store is different
because several machines write to it at once, and that is what shares cannot
do.

## Part 10c — After updating the program

**The server does not update itself by you replacing the folder.** It keeps
running the code it started with, so a server left up for weeks is running
whatever was current then — and an examiner meets faults that were fixed
long ago with no way to tell that is what happened.

After replacing the program folder on the server, restart the task. **Look
its name up rather than assuming it** — a task made months ago is rarely
called what a guide guessed:

```
schtasks /query /fo table | findstr /i "hash fme"
```

Then, with the name that comes back (in this lab it is
`Start_FME_Hash`):

```
schtasks /end /tn "Start_FME_Hash"
schtasks /run /tn "Start_FME_Hash"
```

`schtasks /end` stops only a server the task started. One started by hand
in a Command Prompt is stopped with Ctrl-C in its window, or:

```
netstat -ano | findstr :8787
taskkill /pid <the number that line ends in> /f
```

Then, from an examiner's machine, press **Verify** on the Hash Sets tab. It
shows the server's version and says plainly when it is older than the
program talking to it.

## Part 11 — Looking after it

### Backing it up

Copy `C:\hashstore\data\hashes.db` somewhere else on a schedule your unit is
comfortable with. **Stop the server first**, or copy the `hashes.db-wal` file
alongside it if one exists — a store copied while it is being written to,
without its write-ahead log, is a store missing whatever was in flight.

Keep the **imported sets** too — the Project VIC and NSRL files. They are
what makes the store rebuildable, and a backup of the store without them is
only half a backup.

Safer than a file copy, because it checks the result:

```
python -m fme store --store C:\hashstore\data\hashes.db ^
       --move-to E:\backups\hashes-2026-11-01.db
```

### Checking it for damage

A store is one large SQLite file that several people write to over a
network, on a machine that can lose power mid-import. When it goes wrong
**the symptom is not an error**: lookups quietly stop matching things they
used to match, which inside a case looks like the file simply not being in
the set.

```
python -m fme store --store C:\hashstore\data\hashes.db --integrity
```

Read-only; it changes nothing. Run it **monthly, and after any power loss
or interrupted import**.

A clean result says the file is structurally sound. It does **not** say the
hashes in it are the right hashes — that is what the sets they came from
say, and re-importing a set is how a doubt about one is settled.

### When it is damaged

The check prints these steps; they are here so they can be read before
anything goes wrong.

1. **Stop the server.** Every further write makes it worse.
2. **Copy `hashes.db`, `hashes.db-wal` and `hashes.db-shm` somewhere safe**
   before touching anything. They are the evidence of what went wrong, and
   somebody may need to say what happened.
3. **Restore the most recent good copy** and check it:
   `--integrity` on the restored file before putting it in service.
4. **Re-import the sets** that were added after that copy was taken. This
   is why the set files are kept: a reference store is rebuildable.
5. **Re-contribute the cases** added since. Those cases are still on disk —
   open each and press *Add this case*. The store's activity record shows
   which cases contributed and when, so you know which ones to redo.
6. **Tell the other examiners** the store was rebuilt and from when. A
   lookup that found nothing during the damaged period found nothing
   because of the damage, not because the file was unknown.

**Nothing repairs the store in place, deliberately.** A salvaged database is
one whose contents nobody can account for, and this store decides what an
examiner is shown about a file in a criminal case. Rebuilding from the sets
is slower and the result is something you can stand behind.

**When someone leaves**: People → **disable** next to their name. Their
token stops working immediately; their history stays in the activity record,
which is the point.

**When a token is exposed**: add that person again with the same name. A new
token is issued and the old one stops working.

**The certificate expires** after about two years. Run the `--make-cert`
command again, restart the server, and copy the new `server.crt` to everyone.
Put a reminder in your calendar now.

**Keep an eye on the Overview page.** If someone's lookups are zero, they are
probably not actually connected to the store and do not know it.

---

## When something goes wrong

| What you see | What it means |
| --- | --- |
| **"database is locked"** (rather than the message below) | the server is running an old copy of the program. It was started before this was fixed and has been running since. Stop it, start it again from the updated folder, and press **Verify** — it shows the server's version. |
| **"The store is busy writing something else"** | an import is running, and SQLite allows one writer at a time. Nothing is lost: let the import finish and add the case again. |
| **Everything is slow after an import that was stopped part way** | the import's write-ahead log was never folded back into the store, so every read walks it. `python launch.py store --store <path> --checkpoint` folds it in. It reads the whole log, so on a large store it takes a long while — leave it alone until it finishes. |
| **"disk I/O error"** | the store is on a network share, or something else has it open. Move it to a disk in the server machine and reach it over the network with this server instead. |
| **The file is called `hashes.db`, not what I expected** | the name is whatever you pass to `--store`; `hashes.db` is only the default when you pass nothing. Use one name everywhere and the commands in this guide will match. |
| **"Could not reach the hash store"** | the server is not running, the address is wrong, or the firewall is blocking the port. Check the Command Prompt window on the server first. |
| **"its certificate is not trusted"** | that machine does not have `server.crt`, or is reaching the server by a name the certificate does not list. Check Part 4 and Part 8. |
| **"The hash store did not accept this token"** | the token is mistyped, was reissued, or the person was disabled. |
| **"has the read role, which cannot contribute"** | that is correct behavior. Change their role in People if they should be able to. |
| **Examiner sees fewer hashes than you do** | they are probably still pointed at a local store file rather than the server. Check the Store file box starts with `https://`. |
| **`No module named fme`** | the Command Prompt is in the wrong folder, or the full path to `launch.py` was not given. |

---

## What not to do

- **Do not put the server on the internet.** It belongs on your lab network.
  It has no protection against someone guessing at it repeatedly.
- **Do not share one token between people.** The record of who did what is
  the main safeguard here, and a shared token erases it.
- **Do not copy `server.key` anywhere.** Only `server.crt` travels.
- **Do not run the server without `--cert` and `--key`** on a real network.
  It will warn you. Tokens and the hashes being looked up would cross the
  network readable by anyone watching.
- **Do not keep the only copy of the store on this one machine.** It is a
  database on a computer, and computers fail.
