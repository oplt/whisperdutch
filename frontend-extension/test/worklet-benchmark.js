const { performance } = require("node:perf_hooks");

const { StreamingPCM16Resampler } = require("../audio/worklet.js");

const SOURCE_RATES = [44100, 48000, 96000];
const BATCH_DURATIONS_MS = [10, 20, 40, 80];
const BENCHMARK_SECONDS = 120;

function benchmark(sourceRate, batchDurationMs) {
  const batchSize = Math.max(128, Math.round(sourceRate * batchDurationMs / 1000));
  const input = new Float32Array(batchSize);
  const iterations = Math.ceil(sourceRate * BENCHMARK_SECONDS / batchSize);
  const resampler = new StreamingPCM16Resampler(sourceRate, 16000);
  let outputSamples = 0;
  const started = performance.now();

  for (let index = 0; index < iterations; index += 1) {
    outputSamples += resampler.process(input).byteLength / 2;
  }

  const elapsedMs = performance.now() - started;
  const representedSeconds = iterations * batchSize / sourceRate;
  const expectedSamples = Math.round(representedSeconds * 16000);
  return {
    sourceRate,
    batchDurationMs,
    messagesPerSecond: Number((1000 / batchDurationMs).toFixed(2)),
    processingMs: Number(elapsedMs.toFixed(2)),
    realtimeFactor: Number((elapsedMs / 1000 / representedSeconds).toFixed(6)),
    driftSamples: outputSamples - expectedSamples
  };
}

const results = BATCH_DURATIONS_MS.flatMap(duration => SOURCE_RATES.map(rate => benchmark(rate, duration)));
console.log(JSON.stringify({ selectedBatchDurationMs: 20, benchmarkSeconds: BENCHMARK_SECONDS, results }, null, 2));
