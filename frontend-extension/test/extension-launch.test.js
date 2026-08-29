const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { buildSubtitleUrl, findSubtitleTab, openSubtitleWindow } = require("../background.js");

const manifest = JSON.parse(fs.readFileSync(
  path.join(__dirname, "..", "manifest.json"),
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

test("an existing subtitle window is reused and focused", async () => {
  const calls = [];
  const windows = [{
    id: 7,
    tabs: [{ id: 8, url: "chrome-extension://test-id/subtitle.html?tabId=1" }]
  }];
  const chromeApi = {
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
