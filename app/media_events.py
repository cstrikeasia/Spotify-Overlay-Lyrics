"""Optional event-driven SMTC wakeups, with polling as a permanent safety net.

The WinRT event handler never calls Qt or awaits WinRT methods. It only wakes
MediaMonitor's single worker thread, where all media reads are serialized.
An event may be missing/delayed in some Spotify versions; the 700ms fallback
still keeps the display working without special privileges or Spotify OAuth.
"""
from __future__ import annotations

import logging
import threading

LOG = logging.getLogger(__name__)


class MediaEventWake:
    """Manage WinRT event tokens and release them when sessions disappear."""

    def __init__(self, wake: threading.Event) -> None:
        self.wake = wake
        self.dirty = threading.Event()
        self.dirty.set()
        self.manager = None
        self.manager_token = None
        self.sessions: list[tuple[object, list[tuple[str, object]]]] = []
        self.active_ids: tuple[str, ...] = ()

    def _changed(self, *_args: object) -> None:
        self.wake.set()

    def _sessions_changed(self, *_args: object) -> None:
        self.dirty.set()
        self.wake.set()

    def attach(self, manager: object) -> None:
        self.close()
        self.manager = manager
        self.dirty.set()
        try:
            self.manager_token = manager.add_sessions_changed(self._sessions_changed)
        except Exception as exc:
            LOG.debug("SMTC manager events unavailable; continuing polling: %s", exc)
        self.refresh()

    def refresh(self) -> None:
        """Only enumerate and rebind when the manager reports changed sessions."""
        if not self.dirty.is_set() or self.manager is None:
            return
        self.dirty.clear()
        try:
            found = [s for s in self.manager.get_sessions()
                     if "spotify" in str(s.source_app_user_model_id).casefold()]
        except Exception as exc:
            LOG.debug("Cannot refresh Spotify event sessions: %s", exc)
            self.dirty.set()
            return
        ids = tuple(sorted(str(s.source_app_user_model_id) for s in found))
        # Rebind on every real SessionsChanged event even when the app ID is
        # identical: Spotify might have restarted and created a new session.
        self._release_sessions()
        self.active_ids = ids
        for session in found:
            tokens: list[tuple[str, object]] = []
            for name in ("media_properties_changed", "playback_info_changed",
                         "timeline_properties_changed"):
                try:
                    token = getattr(session, "add_" + name)(self._changed)
                    tokens.append((name, token))
                except Exception as exc:
                    LOG.debug("SMTC %s event unavailable: %s", name, exc)
            if tokens:
                self.sessions.append((session, tokens))
        LOG.debug("SMTC event subscriptions: %s", len(self.sessions))

    def _release_sessions(self) -> None:
        for session, tokens in self.sessions:
            for name, token in tokens:
                try:
                    getattr(session, "remove_" + name)(token)
                except Exception as exc:
                    LOG.debug("Unable to remove SMTC %s token: %s", name, exc)
        self.sessions.clear()
        self.active_ids = ()

    def close(self) -> None:
        self._release_sessions()
        if self.manager is not None and self.manager_token is not None:
            try:
                self.manager.remove_sessions_changed(self.manager_token)
            except Exception as exc:
                LOG.debug("Unable to remove SMTC manager token: %s", exc)
        self.manager_token = None
        self.manager = None
