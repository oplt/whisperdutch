const test = require("node:test");
const assert = require("node:assert/strict");

const { TranscriptStore } = require("../app/transcript-store.js");

function memoryStorage() {
  const data = new Map();
  return {
    getItem(key) {
      return data.has(key) ? data.get(key) : null;
    },
    setItem(key, value) {
      data.set(key, String(value));
    }
  };
}

test("closePrevious does not overwrite a finalized cue end time", () => {
  const store = new TranscriptStore(memoryStorage());
  store.finalize(
    {
      id: "cue-1",
      source_text: "Eerste zin.",
      translation: "First sentence.",
      start: 0,
      end: 2
    },
    0
  );
  assert.equal(store.items[0].endMs, 2000);
  assert.equal(store.items[0].pending, false);

  store.addPending({ id: "cue-2", source_text: "Tweede." }, 8000);
  assert.equal(store.items[0].endMs, 2000);
  assert.equal(store.items[1].startMs, 8000);
  assert.equal(store.items[1].pending, true);
});

test("closePrevious still trims a pending previous cue for display", () => {
  const store = new TranscriptStore(memoryStorage());
  store.addPending({ id: "pending-1", source_text: "Bezig." }, 1000);
  store.addPending({ id: "pending-2", source_text: "Volgende." }, 5000);
  assert.equal(store.items[0].endMs, 4880);
  assert.equal(store.items[0].pending, true);
});

test("async save does not throw on quota failures", async () => {
  const store = new TranscriptStore({
    getItem() { return "{}"; },
    setItem() {
      const error = new Error("quota");
      error.name = "QuotaExceededError";
      throw error;
    }
  });
  store.addPending({ id: "a", source_text: "Hallo" }, 0);
  const snapshot = store.save("demo");
  assert.equal(snapshot.name, "demo");
  await new Promise(resolve => queueMicrotask(resolve));
  assert.equal(store.lastPersistError?.name, "QuotaExceededError");
});
