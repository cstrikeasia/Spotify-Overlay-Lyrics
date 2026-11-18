"""Fetch verified, preferably time-synced LRC lyrics with privacy-aware fallbacks.

Lookup order: local override > staged parallel provider lookup.
Fast LRCLIB responses avoid additional network traffic; slow or missing
responses trigger independent parallel sources with early cancellation.
All remote results require independent metadata checks and real timing evidence.
Never synthesize timestamps for plain lyrics or assume an unrelated song matches.
"""
from __future__ import annotations

import hashlib
import logging
import queue
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlsplit

import requests
from unidecode import unidecode

from .models import NO_LYRICS_MESSAGE, LyricLine, LyricsResult, Playback

LOG = logging.getLogger(__name__)
LRC_LINE = re.compile(r"\[(\d{1,3}):(\d{2})(?:\.(\d{1,3}))?\]")
LRC_OFFSET = re.compile(r"\[offset\s*:\s*([+-]?\d+)\]", re.IGNORECASE)
TAG = re.compile(r"\[[a-zA-Z][a-zA-Z0-9]*\s*:[^\]]*\]")
TITLE_SUFFIX = re.compile(
    r"\s*[\(\[](?:(?:\d{4}\s+)?remaster(?:ed)?|live|official (?:audio|video)|"
    r"lyric video|radio edit|single version|explicit|mono|stereo|deluxe edition)"
    r"[^\)\]]*[\)\]]"
    r"|\s+[-\u2013\u2014]\s+(?:(?:\d{4}\s+)?remaster(?:ed)?|live|radio edit)\b.*$",
    re.IGNORECASE,
)
FEATURE_SUFFIX = re.compile(
    r"\s*[\(\[]\s*(?:feat(?:uring)?\.?|ft\.?)\s+[^\)\]]+[\)\]]",
    re.IGNORECASE,
)
ARTIST_SPLIT = re.compile(
    r"\s*(?:,|;|、|&|＆|/|／|\+)\s*"
    r"|\s+(?:feat\.?|featuring|ft\.?|with|x|×)\s+",
    re.IGNORECASE,
)
LRCLIB_URL = "https://lrclib.net"
LRCAPI_URL = "https://api.lrc.cx"
# Additional public aggregators: verify availability and licensing independently.
# LRCMux offers the LRCLIB compatible response schema. LiriQo uses a normalized
# timed[] response and may combine sources such as NetEase, QQ and PetitLyrics.
LRCMUX_URL = "https://api.lrcmux.dev"
LIRIQO_URL = "https://api.liriqo-alfarrizi.my.id"
# Repeatedly fetching missing/untimed lyrics is wasteful, but those results
# may improve online, so don't lock them in the cache for the entire session.
UNSYNCED_CACHE_SECONDS = 10 * 60
PROVIDER_HEDGE_SECONDS = 0.70
PROVIDER_REQUEST_GAP_SECONDS = 0.45
# LiriQo is an optional community-run aggregator. If its public endpoint is
# temporarily unavailable, do not make every new song wait on the same outage.
# Backoff is deliberately per-host and never disables the other providers.
LIRIQO_FAILURE_BACKOFF_SECONDS = 90
LIRIQO_MAX_FAILURE_BACKOFF_SECONDS = 600
MAX_LYRICS_CHARS = 250_000

# Verified alternate *metadata*, not lyrics. Spotify's international catalog
# often shows Latin script while Japanese lyric catalogs use official names.
# Keep aliases scoped to artist / track rather than lowering the global match
# threshold, which risks showing a cover or another song with the same title.
ARTIST_ALIASES: tuple[tuple[str, ...], ...] = (
    ("優里", "Yuuri", "ゆうり", "ユウリ"),
)
TITLE_ALIASES: tuple[tuple[str, ...], ...] = (
    ("かくれんぼ", "Kakurenbo"),
)


def parse_lrc(text: str) -> tuple[LyricLine, ...]:
    """Read LRC timestamps, multi-stamp lines, offsets and metadata tags."""
    match = LRC_OFFSET.search(text)
    offset = int(match.group(1)) if match else 0
    records: list[LyricLine] = []
    for raw in text.replace("\r", "").split("\n"):
        stamps = list(LRC_LINE.finditer(raw))
        if not stamps:
            continue
        content = LRC_LINE.sub("", raw)
        content = TAG.sub("", content).strip()
        if not content:
            continue
        for stamp in stamps:
            minute = int(stamp.group(1))
            second = int(stamp.group(2))
            if second >= 60:
                continue
            fraction = (stamp.group(3) or "0").ljust(3, "0")[:3]
            ms = max(0, minute * 60_000 + second * 1000 + int(fraction) + offset)
            records.append(LyricLine(start_ms=ms, text=content))
    return tuple(sorted(records, key=lambda line: line.start_ms))


