"""A database opened read-only opens the file it was given, whatever its name.

Twenty places built the URI as "file:" + path. "?" starts a URI's
parameters, "#" its fragment and "%" an escape, so a case folder named
"Case #12" or "50% done" opened the wrong file or failed (v2026.11.312).
"""
import ast
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

from fme.sqlite_uri import ro_uri  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_sqlite_uri-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

print("a database in an awkwardly named folder")
for name in ("Case #12", "50% done", "what?", "a b", "x&mode=rwc", "ünïcode"):
    folder = os.path.join(ROOT, name)
    os.makedirs(folder)
    path = os.path.join(folder, "case.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE marker (name TEXT)")
    con.execute("INSERT INTO marker VALUES (?)", (name,))
    con.commit()
    con.close()
    con = sqlite3.connect(ro_uri(path), uri=True)
    got = con.execute("SELECT name FROM marker").fetchone()[0]
    try:
        con.execute("INSERT INTO marker VALUES ('x')")
        read_only = False
    except sqlite3.OperationalError:
        read_only = True
    con.close()
    good = got == name and read_only
    ok &= good
    print(f"  {name!r}: opened the right file, read-only -- {'ok' if good else 'WRONG'}")

print("\nnothing builds the URI by hand any more")
offenders = []
for folder, _dirs, files in os.walk(os.path.join(PROJECT, "fme")):
    if os.sep + "vendor" in folder:
        continue
    for name in files:
        if not name.endswith(".py"):
            continue
        path = os.path.join(folder, name)
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr) and node.values and \
                    isinstance(node.values[0], ast.Constant) and \
                    str(node.values[0].value).startswith("file:"):
                offenders.append(f"{os.path.relpath(path, PROJECT)}:{node.lineno}")
            # "file:" + path is the same mistake spelled another way: the
            # file index built its URI so until v2026.11.318.
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add) and \
                    isinstance(node.left, ast.Constant) and \
                    str(node.left.value).startswith("file:") and \
                    name != "sqlite_uri.py":         # the one that does it right
                offenders.append(f"{os.path.relpath(path, PROJECT)}:{node.lineno}")
ok &= not offenders
print(f"  no \"file:\" + ... or f\"file:...\" left outside vendor/: {not offenders} {offenders or ''}")

shutil.rmtree(ROOT, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
