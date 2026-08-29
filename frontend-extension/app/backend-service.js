(function (root) {
  const NATIVE_HOST = "com.polatozgur111.dutch_subtitle_backend";

  class BackendService {
    constructor(options = {}) {
      this.client = options.client || root.BackendClient;
      this.chrome = options.chromeApi || root.chrome;
      this.logger = options.logger;
      this.onProgress = options.onProgress || (() => {});
      this.connectionPromise = null;
      this.nativeStatus = "Not checked";
    }

    sendNative(payload) {
      return new Promise((resolve, reject) => {
        this.chrome.runtime.sendNativeMessage(NATIVE_HOST, payload, response => {
          const error = this.chrome.runtime.lastError;
          if (error) {
            this.nativeStatus = "Unavailable";
            reject(new Error(error.message));
            return;
          }
          this.nativeStatus = response?.ok ? "Available" : "Unavailable";
          resolve(response);
        });
      });
    }

    remember(response) {
      if (!response || (!response.base_url && !response.ws_url)) return null;
      const baseUrl = response.base_url || this.client.baseUrlFromWs(response.ws_url);
      return this.client.setConnectionUrls(baseUrl, response.ws_url, "native");
    }

    findConnection() {
      if (this.connectionPromise) return this.connectionPromise;
      this.connectionPromise = this.client.findHealthyConnection().finally(() => {
        this.connectionPromise = null;
      });
      return this.connectionPromise;
    }

    async waitUntilReady() {
      const status = await this.client.waitUntilReady({
        timeoutMs: 45000,
        onProgress: progress => this.onProgress({
          kind: "models",
          message: `Preparing models, check ${progress.attempt}`,
          ...progress
        })
      });
      if (status?.ready) return status;
      throw new Error(await this.diagnosticMessage());
    }

    async ensureReady({ restart = false, device = "cpu" } = {}) {
      this.onProgress({ kind: "backend", message: restart ? "Restarting local service" : "Starting local service" });
      let connection = restart ? null : await this.findConnection();
      if (connection) {
        await this.waitUntilReady();
        return connection;
      }

      const response = await this.sendNative({
        command: restart ? "restart_backend" : "start_backend",
        asr_device: device
      });
      this.remember(response);
      if (!response?.ok) {
        throw new Error(response?.error || "The local translation service could not be started.");
      }
      await this.waitUntilReady();
      connection = await this.findConnection();
      this.logger?.log("info", restart ? "backend_restart" : "backend_started");
      return connection || {
        baseUrl: this.client.getBaseUrl(),
        wsUrl: this.client.getWsUrl(),
        source: "native"
      };
    }

    async restart(device) {
      return this.ensureReady({ restart: true, device });
    }

    async stop() {
      const response = await this.sendNative({ command: "stop_backend" });
      if (!response?.ok) {
        throw new Error(response?.error || "The local translation service could not be stopped.");
      }
      return response;
    }

    async diagnosticMessage() {
      try {
        const response = await root.fetch(this.client.url("/debug/device"), { cache: "no-store" });
        if (!response.ok) return "Backend diagnostics are unavailable.";
        const data = await response.json();
        const readiness = data?.readiness;
        const error = readiness?.last_error || readiness?.startup_status?.error;
        if (error?.message) return error.message;
        if (readiness?.startup_status?.phase) return `Backend phase: ${readiness.startup_status.phase}`;
      } catch (_error) {}
      return "The backend models did not become ready.";
    }

    async diagnostics() {
      const [live, ready] = await Promise.all([
        this.client.probeBackend(),
        this.client.probeReady()
      ]);
      return {
        nativeHost: this.nativeStatus,
        backend: live ? "Running" : "Stopped",
        models: ready?.ready ? "Ready" : live ? ready?.phase || "Loading" : "Unavailable",
        connection: this.client.getConnectionMetadata()
      };
    }
  }

  const api = { BackendService, NATIVE_HOST };
  root.SubtitleApp = Object.assign(root.SubtitleApp || {}, api);
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
