const test = require("node:test");
const assert = require("node:assert/strict");
const { VocabularyStore } = require("../app/vocabulary.js");

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

test("VocabularyStore saves word with sentence context", () => {
  const store = new VocabularyStore(memoryStorage());
  store.add({
    id: "v1",
    word: "fiets",
    meaning: "bicycle",
    dutchSentence: "Ik rij op mijn fiets.",
    englishSentence: "I ride my bicycle.",
    addedAt: "2026-08-29T12:00:00.000Z"
  });
  assert.equal(store.readAll().length, 1);
  assert.equal(store.readAll()[0].word, "fiets");
  assert.equal(store.readAll()[0].dutchSentence, "Ik rij op mijn fiets.");
});

test("VocabularyStore updates duplicate word in same sentence", () => {
  const store = new VocabularyStore(memoryStorage());
  const first = store.add({
    id: "v1",
    word: "Fiets",
    meaning: "My bike is red.",
    dutchSentence: "Mijn fiets is rood.",
    englishSentence: "My bike is red.",
    addedAt: "2026-08-29T12:00:00.000Z"
  });
  const second = store.add({
    id: "v2",
    word: "fiets",
    meaning: "My bicycle is red.",
    dutchSentence: "Mijn fiets is rood.",
    englishSentence: "My bicycle is red.",
    addedAt: "2026-08-29T12:05:00.000Z"
  });
  assert.equal(first.created, true);
  assert.equal(second.created, false);
  assert.equal(store.readAll().length, 1);
  assert.equal(store.readAll()[0].meaning, "My bicycle is red.");
});
