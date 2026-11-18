# OBS Vue 3 + Tailwind CSS 4 frontend

The OBS browser-source screen is now a **Vue 3 application** using **Tailwind CSS 4** for the layout. It is intentionally distributed as **offline compiled files**. The user's Python/Windows app requires **no npm, Node, CDN, or internet connection** to display the overlay.

- `index.html`: Vue template, with reactive title/artist, hero artwork, instant outgoing-title hiding, compact lyrics header, and playback state.
- `src/main.js`: Vue app setup, reactive UI state and lifecycle hooks.
- `src/core.js`: query-string options, scale handling and `OverlayModule` lifecycle base class.
- `src/app.js`: orchestrates stream events and updates the separate controllers.
- `src/controllers/progress.js`: playback timeline, pause/seek and compositor animation.
- `src/controllers/artwork.js`: artwork preloading, decoding, transitions and failure grace period.
- `src/controllers/lyrics.js`: bounded lyric compositor and safe cleanup of outgoing lyrics.
- `src/controllers/stream.js`: single EventSource connection and reconnect warnings.
- `src/controllers/layout.js`: validates real synchronized lyrics and manages the 3-second hero hold.
- `src/controllers/motion.js`: GPU-friendly FLIP/Web Animations cover morph, progress repositioning, lyric reveal, and interruption cleanup.
- `src/styles.css`: fixed endpoint geometries, composited transforms/opacity, incoming-title reveal, stable lyric wrapping, masks, and sizing.
- `build.mjs`: compiles Tailwind utilities and copies Vue's pinned browser build into hashed local assets.
- `dist/`: **prebuilt runtime assets**, required by Python `app/obs_source.py`, including in PyInstaller's `--add-data` bundle. Keep these files when creating a ZIP.
- `vendor/`: offline fallback of the official Vue 3.5.13 global runtime with the MIT license notice.

Each controller extends `OverlayModule`, calls `super(ui, options)` in its constructor, and releases its own resources via `dispose()`. The build combines these ordered sources into **one offline `overlay-js` asset**, preserving the existing OBS endpoint and single-script loading. The native DOM lyric compositor maintains at most 9 line nodes to avoid animation jitter and long-session DOM growth.

To edit/rebuild on a developer computer with Node 22+:

```bash
cd frontend
npm install
npm run build
```

The build writes `dist/index.html` and immutable local `/assets/*` bundles; Python serves these through its existing loopback-only HTTP server. No external requests or runtime build process are required. After editing any `src/*.js` or `src/controllers/*.js` file, run `npm run build` to regenerate the content-hashed bundle.

**New default layout**

- While the current song has no available synchronized lyrics (including loading, missing, plain-text-only, or instrumental), the browser shows a **large centered artwork, song title, and progress bar**. No lyric-error message overlays the card.
- Each newly recognized song remains in **hero mode for 3 seconds**, even if usable synchronized lyrics arrive immediately. Repeated SSE progress, cover or lyrics updates for that same song do not restart the clock. A new track key restarts the hold and cancels the previous song's timer.
- After the 3-second intro, only nonempty valid `kind: synced` lines activate the compact layout. Artwork scales over **1650ms** to 58x58px, then the compact title and artist slide rightward from the left and fade in sequentially, beginning **1850ms and 1980ms** after the transition starts. The progress bar moves upward, and the lyrics viewport is revealed below, visually pushing the header up. The DOM snaps to its target sizes once; FLIP transforms keep the visible artwork and progress bar continuous while the geometry settles.
- The large artwork view displays the song title **and the artist immediately beneath it** (including while lyrics are loading or unavailable). Switching to the compact view instantly hides both large-card labels to avoid overlapping the moving artwork or the incoming title.
- The viewport reserves its final height immediately and animates only its opacity/translation; sequential lines continue their configured CSS transform/opacity animation even if the next lyric arrives while the artwork is still shrinking. Actual width, font-size or line-count changes and large seeks still snap safely.
- On each song change, any active artwork/layout morph is cancelled and the overlay **jumps directly to the large-cover hero layout**, with no compact-to-hero reverse animation. The previous compact title and outgoing lyrics disappear immediately, while the new song title and artist appear beneath the large cover. After the 3-second hold, valid synced lyrics trigger only the smooth hero-to-compact transition.
- Songs with loading, missing, instrumental, or plain-text-only lyrics remain in hero mode even after 3 seconds. The OBS overlay suppresses messages such as `正在搜尋這首歌的歌詞` rather than placing search text over the cover. Empty or whitespace-only lyric payloads do **not** activate the compact layout. Reduced-motion preferences are respected.
- **Art and progress are enabled by default**. Explicit `?cover=0` or `?progress=0` hides each. `?track=0` hides the title (and default artwork unless `?cover=1` is specified). `?mix=0` disables transition effects.

**Existing OBS URLs still work:** `http://127.0.0.1:38571/overlay` plus `?track=0`, `?cover=1/0`, `?lines=1/3/5`, `?progress=1`, `?mix=0`, and `?scale=0.75..2.5` (default `1.4`). SSE remains `/events`, artwork remains `/cover/<SHA256>`, and `/state` remains a debugging endpoint. Browser source HTML always returns `Cache-Control: no-store`; the versioned bundled assets use immutable caching.

Windows PyInstaller packaging includes `frontend/dist` automatically from the provided BAT / CI workflow. The desktop Qt interface and backend are unchanged.

**Rendering performance:** The cover/header/progress morph uses `Element.animate()` with FLIP snapshots instead of per-frame width/height/left updates. Temporary `will-change` hints are removed at completion. On a new track, in-flight layout animations are cancelled and the new hero state is applied immediately; only hero-to-compact uses FLIP. A headless Chromium verification observed ~16.7ms animation frames; OBS rendering frame rate depends on source FPS configuration, CEF version, GPU resources, and system load.

**Spotify-style progress bar:** The gray rail and white fill have fixed circular radii. The fill is revealed with an animated rounded `clip-path` inset instead of `scaleX`, which used to compress the end caps into square corners on longer tracks. The existing Web Animations timeline preserves precise playback, pause and seek positions.
