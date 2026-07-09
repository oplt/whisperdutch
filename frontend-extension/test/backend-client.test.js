const test = require("node:test");
const assert = require("node:assert/strict");
const backend = require("../backend-client.js");

test("backend endpoints use localhost API", () => {
  assert.equal(backend.ENDPOINTS.glossary, "http://127.0.0.1:8000/api/glossary");
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
