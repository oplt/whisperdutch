(function (root) {
  const OPEN = 1;

  class SubtitleSocket {
    constructor(options = {}) {
      this.WebSocketImpl = options.WebSocketImpl || root.WebSocket;
      this.logger = options.logger;
      this.onMessage = options.onMessage || (() => {});
      this.onDisconnect = options.onDisconnect || (() => {});
      this.onReconnectAttempt = options.onReconnectAttempt || (() => {});
      this.socket = null;
      this.generation = 0;
      this.recoveryGeneration = 0;
      this.flushWaiter = null;
      this.intentionalClose = false;
    }

    get isOpen() {
      return this.socket?.readyState === OPEN;
    }

    owns(generation, socket) {
      return this.generation === generation && this.socket === socket;
    }

    async connect(url, timeoutMs = 5000) {
      this.replaceCurrentSocket();
      const generation = ++this.generation;
      const socket = new this.WebSocketImpl(url);
      this.socket = socket;
      this.intentionalClose = false;
      socket.binaryType = "arraybuffer";

      return new Promise((resolve, reject) => {
        let settled = false;
        const timeout = root.setTimeout(() => {
          fail(new Error("Backend connection timed out."));
        }, timeoutMs);

        const fail = error => {
          if (settled) return;
          settled = true;
          root.clearTimeout(timeout);
          if (this.owns(generation, socket)) this.socket = null;
          this.detachAndClose(socket);
          reject(error);
        };

        socket.onopen = () => {
          if (!this.owns(generation, socket)) {
            this.detachAndClose(socket);
            return;
          }
          settled = true;
          root.clearTimeout(timeout);
          this.logger?.log("debug", "websocket_open", { url, generation });
          resolve(socket);
        };

        socket.onerror = () => {
          if (!this.owns(generation, socket)) return;
          this.logger?.log("warn", "websocket_error", { generation });
          if (!settled) fail(new Error("Could not connect to the backend."));
        };

        socket.onclose = () => {
          root.clearTimeout(timeout);
          if (!this.owns(generation, socket)) return;
          this.socket = null;
          if (!settled) {
            settled = true;
            reject(new Error("Backend connection closed before it was ready."));
            return;
          }
          if (!this.intentionalClose) this.onDisconnect({ generation });
        };

        socket.onmessage = event => {
          if (!this.owns(generation, socket)) return;
          try {
            const payload = JSON.parse(event.data);
            if (payload.type === "flushed") this.resolveFlush(true);
            this.onMessage(payload, generation);
          } catch (error) {
            this.logger?.log("warn", "websocket_invalid_message", {
              error: error?.message || String(error)
            });
          }
        };
      });
    }

    send(payload) {
      if (!this.isOpen) return false;
      this.socket.send(typeof payload === "string" ? payload : JSON.stringify(payload));
      return true;
    }

    sendAudio(buffer, dropBytes = 128 * 1024, warnBytes = 32 * 1024) {
      if (!this.isOpen || !buffer?.byteLength) return "closed";
      if (this.socket.bufferedAmount > dropBytes) return "drop";
      const result = this.socket.bufferedAmount > warnBytes ? "warn" : "sent";
      this.socket.send(buffer);
      return result;
    }

    flush(timeoutMs = 8000) {
      if (!this.isOpen) return Promise.resolve(false);
      if (this.flushWaiter) return this.flushWaiter.promise;
      let resolvePromise;
      const promise = new Promise(resolve => {
        resolvePromise = resolve;
      });
      const timeout = root.setTimeout(() => this.resolveFlush(false), timeoutMs);
      this.flushWaiter = { promise, resolve: resolvePromise, timeout };
      this.send({ type: "flush" });
      return promise;
    }

    resolveFlush(result) {
      if (!this.flushWaiter) return;
      root.clearTimeout(this.flushWaiter.timeout);
      const { resolve } = this.flushWaiter;
      this.flushWaiter = null;
      resolve(result);
    }

    async close({ graceful = true, timeoutMs = 8000 } = {}) {
      this.cancelRecovery();
      this.intentionalClose = true;
      if (graceful) await this.flush(timeoutMs);
      this.resolveFlush(false);
      this.replaceCurrentSocket();
    }

    replaceCurrentSocket() {
      const previous = this.socket;
      this.socket = null;
      this.generation += 1;
      if (previous) this.detachAndClose(previous);
    }

    detachAndClose(socket) {
      socket.onopen = null;
      socket.onerror = null;
      socket.onclose = null;
      socket.onmessage = null;
      try {
        socket.close();
      } catch (_error) {}
    }

    cancelRecovery() {
      this.recoveryGeneration += 1;
    }

    async recover(resolveUrl, options = {}) {
      const recoveryGeneration = ++this.recoveryGeneration;
      const maxAttempts = Math.max(1, options.maxAttempts || 8);
      const sleep = options.sleep || (delay => new Promise(resolve => root.setTimeout(resolve, delay)));
      let lastError = new Error("Connection recovery failed.");

      for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
        if (recoveryGeneration !== this.recoveryGeneration) return null;
        const delayMs = Math.min(5000, 500 * (2 ** Math.min(attempt - 1, 3)));
        this.onReconnectAttempt({ attempt, maxAttempts, delayMs });
        await sleep(delayMs);
        if (recoveryGeneration !== this.recoveryGeneration) return null;
        try {
          const url = await resolveUrl(attempt);
          if (!url) throw new Error("Backend is not available yet.");
          const socket = await this.connect(url);
          if (recoveryGeneration !== this.recoveryGeneration) {
            this.detachAndClose(socket);
            return null;
          }
          this.logger?.log("info", "websocket_recovered", { attempt });
          return socket;
        } catch (error) {
          lastError = error;
        }
      }
      throw lastError;
    }
  }

  const api = { SubtitleSocket, OPEN };
  root.SubtitleApp = Object.assign(root.SubtitleApp || {}, api);
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
