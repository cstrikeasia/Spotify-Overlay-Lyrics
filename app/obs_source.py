"""A loopback-only OBS Browser Source with live synced lyrics.

Unlike OS-level Window Capture, OBS Browser Sources are independent of
Qt.Tool, layered/translucent HWNDs, window z-order, and GPU capture method.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .artwork import MAX_ARTWORK_BYTES, image_mime
from .models import NO_LYRICS_MESSAGE, LyricsResult, Playback
from .lyric_timeline import active_lyric_index

LOG = logging.getLogger(__name__)
DEFAULT_OBS_PORT = 38571

# Browser code is a Vue 3 + Tailwind CSS application. The prebuilt assets
# are distributed with the Python app (including inside the Windows EXE), so
# OBS continues working offline without Node, a CDN, or a dev server.
_WEB_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "frontend" / "dist"
_ASSET_URL_RE = re.compile(r"(?:src|href)=[\"'](/assets/[A-Za-z0-9._-]+\.(?:js|css))[\"']")


def load_frontend_assets(root: Path | None = None) -> tuple[bytes, dict[str, tuple[bytes, str]]]:
    """Validate the offline OBS bundle when OBS actually starts.

    A missing *optional* frontend must never prevent the desktop Qt
    application from importing. Every HTML-referenced asset must exist;
    otherwise OBS gets disabled with a useful error instead of a broken page.
    """
    web_root = root if root is not None else _WEB_ROOT
    index = web_root / "index.html"
    if not index.is_file():
        raise FileNotFoundError(
            "OBS 前端檔案遺失 (frontend/dist/index.html)。請重新解壓縮完整 ZIP；"
            "若從原始碼開發，請執行 cd frontend && npm run build。"
        )
    html = index.read_bytes()
    text = html.decode("utf-8")
    if any(token in text for token in ("__OVERLAY_CSS__", "__VUE_JS__", "__OVERLAY_JS__")):
        raise RuntimeError("OBS 前端尚未完成編譯，請重新解壓縮完整 ZIP。")
    urls = set(_ASSET_URL_RE.findall(text))
    if len(urls) < 3:
        raise RuntimeError("OBS 前端檔案不完整：缺少 Vue、CSS 或應用程式腳本。")
    assets: dict[str, tuple[bytes, str]] = {}
    for url in urls:
        # Asset names are constrained to a single filename by _ASSET_URL_RE;
        # never serve or read arbitrary filesystem paths from HTML.
        asset = web_root / "assets" / url.rsplit("/", 1)[-1]
        if not asset.is_file():
            raise FileNotFoundError(f"OBS 前端缺少編譯資源：{url}。請重新解壓縮完整 ZIP。")
        content_type = (
            "application/javascript" if asset.suffix == ".js" else "text/css"
        ) + "; charset=utf-8"
        assets[url] = (asset.read_bytes(), content_type)
    return html, assets


def make_overlay_state(
    playback: Playback | None,
    lyrics: LyricsResult,
    *,
    font_size: int = 30,
    line_count: int = 5,
    offset_ms: int = 0,
    smooth_lyrics: bool = True,
    transition_ms: int = 620,
    show_cover: bool = False,
    message: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Produce a JSON-safe state object using the same timeline as Qt UI."""
    state: dict[str, Any] = {
        "kind": "missing",
        "title": playback.title if playback else "",
        "artist": playback.artist if playback else "",
        "is_playing": playback.is_playing if playback else False,
        "duration_ms": max(0, playback.duration_ms) if playback else 0,
        # Wall-time position is the real media timeline, NOT the +300ms
        # presentation offset used only for highlighting the next lyric.
        "position_ms": playback.position_at(now) if playback else 0,
        "track_key": playback.key if playback else "",
        "show_cover": bool(show_cover),
        "cover_url": (f"/cover/{playback.cover_id}"
                      if playback and re.fullmatch(r"[0-9a-f]{64}", playback.cover_id) else ""),
        "active_index": -1,
        "smooth_lyrics": bool(smooth_lyrics),
        "transition_ms": max(200, min(1200, int(transition_ms))),
        "font_size": max(16, min(64, int(font_size))),
        "line_count": 3 if line_count == 3 else 5,
        "lines": [],
        "message": message if message is not None else ("等待 Spotify 播放..." if playback is None else NO_LYRICS_MESSAGE),
    }
    if playback is None:
        return state
    if lyrics.is_synced:
        # Preserve -1 through the SSE payload: it means the song is still in
        # its intro. The first lyric must not light up before its real LRC time.
        active = active_lyric_index(lyrics.lines, state["position_ms"], offset_ms)
        state["kind"] = "synced"
        state["active_index"] = active
        state["message"] = ""
        state["lines"] = [
            {
                "index": index,
                "distance": index - active,
                "text": lyrics.lines[index].text,
            }
            for index in range(max(0, active - 3), min(len(lyrics.lines), active + 4))
        ]
    elif lyrics.kind == "plain":
        # Defensive fallback for stale/plain-only results. The browser source
        # never receives untimed text, even if one slips past the lookup layer.
        state["message"] = NO_LYRICS_MESSAGE
    elif lyrics.kind == "instrumental":
        state["kind"] = "instrumental"
        state["message"] = lyrics.message or "純音樂（無歌詞）"
    elif lyrics.kind == "missing":
        # Completed missing lookup uses the same placeholder as Windows.
        # A caller-provided loading message is allowed during the lookup.
        if message is None or not message:
            state["message"] = NO_LYRICS_MESSAGE
    elif lyrics.message and not message:
        state["message"] = lyrics.message
    return state


