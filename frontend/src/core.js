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
