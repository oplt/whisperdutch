const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {
  buildSubtitleUrl,
  findSubtitleTab,
  openSubtitleWindow,
  supportsAutomaticCapture
} = require("../background.js");

const manifest = JSON.parse(fs.readFileSync(
  path.join(__dirname, "..", "manifest.json"),
  "utf8"
));
const firefoxManifest = JSON.parse(fs.readFileSync(
  path.join(__dirname, "..", "manifest.firefox.json"),
  "utf8"
));

test("extension icon uses the background launcher instead of a popup", () => {
  assert.equal(manifest.action.default_popup, undefined);
  assert.equal(manifest.background.service_worker, "background.js");
});

test("subtitle window URL carries the selected tab and autostart request", () => {
  const url = buildSubtitleUrl(page => `chrome-extension://test-id/${page}`, 42);
  assert.equal(url, "chrome-extension://test-id/subtitle.html?tabId=42&autostart=1");
});

test("Firefox manifest uses a background script and stable native-messaging ID", () => {
  assert.deepEqual(firefoxManifest.background.scripts, ["background.js"]);
  assert.equal(firefoxManifest.background.service_worker, undefined);
  assert.equal(firefoxManifest.permissions.includes("tabCapture"), false);
  assert.equal(
    firefoxManifest.browser_specific_settings.gecko.id,
    "dutch-subtitle-translator@polatozgur111.local"
  );
});

test("Firefox waits for a direct Start click before requesting audio", () => {
  const firefoxApi = {};
  assert.equal(supportsAutomaticCapture(firefoxApi), false);
  assert.equal(
    buildSubtitleUrl(page => `moz-extension://test-id/${page}`, 42, false),
    "moz-extension://test-id/subtitle.html?tabId=42&autostart=0"
  );
});

test("an existing subtitle window is reused and focused", async () => {
  const calls = [];
  const windows = [{
    id: 7,
    tabs: [{ id: 8, url: "chrome-extension://test-id/subtitle.html?tabId=1" }]
  }];
  const chromeApi = {
    tabCapture: { getMediaStreamId() {} },
    runtime: { getURL: page => `chrome-extension://test-id/${page}` },
    windows: {
      getAll: async () => windows,
      update: async (id, options) => calls.push(["window", id, options]),
      create: async options => calls.push(["create", options])
    },
    tabs: {
      update: async (id, options) => calls.push(["tab", id, options])
    }
  };

  assert.equal(findSubtitleTab(windows, chromeApi.runtime.getURL("subtitle.html")).tab.id, 8);
  await openSubtitleWindow({ id: 42 }, chromeApi);

  assert.deepEqual(calls, [
    ["tab", 8, { url: "chrome-extension://test-id/subtitle.html?tabId=42&autostart=1", active: true }],
    ["window", 7, { focused: true }]
  ]);
});
