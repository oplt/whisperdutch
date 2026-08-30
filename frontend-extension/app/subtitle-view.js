(function (root) {
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
      this.feed = byId("subtitleFeed");
      this.level = byId("inputLevelBar");
      this.announcement = byId("subtitleAnnouncement");
      this.captureHelp = byId("audioSourceHelp");
      this.currentId = null;
      this.currentItem = null;
      this.historyRows = new Map();
      this.levelFrame = null;
      this.pendingLevel = 0;
      this.onDutchWordClick = null;
      this.sourceLang = "nl";
      this.targetLang = "en";
      this.liveRow = this.createRow(true);
      this.feed.appendChild(this.liveRow);
      this.renderIdleLiveRow();
    }

    setCaptureSource(sourceType) {
      if (!this.captureHelp) return;
      const firefoxInput = sourceType === "audio-input";
      this.captureHelp.hidden = !firefoxInput;
      this.captureHelp.textContent = firefoxInput
        ? "Firefox: choose a PipeWire/PulseAudio monitor source in the audio permission dialog. Your microphone will not capture the video sound."
        : "";
    }

    createRow(isLive = false) {
      const row = this.document.createElement("article");
      row.className = isLive ? "subtitle-row is-live" : "subtitle-row";
      const dutch = this.document.createElement("p");
      dutch.className = "subtitle-dutch";
      dutch.lang = this.sourceLang;
      dutch.dir = "auto";
      const translation = this.document.createElement("p");
      translation.className = "subtitle-translation";
      translation.lang = this.targetLang;
      translation.dir = "auto";
      row.append(dutch, translation);
      return row;
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

    renderRow(row, item) {
      const sourceLang = item.sourceLang || this.sourceLang;
      const targetLang = item.targetLang || this.targetLang;
      row.querySelector(".subtitle-dutch").lang = sourceLang;
      this.renderDutch(row.querySelector(".subtitle-dutch"), item);
      const translation = row.querySelector(".subtitle-translation");
      translation.lang = targetLang;
      translation.textContent = item.pending
        ? "Translating…"
        : item.translation || (item.dutch ? "Translation unavailable" : this.idleTranslation());
      translation.dataset.pending = String(Boolean(item.pending));
    }

    sentenceTranslation(item) {
      if (!item || item.pending) return "";
      const translation = String(item.translation || "").trim();
      if (!translation || translation === this.idleTranslation() || translation === "Translation unavailable") return "";
      if (translation === "Translation follows when the phrase is complete.") return "";
      return translation;
    }

    isClickableDutch(item) {
      return Boolean(item?.dutch && item.dutch !== this.idleSource() && typeof this.onDutchWordClick === "function");
    }

    renderDutch(element, item) {
      const text = item?.dutch || "";
      element.replaceChildren();
      if (!this.isClickableDutch(item)) {
        element.textContent = text;
        return;
      }
      const parts = root.SubtitleRenderer.splitDutchText(text);
      parts.forEach(part => {
        if (part.type === "text") {
          element.append(part.value);
          return;
        }
        const button = this.document.createElement("button");
        button.type = "button";
        button.className = "subtitle-word";
        button.textContent = part.value;
        button.title = `Add "${part.value}" to practising vocabulary`;
        button.setAttribute("aria-label", `Add ${part.value} to practising vocabulary`);
        button.addEventListener("click", event => {
          event.preventDefault();
          event.stopPropagation();
          this.onDutchWordClick({
            word: part.value,
            dutchSentence: text,
            englishSentence: this.sentenceTranslation(item),
            sourceLanguage: item.sourceLang || this.sourceLang,
            targetLanguage: item.targetLang || this.targetLang
          });
        });
        element.append(button);
      });
    }

    renderIdleLiveRow() {
      this.renderRow(this.liveRow, {
        dutch: this.idleSource(),
        translation: this.idleTranslation(),
        sourceLang: this.sourceLang,
        targetLang: this.targetLang,
        pending: false
      });
    }

    showPartial(dutch, sourceLang = this.sourceLang, targetLang = this.targetLang) {
      if (this.currentItem?.pending) return;
      if (this.currentItem?.dutch || this.currentItem?.translation) {
        this.moveCurrentToHistory();
      } else {
        this.currentId = null;
        this.currentItem = null;
      }
      if (!dutch) {
        this.renderIdleLiveRow();
        return;
      }
      this.renderRow(this.liveRow, {
        dutch,
        translation: "Translation follows when the phrase is complete.",
        sourceLang,
        targetLang,
        pending: true
      });
    }

    idleSource() {
      return `Listening for ${this.languageName(this.sourceLang)} speech…`;
    }

    idleTranslation() {
      return `${this.languageName(this.targetLang)} translation will appear here.`;
    }

    languageName(code) {
      return root.SubtitleApp?.languageName?.(code) || String(code || "").toUpperCase();
    }

    setLanguages(sourceLang, targetLang) {
      this.sourceLang = sourceLang || "nl";
      this.targetLang = targetLang || "en";
      if (!this.currentItem) this.renderIdleLiveRow();
    }

    showPending(item) {
      if (this.currentItem && this.currentId !== item.id) this.moveCurrentToHistory();
      this.currentId = item.id;
      this.currentItem = item;
      this.renderRow(this.liveRow, item);
    }

    showFinal(item) {
      if (this.currentId === item.id) {
        this.currentItem = item;
        this.renderRow(this.liveRow, item);
      } else if (this.historyRows.has(item.id)) {
        const row = this.historyRows.get(item.id);
        this.renderRow(row, item);
        this.promoteHistoryRow(row);
      } else if (!this.currentItem) {
        this.currentId = item.id;
        this.currentItem = item;
        this.renderRow(this.liveRow, item);
      } else {
        this.prependHistory(item);
      }
      if (this.announcement) this.announcement.textContent = item.translation || item.dutch;
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
        this.promoteHistoryRow(existing);
        return;
      }
      const row = this.createRow(false);
      row.dataset.subtitleId = item.id;
      this.renderRow(row, item);
      this.feed.insertBefore(row, this.liveRow.nextSibling);
      this.historyRows.set(item.id, row);
    }

    promoteHistoryRow(row) {
      if (row === this.liveRow || row === this.liveRow.nextSibling) return;
      this.feed.insertBefore(row, this.liveRow.nextSibling);
    }

    restore(items) {
      this.clear({ keepLiveRow: true });
      const visible = items.filter(item => item.dutch || item.translation);
      if (!visible.length) {
        this.renderIdleLiveRow();
        return;
      }
      const current = visible[visible.length - 1];
      visible.slice(0, -1).reverse().forEach(item => this.prependHistory(item));
      this.currentId = current.id;
      this.currentItem = current;
      this.renderRow(this.liveRow, current);
    }

    clear(options = {}) {
      this.currentId = null;
      this.currentItem = null;
      this.historyRows.clear();
      [...this.feed.children].forEach(child => {
        if (child !== this.liveRow) child.remove();
      });
      if (options.keepLiveRow !== true) {
        if (this.liveRow.parentNode !== this.feed) this.feed.prepend(this.liveRow);
      }
      this.renderIdleLiveRow();
      this.setLatency(null);
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

  const api = { SubtitleView, STATE_LABELS };
  root.SubtitleApp = Object.assign(root.SubtitleApp || {}, api);
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
