const test = require("node:test");
const assert = require("node:assert/strict");

const {
  BackendService,
  NATIVE_HOST,
  nativeErrorMessage
} = require("../app/backend-service.js");

test("missing native host error explains how to repair the current browser", () => {
  assert.match(
    nativeErrorMessage("Specified native messaging host not found."),
    /native-host\/install_linux\.sh.*without sudo.*restart the browser/i
  );
  assert.match(
    nativeErrorMessage("No such native application com.example.host"),
    /native-host\/install_linux\.sh/i
  );
});

test("native messaging uses the registered backend host name", async () => {
  const calls = [];
  const chromeApi = {
    runtime: {
      lastError: null,
      sendNativeMessage(host, payload, callback) {
        calls.push({ host, payload });
        callback({ ok: true });
      }
    }
  };
  const service = new BackendService({ chromeApi });

  assert.deepEqual(await service.sendNative({ command: "start_backend" }), { ok: true });
  assert.deepEqual(calls, [{
    host: NATIVE_HOST,
    payload: { command: "start_backend" }
  }]);
});
