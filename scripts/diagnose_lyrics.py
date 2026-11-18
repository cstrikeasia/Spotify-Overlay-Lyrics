"""Inspect which read-only provider returned synchronized lyrics.

No lyric text is printed, saved or exported. Only the track metadata, provider
name, timing count and transient errors are shown.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.lyrics import LyricsService, verified_metadata_queries  # noqa: E402
from app.models import Playback  # noqa: E402
from app.lyric_script import resolved_script_mode, display_lyrics  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description='Read-only synchronized lyric source check')
    parser.add_argument('--title', required=True)
    parser.add_argument('--artist', required=True)
    parser.add_argument('--duration', type=float, default=0,
                        help='Track duration in seconds (0 skips duration validation)')
    parser.add_argument('--lyrics-script', default='auto',
                        choices=('auto', 'traditional', 'simplified', 'original'))
    parser.add_argument('--ui-language', default='zh_TW',
                        choices=('zh_TW', 'zh_CN', 'en', 'ja', 'ko'))
    opts = parser.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format='%(levelname)s %(name)s: %(message)s')
    song = Playback(title=opts.title, artist=opts.artist,
                    duration_ms=max(0, int(opts.duration * 1000)))
    service = LyricsService(ROOT / 'lyrics_diagnostic_local')
    try:
        print('Spotify Overlay Lyrics - online lyric source diagnostic')
        print('Track:', song.title)
        print('Artist:', song.artist)
        print('Duration:', f'{opts.duration:g} seconds' if opts.duration else 'not specified')
        print('Verified metadata search queries:', ' | '.join(verified_metadata_queries(song, limit=5)))
        print('Sources: LRCLIB, QQ, NetEase, Kugou, Migu, LrcAPI, LRCMux, LiriQo')
        print('Display script:', resolved_script_mode(song, opts.lyrics_script, opts.ui_language))
        print('Looking for timestamped lyrics...')
        result = service.fetch(song, force=True)
        print('\nRESULT:', result.kind)
        print('SOURCE:', result.source or '(none)')
        print('TIMED LINES:', len(result.lines))
        print('MESSAGE:', result.message or '(none)')
        if result.is_synced:
            rendered = display_lyrics(result, song, mode=opts.lyrics_script,
                                      ui_language=opts.ui_language)
            print('Conversion altered lines:', sum(a.text != b.text
                  for a, b in zip(result.lines, rendered.lines)))
            print('First timestamp:', f'{result.lines[0].start_ms / 1000:.2f}s')
            print('Matched synchronized lyrics successfully.')
            return 0
        print('A provider may be unavailable or may not have synchronized LRC.')
        print('A local .lrc file with accurate timestamps remains supported.')
        return 2
    finally:
        service._session.close()


if __name__ == '__main__':
    raise SystemExit(main())
