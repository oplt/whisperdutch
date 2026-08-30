const test = require("node:test");
const assert = require("node:assert/strict");
const {
  AudioBackpressureController,
  PCM16_MONO_16K_BYTES_PER_SECOND,
  bufferedAudioMs,
  BACKPRESSURE_STATES
} = require("../app/audio-backpressure.js");

function bytesForMs(ms) {
  return Math.round((ms / 1000) * PCM16_MONO_16K_BYTES_PER_SECOND);
}

test("bufferedAudioMs converts websocket backlog to audio time", () => {
  assert.equal(bufferedAudioMs(bytesForMs(600)), 600);
  assert.equal(bufferedAudioMs(bytesForMs(1200)), 1200);
});

test("backpressure uses hysteresis across normal congested dropping recovery", () => {
  let now = 1_000;
  const controller = new AudioBackpressureController({ now: () => now });

  let evaluation = controller.evaluate(bytesForMs(500));
  assert.equal(evaluation.state, BACKPRESSURE_STATES.NORMAL);
  assert.equal(evaluation.action, "sent");

  evaluation = controller.evaluate(bytesForMs(700));
  assert.equal(evaluation.state, BACKPRESSURE_STATES.CONGESTED);
  assert.equal(evaluation.action, "warn");

  evaluation = controller.evaluate(bytesForMs(500));
  assert.equal(evaluation.state, BACKPRESSURE_STATES.CONGESTED);

  evaluation = controller.evaluate(bytesForMs(300));
  assert.equal(evaluation.state, BACKPRESSURE_STATES.NORMAL);

  evaluation = controller.evaluate(bytesForMs(1300));
  assert.equal(evaluation.state, BACKPRESSURE_STATES.DROPPING);
  assert.equal(evaluation.action, "drop");

  controller.recordDrop();
  evaluation = controller.evaluate(bytesForMs(300));
  assert.equal(evaluation.state, BACKPRESSURE_STATES.RECOVERY);
  assert.equal(evaluation.needsGapReset, true);

  evaluation = controller.evaluate(bytesForMs(300));
  assert.equal(evaluation.needsGapReset, true);
  controller.noteGapSent();
  assert.equal(controller.snapshot().gapEvents, 1);
  assert.equal(controller.snapshot().state, BACKPRESSURE_STATES.NORMAL);
});

test("backpressure tracks congestion duration and drops", () => {
  let now = 0;
  const controller = new AudioBackpressureController({ now: () => now });

  controller.evaluate(bytesForMs(800));
  now += 2_500;
  controller.evaluate(bytesForMs(100));
  controller.noteGapSent();

  const stats = controller.snapshot();
  assert.equal(stats.congestionEvents, 1);
  assert.ok(stats.longestCongestionMs >= 2_500);
  assert.equal(stats.droppedChunks, 0);
  controller.recordDrop();
  assert.equal(controller.snapshot().droppedChunks, 1);
});
