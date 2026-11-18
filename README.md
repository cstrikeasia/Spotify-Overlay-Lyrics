# Spotify Overlay Lyrics

**Windows Spotify Real-Time Lyrics Sync and Desktop Overlay Tool**

![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011-blue)
![Version](https://img.shields.io/badge/Version-v0.0.1-green)
![License](https://img.shields.io/badge/License-MIT-blue)

Automatically detects Spotify playback information, album art, and playback progress through Windows’ native media sessions, and retrieves synchronized lyrics from multiple third-party lyric services.

Developed using **Python + PySide6**, it provides a transparent desktop overlay window and OBS Browser Source integration.

**No Spotify API, Client ID, OAuth, or Premium subscription required.**

## Web Version Screenshot
<img width="1920" height="1080" alt="image" src="https://github.com/user-attachments/assets/ef4ac4e4-af08-46e7-8010-4ea8d2d84d99" />


## Desktop App Screenshot
<img width="1919" height="943" alt="image" src="https://github.com/user-attachments/assets/226d4227-58bc-4f90-9101-6cd68c02a93f" />
<img width="1919" height="943" alt="image" src="https://github.com/user-attachments/assets/27188456-739c-4bf3-80fb-ef5df4bc0edd" />


## Desktop Overlay Window
<img width="421" height="209" alt="image" src="https://github.com/user-attachments/assets/ae7402e4-572d-4bf5-a531-74756787ed51" />

## Full-Screen Game Overlay Window
<img width="1919" height="1079" alt="image" src="https://github.com/user-attachments/assets/07e428dd-f7c6-48b7-a2bc-fb5bb1c544b7" />



## Features

- **Automatic Song Detection**: Reads Spotify song titles, artists, albums, album art, and playback status.
- **Real-time Lyric Synchronization**: Supports LRCLIB, LrcAPI, LRCMux, and LiriQo.
- **Desktop Overlay Window**: Transparent, borderless, draggable, and resizable.
- **OBS Integration**: Album art, song information, progress bar, and dynamically synchronized lyrics.
- **Multiple Display Modes**: Full-screen window and plain-text lyrics.
- **Local LRC**: Supports importing, offline display, and custom lyrics.
- **No Python Installation Required**: Automatically sets up the runtime environment.
- **Standalone EXE**: Supports Windows PyInstaller packaging.

## Installation

1. Download the latest ZIP version.
2. Extract the contents completely.
3. Run `start.bat`.
4. Open the Windows desktop version of Spotify and play some music.

Upon first launch, the program will automatically check your Python environment and install Python and related packages if necessary.

No administrator privileges are required, and the system PATH will not be modified.

## OBS Integration

Built with Vue 3, Tailwind CSS 4, and the Web Animations API to provide smooth transitions between song cards and animated lyrics.

## Requirements

- Windows 10 (1809+) / Windows 11
- Spotify for Windows desktop
- Internet connection (for online lyrics lookup)
- OBS Studio (optional)

## Tech Stack

- Python / PySide6
- Windows SMTC / WinRT
- Vue 3 / Tailwind CSS 4
- HTTP Server / Server-Sent Events (SSE)
- LRCLIB / LrcAPI / LRCMux / LiriQo
- PyInstaller

## License

[MIT License](LICENSE)

This is an independent third-party project and is not affiliated with Spotify or any lyrics provider.

Translated with DeepL.com (free version)

## v0.0.1 — OBS offline startup fix

The complete ZIP now includes `frontend/dist/index.html` and the compiled Vue, CSS,
and JavaScript assets. **Node.js/npm is not needed to launch the application.**
The desktop window will keep running even if the optional OBS assets are missing;
the OBS Browser Source is disabled and the reason is written to the application log.
The Windows EXE build checks all required assets before packaging.

To use the fixed project, extract **all files** from the ZIP (not just the BAT
file) into a new folder and start `start.bat` on Windows. Existing preferences
in `%LOCALAPPDATA%\SpotifyOverlayLyrics` are not deleted.
