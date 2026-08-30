(function (root) {
  const TARGET_SAMPLE_RATE = 16000;
  const DEFAULT_AUDIO_LEVEL_THRESHOLD = 0.005;
  const DEFAULT_CAPTURE_LOSS_TIMEOUT_MS = 60000;

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
      this.now = options.now || (() => Date.now());
      this.silenceTimeoutMs = Math.max(1000, Number(options.silenceTimeoutMs) || 8000);
      this.captureLossTimeoutMs = Math.max(
        this.silenceTimeoutMs + 1000,
        Number(options.captureLossTimeoutMs) || DEFAULT_CAPTURE_LOSS_TIMEOUT_MS
      );
      this.audioLevelThreshold = Number(options.audioLevelThreshold) || DEFAULT_AUDIO_LEVEL_THRESHOLD;
      this.batchDurationMs = Number(options.batchDurationMs) || 20;
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
      this.captureLossTimer = null;
      this.heardAudio = false;
      this.silenceWarned = false;
      this.lastAudibleAt = 0;
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
      this.worklet.port.postMessage({
        type: "config",
        targetSampleRate: TARGET_SAMPLE_RATE,
        batchDurationMs: this.batchDurationMs
      });
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
      if (level > this.audioLevelThreshold) {
        this._noteAudibleActivity(level);
      }
      if (!this.acceptingAudio || this.paused) return;
      const result = this.socket.sendAudio(pcm);
      if (result === "sent") return;
      const stats = typeof this.socket.getBackpressureStats === "function"
        ? this.socket.getBackpressureStats()
        : { droppedChunks: this.droppedChunks, bufferedAudioMs: 0, state: result };
      if (result === "drop") this.droppedChunks = stats.droppedChunks;
      const now = Date.now();
      if (now - this.lastBackpressureAt < 1500) return;
      this.lastBackpressureAt = now;
      this.onBackpressure({ result, ...stats });
      this.logger?.log(result === "drop" ? "warn" : "debug", "websocket_backpressure", {
        result,
        ...stats
      });
    }

    setPaused(paused) {
      if (!this.active) return false;
      this.paused = Boolean(paused);
      if (this.paused) {
        this._cancelCaptureLossWatch();
      } else if (this.heardAudio) {
        this._scheduleCaptureLossWatch();
      }
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
      this._cancelSilenceWatchdog();
    }

    watchForSilence() {
      this._cancelSilenceWatchdog();
      this.heardAudio = false;
      this.silenceWarned = false;
      this.lastAudibleAt = 0;
      this.silenceTimer = this.setTimeout(() => {
        this.silenceTimer = null;
        if (this.acceptingAudio && !this.paused && !this.heardAudio) {
          this._raiseSilenceWarning("initial_silence", this.silenceTimeoutMs);
        }
      }, this.silenceTimeoutMs);
    }

    _noteAudibleActivity(_level) {
      const now = this.now();
      this.lastAudibleAt = now;
      const wasSilentWarning = this.silenceWarned;
      if (!this.heardAudio) {
        this.heardAudio = true;
        this._cancelInitialSilenceWatch();
      }
      if (wasSilentWarning) {
        this.silenceWarned = false;
        this.onAudioRestored();
      }
      if (this.acceptingAudio && !this.paused) {
        this._scheduleCaptureLossWatch();
      }
    }

    _scheduleCaptureLossWatch() {
      this._cancelCaptureLossWatch();
      this.captureLossTimer = this.setTimeout(() => {
        this.captureLossTimer = null;
        if (!this.acceptingAudio || this.paused || !this.heardAudio) return;
        this._raiseSilenceWarning("capture_loss", Math.max(this.captureLossTimeoutMs, this.now() - this.lastAudibleAt));
      }, this.captureLossTimeoutMs);
    }

    _raiseSilenceWarning(reason, silentForMs) {
      if (this.silenceWarned) return;
      this.silenceWarned = true;
      this.onSilence({ reason, silentForMs });
      this.logger?.log("warn", "capture_silence_detected", {
        reason,
        silentForMs,
        sourceType: this.sourceType
      });
    }

    _cancelInitialSilenceWatch() {
      if (this.silenceTimer === null) return;
      this.clearTimeout(this.silenceTimer);
      this.silenceTimer = null;
    }

    _cancelCaptureLossWatch() {
      if (this.captureLossTimer === null) return;
      this.clearTimeout(this.captureLossTimer);
      this.captureLossTimer = null;
    }

    _cancelSilenceWatchdog() {
      this._cancelInitialSilenceWatch();
      this._cancelCaptureLossWatch();
    }

    cancelSilenceWarning() {
      this._cancelSilenceWatchdog();
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

  const api = {
    CaptureController,
    TARGET_SAMPLE_RATE,
    DEFAULT_AUDIO_LEVEL_THRESHOLD,
    DEFAULT_CAPTURE_LOSS_TIMEOUT_MS
  };
  root.SubtitleApp = Object.assign(root.SubtitleApp || {}, api);
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
