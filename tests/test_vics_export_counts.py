"""The Project VIC export counts files, not places, and its totals agree with
its own records.

One picture found in a camera folder, a copy and unallocated space was
written as three Media records with one MD5, so TotalMediaFiles and the
export's image count were the number of places, not of files -- a different
number from the case's own report. VICS models that file as one Media with
three MediaFiles. And TotalPrecategorized was written as 0 whatever the
records said, under records marked IsPrecategorized once examiner
categories were exported.
"""
import contextlib
import hashlib
import io
import json
import logging
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)
with contextlib.redirect_stdout(io.StringIO()):
    import make_media_test_image as fixtures  # noqa: E402
from fme.exports import projectvic_export, vic_import  # noqa: E402
from fme.review import casedb  # noqa: E402

ok = True
ROOT = os.path.join(tempfile.gettempdir(), "fme_vics_counts-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
CASE = os.path.join(ROOT, "case")
os.makedirs(os.path.join(CASE, "images", "jpg"))


def check(label, good, detail=""):
    global ok
    ok &= bool(good)
    print(f"{label}{(': ' + detail) if detail else ''} -- {'ok' if good else 'WRONG'}")


def recovered(name, data, **fields):
    rel = f"images/jpg/{name}"
    with open(os.path.join(CASE, rel), "wb") as fh:
        fh.write(data)
    return {"out_path": rel, "md5": hashlib.md5(data).hexdigest(),
            "sha1": hashlib.sha1(data).hexdigest(),
            "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
            "category": "image", "ext": "jpg", "mtime": "", "crtime": "",
            "atime": "", **fields}


one, two = fixtures.make_jpeg(3), fixtures.make_jpeg(7)
kept = recovered("recovered_0001_a.jpg", one, source_path="/DCIM/IMG_0001.jpg")
other = recovered("recovered_0002_b.jpg", two, source_path="/DCIM/IMG_0002.jpg")
duplicates = [dict(kept, source_path="/Pictures/copy.jpg", deleted=True),
              dict(kept, source_path="", offset=4096, engine="carve")]
manifest = os.path.join(CASE, "manifest.json")
with open(manifest, "w") as fh:
    json.dump({"summary": {}, "files": [kept, other], "duplicates": duplicates}, fh)
case = casedb.open_case(CASE, "Examiner", "T-1")
case.set_category(kept["sha256"], 1, by="examiner")
case.close()

print("the export")
out = os.path.join(CASE, "ProjectVIC.json")
summary = projectvic_export.export(manifest, out, case_number="T-1")
block = json.load(open(out))["value"][0]
media = block["Media"]
md5s = [m["MD5"] for m in media]
check("one Media per distinct file", md5s == [kept["md5"], other["md5"]], str(md5s))
first = media[0]["MediaFiles"]
paths = [m["FilePath"] for m in first]
check("every place the file was found is a MediaFiles entry under it",
      paths == ["DCIM\\IMG_0001.jpg", "Pictures\\copy.jpg",
                "Unallocated Clusters (offset 4096)"], str(paths))
check("each place keeps its own details",
      [m["Deleted"] for m in first] == [False, True, False]
      and all(m["MD5"] == kept["md5"] for m in first))
check("TotalMediaFiles counts files", block["TotalMediaFiles"] == 2,
      str(block["TotalMediaFiles"]))
marked = sum(m["IsPrecategorized"] for m in media)
check("TotalPrecategorized agrees with the records",
      block["TotalPrecategorized"] == marked == 1,
      f"{block['TotalPrecategorized']} vs {marked}")
check("the summary says 2 images in 4 places",
      summary["total"] == 2 and summary["images"] == 2 and summary["places"] == 4
      and summary["precategorized"] == 1,
      str({k: summary[k] for k in ("total", "images", "places", "precategorized")}))
check("MediaIDs run 1, 2", [m["MediaID"] for m in media] == [1, 2])

print("\nthe validator")
report = projectvic_export.validate(out)
check("finds nothing to warn about in it", report["is_vics"] and not report["warnings"],
      str(report["warnings"]))
old = dict(block, TotalPrecategorized=0, TotalMediaFiles=4,
           Media=[media[0], media[1], dict(media[0]), dict(media[0])])
old_path = os.path.join(CASE, "old_style.json")
with open(old_path, "w") as fh:
    json.dump({"@odata.context": projectvic_export.ODATA_CONTEXT, "value": [old]}, fh)
report = projectvic_export.validate(old_path)
said = " ".join(report["warnings"])
# Written the old way, TotalMediaFiles matched its own Media array -- both
# counted places -- so what shows is the repeated MD5s and the 0.
check("warns about a file written the old way",
      "TotalPrecategorized says 0" in said and "2 Media record(s) repeat an MD5" in said
      and "TotalMediaFiles" not in said, str(report["warnings"]))
inconsistent = os.path.join(CASE, "inconsistent.json")
with open(inconsistent, "w") as fh:
    json.dump({"@odata.context": projectvic_export.ODATA_CONTEXT,
               "value": [dict(block, TotalMediaFiles=5)]}, fh)
said = " ".join(projectvic_export.validate(inconsistent)["warnings"])
check("and about a total that disagrees with the Media array",
      "TotalMediaFiles says 5; the Media array holds 2" in said, said)

print("\nthe hash layout")
hashed = os.path.join(ROOT, "staged", "ProjectVIC.json")
os.makedirs(os.path.dirname(hashed))
projectvic_export.export(manifest, hashed, case_number="T-1", files_layout="hash")
staged = json.load(open(hashed))["value"][0]["Media"]
check("each Media points at its staged file",
      [m["RelativeFilePath"] for m in staged]
      == [f"ProjectVICFiles\\{kept['md5']}", f"ProjectVICFiles\\{other['md5']}"])

print("\nback in again, with this program's own importer")
imported = os.path.join(ROOT, "imported")
with contextlib.redirect_stdout(io.StringIO()):
    result = vic_import.import_media(out, imported)
again = json.load(open(os.path.join(imported, "manifest.json")))
places = sorted([f["source_path"] for f in again["files"]]
                + [d["source_path"] for d in again["duplicates"]])
check("two files, and all four places they were found",
      len(again["files"]) == 2 and len(places) == 4
      and "Pictures\\copy.jpg" in places
      and "Unallocated Clusters (offset 4096)" in places, str(places))
check("counted as two found", result.get("found") == 2, str(result.get("found")))

shutil.rmtree(ROOT, ignore_errors=True)
if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
