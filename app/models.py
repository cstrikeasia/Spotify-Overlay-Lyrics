"""UI-independent playback and lyrics data structures."""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic


# User-facing message for a completed lyric lookup without any result.
NO_LYRICS_MESSAGE = "沒有找到歌詞"


@dataclass(frozen=True)
class Playback:
    title: str
    artist: str
    album: str = ""
    duration_ms: int = 0
    position_ms: int = 0
    is_playing: bool = False
    source_app: str = "Spotify"
    cover_id: str = ""  # content digest; empty if SMTC does not supply a thumbnail
    cover_path: str = ""  # user-local on-disk cache path, never sent to OBS
    cover_mime: str = ""
    observed_monotonic: float = field(default_factory=monotonic)

    @property
    def key(self) -> str:
        return "\x1f".join((self.title.strip().casefold(), self.artist.strip().casefold(), self.album.strip().casefold()))

    def position_at(self, now: float | None = None) -> int:
        if now is None:
            now = monotonic()
        elapsed = max(0, int((now - self.observed_monotonic) * 1000)) if self.is_playing else 0
        result = max(0, self.position_ms + elapsed)
        return min(result, self.duration_ms) if self.duration_ms > 0 else result


@dataclass(frozen=True, order=True)
class LyricLine:
    start_ms: int
    text: str


@dataclass(frozen=True)
class LyricsResult:
    kind: str  # synced | plain | instrumental | missing | error
    lines: tuple[LyricLine, ...] = ()
    plain_text: str = ""
    source: str = ""
    message: str = ""

    @property
    def is_synced(self) -> bool:
        return self.kind == "synced" and bool(self.lines)
