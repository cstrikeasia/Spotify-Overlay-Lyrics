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
