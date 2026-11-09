"""
appcache.py -- media an app cached in pieces, rejoined.

Two app formats that leave real media on a phone in a shape nothing else
recovers:

**ExoPlayer caches.** ExoPlayer is the media library most Android apps
stream through (Instagram, Snapchat, Reddit, X, Spotify and many more). What
it caches is not one file per video: each video is cut into pieces named for
where they start, scattered across ten numbered subfolders, with the name of
the video kept in a separate index. Carving finds the first piece -- it opens
with a video header -- and nothing else, because the rest begins mid-stream.
Rejoining them in order gives back a playable file.

**Snapchat content bundles.** Files under `com.snap.file_manager_*_SCContent_`
are named by hash with no extension. Most are plain images, which a scan
finds; about a third are an `LZC\\0` container holding Zstandard frames.

What is joined and what is not matters evidentially, so it is recorded rather
than smoothed over. Pieces are joined from the start of the item until the
first gap: a piece after a gap cannot be placed in a playable file without
the bytes before it, so it is left alone rather than concatenated into
something that looks whole and is not. Every rejoined file says how many
pieces went in and whether the result is complete, partial, or stops at a
gap.
"""

from __future__ import annotations

import os
import re
import sqlite3
import struct
from typing import Dict, Iterator, List, Optional, Tuple

__version__ = "1.0.0"

# <id>.<position>.<timestamp>.v3.exo, and the older v1/v2 forms that carry
# the key in the name instead of in an index.
EXO_PIECE = re.compile(
    r"^(?P<key>.+)\.(?P<position>\d+)\.(?P<timestamp>\d+)\.v(?P<version>[123])\.exo$")

MAX_JOIN_BYTES = 4 * 1024 * 1024 * 1024
LZC_MAGIC = b"LZC\x00"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


class ExoItem:
    """One cached item: its pieces, and what is known about its name.

    `cache_dir` is the cache it belongs to. An item id is a number the cache
    hands out from 0, so every app's cache has an item 3; the id alone named
    Instagram's item 3 and Reddit's item 3 as one video and joined their
    pieces into one file.
    """

    def __init__(self, key: str, cache_dir: str = ""):
        self.key = key
        self.cache_dir = cache_dir
        self.pieces: List[Tuple[int, str, int]] = []   # position, path, size

    def add(self, position: int, path: str, size: int) -> None:
        self.pieces.append((position, path, size))

    def ordered(self) -> List[Tuple[int, str, int]]:
        return sorted(self.pieces, key=lambda p: p[0])

    def contiguous(self) -> Tuple[List[Tuple[int, str, int]], str, int]:
        """The run of pieces from the start, and where it stops.

        Returns the joinable pieces, a word for how it ended, and the byte
        the gap begins at. A cache rarely holds a whole video -- a user who
        watched ten seconds leaves ten seconds -- so "partial" is the normal
        case, not a failure.
        """
        ordered = self.ordered()
        if not ordered or ordered[0][0] != 0:
            return [], "no start", 0
        run = []
        expected = 0
        for position, path, size in ordered:
            if position < expected:
                continue                 # an overlapping rewrite; skip it
            if position > expected:
                return run, "stops at a gap", expected
            run.append((position, path, size))
            expected += size
        return run, "continuous", expected


def cache_dir_of(folder: str) -> str:
    """The cache a piece's folder belongs to: SimpleCache keeps v3 pieces in
    ten subfolders named 0 to 9 under the cache directory, and the older
    forms in the cache directory itself."""
    if os.path.basename(folder) in tuple("0123456789"):
        return os.path.dirname(folder)
    return folder


