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
