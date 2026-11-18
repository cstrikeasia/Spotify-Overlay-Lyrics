"""Console diagnostic for the real Windows audio meter (no raw audio saved)."""
from __future__ import annotations

import argparse
import time

from app.audio_meter import SpotifyAudioMeter


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=8)
    args = parser.parse_args()
    print('Real audio meter: play music in the Spotify Windows desktop app.')
    meter = SpotifyAudioMeter()
    meter.set_active(True)
    try:
        last_status = ''
        for i in range(max(1, args.seconds * 10)):
            time.sleep(.1)
            sample = meter.snapshot()
            if sample.source != last_status:
                print(f'  source={sample.source}; {sample.detail}')
                last_status = sample.source
            width = round(min(1, sample.level) * 28)
            print(f'  peak {sample.level:.3f} | {"#" * width:<28}', end='\r', flush=True)
        print('\n\n' + meter.diagnostic_text())
    finally:
        meter.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
