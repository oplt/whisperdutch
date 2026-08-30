const assert = require("node:assert/strict");
const test = require("node:test");

const { StreamingPCM16Resampler, DEFAULT_WORKLET_BATCH_DURATION_MS } = require("../audio/worklet.js");

function resampleInChunks(sourceRate, durationSeconds, chunkSize) {
  const resampler = new StreamingPCM16Resampler(sourceRate, 16000);
  const totalSamples = sourceRate * durationSeconds;
  const chunks = [];
  let outputSamples = 0;

  for (let offset = 0; offset < totalSamples; offset += chunkSize) {
    const size = Math.min(chunkSize, totalSamples - offset);
    const input = new Float32Array(size);
    for (let index = 0; index < size; index += 1) {
      input[index] = Math.sin((offset + index) * 2 * Math.PI * 440 / sourceRate) * 0.5;
    }
    const output = resampler.process(input);
    chunks.push(Buffer.from(output));
    outputSamples += output.byteLength / 2;
  }

  return { bytes: Buffer.concat(chunks), outputSamples };
}

for (const sourceRate of [44100, 48000, 96000]) {
  test(`${sourceRate} Hz to 16 kHz is phase-continuous`, () => {
    const streaming = resampleInChunks(sourceRate, 2, 128);
    const singleBlock = resampleInChunks(sourceRate, 2, sourceRate * 2);

    assert.equal(streaming.outputSamples, 32000);
    assert.deepEqual(streaming.bytes, singleBlock.bytes);
  });

  test(`${sourceRate} Hz long-duration output has no timing drift`, () => {
    const durationSeconds = 60;
    const result = resampleInChunks(sourceRate, durationSeconds, Math.round(sourceRate * DEFAULT_WORKLET_BATCH_DURATION_MS / 1000));
    assert.equal(result.outputSamples, durationSeconds * 16000);
  });
}

test("PCM output is clamped to the signed 16-bit range", () => {
  const resampler = new StreamingPCM16Resampler(48000, 16000);
  const output = new Int16Array(resampler.process(Float32Array.from([-2, -2, -2, 0, 0, 0, 2, 2, 2, 2])));

  assert.equal(Math.min(...output), -32768);
  assert.equal(Math.max(...output), 32767);
  assert.ok([...output].every(sample => sample >= -32768 && sample <= 32767));
});
