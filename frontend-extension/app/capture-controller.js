(function (root) {
  const TARGET_SAMPLE_RATE = 16000;

  class CaptureController {
    constructor(options = {}) {
      this.chrome = options.chromeApi || root.chrome;
      this.navigator = options.navigatorApi || root.navigator;
      this.AudioContextImpl = options.AudioContextImpl || root.AudioContext;
      this.AudioWorkletNodeImpl = options.AudioWorkletNodeImpl || root.AudioWorkletNode;
      this.socket = options.socket;
      this.logger = options.logger;
      this.onLevel = options.onLevel || (() => {});
      this.onBackpressure = options.onBackpressure || (() => {});
      this.stream = null;
      this.context = null;
      this.source = null;
      this.worklet = null;
      this.monitor = null;
      this.acceptingAudio = false;
      this.paused = false;
      this.generation = 0;
      this.droppedChunks = 0;
      this.lastBackpressureAt = 0;
      this.volume = 1;
      this.muted = false;
    }

    get active() {
      return Boolean(this.stream && this.worklet);
    }

    async start(tabId) {
      const generation = ++this.generation;
      const stream = await this.captureTabAudio(tabId);
      if (generation !== this.generation) {
        stream.getTracks().forEach(track => track.stop());
        return false;
      }
      this.stream = stream;
      const context = new this.AudioContextImpl({ latencyHint: "interactive" });
      this.context = context;
      await context.audioWorklet.addModule("audio/worklet.js");
      if (generation !== this.generation) {
        await context.close().catch(() => {});
        stream.getTracks().forEach(track => track.stop());
        return false;
      }

      this.source = context.createMediaStreamSource(stream);
      this.worklet = new this.AudioWorkletNodeImpl(context, "pcm-worklet");
      this.monitor = context.createGain();
      this.applyMonitor();
      this.worklet.port.postMessage({ type: "config", targetSampleRate: TARGET_SAMPLE_RATE });
      this.source.connect(this.worklet);
      this.source.connect(this.monitor);
      this.monitor.connect(context.destination);
      this.worklet.port.onmessage = event => this.handleAudio(event);
      this.acceptingAudio = true;
      this.paused = false;
      return true;
    }

    captureTabAudio(tabId) {
      return new Promise((resolve, reject) => {
        this.chrome.tabCapture.getMediaStreamId({ targetTabId: tabId }, async streamId => {
          const lastError = this.chrome.runtime.lastError;
          if (lastError) {
            reject(new Error(lastError.message));
            return;
          }
          if (!streamId) {
            reject(new Error("Could not obtain the current tab audio stream."));
            return;
          }
          try {
            resolve(await this.navigator.mediaDevices.getUserMedia({
              audio: {
                mandatory: {
                  chromeMediaSource: "tab",
                  chromeMediaSourceId: streamId
                }
              },
              video: false
            }));
          } catch (error) {
            reject(error);
          }
        });
      });
    }

    handleAudio(event) {
      const pcm = event.data?.pcm || event.data;
      this.onLevel(Number(event.data?.level) || 0);
      if (!this.acceptingAudio || this.paused) return;
      const result = this.socket.sendAudio(pcm);
      if (result !== "drop" && result !== "warn") return;
      if (result === "drop") this.droppedChunks += 1;
      const now = Date.now();
      if (now - this.lastBackpressureAt < 1500) return;
      this.lastBackpressureAt = now;
      this.onBackpressure({ result, droppedChunks: this.droppedChunks });
      this.logger?.log(result === "drop" ? "warn" : "debug", "websocket_backpressure", {
        result,
        droppedChunks: this.droppedChunks
      });
    }

    setPaused(paused) {
      if (!this.active) return false;
      this.paused = Boolean(paused);
      return this.paused;
    }

    setMonitor(volume, muted) {
      const numeric = Number(volume);
      this.volume = Number.isFinite(numeric) ? Math.max(0, Math.min(1, numeric)) : 1;
      this.muted = Boolean(muted);
      this.applyMonitor();
    }

    applyMonitor() {
      if (this.monitor) this.monitor.gain.value = this.muted ? 0 : this.volume;
    }

    stopAcceptingAudio() {
      this.acceptingAudio = false;
      this.paused = false;
      this.generation += 1;
    }

    async close() {
      this.stopAcceptingAudio();
      const worklet = this.worklet;
      const source = this.source;
      const monitor = this.monitor;
      const context = this.context;
      const stream = this.stream;
      this.worklet = null;
      this.source = null;
      this.monitor = null;
      this.context = null;
      this.stream = null;
      this.onLevel(0);
      this.safe(() => { if (worklet) worklet.port.onmessage = null; });
      this.safe(() => worklet?.disconnect());
      this.safe(() => source?.disconnect());
      this.safe(() => monitor?.disconnect());
      if (context && context.state !== "closed") {
        await context.close().catch(error => {
          this.logger?.log("warn", "audio_context_close_failed", {
            error: error?.message || String(error)
          });
        });
      }
      this.safe(() => stream?.getTracks().forEach(track => track.stop()));
    }

    safe(action) {
      try {
        action();
      } catch (error) {
        this.logger?.log("warn", "capture_cleanup_failed", {
          error: error?.message || String(error)
        });
      }
    }
  }

  const api = { CaptureController, TARGET_SAMPLE_RATE };
  root.SubtitleApp = Object.assign(root.SubtitleApp || {}, api);
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
