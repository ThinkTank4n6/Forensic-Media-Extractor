# Building a self-contained executable

One file. No Python on the target machine, no `pip install`.

```bash
pip install -r requirements.txt      # do this FIRST — see the warning below
pip install pyinstaller

python build_exe.py --portable       # a folder: the .exe beside _internal/
python build_exe.py                  # one file
```

**Which to build.** A portable folder starts immediately and is the right
shape for a stick or a share; copy the whole folder, because the executable
needs `_internal/` beside it. A single file is tidier to hand over but
unpacks itself to a temporary folder on every launch, which on a bundle of
this size is a wait before every run. Expect 300–450 MB with OpenCV and
Pillow in it.

Both commands build from the one spec, `packaging/fme.spec` (`python packaging/build.py` drives the same spec without the capability report). It builds `packaging/entrypoint.py`, which starts `fme.cli` exactly as `python -m fme` and an installed `fme` do, after the multiprocessing guard a frozen build needs. `tests/test_packaging.py` checks the spec against the repository: the files it carries, and that every reader imported by name resolves on its path -- a name written in the spec that PyInstaller cannot find is dropped from the build with only a warning.

Produces `dist/ForensicMediaExtractor` (`.exe` on Windows). Double-click it
and the interface opens. From a command prompt, one binary runs every tool:

```
ForensicMediaExtractor                                  the interface
ForensicMediaExtractor gui -o D:\cases                  results on D:
ForensicMediaExtractor extract -i case.E01 -o D:\out --vic DC26-253
ForensicMediaExtractor hashsets --scan D:\out\manifest.json --nsrl NSRLFile.txt
ForensicMediaExtractor report -m D:\out\manifest.json -o D:\out\report.html
ForensicMediaExtractor doctor                           what made it in
```

## Install the forensic libraries before building, not after

PyInstaller bundles what is importable **at build time**. Build in an
environment without `pytsk3` and you get an executable that cannot read NTFS,
exFAT, HFS+ or APFS — and it will not say so until an examiner points it at
an exhibit. The same goes for `libewf-python` and E01 images.

`python build_exe.py --check` reports exactly what would be included before
you spend the time, and refuses to build without Flask. The build writes a
`build_info.json` into the bundle, so `ForensicMediaExtractor doctor` on the
finished article reports the same thing.

## Build on the platform you will run on

PyInstaller freezes the interpreter and the compiled extensions it finds; it
does not cross-compile. A Windows `.exe` must be built on Windows, a Linux
binary on Linux, macOS separately again. `pytsk3` and the libyal libraries
are compiled extensions, which is what makes this unavoidable.

## Two details that matter in a frozen build

**Output goes beside the executable, not into the temp directory.** A
one-file build unpacks itself somewhere temporary and deletes it on exit, so
anything written relative to `__file__` is lost when the program closes. The
code uses the executable's own folder instead.

**Parallel carving needs `freeze_support()`.** Workers are started with the
"spawn" method, and a frozen executable creates one by re-running itself.
Without the guard at the top of `fme.py`, each worker restarts the whole
program rather than running the worker function — for an interface that
opens a browser window, that means a new window per worker, repeatedly.

## Size, antivirus and signing

Expect 40–80 MB depending on what is installed. UPX compression is
deliberately off: packed executables trip antivirus heuristics constantly,
and a forensic tool quarantined mid-case is worse than a larger download.

Unsigned executables from an unknown publisher are still flagged by
SmartScreen on Windows and Gatekeeper on macOS. If this is going to other
examiners, sign it with your organization's certificate — a step neither this
script nor PyInstaller can do for you.

## If it builds but misbehaves

Run `ForensicMediaExtractor doctor` first: it reports the version, the
capabilities actually present, and where results will be written. Most
surprises are one of those three.
