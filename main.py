"""Spotify Overlay Lyrics for Windows, with diagnosable startup failures."""
from __future__ import annotations

import argparse
import logging
import os
import platform
import sys
import traceback
from logging.handlers import RotatingFileHandler

from app.settings import data_dir


LOG = logging.getLogger(__name__)
_ERROR_SHOWN = False


def setup_logging() -> None:
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[RotatingFileHandler(folder / "application.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")],
    )
    LOG.info("Starting Spotify Overlay Lyrics v0.0.2 / Python %s / %s", sys.version.split()[0], platform.platform())


def report_exception(error_type: type[BaseException], error: BaseException, tb: object) -> None:
    """Report both regular Python exceptions and PySide6 callback exceptions.

    A native Windows dialog is shown when pythonw.exe has no visible console.
    """
    global _ERROR_SHOWN
    try:
        LOG.error("Unhandled application exception", exc_info=(error_type, error, tb))
    except Exception:
        pass
    traceback.print_exception(error_type, error, tb, file=sys.stderr)
    if os.name == "nt" and not _ERROR_SHOWN:
        _ERROR_SHOWN = True
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                "Spotify 浮動歌詞啟動或執行時發生錯誤。\n\n"
                f"{error_type.__name__}: {error}\n\n"
                "請複製以下日誌提供給開發者：\n"
                f"{data_dir() / 'application.log'}",
                "Spotify 浮動歌詞 - 發生錯誤",
                0x10,
            )
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows Spotify overlay lyric overlay")
    parser.add_argument("--demo", action="store_true", help="Preview with self-written sample lyrics")
    args = parser.parse_args()
    if platform.system() != "Windows" and not args.demo:
        parser.error("實際 Spotify 監控僅支援 Windows 10/11；可透過 --demo 預覽 UI")
    setup_logging()
    sys.excepthook = report_exception
    from app.ui import launch
    LOG.info("Creating overlay lyrics window; demo=%s", args.demo)
    exit_code = launch(demo=args.demo)
    LOG.info("Qt application exited with code %s", exit_code)
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        report_exception(*sys.exc_info())
        raise SystemExit(1)
