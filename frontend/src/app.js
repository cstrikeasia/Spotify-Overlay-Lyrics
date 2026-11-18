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
