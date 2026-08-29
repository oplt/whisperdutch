(function (root) {
  const MAX_RENDERED_SUBTITLES = 100;
  const STATE_LABELS = Object.freeze({
    idle: "Ready",
    "starting-backend": "Starting service",
    connecting: "Connecting",
    capturing: "Listening",
    paused: "Paused",
    reconnecting: "Reconnecting",
    stopping: "Stopping",
    error: "Needs attention"
  });

  class SubtitleView {
    constructor(documentRef = root.document) {
      this.document = documentRef;
      const byId = id => documentRef.getElementById(id);
      this.status = byId("statusText");
      this.statusGroup = byId("statusGroup");
      this.latency = byId("latency");
      this.startButton = byId("startBtn");
      this.pauseButton = byId("pauseBtn");
      this.stopButton = byId("stopBtn");
      this.retryButton = byId("retryBtn");
      this.dutch = byId("currentDutch");
      this.translation = byId("currentTranslation");
      this.history = byId("historySubtitles");
      this.historyCount = byId("historyCount");
      this.level = byId("inputLevelBar");
      this.announcement = byId("subtitleAnnouncement");
      this.currentId = null;
      this.currentItem = null;
      this.historyRows = new Map();
      this.levelFrame = null;
      this.pendingLevel = 0;
    }

    renderState(snapshot) {
      const state = snapshot.value;
      this.status.textContent = snapshot.detail || STATE_LABELS[state];
      this.statusGroup.dataset.state = state;
      const busy = ["starting-backend", "connecting", "reconnecting", "stopping"].includes(state);
      this.startButton.hidden = state === "capturing" || state === "paused";
      this.startButton.disabled = busy || state === "error";
      this.pauseButton.hidden = state !== "capturing" && state !== "paused";
      this.pauseButton.disabled = busy;
      this.pauseButton.textContent = state === "paused" ? "Resume" : "Pause";
      this.stopButton.hidden = !["capturing", "paused", "reconnecting"].includes(state);
      this.stopButton.disabled = state === "stopping";
      this.retryButton.hidden = state !== "error";
    }

    setStatus(message) {
      this.status.textContent = message;
    }

    setLatency(value) {
      this.latency.textContent = typeof value === "number" ? `${Math.round(value)} ms` : "";
      this.latency.hidden = typeof value !== "number";
    }

    showPartial(dutch) {
      if (this.currentItem?.pending) return;
      this.currentId = null;
      this.currentItem = null;
      this.dutch.textContent = dutch || "Listening for Dutch speech…";
      this.translation.textContent = dutch ? "Translation follows when the phrase is complete." : "English translation will appear here.";
      this.translation.dataset.pending = dutch ? "true" : "false";
    }

    showPending(item) {
      if (this.currentItem && this.currentId !== item.id) this.moveCurrentToHistory();
      this.currentId = item.id;
      this.currentItem = item;
      this.renderCurrent(item);
    }

    showFinal(item) {
      if (this.currentId === item.id) {
        this.currentItem = item;
        this.renderCurrent(item);
      } else if (this.historyRows.has(item.id)) {
        this.renderRow(this.historyRows.get(item.id), item);
      } else if (!this.currentItem) {
        this.currentId = item.id;
        this.currentItem = item;
        this.renderCurrent(item);
      } else {
        this.prependHistory(item);
      }
      if (this.announcement) this.announcement.textContent = item.translation || item.dutch;
    }

    renderCurrent(item) {
      this.dutch.textContent = item.dutch || "Listening for Dutch speech…";
      this.translation.textContent = item.pending ? "Translating…" : item.translation || "Translation unavailable";
      this.translation.dataset.pending = String(Boolean(item.pending));
    }

    moveCurrentToHistory() {
      if (this.currentItem) this.prependHistory(this.currentItem);
      this.currentId = null;
      this.currentItem = null;
    }

    prependHistory(item) {
      const existing = this.historyRows.get(item.id);
      if (existing) {
        this.renderRow(existing, item);
        return;
      }
      const row = this.document.createElement("article");
      row.className = "history-row";
      row.dataset.subtitleId = item.id;
      const dutch = this.document.createElement("p");
      dutch.className = "history-dutch";
      const translation = this.document.createElement("p");
      translation.className = "history-translation";
      row.append(dutch, translation);
      this.renderRow(row, item);
      this.history.prepend(row);
      this.historyRows.set(item.id, row);
      while (this.history.childElementCount > MAX_RENDERED_SUBTITLES) {
        const last = this.history.lastElementChild;
        if (!last) break;
        this.historyRows.delete(last.dataset.subtitleId);
        last.remove();
      }
      this.updateHistoryCount();
    }

    renderRow(row, item) {
      row.querySelector(".history-dutch").textContent = item.dutch || "";
      const translation = row.querySelector(".history-translation");
      translation.textContent = item.pending ? "Translating…" : item.translation || "Translation unavailable";
      translation.dataset.pending = String(Boolean(item.pending));
    }

    restore(items) {
      this.clear();
      const visible = items.filter(item => item.dutch || item.translation).slice(-(MAX_RENDERED_SUBTITLES + 1));
      const current = visible.pop();
      visible.forEach(item => this.prependHistory(item));
      if (current) {
        this.currentId = current.id;
        this.currentItem = current;
        this.renderCurrent(current);
      }
      this.updateHistoryCount();
    }

    clear() {
      this.currentId = null;
      this.currentItem = null;
      this.historyRows.clear();
      this.history.replaceChildren();
      this.dutch.textContent = "Listening for Dutch speech…";
      this.translation.textContent = "English translation will appear here.";
      this.translation.dataset.pending = "false";
      this.setLatency(null);
      this.updateHistoryCount();
    }

    updateHistoryCount() {
      const count = this.history.childElementCount;
      this.historyCount.textContent = count ? `${count} recent` : "Empty";
    }

    setLevel(level) {
      this.pendingLevel = Math.max(0, Math.min(1, Number(level) || 0));
      if (this.levelFrame !== null) return;
      this.levelFrame = root.requestAnimationFrame(() => {
        this.levelFrame = null;
        this.level.style.transform = `scaleX(${this.pendingLevel})`;
      });
    }

    setFontSizes(dutch, translation) {
      const clamp = (value, fallback) => Math.max(16, Math.min(96, Math.round(Number(value) || fallback)));
      const dutchSize = clamp(dutch, 48);
      const translationSize = clamp(translation, 38);
      this.document.documentElement.style.setProperty("--dutch-size", `${dutchSize}px`);
      this.document.documentElement.style.setProperty("--translation-size", `${translationSize}px`);
      return { dutch: dutchSize, translation: translationSize };
    }
  }

  const api = { SubtitleView, MAX_RENDERED_SUBTITLES, STATE_LABELS };
  root.SubtitleApp = Object.assign(root.SubtitleApp || {}, api);
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
