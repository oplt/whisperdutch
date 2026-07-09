(function (root) {
  function getMode(storage) {
    const saved = storage.getItem("subtitleQualityMode");
    return ["fast", "balanced", "quality"].includes(saved) ? saved : "balanced";
  }

  function getContextPrompt(storage) {
    return storage.getItem("subtitleContextPrompt") || "";
  }

  function getMonitor(storage) {
    return {
      volume: storage.getItem("subtitleMonitorVolume") || "1",
      muted: storage.getItem("subtitleMonitorMuted") === "1"
    };
  }

  function getDisplayMode(storage) {
    const saved = storage.getItem("subtitleDisplayMode");
    return ["current", "two-line", "history", "compact"].includes(saved) ? saved : "current";
  }

  function setMonitor(storage, volume, muted) {
    storage.setItem("subtitleMonitorVolume", String(volume));
    storage.setItem("subtitleMonitorMuted", muted ? "1" : "0");
  }

  const api = { getMode, getContextPrompt, getMonitor, setMonitor, getDisplayMode };
  root.Settings = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
