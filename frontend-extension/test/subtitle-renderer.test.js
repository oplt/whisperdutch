const test = require("node:test");
const assert = require("node:assert/strict");
const renderer = require("../subtitle-renderer.js");

test("stabilizePartial keeps longer prior text on short regression", () => {
  assert.equal(renderer.stabilizePartial("dit is een langere zin", "dit is"), "dit is een langere zin");
});

test("mergeByWordOverlap merges repeated boundary words", () => {
  assert.equal(renderer.mergeByWordOverlap("dit is een", "een test"), "dit is een test");
});

test("exports VTT with timestamps", () => {
  const rows = [{ startMs: 0, endMs: 1200, dutch: "Hallo.", translation: "Hello." }];
  assert.match(renderer.toVtt(rows), /00:00:00\.000 --> 00:00:01\.200/);
});
