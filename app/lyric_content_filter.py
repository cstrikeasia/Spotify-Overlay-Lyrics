"""Legacy API shim: user disabled ALL lyric-content suppression.

No song title, credit, copyright, non-lyric, or metadata line is filtered.
These functions are deliberately inert for old callers or plugins that still
import the previous module. Normal display uses app.lyric_script directly.
"""
from __future__ import annotations

from .models import LyricLine, LyricsResult, Playback


def is_non_lyric_line(text: str) -> bool:
    """Content classification is disabled; no text is blocked."""
    return False


def filtered_synced_lines(lines: tuple[LyricLine, ...],
                          playback: Playback | None = None) -> tuple[LyricLine, ...]:
    """Return the original synced lines, preserving content and timestamps."""
    return lines


def filter_display_lyrics(result: LyricsResult,
                          playback: Playback | None = None) -> LyricsResult:
    """Return the original result without hiding any metadata or lyric line."""
    return result
