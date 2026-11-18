"""Fetch verified, preferably time-synced LRC lyrics with privacy-aware fallbacks.

Lookup order: local override > memory/disk cache > staged parallel providers.
The two most likely hosts launch together, then other sources are hedged as
needed. Results are metadata-verified with independent timed-lyric evidence.
All remote results require independent metadata checks and real timing evidence.
Never synthesize timestamps for plain lyrics or assume an unrelated song matches.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import math
import os
import queue
import re
import threading
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
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
# Additional read-only, unauthenticated catalog lookups. These consumer web
# endpoints are not stable public developer APIs and can stop working without
# notice; never use account cookies or bypass subscriptions/DRM.
NETEASE_URL = "https://music.163.com"
QQ_SEARCH_URL = "https://u.y.qq.com"
QQ_LYRIC_URL = "https://c.y.qq.com"
# Best-effort read-only Chinese lyric catalogues (not official SDKs).
KUGOU_SEARCH_URL = "https://songsearch.kugou.com"
KUGOU_CANDIDATES_URL = "https://krcs.kugou.com"
KUGOU_LYRIC_URL = "https://lyrics.kugou.com"
MIGU_SEARCH_URL = "https://c.musicapp.migu.cn"
# Migu lyricUrl may point at several Migu-owned media subdomains.
MIGU_ALLOWED_DOMAINS = ("migu.cn", "migu.com")
# Repeatedly fetching missing/untimed lyrics is wasteful, but those results
# may improve online, so don't lock them in the cache for the entire session.
# Re-check absent lyrics sooner; new tracks and incomplete searches should not
# be hidden behind a ten-minute negative cache.
UNSYNCED_CACHE_SECONDS = 90
# Start the two most likely sources immediately. Hedge the remaining sources
# in small increments only while the result is unresolved. Each host still has
# independent rate-limit/backoff protection.
PROVIDER_HEDGE_SECONDS = 0.18
PROVIDER_INITIAL_PARALLEL = 2
PROVIDER_REQUEST_GAP_SECONDS = 0.22
# Only verified synced lyrics are persisted (raw script, not UI-converted text).
# This makes replays instant, including after an application restart.
SYNCED_DISK_CACHE_VERSION = 1
SYNCED_DISK_CACHE_TTL_SECONDS = 21 * 24 * 3600
MAX_DISK_CACHE_BYTES = 600_000
# LiriQo is an optional community-run aggregator. If its public endpoint is
# temporarily unavailable, do not make every new song wait on the same outage.
# Backoff is deliberately per-host and never disables the other providers.
LIRIQO_FAILURE_BACKOFF_SECONDS = 90
LIRIQO_MAX_FAILURE_BACKOFF_SECONDS = 600
OPTIONAL_PROVIDER_URLS = frozenset({LIRIQO_URL, NETEASE_URL, QQ_SEARCH_URL, QQ_LYRIC_URL,
                                    KUGOU_SEARCH_URL, KUGOU_CANDIDATES_URL, KUGOU_LYRIC_URL,
                                    MIGU_SEARCH_URL})
MAX_LYRICS_CHARS = 250_000

# Never keep per-track or per-artist alias tables in application code. All
# search alternatives below are inferred from the current playback metadata:
# script conversion, Unicode normalization, transliteration and explicit
# bilingual names included in the catalog metadata itself.
BRACKETED_METADATA = re.compile(
    r"^(.+?)\s*[\(\[\uFF08\u3010]\s*(.+?)\s*[\)\]\uFF09\u3011]$"
)
CJK_SCRIPT = r"\u3400-\u9fff\u3040-\u30ff\uac00-\ud7a3"
LATIN_NAME = r"[A-Za-z][A-Za-z0-9.\-'\u2019]*(?:\s+[A-Za-z][A-Za-z0-9.\-'\u2019]*)*"
BILINGUAL_METADATA = re.compile(
    rf"^(?:([{CJK_SCRIPT}]+)\s+({LATIN_NAME})|({LATIN_NAME})\s+([{CJK_SCRIPT}]+))$"
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


@lru_cache(maxsize=2)
def _chinese_converter(config: str):
    """Optional pure-Python OpenCC (installed by the Windows launcher)."""
    try:
        from opencc import OpenCC
    except ImportError:
        return None
    return OpenCC(config)


@lru_cache(maxsize=512)
def chinese_variants(value: str) -> tuple[str, ...]:
    """Keep original spelling; add simplified/traditional metadata queries."""
    value = str(value or '').strip()
    if not value:
        return ()
    variants = [value]
    if any('\u3400' <= ch <= '\u9fff' for ch in value):
        for cfg in ('t2s', 's2t'):
            converter = _chinese_converter(cfg)
            if converter is not None:
                converted = converter.convert(value)
                if converted:
                    variants.append(converted)
    return tuple(dict.fromkeys(variants))


def metadata_components(value: str) -> tuple[str, ...]:
    """Extract independently named spellings from the current *metadata*.

    For example, a name supplied by Spotify as "漢字歌手 (Stage Name)"
    already contains two self-declared spellings. Treating them as alternatives
    requires no globally hardcoded artist identity or title translations.
    A title with "(Live)" is *not* an alternate name.
    """
    value = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not value:
        return ()
    values = [value]
    match = BRACKETED_METADATA.fullmatch(value)
    if match:
        outside, inside = (part.strip() for part in match.groups())
        if outside and inside and not edition_tags(value) and strip_version(value) == value and not re.match(
            r"^(?:feat(?:uring)?\.?|ft\.?|with)\s+", inside, re.I
        ):
            values.extend((outside, inside))
    # Only split a complete CJK/Latin two-part name, not a multilingual lyric
    # title containing several fragments (which could refer to a different song).
    bilingual = BILINGUAL_METADATA.fullmatch(value)
    if bilingual:
        values.extend(part for part in bilingual.groups() if part)
    return tuple(dict.fromkeys(values))


@lru_cache(maxsize=1024)
def metadata_variants(value: str) -> tuple[str, ...]:
    """General queries: observed spellings, script forms, then kana romanization.

    Original metadata and explicitly supplied bilingual components come first.
    Unverifiable phonetic guesses are not submitted as catalog queries.
    """
    components = metadata_components(value)
    if not components:
        return ()
    choices = list(components)
    for component in components:
        choices.extend(chinese_variants(component)[1:])
    for component in components:
        romanized = search_alias(component)
        if romanized:
            choices.append(romanized)
    return tuple(dict.fromkeys(v for v in choices if v.strip()))


def title_variants(value: str) -> tuple[str, ...]:
    return metadata_variants(value)


def artist_variants(value: str) -> tuple[str, ...]:
    return metadata_variants(value)


def _identity_metadata_variants(value: str) -> tuple[str, ...]:
    """Verified comparison forms, excluding unsafe CJK phonetic guesses.

    Chinese Han characters often have homophones: converting both names to
    pinyin would incorrectly equate DIFFERENT singers. Kana romanization is
    safe enough to compare exact spellings, but other transliterations may be
    used only to broaden the *search*, never to certify identity.
    """
    forms: list[str] = []
    for component in metadata_components(value):
        forms.extend(chinese_variants(component))
        if re.search(r"[\u3040-\u30ff]", component) and not re.search(r"[\u3400-\u9fff]", component):
            latin = search_alias(component)
            if latin:
                forms.append(latin)
    return tuple(dict.fromkeys(forms))


def alias_equivalent(a: str, b: str) -> bool:
    """Prove equivalent catalog spellings without global artist/song tables."""
    if not normalize(a) or not normalize(b):
        return False
    return bool(
        {normalize(v) for v in _identity_metadata_variants(a)} &
        {normalize(v) for v in _identity_metadata_variants(b)}
    )


def _alternate_metadata_spelling(value: str) -> str:
    """Prefer a second explicit metadata/script spelling, if present."""
    # Script alternatives/explicit bilingual names take precedence over
    # unverified Han-character transliteration for all provider requests.
    strict = _identity_metadata_variants(value)
    return next((part for part in strict if normalize(part) != normalize(value)), value)


def verified_metadata_queries(playback: Playback, limit: int = 4) -> tuple[str, ...]:
    """Plan a fast catalog search from actual Spotify metadata only."""
    title = strip_version(playback.title) or playback.title
    artist = primary_artist(playback.artist) or playback.artist
    titles = title_variants(title)
    artists = artist_variants(artist)
    pairings = [f"{t} {a}" for t, a in zip(titles, artists)]
    # Prioritize the exact pair, then explicitly supplied bilingual names or
    # matching Chinese-script conversions; preserve title-only discovery early.
    choices = [*pairings[:3], title]
    choices.extend(pairings[3:])
    choices.extend(f"{t} {artist}" for t in titles[1:3])
    choices.extend(f"{title} {a}" for a in artists[1:3])
    choices.extend(titles[1:3])
    return tuple(dict.fromkeys(q.strip()[:180] for q in choices if q.strip()))[:limit]


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    return SequenceMatcher(None, na, nb).ratio() if na and nb else 0.0


def metadata_similarity(a: str, b: str) -> float:
    """Compare observed names and verified script equivalents only.

    Never use phonetic Chinese pinyin to certify two differently written
    artists/titles: it can turn near-name mistakes into false matches.
    """
    if not normalize(a) or not normalize(b):
        return 0.0
    if alias_equivalent(a, b):
        return 1.0
    return max(
        similarity(va, vb)
        for va in _identity_metadata_variants(a)
        for vb in _identity_metadata_variants(b)
    )


def search_alias(name: str) -> str:
    """Transliterate kana for queries, never invent Hanzi artist identities.

    Phonetic Chinese pinyin could match multiple distinct written names yet
    fail identity validation, so do not waste provider requests on it.
    """
    if re.search(r"[\u3400-\u9fff]", name) or not re.search(r"[\u3040-\u30ff]", name):
        return ""
    ascii_name = " ".join(unidecode(name).split())
    return ascii_name if ascii_name and normalize(ascii_name) != normalize(name) else ""


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
    # A collaboration may be labelled with only one artist, but an exact
    # credit is still preferred over a different collaboration.
    artist = max(artist_whole, artist_primary * 0.94)
    # Generic identity protection: minor spelling edits (such as one deleted
    # Latin character) must not make two different artists interchangeable.
    # Exact bilingual/script variants are accepted without hardcoded aliases.
    verified_artist = (alias_equivalent(c_artist, playback.artist) or
                       alias_equivalent(primary_artist(c_artist), primary_artist(playback.artist)))
    if max(whole_title, clean_title) < 0.72:
        return -1.0
    if not verified_artist:
        latin_only = lambda text: bool(text and not re.search(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7a3]", text))
        threshold = 0.985 if latin_only(c_artist) and latin_only(playback.artist) else 0.94
        if artist < threshold:
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
    # A bare trailing version qualifier also matters, e.g. "Song Remix".
    bare = re.search(r"\b(remix|acoustic|instrumental|karaoke|demo)\s*$", title, re.I)
    if bare is not None:
        qualifiers.append(bare.group(1))
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
                if (host.endswith('y.qq.com') or host == 'music.163.com'
                        or host.endswith('kugou.com') or host.endswith('migu.cn')):
                    session.headers['User-Agent'] = (
                        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/125.0 Safari/537.36'
                    )
                    session.headers['Referer'] = (
                        'https://y.qq.com/' if host.endswith('y.qq.com')
                        else 'https://music.163.com/' if host == 'music.163.com'
                        else 'https://music.migu.cn/' if host.endswith('migu.cn')
                        else 'https://www.kugou.com/'
                    )
                self._sessions[host] = session
        return session.get(url, params=params, timeout=timeout)

    def post(self, url: str, *, json_payload: dict, timeout: tuple[float, float]):
        host = urlsplit(url).netloc
        with self._lock:
            session = self._sessions.get(host)
            if session is None:
                session = requests.Session()
                session.headers.update(self.headers)
                if host.endswith('y.qq.com'):
                    session.headers['User-Agent'] = (
                        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/125.0 Safari/537.36'
                    )
                    session.headers['Referer'] = 'https://y.qq.com/'
                self._sessions[host] = session
        return session.post(url, json=json_payload, timeout=timeout)

    def get_text(self, url: str, *, timeout: tuple[float, float]):
        """Fetch one Migu-owned HTTPS LRC document, without following redirects."""
        host = urlsplit(url).hostname or ''
        with self._lock:
            session = self._sessions.get(host)
            if session is None:
                session = requests.Session()
                session.headers.update(self.headers)
                session.headers['Referer'] = 'https://music.migu.cn/'
                self._sessions[host] = session
        return session.get(url, timeout=timeout, allow_redirects=False, stream=True)

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
        self._cache_duration_ms: dict[str, int] = {}
        self._lock = threading.RLock()
        # Each network host has a private connection pool and rate limiter.
        # Do not hold a global lock while waiting for a remote response.
        self._host_locks: dict[str, threading.Lock] = {
            host: threading.Lock() for host in (
                LRCLIB_URL, LRCAPI_URL, LRCMUX_URL, LIRIQO_URL,
                NETEASE_URL, QQ_SEARCH_URL, QQ_LYRIC_URL,
                KUGOU_SEARCH_URL, KUGOU_CANDIDATES_URL, KUGOU_LYRIC_URL,
                MIGU_SEARCH_URL,
            )
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
        self._active_lookup_key: str | None = None

    @staticmethod
    def _provider_priority(playback: Playback) -> tuple[str, ...]:
        """Prefer likely catalogs without excluding any fallback source.

        Chinese music often resolves fastest in regional sources; Western songs
        favor global APIs and Japanese songs favor the multilingual sources.
        This changes *launch order*, never the existing metadata validation.
        """
        text = f"{playback.title} {playback.artist}"
        has_kana = any('\u3040' <= ch <= '\u30ff' for ch in text)
        has_han = any('\u3400' <= ch <= '\u9fff' for ch in text)
        if has_kana:
            return ('LRCLIB', 'LiriQo', 'LRCMux', 'LrcAPI',
                    'QQ Music', 'NetEase', 'Kugou', 'Migu')
        if has_han:
            return ('LRCLIB', 'QQ Music', 'NetEase', 'Kugou',
                    'Migu', 'LrcAPI', 'LRCMux', 'LiriQo')
        return ('LRCLIB', 'LrcAPI', 'LRCMux', 'LiriQo',
                'QQ Music', 'NetEase', 'Kugou', 'Migu')

    def _cancel_lookup_for_other_track(self, key: str) -> None:
        """A cached new song must still cancel obsolete network workers."""
        with self._active_lookup_lock:
            if (self._active_lookup_cancel is not None and
                    self._active_lookup_key != key):
                self._active_lookup_cancel.set()

    def _disk_cache_path(self, playback: Playback) -> Path:
        digest = hashlib.sha256(playback.key.encode('utf-8')).hexdigest()
        return self.local_dir / 'synced_cache' / f'{digest}.json'

    def _read_synced_cache(self, playback: Playback) -> LyricsResult | None:
        """Read only small, versioned, non-expired, validated cached LRC lines."""
        path = self._disk_cache_path(playback)
        try:
            stat = path.stat()
            if stat.st_size <= 0 or stat.st_size > MAX_DISK_CACHE_BYTES:
                return None
            payload = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(payload, dict) or payload.get('v') != SYNCED_DISK_CACHE_VERSION:
                return None
            saved = payload.get('created')
            if isinstance(saved, bool) or not isinstance(saved, (float, int)) or not math.isfinite(saved):
                return None
            age = time.time() - saved
            if not (0 <= age <= SYNCED_DISK_CACHE_TTL_SECONDS):
                return None
            recorded_ms = payload.get('duration_ms', 0)
            if type(recorded_ms) is not int or recorded_ms < 0:
                return None
            if playback.duration_ms and recorded_ms and abs(playback.duration_ms - recorded_ms) > 8000:
                return None
            source, rows = payload.get('source'), payload.get('lines')
            if not isinstance(source, str) or not (0 < len(source) <= 96):
                return None
            if not isinstance(rows, list) or not (1 <= len(rows) <= 750):
                return None
            lines: list[LyricLine] = []
            for item in rows:
                if (not isinstance(item, list) or len(item) != 2 or
                        type(item[0]) is not int or not (0 <= item[0] < 3_600_000) or
                        not isinstance(item[1], str) or not (0 < len(item[1]) <= 1000)):
                    return None
                lines.append(LyricLine(start_ms=item[0], text=item[1]))
            if not any(line.start_ms > 0 for line in lines):
                return None
            return LyricsResult(kind='synced', lines=tuple(sorted(lines)), source=source)
        except (OSError, UnicodeError, ValueError, TypeError, OverflowError):
            return None

    def _write_synced_cache(self, playback: Playback, result: LyricsResult) -> None:
        if not result.is_synced or len(result.lines) > 750:
            return
        path = self._disk_cache_path(playback)
        temp_path: str | None = None
        try:
            payload = {'v': SYNCED_DISK_CACHE_VERSION, 'created': time.time(),
                       'duration_ms': max(0, playback.duration_ms),
                       'source': result.source[:96],
                       'lines': [[line.start_ms, line.text] for line in result.lines]}
            encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            if len(encoded) > MAX_DISK_CACHE_BYTES:
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(prefix='.tmp-lyric-', suffix='.json', dir=path.parent)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(encoded)
            os.replace(temp_path, path)
            temp_path = None
        except OSError as exc:
            LOG.debug('Unable to save verified synced lyric cache: %s', exc)
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

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
            self._cache_duration_ms[playback.key] = playback.duration_ms
            self._cache_deadline.pop(playback.key, None)
        return result

    def clear_cache(self, playback: Playback) -> None:
        with self._lock:
            self._cache.pop(playback.key, None)
            self._cache_duration_ms.pop(playback.key, None)
            self._cache_deadline.pop(playback.key, None)
        try:
            self._disk_cache_path(playback).unlink(missing_ok=True)
        except OSError as exc:
            LOG.debug('Unable to remove synced lyric cache: %s', exc)

    def fetch(self, playback: Playback, *, force: bool = False) -> LyricsResult:
        # Even when a new track hits cache, stop pending searches for the old one.
        self._cancel_lookup_for_other_track(playback.key)
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
            recorded_duration = self._cache_duration_ms.get(playback.key, 0)
            matching_duration = (not recorded_duration or not playback.duration_ms or
                                 abs(recorded_duration - playback.duration_ms) <= 8000)
            if cached and not force and matching_duration and time.monotonic() < deadline:
                LOG.debug('Lyrics memory-cache hit for %r', playback.title)
                return cached
        if not force:
            disk_hit = self._read_synced_cache(playback)
            if disk_hit is not None:
                with self._lock:
                    self._cache[playback.key] = disk_hit
                    self._cache_duration_ms[playback.key] = playback.duration_ms
                    self._cache_deadline.pop(playback.key, None)
                LOG.info('Synced lyrics disk-cache hit for %r / %r', playback.title, playback.artist)
                return disk_hit
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
                self._cache_duration_ms[playback.key] = playback.duration_ms
                if result.kind == "missing":
                    self._cache_deadline[playback.key] = time.monotonic() + UNSYNCED_CACHE_SECONDS
                else:
                    self._cache_deadline.pop(playback.key, None)
            if result.is_synced:
                self._write_synced_cache(playback, result)
        return result

    def _check_cancelled(self) -> None:
        event = getattr(self._worker_local, "cancel_event", None)
        if event is not None and event.is_set():
            raise LyricsSearchCancelled()

    def _note_provider_outage(self, base: str, reason: str) -> None:
        """Apply a bounded circuit-breaker to optional catalogue hosts.

        Call with its per-host lock held. Other hosts continue to work even if
        one endpoint times out, refuses requests, or temporarily returns 5xx.
        """
        if base not in OPTIONAL_PROVIDER_URLS:
            return
        failures = self._provider_failures.get(base, 0) + 1
        self._provider_failures[base] = failures
        delay = min(LIRIQO_FAILURE_BACKOFF_SECONDS * 2 ** min(failures - 1, 4),
                    LIRIQO_MAX_FAILURE_BACKOFF_SECONDS)
        self._provider_cooldown_until[base] = time.monotonic() + delay
        LOG.warning("Optional lyrics provider %s unavailable (%s); retry in %ss", base, reason, delay)

    def _note_provider_healthy(self, base: str) -> None:
        if base in OPTIONAL_PROVIDER_URLS:
            self._provider_failures.pop(base, None)
            self._provider_cooldown_until.pop(base, None)

    def _request_json(self, base: str, path: str, params: dict,
                      *, json_payload: dict | None = None) -> dict | list | None:
        self._check_cancelled()
        lock = self._host_locks[base]
        while not lock.acquire(timeout=0.075):
            # A previous-song request may still own this host. An obsolete
            # search should not queue more HTTP calls behind it.
            self._check_cancelled()
        try:
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
            timeout = (2.5, 6.0) if base == LIRIQO_URL else (2.2, 5.0)
            try:
                response = (self._session.post(base + path, json_payload=json_payload, timeout=timeout)
                            if json_payload is not None else
                            self._session.get(base + path, params=params, timeout=timeout))
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
        finally:
            lock.release()

    def _request_migu_lyric_text(self, url: str) -> str | None:
        """Get a small, verified Migu-owned HTTPS LRC body (no account data).

        Handles up to two same-provider redirects while blocking off-site,
        local/network, non-HTTPS targets and response bodies over 1 MB.
        """
        from urllib.parse import urljoin, urlunsplit

        def allowed(raw: str) -> str | None:
            try:
                parsed = urlsplit(str(raw or ''))
                hostname = (parsed.hostname or '').casefold().strip('.')
                valid = (parsed.scheme in {'https', 'http'} and
                         any(hostname == d or hostname.endswith('.' + d)
                             for d in MIGU_ALLOWED_DOMAINS) and
                         not parsed.username and not parsed.password and
                         parsed.port in (None, 80, 443))
                if not valid:
                    return None
                # Source can publish old http links; require HTTPS on the wire.
                return urlunsplit(('https', hostname, parsed.path, parsed.query, ''))
            except (ValueError, UnicodeError):
                return None

        current = allowed(url)
        if not current:
            return None
        for _ in range(3):
            self._check_cancelled()
            response = None
            try:
                response = self._session.get_text(current, timeout=(3, 7))
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get('Location', '')
                    current = allowed(urljoin(current, location)) if location else None
                    if current is None:
                        return None
                    continue
                response.raise_for_status()
                contents = bytearray()
                for chunk in response.iter_content(chunk_size=8192):
                    self._check_cancelled()
                    contents.extend(chunk)
                    if len(contents) > MAX_LYRICS_CHARS * 4:
                        return None
                return bytes(contents).decode('utf-8-sig', errors='replace')
            finally:
                if response is not None:
                    response.close()
        return None

    def _get(self, path: str, params: dict) -> dict | list | None:
        """Keep the LRCLIB client entrypoint stable for older callers/tests."""
        return self._request_json(LRCLIB_URL, path, params)

    def _fetch_online(self, playback: Playback) -> LyricsResult:
        if not playback.title or not playback.artist:
            return LyricsResult(kind="missing", message=NO_LYRICS_MESSAGE)

        # Launch the primary immediately. If it is fast, avoid requesting any
        # backups. If it is slow or inconclusive, hedge a provider every 700ms.
        # Each provider remains rate-limited independently.
        functions = {
            "LRCLIB": self._search_lrclib,
            "QQ Music": self._search_qq_music,
            "NetEase": self._search_netease,
            "Kugou": self._search_kugou,
            "Migu": self._search_migu,
            "LrcAPI": self._search_lrcapi,
            "LRCMux": self._search_lrcmux,
            "LiriQo": self._search_liriqo,
        }
        providers = tuple((name, functions[name]) for name in self._provider_priority(playback))
        pending: queue.Queue[tuple[str, object]] = queue.Queue()
        cancelled = threading.Event()
        with self._active_lookup_lock:
            if self._active_lookup_cancel is not None:
                self._active_lookup_cancel.set()  # obsolete song / manual retry
            self._active_lookup_cancel = cancelled
            self._active_lookup_key = playback.key
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
                if launched < len(providers) and (
                        launched < PROVIDER_INITIAL_PARALLEL or
                        active == 0 or now >= next_hedge):
                    name, fn = providers[launched]
                    threading.Thread(
                        target=lookup, args=(name, fn), daemon=True,
                        name=f"Lyrics-{name}",
                    ).start()
                    launched += 1
                    active += 1
                    next_hedge = time.monotonic() + PROVIDER_HEDGE_SECONDS
                    if launched < PROVIDER_INITIAL_PARALLEL:
                        continue  # Launch both top-ranked hosts without a wait.
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
                if failures and completed_providers < max(2, len(providers) // 2):
                    return LyricsResult(kind="error", message="部分歌詞來源暫時不可用，稍後自動重試")
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
            # A partial outage must never be silently described as a
            # definitive catalogue miss. The UI can retry a recoverable error.
            if failures:
                LOG.info("No synchronized lyrics; %d provider(s) completed, %d unavailable: %s",
                         completed_providers, len(failures), ", ".join(failures))
                if completed_providers < max(2, len(providers) // 2):
                    return LyricsResult(kind="error", message="部分歌詞來源暫時不可用，稍後自動重試")
            LOG.info("No verified lyrics after all providers for %r / %r (%dms) in %.0fms",
                     playback.title, playback.artist, playback.duration_ms,
                     (time.monotonic() - began) * 1000)
            return LyricsResult(kind="missing", message=NO_LYRICS_MESSAGE)
        finally:
            cancelled.set()
            with self._active_lookup_lock:
                if self._active_lookup_cancel is cancelled:
                    self._active_lookup_cancel = None
                    self._active_lookup_key = None

    def _search_lrclib(self, playback: Playback) -> tuple[LyricsResult | None, LyricsResult | None, LyricsResult | None]:
        best_plain: LyricsResult | None = None
        instrumental: LyricsResult | None = None
        base_title = strip_version(playback.title) or playback.title
        lead_artist = primary_artist(playback.artist) or playback.artist
        titles = title_variants(base_title)
        artists = artist_variants(lead_artist)
        # A second observed/script spelling, never an artist-specific alias table.
        native_title = _alternate_metadata_spelling(base_title)
        native_artist = _alternate_metadata_spelling(lead_artist)

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

        # Query with metadata-derived spellings BEFORE broad title-only search. The latter
        # frequently returns unrelated songs or covers with matching titles.
        queries: list[dict[str, str]] = [
            {"track_name": playback.title, "artist_name": playback.artist},
            {"track_name": native_title, "artist_name": native_artist},
            {"track_name": base_title, "artist_name": lead_artist},
        ]
        # Chinese song names often differ by script across catalogues.
        # Explicitly add converted pairs, not merely a broad title-only query.
        for title in chinese_variants(base_title):
            for artist in chinese_variants(lead_artist):
                queries.append({"track_name": title, "artist_name": artist})
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
        native_title = _alternate_metadata_spelling(base_title)
        native_artist = _alternate_metadata_spelling(lead_artist)
        base_params = {"title": base_title, "artist": lead_artist}
        queries: list[dict[str, str]] = []
        if playback.album:
            queries.append(dict(base_params, album=playback.album))
        queries.append(dict(base_params))
        for title in chinese_variants(base_title):
            for artist in chinese_variants(lead_artist):
                queries.append({"title": title, "artist": artist})
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
        native_title = _alternate_metadata_spelling(base_title)
        native_artist = _alternate_metadata_spelling(lead_artist)
        pairs = [
            (base_title, lead_artist),
            (native_title, native_artist),
            *((title, artist) for title in chinese_variants(base_title)
              for artist in chinese_variants(lead_artist)),
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
        native_title = _alternate_metadata_spelling(base_title)
        native_artist = _alternate_metadata_spelling(lead_artist)
        pairs = [(base_title, lead_artist), (native_title, native_artist),
                 *((title, artist) for title in chinese_variants(base_title)
                   for artist in chinese_variants(lead_artist))]
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

    def _search_netease(self, playback: Playback) -> LyricsResult | None:
        """Use NetEase's read-only web search + timestamped LRC response.

        This is a best-effort consumer endpoint, not an authenticated account
        integration; keep strict metadata checks and fall back on failures.
        """
        for query in verified_metadata_queries(playback, limit=5):
            self._check_cancelled()
            params = {'s': query, 'type': 1, 'limit': 20, 'offset': 0}
            payload = self._request_json(NETEASE_URL, '/api/search/get/web', params)
            result = payload.get('result') if isinstance(payload, dict) else None
            songs = result.get('songs') if isinstance(result, dict) else None
            if not isinstance(songs, list):
                # Older Web clients use the shorter endpoint. Never issue
                # both when the preferred endpoint returned valid results.
                payload = self._request_json(NETEASE_URL, '/api/search/get', params)
                result = payload.get('result') if isinstance(payload, dict) else None
                songs = result.get('songs') if isinstance(result, dict) else None
            if not isinstance(songs, list):
                continue
            ranked: list[tuple[float, int, dict]] = []
            for song in songs[:20]:
                if not isinstance(song, dict):
                    continue
                artists = song.get('artists') or song.get('ar')
                names = [str(a.get('name')).strip() for a in artists
                         if isinstance(a, dict) and a.get('name')] if isinstance(artists, list) else []
                # Duration is normally milliseconds on NetEase search results.
                duration = song.get('duration', song.get('dt'))
                seconds = (duration / 1000.0 if isinstance(duration, (int, float)) and
                           not isinstance(duration, bool) and duration > 5000 else duration)
                match = {'trackName': song.get('name'), 'artistName': ', '.join(names),
                         'duration': seconds}
                score = candidate_score(match, playback)
                song_id = song.get('id')
                if score >= 0.85 and type(song_id) is int and 0 < song_id < 10**12:
                    ranked.append((score, song_id, match))
            for score, song_id, match in sorted(ranked, reverse=True)[:2]:
                self._check_cancelled()
                lyric_data = self._request_json(NETEASE_URL, '/api/song/lyric', {
                    'id': song_id, 'lv': 1, 'kv': -1, 'tv': -1,
                })
                if not isinstance(lyric_data, dict):
                    continue
                raw = lyric_data.get('lrc')
                lyric = raw.get('lyric') if isinstance(raw, dict) else None
                if not isinstance(lyric, str) or len(lyric) > MAX_LYRICS_CHARS:
                    continue
                result = text_to_lyrics(lyric, None, source='網易雲音樂')
                if result.is_synced:
                    LOG.info('NetEase verified track: %r / %r (%.3f)',
                             match['trackName'], match['artistName'], score)
                    return result
        return None

    def _search_qq_music(self, playback: Playback) -> LyricsResult | None:
        """Search QQ Music's read-only song catalogue and standard LRC field.

        No account cookies, authentication tokens or decrypted player assets
        are needed. Some songs offer QRC only and are intentionally skipped.
        """
        module = 'music.search.SearchCgiService'
        for query in verified_metadata_queries(playback, limit=5):
            self._check_cancelled()
            request = {
                module: {
                    'module': module,
                    'method': 'DoSearchForQQMusicDesktop',
                    'param': {'search_type': 0, 'query': query,
                              'page_num': 1, 'num_per_page': 20},
                },
            }
            payload = self._request_json(QQ_SEARCH_URL, '/cgi-bin/musicu.fcg', {},
                                         json_payload=request)
            if not isinstance(payload, dict):
                continue
            envelope = payload.get(module) or payload.get('req_1') or payload.get('req_0')
            response = envelope.get('data') if isinstance(envelope, dict) else None
            body = response.get('body') if isinstance(response, dict) else None
            collection = body.get('song') if isinstance(body, dict) else None
            songs = collection.get('list') if isinstance(collection, dict) else None
            if not isinstance(songs, list):
                continue
            ranked: list[tuple[float, str, dict]] = []
            for song in songs[:20]:
                if not isinstance(song, dict):
                    continue
                artists = song.get('singer')
                names = [str(a.get('name')).strip() for a in artists
                         if isinstance(a, dict) and a.get('name')] if isinstance(artists, list) else []
                match = {'trackName': song.get('name') or song.get('title'),
                         'artistName': ', '.join(names), 'duration': song.get('interval')}
                score = candidate_score(match, playback)
                mid = song.get('mid') or song.get('songmid')
                if score >= 0.85 and isinstance(mid, str) and re.fullmatch(r'[A-Za-z0-9]{6,40}', mid):
                    ranked.append((score, mid, match))
            for score, mid, match in sorted(ranked, reverse=True)[:2]:
                self._check_cancelled()
                lyrics = self._request_json(QQ_LYRIC_URL,
                    '/lyric/fcgi-bin/fcg_query_lyric_new.fcg',
                    {'songmid': mid, 'format': 'json', 'g_tk': '5381',
                     'loginUin': '0', 'hostUin': '0', 'platform': 'yqq',
                     'needNewCode': '0', 'outCharset': 'utf8'})
                if not isinstance(lyrics, dict):
                    continue
                encoded = lyrics.get('lyric')
                if not isinstance(encoded, str) or len(encoded) > MAX_LYRICS_CHARS * 2:
                    continue
                try:
                    raw = base64.b64decode(encoded, validate=True).decode('utf-8-sig')
                except (binascii.Error, ValueError, UnicodeError):
                    continue
                result = text_to_lyrics(raw, None, source='QQ 音樂')
                if result.is_synced:
                    LOG.info('QQ Music verified track: %r / %r (%.3f)',
                             match['trackName'], match['artistName'], score)
                    return result
        return None

    def _search_kugou(self, playback: Playback) -> LyricsResult | None:
        """Kugou song catalogue -> verified hash -> LRC candidate -> timed LRC.

        Two independent steps protect against accepting a same-title cover or
        an unrelated song from broad keyword-only lyric searches.
        """
        for query in verified_metadata_queries(playback, limit=4):
            self._check_cancelled()
            data = self._request_json(KUGOU_SEARCH_URL, '/song_search_v2', {
                'keyword': query, 'page': 1, 'pagesize': 20,
                'platform': 'WebFilter', 'filter': 2, 'iscorrection': 1,
            })
            collection = data.get('data') if isinstance(data, dict) else None
            songs = collection.get('lists') if isinstance(collection, dict) else None
            if not isinstance(songs, list):
                continue
            ranked: list[tuple[float, str, dict]] = []
            for song in songs[:20]:
                if not isinstance(song, dict):
                    continue
                filename = str(song.get('FileName') or song.get('filename') or '')
                title = song.get('SongName') or song.get('songname')
                artist = song.get('SingerName') or song.get('singername')
                if not artist and ' - ' in filename:
                    artist = filename.split(' - ', 1)[0]
                if not title and ' - ' in filename:
                    title = filename.split(' - ', 1)[1]
                duration = song.get('Duration', song.get('duration'))
                if (isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration > 5000
                        or isinstance(duration, str) and duration.isdecimal() and int(duration) > 5000):
                    duration = float(duration) / 1000
                meta = {'trackName': title, 'artistName': artist, 'duration': duration}
                score = candidate_score(meta, playback)
                song_hash = song.get('FileHash') or song.get('Hash') or song.get('hash')
                if score >= 0.85 and isinstance(song_hash, str) and re.fullmatch(r'[0-9A-Fa-f]{32}', song_hash):
                    ranked.append((score, song_hash, meta))
            for score, song_hash, meta in sorted(ranked, key=lambda p: p[0], reverse=True)[:2]:
                self._check_cancelled()
                candidates_data = self._request_json(KUGOU_CANDIDATES_URL, '/search', {
                    'ver': 1, 'man': 'yes', 'client': 'mobi',
                    'hash': song_hash,
                    'duration': playback.duration_ms or -1,
                })
                candidates = (candidates_data.get('candidates')
                              if isinstance(candidates_data, dict) else None)
                if not isinstance(candidates, list):
                    continue
                for candidate in candidates[:3]:
                    if not isinstance(candidate, dict):
                        continue
                    lyric_id, accesskey = candidate.get('id'), candidate.get('accesskey')
                    if type(lyric_id) is int and lyric_id > 0:
                        lyric_id = str(lyric_id)
                    if (not isinstance(lyric_id, str) or not lyric_id.isdigit() or
                            not isinstance(accesskey, str) or
                            not re.fullmatch(r'[A-Za-z0-9]{8,96}', accesskey)):
                        continue
                    payload = self._request_json(KUGOU_LYRIC_URL, '/download', {
                        'ver': 1, 'client': 'pc', 'id': lyric_id,
                        'accesskey': accesskey, 'fmt': 'lrc', 'charset': 'utf8',
                    })
                    encoded = payload.get('content') if isinstance(payload, dict) else None
                    if not isinstance(encoded, str) or len(encoded) > MAX_LYRICS_CHARS * 2:
                        continue
                    try:
                        raw = base64.b64decode(encoded, validate=True).decode('utf-8-sig')
                    except (binascii.Error, UnicodeError, ValueError):
                        continue
                    found = text_to_lyrics(raw, None, source='酷狗音樂')
                    if found.is_synced:
                        LOG.info('Kugou verified track %r / %r score %.3f',
                                 meta['trackName'], meta['artistName'], score)
                        return found
        return None

    def _search_migu(self, playback: Playback) -> LyricsResult | None:
        """Migu web search + metadata verification + allowlisted HTTPS LRC URL."""
        for query in verified_metadata_queries(playback, limit=4):
            self._check_cancelled()
            payload = self._request_json(MIGU_SEARCH_URL,
                '/v1.0/content/search_all.do', {
                    'text': query, 'pageNo': 1, 'pageSize': 20,
                    'isCopyright': 1, 'sort': 1,
                    'searchSwitch': json.dumps({
                        'song': 1, 'album': 0, 'singer': 0, 'tagSong': 0,
                        'mvSong': 0, 'bestShow': 1,
                    }, separators=(',', ':')),
                })
            container = payload.get('songResultData') if isinstance(payload, dict) else None
            songs = container.get('result') if isinstance(container, dict) else None
            if not isinstance(songs, list):
                continue
            ranked = []
            for song in songs[:20]:
                if not isinstance(song, dict):
                    continue
                singers = song.get('singers') or song.get('singer') or []
                artists = [str(s.get('name')).strip() for s in singers
                           if isinstance(s, dict) and s.get('name')] if isinstance(singers, list) else []
                duration = song.get('duration') or song.get('songDuration')
                if (isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration > 5000
                        or isinstance(duration, str) and duration.isdecimal() and int(duration) > 5000):
                    duration = float(duration) / 1000
                meta = {'trackName': song.get('name') or song.get('songName'),
                        'artistName': ', '.join(artists), 'duration': duration}
                score = candidate_score(meta, playback)
                lyric_url = song.get('lyricUrl') or song.get('lrcUrl')
                if isinstance(lyric_url, str) and lyric_url.startswith('//'):
                    lyric_url = 'https:' + lyric_url
                if not lyric_url:
                    copyright_id = song.get('copyrightId')
                    # The fixed Migu lyric API is a backup if no lyricUrl is
                    # published. Never request arbitrary candidate URLs.
                    if isinstance(copyright_id, (str, int)) and not isinstance(copyright_id, bool):
                        identifier = str(copyright_id)
                        if re.fullmatch(r'[A-Za-z0-9]{1,40}', identifier):
                            lyric_url = ('https://music.migu.cn/v3/api/music/audioPlayer/getLyric'
                                         '?copyrightId=' + identifier)
                if score >= 0.85 and isinstance(lyric_url, str):
                    ranked.append((score, lyric_url, meta))
            for score, lyric_url, meta in sorted(ranked, key=lambda p: p[0], reverse=True)[:3]:
                self._check_cancelled()
                raw = self._request_migu_lyric_text(lyric_url)
                if not raw:
                    continue
                # Migu occasionally returns JSON rather than a raw LRC file.
                if raw.lstrip().startswith('{'):
                    try:
                        obj = json.loads(raw)
                        if isinstance(obj, dict):
                            data = obj.get('data')
                            raw = obj.get('lyric') or (data.get('lyric') if isinstance(data, dict) else '') or ''
                    except ValueError:
                        continue
                found = text_to_lyrics(raw, None, source='咪咕音樂')
                if found.is_synced:
                    LOG.info('Migu verified track %r / %r score %.3f',
                             meta['trackName'], meta['artistName'], score)
                    return found
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
