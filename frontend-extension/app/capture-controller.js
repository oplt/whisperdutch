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
      this.onSilence = options.onSilence || (() => {});
      this.onAudioRestored = options.onAudioRestored || (() => {});
      this.setTimeout = options.setTimeout || root.setTimeout.bind(root);
      this.clearTimeout = options.clearTimeout || root.clearTimeout.bind(root);
      this.silenceTimeoutMs = Math.max(1000, Number(options.silenceTimeoutMs) || 8000);
      this.stream = null;
      this.context = null;
      this.source = null;
      this.worklet = null;
      this.monitor = null;
      this.processorSink = null;
      this.acceptingAudio = false;
      this.paused = false;
      this.generation = 0;
      this.droppedChunks = 0;
      this.lastBackpressureAt = 0;
      this.volume = 1;
      this.muted = false;
      this.silenceTimer = null;
      this.heardAudio = false;
      this.silenceWarned = false;
      this.sourceType = this.supportsTabCapture() ? "tab" : "audio-input";
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
      this.processorSink = context.createGain();
      this.processorSink.gain.value = 0;
      this.applyMonitor();
      this.worklet.port.postMessage({ type: "config", targetSampleRate: TARGET_SAMPLE_RATE });
      this.source.connect(this.worklet);
      // Keep the processor in Brave's destination graph. A dangling AudioWorklet
      // can be treated as inaudible and stop receiving render callbacks.
      this.worklet.connect(this.processorSink);
      this.processorSink.connect(context.destination);
      // Chromium tabCapture replaces the tab's normal playback, so route it
      // back to the speakers. Firefox captures an OS monitor input whose audio
      // is already playing and must not be duplicated.
      if (this.sourceType === "tab") {
        this.source.connect(this.monitor);
        this.monitor.connect(context.destination);
      }
      this.worklet.port.onmessage = event => this.handleAudio(event);
      if (context.state === "suspended") await context.resume();
      if (context.state !== "running") {
        throw new Error("The browser blocked audio processing. Click Retry, then allow audio capture.");
      }
      this.acceptingAudio = true;
      this.paused = false;
      this.watchForSilence();
      return true;
    }

    async captureTabAudio(tabId) {
      if (!this.supportsTabCapture()) return this.captureAudioInput();

      const consumerTabId = await this.currentTabId();
      const options = { targetTabId: tabId };
      if (consumerTabId) options.consumerTabId = consumerTabId;

      return new Promise((resolve, reject) => {
        this.chrome.tabCapture.getMediaStreamId(options, async streamId => {
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

    supportsTabCapture() {
      return typeof this.chrome?.tabCapture?.getMediaStreamId === "function";
    }

    async captureAudioInput() {
      const getUserMedia = this.navigator?.mediaDevices?.getUserMedia;
      if (typeof getUserMedia !== "function") {
        throw new Error("This Firefox installation cannot access an audio input.");
      }

      let stream;
      try {
        stream = await getUserMedia.call(this.navigator.mediaDevices, {
          audio: {
            echoCancellation: false,
            noiseSuppression: false,
            autoGainControl: false
          },
          video: false
        });
      } catch (error) {
        if (["NotAllowedError", "SecurityError"].includes(error?.name)) {
          throw new Error("Firefox needs audio permission. Click Retry and select your system audio monitor in the permission dialog.");
        }
        if (["NotFoundError", "OverconstrainedError"].includes(error?.name)) {
          throw new Error("Firefox cannot find a usable audio source. Expose a PipeWire/PulseAudio monitor, then click Retry.");
        }
        throw error;
      }

      if (!stream?.getAudioTracks?.().length) {
        stream?.getTracks?.().forEach(track => track.stop());
        throw new Error("Firefox returned no audio track. Click Retry and select a system audio monitor, not a microphone.");
      }
      return stream;
    }

    currentTabId() {
      if (!this.chrome?.tabs?.getCurrent) return Promise.resolve(null);
      return new Promise(resolve => {
        this.chrome.tabs.getCurrent(tab => {
          // Treat lookup failure as a compatibility fallback. Capturing from the
          // calling extension page still works when consumerTabId is omitted.
          if (this.chrome.runtime.lastError) {
            resolve(null);
            return;
          }
          resolve(Number.isInteger(tab?.id) && tab.id > 0 ? tab.id : null);
        });
      });
    }

    handleAudio(event) {
      const pcm = event.data?.pcm || event.data;
      const level = Number(event.data?.level) || 0;
      this.onLevel(level);
      if (level > 0.005 && !this.heardAudio) {
        this.heardAudio = true;
        this.cancelSilenceWarning();
        if (this.silenceWarned) this.onAudioRestored();
      }
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
      this.cancelSilenceWarning();
    }

    watchForSilence() {
      this.cancelSilenceWarning();
      this.heardAudio = false;
      this.silenceWarned = false;
      this.silenceTimer = this.setTimeout(() => {
        this.silenceTimer = null;
        if (this.acceptingAudio && !this.paused && !this.heardAudio) {
          this.silenceWarned = true;
          this.onSilence();
        }
      }, this.silenceTimeoutMs);
    }

    cancelSilenceWarning() {
      if (this.silenceTimer === null) return;
      this.clearTimeout(this.silenceTimer);
      this.silenceTimer = null;
    }

    async close() {
      this.stopAcceptingAudio();
      const worklet = this.worklet;
      const source = this.source;
      const monitor = this.monitor;
      const processorSink = this.processorSink;
      const context = this.context;
      const stream = this.stream;
      this.worklet = null;
      this.source = null;
      this.monitor = null;
      this.processorSink = null;
      this.context = null;
      this.stream = null;
      this.onLevel(0);
      this.safe(() => { if (worklet) worklet.port.onmessage = null; });
      this.safe(() => worklet?.disconnect());
      this.safe(() => source?.disconnect());
      this.safe(() => monitor?.disconnect());
      this.safe(() => processorSink?.disconnect());
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
