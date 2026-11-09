"""
lava_launch.py -- find a locally installed LAVA and open a project in it.

LAVA is handed the project file itself -- the `_lava_data.lava` written by
lava_export.py -- not the folder containing it. That matches how iLEAPP does
it: it builds the path to the .lava file and passes that to the launcher.

Detection follows the same three routes on each platform, because any one of
them can be the only one available on a given machine:

    executable    LAVA on PATH, or in the usual install locations
    association   the .lava extension is registered to something called LAVA
    application   macOS bundle, located with `open -Ra`

    from fme.exports.lava_launch import find_lava, open_project
    launcher = find_lava("/cases/out/lava/_lava_data.lava")
    if launcher:
        open_project("/cases/out/lava/_lava_data.lava", launcher)

If nothing is found, send the examiner to https://www.leapps.org/#lava
rather than failing silently -- the output set is still valid and can be
opened by hand.

Nothing here is tested against a real LAVA install: this environment has
none. The paths and registry keys follow LAVA's documented install layout.
"""

from __future__ import annotations

# Allow this file to be run directly -- "python fme/engines/media_core.py" --
# as well as imported. Run as a script there is no package context, so the
# project root goes on the path before the package imports below.
if __package__ in (None, ""):  # pragma: no cover - only when run as a script
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))

import os
import shutil
import subprocess
import sys
from pathlib import Path

LAVA_WEBSITE = "https://www.leapps.org/#lava"
PROJECT_FILENAME = "_lava_data.lava"


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------
def _win_registry_default(root, key_path):
    try:
        import winreg
        with winreg.OpenKey(root, key_path) as key:
            return winreg.QueryValueEx(key, None)[0]
    except (ImportError, OSError):
        return None


def _win_executable():
    found = shutil.which("LAVA.exe") or shutil.which("lava.exe")
    if found:
        return found
    for env_name in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_name)
        if not base:
            continue
        for candidate in (Path(base, "Programs", "LAVA", "LAVA.exe"),
                          Path(base, "LAVA", "LAVA.exe")):
            try:
                if candidate.is_file():
                    return str(candidate)
            except OSError:
                continue
    return None


def _win_association():
    try:
        import winreg
    except ImportError:
        return False
    prog_ids = []
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer"
                r"\FileExts\.lava\UserChoice") as key:
            prog_ids.append(winreg.QueryValueEx(key, "ProgId")[0])
    except OSError:
        pass
    for root, prefix in ((winreg.HKEY_CURRENT_USER, r"Software\Classes"),
                         (winreg.HKEY_CLASSES_ROOT, "")):
        ext_key = rf"{prefix}\.lava" if prefix else ".lava"
        prog_id = _win_registry_default(root, ext_key)
        if prog_id:
            prog_ids.append(prog_id)
        for pid in prog_ids:
            cmd_key = (rf"{prefix}\{pid}\shell\open\command" if prefix
                       else rf"{pid}\shell\open\command")
            command = _win_registry_default(root, cmd_key)
            if command and "lava" in command.lower():
                return True
    return False


def _win_detached(args):
    subprocess.Popen(
        args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, close_fds=True,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                       | subprocess.DETACHED_PROCESS))


# --------------------------------------------------------------------------
# Linux
# --------------------------------------------------------------------------
def _linux_executable():
    for name in ("lava", "LAVA", "lava.AppImage", "LAVA.AppImage"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (Path.home() / "Applications" / "LAVA.AppImage",
                      Path.home() / "Applications" / "lava.AppImage",
                      Path.home() / ".local" / "bin" / "lava",
                      Path("/opt/LAVA/LAVA.AppImage"),
                      Path("/opt/lava/lava.AppImage")):
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return None


def _linux_association(project_path):
    xdg_mime = shutil.which("xdg-mime")
    if not xdg_mime or not shutil.which("xdg-open"):
        return False
    try:
        mime = subprocess.run([xdg_mime, "query", "filetype", project_path],
                              capture_output=True, text=True,
                              check=False, timeout=5).stdout.strip()
        desktop = subprocess.run([xdg_mime, "query", "default", mime],
                                 capture_output=True, text=True,
                                 check=False, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(mime and desktop and "lava" in desktop.lower())


# --------------------------------------------------------------------------
# Public
# --------------------------------------------------------------------------
def find_lava(project_path: str = ""):
    """Return (kind, value) for a usable launcher, or None."""
    if sys.platform == "win32":
        executable = _win_executable()
        if executable:
            return ("executable", executable)
        return ("association", None) if _win_association() else None

    if sys.platform == "darwin":
        try:
            result = subprocess.run(["open", "-Ra", "LAVA"],
                                    capture_output=True, check=False, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        return ("application", "LAVA") if result.returncode == 0 else None

    executable = _linux_executable()
    if executable:
        return ("executable", executable)
    if project_path and _linux_association(project_path):
        return ("association", None)
    return None


def open_project(project_path: str, launcher=None) -> str:
    """Open a .lava project. Returns the launcher kind actually used."""
    if not os.path.isfile(project_path):
        raise FileNotFoundError(project_path)
    launcher = launcher or find_lava(project_path)
    if not launcher:
        raise RuntimeError(
            f"LAVA was not found on this machine. The output set is still "
            f"valid -- open {os.path.basename(project_path)} by hand, or get "
            f"LAVA from {LAVA_WEBSITE}")

    kind, value = launcher
    if kind == "executable":
        if sys.platform == "win32":
            _win_detached([value, project_path])
        else:
            subprocess.Popen([value, project_path])
    elif kind == "application":
        subprocess.Popen(["open", "-a", value, project_path])
    elif kind == "association":
        if sys.platform == "win32":
            explorer = shutil.which("explorer.exe")
            if not explorer:
                raise FileNotFoundError("explorer.exe is not available")
            _win_detached([explorer, project_path])
        else:
            xdg_open = shutil.which("xdg-open")
            if not xdg_open:
                raise FileNotFoundError("xdg-open is not installed")
            subprocess.Popen([xdg_open, project_path])
    else:
        raise ValueError(f"Unknown launcher kind: {kind}")
    return kind


def project_file(lava_dir: str) -> str:
    """The file LAVA opens, given the folder lava_export.py wrote."""
    return os.path.join(lava_dir, PROJECT_FILENAME)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Find LAVA and open a project in it.")
    ap.add_argument("project", nargs="?", default="",
                    help="path to _lava_data.lava, or the folder holding it")
    ap.add_argument("--detect-only", action="store_true")
    args = ap.parse_args(argv)

    path = args.project
    if path and os.path.isdir(path):
        path = project_file(path)

    launcher = find_lava(path)
    if not launcher:
        print(f"LAVA not found on this machine. Get it from {LAVA_WEBSITE}")
        print("The output set is still valid; open the .lava file by hand.")
        return 1
    print(f"LAVA found: {launcher[0]}" + (f" at {launcher[1]}" if launcher[1] else ""))
    if args.detect_only or not path:
        return 0
    kind = open_project(path, launcher)
    print(f"Opened {path} via {kind}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
