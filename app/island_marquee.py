"""Timing state for desktop-island marquee text (no Qt dependency).

Artist/title loops after reaching the end. A lyric line moves only once,
holds the final character visible, and restarts when the line changes.
"""
from __future__ import annotations

from collections.abc import Sequence

from .models import LyricLine


class MarqueeClock:
    """Tracks animation age without restarting on every UI redraw."""

    def __init__(self) -> None:
        self._text: str | None = None
        self._start: float | None = None

    def reset(self) -> None:
        self._text = None
        self._start = None

    def elapsed(self, text: str, now: float) -> float:
        if text != self._text or self._start is None:
            self._text = text
            self._start = float(now)
        return max(0.0, float(now) - self._start)


def marquee_offset(
    *,
    elapsed: float,
    text_width: float,
    viewport_width: float,
    speed: float = 34.0,
    initial_pause: float = 1.0,
    end_pause: float = 1.2,
    repeat: bool = False,
) -> float:
    """How far the text has scrolled left, in pixels.

    For repeat=False, the position is clamped to the precise overflow so the
    right edge of the lyric remains visible (no gap/blank space).
    For repeat=True, hold at both ends and return to the start of the title.
    """
    width = max(0.0, float(text_width))
    viewport = max(0.0, float(viewport_width))
    if viewport <= 0.0 or width <= viewport:
        return 0.0
    travel = width - viewport
    velocity = max(1.0, float(speed))
    start_hold = max(0.0, float(initial_pause))
    end_hold = max(0.0, float(end_pause))
    travel_time = travel / velocity
    seconds = max(0.0, float(elapsed))
    if repeat:
        seconds %= start_hold + travel_time + end_hold
    if seconds <= start_hold:
        return 0.0
    if seconds >= start_hold + travel_time:
        return travel
    return min(travel, velocity * (seconds - start_hold))


def synced_lyric_window(
    lines: Sequence[LyricLine],
    index: int,
    *,
    offset_ms: int = 0,
    track_duration_ms: int = 0,
) -> tuple[int, int] | None:
    """Return the wall-clock playback interval for one active synced lyric.

    The same offset and first-lyric intro guard as active_lyric_index are used.
    An end time is available from the *next lyric* or from the track duration
    for the final line. Without a usable end timestamp, the caller should use
    the normal single-pass marquee as a fallback.
    """
    if not 0 <= index < len(lines):
        return None
    first_time = max(0, int(lines[0].start_ms))
    shift = int(offset_ms)

    def onset(line_index: int) -> int:
        # The app never highlights lyrics before the original first LRC time,
        # even if the user applies a positive advance offset.
        return max(first_time, int(lines[line_index].start_ms) - shift)

    start_ms = onset(index)
    if index + 1 < len(lines):
        end_ms = onset(index + 1)
    else:
        end_ms = max(0, int(track_duration_ms))
    if end_ms <= start_ms:
        return None
    return start_ms, end_ms


def timed_lyric_offset(
    *,
    elapsed_ms: float,
    duration_ms: float,
    text_width: float,
    viewport_width: float,
    preferred_speed: float = 34.0,
) -> float:
    """Scroll a synced lyric all the way to its end before the next line.

    On slow lines the preferred pixel-per-second speed is preserved. On a fast
    line the speed *increases only as much as necessary* to show the last
    character before its LRC interval ends. The short opening/ending pauses
    automatically shrink when the singer delivers the line quickly.

    Progress is based on Spotify's playback timeline, NOT on time spent since
    the Qt widget last painted. Seeking, pausing, and late lyric lookups are
    therefore handled without accumulating a scrolling time offset.
    """
    width = max(0.0, float(text_width))
    viewport = max(0.0, float(viewport_width))
    if viewport <= 0.0 or width <= viewport:
        return 0.0
    travel = width - viewport
    duration_s = max(0.0, float(duration_ms) / 1000.0)
    if duration_s <= 0.0:
        return 0.0

    # The former one-second opening hold was the main reason that long, fast
    # lines could never reach their end. A 1-second lyric now starts moving
    # after 80 ms, whereas a long, relaxed line can still take 280 ms.
    opening_hold_s = min(0.28, duration_s * 0.08)
    final_hold_s = min(0.22, duration_s * 0.10)
    usable_s = max(0.001, duration_s - opening_hold_s - final_hold_s)
    required_speed = travel / usable_s
    pixels_per_second = max(1.0, float(preferred_speed), required_speed)

    song_elapsed_s = max(0.0, float(elapsed_ms) / 1000.0)
    moving_s = max(0.0, song_elapsed_s - opening_hold_s)
    return min(travel, pixels_per_second * moving_s)


def two_line_vertical_offset(
    *, content_height: float, viewport_height: float, elapsed: float = 0.0,
    elapsed_ms: float | None = None, duration_ms: float | None = None,
) -> float:
    """Reveal 2 rows of the current lyric, scrolling vertically only if needed.

    A short or two-row line stays still. Long lyrics use the same LRC-timed
    adaptive speed as the original single-row marquee, so even fast passages
    reach the final row before the next LRC cue. Without a timestamp we move
    once and stop, never jump back to the top until the lyric changes.
    """
    height = max(0.0, float(content_height))
    viewport = max(0.0, float(viewport_height))
    if viewport <= 0.0 or height <= viewport:
        return 0.0
    if duration_ms is not None and elapsed_ms is not None and duration_ms > 0:
        return timed_lyric_offset(
            elapsed_ms=elapsed_ms, duration_ms=duration_ms,
            text_width=height, viewport_width=viewport, preferred_speed=23.0,
        )
    return marquee_offset(
        elapsed=elapsed, text_width=height, viewport_width=viewport,
        speed=23.0, initial_pause=0.35, end_pause=0.6, repeat=False,
    )
