(() => {

'use strict';

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

class OverlayOptions {
  constructor() {
    const params = new URLSearchParams(location.search);
    const rawScale = params.get('scale');
    const parsedScale = rawScale === null || rawScale.trim() === '' ? NaN : Number(rawScale);
    const uiScale = Number.isFinite(parsedScale)
      ? Math.min(2.5, Math.max(0.75, parsedScale)) : 1.4;

    this.scale = uiScale;
    this.requestedCount = Number(params.get('lines'));
    this.showTrack = params.get('track') !== '0';
    this.coverOverride = params.get('cover');
    this.progressEnabled = params.get('progress') === '1' || params.get('progress') === null;
    this.reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.mixEffects = params.get('mix') !== '0' && !this.reduceMotion;
  }

  applyScale() {
    const uiScale = this.scale;
    document.documentElement.style.setProperty('--overlay-scale', String(uiScale));
    document.documentElement.style.setProperty('--overlay-extent', (100 / uiScale).toFixed(8) + '%');
  }

  showCover(state) {
    return this.coverOverride === '1' ||
      (this.coverOverride !== '0' && (this.showTrack || state.show_cover === true) && Boolean(state.title));
  }

  lineCount(state) {
    return [1, 3, 5].includes(this.requestedCount) ? this.requestedCount : state.line_count;
  }
}

class OverlayModule {
  constructor(ui, options) {
    this.ui = ui;
    this.options = options;
    this.disposed = false;
    this.cleanups = [];
  }

  listen(target, type, listener) {
    target.addEventListener(type, listener);
    this.onDispose(() => target.removeEventListener?.(type, listener));
  }

  onDispose(cleanup) {
    this.cleanups.push(cleanup);
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    for (const cleanup of this.cleanups.splice(0).reverse()) cleanup();
  }
}


class ProgressController extends OverlayModule {
  constructor(ui, options) {
    super(ui, options);
    this.fill = document.getElementById('progress-fill');
    this.animation = null;
    this.trackKey = null;
    this.durationMs = 0;
    this.positionMs = 0;
    this.sampleTime = 0;
    this.playing = false;
    this.timer = null;
  }

  static formatTime(value) {
    const seconds = Math.floor(Math.max(0, value) / 1000);
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  }

  estimatedPosition() {
    return clamp(
      this.positionMs + (this.playing ? performance.now() - this.sampleTime : 0),
      0, this.durationMs
    );
  }

  drawTime() {
    this.timer = null;
    if (this.disposed || !this.options.progressEnabled) return;

    const position = this.estimatedPosition();
    const elapsed = ProgressController.formatTime(position);
    const total = this.durationMs ? ProgressController.formatTime(this.durationMs) : '--:--';
    if (this.ui.elapsed !== elapsed) this.ui.elapsed = elapsed;
    if (this.ui.total !== total) this.ui.total = total;
    this.ui.progressPercent = this.durationMs ? Math.round(position / this.durationMs * 100) : 0;

    if (this.playing && this.durationMs > 0 && position < this.durationMs && !document.hidden) {
      this.timer = window.setTimeout(() => this.drawTime(), 1000);
    }
  }

  scheduleTime() {
    this.suspend();
    this.drawTime();
  }

  suspend() {
    if (this.timer !== null) window.clearTimeout(this.timer);
    this.timer = null;
  }

  stopAnimation() {
    if (this.animation) this.animation.cancel();
    this.animation = null;
    this.fill.style.clipPath = 'inset(0 100% 0 0 round 9999px)';
  }

  render(state) {
    if (!this.options.progressEnabled || this.disposed) return;

    const progressFill = this.fill;
    const key = String(state.track_key || '');
    const duration = clamp(Number(state.duration_ms) || 0, 0, 24 * 60 * 60 * 1000);
    const position = duration ? clamp(Number(state.position_ms) || 0, 0, duration) : 0;
    const playing = Boolean(key && duration && state.is_playing && position < duration);
    const newTimeline = key !== this.trackKey || duration !== this.durationMs;
    const oldVisualPosition = this.animation ? Number(this.animation.currentTime) : NaN;
    const jumped = Number.isFinite(oldVisualPosition) && Math.abs(position - oldVisualPosition) > 1000;
    const playbackChanged = playing !== this.playing;

    this.trackKey = key;
    this.durationMs = duration;
    this.positionMs = position;
    this.sampleTime = performance.now();
    this.playing = playing;
    this.ui.playing = playing;

    if (newTimeline) this.stopAnimation();
    if (!duration) {
      this.stopAnimation();
      this.scheduleTime();
      return;
    }

    if (!this.animation && typeof progressFill.animate === 'function') {
      this.animation = progressFill.animate(
        [{ clipPath: 'inset(0 100% 0 0 round 9999px)' },
         { clipPath: 'inset(0 0% 0 0 round 9999px)' }],
        { duration, fill: 'both', easing: 'linear' }
      );
      this.animation.pause();
      this.animation.currentTime = position;
    }

    if (this.animation) {
      if (newTimeline || jumped || playbackChanged) this.animation.currentTime = position;
      if (playing) {
        if (this.animation.playState !== 'running') this.animation.play();
      } else {
        this.animation.pause();
      }
    } else {
      progressFill.style.clipPath = `inset(0 ${(1 - position / duration) * 100}% 0 0 round 9999px)`;
    }
    this.scheduleTime();
  }

  resume() {
    if (!this.options.progressEnabled) return;
    if (this.animation && this.playing) this.animation.currentTime = this.estimatedPosition();
    this.scheduleTime();
  }

  dispose() {
    if (this.disposed) return;
    this.suspend();
    if (this.animation) this.animation.cancel();
    this.animation = null;
    super.dispose();
  }
}


class ArtworkController extends OverlayModule {
  constructor(ui, options) {
    super(ui, options);
    this.cover = document.getElementById('cover');
    this.coverNext = document.getElementById('cover-next');
    this.activeCover = this.coverNext;
    this.loadingCover = null;
    this.trackKey = null;
    this.displayedUrl = '';
    this.wantedUrl = '';
    this.failedUrl = '';
    this.retryAt = 0;
    this.generation = 0;
    this.graceTimer = null;

    for (const img of [this.cover, this.coverNext]) {
      this.listen(img, 'load', () => this.finishLoad(img));
      this.listen(img, 'error', () => {
        if (img === this.loadingCover && img.getAttribute('src') === this.wantedUrl) {
          this.fail(this.wantedUrl);
        }
      });
    }
  }

  clearGrace() {
    if (this.graceTimer !== null) window.clearTimeout(this.graceTimer);
    this.graceTimer = null;
  }

  fadeToEmpty() {
    this.clearGrace();
    ++this.generation;
    this.wantedUrl = '';
    this.loadingCover = null;
    this.displayedUrl = '';
    this.cover.classList.remove('ready');
    this.coverNext.classList.remove('ready');
  }

  deferMissing() {
    this.clearGrace();
    if (!this.displayedUrl || !this.options.mixEffects) {
      this.fadeToEmpty();
      return;
    }
    const key = this.trackKey;
    this.graceTimer = window.setTimeout(() => {
      this.graceTimer = null;
      if (key === this.trackKey && !this.wantedUrl) this.fadeToEmpty();
    }, 1600);
  }

  finishLoad(img) {
    if (img !== this.loadingCover || !this.wantedUrl ||
        img.getAttribute('src') !== this.wantedUrl || !img.naturalWidth) return;

    const generation = this.generation;
    const url = this.wantedUrl;
    const decoded = typeof img.decode === 'function' ? img.decode() : Promise.resolve();
    Promise.resolve(decoded).then(() => {
      if (this.disposed || generation !== this.generation || img !== this.loadingCover ||
          url !== this.wantedUrl || img.getAttribute('src') !== url) return;
      requestAnimationFrame(() => {
        if (this.disposed || generation !== this.generation || img !== this.loadingCover ||
            url !== this.wantedUrl) return;
        this.clearGrace();
        img.classList.add('ready');
        (img === this.cover ? this.coverNext : this.cover).classList.remove('ready');
        this.activeCover = img;
        this.displayedUrl = url;
        this.failedUrl = '';
        this.retryAt = 0;
        this.loadingCover = null;
      });
    }).catch(() => {
      if (generation === this.generation && url === this.wantedUrl) this.fail(url);
    });
  }

  fail(url) {
    this.failedUrl = url;
    this.retryAt = performance.now() + 8000;
    this.wantedUrl = '';
    this.loadingCover = null;
    this.deferMissing();
  }

  update(key, visible, url) {
    const changed = key !== this.trackKey;
    this.trackKey = key;
    if (!visible || !key) {
      if (this.displayedUrl || this.wantedUrl || this.loadingCover || this.graceTimer !== null) {
        this.fadeToEmpty();
      }
      return;
    }

    if (url && url === this.displayedUrl) {
      this.clearGrace();
      if (this.loadingCover && this.loadingCover !== this.activeCover) {
        ++this.generation;
        this.loadingCover.classList.remove('ready');
        this.loadingCover = null;
      }
      this.wantedUrl = url;
      return;
    }

    if (!url) {
      if (changed || this.wantedUrl) {
        ++this.generation;
        this.wantedUrl = '';
        this.loadingCover = null;
        this.deferMissing();
      }
      return;
    }

    if (url === this.wantedUrl && this.loadingCover) return;
    if (url === this.failedUrl && performance.now() < this.retryAt) return;
    this.clearGrace();

    const target = this.activeCover === this.cover ? this.coverNext : this.cover;
    ++this.generation;
    this.wantedUrl = url;
    this.loadingCover = target;
    target.classList.remove('ready');
    if (target.getAttribute('src') !== url) target.setAttribute('src', url);
    else if (target.naturalWidth) this.finishLoad(target);
  }

  dispose() {
    if (this.disposed) return;
    this.clearGrace();
    ++this.generation;
    this.loadingCover = null;
    super.dispose();
  }
}


const MAX_RENDERED_LINES = 9;

class LyricHandoffController extends OverlayModule {
  constructor(ui, options) {
    super(ui, options);
    this.outgoing = document.getElementById('lyric-outgoing');
    this.trackKey = null;
    this.generation = 0;
  }

  update(nextKey, duration, activeNode) {
    if (nextKey === this.trackKey) return false;

    const hadSong = this.trackKey !== null && this.trackKey !== '';
    const line = activeNode?.textContent || '';
    this.trackKey = nextKey;
    ++this.generation;
    for (const animation of this.outgoing.getAnimations?.() || []) animation.cancel();
    this.outgoing.textContent = '';

    if (!hadSong || !line || !this.options.mixEffects || !nextKey) return true;
    this.outgoing.textContent = line;
    this.outgoing.style.fontSize = activeNode.style.fontSize || '30px';
    if (typeof this.outgoing.animate === 'function') {
      const generation = this.generation;
      const animation = this.outgoing.animate([
        { opacity: .88, transform: 'translate3d(0,-50%,0) scale(1)' },
        { opacity: 0, transform: 'translate3d(0,calc(-50% - 7px),0) scale(.985)' }
      ], {
        duration: clamp(duration, 280, 500),
        easing: 'ease-out',
        fill: 'forwards'
      });
      animation.onfinish = () => {
        if (!this.disposed && generation === this.generation) this.outgoing.textContent = '';
      };
    }
    return true;
  }

  dispose() {
    if (this.disposed) return;
    ++this.generation;
    for (const animation of this.outgoing.getAnimations?.() || []) animation.cancel();
    this.outgoing.textContent = '';
    super.dispose();
  }
}

class LyricsController extends OverlayModule {
  constructor(ui, options) {
    super(ui, options);
    this.viewport = document.getElementById('viewport');
    const rendered = new Map();
    this.rendered = rendered;
    this.previousTrack = null;
    this.previousIndex = null;
    this.previousLayout = '';
    this.renderGeneration = 0;
  }

  get activeNode() {
    return this.previousIndex !== null && this.previousIndex >= 0
      ? this.rendered.get(this.previousIndex) : null;
  }

  static positionLine(node, px, scale) {
    node.style.transform = `translate3d(0, calc(-50% + ${px.toFixed(2)}px), 0) scale(${scale.toFixed(4)})`;
  }

  static appearance(distance, count) {
    const d = Math.abs(distance);
    const size = 1 - .19 * Math.min(d, 1) - .085 * Math.min(Math.max(0, d - 1), 1);
    let alpha = d <= 1 ? 1 - .42 * d : d <= 2 ? .58 - .30 * (d - 1) : Math.max(0, .28 * (3 - d));
    const limit = count === 1 ? 0 : count === 3 ? 1 : 2;
    if (d > limit) alpha *= Math.max(0, 1 - (d - limit) / .70);
    return { size, alpha: clamp(alpha, 0, 1) };
  }

  resetLines() {
    ++this.renderGeneration;
    for (const node of this.rendered.values()) node.remove();
    this.rendered.clear();
    this.previousIndex = null;
    this.previousLayout = '';
  }

  showMessage(value, trackKey = this.previousTrack) {
    this.ui.message = value;
    this.ui.showMessage = Boolean(value);
    this.previousTrack = trackKey;
    this.resetLines();
  }

  invalidateLayout() {
    this.previousLayout = '';
  }

  renderSynced(state, size, count, ms) {
    const rendered = this.rendered;
    const viewport = this.viewport;
    const reduceMotion = this.options.reduceMotion;
    const key = state.track_key || '';
    const trackChanged = key !== this.previousTrack;
    if (trackChanged) {
      const hadTrack = this.previousTrack !== null && this.previousTrack !== '';
      this.resetLines();
      if (hadTrack && ms > 0 && typeof viewport.animate === 'function') {
        viewport.animate(
          [{ opacity: .25 }, { opacity: 1 }],
          { duration: Math.min(ms, 420), easing: 'ease-out' }
        );
      }
    }
    this.previousTrack = key;
    this.ui.showMessage = false;

    const rawActive = Number(state.active_index);
    const active = Number.isInteger(rawActive) ? Math.max(-1, rawActive) : -1;
    const valid = (Array.isArray(state.lines) ? state.lines : [])
      .filter(item => Number.isInteger(item.index) && typeof item.text === 'string' &&
                      Math.abs(item.index - active) <= 4)
      .slice(0, MAX_RENDERED_LINES);
    const ids = new Set(valid.map(item => item.index));
    let textChanged = false;
    const fresh = new Set();

    for (const item of valid) {
      let node = rendered.get(item.index);
      if (!node) {
        node = document.createElement('div');
        node.className = 'line';
        node.style.transition = 'none';
        viewport.appendChild(node);
        rendered.set(item.index, node);
        fresh.add(node);
      }
      const displayedText = item.text || '\u00a0';
      if (node.textContent !== displayedText) {
        if (!fresh.has(node)) textChanged = true;
        node.textContent = displayedText;
      }
    }

    const layout = `${size}/${count}/${viewport.clientWidth}`;
    const removed = [...rendered.keys()].some(index => !ids.has(index));
    if (active === this.previousIndex && layout === this.previousLayout && !trackChanged &&
        !textChanged && fresh.size === 0 && !removed) return;

    const isInitial = this.previousIndex === null || trackChanged;
    const isSeek = !isInitial && Math.abs(active - this.previousIndex) > 2;
    const snap = isSeek || (!isInitial && (layout !== this.previousLayout || textChanged));
    document.documentElement.style.setProperty('--motion', (snap || reduceMotion ? 0 : ms) + 'ms');
    const generation = ++this.renderGeneration;

    const sorted = valid.slice().sort((a, b) => a.index - b.index);
    const heights = new Map();
    const fontSize = size.toFixed(1) + 'px';
    for (const item of sorted) rendered.get(item.index).style.fontSize = fontSize;
    for (const item of sorted) {
      heights.set(item.index, Math.max(size * 1.30, rendered.get(item.index).scrollHeight));
    }

    const gap = Math.max(10, size * .32);
    const centers = new Map();
    let last = null;
    let y = 0;
    for (const item of sorted) {
      if (last !== null) {
        const spacing = (heights.get(last) + heights.get(item.index)) / 2 + gap;
        y += spacing * Math.max(1, item.index - last);
      }
      centers.set(item.index, y);
      last = item.index;
    }

    const anchorIndex = centers.has(active) ? active : (sorted[0] || {}).index;
    let anchor = centers.get(anchorIndex) || 0;
    if (sorted.length && active < sorted[0].index) {
      anchor -= ((heights.get(sorted[0].index) + size * 1.30) / 2 + gap) *
        (sorted[0].index - active);
    }

    const entering = [];
    for (const item of sorted) {
      const node = rendered.get(item.index);
      const distance = item.index - active;
      const visual = LyricsController.appearance(distance, count);
      const targetY = centers.get(item.index) - anchor;
      node.style.color = Math.abs(distance) < 1 ? '#f8fff9' : '#b9ccc5';
      if (fresh.has(node)) {
        if (snap || ms === 0 || reduceMotion) {
          node.style.transition = 'none';
          LyricsController.positionLine(node, targetY, visual.size);
          node.style.opacity = String(visual.alpha);
        } else {
          LyricsController.positionLine(node, targetY + (distance >= 0 ? 13 : -13), visual.size);
          node.style.opacity = '0';
          entering.push({ node, index: item.index, targetY, size: visual.size, alpha: visual.alpha });
        }
      } else {
        node.style.transition = snap ? 'none' : '';
        LyricsController.positionLine(node, targetY, visual.size);
        node.style.opacity = String(visual.alpha);
        if (snap) requestAnimationFrame(() => {
          if (generation === this.renderGeneration && rendered.get(item.index) === node) {
            node.style.transition = '';
          }
        });
      }
    }

    if (entering.length) {
      void viewport.offsetHeight;
      requestAnimationFrame(() => {
        if (generation !== this.renderGeneration || this.disposed) return;
        for (const item of entering) {
          if (rendered.get(item.index) !== item.node) continue;
          item.node.style.transition = '';
          LyricsController.positionLine(item.node, item.targetY, item.size);
          item.node.style.opacity = String(item.alpha);
        }
      });
    }

    for (const [idx, node] of rendered) {
      if (ids.has(idx)) continue;
      rendered.delete(idx);
      node.remove();
    }
    this.previousIndex = active;
    this.previousLayout = layout;
  }

  dispose() {
    if (this.disposed) return;
    this.resetLines();
    super.dispose();
  }
}


class OverlayStreamController extends OverlayModule {
  constructor(ui, options, onState, onDisconnect) {
    super(ui, options);
    this.onState = onState;
    this.onDisconnect = onDisconnect;
    this.stream = null;
    this.disconnectedTimer = null;
  }

  start() {
    if (this.stream || this.disposed) return;
    const stream = new EventSource('/events');
    this.stream = stream;

    const receive = event => {
      try {
        this.onState(JSON.parse(event.data));
        this.clearWarning();
      } catch (error) {
        console.warn('Invalid OBS lyrics stream update:', error);
      }
    };
    const opened = () => this.clearWarning();
    const failed = () => {
      if (this.disconnectedTimer !== null) return;
      this.disconnectedTimer = window.setTimeout(() => {
        this.disconnectedTimer = null;
        if (!this.disposed && stream.readyState !== EventSource.OPEN) this.onDisconnect();
      }, 3000);
    };

    stream.addEventListener('state', receive);
    stream.addEventListener('open', opened);
    stream.addEventListener('error', failed);
    this.onDispose(() => {
      stream.removeEventListener?.('state', receive);
      stream.removeEventListener?.('open', opened);
      stream.removeEventListener?.('error', failed);
    });
  }

  clearWarning() {
    if (this.disconnectedTimer !== null) window.clearTimeout(this.disconnectedTimer);
    this.disconnectedTimer = null;
  }

  dispose() {
    if (this.disposed) return;
    this.clearWarning();
    this.stream?.close?.();
    this.stream = null;
    super.dispose();
  }
}


const HERO_HOLD_MS = 3000;

class OverlayLayoutController extends OverlayModule {
  constructor(ui, options, onHoldComplete) {
    super(ui, options);
    this.onHoldComplete = onHoldComplete;
    this.trackKey = null;
    this.holding = false;
    this.holdTimer = null;
    this.ready = false;
  }

  static hasLyrics(state) {
    return state.kind === 'synced' && Array.isArray(state.lines) &&
      state.lines.some(line => Number.isInteger(line.index) &&
        typeof line.text === 'string' && line.text.trim().length > 0);
  }

  update(state) {
    const hasSong = Boolean(state.title);
    const nextKey = hasSong ? (state.track_key || `${state.title}\u0000${state.artist || ''}`) : '';

    if (nextKey !== this.trackKey) {
      this.clearHold();
      this.trackKey = nextKey;
      if (hasSong) {
        this.holding = true;
        this.holdTimer = window.setTimeout(() => {
          this.holdTimer = null;
          if (this.disposed || this.trackKey !== nextKey) return;
          this.holding = false;
          this.onHoldComplete();
        }, HERO_HOLD_MS);
      }
    }

    this.ready = hasSong && !this.holding && OverlayLayoutController.hasLyrics(state);
    this.ui.lyricsReady = this.ready;
    this.ui.hasSong = hasSong;
    return this.ready;
  }

  clearHold() {
    if (this.holdTimer !== null) window.clearTimeout(this.holdTimer);
    this.holdTimer = null;
    this.holding = false;
  }

  reset() {
    this.clearHold();
    this.trackKey = null;
    this.ready = false;
    this.ui.lyricsReady = false;
  }

  dispose() {
    if (this.disposed) return;
    this.clearHold();
    super.dispose();
  }
}


const LAYOUT_MORPH_MS = 1650;
const LAYOUT_MORPH_EASE = 'cubic-bezier(.42, 0, .22, 1)';

class OverlayMotionController extends OverlayModule {
  constructor(ui, options) {
    super(ui, options);
    this.overlay = document.getElementById('overlay');
    this.elements = {
      track: document.getElementById('track'),
      cover: document.getElementById('cover-slot'),
      progress: document.getElementById('playback-progress'),
      viewport: document.getElementById('viewport'),
      hero: document.getElementById('hero-title'),
      heroArtist: document.getElementById('hero-artist')
    };
    this.animations = new Map();
    this.cleanupTimer = null;
  }

  static bounds(node) {
    if (!node || getComputedStyle(node).display === 'none') return null;
    const { x, y, width, height } = node.getBoundingClientRect();
    return width && height ? { x, y, width, height } : null;
  }

  snapshot() {
    return Object.fromEntries(Object.entries(this.elements)
      .map(([name, node]) => [name, OverlayMotionController.bounds(node)]));
  }

  cancel() {
    if (this.cleanupTimer !== null) window.clearTimeout(this.cleanupTimer);
    this.cleanupTimer = null;
    for (const animation of this.animations.values()) animation.cancel();
    this.animations.clear();
    this.overlay.classList.remove('is-morphing');
  }

  begin() {
    if (this.disposed || !this.options.mixEffects) return null;
    const previous = this.snapshot();
    this.cancel();
    return previous;
  }

  animate(name, keyframes, options = {}) {
    const node = this.elements[name];
    if (!node || typeof node.animate !== 'function') return;
    const animation = node.animate(keyframes, {
      duration: LAYOUT_MORPH_MS,
      easing: LAYOUT_MORPH_EASE,
      fill: 'both',
      ...options
    });
    this.animations.set(name, animation);
    animation.onfinish = () => {
      if (this.animations.get(name) !== animation) return;
      this.animations.delete(name);
      animation.cancel();
    };
  }

  transition(previous, lyricsReady) {
    if (!previous || this.disposed || !this.options.mixEffects) return;
    const next = this.snapshot();
    const scale = this.options.scale;
    this.overlay.classList.add('is-morphing');

    for (const name of ['track', 'progress']) {
      const before = previous[name];
      const after = next[name];
      if (!before || !after) continue;
      const dx = (before.x - after.x) / scale;
      const dy = (before.y - after.y) / scale;
      if (Math.abs(dx) < .2 && Math.abs(dy) < .2) continue;
      this.animate(name, [
        { transform: `translate3d(${dx}px, ${dy}px, 0)` },
        { transform: 'translate3d(0, 0, 0)' }
      ]);
    }

    if (previous.track && next.track && previous.cover && next.cover) {
      const dx = ((previous.cover.x - previous.track.x) -
        (next.cover.x - next.track.x)) / scale;
      const dy = ((previous.cover.y - previous.track.y) -
        (next.cover.y - next.track.y)) / scale;
      const scaleX = previous.cover.width / next.cover.width;
      const scaleY = previous.cover.height / next.cover.height;
      this.animate('cover', [
        { transform: `translate3d(${dx}px, ${dy}px, 0) scale(${scaleX}, ${scaleY})` },
        { transform: 'translate3d(0, 0, 0) scale(1)' }
      ]);
    }

    if (!lyricsReady && previous.viewport && next.hero) {
      this.animate('hero', [
        { opacity: 0 },
        { opacity: 1 }
      ], { duration: 420, delay: 1070 });
      if (next.heroArtist) {
        this.animate('heroArtist', [
          { opacity: 0 },
          { opacity: .9 }
        ], { duration: 440, delay: 1190 });
      }
    }

    if (lyricsReady && next.viewport) {
      this.animate('viewport', [
        { opacity: 0, transform: 'translate3d(0, 24px, 0)' },
        { opacity: 1, transform: 'translate3d(0, 0, 0)' }
      ], { duration: 1100, delay: 420 });
    }

    this.cleanupTimer = window.setTimeout(() => {
      this.cleanupTimer = null;
      if (this.disposed) return;
      this.cancel();
    }, LAYOUT_MORPH_MS + 80);
  }

  dispose() {
    if (this.disposed) return;
    this.cancel();
    super.dispose();
  }
}


class OverlayApp extends OverlayModule {
  constructor(ui, nextTick) {
    const options = new OverlayOptions();
    super(ui, options);
    this.nextTick = nextTick;
    this.options.applyScale();

    this.viewport = document.getElementById('viewport');
    this.layout = new OverlayLayoutController(ui, options, () => {
      if (this.currentState) this.scheduleState(this.currentState);
    });
    this.motion = new OverlayMotionController(ui, options);
    this.progress = new ProgressController(ui, options);
    this.artwork = new ArtworkController(ui, options);
    this.lyrics = new LyricsController(ui, options);
    this.handoff = new LyricHandoffController(ui, options);
    this.stream = new OverlayStreamController(
      ui, options,
      state => this.scheduleState(state, true),
      () => {
        this.layout.reset();
        this.lyrics.showMessage('等待 Spotify 浮動歌詞連線...', null);
      }
    );

    this.currentState = null;
    this.pendingState = null;
    this.pendingProgressUpdate = false;
    this.frameScheduled = false;
    ui.progressEnabled = options.progressEnabled;
    ui.mixEffects = options.mixEffects;
  }

  start() {
    this.listen(window, 'resize', () => this.onResize());
    this.listen(document, 'visibilitychange', () => this.onVisibilityChange());
    this.stream.start();
  }

  scheduleState(state, fromStream = false) {
    if (this.disposed) return;
    this.pendingState = state;
    this.currentState = state;
    this.pendingProgressUpdate = this.pendingProgressUpdate || fromStream;
    if (this.frameScheduled) return;

    this.frameScheduled = true;
    requestAnimationFrame(() => {
      this.frameScheduled = false;
      if (this.disposed) return;
      const latest = this.pendingState;
      const updateProgress = this.pendingProgressUpdate;
      this.pendingState = null;
      this.pendingProgressUpdate = false;
      if (latest) void this.applyState(latest, updateProgress);
    });
  }

  async applyState(state, updateProgress) {
    if (this.disposed || !state || typeof state !== 'object') return;
    this.currentState = state;
    const options = this.options;
    const size = clamp(Number(state.font_size) || 30, 16, 64);
    const ms = state.smooth_lyrics === false || options.reduceMotion
      ? 0 : clamp(Number(state.transition_ms) || 620, 200, 1200);
    const count = options.lineCount(state);
    const fontSetting = size + 'px';
    if (document.documentElement.style.getPropertyValue('--font-size') !== fontSetting) {
      document.documentElement.style.setProperty('--font-size', fontSetting);
    }

    const wasReady = this.ui.lyricsReady;
    const incomingKey = state.title
      ? (state.track_key || `${state.title}\u0000${state.artist || ''}`) : '';
    const trackChanged = incomingKey !== this.layout.trackKey;
    const willBeReady = Boolean(state.title) && !trackChanged &&
      !this.layout.holding && OverlayLayoutController.hasLyrics(state);
    if (trackChanged) this.motion.cancel();
    const oldLayout = !trackChanged && wasReady !== willBeReady
      ? this.motion.begin() : null;
    const lyricsReady = this.layout.update(state);
    const nextKey = state.track_key || '';
    this.handoff.update(nextKey, ms, null);
    if (!lyricsReady) {
      const idleMessage = state.message || '等待 Spotify 播放...';
      this.lyrics.showMessage(state.title || /正在搜尋.*歌詞/.test(idleMessage) ? '' : idleMessage, nextKey);
    }
    if (this.ui.title !== (state.title || '')) this.ui.title = state.title || '';
    if (this.ui.artist !== (state.artist || '')) this.ui.artist = state.artist || '';

    const showCover = options.showCover(state);
    this.ui.coverVisible = showCover;
    this.ui.trackInfoVisible = options.showTrack;
    this.ui.trackVisible = Boolean(state.title && (options.showTrack || showCover));

    await this.nextTick();
    if (this.disposed || state !== this.currentState) return;
    this.motion.transition(oldLayout, lyricsReady);
    const nextCoverUrl = showCover && state.title ? (state.cover_url || '') : '';
    this.artwork.update(nextKey, showCover, nextCoverUrl);
    if (updateProgress) this.progress.render(state);

    if (lyricsReady) {
      const wasWaiting = this.lyrics.rendered.size === 0 && this.lyrics.previousIndex === null;
      this.lyrics.renderSynced(state, size, count, ms);
      if (wasReady && wasWaiting && options.mixEffects && nextKey &&
          typeof this.viewport.animate === 'function') {
        for (const animation of this.viewport.getAnimations()) animation.cancel();
        this.viewport.animate([{ opacity: .55 }, { opacity: 1 }],
          { duration: 330, easing: 'ease-out' });
      }
    }
  }

  onResize() {
    this.motion.cancel();
    if (this.currentState?.kind === 'synced') {
      this.lyrics.invalidateLayout();
      this.scheduleState(this.currentState);
    }
  }

  onVisibilityChange() {
    if (document.hidden) {
      this.progress.suspend();
      return;
    }
    this.progress.resume();
    this.onResize();
  }

  dispose() {
    if (this.disposed) return;
    this.stream.dispose();
    this.layout.dispose();
    this.motion.dispose();
    this.progress.dispose();
    this.artwork.dispose();
    this.handoff.dispose();
    this.lyrics.dispose();
    for (const animation of this.viewport.getAnimations?.() || []) animation.cancel();
    super.dispose();
  }
}


const { createApp, reactive, onMounted, onBeforeUnmount, nextTick } = Vue;

createApp({
  setup() {
    const ui = reactive({
      title: '',
      artist: '',
      trackVisible: false,
      hasSong: false,
      lyricsReady: false,
      trackInfoVisible: true,
      coverVisible: false,
      progressEnabled: true,
      playing: false,
      mixEffects: true,
      elapsed: '0:00',
      total: '--:--',
      progressPercent: 0,
      showMessage: true,
      message: '等待 Spotify 播放...'
    });
    let overlay = null;
    onMounted(() => {
      overlay = new OverlayApp(ui, nextTick);
      overlay.start();
    });
    onBeforeUnmount(() => overlay?.dispose());
    return { ui };
  }
}).mount('#app');


})();