const test = require("node:test");
const assert = require("node:assert/strict");
const audio = require("../audio-client.js");

test("float32ToPCM16LE clamps samples", () => {
  const view = new DataView(audio.float32ToPCM16LE(new Float32Array([-2, 0, 2])));
  assert.equal(view.getInt16(0, true), -32768);
  assert.equal(view.getInt16(2, true), 0);
  assert.equal(view.getInt16(4, true), 32767);
});

test("resampleLinear downsamples by ratio", () => {
  const out = audio.resampleLinear(new Float32Array([0, 1, 2, 3]), 4, 2);
  assert.deepEqual(Array.from(out), [0, 2]);
});
