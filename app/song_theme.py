"""Artwork-derived, Spotify-like per-track backgrounds.

Spotify's public track metadata does not expose the exact background color
used by its proprietary lyrics UI. Derive a stable, readable tone from the
already-available local Windows media thumbnail, without network requests.
"""
from __future__ import annotations

import colorsys
from collections import defaultdict
from collections.abc import Iterable

DEFAULT_THEME = ("#303236", "#24262A", "#181A1D")


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{min(255, max(0, int(round(x * 255)))):02X}" for x in rgb)


def theme_from_pixels(pixels: Iterable[tuple[int, int, int]]) -> tuple[str, str, str]:
    """Return upper/mid/lower gradient colors from a small artwork sample.

    Highly saturated colors are preferred without allowing a single vibrant
    pixel to dominate a grayscale album. All returned colors remain dark so
    white synchronized lyric text meets a high contrast threshold.
    """
    groups: dict[int, list[tuple[float, float, float, float]]] = defaultdict(list)
    for r, g, b in pixels:
        r, g, b = (max(0, min(255, int(x))) for x in (r, g, b))
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        if s < 0.20 or v < 0.16:
            continue
        # Discard near-white and very dark pixels; Spotify backgrounds tend to
        # use the most visually meaningful shade of the album, not its border.
        weight = (0.32 + 0.68 * s) * (0.5 + 0.5 * min(1.0, v / 0.65))
        groups[int(h * 18) % 18].append((h, s, v, weight))
    if not groups:
        return DEFAULT_THEME
    ranked = sorted(groups.values(), key=lambda group: sum(p[3] for p in group), reverse=True)
    strongest = ranked[0]
    if len(strongest) < 3:
        return DEFAULT_THEME
    weight_sum = sum(p[3] for p in strongest)
    # Circular mean keeps hue stable around the red 0/360-degree boundary.
    import math
    sin = sum(math.sin(p[0] * math.tau) * p[3] for p in strongest)
    cos = sum(math.cos(p[0] * math.tau) * p[3] for p in strongest)
    hue = (math.atan2(sin, cos) / math.tau) % 1.0
    saturation = sum(p[1] * p[3] for p in strongest) / weight_sum
    saturation = min(0.54, max(0.30, saturation * 0.75))
    return tuple(_hex(colorsys.hls_to_rgb(hue, lightness, saturation))
                 for lightness in (0.26, 0.20, 0.13))
