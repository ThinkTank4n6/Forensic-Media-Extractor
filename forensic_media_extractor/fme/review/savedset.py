"""
savedset.py -- the examiner's own categories and flags, carried between cases.

The locked categories are the same in every case by design: 1 means Child
Abuse Material here and in the next case and in the one after. What is *not*
the same is everything an examiner adds for their own work -- a flag for a
series, a category for a recurring kind of material, a renamed label their
unit uses. Without somewhere to keep those, they are re-created by hand at
the start of every case, and a flag spelled slightly differently in two cases
cannot be compared across them.

This stores that set once, beside the program, and applies it to new cases.

    python -m fme scheme --save            # from the current case
    python -m fme scheme --show
    python -m fme scheme --apply <case folder>

What it stores is the *examiner's additions*: custom categories and custom
flags. The locked categories are left alone, because they mean the same
thing in every case. It never stores a decision, a file or a hash -- applying a set to a case changes the labels
available, not what anyone concluded with them.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys

if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

from fme.review import casedb

__version__ = "1.0.0"

DEFAULT_NAME = "scheme.json"


def default_path() -> str:
    """Beside the program, like the hash store and the case list."""
    return os.path.join(os.getcwd(), DEFAULT_NAME)


def capture(case_dir: str) -> dict:
    """Read what an examiner added to a case: their categories and flags.

    The locked categories are deliberately not captured. They are the same
    in every case -- that is what makes a category 1 in one case mean what
    it means in another -- and the case database refuses to rename them, so
    carrying them would be carrying something that can never be applied.
    """
    case = casedb.CaseDB(os.path.join(case_dir, "case.db"))
    try:
        categories = [{"code": row["code"], "name": row["name"],
                       "color": row["color"]}
                      for row in case.categories()
                      if not row["locked"]]
        flags = [{"name": f["name"], "color": f["color"],
                  "key": f.get("key") or ""} for f in case.flags()]
        return {"saved_at": _dt.datetime.now().replace(
                    microsecond=0).isoformat(),
                "categories": categories, "flags": flags}
    finally:
        case.close()


def save(case_dir: str, path: str = "") -> dict:
    """Store the scheme of a case as the one to use from now on."""
    scheme = capture(case_dir)
    path = path or default_path()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(scheme, fh, indent=2)
    scheme["path"] = os.path.abspath(path)
    return scheme


def load(path: str = "") -> dict:
    path = path or default_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            scheme = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return scheme if isinstance(scheme, dict) else {}


def apply(case_dir: str, path: str = "", scheme: dict = None) -> dict:
    """Add a saved scheme's categories and flags to a case.

    Additive and idempotent: anything already there by name is left alone,
    so applying a set twice does not duplicate it, and applying it to a case
    an examiner has already customized does not undo their work. Nothing is
    ever removed -- a category that holds decisions must not vanish because
    a saved set does not mention it.
    """
    scheme = scheme if scheme is not None else load(path)
    if not scheme:
        return {"applied": False, "reason": "no saved scheme"}

    case = casedb.CaseDB(os.path.join(case_dir, "case.db"))
    added_categories = added_flags = renamed = 0
    renumbered: list = []
    try:
        existing = {c["name"].lower(): c for c in case.categories()}
        for entry in scheme.get("categories", []):
            if entry["name"].lower() in existing:
                continue
            # Under the code it was saved with where that is free here: by
            # name alone it took the next free number, so "Series X" was 10
            # in one case and 11 in the next, and exports by code disagreed.
            wanted = entry.get("code")
            given = case.add_category(
                entry["name"], entry.get("color") or "#6b7280",
                code=wanted if isinstance(wanted, int) else None)
            if isinstance(wanted, int) and given != wanted:
                renumbered.append({"name": entry["name"], "saved_as": wanted,
                                   "here": given})
            added_categories += 1

        have = {f["name"].lower() for f in case.flags()}
        for entry in scheme.get("flags", []):
            if entry["name"].lower() in have:
                continue
            case.add_flag(entry["name"], entry.get("color") or "#6b7280",
                          entry.get("key") or "")
            added_flags += 1
    finally:
        case.close()

    return {"applied": True, "categories_added": added_categories,
            "flags_added": added_flags, "categories_renamed": renamed,
            # Said, not silent: a code this case had already used.
            "categories_renumbered": renumbered}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="The examiner's saved categories and flags.")
    ap.add_argument("--path", default="", help="where the scheme is kept")
    ap.add_argument("--save", default="",
                    help="save the scheme of this case folder")
    ap.add_argument("--apply", default="",
                    help="apply the saved scheme to this case folder")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.save:
            scheme = save(args.save, args.path)
            print(f"Saved to {scheme['path']}")
        if args.apply:
            result = apply(args.apply, args.path)
            if not result["applied"]:
                print(result["reason"], file=sys.stderr)
                return 2
            print(f"Added {result['categories_added']} category(ies), "
                  f"{result['flags_added']} flag(s), renamed "
                  f"{result['categories_renamed']}")
        if args.show or not (args.save or args.apply):
            scheme = load(args.path)
            if not scheme:
                print("No scheme saved yet.")
                return 0
            print(f"Saved {scheme.get('saved_at', '')}\n")
            for entry in scheme.get("categories", []):
                print(f"  category  {entry['code']:>3}  {entry['name']}")
            for entry in scheme.get("flags", []):
                key = f" [{entry['key']}]" if entry.get("key") else ""
                print(f"  flag           {entry['name']}{key}")
    except (OSError, ValueError) as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