def text_to_lyrics(synced: str | None, plain: str | None, *, source: str) -> LyricsResult:
    if isinstance(synced, str) and synced:
        lines = parse_lrc(synced[:MAX_LYRICS_CHARS])
        if lines:
            return LyricsResult(kind="synced", lines=lines, source=source)
    if isinstance(plain, str) and plain.strip():
        return LyricsResult(kind="plain", plain_text=plain.strip()[:MAX_LYRICS_CHARS], source=source)
    return LyricsResult(kind="missing", source=source, message=NO_LYRICS_MESSAGE)


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s or "")).casefold()
    s = re.sub(r"[^\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7a3]+", " ", s)
    return " ".join(s.split())


def alias_variants(value: str, groups: tuple[tuple[str, ...], ...]) -> tuple[str, ...]:
    """List original and trusted equivalent spellings, original first.

    The first entry in each group is the official native-script spelling.
    Never use substring alias matches: "Yuri" and "Yuuri" are not the same
    artist, and arbitrary shared characters are not proof of identity.
    """
    value = str(value or "").strip()
    variants = [value] if value else []
    canonical = normalize(value)
    for group in groups:
        if canonical in {normalize(part) for part in group}:
            variants.extend(group)
            break
    return tuple(dict.fromkeys(variants))


def native_spelling(value: str, groups: tuple[tuple[str, ...], ...]) -> str:
    """Prefer official Japanese metadata spelling when a verified alias is known."""
    normalized = normalize(value)
    for group in groups:
        if normalized in {normalize(part) for part in group}:
            return group[0]
    return value


def title_variants(value: str) -> tuple[str, ...]:
    return alias_variants(value, TITLE_ALIASES)


def artist_variants(value: str) -> tuple[str, ...]:
    return alias_variants(value, ARTIST_ALIASES)


def alias_equivalent(a: str, b: str) -> bool:
    """Only accept explicitly verified cross-script metadata aliases."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return False
    for group in (*ARTIST_ALIASES, *TITLE_ALIASES):
        normalized = {normalize(part) for part in group}
        if na in normalized and nb in normalized:
            return True
    return False


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    return SequenceMatcher(None, na, nb).ratio() if na and nb else 0.0


def metadata_similarity(a: str, b: str) -> float:
    """Match Japanese kana aliases (ミセカイ / Misekai) without dropping artist guards.

    Romanization only expands possible search matches. We still require the
    track title, artist and (when present) recording duration to agree.
    """
    if alias_equivalent(a, b):
        return 1.0
    direct = similarity(a, b)
    if direct >= 0.999:
        return direct
    roman = similarity(unidecode(a), unidecode(b))
    return max(direct, roman * 0.98)


def search_alias(name: str) -> str:
    """Return a useful transliterated artist alias only if spelling changes."""
    ascii_name = " ".join(unidecode(name).split())
    if ascii_name and normalize(ascii_name) != normalize(name):
        return ascii_name
    return ""


def strip_version(title: str) -> str:
    title = str(title or "").strip()
    previous = None
    while previous != title:
        previous = title
        title = FEATURE_SUFFIX.sub("", TITLE_SUFFIX.sub("", title)).strip()
    return title


def primary_artist(artist: str) -> str:
    """Use the leading credited artist as a backup for multi-artist metadata."""
    parts = ARTIST_SPLIT.split(str(artist or ""))
    return (parts[0] if parts else "").strip() or str(artist or "").strip()


def duration_seconds(value: object) -> float | None:
    """Some aggregators describe duration as 4:31 rather than 271 seconds."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if 0 < value < 36000 else None
    if isinstance(value, str):
        try:
            if ':' in value:
                numbers = [int(part) for part in value.strip().split(':')]
                if len(numbers) not in (2, 3) or any(number < 0 for number in numbers):
                    return None
                if any(number > 59 for number in numbers[1:]):
                    return None
                return float(sum(number * 60 ** i for i, number in enumerate(reversed(numbers))))
            parsed = float(value.strip())
            return parsed if 0 < parsed < 36000 else None
        except ValueError:
            return None
    return None


