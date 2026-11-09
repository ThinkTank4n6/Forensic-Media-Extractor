"""What the carve could not read is listed, however the read fell short.

- A read that returned some bytes but fewer than its chunk -- a segment
  shorter than the set says, a reader that stops early -- left the rest of
  the chunk unscanned, and only an empty read was ever listed as unread.
- A source that could not report its size had size 0: the carve scanned
  nothing and was recorded as finished.
"""
import logging
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
logging.disable(logging.CRITICAL)
from fme.engines import media_core  # noqa: E402

ok = True
MIB = 1 << 20


class StopsShort(media_core.ImageReader):
    """A 4 MiB source whose second MiB reads back only its first 100 KiB."""
    size = 4 * MIB

    def read(self, offset, length):
        if offset >= self.size or length <= 0:
            return b""
        end = min(offset + length, self.size)
        if offset <= MIB < end or MIB <= offset < 2 * MIB:
            end = max(offset, MIB) + 100 * 1024 if offset <= MIB else offset
        return b"\0" * max(0, end - offset)


unread = []
media_core._detect_range(StopsShort(), media_core.SIGNATURES, hit_lo=0,
                         hit_hi=4 * MIB, start_lo=0, start_hi=None,
                         next_allowed=0, align=1, max_carve_size=MIB,
                         chunk_size=MIB, unread=unread)
gap_start = MIB + 100 * 1024
covered = any(start <= gap_start and start + length >= 2 * MIB
              for start, length in unread)
ok &= covered
print(f"a chunk read short: the rest of it is listed as unread {covered} "
      f"{unread[:3]} -- {'ok' if covered else 'WRONG'}")


class NoSize:
    def get_size(self):
        raise OSError("size not available")

    def seek(self, offset):
        pass

    def read(self, length):
        return b"\0" * length


stats = {"candidates": 0, "carved_files": 0, "carved_bytes": 0}
args = types.SimpleNamespace(chunk_size=MIB, align=1, max_carve_size=MIB,
                             progress=False, resume_from=0, resuming=None)
out = types.SimpleNamespace(outdir="/nonexistent", records=[])
media_core.run_carve_engine(media_core.wrap_source(NoSize()), out, args, stats)
said = bool(stats.get("carve_incomplete"))
ok &= said
print(f"a source with no size: the carve says it carved nothing {said} -- "
      f"{'ok' if said else 'WRONG'}")

if not ok:
    print("FAILURES ABOVE")
sys.exit(0 if ok else 1)
