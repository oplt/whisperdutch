(function (root) {
  const WINDOW_WIDTH = 920;
  const WINDOW_HEIGHT = 680;

  function buildSubtitleUrl(getUrl, tabId, autoStart = true) {
    const url = new URL(getUrl("subtitle.html"));
    url.searchParams.set("tabId", String(tabId));
    url.searchParams.set("autostart", autoStart ? "1" : "0");
    return url.toString();
  }

  function supportsAutomaticCapture(chromeApi) {
    return typeof chromeApi?.tabCapture?.getMediaStreamId === "function";
  }

  function findSubtitleTab(windows, subtitlePageUrl) {
    for (const browserWindow of windows || []) {
      const tab = (browserWindow.tabs || []).find(candidate => candidate.url?.startsWith(subtitlePageUrl));
      if (tab) return { browserWindow, tab };
    }
    return null;
  }

  async function openSubtitleWindow(sourceTab, chromeApi = root.chrome) {
    if (!Number.isInteger(sourceTab?.id)) return null;

    const subtitlePageUrl = chromeApi.runtime.getURL("subtitle.html");
    const targetUrl = buildSubtitleUrl(
      chromeApi.runtime.getURL,
      sourceTab.id,
      supportsAutomaticCapture(chromeApi)
    );
    const windows = await chromeApi.windows.getAll({ populate: true });
    const existing = findSubtitleTab(windows, subtitlePageUrl);

    if (existing) {
      await chromeApi.tabs.update(existing.tab.id, { url: targetUrl, active: true });
      await chromeApi.windows.update(existing.browserWindow.id, { focused: true });
      return existing.browserWindow;
    }

    return chromeApi.windows.create({
      url: targetUrl,
      type: "popup",
      width: WINDOW_WIDTH,
      height: WINDOW_HEIGHT,
      focused: true
    });
  }

  if (root.chrome?.action?.onClicked) {
    root.chrome.action.onClicked.addListener(tab => {
      openSubtitleWindow(tab).catch(error => {
        console.error("[DutchSubtitles] Could not open subtitle window", error);
      });
    });
  }

  const api = { buildSubtitleUrl, findSubtitleTab, openSubtitleWindow, supportsAutomaticCapture };
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : self);
