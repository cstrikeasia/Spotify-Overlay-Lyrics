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