class _HttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class ObsOverlayServer:
    """Push OBS lyric changes over one localhost-only SSE connection per client.

    The desktop Qt timer continues to update the local display, but publishes
    are deduplicated: network clients only receive a new event when the visible
    lyric state actually changes. No third-party WebSocket dependencies.
    """

    # A comment-only event keeps proxies and OBS' embedded Chromium connection
    # alive without rerendering or sending duplicate song data.
    HEARTBEAT_SECONDS = 18.0

    def __init__(self, port: int = DEFAULT_OBS_PORT, *, host: str = "127.0.0.1") -> None:
        self.host = host
        self.port = port
        self._condition = threading.Condition()
        self._state: dict[str, Any] = make_overlay_state(None, LyricsResult(kind="missing"))
        self._state_sent_at = time.monotonic()
        self._revision = 0
        self._stopping = False
        self._httpd: _HttpServer | None = None
        self._thread: threading.Thread | None = None
        self._artwork: OrderedDict[str, tuple[bytes, str]] = OrderedDict()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/overlay"

    def publish(self, state: dict[str, Any]) -> None:
        with self._condition:
            if state == self._state:
                return
            now = time.monotonic()
            # The desktop refreshes roughly every 120 ms. Adding playback
            # position to the payload must not turn SSE into an 8 Hz stream.
            # One client-side animation handles the uninterrupted timeline;
            # only changes in visible state, a seek, or occasional clock
            # resynchronisation should emit a new event.
            stable_fields = set(state) | set(self._state)
            stable_fields.discard("position_ms")
            changed = any(state.get(k) != self._state.get(k) for k in stable_fields)
            if not changed:
                old_pos = int(self._state.get("position_ms", 0))
                old_duration = int(self._state.get("duration_ms", 0))
                expected = old_pos + (max(0.0, now - self._state_sent_at) * 1000
                                      if state.get("is_playing") else 0)
                if old_duration > 0:
                    expected = min(expected, old_duration)
                seeked = abs(int(state.get("position_ms", 0)) - expected) > 1000
                needs_sync = (bool(state.get("is_playing")) and
                              now - self._state_sent_at >= 12.0)
                if not (seeked or needs_sync):
                    return
            self._state = deepcopy(state)
            self._state_sent_at = now
            self._revision += 1
            self._condition.notify_all()

    def register_artwork(self, image_id: str, contents: bytes, mime_type: str) -> bool:
        """Register verified local image bytes for the localhost image endpoint.

        Cover files are never sent through the SSE state or read by arbitrary
        path. Only a content hash can select an image. Keep a small LRU so an
        in-flight OBS image request can finish just after a track change.
        """
        if (not re.fullmatch(r"[0-9a-f]{64}", image_id or "")
                or not contents or len(contents) > MAX_ARTWORK_BYTES
                or image_mime(contents) != mime_type
                or hashlib.sha256(contents).hexdigest() != image_id):
            return False
        with self._condition:
            self._artwork[image_id] = (contents, mime_type)
            self._artwork.move_to_end(image_id)
            while len(self._artwork) > 8:
                self._artwork.popitem(last=False)
        return True

    def _lookup_artwork(self, image_id: str) -> tuple[bytes, str] | None:
        with self._condition:
            return self._artwork.get(image_id)

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return deepcopy(self._state)

    def _wait_for_change(self, revision: int, timeout: float) -> tuple[int, dict[str, Any], bool]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._stopping and self._revision == revision:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            state = deepcopy(self._state)
            if revision == -1 and state.get("is_playing"):
                # A newly connected OBS source needs a CURRENT position even
                # when the last event was sent several seconds ago. Interpolate
                # for this client without generating additional SSE messages.
                position = int(state.get("position_ms", 0) +
                               max(0.0, time.monotonic() - self._state_sent_at) * 1000)
                duration = int(state.get("duration_ms", 0))
                state["position_ms"] = min(position, duration) if duration > 0 else position
            return self._revision, state, self._stopping

    def start(self) -> None:
        if self._httpd is not None:
            return
        # Resolve assets lazily: a missing OBS bundle must not abort the app.
        overlay_html, frontend_assets = load_frontend_assets()
        with self._condition:
            self._stopping = False
        controller = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                # Localhost only and no CORS. Prevent DNS rebinding from using
                # a malicious Host header to read song metadata.
                valid_hosts = {
                    f"{controller.host}:{controller.port}",
                    f"localhost:{controller.port}",
                }
                if self.headers.get("Host", "") not in valid_hosts:
                    self._send(403, b"Forbidden", "text/plain; charset=utf-8")
                    return
                path = urlsplit(self.path).path
                if path in ("/", "/overlay"):
                    self._send(200, overlay_html, "text/html; charset=utf-8")
                elif path in frontend_assets:
                    asset_data, content_type = frontend_assets[path]
                    self._send(200, asset_data, content_type,
                               cache_control="private, max-age=31536000, immutable")
                elif re.fullmatch(r"/cover/[0-9a-f]{64}", path):
                    stored = controller._lookup_artwork(path[len("/cover/"):])
                    if stored is None:
                        self._send(404, b"Not Found", "text/plain; charset=utf-8")
                    else:
                        self._send(200, stored[0], stored[1], cache_control="private, max-age=86400, immutable")
                elif path == "/events":
                    self._stream_events()
                elif path == "/state":
                    # Kept for backwards compatibility and manual diagnostics.
                    # The overlay page itself never fetches this endpoint.
                    body = json.dumps(controller.snapshot(), ensure_ascii=False).encode("utf-8")
                    self._send(200, body, "application/json; charset=utf-8")
                else:
                    self._send(404, b"Not Found", "text/plain; charset=utf-8")

            def _stream_events(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "keep-alive")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                # A reconnection always receives the latest snapshot first,
                # including an initially idle/no-song state.
                try:
                    self._write_event_chunk(b"retry: 1500\n\n")
                    last_seen = -1
                    while True:
                        revision, state, stopping = controller._wait_for_change(
                            last_seen, controller.HEARTBEAT_SECONDS
                        )
                        if stopping:
                            break
                        if revision != last_seen:
                            payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
                            frame = f"id: {revision}\nevent: state\ndata: {payload}\n\n".encode("utf-8")
                            last_seen = revision
                        else:
                            frame = b": keep-alive\n\n"
                        self._write_event_chunk(frame)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, TimeoutError, OSError):
                    # Normal: OBS may close a browser source during scene changes.
                    pass
                finally:
                    self.close_connection = True

            def _write_event_chunk(self, frame: bytes) -> None:
                # Proper HTTP/1.1 streaming framing; OBS Chromium and proxies
                # can process every event immediately without waiting for EOF.
                self.wfile.write(f"{len(frame):X}\r\n".encode("ascii"))
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                self.wfile.flush()

            def _send(self, status: int, body: bytes, content_type: str,
                      *, cache_control: str = "no-store") -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", cache_control)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "SAMEORIGIN")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                LOG.debug("OBS HTTP %s", format % args)

        # Bind synchronously: report a port conflict immediately in the UI.
        server = _HttpServer((self.host, self.port), Handler)
        self.port = server.server_address[1]  # supports port=0 for tests
        self._httpd = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="ObsLyricsBrowserSource",
            kwargs={"poll_interval": 0.15},
            daemon=True,
        )
        self._thread.start()
        LOG.info("OBS browser source (SSE push) available at %s (loopback only)", self.url)

    def stop(self) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        server, self._httpd = self._httpd, None
        thread, self._thread = self._thread, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
