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
