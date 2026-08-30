const test = require("node:test");
const assert = require("node:assert/strict");

const { LANGUAGE_OPTIONS, languageName, normalizeLanguage } = require("../app/languages.js");

test("language catalog exposes multilingual source and target choices", () => {
  assert.equal(LANGUAGE_OPTIONS.length, 20);
  assert.equal(languageName("de"), "German");
  assert.equal(normalizeLanguage(" FR ", "nl"), "fr");
  assert.equal(normalizeLanguage("xx", "nl"), "nl");
});