def find_exo_pieces(root: str) -> Dict[Tuple[str, str], ExoItem]:
    """Group every ExoPlayer piece under a folder by the item it belongs to,
    keyed by (cache directory, item key)."""
    items: Dict[Tuple[str, str], ExoItem] = {}
    for folder, _, names in os.walk(root):
        for name in names:
            match = EXO_PIECE.match(name)
            if not match:
                continue
            path = os.path.join(folder, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            key = match.group("key")
            if match.group("version") == "2":
                key = _unescape_key(key)
            cache = cache_dir_of(folder)
            item = items.get((cache, key))
            if item is None:
                item = items[(cache, key)] = ExoItem(key, cache)
            item.add(int(match.group("position")), path, size)
    return items


def _unescape_key(name: str) -> str:
    """Undo ExoPlayer's %xx escaping of a cache key."""
    def replace(match):
        try:
            return chr(int(match.group(1), 16))
        except ValueError:
            return match.group(0)
    return re.sub(r"%([0-9A-Fa-f]{2})", replace, name)


def read_index(root: str) -> Dict[str, str]:
    """Map item ids to the names the index recorded, usually a URL -- every
    index under `root`, merged. Only right for one cache; read_indexes()
    keeps caches apart.

    Two storages exist: a SQLite table in the app's own database, and the
    older `cached_content_index.exi` file. The database is read properly;
    the `.exi` is a private binary format, so the keys are lifted out of it
    by looking for the URLs it stores rather than by pretending to parse a
    format that is not documented. A key that cannot be read costs the name,
    not the video.
    """
    names: Dict[str, str] = {}
    for tables in _index_tables(root).values():
        names.update(tables)
    for folder, _, files in os.walk(root):
        for name in files:
            if name in ("cached_content_index.exi", "cached_content_index.exi.bak"):
                names.update(_read_index_file(os.path.join(folder, name)))
    return names


def _uid_hex(name: str) -> str:
    """The table suffix a cache's <uid>.uid file names. Java writes the file
    with Long.toString(uid, 16), which is signed, and the table with
    Long.toHexString(uid), which is not."""
    try:
        value = int(name[:-len(".uid")], 16)
    except ValueError:
        return ""
    return format(value & 0xFFFFFFFFFFFFFFFF, "x")


def _index_tables(root: str) -> Dict[str, Dict[str, str]]:
    """Every ExoPlayerCacheIndex table under `root`, by its uid suffix."""
    tables: Dict[str, Dict[str, str]] = {}
    for folder, _, files in os.walk(root):
        for name in files:
            if name.endswith(".db") or name.endswith(".db-wal"):
                path = os.path.join(folder, name)
                for suffix, names in _read_index_database(path, True).items():
                    tables.setdefault(suffix, {}).update(names)
    return tables


def read_indexes(root: str, caches) -> Dict[str, Dict[str, str]]:
    """Item names for each cache directory in `caches`, from its own index.

    A cache's names come from the cached_content_index.exi in its folder,
    or from the database table named for the uid in its `<uid>.uid` file.
    A table no cache claims is used only when there is one cache and one
    such table, so nothing is ambiguous; otherwise its names are left out
    rather than given to the wrong app's videos.
    """
    tables = _index_tables(root)
    found: Dict[str, Dict[str, str]] = {}
    claimed = set()
    for cache in caches:
        names: Dict[str, str] = {}
        try:
            listing = os.listdir(cache)
        except OSError:
            listing = []
        for name in listing:
            if name.endswith(".uid"):
                suffix = _uid_hex(name)
                if suffix in tables:
                    names.update(tables[suffix])
                    claimed.add(suffix)
        for name in ("cached_content_index.exi.bak", "cached_content_index.exi"):
            if name in listing:
                names.update(_read_index_file(os.path.join(cache, name)))
        found[cache] = names
    loose = [suffix for suffix in tables if suffix not in claimed]
    caches = list(caches)
    if len(caches) == 1 and len(loose) == 1 and not found[caches[0]]:
        found[caches[0]] = dict(tables[loose[0]])
    return found


def _read_index_database(path: str, by_table: bool = False):
    """{id: key} from a database's index tables -- or, with `by_table`,
    {uid suffix: {id: key}}, one per table, so caches stay apart."""
    names: Dict[str, str] = {}
    per: Dict[str, Dict[str, str]] = {}
    try:
        from fme.sqlite_uri import ro_uri  # lazy: nothing else here imports fme
        con = sqlite3.connect(ro_uri(path), uri=True)
    except sqlite3.Error:
        return per if by_table else names
    try:
        tables = [row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'ExoPlayerCacheIndex%'")]
        for table in tables:
            columns = {row[1].lower() for row in
                       con.execute(f'PRAGMA table_info("{table}")')}
            if not {"id", "key"} <= columns:
                continue
            suffix = table[len("ExoPlayerCacheIndex"):].lower()
            for item_id, key in con.execute(f'SELECT id, key FROM "{table}"'):
                if key:
                    names[str(item_id)] = str(key)
                    per.setdefault(suffix, {})[str(item_id)] = str(key)
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return per if by_table else names


def _read_index_file(path: str) -> Dict[str, str]:
    """Ids and keys out of a legacy index, read leniently."""
    names: Dict[str, str] = {}
    try:
        with open(path, "rb") as fh:
            blob = fh.read(16 * 1024 * 1024)
    except OSError:
        return names
    # Entries are an id followed by a length-prefixed UTF-8 key. Rather than
    # assume a layout, find the keys and take the four bytes before each
    # length as the id.
    for match in re.finditer(rb"[\x20-\x7e]{8,512}", blob):
        text = match.group(0).decode("ascii", "replace")
        if "://" not in text and not text.startswith("/"):
            continue
        start = match.start()
        if start >= 6:
            try:
                item_id = struct.unpack(">I", blob[start - 6:start - 2])[0]
                names.setdefault(str(item_id), text)
            except struct.error:
                continue
    return names


def join_item(item: ExoItem, max_bytes: int = MAX_JOIN_BYTES) -> Tuple[bytes, dict]:
    """Join an item's pieces into one buffer, with a note on what happened."""
    run, ending, joined_to = item.contiguous()
    details = {"key": item.key, "pieces_total": len(item.pieces),
               "pieces_joined": len(run), "ending": ending,
               "bytes": 0, "stops_at": joined_to}
    if not run:
        return b"", details

    chunks = []
    total = 0
    for _, path, size in run:
        if total + size > max_bytes:
            details["ending"] = "stopped at the size limit"
            break
        try:
            with open(path, "rb") as fh:
                chunks.append(fh.read())
            total += size
        except OSError:
            details["ending"] = "a piece could not be read"
            break
    details["bytes"] = total
    # Counted from what was read: a limit or a failed read stopped the join
    # part way, and the pieces after it were still counted as joined -- and
    # "complete" came from the run's ending before that, so a join cut at the
    # size limit was called complete.
    details["pieces_joined"] = len(chunks)
    details["stops_at"] = total if details["ending"] != ending else joined_to
    # Every cached piece, joined without a gap. Whether that is the whole
    # video is another question the pieces cannot answer: the cache holds
    # what was watched, and nothing here reads the video's full length. So
    # "complete" is never claimed; this says only that nothing cached was
    # left out.
    details["all_cached"] = (details["ending"] == "continuous"
                             and len(chunks) == len(item.pieces))
    details["complete"] = False
    return b"".join(chunks), details


def rejoin_folder(root: str) -> Iterator[Tuple[str, bytes, dict]]:
    """Every ExoPlayer item under a folder, rejoined where possible.

    Yields (name, bytes, details). `name` is what the cache's index called
    the item (usually its URL), or its id. Where the bytes are on disk is in
    the details, relative to `root` with "/" separators: "source_path" (the
    first piece, where the file begins), "piece_paths" (every piece joined,
    in order) and "cache_dir" (the cache it came from) -- the name is a URL,
    not a place on the device, and was being recorded as one.
    """
    items = find_exo_pieces(root)
    if not items:
        return
    names = read_indexes(root, sorted({cache for cache, _ in items}))

    def rel(path):
        return os.path.relpath(path, root).replace(os.sep, "/")

    for (cache, key), item in sorted(items.items()):
        payload, details = join_item(item)
        if not payload:
            continue
        joined = item.contiguous()[0][:details["pieces_joined"]]
        details["name"] = names.get(cache, {}).get(key, key)
        details["cache_dir"] = rel(cache)
        details["piece_paths"] = [rel(path) for _, path, _ in joined]
        details["source_path"] = details["piece_paths"][0] if joined else ""
        yield details["name"], payload, details


# --------------------------------------------------------------------------
# Snapchat content bundles
# --------------------------------------------------------------------------
def zstd_available() -> Optional[str]:
    """Which Zstandard decompressor is installed, if any."""
    for module in ("compression.zstd", "zstandard", "pyzstd"):
        try:
            __import__(module)
            return module
        except ImportError:
            continue
    return None


def _zstd_decompress(data: bytes) -> bytes:
    module = zstd_available()
    if module == "compression.zstd":
        from compression import zstd           # Python 3.14 and later
        return zstd.decompress(data)
    if module == "zstandard":
        import zstandard
        return zstandard.ZstdDecompressor().decompressobj().decompress(data)
    if module == "pyzstd":
        import pyzstd
        return pyzstd.decompress(data)
    raise RuntimeError("no Zstandard decompressor installed")


def is_lzc(head: bytes) -> bool:
    return head[:4] == LZC_MAGIC


def lzc_frames(data: bytes) -> Iterator[bytes]:
    """Each Zstandard frame in a bundle, decompressed.

    The container holds one or more frames; a frame may be a media file on
    its own, or an asset bundle with media inside it, so what comes out is
    handed back to the ordinary scan rather than assumed to be a picture.
    """
    if not is_lzc(data):
        return
    position = data.find(ZSTD_MAGIC)
    while position >= 0:
        following = data.find(ZSTD_MAGIC, position + 4)
        frame = data[position:following if following > 0 else len(data)]
        try:
            yield _zstd_decompress(frame)
        except Exception:  # noqa: BLE001 - a truncated frame is common
            pass
        position = following
