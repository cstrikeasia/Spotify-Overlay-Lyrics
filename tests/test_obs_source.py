"""OBS integration checks runnable without PySide6 or Windows."""
from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from app.models import LyricLine, LyricsResult, Playback
from app.obs_source import DEFAULT_OBS_PORT, ObsOverlayServer, make_overlay_state
from tests.overlay_vue_harness import bundle_source
from app.settings import Settings, load_settings, save_settings


def _playback() -> Playback:
    return Playback(title='測試歌曲 <b>XSS</b>', artist='歌手',
                    duration_ms=15000, position_ms=3500,
                    is_playing=True, observed_monotonic=100)


def _synced() -> LyricsResult:
    return LyricsResult(kind='synced', lines=(
        LyricLine(0, '第一行'), LyricLine(3000, '第二行'),
        LyricLine(6500, '第三行'), LyricLine(11000, '第四行'),
    ), source='Test')


def test_state_tracks_synchronised_position_and_offset() -> None:
    item = _playback()
    state = make_overlay_state(item, _synced(), now=100,
                               line_count=3, offset_ms=0, font_size=27)
    assert state['kind'] == 'synced'
    assert state['title'] == item.title
    assert state['font_size'] == 27
    assert state['line_count'] == 3
    assert state['active_index'] == 1
    assert state['lines'][1] == {'index': 1, 'distance': 0, 'text': '第二行'}
    assert state['track_key'] == item.key
    assert state['smooth_lyrics'] is True
    assert state['transition_ms'] == 620
    advanced = make_overlay_state(item, _synced(), now=101,
                                  offset_ms=2000)
    assert advanced['active_index'] == 2
    assert next(line for line in advanced['lines'] if line['distance'] == 0)['text'] == '第三行'
    paused = Playback('One', 'Two', position_ms=6900, is_playing=False, observed_monotonic=100)
    assert make_overlay_state(paused, _synced(), now=200)['active_index'] == 2


def test_idle_plain_and_instrumental_states() -> None:
    assert make_overlay_state(None, _synced())['title'] == ''
    state = make_overlay_state(_playback(), LyricsResult(kind='plain',
        plain_text='one\ntwo\nthree\nfour\nfive'), now=100)
    assert state['kind'] == 'missing'
    assert state['message'] == '沒有找到歌詞'
    assert state['lines'] == []
    state = make_overlay_state(_playback(), LyricsResult(kind='instrumental'), now=100)
    assert '純音樂' in state['message']


def test_obs_preferences_are_persistent_and_bounded(tmp_path: Path) -> None:
    path = tmp_path / 'obs-settings.json'
    s = Settings(obs_browser_enabled=False, obs_window_capture=True, obs_browser_port=90000)
    save_settings(s, path)
    loaded = load_settings(path)
    assert loaded.obs_browser_enabled is False
    assert loaded.obs_window_capture is True
    assert loaded.obs_browser_port == 65535
    s.obs_browser_port = 1
    save_settings(s, path)
    assert load_settings(path).obs_browser_port == 1024
    assert DEFAULT_OBS_PORT == Settings().obs_browser_port


def test_obs_http_serves_transparent_page_and_live_json() -> None:
    server = ObsOverlayServer(port=0)
    try:
        server.start()
        assert server._httpd is not None
        assert server._httpd.server_address[0] == '127.0.0.1'
        assert server.url.startswith('http://127.0.0.1:')
        with urllib.request.urlopen(server.url, timeout=3) as response:
            html = response.read().decode('utf-8')
            assert response.status == 200
            assert response.headers['Content-Type'].startswith('text/html')
            assert response.headers['Cache-Control'] == 'no-store'
            assert '<div id="app"' in html
            assert 'v-show="ui.trackVisible"' in html
            assert 'id="playback-progress"' in html
            assert 'https://' not in html
            bundles = re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)
            assert len(bundles) == 3  # Vue, Tailwind CSS, app JS
            for asset in bundles:
                with urllib.request.urlopen(server.url.replace('/overlay', asset), timeout=3) as response:
                    assert response.status == 200
                    assert response.headers['Cache-Control'].endswith('immutable')
                    assert 'Access-Control-Allow-Origin' not in response.headers
                    assert response.headers['Content-Type'].startswith(
                        'text/css' if asset.endswith('.css') else 'application/javascript')
            js = bundle_source('.js')
            css = bundle_source('.css')
            assert "new EventSource('/events')" in js
            assert "stream.addEventListener('state'" in js
            assert 'setInterval(update' not in js
            assert 'fetch(' not in js
            assert 'node.textContent' in js
            assert 'const rendered = new Map()' in js
            assert 'transition: transform var(--motion)' in css
            assert 'font-size var(--motion)' not in css
            assert 'transform-origin: center center' in css
            assert 'rendered.get(item.index).scrollHeight' in js
            assert 'const centers = new Map()' in js
            assert 'lines.replaceChildren()' not in js
            assert 'innerHTML' not in js  # dynamic song content is Vue escaped text
        state = make_overlay_state(_playback(), _synced(), now=100)
        server.publish(state)
        with urllib.request.urlopen(server.url.replace('/overlay', '/state'), timeout=3) as response:
            result = json.load(response)
            assert result == state
            assert 'Access-Control-Allow-Origin' not in response.headers
        state['title'] = 'After mutation'
        assert server.snapshot()['title'] == _playback().title
    finally:
        server.stop()


