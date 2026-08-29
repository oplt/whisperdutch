const test = require("node:test");
const assert = require("node:assert/strict");
const backend = require("../backend-client.js");

test("backend paths use the active localhost API", () => {
  assert.equal(backend.url("/api/glossary"), "http://127.0.0.1:8000/api/glossary");
});

test("history session URL encodes client id", async () => {
  const calls = [];
  global.fetch = async (url) => {
    calls.push(url);
    return { ok: true, json: async () => ({ ok: true }) };
  };
  await backend.fetchHistorySession("ws/a b");
  assert.equal(calls[0], "http://127.0.0.1:8000/api/history/ws%2Fa%20b");
  delete global.fetch;
});

test("invalid cached backend URL falls back and clears stale connection metadata", () => {
  const values = {
    backendBaseUrl: "not-a-url",
    backendWsUrl: "ws://127.0.0.1:8000/ws/subtitles",
    backendSource: "native",
    backendPort: "8000"
  };
  global.localStorage = {
    getItem: (key) => values[key] || null,
    setItem: (key, value) => { values[key] = String(value); },
    removeItem: (key) => { delete values[key]; }
  };

  assert.equal(backend.getBaseUrl(), backend.DEFAULT_BASE_URL);
  assert.equal(values.backendBaseUrl, undefined);
  assert.equal(values.backendWsUrl, undefined);
  assert.equal(values.backendSource, undefined);
  assert.equal(backend.getConnectionMetadata().recoveryCount, 0);
  delete global.localStorage;
});

test("connection setters normalize URLs and record native-host metadata", () => {
  const values = {};
  global.localStorage = {
    getItem: (key) => values[key] || null,
    setItem: (key, value) => { values[key] = String(value); },
    removeItem: (key) => { delete values[key]; }
  };

  const connection = backend.setConnectionUrls("http://127.0.0.1:8123/", null, "native");

  assert.deepEqual(connection, {
    baseUrl: "http://127.0.0.1:8123",
    wsUrl: "ws://127.0.0.1:8123/ws/subtitles",
    source: "native"
  });
  assert.deepEqual(backend.getConnectionMetadata(), { updatedAt: values.backendUrlUpdatedAt, port: "8123", source: "native", recoveryCount: 0 });
  delete global.localStorage;
});

test("healthy backend reconciliation refreshes stale port and bypasses HTTP cache", async () => {
  const values = { backendBaseUrl: "http://127.0.0.1:8001", backendWsUrl: "ws://127.0.0.1:8001/ws/subtitles" };
  global.localStorage = {
    getItem: (key) => values[key] || null,
    setItem: (key, value) => { values[key] = String(value); },
    removeItem: (key) => { delete values[key]; }
  };
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => ({ ok: true, live: true }) };
  };

  const connection = await backend.findHealthyConnection();

  assert.equal(connection.baseUrl, "http://127.0.0.1:8001");
  assert.equal(connection.wsUrl, "ws://127.0.0.1:8001/ws/subtitles");
  assert.equal(calls[0].options.cache, "no-store");
  delete global.fetch;
  delete global.localStorage;
});

test("stale backend recovery increments the local recovery metric", async () => {
  const values = { backendBaseUrl: "http://127.0.0.1:8001", backendWsUrl: "ws://127.0.0.1:8001/ws/subtitles" };
  global.localStorage = {
    getItem: (key) => values[key] || null,
    setItem: (key, value) => { values[key] = String(value); },
    removeItem: (key) => { delete values[key]; }
  };
  global.fetch = async (url) => ({
    ok: url === "http://127.0.0.1:8000/health/live",
    json: async () => ({ ok: true, live: true })
  });

  const connection = await backend.findHealthyConnection();

  assert.equal(connection.recovered, true);
  assert.equal(connection.recoveryCount, 1);
  assert.equal(backend.getConnectionMetadata().recoveryCount, 1);
  delete global.fetch;
  delete global.localStorage;
});
