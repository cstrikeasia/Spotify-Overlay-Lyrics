"""Per-user preferences with bounded values and safe file writes."""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


def data_dir() -> Path:
    if os.name == "nt":
        root = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(root) / "SpotifyOverlayLyrics"
    return Path.home() / ".spotify_overlay_lyrics"


@dataclass
class Settings:
    font_size: int = 26
    opacity: int = 92  # full application FRAME BACKGROUND only (legacy key retained)
    overlay_opacity: int = 92  # overlay lyric window only
    overlay_lyric_effect: str = "scroll"  # none / scroll / fade; OBS is separate
    desktop_text_only: bool = False
    game_overlay_enabled: bool = False  # frameless, no-activate, click-through overlay
    language: str = "system"  # System locale unless explicitly overridden
    adaptive_lyrics_background: bool = True
    desktop_text_color: str = "#F7FFF9"
    desktop_cover_enabled: bool = True
    obs_cover_enabled: bool = False  # opt-in; do not alter existing OBS layouts
    offset_ms: int = 300
    timing_version: int = 1  # v1.0.7 advances old user settings by 300ms exactly once
    line_count: int = 5
    smooth_lyrics: bool = True
    transition_ms: int = 620
    topmost: bool = True
    start_with_windows: bool = False
    obs_browser_enabled: bool = True
    obs_browser_port: int = 38571
    obs_window_capture: bool = False
    width: int = 650
    height: int = 430
    x: int = -1  # full application position
    y: int = -1
    overlay_width: int = 650
    overlay_height: int = 132
    overlay_x: int = -1
    overlay_y: int = -1

    def validate(self) -> None:
        self.font_size = max(17, min(44, int(self.font_size)))
        self.opacity = max(45, min(100, int(self.opacity)))
        self.overlay_opacity = max(45, min(100, int(self.overlay_opacity)))
        self.overlay_lyric_effect = (self.overlay_lyric_effect.strip().lower()
                                      if isinstance(self.overlay_lyric_effect, str)
                                      else "scroll")
        if self.overlay_lyric_effect not in {"none", "scroll", "fade"}:
            self.overlay_lyric_effect = "scroll"
        self.desktop_text_only = bool(self.desktop_text_only)
        self.game_overlay_enabled = bool(self.game_overlay_enabled)
        # A game overlay must never display the full player over a game.
        if self.game_overlay_enabled:
            self.desktop_text_only = True
        self.adaptive_lyrics_background = bool(self.adaptive_lyrics_background)
        if self.language not in {"system", "zh_TW", "zh_CN", "en", "ja", "ko"}:
            self.language = "system"
        self.desktop_cover_enabled = bool(self.desktop_cover_enabled)
        self.obs_cover_enabled = bool(self.obs_cover_enabled)
        color = str(self.desktop_text_color).strip()
        self.desktop_text_color = color.upper() if re.fullmatch(r"#[0-9A-Fa-f]{6}", color) else "#F7FFF9"
        self.offset_ms = max(-10000, min(10000, int(self.offset_ms)))
        self.timing_version = 1
        self.line_count = 3 if int(self.line_count) == 3 else 5
        self.smooth_lyrics = bool(self.smooth_lyrics)
        self.transition_ms = max(200, min(1200, int(self.transition_ms)))
        # Responsive player layout handles sizes below the old 430x390 floor.
        self.width = max(325, min(1400, int(self.width)))
        self.height = max(265, min(1000, int(self.height)))
        self.obs_browser_enabled = bool(self.obs_browser_enabled)
        self.obs_window_capture = bool(self.obs_window_capture)
        self.obs_browser_port = max(1024, min(65535, int(self.obs_browser_port)))
        self.x = int(self.x)
        self.y = int(self.y)
        self.overlay_width = max(220, min(1400, int(self.overlay_width)))
        self.overlay_height = max(62, min(1000, int(self.overlay_height)))
        self.overlay_x = int(self.overlay_x)
        self.overlay_y = int(self.overlay_y)

    def geometry_for_mode(self, overlay: bool) -> tuple[int, int, int, int]:
        """Return x/y/width/height for one mode without sharing state."""
        if overlay:
            return (self.overlay_x, self.overlay_y,
                    self.overlay_width, self.overlay_height)
        return self.x, self.y, self.width, self.height

    def remember_geometry(self, overlay: bool, x: int, y: int,
                          width: int, height: int) -> None:
        """Update only the active mode; the other mode stays untouched."""
        if overlay:
            self.overlay_x, self.overlay_y = int(x), int(y)
            self.overlay_width, self.overlay_height = int(width), int(height)
        else:
            self.x, self.y = int(x), int(y)
            self.width, self.height = int(width), int(height)


def load_settings(path: Path | None = None) -> Settings:
    path = path or data_dir() / "settings.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Invalid settings object")
        # v1.0.25 used one opacity for both modes. Start the new overlay
        # opacity at that previously saved value, then keep the two separate.
        if "overlay_opacity" not in payload:
            payload["overlay_opacity"] = payload.get("opacity", 92)
        # Preserve the former smooth/instant choice for existing installs.
        # From v1.0.29 onward the overlay effect and OBS smoothing are independent.
        if "overlay_lyric_effect" not in payload:
            payload["overlay_lyric_effect"] = (
                "scroll" if payload.get("smooth_lyrics", True) else "none"
            )

        # Older versions stored whichever mode was active in x/y/width/height.
        # On first upgrade, recover the overlay geometry when the last mode
        # was overlay, rather than treating its small size as the app size.
        if "overlay_x" not in payload and payload.get("desktop_text_only", False):
            payload["overlay_x"] = payload.get("x", -1)
            payload["overlay_y"] = payload.get("y", -1)
            payload["overlay_width"] = payload.get("width", 650)
            # The old shared height was validated against the full window's
            # minimum of 330px even for the compact overlay. Normalize those
            # values back to the old compact-mode maximum of 180px.
            old_height = int(payload.get("height", 132))
            payload["overlay_height"] = min(180, old_height) if old_height >= 330 else old_height
            payload["x"], payload["y"] = -1, -1
            payload["width"], payload["height"] = 650, 430

        if "timing_version" not in payload:
            # Existing installations persist offset_ms=0. Preserve their manual
            # adjustment while applying the requested +300ms once on upgrade.
            payload["offset_ms"] = int(payload.get("offset_ms", 0)) + 300
            payload["timing_version"] = 1
        settings = Settings(**{k: v for k, v in payload.items() if k in Settings.__dataclass_fields__})
        settings.validate()
        return settings
    except (OSError, ValueError, TypeError):
        return Settings()


def save_settings(settings: Settings, path: Path | None = None) -> None:
    path = path or data_dir() / "settings.json"
    settings.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def update_windows_startup(enabled: bool) -> None:
    """Create/remove HKCU Run entry, never require admin privileges."""
    if os.name != "nt":
        return
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            if getattr(sys, "frozen", False):
                command = f'"{sys.executable}"'
            else:
                pythonw = Path(sys.executable).with_name("pythonw.exe")
                executable = pythonw if pythonw.exists() else Path(sys.executable)
                entry = Path(__file__).resolve().parent.parent / "main.py"
                command = f'"{executable}" "{entry}"'
            winreg.SetValueEx(key, "SpotifyOverlayLyrics", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, "SpotifyOverlayLyrics")
            except FileNotFoundError:
                pass
