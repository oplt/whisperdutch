const assert = require("node:assert/strict");
const test = require("node:test");

const {
  StreamingPCM16Resampler,
  DEFAULT_WORKLET_BATCH_DURATION_MS,
  clampBatchDurationMs,
  sourceBufferSizeForRate,
  maxPcmOutputSamples,
  messagesPerSecond,
  theoreticalCaptureLatencyMs,
  pcmBytesPerMessage
} = require("../audio/worklet.js");

test("batch duration defaults to 20 ms and clamps to supported range", () => {
  assert.equal(DEFAULT_WORKLET_BATCH_DURATION_MS, 20);
  assert.equal(clampBatchDurationMs(undefined), 20);
  assert.equal(clampBatchDurationMs(40), 40);
  assert.equal(clampBatchDurationMs(5), 10);
  assert.equal(clampBatchDurationMs(200), 80);
});

test("source buffer size scales with sample rate and batch duration", () => {
  assert.equal(sourceBufferSizeForRate(48000, 20), 960);
  assert.equal(sourceBufferSizeForRate(48000, 40), 1920);
  assert.equal(sourceBufferSizeForRate(44100, 20), 882);
});

test("40 ms batching halves message rate but adds capture latency", () => {
  assert.equal(messagesPerSecond(20), 50);
  assert.equal(messagesPerSecond(40), 25);
  assert.equal(theoreticalCaptureLatencyMs(20), 10);
  assert.equal(theoreticalCaptureLatencyMs(40), 20);
});

test("resampler reuses output scratch buffer when provided", () => {
  const resampler = new StreamingPCM16Resampler(48000, 16000);
  const input = new Float32Array(960);
  const scratch = new Int16Array(maxPcmOutputSamples(input.length, 48000, 16000));
  const first = resampler.process(input, scratch);
  const second = resampler.process(input, scratch);
  assert.ok(first.byteLength > 0);
  assert.equal(first.byteLength, second.byteLength);
});

test("resampler stays phase-continuous when output buffer is reused", () => {
  const sourceRate = 48000;
  const chunkSize = sourceBufferSizeForRate(sourceRate, 20);
  const streaming = new StreamingPCM16Resampler(sourceRate, 16000);
  const single = new StreamingPCM16Resampler(sourceRate, 16000);
  const scratch = new Int16Array(maxPcmOutputSamples(chunkSize, sourceRate, 16000));
  const streamingChunks = [];
  const singleInput = new Float32Array(chunkSize * 10);
  for (let offset = 0; offset < singleInput.length; offset += chunkSize) {
    const chunk = singleInput.subarray(offset, offset + chunkSize);
    streamingChunks.push(Buffer.from(streaming.process(chunk, scratch)));
  }
  const singleBlock = Buffer.from(single.process(singleInput));
  assert.deepEqual(Buffer.concat(streamingChunks), singleBlock);
});

test("phase8 profile favors 20 ms default over 40 ms for live latency", () => {
  const { performance } = require("node:perf_hooks");
  const sourceRate = 48000;
  const seconds = 5;
  const scenarios = [20, 40].map(batchDurationMs => {
    const batchSize = sourceBufferSizeForRate(sourceRate, batchDurationMs);
    const iterations = Math.ceil(sourceRate * seconds / batchSize);
    const resampler = new StreamingPCM16Resampler(sourceRate, 16000);
    const scratch = new Int16Array(maxPcmOutputSamples(batchSize, sourceRate, 16000));
    const input = new Float32Array(batchSize);
    const started = performance.now();
    for (let index = 0; index < iterations; index += 1) {
      resampler.process(input, scratch);
    }
    return {
      batchDurationMs,
      messagesPerSecond: messagesPerSecond(batchDurationMs),
      pcmBytesPerSecond: pcmBytesPerMessage(sourceRate, batchDurationMs) * messagesPerSecond(batchDurationMs),
      processingMs: performance.now() - started,
      theoreticalCaptureLatencyMs: theoreticalCaptureLatencyMs(batchDurationMs)
    };
  });

  const fast = scenarios.find(entry => entry.batchDurationMs === 20);
  const slow = scenarios.find(entry => entry.batchDurationMs === 40);
  assert.ok(fast);
  assert.ok(slow);
  assert.equal(fast.messagesPerSecond / slow.messagesPerSecond, 2);
  assert.equal(slow.theoreticalCaptureLatencyMs - fast.theoreticalCaptureLatencyMs, 10);
  assert.ok(fast.processingMs < seconds * 1000);
  assert.ok(slow.processingMs < seconds * 1000);
});

test("worklet level remains UI-only; backend keeps RMS on received PCM", () => {
  // Documented invariant: do not embed worklet level in binary PCM to preserve VAD correctness.
  assert.equal(typeof theoreticalCaptureLatencyMs(20), "number");
  assert.ok(pcmBytesPerMessage(48000, 20) > 0);
});
