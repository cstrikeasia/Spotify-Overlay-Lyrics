"""Stable text content for the two-line, collapsed desktop island."""
from __future__ import annotations

from .i18n import tr
from .models import Playback


def compact_display_lines(
    playback: Playback | None,
    *,
    current_lyric: str,
    lyrics_kind: str,
    show_lyrics: bool,
) -> tuple[str, str]:
    """Return (artist - title, current lyric) for the unexpanded view.

    Never repeat the title as a fake lyric during the intro. Respect the
    existing setting that lets users hide lyrics. Painting handles ellipsis.
    """
    if playback is None:
        return tr("等待 Spotify 播放"), tr("播放後自動顯示歌曲資訊")

    artist = (playback.artist or "").strip()
    title = (playback.title or "").strip()
    if artist and title:
        metadata = f"{artist} - {title}"
    else:
        metadata = artist or title or tr("等待 Spotify 播放")

    if not show_lyrics:
        return metadata, ""
    if current_lyric.strip():
        return metadata, current_lyric.strip()

    # An instrumental, unavailable lyrics or loading status must not be
    # mistaken for the lyrics of the actual track.
    fallback = {
        "instrumental": tr("純音樂，無同步歌詞"),
        "missing": "",
        "error": tr("歌詞暫時無法載入"),
        "plain": "",
    }
    return metadata, fallback.get(lyrics_kind, "")
