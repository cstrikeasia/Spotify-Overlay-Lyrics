"""Bounded local album-cover cache and Windows SMTC Thumbnail decoding.

No Spotify credentials, external cover APIs, Qt imports, or extra HTTP polls.
Only the media monitor thread calls the WinRT reader, on a new track or
periodic retry if SMTC has not published its image yet.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

LOG = logging.getLogger(__name__)
MAX_ARTWORK_BYTES = 4 * 1024 * 1024
MAX_CACHE_FILES = 160
NEGATIVE_RETRY_SECONDS = 8.0


@dataclass(frozen=True)
class Artwork:
    image_id: str
    mime_type: str
    path: Path


def image_mime(data: bytes) -> str:
    """Only allow recognizable browser/Qt image formats, never arbitrary files."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return ""


_SUFFIX = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}


class ArtworkCache:
    """Content-addressed on-disk cache under the user's local app data."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)

    def put(self, data: bytes) -> Artwork | None:
        if not data or len(data) > MAX_ARTWORK_BYTES:
            return None
        mime = image_mime(data)
        if not mime:
            LOG.warning("SMTC thumbnail ignored: unsupported image format")
            return None
        image_id = hashlib.sha256(data).hexdigest()
        path = self.folder / (image_id + _SUFFIX[mime])
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            if (not path.is_file() or path.stat().st_size != len(data)
                    or path.read_bytes() != data):
                # Never expose partially-written files to the desktop or OBS.
                # The per-thread suffix also avoids two app instances sharing
                # the same temporary file while preparing a new cover.
                tmp = self.folder / f".{image_id}.{os.getpid()}.{threading.get_ident()}.tmp"
                try:
                    tmp.write_bytes(data)
                    os.replace(tmp, path)
                finally:
                    tmp.unlink(missing_ok=True)
                self._prune()
        except OSError as exc:
            LOG.warning("Cannot cache Spotify artwork: %s", exc)
            return None
        return Artwork(image_id, mime, path)

    def _prune(self) -> None:
        try:
            files = sorted(
                (p for p in self.folder.iterdir() if p.suffix.lower() in _SUFFIX.values()),
                key=lambda p: p.stat().st_mtime,
            )
            for old in files[:-MAX_CACHE_FILES]:
                old.unlink(missing_ok=True)
        except OSError as exc:
            LOG.debug("Skipping cover cache prune: %s", exc)


def verified_artwork_bytes(artwork: Artwork) -> bytes | None:
    """Prevent stale paths, mismatched file content, or arbitrary local files."""
    try:
        if artwork.path.stat().st_size > MAX_ARTWORK_BYTES:
            return None
        contents = artwork.path.read_bytes()
    except OSError:
        return None
    if image_mime(contents) != artwork.mime_type:
        return None
    if hashlib.sha256(contents).hexdigest() != artwork.image_id:
        return None
    return contents


async def read_thumbnail(thumbnail: object, *, options: object | None = None) -> bytes:
    """Read a WinRT IRandomAccessStreamReference with bounded chunk reads.

    PyWinRT exposes IInputStream.read_async into a writable Python bytearray.
    See https://pywinrt.readthedocs.io/en/latest/types/#buffers
    """
    if thumbnail is None:
        return b""
    if options is None:
        from winrt.windows.storage.streams import InputStreamOptions
        options = InputStreamOptions.NONE
    stream = await thumbnail.open_read_async()
    try:
        length = int(stream.size)
        if length < 1 or length > MAX_ARTWORK_BYTES:
            LOG.warning("SMTC artwork size rejected: %s", length)
            return b""
        result = bytearray()
        while len(result) < length:
            count = min(64 * 1024, length - len(result))
            chunk = await stream.read_async(bytearray(count), count, options)
            data = bytes(chunk)
            if not data:
                break
            result.extend(data)
        return bytes(result) if len(result) == length else b""
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            close()


class ArtworkResolver:
    """Avoid reading Spotify's same thumbnail on every 700ms media poll."""

    def __init__(self, folder: Path) -> None:
        self.cache = ArtworkCache(folder)
        self._known: OrderedDict[str, Artwork] = OrderedDict()
        self._negative_until: dict[str, float] = {}

    def get_cached(self, track_key: str) -> Artwork | None:
        cached = self._known.get(track_key)
        if cached is not None and cached.path.is_file():
            self._known.move_to_end(track_key)
            return cached
        return None

    async def resolve(self, track_key: str, thumbnail: object | None) -> Artwork | None:
        cached = self.get_cached(track_key)
        if cached is not None:
            return cached
        if time.monotonic() < self._negative_until.get(track_key, 0):
            return None
        if thumbnail is None:
            self._negative_until[track_key] = time.monotonic() + NEGATIVE_RETRY_SECONDS
            return None
        try:
            # A stuck Windows media stream must not freeze the 700ms monitor.
            data = await asyncio.wait_for(read_thumbnail(thumbnail), timeout=1.5)
            cover = self.cache.put(data)
        except (Exception, asyncio.CancelledError) as exc:
            LOG.warning("Reading Spotify SMTC thumbnail failed: %s", exc)
            cover = None
        if cover is None:
            self._negative_until[track_key] = time.monotonic() + NEGATIVE_RETRY_SECONDS
            return None
        self._negative_until.pop(track_key, None)
        self._known[track_key] = cover
        self._known.move_to_end(track_key)
        while len(self._known) > 128:
            self._known.popitem(last=False)
        LOG.info("Spotify cover cached: %s (%s)", cover.image_id[:12], cover.mime_type)
        return cover
