const assert = require("node:assert/strict");
const test = require("node:test");

const {
  StreamingPCM16Resampler,
  mixInputFrame,
  designLowpassTaps
} = require("../audio/worklet.js");

function tone(sourceRate, frequencyHz, seconds, amplitude = 0.5) {
  const samples = Math.round(sourceRate * seconds);
  const out = new Float32Array(samples);
  for (let i = 0; i < samples; i += 1) {
    out[i] = Math.sin((2 * Math.PI * frequencyHz * i) / sourceRate) * amplitude;
  }
  return out;
}

function rms(int16) {
  if (!int16.length) return 0;
  let sum = 0;
  for (let i = 0; i < int16.length; i += 1) sum += (int16[i] / 32768) ** 2;
  return Math.sqrt(sum / int16.length);
}

function resampleTone(frequencyHz, { antialias }) {
  const sourceRate = 48000;
  const resampler = new StreamingPCM16Resampler(sourceRate, 16000, { antialias });
  const pcm = new Int16Array(resampler.process(tone(sourceRate, frequencyHz, 0.25)));
  return rms(pcm);
}

test("anti-alias lowpass attenuates a 12 kHz tone relative to 1 kHz after 48→16 kHz", () => {
  const low = resampleTone(1000, { antialias: true });
  const high = resampleTone(12000, { antialias: true });
  const legacyLow = resampleTone(1000, { antialias: false });
  const legacyHigh = resampleTone(12000, { antialias: false });

  // Without anti-alias, 12 kHz aliases near 4 kHz and keeps comparable energy.
  assert.ok(legacyHigh / legacyLow > 0.7, `legacy ratio=${legacyHigh / legacyLow}`);
  // With FIR lowpass, stopband energy drops sharply.
  assert.ok(high / low < 0.25, `filtered ratio=${high / low} low=${low} high=${high}`);
  assert.ok(low > 0.05);
});

test("mixInputFrame averages available channels including right-only speech", () => {
  const left = Float32Array.from([0, 0, 0, 0]);
  const right = Float32Array.from([0.5, -0.5, 0.25, -0.25]);
  assert.equal(mixInputFrame([left, right], 0), 0.25);
  assert.equal(mixInputFrame([left, right], 1), -0.25);
  assert.equal(mixInputFrame([null, right], 2), 0.25);
  assert.equal(mixInputFrame([right], 3), -0.25);
});

test("designLowpassTaps yields unity DC gain", () => {
  const taps = designLowpassTaps(31, 0.15);
  const sum = taps.reduce((acc, value) => acc + value, 0);
  assert.ok(Math.abs(sum - 1) < 1e-6);
});

test("resampler process returns a detached transferable buffer copy", () => {
  const scratch = new Int16Array(64);
  const resampler = new StreamingPCM16Resampler(48000, 16000);
  const buffer = resampler.process(tone(48000, 440, 0.02), scratch);
  assert.ok(buffer instanceof ArrayBuffer);
  assert.notEqual(buffer, scratch.buffer);
  // Scratch remains writable after "transfer" simulation.
  scratch[0] = 123;
  assert.equal(scratch[0], 123);
});
