const test = require("node:test");
const assert = require("node:assert/strict");

const { CaptureController } = require("../app/capture-controller.js");

function createHarness(options = {}) {
  const connections = [];
  const mediaRequests = [];
  const streamOptions = [];
  const track = { stopCalled: false, stop() { this.stopCalled = true; } };
  const stream = {
    getTracks: () => [track],
    getAudioTracks: () => options.noAudioTracks ? [] : [track]
  };
  const destination = { name: "destination" };
  const source = {
    name: "source",
    connect(target) { connections.push([this.name, target.name]); },
    disconnect() {}
  };
  const gains = [];
  let silenceCallback = null;
  const clearedTimers = [];

  class FakeAudioContext {
    constructor() {
      this.state = options.initialContextState || "running";
      this.destination = destination;
      this.audioWorklet = { addModule: async () => {} };
    }

    createMediaStreamSource(receivedStream) {
      assert.equal(receivedStream, stream);
      return source;
    }

    createGain() {
      const node = {
        name: gains.length === 0 ? "monitor" : "processor-sink",
        gain: { value: 1 },
        connect(target) { connections.push([this.name, target.name]); },
        disconnect() {}
      };
      gains.push(node);
      return node;
    }

    async resume() {
      this.state = options.resumedContextState || "running";
    }

    async close() {
      this.state = "closed";
    }
  }

  class FakeAudioWorkletNode {
    constructor() {
      this.name = "worklet";
      this.port = { postMessage() {}, onmessage: null };
    }

    connect(target) { connections.push([this.name, target.name]); }
    disconnect() {}
  }

  const chromeApi = {
    runtime: { lastError: null },
    tabs: {
      getCurrent(callback) { callback({ id: 91 }); }
    },
    tabCapture: {
      getMediaStreamId(captureOptions, callback) {
        streamOptions.push(captureOptions);
        callback("brave-stream-id");
      }
    }
  };
  const navigatorApi = {
    mediaDevices: {
      async getUserMedia(constraints) {
        mediaRequests.push(constraints);
        if (options.mediaError) throw options.mediaError;
        return stream;
      }
    }
  };
  if (options.firefox) delete chromeApi.tabCapture;
  const controller = new CaptureController({
    chromeApi,
    navigatorApi,
    AudioContextImpl: FakeAudioContext,
    AudioWorkletNodeImpl: FakeAudioWorkletNode,
    socket: { sendAudio: () => "sent" },
    onSilence: options.onSilence,
    onAudioRestored: options.onAudioRestored,
    silenceTimeoutMs: 1000,
    setTimeout(callback) {
      silenceCallback = callback;
      return 7;
    },
    clearTimeout(timer) {
      clearedTimers.push(timer);
    }
  });

  return {
    controller,
    connections,
    gains,
    mediaRequests,
    streamOptions,
    track,
    clearedTimers,
    runSilenceTimer: () => silenceCallback?.()
  };
}

test("capture identifies the Brave consumer tab explicitly", async () => {
  const harness = createHarness();

  await harness.controller.start(42);

  assert.deepEqual(harness.streamOptions, [{ targetTabId: 42, consumerTabId: 91 }]);
  assert.equal(
    harness.mediaRequests[0].audio.mandatory.chromeMediaSourceId,
    "brave-stream-id"
  );
  await harness.controller.close();
});

test("audio worklet stays connected to a silent destination sink", async () => {
  const harness = createHarness();

  await harness.controller.start(42);

  assert.deepEqual(harness.connections, [
    ["source", "worklet"],
    ["worklet", "processor-sink"],
    ["processor-sink", "destination"],
    ["source", "monitor"],
    ["monitor", "destination"]
  ]);
  assert.equal(harness.gains[1].gain.value, 0);
  await harness.controller.close();
  assert.equal(harness.track.stopCalled, true);
});

test("suspended audio context is resumed before listening", async () => {
  const harness = createHarness({ initialContextState: "suspended" });

  assert.equal(await harness.controller.start(42), true);
  assert.equal(harness.controller.context.state, "running");
  await harness.controller.close();
});

test("blocked browser audio returns a recoverable error", async () => {
  const harness = createHarness({
    initialContextState: "suspended",
    resumedContextState: "suspended"
  });

  await assert.rejects(
    harness.controller.start(42),
    /browser blocked audio processing/i
  );
  await harness.controller.close();
});

test("Firefox requests an unprocessed system audio input", async () => {
  const harness = createHarness({ firefox: true });

  assert.equal(harness.controller.sourceType, "audio-input");
  assert.equal(await harness.controller.start(42), true);
  assert.deepEqual(harness.mediaRequests, [{
    audio: {
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false
    },
    video: false
  }]);
  assert.deepEqual(harness.connections, [
    ["source", "worklet"],
    ["worklet", "processor-sink"],
    ["processor-sink", "destination"]
  ]);
  await harness.controller.close();
});

test("Firefox permission denial explains how to select system audio", async () => {
  const permissionError = new Error("Permission denied");
  permissionError.name = "NotAllowedError";
  const harness = createHarness({ firefox: true, mediaError: permissionError });

  await assert.rejects(
    harness.controller.start(42),
    /select your system audio monitor/i
  );
});

test("Firefox missing audio input explains how to expose a monitor", async () => {
  const missingSource = new Error("Requested device not found");
  missingSource.name = "NotFoundError";
  const harness = createHarness({ firefox: true, mediaError: missingSource });

  await assert.rejects(
    harness.controller.start(42),
    /PipeWire\/PulseAudio monitor/i
  );
});

test("Firefox rejects a stream without an audio track", async () => {
  const harness = createHarness({ firefox: true, noAudioTracks: true });

  await assert.rejects(
    harness.controller.start(42),
    /returned no audio track/i
  );
  assert.equal(harness.track.stopCalled, true);
});

test("silent Brave capture warns instead of appearing healthy forever", async () => {
  let warnings = 0;
  const harness = createHarness({ onSilence: () => { warnings += 1; } });

  await harness.controller.start(42);
  harness.runSilenceTimer();

  assert.equal(warnings, 1);
  await harness.controller.close();
});

test("audible worklet input cancels the silence warning", async () => {
  let warnings = 0;
  const harness = createHarness({ onSilence: () => { warnings += 1; } });

  await harness.controller.start(42);
  harness.controller.handleAudio({ data: { pcm: new ArrayBuffer(8), level: 0.2 } });
  harness.runSilenceTimer();

  assert.equal(warnings, 0);
  assert.ok(harness.clearedTimers.includes(7));
  await harness.controller.close();
});

test("audio arriving after a silence warning restores listening status", async () => {
  let restored = 0;
  const harness = createHarness({ onAudioRestored: () => { restored += 1; } });

  await harness.controller.start(42);
  harness.runSilenceTimer();
  harness.controller.handleAudio({ data: { pcm: new ArrayBuffer(8), level: 0.2 } });

  assert.equal(restored, 1);
  await harness.controller.close();
});
