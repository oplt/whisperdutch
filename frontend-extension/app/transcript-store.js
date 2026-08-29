(function (root) {
  const STORAGE_KEY = "subtitleSavedSessions";

  class TranscriptStore {
    constructor(storage = root.localStorage) {
      this.storage = storage;
      this.items = [];
      this.byId = new Map();
      this.sessionId = `session-${Date.now()}`;
      this.sessionName = "Untitled session";
    }

    addPending(payload, startMs) {
      this.closePrevious(startMs);
      const item = {
        id: payload.id || `local-${Date.now()}`,
        startMs,
        endMs: startMs + 3500,
        dutch: payload.dutch || "",
        translation: "",
        pending: true,
        mode: payload.mode || "balanced",
        quality: payload.quality || null
      };
      this.items.push(item);
      this.byId.set(item.id, item);
      return item;
    }

    finalize(payload, startMs) {
      let item = payload.id ? this.byId.get(payload.id) : null;
      if (!item) {
        this.closePrevious(startMs);
        item = {
          id: payload.id || `local-${Date.now()}`,
          startMs,
          endMs: startMs + 3500,
          dutch: "",
          translation: "",
          pending: false,
          mode: payload.mode || "balanced",
          quality: null
        };
        this.items.push(item);
        this.byId.set(item.id, item);
      }
      item.dutch = payload.dutch || item.dutch;
      item.translation = payload.translation || item.translation || "Translation unavailable";
      item.pending = false;
      item.mode = payload.mode || item.mode;
      item.quality = payload.quality || item.quality;
      return item;
    }

    closePrevious(nextStartMs) {
      const previous = this.items[this.items.length - 1];
      if (previous) previous.endMs = Math.max(previous.startMs + 800, nextStartMs - 120);
    }

    clear() {
      this.items = [];
      this.byId.clear();
    }

    newSession(name = "Untitled session") {
      this.clear();
      this.sessionId = `session-${Date.now()}`;
      this.sessionName = name;
      return this.sessionId;
    }

    readSessions() {
      try {
        const parsed = JSON.parse(this.storage?.getItem(STORAGE_KEY) || "{}");
        return parsed && typeof parsed === "object" ? parsed : {};
      } catch (_error) {
        return {};
      }
    }

    listSessions() {
      return Object.values(this.readSessions())
        .sort((left, right) => String(right.savedAt).localeCompare(String(left.savedAt)));
    }

    save(name = this.sessionName) {
      this.sessionName = String(name || "Untitled session").trim() || "Untitled session";
      const sessions = this.readSessions();
      const snapshot = {
        id: this.sessionId,
        name: this.sessionName,
        savedAt: new Date().toISOString(),
        transcriptItems: this.items
      };
      sessions[this.sessionId] = snapshot;
      this.storage?.setItem(STORAGE_KEY, JSON.stringify(sessions));
      return snapshot;
    }

    restore(sessionId) {
      const snapshot = this.readSessions()[sessionId];
      if (!snapshot) return null;
      this.sessionId = snapshot.id;
      this.sessionName = snapshot.name || "Untitled session";
      this.items = Array.isArray(snapshot.transcriptItems)
        ? snapshot.transcriptItems.map(item => ({ ...item }))
        : [];
      this.byId = new Map(this.items.filter(item => item?.id).map(item => [item.id, item]));
      return snapshot;
    }
  }

  const api = { TranscriptStore, STORAGE_KEY };
  root.SubtitleApp = Object.assign(root.SubtitleApp || {}, api);
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
