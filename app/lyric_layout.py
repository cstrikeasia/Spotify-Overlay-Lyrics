"""Small, Qt-independent rules for fitting wrapped lyrics into a viewport.

Only text whose normal size cannot fit is reduced.  The caller supplies its
real Qt text measurement, so language-specific wrapping (CJK, English, etc.)
is handled by the same renderer that ultimately paints the line.
"""
from __future__ import annotations

from typing import Callable


def fit_wrapped_line(
    preferred_size: int,
    available_text_height: int | float,
    measure: Callable[[int], int | float],
    *,
    minimum_size: int = 10,
) -> tuple[int, float]:
    """Return the largest readable font and its measured wrapped height.

    On exceptionally long lines, even the minimum font may be taller than
    the window.  The view provides manual wheel scrolling for that case,
    rather than silently cutting off the remaining words.
    """
    preferred = max(1, int(preferred_size))
    minimum = min(preferred, max(1, int(minimum_size)))
    limit = max(0.0, float(available_text_height))
    for size in range(preferred, minimum - 1, -1):
        measured = max(0.0, float(measure(size)))
        if measured <= limit or size == minimum:
            return size, measured
    raise AssertionError("minimum font size should always be measured")
