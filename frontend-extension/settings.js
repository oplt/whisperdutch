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
    return ["current", "two-line", "history", "compact"].includes(saved) ? saved : "history";
  }

  function getDevice(storage) {
    const saved = storage.getItem("subtitleAsrDevice");
    return ["cpu", "cuda"].includes(saved) ? saved : "cpu";
  }

  function setDevice(storage, device) {
    const normalized = ["cpu", "cuda"].includes(device) ? device : "cpu";
    storage.setItem("subtitleAsrDevice", normalized);
    return normalized;
  }

  function setMonitor(storage, volume, muted) {
    storage.setItem("subtitleMonitorVolume", String(volume));
    storage.setItem("subtitleMonitorMuted", muted ? "1" : "0");
  }

  const api = { getMode, getContextPrompt, getMonitor, setMonitor, getDisplayMode, getDevice, setDevice };
  root.Settings = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
