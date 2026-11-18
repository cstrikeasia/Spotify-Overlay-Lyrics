# Spotify Overlay Lyrics

**Windows Spotify Real-Time Lyrics Sync and Desktop Overlay Tool**

![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011-blue)
![Version](https://img.shields.io/badge/Version-v0.0.2-green)
![License](https://img.shields.io/badge/License-MIT-blue)

Automatically detects Spotify playback information, album art, and playback progress through Windows’ native media sessions, and retrieves synchronized lyrics from multiple third-party lyric services.

Developed using **Python + PySide6**, it provides a transparent desktop overlay window and OBS Browser Source integration.

**No Spotify API, Client ID, OAuth, or Premium subscription required.**

## Web Version Screenshot
<img width="1920" height="1080" alt="image" src="https://github.com/user-attachments/assets/b55b2d1e-3eb0-4006-ac90-d16ad51c8797" />

## Desktop App Screenshot
<img width="1919" height="945" alt="image" src="https://github.com/user-attachments/assets/e9b5d2e2-5eff-42df-aefe-9635c6466076" />
<img width="1919" height="943" alt="image" src="https://github.com/user-attachments/assets/83a78550-d69e-4030-9c95-e99f52b2de12" />

## Desktop Overlay Window
<img width="884" height="501" alt="image" src="https://github.com/user-attachments/assets/3737715b-ef7c-48ca-8912-13efcb1ec940" />

## Dynamic Island Screenshot
<img width="1919" height="1031" alt="image" src="https://github.com/user-attachments/assets/6739d234-16c2-446a-8af9-c17c2247cd51" />
<img width="1919" height="1031" alt="image" src="https://github.com/user-attachments/assets/bf2270e2-59fa-42bd-96d0-a7369e1c8dec" />

## CPU and Memory Usage
<img width="659" height="90" alt="image" src="https://github.com/user-attachments/assets/2a709b0d-2e5e-46b8-b801-83a27b81bb4a" />




## Features

- **Automatic Song Detection**: Reads Spotify song titles, artists, albums, album art, and playback status.
- **Real-time Lyric Synchronization**: Supports LRCLIB, LrcAPI, LRCMux, LiriQo and best-effort QQ Music, NetEase Cloud Music, Kugou and Migu read-only searches.
- **Desktop Overlay Window**: Transparent, borderless, draggable, and resizable.
- **Dynamic Island for Windows**: Always-on-top media capsule with animated expand/collapse, album art, synced lyrics, playback and seek controls. Independent from the existing lyric overlay; configured in Settings → System or via the tray menu. See [Dynamic Island guide](DYNAMIC_ISLAND.md).
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
- LRCLIB / LrcAPI / LRCMux / LiriQo / QQ Music / NetEase Cloud Music / Kugou / Migu
- PyInstaller

## License

[MIT License](LICENSE)

This is an independent third-party project and is not affiliated with Spotify or any lyrics provider.
