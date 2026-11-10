"""Does the page's script survive being loaded?

Everything else about the interface is checked by reading it: that the
elements exist, that the functions are defined, that the handlers are
wired. None of that catches the fault that matters most -- a throw at the
top level, which stops every line after it. The page still renders, because
the markup is already there, and not one button works.

That shipped once. The ribbon read variables declared further down the
script, threw as the page loaded, and took the whole interface with it.

So the script is actually run here, against a pretend browser: enough DOM
for it to reach the end. It is not a test of behaviour -- nothing is
clicked -- it is a test that the program starts at all.

Needs node, which is how the other scripts here are checked too. Without
it the test says so and stops rather than passing quietly.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import tempfile
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fme.web import media_gui

HERE = os.path.dirname(os.path.abspath(__file__))
SHIM = os.path.join(HERE, "support", "dom_shim.js")

if shutil.which("node") is None:
    print("node is not installed, so the page script cannot be run; skipping")
    sys.exit(0)

ok = True
script = re.search(r"<script>(.*)</script>", media_gui.PAGE, re.S).group(1)
work = os.path.join(tempfile.gettempdir(), "gui_boot_test")
os.makedirs(work, exist_ok=True)
page = os.path.join(work, "page.js")
with open(page, "w", encoding="utf-8") as fh:
    fh.write(script)

runner = os.path.join(work, "run.js")
with open(runner, "w", encoding="utf-8") as fh:
    fh.write(f"""
require({SHIM!r});
const code = require("fs").readFileSync({page!r}, "utf8");
try {{
  new Function(code)();
  console.log("RAN");
}} catch (e) {{
  console.log("THREW: " + e.message);
  console.log(((e.stack || "").split("\\n")[1] || "").trim());
  process.exit(1);
}}
""")

result = subprocess.run([shutil.which("node"), runner], capture_output=True,
                        text=True, timeout=120)
ran = "RAN" in result.stdout
ok &= ran
print("the page's script runs to the end: " + ("yes" if ran else "NO"))
if not ran:
    print(result.stdout.strip() or result.stderr.strip()[:400])

# The specific shape of the fault that shipped: something read before it is
# declared. Those throw only when the line runs, which is why reading the
# code does not find them.
print("\nwhat this catches that reading cannot:")
print("  a variable used above its own 'let'")
print("  a function called before the thing it needs exists")
print("  any top-level throw, which stops every line after it")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