def candidate_score(candidate: dict, playback: Playback) -> float:
    """Score metadata with a duration guard; never trust a provider's first hit."""
    c_title = str(candidate.get("trackName") or "")
    c_artist = str(candidate.get("artistName") or "")
    whole_title = metadata_similarity(c_title, playback.title)
    clean_title = metadata_similarity(strip_version(c_title), strip_version(playback.title))
    artist_whole = metadata_similarity(c_artist, playback.artist)
    artist_primary = metadata_similarity(primary_artist(c_artist), primary_artist(playback.artist))
    # A registered artist spelling is a verified identity, not an invitation
    # to accept near-identical names (Yuuri vs Yuri) as the same performer.
    c_primary = primary_artist(c_artist)
    p_primary = primary_artist(playback.artist)
    for group in ARTIST_ALIASES:
        aliases = {normalize(part) for part in group}
        if (normalize(c_primary) in aliases or normalize(p_primary) in aliases) and not (
            normalize(c_primary) in aliases and normalize(p_primary) in aliases
        ):
            return -1.0
    # A collaboration may be labelled with only one artist, but an exact
    # credit is still preferred over a different collaboration.
    artist = max(artist_whole, artist_primary * 0.94)
    if max(whole_title, clean_title) < 0.72 or artist < 0.54:
        return -1.0
    if edition_tags(c_title) != edition_tags(playback.title):
        return -1.0  # don't put studio timestamps on a live/remix recording
    score = max(whole_title, clean_title) * 0.64 + artist * 0.30
    seconds = candidate.get("duration")
    if seconds and playback.duration_ms:
        try:
            duration_value = duration_seconds(seconds)
            if duration_value is None:
                return -1.0
            delta = abs(duration_value - playback.duration_ms / 1000)
        except (TypeError, ValueError):
            return -1.0
        if delta > 12:
            return -1.0  # avoid matching unrelated live / extended versions
        score += 0.06 * (1 - min(1.0, delta / 12))
    if candidate.get("syncedLyrics"):
        score += 0.05
    if candidate.get("instrumental"):
        score -= 0.02
    return score


def edition_tags(title: str) -> frozenset[str]:
    """Identify versions with materially different musical timing.

    Only scan parenthetical/bracketed and dash-suffixed qualifiers so a song
    literally titled "Live Your Life" is not misclassified as a live take.
    """
    qualifiers = []
    for match in re.finditer(r"\(([^)]*)\)|\[([^]]*)\]|\s+[-–—]\s+(.+)$", title):
        qualifiers.append(next((part for part in match.groups() if part is not None), ""))
    text = " ".join(normalize(part) for part in qualifiers)
    labels = ("live", "acoustic", "remix", "instrumental", "karaoke", "demo", "sped up", "slowed")
    return frozenset(label for label in labels if re.search(r"\b" + re.escape(label) + r"\b", text))


def local_lyric_filename(playback: Playback) -> str:
    return hashlib.sha256(playback.key.encode("utf-8")).hexdigest() + ".lrc"


