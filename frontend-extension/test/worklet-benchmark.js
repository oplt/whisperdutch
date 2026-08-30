const { performance } = require("node:perf_hooks");
const fs = require("node:fs");
const path = require("node:path");

const {
  StreamingPCM16Resampler,
  DEFAULT_WORKLET_BATCH_DURATION_MS,
  PCM16_MONO_16K_BYTES_PER_SECOND,
  sourceBufferSizeForRate,
  maxPcmOutputSamples,
  messagesPerSecond,
  theoreticalCaptureLatencyMs,
  pcmBytesPerMessage
} = require("../audio/worklet.js");

const SOURCE_RATES = [44100, 48000, 96000];
const BATCH_DURATIONS_MS = [20, 40];
const BENCHMARK_SECONDS = 120;

function benchmark(sourceRate, batchDurationMs, { reuseOutput = true } = {}) {
  const batchSize = sourceBufferSizeForRate(sourceRate, batchDurationMs);
  const input = new Float32Array(batchSize);
  const iterations = Math.ceil(sourceRate * BENCHMARK_SECONDS / batchSize);
  const resampler = new StreamingPCM16Resampler(sourceRate, 16000);
  const maxOutput = maxPcmOutputSamples(batchSize, sourceRate, 16000);
  const scratch = reuseOutput ? new Int16Array(maxOutput) : null;
  let outputSamples = 0;
  let allocations = 0;
  const started = performance.now();

  for (let index = 0; index < iterations; index += 1) {
    const before = reuseOutput ? 0 : 1;
    const pcm = resampler.process(input, scratch);
    if (!reuseOutput || pcm.byteLength === 0) allocations += before || 1;
    else if (!scratch) allocations += 1;
    outputSamples += pcm.byteLength / 2;
  }

  const elapsedMs = performance.now() - started;
  const representedSeconds = iterations * batchSize / sourceRate;
  const expectedSamples = Math.round(representedSeconds * 16000);
  const pcmBytesPerSecond = pcmBytesPerMessage(sourceRate, batchDurationMs) * messagesPerSecond(batchDurationMs);

  return {
    sourceRate,
    batchDurationMs,
    reuseOutput,
    messagesPerSecond: Number(messagesPerSecond(batchDurationMs).toFixed(2)),
    websocketMessagesPer120s: Math.round(messagesPerSecond(batchDurationMs) * BENCHMARK_SECONDS),
    pcmBytesPerSecond,
    theoreticalCaptureLatencyMs: theoreticalCaptureLatencyMs(batchDurationMs),
    processingMs: Number(elapsedMs.toFixed(2)),
    realtimeFactor: Number((elapsedMs / 1000 / representedSeconds).toFixed(6)),
    driftSamples: outputSamples - expectedSamples,
    estimatedAllocationsPer120s: reuseOutput ? Math.max(1, Math.ceil(maxOutput / batchSize)) : Math.round(messagesPerSecond(batchDurationMs) * BENCHMARK_SECONDS)
  };
}

const results = BATCH_DURATIONS_MS.flatMap(duration =>
  SOURCE_RATES.flatMap(rate => [
    benchmark(rate, duration, { reuseOutput: true }),
    benchmark(rate, duration, { reuseOutput: false })
  ])
);

const selected = results.find(entry => entry.sourceRate === 48000 && entry.batchDurationMs === 20 && entry.reuseOutput)
  || results[0];
const alternate = results.find(entry => entry.sourceRate === 48000 && entry.batchDurationMs === 40 && entry.reuseOutput)
  || results[1];

const report = {
  phase: "phase8-worklet",
  generatedAt: new Date().toISOString(),
  selectedBatchDurationMs: DEFAULT_WORKLET_BATCH_DURATION_MS,
  benchmarkSeconds: BENCHMARK_SECONDS,
  pcmBytesPerSecondAt16k: PCM16_MONO_16K_BYTES_PER_SECOND,
  recommendation: {
    defaultBatchDurationMs: DEFAULT_WORKLET_BATCH_DURATION_MS,
    reason: "20 ms keeps average capture latency near 10 ms while 40 ms would add ~10 ms extra subtitle delay for half the message rate.",
    rmsReuse: "Worklet level drives the UI meter only; backend VAD keeps RMS on decoded PCM to avoid binary protocol changes and stay aligned with segmented audio."
  },
  comparison48000Hz: {
    batch20ms: selected,
    batch40ms: alternate,
    messageRateReductionRatio: Number((selected.messagesPerSecond / alternate.messagesPerSecond).toFixed(2)),
    addedCaptureLatencyMs: alternate.theoreticalCaptureLatencyMs - selected.theoreticalCaptureLatencyMs
  },
  results
};

const outputPath = path.join(__dirname, "../../docs/benchmark-artifacts/phase8-worklet-latest.json");
fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, `${JSON.stringify(report, null, 2)}\n`);
console.log(JSON.stringify(report, null, 2));
