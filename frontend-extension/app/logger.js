(function (root) {
  const REMOTE_INFO_EVENTS = new Set([
    "capture_started",
    "capture_stopped",
    "websocket_recovered",
    "backend_restart"
  ]);

  class ClientLogger {
    constructor(client = root.BackendClient, options = {}) {
      this.client = client;
      this.debug = Boolean(options.debug);
      this.buffer = [];
      this.flushTimer = null;
    }

    log(level, message, context = {}) {
      if (level === "debug" && !this.debug) return;
      const safeContext = { ...context };
      delete safeContext.dutch;
      delete safeContext.translation;
      delete safeContext.transcript;
      const record = {
        ts: new Date().toISOString(),
        level,
        source: "subtitle-window",
        message,
        context: safeContext
      };
      const method = level === "error" ? "error" : level === "warn" ? "warn" : "log";
      root.console?.[method]?.("[DutchSubtitles]", message, safeContext);
      if (level === "error" || level === "warn" || REMOTE_INFO_EVENTS.has(message)) {
        this.buffer.push(record);
        this.scheduleFlush();
      }
    }

    scheduleFlush() {
      if (this.flushTimer !== null) return;
      this.flushTimer = root.setTimeout(() => this.flush(), 250);
    }

    flush() {
      this.flushTimer = null;
      const records = this.buffer.splice(0, this.buffer.length);
      records.forEach(record => this.client?.postClientLog(record).catch(() => {}));
    }
  }

  const api = { ClientLogger, REMOTE_INFO_EVENTS };
  root.SubtitleApp = Object.assign(root.SubtitleApp || {}, api);
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