def test_obs_http_rejects_unknown_paths_and_malicious_host() -> None:
    server = ObsOverlayServer(port=0)
    try:
        server.start()
        with urllib.request.urlopen(server.url.replace('/overlay', '/'), timeout=3) as response:
            assert response.status == 200
        try:
            urllib.request.urlopen(server.url.replace('/overlay', '/secret'), timeout=3)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError('Unknown route should return HTTP 404')
        conn = http.client.HTTPConnection('127.0.0.1', server.port, timeout=3)
        conn.putrequest('GET', '/state', skip_host=True)
        conn.putheader('Host', 'evil.example')
        conn.endheaders()
        response = conn.getresponse()
        assert response.status == 403
        conn.close()
    finally:
        server.stop()


def test_obs_server_can_restart_after_shutdown() -> None:
    server = ObsOverlayServer(port=0)
    server.start()
    port = server.port
    server.stop()
    again = ObsOverlayServer(port=port)
    try:
        again.start()
        with urllib.request.urlopen(again.url, timeout=3) as response:
            assert response.status == 200
    finally:
        again.stop()


def test_window_compatibility_flag_and_tray_copy_are_wired() -> None:
    code = (Path(__file__).resolve().parents[1] / 'app' / 'ui.py').read_text(encoding='utf-8')
    # Normal capture mode remains a Window; game mode forces a Tool overlay.
    assert 'else Qt.WindowType.Window)' in code
    assert 'game = self.settings.game_overlay_enabled and not self._game_overlay_suspended' in code
    assert 'self._publish_obs_state()' in code
    assert 'self.action_copy_obs.triggered.connect(self._copy_obs_url)' in code
    assert 'self.obs_server.stop()' in code


def test_obs_state_keeps_stable_indices_during_seek_and_clamps_preferences() -> None:
    item = _playback()
    backwards = make_overlay_state(item, _synced(), now=100, offset_ms=-10000,
                                   smooth_lyrics=False, transition_ms=99999)
    assert backwards['active_index'] == 0
    assert backwards['lines'][0]['index'] == 0
    assert backwards['lines'][0]['distance'] == 0
    assert backwards['transition_ms'] == 1200
    assert backwards['smooth_lyrics'] is False
    end = make_overlay_state(item, _synced(), now=300)
    assert end['active_index'] == 3
    assert end['lines'][-1]['index'] == 3
    assert len(end['lines']) <= 7


def test_new_animation_preferences_persist_backward_compatibly(tmp_path: Path) -> None:
    path = tmp_path / 'settings.json'
    path.write_text('{"font_size": 34, "line_count": 3}', encoding='utf-8')
    old = load_settings(path)
    assert old.smooth_lyrics is True
    assert old.transition_ms == 620
    old.smooth_lyrics = False
    old.transition_ms = 2000
    save_settings(old, path)
    new = load_settings(path)
    assert new.font_size == 34
    assert new.line_count == 3
    assert new.smooth_lyrics is False
    assert new.transition_ms == 1200


def test_desktop_uses_persistent_smooth_canvas() -> None:
    folder = Path(__file__).resolve().parents[1] / 'app'
    ui = (folder / 'ui.py').read_text(encoding='utf-8')
    canvas = (folder / 'animated_lyrics.py').read_text(encoding='utf-8')
    assert 'self.lyric_view = AnimatedLyricsView()' in ui
    assert 'self.lyric_view.set_active_index(current)' in ui
    assert 'self.lyric_view.set_lyrics(result.lines)' in ui
    assert 'QPropertyAnimation' in canvas
    assert 'QEasingCurve.Type.OutCubic' in canvas
    assert 'abs(target_index - previous) <= 2' in canvas