def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp950", "gb18030"):
        try:
            return raw.decode(encoding)
        except (UnicodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def parse_local_file(text: str) -> LyricsResult:
    lines = parse_lrc(text)
    if lines:
        return LyricsResult(kind="synced", lines=lines, source="本機 LRC")
    # An untimed local TXT/LRC must never become a displayable lyric result.
    # Keep the file on disk for compatibility, but ignore it for playback.
    return LyricsResult(kind="missing", message=NO_LYRICS_MESSAGE)


def normalized_timed_lines(raw: object, duration_ms: int = 0) -> tuple[LyricLine, ...]:
    """Parse normalized millisecond lines from multi-provider APIs.

    Reject static text, invalid times, synthetic all-zero timelines and
    timestamps far beyond the known track duration. No text is generated.
    """
    if not isinstance(raw, list):
        return ()
    lines: list[LyricLine] = []
    for row in raw[:750]:
        if not isinstance(row, dict):
            continue
        text = row.get("text")
        start = row.get("start")
        if not isinstance(text, str) or not text.strip() or isinstance(start, bool):
            continue
        if not isinstance(start, (int, float)) or not (0 <= start < 3_600_000):
            continue
        if not float(start).is_integer() or len(text) > 1000:
            continue
        if duration_ms > 0 and start > duration_ms + 10_000:
            continue
        lines.append(LyricLine(start_ms=int(start), text=text.strip()))
    if not lines or not any(line.start_ms > 0 for line in lines):
        return ()
    return tuple(sorted(lines, key=lambda item: item.start_ms))


class ProviderSessions:
    """Connection pools are isolated by host so concurrent requests do not
    share a mutable requests.Session or its cookie jar.

    A single ``get`` entry point is intentionally kept for callers and test
    fixtures that patch the session HTTP method.
    """

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self._sessions: dict[str, requests.Session] = {}
        self._lock = threading.Lock()

    def get(self, url: str, *, params: dict, timeout: tuple[float, float]):
        host = urlsplit(url).netloc
        with self._lock:
            session = self._sessions.get(host)
            if session is None:
                session = requests.Session()
                session.headers.update(self.headers)
                self._sessions[host] = session
        return session.get(url, params=params, timeout=timeout)

    def close(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close()


class LyricsSearchCancelled(Exception):
    """An obsolete lookup should not continue making provider requests."""


class LyricsProviderCoolingDown(Exception):
    """Skip an optional provider while its temporary outage is cooling down."""

    def __init__(self, seconds: int):
        self.seconds = seconds
        super().__init__(f"Provider cooling down for {seconds} seconds")


@dataclass
class LyricsService:
    local_dir: Path

    def __post_init__(self) -> None:
        self._cache: dict[str, LyricsResult] = {}
        self._cache_deadline: dict[str, float] = {}
        self._lock = threading.RLock()
        # Each network host has a private connection pool and rate limiter.
        # Do not hold a global lock while waiting for a remote response.
        self._host_locks: dict[str, threading.Lock] = {
            host: threading.Lock() for host in (LRCLIB_URL, LRCAPI_URL, LRCMUX_URL, LIRIQO_URL)
        }
        self._last_request: dict[str, float] = {}
        self._retry_not_before: dict[str, float] = {}
        self._provider_failures: dict[str, int] = {}
        self._provider_cooldown_until: dict[str, float] = {}
        self._worker_local = threading.local()
        self._active_lookup_lock = threading.Lock()
        self._active_lookup_cancel: threading.Event | None = None
        self._session = ProviderSessions()
        # LRCLIB asks free API clients to identify themselves.
        self._session.headers.update({
            "User-Agent": "SpotifyOverlayLyrics/1.0.17 (Windows overlay; contact: https://lrclib.net)",
            "Accept": "application/json",
        })

    def imported_path(self, playback: Playback) -> Path:
        return self.local_dir / local_lyric_filename(playback)

    def has_synced_local(self, playback: Playback) -> bool:
        """Only a correctly timed local override may block online results."""
        path = self.imported_path(playback)
        if not path.is_file():
            return False
        try:
            return parse_local_file(read_text_file(path)).is_synced
        except OSError as exc:
            LOG.warning("Cannot read local LRC override %s: %s", path, exc)
            return False

    def import_file(self, playback: Playback, source_path: Path) -> LyricsResult:
        if source_path.stat().st_size > 2_000_000:
            raise ValueError("歌詞檔案超過 2 MB")
        raw = read_text_file(source_path)
        result = parse_local_file(raw)
        if not result.is_synced:
            raise ValueError("僅支援含時間碼的同步 LRC 歌詞，純文字歌詞不會顯示。")
        self.local_dir.mkdir(parents=True, exist_ok=True)
        self.imported_path(playback).write_text(raw, encoding="utf-8")
        with self._lock:
            self._cache[playback.key] = result
            self._cache_deadline.pop(playback.key, None)
        return result

    def clear_cache(self, playback: Playback) -> None:
        with self._lock:
            self._cache.pop(playback.key, None)
            self._cache_deadline.pop(playback.key, None)

    def fetch(self, playback: Playback, *, force: bool = False) -> LyricsResult:
        local_path = self.imported_path(playback)
        if local_path.exists():
            try:
                local_result = parse_local_file(read_text_file(local_path))
                if local_result.is_synced:
                    return local_result
                LOG.info("Ignoring untimed local lyric file for %r; checking synced providers", playback.title)
            except OSError as exc:
                LOG.warning("Cannot read local lyrics at %s: %s", local_path, exc)
        with self._lock:
            cached = self._cache.get(playback.key)
            deadline = self._cache_deadline.get(playback.key, float("inf"))
            if cached and not force and time.monotonic() < deadline:
                return cached
        try:
            result = self._fetch_online(playback)
        except requests.RequestException as exc:
            LOG.warning("Lyrics network error: %s", exc)
            return LyricsResult(kind="error", message="歌詞服務連線失敗，請檢查網路後重試")
        except Exception:
            LOG.exception("Lyrics fetch error")
            return LyricsResult(kind="error", message="歌詞讀取發生錯誤")
        # Public API contract: only time-stamped lyrics are displayable.
        # Provider-specific parsers can still classify plain text internally
        # while searching other sources, but fetch() must never expose it.
        if result.kind == "plain":
            result = LyricsResult(kind="missing", message=NO_LYRICS_MESSAGE)
        if result.kind in {"synced", "instrumental", "missing"}:
            with self._lock:
                self._cache[playback.key] = result
                if result.kind == "missing":
                    self._cache_deadline[playback.key] = time.monotonic() + UNSYNCED_CACHE_SECONDS
                else:
                    self._cache_deadline.pop(playback.key, None)
        return result

    def _check_cancelled(self) -> None:
        event = getattr(self._worker_local, "cancel_event", None)
        if event is not None and event.is_set():
            raise LyricsSearchCancelled()

    def _note_provider_outage(self, base: str, reason: str) -> None:
        """Apply a bounded circuit-breaker only to the optional LiriQo host.

        Call with its per-host lock held. Other hosts continue to work even if
        this endpoint times out, refuses requests, or temporarily returns 5xx.
        """
        if base != LIRIQO_URL:
            return
        failures = self._provider_failures.get(base, 0) + 1
        self._provider_failures[base] = failures
        delay = min(LIRIQO_FAILURE_BACKOFF_SECONDS * 2 ** min(failures - 1, 4),
                    LIRIQO_MAX_FAILURE_BACKOFF_SECONDS)
        self._provider_cooldown_until[base] = time.monotonic() + delay
        LOG.warning("LiriQo temporarily unavailable (%s); retry in %ss", reason, delay)

    def _note_provider_healthy(self, base: str) -> None:
        if base == LIRIQO_URL:
            self._provider_failures.pop(base, None)
            self._provider_cooldown_until.pop(base, None)

    def _request_json(self, base: str, path: str, params: dict) -> dict | list | None:
        self._check_cancelled()
        lock = self._host_locks[base]
        with lock:
            self._check_cancelled()
            cooling = self._provider_cooldown_until.get(base, 0) - time.monotonic()
            if cooling > 0:
                raise LyricsProviderCoolingDown(int(cooling + 0.99))
            wait_backoff = self._retry_not_before.get(base, 0) - time.monotonic()
            if wait_backoff > 0:
                raise LyricsRateLimited(int(wait_backoff + 0.99))
            delay = max(0.0, PROVIDER_REQUEST_GAP_SECONDS -
                        (time.monotonic() - self._last_request.get(base, float("-inf"))))
            if delay:
                # The delay is limited to this provider; other hosts continue.
                time.sleep(delay)
            self._check_cancelled()
            self._last_request[base] = time.monotonic()
            # Keep bounded timeouts so a non-responsive aggregator cannot
            # dominate the whole song lookup. Slow hosts can be searched by
            # other providers in parallel rather than delaying them.
            timeout = (3, 9) if base == LIRIQO_URL else (3, 7)
            try:
                response = self._session.get(base + path, params=params, timeout=timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                self._note_provider_outage(base, type(exc).__name__)
                raise
            if response.status_code == 429:
                try:
                    retry_seconds = max(5, min(3600, int(response.headers.get("Retry-After", "60"))))
                except ValueError:
                    retry_seconds = 60
                self._retry_not_before[base] = time.monotonic() + retry_seconds
                raise LyricsRateLimited(retry_seconds)
            if response.status_code == 404:
                self._note_provider_healthy(base)
                return None
            if response.status_code in (403, 500, 502, 503, 504):
                self._note_provider_outage(base, f"HTTP {response.status_code}")
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError:
                self._note_provider_outage(base, "invalid JSON response")
                raise
            self._note_provider_healthy(base)
            return payload

    def _get(self, path: str, params: dict) -> dict | list | None:
        """Keep the LRCLIB client entrypoint stable for older callers/tests."""
        return self._request_json(LRCLIB_URL, path, params)

    def _fetch_online(self, playback: Playback) -> LyricsResult:
        if not playback.title or not playback.artist:
            return LyricsResult(kind="missing", message=NO_LYRICS_MESSAGE)

        # Launch the primary immediately. If it is fast, avoid requesting any
        # backups. If it is slow or inconclusive, hedge a provider every 700ms.
        # Each provider remains rate-limited independently.
        providers = (
            ("LRCLIB", self._search_lrclib),
            ("LrcAPI", self._search_lrcapi),
            ("LRCMux", self._search_lrcmux),
            ("LiriQo", self._search_liriqo),
        )
        pending: queue.Queue[tuple[str, object]] = queue.Queue()
        cancelled = threading.Event()
        with self._active_lookup_lock:
            if self._active_lookup_cancel is not None:
                self._active_lookup_cancel.set()  # obsolete song / manual retry
            self._active_lookup_cancel = cancelled
        began = time.monotonic()
        launched = 0
        active = 0
        next_hedge = began
        best_plain: LyricsResult | None = None
        instrumental: LyricsResult | None = None
        # An individual provider failure is diagnostic information, not an
        # error to show on the lyrics overlay if other providers responded.
        failures: list[str] = []
        retry_seconds: list[int] = []
        completed_providers = 0

        def lookup(name: str, fn) -> None:
            self._worker_local.cancel_event = cancelled
            try:
                result = fn(playback)
                pending.put((name, result))
            except LyricsSearchCancelled:
                pending.put((name, None))
            except LyricsRateLimited as exc:
                pending.put((name, exc))
            except LyricsProviderCoolingDown as exc:
                pending.put((name, exc))
            except (requests.RequestException, ValueError, TypeError) as exc:
                pending.put((name, exc))
            except Exception as exc:
                LOG.exception("Unexpected %s lyrics search failure", name)
                pending.put((name, exc))
            finally:
                self._worker_local.cancel_event = None

        try:
            while launched < len(providers) or active:
                if cancelled.is_set():
                    # A newer song or manual retry has superseded this job.
                    # Do not publish an obsolete "missing" result to the UI.
                    return LyricsResult(kind="cancelled")
                now = time.monotonic()
                if launched < len(providers) and (active == 0 or now >= next_hedge):
                    name, fn = providers[launched]
                    threading.Thread(
                        target=lookup, args=(name, fn), daemon=True,
                        name=f"Lyrics-{name}",
                    ).start()
                    launched += 1
                    active += 1
                    next_hedge = time.monotonic() + PROVIDER_HEDGE_SECONDS
                wait = max(0.0, next_hedge - time.monotonic()) if launched < len(providers) else 0.5
                try:
                    label, outcome = pending.get(timeout=min(wait, 0.5))
                except queue.Empty:
                    continue
                active -= 1
                if cancelled.is_set():
                    return LyricsResult(kind="cancelled")
                if isinstance(outcome, LyricsRateLimited):
                    failures.append(label)
                    retry_seconds.append(outcome.seconds)
                    LOG.warning("%s rate limited: %s", label, outcome)
                    continue
                if isinstance(outcome, LyricsProviderCoolingDown):
                    failures.append(label)
                    LOG.info("Skipping %s during temporary outage: %ss remaining", label, outcome.seconds)
                    continue
                if isinstance(outcome, Exception):
                    failures.append(label)
                    LOG.warning("%s lyrics search failed: %s", label, outcome)
                    continue
                completed_providers += 1
                if label == "LRCLIB":
                    plain, instr, synced = outcome
                    if plain is not None:
                        best_plain = plain
                    if instr is not None:
                        instrumental = instr
                    result = synced
                else:
                    result = outcome
                if result is not None and result.is_synced:
                    elapsed = (time.monotonic() - began) * 1000
                    LOG.info("Synced lyrics found in %.0fms via %s for %r / %r",
                             elapsed, label, playback.title, playback.artist)
                    return result
                if result is not None and result.kind == "plain" and best_plain is None:
                    best_plain = result
            if best_plain is not None:
                LOG.info("Only untimed lyrics found for %r / %r; hiding static text",
                         playback.title, playback.artist)
                return LyricsResult(kind="missing", message=NO_LYRICS_MESSAGE)
            if instrumental is not None:
                return instrumental
            if completed_providers == 0 and failures:
                LOG.warning("All lyrics providers unavailable for %r / %r: %s",
                            playback.title, playback.artist, ", ".join(failures))
                if len(retry_seconds) == len(failures):
                    return LyricsResult(kind="error", message=(
                        f"歌詞服務請求過多，請於 {min(retry_seconds)} 秒後重試"))
                return LyricsResult(kind="error", message="歌詞服務連線失敗，請檢查網路後重試")
            # At least one provider completed its lookup; a different
            # provider going offline must not be presented as a song error.
            if failures:
                LOG.info("No synchronized lyrics; %d provider(s) completed, %d unavailable: %s",
                         completed_providers, len(failures), ", ".join(failures))
            LOG.info("No verified lyrics after all providers for %r / %r (%dms) in %.0fms",
                     playback.title, playback.artist, playback.duration_ms,
                     (time.monotonic() - began) * 1000)
            return LyricsResult(kind="missing", message=NO_LYRICS_MESSAGE)
        finally:
            cancelled.set()
            with self._active_lookup_lock:
                if self._active_lookup_cancel is cancelled:
                    self._active_lookup_cancel = None

    def _search_lrclib(self, playback: Playback) -> tuple[LyricsResult | None, LyricsResult | None, LyricsResult | None]:
        best_plain: LyricsResult | None = None
        instrumental: LyricsResult | None = None
        base_title = strip_version(playback.title) or playback.title
        lead_artist = primary_artist(playback.artist) or playback.artist
        titles = title_variants(base_title)
        artists = artist_variants(lead_artist)
        # Official local name (first member of a verified alias group).
        native_title = native_spelling(base_title, TITLE_ALIASES)
        native_artist = native_spelling(lead_artist, ARTIST_ALIASES)

        # Exact signature: the API may use duration/album to trigger a record
        # lookup. Try a native metadata signature without the Latin album name,
        # which otherwise prevents finding Japanese singles.
        signatures = [(playback.title, playback.artist, playback.album)]
        if (native_title, native_artist) != (playback.title, playback.artist):
            signatures.append((native_title, native_artist, ""))
        seen_exact: set[tuple[str, str, str]] = set()
        for title, artist, album in signatures:
            signature = (title, artist, album)
            if signature in seen_exact:
                continue
            seen_exact.add(signature)
            params: dict[str, object] = {"track_name": title, "artist_name": artist}
            if album:
                params["album_name"] = album
            if 1000 <= playback.duration_ms <= 3_600_000:
                params["duration"] = round(playback.duration_ms / 1000)
            try:
                record = self._get("/api/get", params)
            except LyricsRateLimited:
                if best_plain is None:
                    raise
                break
            if isinstance(record, dict):
                score = candidate_score(record, playback)
                LOG.info("LRCLIB exact match candidate %r / %r: score=%.3f",
                         record.get("trackName"), record.get("artistName"), score)
                if score >= 0.80:
                    result = self._result_from_record(record)
                    if result.is_synced:
                        return None, None, result
                    if result.kind == "plain" and best_plain is None:
                        best_plain = result
                    elif result.kind == "instrumental" and score >= 0.96:
                        instrumental = result

        # Query with native aliases BEFORE broad title-only search. The latter
        # frequently returns unrelated songs or covers with matching titles.
        queries: list[dict[str, str]] = [
            {"track_name": playback.title, "artist_name": playback.artist},
            {"track_name": native_title, "artist_name": native_artist},
            {"track_name": base_title, "artist_name": lead_artist},
        ]
        if native_title != base_title:
            queries.append({"track_name": native_title, "artist_name": lead_artist})
        if native_artist != lead_artist:
            queries.append({"track_name": base_title, "artist_name": native_artist})
        roman_artist = search_alias(lead_artist)
        if roman_artist:
            queries.extend([
                {"track_name": base_title, "artist_name": roman_artist},
                {"q": f"{base_title} {roman_artist}"},
            ])
        queries.extend([
            {"q": f"{native_title} {native_artist}"},
            {"q": f"{base_title} {lead_artist}"},
            {"track_name": native_title},
            {"track_name": base_title},
            {"q": base_title},
        ])
        seen: set[tuple[tuple[str, str], ...]] = set()
        for query in queries:
            signature = tuple(sorted(query.items()))
            if signature in seen:
                continue
            seen.add(signature)
            try:
                records = self._get("/api/search", query)
            except LyricsRateLimited:
                if best_plain is None:
                    raise
                break
            except (requests.RequestException, ValueError, TypeError) as exc:
                LOG.warning("LRCLIB follow-up search failed: %s", exc)
                break
            if not isinstance(records, list):
                continue
            ranked = sorted(
                ((candidate_score(r, playback), r) for r in records if isinstance(r, dict)),
                key=lambda choice: choice[0], reverse=True,
            )
            if ranked:
                LOG.info("LRCLIB search %s: %d candidate(s); best score %.3f",
                         query, len(ranked), ranked[0][0])
            for score, item in ranked:
                if score < 0.80:
                    break
                result = self._result_from_record(item)
                if result.is_synced:
                    return best_plain, instrumental, result
                if best_plain is None and result.kind == "plain":
                    best_plain = result
                elif instrumental is None and result.kind == "instrumental" and score >= 0.96:
                    instrumental = result
        return best_plain, instrumental, None

    def _search_lrcapi(self, playback: Playback) -> LyricsResult | None:
        base_title = strip_version(playback.title) or playback.title
        lead_artist = primary_artist(playback.artist) or playback.artist
        titles = title_variants(base_title)
        artists = artist_variants(lead_artist)
        native_title = native_spelling(base_title, TITLE_ALIASES)
        native_artist = native_spelling(lead_artist, ARTIST_ALIASES)
        base_params = {"title": base_title, "artist": lead_artist}
        queries: list[dict[str, str]] = []
        if playback.album:
            queries.append(dict(base_params, album=playback.album))
        queries.append(dict(base_params))
        if (native_title, native_artist) != (base_title, lead_artist):
            queries.append({"title": native_title, "artist": native_artist})
        if native_title != base_title:
            queries.append({"title": native_title, "artist": lead_artist})
        if native_artist != lead_artist:
            queries.append({"title": base_title, "artist": native_artist})
        roman_artist = search_alias(lead_artist)
        if roman_artist:
            queries.append({"title": base_title, "artist": roman_artist})
        queries.append({"title": native_title})
        queries.append({"title": base_title})
        seen: set[tuple[tuple[str, str], ...]] = set()
        best_plain: tuple[float, LyricsResult] | None = None
        for params in queries:
            signature = tuple(sorted(params.items()))
            if signature in seen:
                continue
            seen.add(signature)
            records = self._request_json(LRCAPI_URL, "/api/v1/lyrics/advance", params)
            if not isinstance(records, list):
                continue
            ranked: list[tuple[float, LyricsResult]] = []
            for item in records[:30]:
                if not isinstance(item, dict):
                    continue
                lrc = item.get("lyrics")
                if not isinstance(lrc, str) or not lrc.strip() or len(lrc) > MAX_LYRICS_CHARS:
                    continue
                match = {"trackName": item.get("title"), "artistName": item.get("artist"),
                         "duration": item.get("duration"), "syncedLyrics": lrc if LRC_LINE.search(lrc) else None}
                score = candidate_score(match, playback)
                if score < 0.84:
                    continue
                lines = parse_lrc(lrc)
                if lines:
                    ranked.append((score, LyricsResult(kind="synced", lines=lines, source="LrcAPI")))
                else:
                    content = TAG.sub("", lrc).strip()
                    if content:
                        ranked.append((score, LyricsResult(kind="plain", plain_text=content, source="LrcAPI")))
            if ranked:
                synced = [entry for entry in ranked if entry[1].is_synced]
                if synced:
                    return max(synced, key=lambda pair: pair[0])[1]
                strongest = max(ranked, key=lambda pair: pair[0])
                if best_plain is None or strongest[0] > best_plain[0]:
                    best_plain = strongest
        return best_plain[1] if best_plain else None

    def _search_lrcmux(self, playback: Playback) -> LyricsResult | None:
        """Query LRCMux's documented LRCLIB-compatible search endpoint.

        LRCMux aggregates multiple providers, but we *still* verify the
        returned song/artist/duration rather than trusting its first result.
        """
        base_title = strip_version(playback.title) or playback.title
        lead_artist = primary_artist(playback.artist) or playback.artist
        native_title = native_spelling(base_title, TITLE_ALIASES)
        native_artist = native_spelling(lead_artist, ARTIST_ALIASES)
        pairs = [
            (base_title, lead_artist),
            (native_title, native_artist),
        ]
        roman_artist = search_alias(lead_artist)
        if roman_artist:
            pairs.append((base_title, roman_artist))
        seen: set[tuple[str, str]] = set()
        for title, artist in pairs:
            if (title, artist) in seen:
                continue
            seen.add((title, artist))
            payload = self._request_json(
                LRCMUX_URL, "/compat/lrclib/api/search",
                {"track_name": title, "artist_name": artist},
            )
            records = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
            ranked = sorted(
                ((candidate_score(item, playback), item) for item in records[:20] if isinstance(item, dict)),
                key=lambda item: item[0], reverse=True,
            )
            LOG.info("LRCMux query %r/%r, candidates=%d best_score=%.3f",
                     title, artist, len(ranked), ranked[0][0] if ranked else -1.0)
            for score, item in ranked:
                if score < 0.84:
                    break
                # LRCMux can return a plain-only fallback: never expose it.
                lrc = item.get("syncedLyrics")
                if not isinstance(lrc, str) or len(lrc) > MAX_LYRICS_CHARS:
                    continue
                result = text_to_lyrics(lrc, None, source="LRCMux")
                if result.is_synced:
                    LOG.info("LRCMux matched %r / %r: %.3f", item.get("trackName"), item.get("artistName"), score)
                    return result
        return None

    def _search_liriqo(self, playback: Playback) -> LyricsResult | None:
        """Fetch LiriQo normalized timed[] output, rejecting plain/untimed data.

        The remote endpoint documents milliseconds for timed[].start. A
        provider may have rich word timing; the current UI needs only lines.
        We do NOT invent timestamps for static results.
        """
        base_title = strip_version(playback.title) or playback.title
        lead_artist = primary_artist(playback.artist) or playback.artist
        native_title = native_spelling(base_title, TITLE_ALIASES)
        native_artist = native_spelling(lead_artist, ARTIST_ALIASES)
        pairs = [(base_title, lead_artist), (native_title, native_artist)]
        seen: set[tuple[str, str]] = set()
        for title, artist in pairs:
            if (title, artist) in seen:
                continue
            seen.add((title, artist))
            params: dict[str, str | int] = {"title": title, "artist": artist}
            if playback.duration_ms > 0:
                params["duration"] = round(playback.duration_ms / 1000)
            payload = self._request_json(LIRIQO_URL, "/v1/lyrics", params)
            if not isinstance(payload, dict) or payload.get("error"):
                continue
            # Providers are ranked, but metadata and timing are verified per
            # candidate. The "primary" result may have only static lyrics.
            tracks: list[dict] = []
            primary = payload.get("primary")
            if isinstance(primary, dict):
                tracks.append(primary)
            offered = payload.get("tracks")
            if isinstance(offered, list):
                tracks.extend(track for track in offered[:20] if isinstance(track, dict))
            metadata = payload.get("metadata")
            common = metadata if isinstance(metadata, dict) else {}
            LOG.info("LiriQo query %r/%r: candidates=%d", title, artist, len(tracks))
            for track in tracks:
                # Track-specific metadata takes precedence; top-level metadata
                # is the resolved recording according to LiriQo's response.
                meta = track.get("metadata") if isinstance(track.get("metadata"), dict) else common
                match = {
                    "trackName": track.get("title") or meta.get("title"),
                    "artistName": track.get("artist") or meta.get("artist"),
                    "duration": track.get("duration") or meta.get("duration"),
                }
                score = candidate_score(match, playback)
                if score < 0.84:
                    continue
                if str(track.get("syncLevel", "")).lower() not in {"line", "word", "syllable", "character"}:
                    continue
                lines = normalized_timed_lines(track.get("timed"), playback.duration_ms)
                if not lines:
                    continue
                provider = str(track.get("upstreamProvider") or track.get("provider") or "").strip()[:48]
                LOG.info("LiriQo matched provider=%s score=%.3f count=%d", provider, score, len(lines))
                return LyricsResult(kind="synced", lines=lines, source=f"LiriQo ({provider})" if provider else "LiriQo")
        return None

    @staticmethod
    def _result_from_record(record: dict) -> LyricsResult:
        if record.get("instrumental"):
            return LyricsResult(kind="instrumental", source="LRCLIB", message="這是一首純演奏歌曲")
        return text_to_lyrics(record.get("syncedLyrics"), record.get("plainLyrics"), source="LRCLIB")


class LyricsRateLimited(Exception):
    def __init__(self, seconds: int):
        self.seconds = seconds
        super().__init__(f"Rate limited for {seconds} seconds")
