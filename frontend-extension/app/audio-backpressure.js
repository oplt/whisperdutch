(function (root) {
  const PCM16_MONO_16K_BYTES_PER_SECOND = 16000 * 2;

  const STATES = {
    NORMAL: "normal",
    CONGESTED: "congested",
    DROPPING: "dropping",
    RECOVERY: "recovery"
  };

  function bufferedAudioMs(bufferedAmount, bytesPerSecond = PCM16_MONO_16K_BYTES_PER_SECOND) {
    if (!Number.isFinite(bufferedAmount) || bufferedAmount <= 0) return 0;
    return (bufferedAmount / bytesPerSecond) * 1000;
  }

  class AudioBackpressureController {
    constructor(options = {}) {
      this.bytesPerSecond = options.bytesPerSecond || PCM16_MONO_16K_BYTES_PER_SECOND;
      this.congestedEnterMs = options.congestedEnterMs ?? 600;
      this.congestedExitMs = options.congestedExitMs ?? 350;
      this.droppingEnterMs = options.droppingEnterMs ?? 1200;
      this.recoveryExitMs = options.recoveryExitMs ?? 400;
      this.state = STATES.NORMAL;
      this.droppedChunks = 0;
      this.gapEvents = 0;
      this.congestionEvents = 0;
      this.congestionStartedAt = null;
      this.longestCongestionMs = 0;
      this.lastBufferedAmount = 0;
      this.lastBufferedAudioMs = 0;
      this.pendingGapReset = false;
      this.now = options.now || (() => Date.now());
    }

    evaluate(bufferedAmount) {
      const bufferedMs = bufferedAudioMs(bufferedAmount, this.bytesPerSecond);
      this.lastBufferedAmount = bufferedAmount;
      this.lastBufferedAudioMs = bufferedMs;
      const previousState = this.state;
      this._transition(bufferedMs);

      let action = "sent";
      if (this.state === STATES.DROPPING) {
        action = "drop";
      } else if (this.state === STATES.CONGESTED || this.state === STATES.RECOVERY) {
        action = "warn";
      }

      return {
        action,
        state: this.state,
        previousState,
        bufferedAmount,
        bufferedAudioMs: bufferedMs,
        needsGapReset: this.pendingGapReset,
        stats: this.snapshot()
      };
    }

    _transition(bufferedMs) {
      switch (this.state) {
        case STATES.NORMAL:
          if (bufferedMs >= this.droppingEnterMs) {
            this._setState(STATES.DROPPING);
          } else if (bufferedMs >= this.congestedEnterMs) {
            this._setState(STATES.CONGESTED);
          }
          break;
        case STATES.CONGESTED:
          if (bufferedMs >= this.droppingEnterMs) {
            this._setState(STATES.DROPPING);
          } else if (bufferedMs <= this.congestedExitMs) {
            this._setState(STATES.NORMAL);
          }
          break;
        case STATES.DROPPING:
          if (bufferedMs <= this.recoveryExitMs) {
            this._setState(STATES.RECOVERY);
            this.pendingGapReset = true;
          }
          break;
        case STATES.RECOVERY:
          if (bufferedMs >= this.droppingEnterMs) {
            this._setState(STATES.DROPPING);
            this.pendingGapReset = false;
          } else if (bufferedMs <= this.congestedExitMs && !this.pendingGapReset) {
            this._setState(STATES.NORMAL);
          }
          break;
        default:
          break;
      }
    }

    _setState(nextState) {
      if (nextState === this.state) return;
      const now = this.now();
      if (nextState === STATES.CONGESTED || nextState === STATES.DROPPING) {
        if (this.congestionStartedAt === null) {
          this.congestionStartedAt = now;
          this.congestionEvents += 1;
        }
      }
      if (nextState === STATES.NORMAL && this.congestionStartedAt !== null) {
        const duration = now - this.congestionStartedAt;
        this.longestCongestionMs = Math.max(this.longestCongestionMs, duration);
        this.congestionStartedAt = null;
      }
      this.state = nextState;
    }

    recordDrop() {
      this.droppedChunks += 1;
    }

    noteGapSent() {
      this.pendingGapReset = false;
      this.gapEvents += 1;
      if (this.state === STATES.RECOVERY) {
        this._setState(STATES.NORMAL);
      }
    }

    snapshot() {
      const now = this.now();
      return {
        state: this.state,
        bufferedAmount: this.lastBufferedAmount,
        bufferedAudioMs: Math.round(this.lastBufferedAudioMs),
        droppedChunks: this.droppedChunks,
        gapEvents: this.gapEvents,
        congestionEvents: this.congestionEvents,
        longestCongestionMs: this.longestCongestionMs,
        activeCongestionMs: this.congestionStartedAt ? Math.max(0, now - this.congestionStartedAt) : 0,
        pendingGapReset: this.pendingGapReset
      };
    }
  }

  const api = {
    AudioBackpressureController,
    PCM16_MONO_16K_BYTES_PER_SECOND,
    bufferedAudioMs,
    BACKPRESSURE_STATES: STATES
  };
  root.SubtitleApp = Object.assign(root.SubtitleApp || {}, api);
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
