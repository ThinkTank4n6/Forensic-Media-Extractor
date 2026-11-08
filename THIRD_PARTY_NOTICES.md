# Licenses and notices

## This program

Forensic Media Extractor is released under the **MIT License**:

> Copyright (c) 2026 Christopher A. Tankelewicz (ThinkTank4n6)
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in
> all copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

The full text is in `LICENSE`. It covers the code written for this project.
Everything below is somebody else's work, included or bundled under its own
license, and keeps that license.

### No warranty, for third-party components too

As the licenses of The Sleuth Kit (IBM Public License 1.0 and Common Public
License 1.0, section 3) require of anyone who distributes it in object code,
this program's license **disclaims, on behalf of all contributors, all
warranties and conditions, express and implied**, including warranties or
conditions of title and non-infringement, and implied warranties or conditions
of merchantability and fitness for a particular purpose; and **excludes, on
behalf of all contributors, all liability for damages**, including direct,
indirect, special, incidental and consequential damages, such as lost profits.
**Any provisions that differ from those licenses are offered by the author of
this program alone and not by any other party.**

This program is a tool for an examiner. It does not replace the examiner's own
verification of what it reports; `docs/VALIDATION.md` says what it has and has
not been tested against.

## The Sleuth Kit — read through pytsk3

```
Copyright (c) 1997,1998,1999, International Business Machines
Corporation and others. All Rights Reserved.
```

The Sleuth Kit (TSK) originates from The Coroner's Toolkit, written by Wietse
Venema under the IBM Public License 1.0, and was adopted by Brian Carrier under
the Common Public License 1.0:

- Copyright (c) 2002-2003 Brian Carrier, @stake Inc. All rights reserved.
- Copyright (c) 2003-2005 Brian Carrier. All rights reserved.
- Copyright (c) 2006-2011 Brian Carrier, Basis Technology. All rights reserved.

TSK is not part of this program's source. The standalone Windows build bundles
the TSK library as compiled into **pytsk3**, unmodified. This program's author
made no changes to TSK and contributes nothing to it.

- Licenses: `licenses/IBM-PL-1.0.txt`, `licenses/CPL-1.0.txt`
- **Source code** for TSK is available from https://www.sleuthkit.org/ and
  https://github.com/sleuthkit/sleuthkit, and for pytsk3 from
  https://github.com/py4n6/pytsk. On request, the author of this program will
  provide the exact source versions used in a given build.

## pytsk3

Python bindings for The Sleuth Kit. Apache License 2.0
(`licenses/Apache-2.0.txt`). Source: https://github.com/py4n6/pytsk.

## libewf / pyewf — reading EnCase (E01) images

Copyright (C) Joachim Metz. GNU Lesser General Public License, version 3 or
later (`licenses/LGPL-3.0.txt`, which supplements `licenses/GPL-3.0.txt`).
Source: https://github.com/libyal/libewf. In the standalone build it is a
separate, dynamically loaded module (`pyewf*.pyd`), unmodified; it can be
replaced with another build of the same library.

## pillow-heif — reading HEIC/HEIF (iPhone) pictures

pillow-heif's own code is BSD-3-Clause. Its binary wheels, which the standalone
build bundles when pillow-heif is installed, include:

| library | license | source |
|---|---|---|
| libheif | LGPL-3.0 | https://github.com/strukturag/libheif |
| libde265 | LGPL-3.0 | https://github.com/strukturag/libde265 |
| x265 | **GPL-2.0** | https://github.com/Multicorewareinc/x265 |
| MinGW-w64 runtime (Windows) | GPL-3.0 with GCC Runtime Library Exception; MIT and BSD | https://sourceforge.net/projects/mingw-w64/ |

pillow-heif states that its binary wheels are, as a whole, **GPLv2** because of
x265. This program only reads HEIC; it never calls the x265 encoder. The
standalone build therefore carries GPLv2 obligations for that component:
`licenses/GPL-2.0.txt` is included, the source of every component is available
at the addresses above, and this program's own source -- MIT, which may be
combined with GPL-2.0 code -- is available from its author. A build made
without pillow-heif installed contains none of these libraries, and recovers
HEIC files without being able to show them.

## Other libraries bundled in the standalone build

The build copies each bundled Python package's own license files into
`licenses/python/<package>/` (see `packaging/notices.py`); those files are the
authoritative text. In summary:

| package | license |
|---|---|
| Flask, Werkzeug, Jinja2, MarkupSafe, itsdangerous, click | BSD-3-Clause |
| blinker | MIT |
| waitress | Zope Public License 2.1 |
| cheroot | BSD-3-Clause |
| Pillow | MIT-CMU (HPND) |
| NumPy | BSD-3-Clause (bundled parts: 0BSD, MIT, Zlib, CC0-1.0) |
| OpenCV (opencv-python-headless) | Apache-2.0; its wheels include FFmpeg (LGPL-2.1, `licenses/LGPL-2.1.txt`) |
| cryptography | Apache-2.0 or BSD-3-Clause |
| pycryptodome | BSD-2-Clause and public domain |
| py7zr | LGPL-2.1-or-later |
| zstandard | BSD-3-Clause |
| ReportLab | BSD |
| texture2ddecoder | MIT |
| pyliblzfse | MIT (LZFSE itself BSD-3-Clause) |

## Code included in this program's source

Each is kept byte-identical to what its author published, with its license
beside it in `fme/vendor/` (provenance in `fme/vendor/vendored.json`):

| component | copyright | license |
|---|---|---|
| mediacarve, qnxprobe, ewfprobe, exoprobe, c2pareader | Copyright (c) 2026 Alexis Brignoni | MIT |
| GLEAPP, from which qnxprobe, mediacarve, the ESE reader, the models and the map assets were taken (`fme/vendor/LICENSE-gleapp`) | Copyright (c) 2026 charpy4n6, Alexis Brignoni | MIT |
| qnxmount (byte layouts transcribed by qnxprobe) | | Apache-2.0 |
| impacket ESE reader | Copyright (c) 2003-2023 Fortra (impacket); adaptation by DLEAPP | Apache-1.1 (Impacket variant), with an MIT part, Copyright (c) 2019 Isaac Boukris |

## Models

In `fme/models/`, each with its license file:

| model | license |
|---|---|
| DINOv2 (content search) | Apache-2.0 |
| SFace (face recognition) | Apache-2.0 |
| YuNet (face detection), Copyright (c) 2020 Shiqi Yu | MIT |

## Map assets

In `fme/web/ui/maps/`, each with its license file. No map data ships with this
program; a basemap the examiner imports carries its own terms (OpenStreetMap
data is ODbL and must be attributed).

| asset | license |
|---|---|
| MapLibre GL JS | BSD-3-Clause |
| PMTiles | BSD-3-Clause |
| Protomaps basemap styles | BSD-3-Clause |
| Noto Sans fonts | SIL Open Font License 1.1 |
| Protomaps sprites | derived from MIT-licensed tangrams/icons |
