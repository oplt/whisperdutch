const test = require("node:test");
const assert = require("node:assert/strict");
const { SubtitleSocket } = require("../app/websocket-client.js");

class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.binaryType = "arraybuffer";
    this.bufferedAmount = 0;
    this.onopen = null;
    this.onerror = null;
    this.onclose = null;
    this.onmessage = null;
    this.sent = [];
    FakeWebSocket.instances.push(this);
  }

  send(data) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 3;
    this.onclose?.();
  }

  open() {
    this.readyState = 1;
    this.onopen?.();
  }
}

FakeWebSocket.instances = [];

test("SubtitleSocket ignores stale socket callbacks after replacement", async () => {
  FakeWebSocket.instances = [];
  const messages = [];
  const disconnects = [];
  const socket = new SubtitleSocket({
    WebSocketImpl: FakeWebSocket,
    onMessage: payload => messages.push(payload),
    onDisconnect: () => disconnects.push("disconnect")
  });

  const connectPromise = socket.connect("ws://127.0.0.1:8000/ws/subtitles");
  const first = FakeWebSocket.instances[0];
  first.open();
  await connectPromise;

  await socket.close({ graceful: false });
  first.onmessage?.({ data: JSON.stringify({ type: "partial", dutch: "stale" }) });
  first.onclose?.();

  assert.deepEqual(messages, []);
  assert.deepEqual(disconnects, []);
});

test("SubtitleSocket recovery stops when cancelled", async () => {
  FakeWebSocket.instances = [];
  const socket = new SubtitleSocket({ WebSocketImpl: FakeWebSocket });
  const recovery = socket.recover(async () => "ws://127.0.0.1:8000/ws/subtitles", {
    maxAttempts: 3,
    sleep: async () => {}
  });
  socket.cancelRecovery();
  const result = await recovery;
  assert.equal(result, null);
});

test("sendAudio reports backpressure thresholds", async () => {
  FakeWebSocket.instances = [];
  const socket = new SubtitleSocket({ WebSocketImpl: FakeWebSocket });
  const connectPromise = socket.connect("ws://127.0.0.1:8000/ws/subtitles");
  const ws = FakeWebSocket.instances[0];
  ws.open();
  await connectPromise;

  ws.bufferedAmount = 40 * 1024;
  assert.equal(socket.sendAudio(new ArrayBuffer(8)), "warn");
  ws.bufferedAmount = 200 * 1024;
  assert.equal(socket.sendAudio(new ArrayBuffer(8)), "drop");
});
