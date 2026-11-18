"""Read Spotify Desktop state via native Windows media sessions (SMTC)."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import threading
import time
from collections.abc import Callable
from queue import Empty, Queue
from dataclasses import replace

from .artwork import ArtworkResolver
from .media_events import MediaEventWake
from .settings import data_dir

from .models import Playback

LOG = logging.getLogger(__name__)


def _ms(delta: object) -> int:
    if isinstance(delta, dt.timedelta):
        return int(delta.total_seconds() * 1000)
    if hasattr(delta, "total_seconds"):
        return int(delta.total_seconds() * 1000)
    return 0


def corrected_position(base_ms: int, last_updated: object, is_playing: bool, now: dt.datetime | None = None) -> int:
    """SMTC position is often timestamped a little before it is read."""
    if not is_playing or not isinstance(last_updated, dt.datetime):
        return max(0, base_ms)
    now = now or dt.datetime.now(dt.timezone.utc)
    if last_updated.tzinfo is None:
        last_updated = last_updated.replace(tzinfo=dt.timezone.utc)
    drift = (now - last_updated).total_seconds()
    if 0 < drift < 8:
        return max(0, base_ms + int(drift * 1000))
    return max(0, base_ms)


async def read_spotify(manager: object, *, artwork_resolver: ArtworkResolver | None = None,
                       on_basic: Callable[[Playback], None] | None = None) -> Playback | None:
    """Select only a Spotify session so browser videos never appear as music."""
    try:
        import winrt.windows.media.control as media
        sessions = [s for s in manager.get_sessions() if "spotify" in str(s.source_app_user_model_id).casefold()]
    except Exception:
        LOG.exception("Reading Windows media sessions failed")
        return None
    if not sessions:
        return None

    playing_status = media.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
    sessions.sort(key=lambda s: s.get_playback_info().playback_status == playing_status, reverse=True)
    for session in sessions:
        try:
            props = await session.try_get_media_properties_async()
            if props is None or not (props.title or "").strip():
                continue
            playback_info = session.get_playback_info()
            is_playing = playback_info.playback_status == playing_status
            timeline = session.get_timeline_properties()
            duration_ms = max(0, _ms(timeline.end_time) - _ms(timeline.start_time)) if timeline else 0
            base_ms = max(0, _ms(timeline.position) - _ms(timeline.start_time)) if timeline else 0
            position = corrected_position(base_ms, timeline.last_updated_time, is_playing) if timeline else 0
            if duration_ms:
                position = min(duration_ms, position)
            item = Playback(
                title=(props.title or "").strip(),
                artist=(props.artist or "").strip(),
                album=(props.album_title or "").strip(),
                duration_ms=duration_ms,
                position_ms=position,
                is_playing=is_playing,
                source_app=str(session.source_app_user_model_id),
                observed_monotonic=time.monotonic(),
            )
            if artwork_resolver is not None:
                # Publish a new track BEFORE any potentially slow cover read so
                # lyrics can start looking up immediately on the Qt thread.
                if on_basic is not None and artwork_resolver.get_cached(item.key) is None:
                    on_basic(item)
                # Read only when new or when Windows publishes a late cover.
                cover = await artwork_resolver.resolve(item.key, getattr(props, "thumbnail", None))
                if cover is not None:
                    item = replace(item, cover_id=cover.image_id,
                                   cover_path=str(cover.path), cover_mime=cover.mime_type)
            return item
        except Exception:
            LOG.exception("Reading one Spotify session failed")
    return None


# Local Windows SMTC controls do not require Spotify Web API/OAuth/Premium.
# The desktop client must expose the relevant Windows transport capability.
_TRANSPORT_METHODS = {
    "play": "try_play_async",
    "pause": "try_pause_async",
    "next": "try_skip_next_async",
    "previous": "try_skip_previous_async",
}


async def control_spotify(manager: object, action: str,
                          position_ms: int | None = None) -> tuple[bool, str]:
    """Execute transport or a timeline seek on a Spotify SMTC session only.

    WinRT's TryChangePlaybackPositionAsync accepts a *100-nanosecond tick*
    count, not milliseconds. The value is also on the media session timeline,
    so account for a possible nonzero start_time when seeking.
    """
    method_name = _TRANSPORT_METHODS.get(action)
    if method_name is None and action != "seek":
        return False, "Unsupported command"
    try:
        sessions = [s for s in manager.get_sessions()
                    if "spotify" in str(s.source_app_user_model_id).casefold()]
        if not sessions:
            return False, "No active Spotify media session"
        # Prefer the currently playing desktop session when multiple clients
        # are registered. Do not target unrelated players or browser tabs.
        sessions.sort(key=lambda s: str(s.get_playback_info().playback_status).casefold().endswith("playing"),
                      reverse=True)
        session = sessions[0]
        if action == "seek":
            if not isinstance(position_ms, int) or isinstance(position_ms, bool) or position_ms < 0:
                return False, "Invalid seek position"
            info = session.get_playback_info()
            controls = getattr(info, "controls", None)
            if controls is not None and getattr(controls, "is_playback_position_enabled", True) is False:
                return False, "Playback seeking unsupported"
            timeline = session.get_timeline_properties()
            if timeline is None:
                return False, "Playback timeline unavailable"
            start_ms = _ms(timeline.start_time)
            duration_ms = _ms(timeline.end_time) - start_ms
            if duration_ms <= 0:
                return False, "Playback duration unavailable"
            requested_ms = start_ms + min(position_ms, duration_ms)
            min_seek = _ms(getattr(timeline, "min_seek_time", None))
            max_seek = _ms(getattr(timeline, "max_seek_time", None))
            if max_seek > min_seek:
                requested_ms = max(min_seek, min(requested_ms, max_seek))
            method_name = "try_change_playback_position_async"
        method = getattr(session, method_name, None)
        if method is None:
            return False, "Playback seeking unsupported" if action == "seek" else "Playback control unsupported"
        accepted = await method(requested_ms * 10_000) if action == "seek" else await method()
        return (True, "") if accepted else (False, "Spotify declined the command")
    except Exception as exc:
        LOG.warning("Spotify transport request %s failed: %s", action, exc)
        return False, str(exc)


class MediaMonitor(threading.Thread):
    """Own an asyncio event loop in a daemon thread for WinRT calls."""

    def __init__(self, on_playback: Callable[[Playback | None], None], on_error: Callable[[str], None],
                 on_control: Callable[[str, bool, str], None] | None = None):
        super().__init__(daemon=True, name="SpotifyMediaMonitor")
        self._on_playback = on_playback
        self._on_error = on_error
        self._on_control = on_control or (lambda _command, _ok, _message: None)
        self._commands: Queue[tuple[str, int | None]] = Queue(maxsize=12)
        self._stopping = threading.Event()
        self._wake = threading.Event()
        self._artwork_resolver = ArtworkResolver(data_dir() / "cover_cache")

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()

    def submit_control(self, action: str) -> bool:
        """Queue a command for execution on the monitor's WinRT event loop."""
        if action not in _TRANSPORT_METHODS or self._stopping.is_set():
            return False
        try:
            self._commands.put_nowait((action, None))
        except Exception:  # Queue overflow; do not block the GUI thread.
            return False
        self._wake.set()
        return True

    def submit_seek(self, position_ms: int) -> bool:
        """Queue exactly one requested position when the user releases the scrubber."""
        if (not isinstance(position_ms, int) or isinstance(position_ms, bool)
                or position_ms < 0 or self._stopping.is_set()):
            return False
        try:
            self._commands.put_nowait(("seek", position_ms))
        except Exception:  # A full command queue must not stall the UI.
            return False
        self._wake.set()
        return True

    def run(self) -> None:
        try:
            import winrt.windows.media.control as media
        except ImportError:
            self._on_error("缺少 Windows 媒體支援套件，請重新執行啟動腳本以安裝依賴")
            return
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        manager = None
        events = MediaEventWake(self._wake)
        last_basic_key = None
        def publish_early(item: Playback) -> None:
            nonlocal last_basic_key
            if item.key != last_basic_key:
                last_basic_key = item.key
                self._on_playback(item)
        try:
            while not self._stopping.is_set():
                if manager is None:
                    try:
                        manager = loop.run_until_complete(
                            media.GlobalSystemMediaTransportControlsSessionManager.request_async()
                        )
                        events.attach(manager)
                        self._on_error("")
                    except Exception as exc:
                        LOG.warning("Could not get Windows media manager: %s", exc)
                        self._on_error("無法存取 Windows 媒體工作階段；請確認系統為 Windows 10/11")
                        self._stopping.wait(5.0)
                        continue
                try:
                    while True:
                        try:
                            command, position_ms = self._commands.get_nowait()
                        except Empty:
                            break
                        success, message = loop.run_until_complete(
                            control_spotify(manager, command, position_ms))
                        self._on_control(command, success, message)
                    events.refresh()
                    item = loop.run_until_complete(read_spotify(
                        manager, artwork_resolver=self._artwork_resolver,
                        on_basic=publish_early,
                    ))
                    if item is None:
                        last_basic_key = None
                    self._on_playback(item)
                except Exception as exc:
                    LOG.exception("SMTC polling failed: %s", exc)
                    events.close()
                    manager = None
                    self._on_error("Windows 媒體偵測暫時失敗，正在重試")
                # A media/timeline event wakes immediately, while polling every
                # 700ms remains a fallback for clients that do not emit WinRT
                # notifications. Coalesce rapid callbacks into one media read.
                if self._wake.wait(0.7):
                    self._wake.clear()
                    if not self._stopping.is_set():
                        self._stopping.wait(0.055)
        finally:
            events.close()
            try:
                loop.close()
            except Exception:
                pass
