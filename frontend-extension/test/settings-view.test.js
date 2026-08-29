const test = require("node:test");
const assert = require("node:assert/strict");

globalThis.BackendClient = {
  getWsUrl: () => "ws://127.0.0.1:8000/ws/subtitles",
  setWsUrl: () => ({ wsUrl: "ws://127.0.0.1:8000/ws/subtitles" })
};

const { SettingsView } = require("../app/settings-view.js");

function memoryStorage(values = {}) {
  return {
    getItem(key) {
      return Object.prototype.hasOwnProperty.call(values, key) ? values[key] : null;
    },
    setItem(key, value) {
      values[key] = value;
    }
  };
}

function mockElement() {
  return {
    value: "",
    checked: false,
    addEventListener() {}
  };
}

function mockDocument() {
  const elements = Object.create(null);
  return {
    getElementById(id) {
      return elements[id] || (elements[id] = mockElement());
    },
    querySelectorAll() {
      return [];
    }
  };
}

test("SettingsView load falls back to balanced mode and CPU device", () => {
  const storage = memoryStorage({ subtitleQualityMode: "turbo", subtitleAsrDevice: "unknown" });
  const view = new SettingsView(mockDocument(), storage);
  view.load();
  assert.equal(view.quality.value, "balanced");
  assert.equal(view.device, "cpu");
});

test("SettingsView persists monitor and font settings", () => {
  const storage = memoryStorage();
  const view = new SettingsView(mockDocument(), storage);
  view.volume.value = "0.5";
  view.muted.checked = true;
  view.dutchFont.value = "52";
  view.translationFont.value = "40";
  view.persistFonts({ dutch: 52, translation: 40 });
  assert.equal(storage.getItem("subtitleMonitorVolume"), null);
  view.storage.setItem("subtitleMonitorVolume", view.volume.value);
  view.storage.setItem("subtitleMonitorMuted", view.muted.checked ? "1" : "0");
  assert.equal(storage.getItem("subtitleMonitorVolume"), "0.5");
  assert.equal(storage.getItem("subtitleMonitorMuted"), "1");
  assert.equal(storage.getItem("dutchSubtitleFontSize"), "52");
  assert.equal(storage.getItem("translationSubtitleFontSize"), "40");
});

test("SettingsView values expose trimmed context prompt", () => {
  const storage = memoryStorage();
  const view = new SettingsView(mockDocument(), storage);
  view.quality.value = "fast";
  view.context.value = "  Ajax match  ";
  view.volume.value = "1";
  assert.deepEqual(view.values(), {
    mode: "fast",
    contextPrompt: "Ajax match",
    volume: 1,
    muted: false,
    dutchFont: 0,
    translationFont: 0,
    device: "cpu"
  });
});
