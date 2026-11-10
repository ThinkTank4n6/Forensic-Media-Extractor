"""A reader run in a child process cannot take the program down with it.

fme/engines/isolated.py runs the ESE reader (which can spin forever) and the
GPU-texture decoder (which can segfault) in a child with a deadline. What
matters is how each way of ending is reported: a crash must be called a
crash, a hang a timeout, and neither may reach this process.
"""
import os
import subprocess
import sys
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from fme.engines import isolated

ok = True
print("how a child process ends is reported, and this one survives it")

crash = isolated.run("edb", [], 30, command_line=[
    sys.executable, "-c", "import os; os.abort()"])
ok &= crash.status == "crashed"
print(f"  a child that aborts: {crash.status} ({crash.detail})")

t0 = time.monotonic()
hang = isolated.run("edb", [], 2, command_line=[
    sys.executable, "-c", "import time; time.sleep(60)"])
took = time.monotonic() - t0
ok &= hang.status == "timeout" and took < 15
print(f"  a child that hangs: {hang.status} after {took:.1f} s")

fail = isolated.run("edb", [], 30, command_line=[
    sys.executable, "-c", "import sys; print('no', file=sys.stderr); sys.exit(3)"])
ok &= fail.status == "failed" and "no" in fail.detail
print(f"  a child that fails: {fail.status} ({fail.detail})")

print("\nonly named workers can be started")
try:
    isolated.command("os.system", [])
    refused = False
except ValueError:
    refused = True
ok &= refused
print(f"  an unknown worker name is refused: {refused}")
ok &= isolated.dispatch("os.system", []) == 2
print("  and refused inside the child as well: True")

print("\nthe worker starts from anywhere, and from a frozen build")
# From a directory that is not the project root: python -m would fail here.
import tempfile
elsewhere = os.path.join(tempfile.gettempdir(), "fme-isolated-" + str(os.getpid()))
os.makedirs(elsewhere, exist_ok=True)
outcome = subprocess.run(isolated.command("edb", [os.path.join(elsewhere, "absent", "Windows.edb")]),
                         capture_output=True, text=True, cwd=elsewhere, timeout=60)
started = outcome.returncode == 3 and "No module named" not in outcome.stderr
ok &= started
print(f"  started from {elsewhere}: imported fme and answered (exit "
      f"{outcome.returncode}): {started}")

# A frozen build runs itself with the hidden argument; fme.cli must send it
# to the worker rather than treat it as an unknown tool.
frozen = isolated.command.__globals__["sys"]
was = getattr(frozen, "frozen", None)
frozen.frozen = True
try:
    cmd = isolated.command("edb", ["x.edb"])
finally:
    if was is None:
        del frozen.frozen
    else:
        frozen.frozen = was
shape = cmd[1:] == ["_worker", "edb", "x.edb"]
ok &= shape
print(f"  frozen command is <exe> _worker edb <file>: {shape}")
via_cli = subprocess.run([sys.executable, "-m", "fme", "_worker", "edb",
                          os.path.join(elsewhere, "absent", "Windows.edb")], capture_output=True,
                         text=True, cwd=PROJECT, timeout=60)
dispatched = via_cli.returncode == 3 and "Unknown tool" not in via_cli.stderr
ok &= dispatched
print(f"  fme.cli hands _worker to the worker (exit {via_cli.returncode}): "
      f"{dispatched}")
usage = subprocess.run([sys.executable, "-m", "fme", "--help"],
                       capture_output=True, text=True, cwd=PROJECT).stdout
hidden = "_worker" not in usage
ok &= hidden
print(f"  and does not list it as a tool: {hidden}")

import shutil
shutil.rmtree(elsewhere, ignore_errors=True)
print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
