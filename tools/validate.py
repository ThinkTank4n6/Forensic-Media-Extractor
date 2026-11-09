"""
validate.py -- run the validation protocol and write down what happened.

    python tools/validate.py --examiner "C. Tankelewicz" --agency "..."

This runs the protocol in docs/VALIDATION.md on this machine and writes a
record: the tool version, the environment, every test that ran, what it
asserted, and what it returned. The record is the point. A tool that
passes its tests and cannot show that it did is in the same position as
one that never ran them.

Three rules this file keeps, because a validation record that overstates
is worse than none:

- A check that could not run is recorded as **not run**, never as passed.
  Where a library is missing, the record says which capability was
  therefore untested.
- Nothing is summarised as "validated". The record reports what was
  asserted and what came back; what that is worth is for the reader.
- The record names what this program is known not to do, from
  docs/VALIDATION.md, so the limitations travel with the results rather
  than living in a document nobody opens.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

# The tests that make up the protocol, and what each one is evidence of.
PROTOCOL = [
    ("test_known_answer.py",
     "Files whose hashes were recorded before deletion are returned "
     "byte-for-byte, on a volume built for the purpose, by both of this "
     "program's readers independently."),
    ("test_filesystems.py",
     "Each supported file system is read, and a deleted FAT entry is "
     "recovered whole rather than truncated to one cluster."),
    ("test_report.py",
     "A report states its time zone, orders findings by the scheme, and "
     "holds no image when built without them."),
    ("test_protocol.py",
     "The Word report carries the sections a hand-over needs."),
    ("test_report_contents.py",
     "The HTML, Word and PDF reports are counted once and agree."),
    ("test_recover.py",
     "A case whose run was interrupted can be rebuilt from the rows it "
     "had written, and says it is partial."),
    ("test_resume.py",
     "A resumed run finds the same files as one that was never "
     "interrupted."),
    ("test_hashstore.py",
     "Hash sets import and match, and the empty-file hash is excluded."),
    ("test_endpoints.py",
     "Every endpoint the interface calls answers."),
    ("test_metadata.py",
     "Dates, camera and location are read as recorded."),
    ("test_false_positives.py",
     "Matching does not pair unrelated pictures."),
]

CAPABILITIES = [
    ("pytsk3", "NTFS, exFAT, HFS+, APFS and ext2/3/4"),
    ("pyewf", "E01 and Ex01 through libewf"),
    ("PIL", "thumbnails, visual similarity and report pictures"),
    ("cv2", "video key frames, edited-copy matching and faces"),
    ("numpy", "the signature scan and the matchers"),
    ("pillow_heif", "HEIC as written by an iPhone"),
    ("cryptography", "C2PA signature checking"),
]


def have(module: str) -> bool:
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def tool_version() -> str:
    for name in ("VERSION", os.path.join("fme", "VERSION")):
        path = os.path.join(PROJECT, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
    return "unknown"


def fingerprint() -> str:
    """One hash over the code that ran, so a record names its own build."""
    # The program's own, not a copy of it: two copies can disagree.
    sys.path.insert(0, PROJECT)
    from fme.cli import fingerprint_of

    return fingerprint_of(os.path.join(PROJECT, "fme"))


def run_one(name: str) -> dict:
    path = os.path.join(PROJECT, "tests", name)
    if not os.path.isfile(path):
        return {"state": "missing", "detail": "the test is not in this copy"}
    started = _dt.datetime.now(_dt.timezone.utc)
    finished = subprocess.run([sys.executable, path], capture_output=True,
                              text=True, cwd=PROJECT, timeout=1800)
    output = (finished.stdout or "") + (finished.stderr or "")
    # A test that skipped is not a test that passed, and the difference
    # is the whole value of this record.
    skipped = [line.strip() for line in output.splitlines()
               if line.strip().startswith(("not checked:", "(the", "(no"))
               or "skipping" in line.lower()]
    # The checks that failed, by the two forms the tests print them in. Only
    # the not-checked lines were shown, so a failed run printed FAILED over
    # three lines about what was skipped, and the examiner had to open the
    # record to learn what had gone wrong.
    failing = [line.strip() for line in output.splitlines()
               if "[ FAILED ]" in line or line.rstrip().endswith("-- WRONG")]
    return {
        "failing": failing,
        "state": "passed" if finished.returncode == 0 else "failed",
        "seconds": round(
            (_dt.datetime.now(_dt.timezone.utc) - started).total_seconds(), 1),
        "not_checked": skipped,
        "tail": "\n".join(output.strip().splitlines()[-12:]),
    }


def records_folder() -> str:
    """Where validation records live: outside the program folder.

    They were written into `docs/` inside the program, which is the one
    place guaranteed to be destroyed -- an examiner updates by replacing
    that folder, and every record of what was checked and when goes with
    it. A record that does not survive an update is worse than none,
    because its whole value is that it persists.

    So they go beside the examiner's own data instead: somewhere the
    program never writes to except for this, and an update never
    touches.
    """
    override = os.environ.get("FME_VALIDATION_DIR")
    if override:
        return override
    if os.name == "nt":
        base = (os.environ.get("LOCALAPPDATA")
                or os.path.expanduser("~\\AppData\\Local"))
        return os.path.join(base, "ForensicMediaExtractor", "validation")
    base = (os.environ.get("XDG_DATA_HOME")
            or os.path.join(os.path.expanduser("~"), ".local", "share"))
    return os.path.join(base, "forensic-media-extractor", "validation")


def rescue_old_records(into: str) -> int:
    """Move records written into the program folder by older versions.

    Up to v221 these were written inside `docs/`. Anyone updating by
    replacing that folder lost them. Where an old copy is still there,
    it is moved rather than left to be deleted by the next update.
    """
    moved = 0
    for old in (os.path.join(PROJECT, "docs", "validation"),):
        if not os.path.isdir(old):
            continue
        for name in sorted(os.listdir(old)):
            source = os.path.join(old, name)
            target = os.path.join(into, name)
            if os.path.isfile(source) and not os.path.exists(target):
                os.makedirs(into, exist_ok=True)
                shutil.move(source, target)
                moved += 1
    old_log = os.path.join(PROJECT, "docs", "VALIDATION_LOG.md")
    new_log = os.path.join(into, "VALIDATION_LOG.md")
    if os.path.isfile(old_log) and not os.path.isfile(new_log):
        os.makedirs(into, exist_ok=True)
        shutil.move(old_log, new_log)
        moved += 1
    return moved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examiner", default="")
    parser.add_argument("--agency", default="")
    parser.add_argument("--note", default="",
                        help="why this run was made, in your words")
    parser.add_argument("--out", default="",
                        help="where to write this run's record. The "
                             "default is beside the kept records, "
                             "outside the program folder, so an update "
                             "cannot delete it")
    args = parser.parse_args()
    if not args.out:
        args.out = os.path.join(records_folder(), "validation-record.md")

    when = _dt.datetime.now(_dt.timezone.utc)
    print(f"Forensic Media Extractor v{tool_version()} -- validation run")
    print(f"{when.isoformat(timespec='seconds')}\n")

    absent = [(name, what) for name, what in CAPABILITIES if not have(name)]
    for name, what in CAPABILITIES:
        print(f"  {'yes' if have(name) else 'NO '}  {name:14} {what}")
    print()

    results = []
    for name, claim in PROTOCOL:
        print(f"  running {name} …", flush=True)
        outcome = run_one(name)
        outcome.update({"test": name, "asserts": claim})
        results.append(outcome)
        mark = {"passed": "passed", "failed": "FAILED",
                "missing": "not in this copy"}[outcome["state"]]
        print(f"    {mark}" + (f" ({outcome['seconds']}s)"
                               if "seconds" in outcome else ""))
        for line in outcome.get("failing", [])[:5]:
            print(f"      failed: {line[:100]}")
        if len(outcome.get("failing", [])) > 5:
            print(f"      … and {len(outcome['failing']) - 5} more; the record has them")
        for line in outcome.get("not_checked", [])[:3]:
            print(f"      not checked: {line[:96]}")

    passed = sum(1 for r in results if r["state"] == "passed")
    failed = [r for r in results if r["state"] == "failed"]
    unchecked = [line for r in results for line in r.get("not_checked", [])]

    record = [
        "# Validation record",
        "",
        "This records what was run and what it returned. It is not a",
        "statement that the tool is correct; it is evidence of what was",
        "tested, on what, and when. Read it beside `docs/VALIDATION.md`,",
        "which sets out what this program is known not to do.",
        "",
        "| | |",
        "| --- | --- |",
        f"| Tool | Forensic Media Extractor v{tool_version()} |",
        f"| Code fingerprint | `{fingerprint()[:32]}` |",
        f"| Run at | {when.isoformat(timespec='seconds')} |",
        f"| Examiner | {args.examiner or '(not given)'} |",
        f"| Agency | {args.agency or '(not given)'} |",
        f"| Machine | {platform.platform()} |",
        f"| Python | {platform.python_version()} |",
        f"| Reason for this run | {args.note or '(not given)'} |",
        "",
        "## What this machine could test",
        "",
        "| library | present | what it enables |",
        "| --- | --- | --- |",
    ]
    for name, what in CAPABILITIES:
        record.append(f"| {name} | {'yes' if have(name) else '**no**'} "
                      f"| {what} |")
    # An honest record has to say what its cross-check is worth. TSK is
    # this program's own reader for NTFS and the rest, so comparing
    # against icat there is one library answering twice.
    if have("pytsk3"):
        record += [
            "",
            "**On independence.** This machine reads NTFS, exFAT, APFS, "
            "HFS+ and ext through `pytsk3`, which is The Sleuth Kit. A "
            "comparison against TSK on those file systems is the same "
            "library answering twice and is not independent evidence. "
            "The independent comparison in these results is between this "
            "program's two readers, which share no code.",
        ]

    if absent:
        record += [
            "",
            "**Not installed on this machine, and therefore untested in "
            "this run:**",
            "",
        ] + [f"- {what} (`{name}`)" for name, what in absent]

    record += ["", "## What was run", "",
               "| test | result | what it asserts |", "| --- | --- | --- |"]
    for row in results:
        state = {"passed": "passed", "failed": "**FAILED**",
                 "missing": "not in this copy"}[row["state"]]
        record.append(f"| `{row['test']}` | {state} | {row['asserts']} |")

    if unchecked:
        record += ["", "## Recorded as not checked", "",
                   "Each of these is something a test declined to assert, "
                   "usually because a library or a fixture was absent. "
                   "They are listed because a check that did not run is "
                   "not a check that passed.", ""]
        record += [f"- {line}" for line in unchecked]

    if failed:
        record += ["", "## Failures", ""]
        for row in failed:
            record += [f"### {row['test']}", ""]
            if row.get("failing"):
                record += [f"- {line}" for line in row["failing"]] + [""]
            record += ["```", row["tail"], "```", ""]

    record += [
        "",
        "## Summary",
        "",
        f"- {passed} of {len(results)} tests passed.",
        f"- {len(failed)} failed.",
        f"- {len(unchecked)} check(s) did not run and are listed above.",
        "",
        "A reader deciding what this is worth should begin with the list "
        "of what did not run, and with `docs/VALIDATION.md`.",
        "",
    ]

    # The folder may not exist yet on a machine that has never run this.
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(record))

    # And a line in the log, which is never overwritten.
    #
    # A record that replaces the last one answers "does it work now" and
    # nothing else. What a validation history is for is the other
    # question -- what was true on the day of the case -- so the log is
    # appended to, each run keeping its own dated record beside it.
    log_dir = records_folder()
    os.makedirs(log_dir, exist_ok=True)
    rescued = rescue_old_records(log_dir)
    if rescued:
        print(f"  moved {rescued} record(s) out of the program folder, "
              f"where an update would have deleted them")
    stamp = when.strftime("%Y-%m-%dT%H%M%SZ")
    kept = os.path.join(log_dir, f"{stamp}-v{tool_version()}.md")
    with open(kept, "w", encoding="utf-8") as fh:
        fh.write("\n".join(record))

    log = os.path.join(log_dir, "VALIDATION_LOG.md")
    if not os.path.isfile(log):
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(
                "# Validation log\n\n"
                "Every validation run on every machine, oldest first. Each\n"
                "row links the record written that day: what was tested,\n"
                "on what, by whom, and which checks did not run.\n\n"
                "Rows are appended and never edited. A run that failed\n"
                "stays here: a log that only records successes is not a\n"
                "log.\n\n"
                "| date (UTC) | version | examiner | machine | passed | "
                "failed | not run | record |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n")
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"| {when.strftime('%Y-%m-%d %H:%M')} "
                 f"| v{tool_version()} "
                 f"| {args.examiner or '(not given)'} "
                 f"| {platform.system()} {platform.release().split('-')[0]} "
                 f"| {passed} | {len(failed)} | {len(unchecked)} "
                 f"| [{stamp}]({os.path.basename(kept)}) |\n")

    print(f"\n{passed} of {len(results)} passed, {len(failed)} failed, "
          f"{len(unchecked)} check(s) did not run")
    print(f"record written to {args.out}")
    print(f"kept at        {kept}")
    print(f"logged in      {log}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
