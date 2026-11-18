"""Single source of truth for timed lyric highlighting across Qt and OBS."""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence

from .models import LyricLine


def active_lyric_index(lines: Sequence[LyricLine], position_ms: int,
                       offset_ms: int = 0) -> int:
    """Return -1 until the first line actually begins, else the LRC index.

    The user's advance/lag offset still applies to transitions *between*
    lyric lines.  It must not prematurely highlight line zero over a song's
    instrumental intro.  The same rule is used by the desktop and OBS UI.
    """
    if not lines:
        return -1
    elapsed = max(0, int(position_ms))
    if elapsed < lines[0].start_ms:
        return -1
    corrected = max(0, elapsed + int(offset_ms))
    return bisect_right([line.start_ms for line in lines], corrected) - 1
