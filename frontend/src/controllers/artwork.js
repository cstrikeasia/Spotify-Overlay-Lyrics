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
