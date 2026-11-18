"""Windows audio peak reader for the Spotify dynamic island.

This module never synthesizes waveform values.  On Windows it samples the
Spotify application audio session through IAudioMeterInformation.  If a session
meter cannot be read, it can use the actual default/output endpoint meter as a
clearly identified fallback (that output may include other applications).

COM objects are created, sampled and released on one worker thread.  Qt's GUI
thread only reads the latest snapshot, so enumeration cannot freeze painting.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import logging
import math
import os
import threading
import time
from typing import Any

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class MeterSnapshot:
    level: float = 0.0
    source: str = "initializing"
    detail: str = ""
    spotify_sessions: int = 0
    output_devices: int = 0


class RealPeakMotion:
    """Visual contrast enhancement using *only real* recent peak-meter values.

    Each of the five visible bars is a successive, measured peak sample, not a
    fabricated frequency band or a timer-driven animation. This processor
    makes modest musical dynamics readable without animating during silence.
    """

    def __init__(self, bar_count: int = 5, window_size: int = 28) -> None:
        if bar_count < 2 or window_size < 2:
            raise ValueError("bar_count and window_size must be >= 2")
        self._bar_count = int(bar_count)
        self._recent: deque[float] = deque(maxlen=int(window_size))
        self._bars: deque[float] = deque((0.0 for _ in range(bar_count)), maxlen=bar_count)
        self._envelope = 0.0

    @property
    def bars(self) -> tuple[float, ...]:
        """Bar heights in the 0..1 range, ordered oldest to newest."""
        return tuple(self._bars)

    def reset(self) -> None:
        self._recent.clear()
        self._bars.clear()
        self._bars.extend(0.0 for _ in range(self._bar_count))
        self._envelope = 0.0

    def push(self, measured_peak: float, *, active: bool = True) -> tuple[float, ...]:
        """Consume one measured peak. No clocks, randomness or synthesized data."""
        if not active:
            self.reset()
            return self.bars

        try:
            raw = float(measured_peak)
        except (TypeError, ValueError, OverflowError):
            raw = 0.0
        if not math.isfinite(raw):
            raw = 0.0
        raw = max(0.0, min(1.0, raw))
        self._recent.append(raw)

        # A near-zero input really is silence. Never animate silence to
        # simulate an iOS waveform; the bar heights decay to their baseline.
        if raw <= 0.002:
            target = 0.0
        else:
            recent = self._recent
            average = sum(recent) / len(recent)
            # Short-term local contrast emphasizes real dynamic changes even
            # on heavily compressed/normalized masters. The floor prevents
            # amplifying numerical jitter to full scale.
            width = max(max(recent) - min(recent), average * 0.018, 0.012)
            contrast = max(-1.0, min(1.0, (raw - average) / width))
            energy = raw / (raw + 0.05)
            target = max(0.0, min(1.0, 0.11 + 0.50 * energy + 0.40 * contrast))

        # Responsive attack, controlled release. Do not smooth each peak so
        # heavily that successive bars become indistinguishable.
        response = 0.86 if target >= self._envelope else 0.64
        self._envelope += (target - self._envelope) * response
        if self._envelope < 0.006:
            self._envelope = 0.0
        self._bars.append(self._envelope)
        return self.bars


class _WindowsMeterBackend:
    """Core Audio COM access, used from a single COM-initialized thread."""

    def __init__(self, audio_utilities: Any, meter_interface: Any,
                 audio_session_class: Any, session_ctl2: Any,
                 comtypes_module: Any) -> None:
        self._audio_utilities = audio_utilities
        self._meter_interface = meter_interface
        self._audio_session_class = audio_session_class
        self._session_ctl2 = session_ctl2
        self._comtypes = comtypes_module
        self._spotify_meters: list[Any] = []
        self._endpoints: list[Any] = []
        self._errors: list[str] = []
        self._zero_count = 0
        self._last_refresh = 0.0
        self._session_count = 0
        self._last_log_signature = None

    @staticmethod
    def _session_name(session: Any) -> str:
        parts: list[str] = []
        for attr in ("DisplayName", "Identifier", "InstanceIdentifier"):
            try:
                part = getattr(session, attr, None)
                if part:
                    parts.append(str(part))
            except Exception:
                continue
        try:
            process = session.Process
            if process is not None:
                parts.append(str(process.name()))
        except Exception:
            pass
        return " ".join(parts).casefold()

    def _meter_for_endpoint(self, device: Any) -> Any:
        unknown = device._dev.Activate(
            self._meter_interface._iid_, self._comtypes.CLSCTX_ALL, None
        )
        return unknown.QueryInterface(self._meter_interface)

    def refresh(self, now: float, *, force: bool = False) -> None:
        if not force and now - self._last_refresh < 2.0:
            return
        self._last_refresh = now
        sessions: list[Any] = []
        endpoints: list[Any] = []
        errors: list[str] = []
        try:
            # Enumerate *all* active render endpoints, not only the system
            # default. Windows can route Spotify to a different output device.
            devices = self._audio_utilities.GetAllDevices(data_flow=0, device_state=1)
        except Exception as exc:
            devices = []
            errors.append(f"enumerate_devices: {type(exc).__name__}: {exc}")
        if not devices:
            try:
                device = self._audio_utilities.GetSpeakers()
                if device:
                    devices = [device]
            except Exception as exc:
                errors.append(f"default_device: {type(exc).__name__}: {exc}")
        for device in devices:
            try:
                endpoints.append(self._meter_for_endpoint(device))
            except Exception as exc:
                errors.append(f"endpoint_meter: {type(exc).__name__}: {exc}")
            try:
                manager = device.AudioSessionManager
                enumerator = manager.GetSessionEnumerator()
                for idx in range(int(enumerator.GetCount())):
                    control = enumerator.GetSession(idx)
                    if control is None:
                        continue
                    ctl2 = control.QueryInterface(self._session_ctl2)
                    session = self._audio_session_class(ctl2)
                    if "spotify" not in self._session_name(session):
                        continue
                    try:
                        sessions.append(ctl2.QueryInterface(self._meter_interface))
                    except Exception as exc:
                        errors.append(f"spotify_meter: {type(exc).__name__}: {exc}")
            except Exception as exc:
                errors.append(f"session_list: {type(exc).__name__}: {exc}")
        # Keep COM pointers only on this worker thread. Replacing these lists
        # releases stale pointers (such as from a disconnected audio device).
        self._spotify_meters = sessions
        self._session_count = len(sessions)
        self._endpoints = endpoints
        self._errors = errors[:4]
        self._zero_count = 0
        signature = (len(sessions), len(endpoints), tuple(errors[:2]))
        if signature != self._last_log_signature:
            LOG.info("Audio meter refresh: Spotify sessions=%s, render meters=%s, errors=%s",
                     len(sessions), len(endpoints), "; ".join(errors[:2]) if errors else "none")
            self._last_log_signature = signature

    @staticmethod
    def _peak_from(meters: list[Any]) -> tuple[float, bool]:
        peak, readable = 0.0, False
        for meter in meters:
            try:
                value = float(meter.GetPeakValue())
                if math.isfinite(value):
                    peak = max(peak, max(0.0, min(1.0, value)))
                    readable = True
            except Exception:
                continue
        return peak, readable

    def sample(self, now: float) -> MeterSnapshot:
        self.refresh(now)
        spotify_peak, session_ok = self._peak_from(self._spotify_meters)
        if session_ok and spotify_peak > 0.0001:
            self._zero_count = 0
            return MeterSnapshot(spotify_peak, "spotify_session", "Spotify per-app audio peak",
                                 self._session_count, len(self._endpoints))
        if session_ok:
            self._zero_count += 1
            # A session may read zero on a silently muted track; don't jump to
            # another application's audio at every quiet instant. Allow the
            # actual output fallback only after a sustained zero reading.
            if self._zero_count < 18:
                return MeterSnapshot(0.0, "spotify_session", "Spotify session currently silent",
                                     self._session_count, len(self._endpoints))
        output_peak, output_ok = self._peak_from(self._endpoints)
        if output_ok:
            detail = "Output-device mix; may contain audio from other applications"
            if output_peak > 0.0001:
                return MeterSnapshot(output_peak, "output_mix", detail,
                                     self._session_count, len(self._endpoints))
            if session_ok:
                return MeterSnapshot(0.0, "spotify_session", "Spotify and output are silent",
                                     self._session_count, len(self._endpoints))
            return MeterSnapshot(0.0, "output_mix", "Output-device meter is silent",
                                 self._session_count, len(self._endpoints))
        detail = "; ".join(self._errors) or "No accessible Spotify or output peak meter"
        return MeterSnapshot(0.0, "meter_unavailable", detail,
                             self._session_count, len(self._endpoints))


class SpotifyAudioMeter:
    """Non-blocking real audio level service for Qt and other UI consumers."""

    _SAMPLE_INTERVAL = 0.03

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest = MeterSnapshot()
        self._stop = threading.Event()
        self._active = threading.Event()
        self._thread = threading.Thread(target=self._run, name="SpotifyAudioMeter", daemon=True)
        self._thread.start()

    def set_active(self, playing: bool) -> None:
        if playing:
            self._active.set()
        else:
            self._active.clear()
            with self._lock:
                old = self._latest
                # Do not hide diagnostic errors when playback is paused.
                if old.source not in ("missing_dependency", "com_error", "unsupported"):
                    self._latest = MeterSnapshot(0.0, "paused", "Playback paused or overlay hidden",
                                                 old.spotify_sessions, old.output_devices)

    def snapshot(self) -> MeterSnapshot:
        with self._lock:
            return self._latest

    def diagnostic_text(self) -> str:
        snapshot = self.snapshot()
        return ("Spotify Overlay Lyrics - audio meter\n"
                f"Source: {snapshot.source}\n"
                f"Level: {snapshot.level:.5f}\n"
                f"Spotify session meters: {snapshot.spotify_sessions}\n"
                f"Output device meters: {snapshot.output_devices}\n"
                f"Detail: {snapshot.detail}\n"
                "Log: %LOCALAPPDATA%\\SpotifyOverlayLyrics\\application.log")

    def close(self) -> None:
        self._stop.set()
        self._active.set()  # Wake inactive worker immediately.
        if self._thread.is_alive() and threading.current_thread() != self._thread:
            self._thread.join(timeout=0.25)

    def _publish(self, snapshot: MeterSnapshot) -> None:
        with self._lock:
            self._latest = snapshot

    def _run(self) -> None:
        if os.name != "nt":
            self._publish(MeterSnapshot(source="unsupported", detail="Only Windows audio is supported"))
            return
        try:
            # Import inside the worker thread; never pass COM pointers to Qt.
            import comtypes
            from pycaw.pycaw import (
                AudioSession, AudioUtilities, IAudioMeterInformation, IAudioSessionControl2,
            )
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            LOG.warning("Real audio meter dependencies unavailable: %s", reason)
            self._publish(MeterSnapshot(source="missing_dependency", detail=reason))
            return

        try:
            comtypes.CoInitialize()
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            LOG.warning("Audio meter COM initialization failed: %s", reason)
            self._publish(MeterSnapshot(source="com_error", detail=reason))
            return

        backend = _WindowsMeterBackend(AudioUtilities, IAudioMeterInformation,
                                       AudioSession, IAudioSessionControl2, comtypes)
        last_source = None
        try:
            while not self._stop.is_set():
                if not self._active.is_set():
                    self._stop.wait(0.1)
                    continue
                try:
                    snap = backend.sample(time.monotonic())
                except Exception as exc:
                    reason = f"{type(exc).__name__}: {exc}"
                    snap = MeterSnapshot(source="error", detail=reason)
                self._publish(snap)
                if snap.source != last_source:
                    LOG.info("Audio meter source: %s (%s)", snap.source, snap.detail)
                    last_source = snap.source
                self._stop.wait(self._SAMPLE_INTERVAL)
        finally:
            # Drop all COM references before uninitializing on the same thread.
            backend._spotify_meters.clear()
            backend._endpoints.clear()
            del backend
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass
