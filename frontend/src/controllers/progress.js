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
