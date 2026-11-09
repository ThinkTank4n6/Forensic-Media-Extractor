"""What an examiner's machine remembers between sessions.

Retyping a server address, a certificate path and a token every morning is
not security. It is friction, and friction is what puts a token in a text
file on somebody's desktop -- which is worse than anything the retyping was
protecting against.

So the address and the certificate are remembered, because neither is a
secret. The token is not, unless the examiner asks: it is their identity on
a store full of hashes of child abuse material, and writing it down is a
real reduction in security that some units will accept and others will not.
"""
import _sandbox  # noqa: F401 -- program state goes to a temp folder, not the tree
import tempfile
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# One directory per process. Twenty-seven tests used a

# fixed path here and each wipes it on startup, so two

# runs overlapping -- which happens whenever a batch is

# cut short and its child keeps going -- had the second

# deleting the first's working files mid-run. That is

# the only mechanism found for this suite's intermittent

# failures.

ROOT = os.path.join(tempfile.gettempdir(), "settings_test-") + str(os.getpid())
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
os.environ["XDG_CONFIG_HOME"] = ROOT
os.environ["LOCALAPPDATA"] = ROOT

from fme.review import settings

ok = True
print(f"settings live in {settings.folder()}")
# Per user, not in the program folder: a program folder is shared, replaced
# on update, and sometimes on a share.
program = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok &= not settings.folder().startswith(program)
print(f"  outside the program folder: "
      f"{not settings.folder().startswith(program)}")

print("\nwithout being asked to remember the token")
settings.save({"store_path": "https://LABSERVER:8787",
               "store_cert": r"C:\hashstore\server.crt",
               "store_token": "a-real-token"}, remember_token=False)
kept = settings.load()
ok &= kept.get("store_path") == "https://LABSERVER:8787"
ok &= kept.get("store_cert") == r"C:\hashstore\server.crt"
ok &= "store_token" not in kept
print(f"  the address is kept    : {kept.get('store_path')}")
print(f"  the certificate is kept: {bool(kept.get('store_cert'))}")
print(f"  the token is NOT kept  : {'store_token' not in kept}")

# And it is genuinely not on disk -- not merely absent from what load()
# returns, which would be a lie an examiner could not check.
with open(settings.path(), encoding="utf-8") as fh:
    raw = fh.read()
ok &= "a-real-token" not in raw
print(f"  and the token is not in the file at all: {'a-real-token' not in raw}")

print("\nwhen asked to remember it")
settings.save({"store_path": "https://LABSERVER:8787",
               "store_token": "a-real-token"}, remember_token=True)
kept = settings.load()
ok &= kept.get("store_token") == "a-real-token"
print(f"  the token is kept: {bool(kept.get('store_token'))}")

# The file has to say what it holds: somebody finding it later, or backing
# it up, should not have to work out that it is a credential.
ok &= "token" in (kept.get("token_warning") or "").lower()
print(f"  and the file says what that means: "
      f"{bool(kept.get('token_warning'))}")
print(f"    \"{kept.get('token_warning', '')[:70]}…\"")

if os.name != "nt":
    mode = os.stat(settings.path()).st_mode & 0o777
    ok &= mode == 0o600
    print(f"  readable by this account only: {oct(mode)}")

# Saving something else must not wipe the token. Passing "no token" as an
# empty string did exactly that: the setting survived the restart as a
# blank, and the examiner was asked for it again having ticked the box that
# says they would not be.
print("\nsaving other settings afterwards")
settings.save({"store_token": "a-real-token"}, remember_token=True)
settings.save({"store_path": "https://LABSERVER:8787"}, remember_token=True)
kept = settings.load()
ok &= kept.get("store_token") == "a-real-token"
ok &= kept.get("store_path") == "https://LABSERVER:8787"
print(f"  the token survives saving the store path: "
      f"{kept.get('store_token') == 'a-real-token'}")

print("\nforgetting")
settings.forget_token()
after = settings.load()
ok &= "store_token" not in after and bool(after.get("store_path"))
print(f"  the token goes, the address stays: "
      f"{'store_token' not in after and bool(after.get('store_path'))}")

settings.forget()
ok &= settings.load() == {}
print(f"  forgetting everything leaves nothing: {settings.load() == {}}")


# A results folder saved in Settings must be used on the next launch.
#
# It was written to the settings file and never read back: the store
# path, the token and the certificate were restored at startup and
# this was not. An examiner set a central folder, saw it in Settings,
# and every run still went to the default beside the program.
print("\nthe saved results folder")
import importlib as _imp
import subprocess as _sub

_central = os.path.join(tempfile.gettempdir(), "fme_saved_out")
shutil.rmtree(_central, ignore_errors=True)
os.makedirs(_central, exist_ok=True)

_probe = f"""
import os, sys
sys.path.insert(0, {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))!r})
from fme.review import settings
settings.save({{"output_root": {_central!r}}})
from fme.web import media_gui
print(media_gui.OUT_ROOT)
"""
_said = _sub.run([sys.executable, "-c", _probe], capture_output=True,
                 text=True).stdout.strip().splitlines()
_used = _said[-1] if _said else ""
ok &= os.path.abspath(_used) == os.path.abspath(_central)
print(f"   a fresh start uses it: {os.path.abspath(_used) == os.path.abspath(_central)}")

# And a folder that cannot be used -- an evidence drive that is not
# attached today -- must not stop the program starting.
_gone = f"""
import os, sys
sys.path.insert(0, {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))!r})
from fme.review import settings
settings.save({{"output_root": "/proc/cannot/make/this"}})
from fme.web import media_gui
print(media_gui.OUT_ROOT)
"""
_r = _sub.run([sys.executable, "-c", _gone], capture_output=True, text=True)
_fell_back = bool(_r.stdout.strip()) and _r.returncode == 0
ok &= _fell_back
print(f"   an unusable one does not stop it starting: {_fell_back}")
ok &= "cannot be used" in _r.stderr
print(f"   and it says why: {'cannot be used' in _r.stderr}")

print("\nALL CHECKS PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
