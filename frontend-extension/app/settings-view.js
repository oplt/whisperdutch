(function (root) {
  class SettingsView {
    constructor(documentRef = root.document, storage = root.localStorage) {
      this.document = documentRef;
      this.storage = storage;
      const byId = id => documentRef.getElementById(id);
      this.openButton = byId("settingsBtn");
      this.dialog = byId("settingsDialog");
      this.quality = byId("qualityMode");
      this.context = byId("contextPrompt");
      this.volume = byId("monitorVolume");
      this.muted = byId("muteMonitor");
      this.dutchFont = byId("dutchFontSize");
      this.translationFont = byId("translationFontSize");
      this.backendUrl = byId("backendUrl");
      this.privacy = byId("transcriptLogging");
      this.sessionName = byId("sessionName");
      this.sessionSelect = byId("sessionSelect");
      this.diagnosticsOutput = byId("diagnosticsOutput");
      this.logsOutput = byId("logsOutput");
      this.deviceInputs = Array.from(documentRef.querySelectorAll('input[name="asrDevice"]'));
      this.contextTimer = null;
      this.bindDialog();
    }

    bindDialog() {
      this.openButton.addEventListener("click", () => this.open());
      this.dialog.addEventListener("close", () => this.openButton.focus());
    }

    open() {
      if (typeof this.dialog.showModal === "function") this.dialog.showModal();
      else this.dialog.setAttribute("open", "");
      this.dialog.querySelector("button, input, select, summary")?.focus();
    }

    load() {
      const mode = this.storage.getItem("subtitleQualityMode");
      this.quality.value = ["fast", "balanced", "quality"].includes(mode) ? mode : "balanced";
      this.context.value = this.storage.getItem("subtitleContextPrompt") || "";
      this.volume.value = this.storage.getItem("subtitleMonitorVolume") || "1";
      this.muted.checked = this.storage.getItem("subtitleMonitorMuted") === "1";
      this.dutchFont.value = this.storage.getItem("dutchSubtitleFontSize") || "48";
      this.translationFont.value = this.storage.getItem("translationSubtitleFontSize") || "38";
      this.backendUrl.value = root.BackendClient.getWsUrl();
      const device = this.device;
      this.deviceInputs.forEach(input => { input.checked = input.value === device; });
      return this.values();
    }

    get device() {
      const value = this.storage.getItem("subtitleAsrDevice");
      return ["cpu", "cuda"].includes(value) ? value : "cpu";
    }

    values() {
      return {
        mode: this.quality.value,
        contextPrompt: this.context.value.trim(),
        volume: Number(this.volume.value),
        muted: this.muted.checked,
        dutchFont: Number(this.dutchFont.value),
        translationFont: Number(this.translationFont.value),
        device: this.device
      };
    }

    bind(callbacks = {}) {
      this.quality.addEventListener("change", () => {
        this.storage.setItem("subtitleQualityMode", this.quality.value);
        callbacks.onConfig?.(this.values());
      });
      this.context.addEventListener("input", () => {
        this.storage.setItem("subtitleContextPrompt", this.context.value);
        root.clearTimeout(this.contextTimer);
        this.contextTimer = root.setTimeout(() => callbacks.onConfig?.(this.values()), 350);
      });
      const monitorChanged = () => {
        this.storage.setItem("subtitleMonitorVolume", this.volume.value);
        this.storage.setItem("subtitleMonitorMuted", this.muted.checked ? "1" : "0");
        callbacks.onMonitor?.(this.values());
      };
      this.volume.addEventListener("input", monitorChanged);
      this.muted.addEventListener("change", monitorChanged);
      const fontChanged = () => {
        callbacks.onFonts?.(this.values());
      };
      this.dutchFont.addEventListener("change", fontChanged);
      this.translationFont.addEventListener("change", fontChanged);
      this.deviceInputs.forEach(input => input.addEventListener("change", () => {
        if (!input.checked) return;
        this.storage.setItem("subtitleAsrDevice", input.value);
        callbacks.onDevice?.(input.value);
      }));
      this.backendUrl.addEventListener("change", () => {
        const connection = root.BackendClient.setWsUrl(this.backendUrl.value, { source: "manual" });
        this.backendUrl.value = connection?.wsUrl || root.BackendClient.getWsUrl();
        callbacks.onBackendUrl?.(connection);
      });
      this.privacy.addEventListener("change", () => callbacks.onPrivacy?.(this.privacy.checked));
      this.document.getElementById("restartBackendBtn").addEventListener("click", () => callbacks.onRestart?.());
      this.document.getElementById("stopBackendBtn").addEventListener("click", () => callbacks.onStopBackend?.());
      this.document.getElementById("refreshDiagnosticsBtn").addEventListener("click", () => callbacks.onDiagnostics?.());
      this.document.getElementById("refreshLogsBtn").addEventListener("click", () => callbacks.onLogs?.());
      this.document.getElementById("newSessionBtn").addEventListener("click", () => callbacks.onNewSession?.());
      this.document.getElementById("saveSessionBtn").addEventListener("click", () => callbacks.onSaveSession?.());
      this.document.getElementById("restoreSessionBtn").addEventListener("click", () => callbacks.onRestoreSession?.());
      this.document.getElementById("clearSessionBtn").addEventListener("click", () => callbacks.onClearSession?.());
      ["txt", "vtt", "srt"].forEach(format => {
        this.document.getElementById(`export${format.toUpperCase()}Btn`)
          .addEventListener("click", () => callbacks.onExport?.(format));
      });
    }

    persistFonts(sizes) {
      this.dutchFont.value = String(sizes.dutch);
      this.translationFont.value = String(sizes.translation);
      this.storage.setItem("dutchSubtitleFontSize", String(sizes.dutch));
      this.storage.setItem("translationSubtitleFontSize", String(sizes.translation));
    }

    renderSessions(sessions, selectedId = "") {
      this.sessionSelect.replaceChildren();
      sessions.forEach(session => {
        const option = this.document.createElement("option");
        option.value = session.id;
        option.textContent = `${session.name || "Untitled session"} · ${new Date(session.savedAt).toLocaleString()}`;
        this.sessionSelect.appendChild(option);
      });
      if (selectedId) this.sessionSelect.value = selectedId;
    }

    showDiagnostics(data) {
      this.diagnosticsOutput.textContent = [
        `Native host: ${data.nativeHost}`,
        `Backend: ${data.backend}`,
        `Models: ${data.models}`
      ].join("\n");
    }

    showLogs(data) {
      this.logsOutput.textContent = (data.lines || []).join("\n") || "No backend log entries.";
    }
  }

  const api = { SettingsView };
  root.SubtitleApp = Object.assign(root.SubtitleApp || {}, api);
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
