const test = require("node:test");
const assert = require("node:assert/strict");
const settings = require("../settings.js");

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

test("getMode falls back to balanced", () => {
  assert.equal(settings.getMode(memoryStorage({ subtitleQualityMode: "turbo" })), "balanced");
});

test("monitor settings persist", () => {
  const storage = memoryStorage();
  settings.setMonitor(storage, 0.5, true);
  assert.deepEqual(settings.getMonitor(storage), { volume: "0.5", muted: true });
});

test("display mode falls back to history", () => {
  assert.equal(settings.getDisplayMode(memoryStorage({ subtitleDisplayMode: "floating" })), "history");
  assert.equal(settings.getDisplayMode(memoryStorage({ subtitleDisplayMode: "history" })), "history");
});

test("ASR device falls back to CPU and persists valid choices", () => {
  const storage = memoryStorage({ subtitleAsrDevice: "unknown" });
  assert.equal(settings.getDevice(storage), "cpu");
  assert.equal(settings.setDevice(storage, "cuda"), "cuda");
  assert.equal(settings.getDevice(storage), "cuda");
  assert.equal(settings.setDevice(storage, "invalid"), "cpu");
});
