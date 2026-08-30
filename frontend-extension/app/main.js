(function (root) {
  const App = root.SubtitleApp;

  class SubtitleApplication {
    constructor() {
      const params = new URLSearchParams(root.location.search);
      this.tabId = Number(params.get("tabId"));
      this.autoStart = params.get("autostart") === "1";
      this.state = new App.AppState();
      this.view = new App.SubtitleView();
      this.settings = new App.SettingsView();
      this.logger = new App.ClientLogger();
      this.backend = new App.BackendService({
        logger: this.logger,
        onProgress: progress => this.view.setStatus(progress.message)
      });
      this.store = new App.TranscriptStore();
      this.glossary = new App.GlossaryController();
      this.vocabulary = new App.VocabularyController();
      this.socket = new App.SubtitleSocket({
        logger: this.logger,
        onMessage: payload => this.handleMessage(payload),
        onDisconnect: () => this.handleDisconnect(),
        onReconnectAttempt: ({ attempt, maxAttempts }) => {
          this.view.setStatus(`Reconnecting, attempt ${attempt} of ${maxAttempts}`);
        }
      });
      this.capture = new App.CaptureController({
        socket: this.socket,
        logger: this.logger,
        onLevel: level => this.view.setLevel(level),
        onBackpressure: ({ result, droppedChunks }) => {
          this.view.setStatus(result === "drop"
            ? `Backend is catching up, ${droppedChunks} audio chunks skipped`
            : "Backend is catching up");
        },
        onSilence: () => {
          this.view.setStatus(this.capture.sourceType === "tab"
            ? "No tab sound detected. Start playback in the captured video tab."
            : "No system sound detected. In Firefox, select a monitor audio source instead of a microphone.");
        },
        onAudioRestored: () => {
          if (this.state.value === "capturing") this.view.setStatus("Listening");
        }
      });
      this.startedAt = null;
      this.stablePartial = "";
      this.stopPromise = null;
    }

    async init() {
      this.view.onDutchWordClick = payload => {
        const result = this.vocabulary.addFromSubtitle(payload);
        if (!result) return;
        this.view.setStatus(result.created
          ? `Added "${payload.word}" to practising vocabulary`
          : `"${payload.word}" is already in practising vocabulary`);
      };
      this.state.subscribe(snapshot => this.view.renderState(snapshot));
      const values = this.settings.load();
      this.applyLanguages(values);
      this.view.setCaptureSource(this.capture.sourceType);
      this.settings.setMonitorSupported(this.capture.sourceType === "tab");
      this.applyMonitor(values);
      this.applyFonts(values);
      this.settings.bind(this.settingsCallbacks());
      this.bindPrimaryControls();
      this.settings.renderSessions(this.store.listSessions());
      void this.loadPrivacy();

      if (!Number.isInteger(this.tabId) || this.tabId <= 0) {
        this.state.begin("starting-backend", "No source tab selected");
        this.state.transition("error", "Reopen the extension from the video tab you want to translate.");
        return;
      }
      this.state.reset(this.capture.sourceType === "tab"
        ? "Ready"
        : "Ready — click Start and select your system audio monitor");
      if (this.autoStart) await this.start();
    }

    bindPrimaryControls() {
      this.view.startButton.addEventListener("click", () => this.start());
      this.view.pauseButton.addEventListener("click", () => this.togglePause());
      this.view.stopButton.addEventListener("click", () => this.stop());
      this.view.retryButton.addEventListener("click", () => this.start());
      root.addEventListener("keydown", event => this.handleShortcut(event));
      root.addEventListener("beforeunload", () => {
        this.capture.stopAcceptingAudio();
        void this.socket.close({ graceful: false });
        void this.capture.close();
      });
    }

    settingsCallbacks() {
      return {
        onConfig: () => this.sendConfig(),
        onLanguages: values => {
          this.stablePartial = "";
          this.applyLanguages(values);
          this.sendConfig();
          this.view.setStatus(`${App.languageName(values.sourceLang)} → ${App.languageName(values.targetLang)}`);
        },
        onMonitor: values => this.applyMonitor(values),
        onFonts: values => this.applyFonts(values),
        onRestart: () => this.restartBackend(),
        onStopBackend: () => this.stopBackend(),
        onDiagnostics: () => this.refreshDiagnostics(),
        onLogs: () => this.refreshLogs(),
        onPrivacy: enabled => this.updatePrivacy(enabled),
        onNewSession: () => this.newSession(),
        onSaveSession: () => this.saveSession(),
        onRestoreSession: () => this.restoreSession(),
        onClearSession: () => this.clearSession(),
        onExport: format => this.export(format)
      };
    }

    async start({ restart = false } = {}) {
      if (!["idle", "error"].includes(this.state.value)) return;
      const generation = this.state.value === "error"
        ? this.state.retry(this.capture.sourceType === "tab" ? "Preparing tab audio" : "Requesting system audio")
        : this.state.begin("starting-backend", this.capture.sourceType === "tab" ? "Preparing tab audio" : "Requesting system audio");
      this.stablePartial = "";
      try {
        // Capture first while Brave still associates this page with the user's
        // extension-button click. Model warmup can otherwise outlive that grant
        // and leave a valid-looking stream which contains only silence.
        const started = await this.capture.start(this.tabId);
        if (!started || !this.state.owns(generation)) {
          await this.cleanup(false);
          return;
        }
        const connection = await this.backend.ensureReady({ restart, device: this.settings.device });
        if (!this.state.owns(generation)) return;
        this.state.transition("connecting", "Connecting to local service");
        await this.socket.connect(connection?.wsUrl || root.BackendClient.getWsUrl());
        if (!this.state.owns(generation)) {
          await this.socket.close({ graceful: false });
          return;
        }
        this.sendConfig();
        this.startedAt = Date.now();
        this.state.transition("capturing", "Listening");
        this.logger.log("info", "capture_started", { mode: this.settings.values().mode });
      } catch (error) {
        await this.fail(generation, error);
      }
    }

    togglePause() {
      if (this.state.value === "capturing") {
        this.capture.setPaused(true);
        this.state.transition("paused", "Paused");
      } else if (this.state.value === "paused") {
        this.capture.setPaused(false);
        this.state.transition("capturing", "Listening");
      }
    }

    stop(options = {}) {
      if (this.stopPromise) return this.stopPromise;
      if (!["starting-backend", "connecting", "capturing", "paused", "reconnecting"].includes(this.state.value)) {
        return Promise.resolve();
      }
      const generation = this.state.begin("stopping", "Finishing the last subtitle");
      this.stopPromise = (async () => {
        this.socket.cancelRecovery();
        this.capture.stopAcceptingAudio();
        await this.socket.close({ graceful: options.graceful !== false, timeoutMs: 8000 });
        await this.capture.close();
        if (this.state.owns(generation)) this.state.transition("idle", "Ready");
        this.logger.log("info", "capture_stopped");
      })().finally(() => { this.stopPromise = null; });
      return this.stopPromise;
    }

    async handleDisconnect() {
      if (!["capturing", "paused"].includes(this.state.value)) return;
      const generation = this.state.begin("reconnecting", "Reconnecting automatically");
      this.capture.setPaused(true);
      try {
        await this.socket.recover(async () => {
          const connection = await this.backend.ensureReady({ device: this.settings.device });
          return connection?.wsUrl || root.BackendClient.getWsUrl();
        });
        if (!this.state.owns(generation)) return;
        this.capture.setPaused(false);
        this.sendConfig();
        this.state.transition("capturing", "Listening");
      } catch (error) {
        await this.fail(generation, error);
      }
    }

    async cleanup(graceful) {
      this.capture.stopAcceptingAudio();
      await this.socket.close({ graceful, timeoutMs: 8000 });
      await this.capture.close();
    }

    async fail(generation, error) {
      await this.cleanup(false);
      if (!this.state.owns(generation)) return;
      const message = error?.message || String(error);
      this.state.transition("error", message);
      this.logger.log("error", "capture_failed", { error: message });
    }

    sendConfig() {
      const values = this.settings.values();
      this.socket.send({
        type: "config",
        sample_rate: App.TARGET_SAMPLE_RATE,
        source_lang: values.sourceLang,
        target_lang: values.targetLang,
        mode: values.mode,
        context_prompt: values.contextPrompt
      });
    }

    handleMessage(payload) {
      if (payload.type === "partial") {
        const sourceText = payload.source_text || payload.dutch;
        if (payload.is_cleared) {
          this.stablePartial = "";
        } else if (sourceText) {
          this.stablePartial = root.SubtitleRenderer.stabilizePartial(this.stablePartial, sourceText);
          this.view.showPartial(this.stablePartial, payload.source_lang, payload.target_lang);
        }
        return;
      }
      if (payload.type === "final_pending" && (payload.source_text || payload.dutch)) {
        this.stablePartial = "";
        this.view.showPending(this.store.addPending(payload, this.elapsedMs()));
        return;
      }
      if (payload.type === "final" && (payload.source_text || payload.dutch)) {
        this.stablePartial = "";
        this.view.showFinal(this.store.finalize(payload, this.elapsedMs()));
        this.view.setLatency(payload.latency_ms);
        return;
      }
      if (payload.type === "error" || payload.type === "config_error") {
        this.logger.log("error", "backend_error", { code: payload.code, message: payload.message });
        this.view.setStatus(payload.message || "The backend reported an error.");
      }
    }

    elapsedMs() {
      return this.startedAt ? Date.now() - this.startedAt : 0;
    }

    applyMonitor(values) {
      this.capture.setMonitor(values.volume, values.muted);
    }

    applyFonts(values) {
      this.settings.persistFonts(this.view.setFontSizes(values.dutchFont, values.translationFont));
    }

    applyLanguages(values) {
      this.view.setLanguages(values.sourceLang, values.targetLang);
      this.vocabulary.setLanguages(values.sourceLang, values.targetLang);
    }

    async restartBackend() {
      const resume = ["capturing", "paused", "reconnecting"].includes(this.state.value);
      if (resume) await this.stop();
      if (this.state.value !== "idle") return;
      if (resume) await this.start({ restart: true });
      else {
        this.view.setStatus("Restarting local service");
        await this.backend.restart(this.settings.device)
          .then(() => this.view.setStatus("Local service ready"))
          .catch(error => this.view.setStatus(error?.message || String(error)));
      }
    }

    async stopBackend() {
      if (["capturing", "paused", "reconnecting"].includes(this.state.value)) await this.stop();
      await this.backend.stop()
        .then(() => this.view.setStatus("Local service stopped"))
        .catch(error => this.view.setStatus(error?.message || String(error)));
    }

    async refreshDiagnostics() {
      this.settings.showDiagnostics(await this.backend.diagnostics());
    }

    async refreshLogs() {
      this.settings.showLogs(await root.BackendClient.fetchBackendLogs(120));
    }

    async loadPrivacy() {
      const data = await root.BackendClient.fetchPrivacy().catch(() => null);
      if (data) this.settings.privacy.checked = Boolean(data.log_transcript_text);
    }

    async updatePrivacy(enabled) {
      const data = await root.BackendClient.savePrivacy(enabled).catch(() => null);
      if (data) this.settings.privacy.checked = Boolean(data.log_transcript_text);
    }

    newSession() {
      this.store.newSession();
      this.settings.sessionName.value = this.store.sessionName;
      this.view.clear();
    }

    saveSession() {
      const snapshot = this.store.save(this.settings.sessionName.value);
      this.settings.renderSessions(this.store.listSessions(), snapshot.id);
      this.view.setStatus("Session saved locally");
    }

    restoreSession() {
      const snapshot = this.store.restore(this.settings.sessionSelect.value);
      if (!snapshot) return;
      this.settings.sessionName.value = this.store.sessionName;
      this.view.restore(this.store.items);
      this.view.setStatus("Session restored");
    }

    clearSession() {
      this.store.clear();
      this.view.clear();
      this.view.setStatus("Transcript cleared");
    }

    export(format) {
      const exported = App.exportTranscript(this.store.items, format);
      this.view.setStatus(exported ? `Exported ${format.toUpperCase()}` : "No subtitles to export");
    }

    handleShortcut(event) {
      if (["INPUT", "TEXTAREA", "SELECT"].includes(event.target?.tagName) || event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.key === " ") {
        event.preventDefault();
        if (this.state.value === "idle" || this.state.value === "error") this.start();
        else if (["capturing", "paused", "reconnecting"].includes(this.state.value)) this.stop();
      } else if (event.key.toLowerCase() === "p") {
        event.preventDefault();
        this.togglePause();
      } else if (event.key.toLowerCase() === "m") {
        event.preventDefault();
        this.settings.muted.checked = !this.settings.muted.checked;
        this.settings.muted.dispatchEvent(new Event("change"));
      } else if (event.key.toLowerCase() === "e") {
        event.preventDefault();
        this.export("txt");
      }
    }
  }

  const api = { SubtitleApplication };
  root.SubtitleApp = Object.assign(root.SubtitleApp || {}, api);
  if (typeof module !== "undefined") module.exports = api;
  if (root.document && root.chrome) new SubtitleApplication().init();
})(typeof globalThis !== "undefined" ? globalThis : window);
