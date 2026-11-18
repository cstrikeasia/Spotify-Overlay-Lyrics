"""Non-destructive Simplified/Traditional Chinese rendering of synced lyrics.

Network/local LRC and service caches remain unmodified. Only the copy of the
LyricsResult passed to the desktop, island, and OBS is transformed, preserving
original timestamps and metadata. Japanese (kana) lines are never converted.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from functools import lru_cache

from .models import LyricsResult, Playback

LOG = logging.getLogger(__name__)
SCRIPT_OPTIONS = frozenset({"auto", "traditional", "simplified", "original"})


@lru_cache(maxsize=2)
def _converter(config: str):
    try:
        from opencc import OpenCC
        return OpenCC(config)
    except (ImportError, ValueError, OSError) as exc:
        LOG.warning("OpenCC %s unavailable; leaving original lyric text intact: %s", config, exc)
        return None


def _has_chinese(text: str) -> bool:
    return any('\u3400' <= c <= '\u9fff' for c in text)


def _has_japanese_kana(text: str) -> bool:
    return any('\u3040' <= c <= '\u30ff' for c in text)


def _metadata_script(metadata: str) -> str | None:
    """Identify script-specific characters, excluding ambiguous shared Hanzi.

    Do not infer simplified/traditional from lyric providers: the very problem
    being solved is that their lyric script may disagree with Spotify metadata.
    """
    s2t, t2s = _converter('s2t'), _converter('t2s')
    if s2t is None or t2s is None:
        return None
    traditional, simplified = 0, 0
    for char in set(metadata):
        if not _has_chinese(char):
            continue
        if t2s.convert(char) != char:
            traditional += 1
        if s2t.convert(char) != char:
            simplified += 1
    if traditional > simplified:
        return 'traditional'
    if simplified > traditional:
        return 'simplified'
    return None


def resolved_script_mode(playback: Playback | None, mode: str = 'auto',
                         ui_language: str = 'zh_TW') -> str:
    """Keep the original on non-Chinese songs; prefer Spotify metadata for CJK.

    UI language serves only as a tie-breaker for Chinese titles/artist names
    composed entirely of characters shared by the two scripts.
    """
    if mode not in SCRIPT_OPTIONS:
        mode = 'auto'
    if mode != 'auto':
        return mode
    if playback is None:
        return 'original'
    metadata = f'{playback.title} {playback.artist}'
    if not _has_chinese(metadata) or _has_japanese_kana(metadata):
        return 'original'
    detected = _metadata_script(metadata)
    if detected:
        return detected
    if ui_language == 'zh_TW':
        return 'traditional'
    if ui_language == 'zh_CN':
        return 'simplified'
    return 'original'


def display_lyrics(result: LyricsResult, playback: Playback | None,
                   *, mode: str = 'auto', ui_language: str = 'zh_TW') -> LyricsResult:
    """Return a transformed *copy*, without altering service cache or timing."""
    # No content-based removal: retain every lyric line (including song-title
    # headings, contributor credits, and rights notices) exactly as provided.
    # Script conversion below is optional and does not remove timestamps.
    selected = resolved_script_mode(playback, mode, ui_language)
    if selected not in {'traditional', 'simplified'}:
        return result
    converter = _converter('s2tw' if selected == 'traditional' else 't2s')
    if converter is None:
        return result

    @lru_cache(maxsize=2048)
    def convert_line(text: str) -> str:
        if not _has_chinese(text) or _has_japanese_kana(text):
            return text
        return converter.convert(text)

    if result.is_synced:
        converted = tuple(replace(line, text=convert_line(line.text)) for line in result.lines)
        return replace(result, lines=converted)
    if result.kind == 'plain' and result.plain_text:
        converted = '\n'.join(convert_line(line) for line in result.plain_text.split('\n'))
        return replace(result, plain_text=converted)
    return result
