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
