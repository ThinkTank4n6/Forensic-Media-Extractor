"""
triage_report.py -- summarize a run's manifest so results can be compared
against another tool, and false positives spotted.

Counts alone ("we found 43K images, the other tool found 73K") do not say
where a difference comes from. This breaks a manifest down the ways that
actually distinguish the possibilities:

  * by engine        file system vs carved vs database vs package
  * by signature     which detector produced each result
  * by size band     false positives cluster in the large bands
  * duplicates       how many identical files were collapsed, which alone
                     can account for a large gap against a tool that does
                     not de-duplicate
  * extension-only   file system entries classified by name, not content

    python3 triage_report.py -m out/manifest.json
    python3 triage_report.py -m out/manifest.json --videos --top 40
    python3 triage_report.py -m out/manifest.json --csv breakdown.csv
"""

from __future__ import annotations

# Allow this file to be run directly as well as imported. Run as a script
# there is no package context, so the project root goes on the path first.
if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

import argparse
import collections
import csv
import json
import os
import sys

BANDS = [
    (0, 4 * 1024, "under 4 KB"),
    (4 * 1024, 64 * 1024, "4 KB - 64 KB"),
    (64 * 1024, 1024 * 1024, "64 KB - 1 MB"),
    (1024 * 1024, 16 * 1024 * 1024, "1 MB - 16 MB"),
    (16 * 1024 * 1024, 256 * 1024 * 1024, "16 MB - 256 MB"),
    (256 * 1024 * 1024, 1 << 62, "over 256 MB"),
]


def human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TiB"


def band_of(size: int) -> str:
    for low, high, label in BANDS:
        if low <= size < high:
            return label
    return "unknown"


def load(path: str) -> tuple:
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    return doc.get("files", []), doc.get("summary", {}), doc.get("duplicates", [])


def report(manifest_path: str, category: str = "", top: int = 20,
           csv_path: str = "") -> dict:
    files, summary, duplicates = load(manifest_path)
    if category:
        duplicates = [f for f in duplicates if f.get("category") == category]
        files = [f for f in files if f.get("category") == category]

    by_engine = collections.Counter(f.get("engine", "?") for f in files)
    by_category = collections.Counter(f.get("category", "?") for f in files)
    by_ext = collections.Counter(f.get("ext", "?") for f in files)
    by_sig = collections.Counter(f.get("signature", "?") for f in files)
    bytes_by_sig = collections.Counter()
    for f in files:
        bytes_by_sig[f.get("signature", "?")] += f.get("size", 0)
    by_band = collections.Counter(band_of(f.get("size", 0)) for f in files)
    ext_only = [f for f in files
                if "extension only" in (f.get("signature") or "")]
    deleted = sum(1 for f in files if f.get("deleted"))
    total_bytes = sum(f.get("size", 0) for f in files)

    print(f"Manifest : {os.path.abspath(manifest_path)}")
    print(f"Source   : {summary.get('image', '?')} "
          f"({summary.get('image_size_human', '?')})")
    if category:
        print(f"Filtered : category = {category}")
    print(f"Files    : {len(files):,}   totaling {human(total_bytes)}")
    print(f"Deleted  : {deleted:,}")
    if duplicates:
        print(f"Copies   : {len(duplicates):,} further location(s) of those same "
              f"files, listed in duplicates.csv")
        print(f"           total findings = {len(files) + len(duplicates):,} "
              f"(this is the figure to compare against a tool that lists every "
              f"copy)")
        most = max(files, key=lambda f: f.get("instances", 1), default=None)
        if most and most.get("instances", 1) > 1:
            print(f"           most repeated: {most.get('instances')} copies of "
                  f"{most.get('out_path', '')}")
    print()

    print("By engine")
    for name, count in by_engine.most_common():
        print(f"  {name:14} {count:>8,}")
    print()

    print("By category")
    for name, count in by_category.most_common():
        print(f"  {name:14} {count:>8,}")
    print()

    print(f"By extension (top {top})")
    for name, count in by_ext.most_common(top):
        print(f"  .{name:<13} {count:>8,}")
    print()

    print(f"By signature (top {top}) -- the detector that produced each result")
    print(f"  {'signature':24} {'files':>8}  {'bytes':>10}  {'mean size':>10}")
    for name, count in by_sig.most_common(top):
        total = bytes_by_sig[name]
        print(f"  {name:24} {count:>8,}  {human(total):>10}  "
              f"{human(total / count):>10}")
    print()

    print("By size band")
    for _, _, label in BANDS:
        count = by_band.get(label, 0)
        if count:
            print(f"  {label:16} {count:>8,}")
    print()

    if ext_only:
        print(f"Classified by file name only, header did not match: "
              f"{len(ext_only):,}")
        for f in ext_only[:5]:
            print(f"  {f.get('size', 0):>12,}  {f.get('source_path', '')[:70]}")
        print()

    print("From the run summary")
    for key, label in (("duplicates_suppressed", "duplicates collapsed (SHA-256)"),
                       ("blank_dropped", "blank files dropped"),
                       ("write_failed", "NOT recovered: output write failed"),
                       ("read_failed", "NOT recovered: evidence read failed"),
                       ("skipped_large", "oversized entries skipped"),
                       ("candidates", "carve candidates considered"),
                       ("errors", "read errors")):
        if key in summary:
            print(f"  {label:34} {summary[key]:>10,}")
    print()

    largest = sorted(files, key=lambda f: f.get("size", 0), reverse=True)[:top]
    print(f"Largest {len(largest)} files -- check these first when a count "
          f"looks too high")
    print(f"  {'size':>12}  {'ext':<6} {'engine':<11} {'signature':<22} source")
    for f in largest:
        print(f"  {f.get('size', 0):>12,}  {f.get('ext', ''):<6} "
              f"{f.get('engine', ''):<11} {(f.get('signature') or '')[:22]:<22} "
              f"{(f.get('source_path') or '')[:48]}")

    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["signature", "engine", "category", "files",
                             "total_bytes", "mean_bytes"])
            grouped = collections.defaultdict(lambda: [0, 0])
            for f in files:
                key = (f.get("signature", "?"), f.get("engine", "?"),
                       f.get("category", "?"))
                grouped[key][0] += 1
                grouped[key][1] += f.get("size", 0)
            for (sig, eng, cat), (count, total) in sorted(
                    grouped.items(), key=lambda kv: -kv[1][0]):
                writer.writerow([sig, eng, cat, count, total, total // count])
        print(f"\nWrote {os.path.abspath(csv_path)}")

    return {"files": len(files), "by_engine": dict(by_engine),
            "by_signature": dict(by_sig), "extension_only": len(ext_only)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Summarize a media_core manifest for triage and for "
                    "comparing against another tool's results.")
    ap.add_argument("-m", "--manifest", required=True)
    ap.add_argument("--images", action="store_true", help="images only")
    ap.add_argument("--videos", action="store_true", help="videos only")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--csv", default="", help="write a per-signature breakdown")
    args = ap.parse_args(argv)

    category = "image" if args.images else "video" if args.videos else ""
    try:
        report(args.manifest, category, args.top, args.csv)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Could not read the manifest: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
