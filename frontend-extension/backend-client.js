(function (root) {
  const DEFAULT_BASE_URL = "http://127.0.0.1:8000";
  const DEFAULT_WS_URL = "ws://127.0.0.1:8000/ws/subtitles";
  const STORAGE_KEYS = {
    baseUrl: "backendBaseUrl",
    wsUrl: "backendWsUrl",
    updatedAt: "backendUrlUpdatedAt",
    port: "backendPort",
    source: "backendSource",
    recoveryCount: "backendUrlRecoveryCount"
  };
  let readinessPoll = null;

  function storage() {
    try {
      return root.localStorage || null;
    } catch (_err) {
      return null;
    }
  }

  function read(key) {
    try {
      return storage()?.getItem(key) || "";
    } catch (_err) {
      return "";
    }
  }

  function write(key, value) {
    try {
      storage()?.setItem(key, value);
    } catch (_err) {}
  }

  function remove(key) {
    try {
      storage()?.removeItem(key);
    } catch (_err) {}
  }

  function normalizeUrl(value, protocols) {
    if (!value || typeof value !== "string") return null;
    try {
      const parsed = new URL(value.trim());
      if (!protocols.includes(parsed.protocol) || parsed.username || parsed.password || parsed.hash || parsed.search) return null;
      parsed.pathname = parsed.pathname.replace(/\/+$/, "");
      return parsed.toString().replace(/\/$/, "");
    } catch (_err) {
      return null;
    }
  }

  function normalizeBaseUrl(value) {
    return normalizeUrl(value, ["http:", "https:"]);
  }

  function normalizeWsUrl(value) {
    return normalizeUrl(value, ["ws:", "wss:"]);
  }

  function deriveWsUrl(baseUrl) {
    const normalizedBase = normalizeBaseUrl(baseUrl);
    if (!normalizedBase) return null;
    const parsed = new URL(normalizedBase);
    parsed.protocol = parsed.protocol === "https:" ? "wss:" : "ws:";
    parsed.pathname = `${parsed.pathname.replace(/\/+$/, "")}/ws/subtitles`;
    parsed.search = "";
    parsed.hash = "";
    return parsed.toString();
  }

  function baseUrlFromWs(wsUrl) {
    const normalizedWs = normalizeWsUrl(wsUrl);
    if (!normalizedWs) return null;
    const parsed = new URL(normalizedWs);
    parsed.protocol = parsed.protocol === "wss:" ? "https:" : "http:";
    parsed.pathname = parsed.pathname.replace(/\/ws\/subtitles\/?$/, "") || "";
    parsed.search = "";
    parsed.hash = "";
    return normalizeBaseUrl(parsed.toString());
  }

  function storedBaseUrl() {
    const raw = read(STORAGE_KEYS.baseUrl);
    const normalized = normalizeBaseUrl(raw);
    if (raw && !normalized) invalidateStoredConnection();
    return normalized;
  }

  function storedWsUrl() {
    const raw = read(STORAGE_KEYS.wsUrl);
    const normalized = normalizeWsUrl(raw);
    if (raw && !normalized) remove(STORAGE_KEYS.wsUrl);
    return normalized;
  }

  function getBaseUrl() {
    return storedBaseUrl() || DEFAULT_BASE_URL;
  }

  function getWsUrl() {
    return storedWsUrl() || deriveWsUrl(getBaseUrl()) || DEFAULT_WS_URL;
  }

  function setConnectionUrls(baseUrl, wsUrl, source = "manual") {
    const normalizedBase = normalizeBaseUrl(baseUrl);
    if (!normalizedBase) return null;
    const normalizedWs = normalizeWsUrl(wsUrl) || deriveWsUrl(normalizedBase);
    if (!normalizedWs) return null;
    write(STORAGE_KEYS.baseUrl, normalizedBase);
    write(STORAGE_KEYS.wsUrl, normalizedWs);
    write(STORAGE_KEYS.updatedAt, new Date().toISOString());
    write(STORAGE_KEYS.port, String(new URL(normalizedBase).port || (new URL(normalizedBase).protocol === "https:" ? 443 : 80)));
    write(STORAGE_KEYS.source, source || "manual");
    return { baseUrl: normalizedBase, wsUrl: normalizedWs, source: source || "manual" };
  }

  function setWsUrl(wsUrl, options = {}) {
    const normalizedWs = normalizeWsUrl(wsUrl);
    if (!normalizedWs) return null;
    return setConnectionUrls(baseUrlFromWs(normalizedWs) || getBaseUrl(), normalizedWs, options.source || "manual");
  }

  function invalidateStoredConnection() {
    Object.entries(STORAGE_KEYS)
      .filter(([name]) => name !== "recoveryCount")
      .forEach(([, key]) => remove(key));
  }

  function getConnectionMetadata() {
    return {
      updatedAt: read(STORAGE_KEYS.updatedAt) || null,
      port: read(STORAGE_KEYS.port) || null,
      source: read(STORAGE_KEYS.source) || null,
      recoveryCount: Number(read(STORAGE_KEYS.recoveryCount) || 0)
    };
  }

  function recordRecovery() {
    const count = Number(read(STORAGE_KEYS.recoveryCount) || 0) + 1;
    write(STORAGE_KEYS.recoveryCount, String(count));
    return count;
  }

  function url(path, baseUrl = getBaseUrl()) {
    const normalizedBase = normalizeBaseUrl(baseUrl) || DEFAULT_BASE_URL;
    return `${normalizedBase}${path.startsWith("/") ? path : `/${path}`}`;
  }

  function request(resource, options) {
    const fetchImpl = root.fetch || (typeof fetch === "function" ? fetch : null);
    if (!fetchImpl) return Promise.reject(new Error("Fetch is unavailable."));
    return fetchImpl(resource, options);
  }

  async function probeBackend(baseUrl = getBaseUrl()) {
    const normalizedBase = normalizeBaseUrl(baseUrl);
    if (!normalizedBase) return null;
    try {
      const response = await request(url("/health/live", normalizedBase), { cache: "no-store" });
      if (!response.ok) return null;
      const data = await response.json();
      return data?.ok || data?.live ? data : null;
    } catch (_err) {
      return null;
    }
  }

  async function probeReady(baseUrl = getBaseUrl()) {
    const normalizedBase = normalizeBaseUrl(baseUrl);
    if (!normalizedBase) return null;
    try {
      const response = await request(url("/health/ready", normalizedBase), { cache: "no-store" });
      const data = await response.json();
      return { ...data, ready: Boolean(response.ok && data?.ready && data?.model_ready) };
    } catch (_err) {
      return null;
    }
  }

  function waitUntilReady(options = {}) {
    if (readinessPoll) return readinessPoll;
    const timeoutMs = Math.max(250, Number(options.timeoutMs) || 45000);
    const startedAt = Date.now();
    const onProgress = typeof options.onProgress === "function" ? options.onProgress : () => {};
    const sleep = typeof options.sleep === "function"
      ? options.sleep
      : delay => new Promise(resolve => setTimeout(resolve, delay));

    readinessPoll = (async () => {
      let attempt = 0;
      while (Date.now() - startedAt < timeoutMs) {
        attempt += 1;
        const status = await probeReady();
        if (status?.ready) return status;
        const delayMs = Math.min(2000, Math.round(250 * (1.6 ** Math.min(attempt - 1, 6))));
        onProgress({ attempt, delayMs, phase: status?.phase || "starting" });
        await sleep(delayMs);
      }
      return null;
    })().finally(() => {
      readinessPoll = null;
    });
    return readinessPoll;
  }

  async function findHealthyConnection({ includeDefault = true } = {}) {
    const cachedBase = storedBaseUrl();
    const cachedWs = storedWsUrl();
    const candidates = [cachedBase, includeDefault ? DEFAULT_BASE_URL : null].filter(Boolean).filter((value, index, all) => all.indexOf(value) === index);
    for (const candidate of candidates) {
      const health = await probeBackend(candidate);
      if (!health) continue;
      const source = candidate === cachedBase ? (read(STORAGE_KEYS.source) || "manual") : "default";
      const expectedWs = deriveWsUrl(candidate);
      const recovered = Boolean(cachedBase && (candidate !== cachedBase || (cachedWs && cachedWs !== expectedWs)));
      const recoveryCount = recovered ? recordRecovery() : getConnectionMetadata().recoveryCount;
      const connection = setConnectionUrls(candidate, expectedWs, source);
      return { ...connection, health, recovered, recoveryCount };
    }
    return null;
  }

  async function postClientLog(record) {
    return request(url("/api/logs/client"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(record),
      cache: "no-store",
      keepalive: true
    });
  }

  async function fetchBackendLogs(lines) {
    const response = await request(`${url("/api/logs/recent")}?lines=${lines}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function fetchGlossary() {
    const response = await request(url("/api/glossary"), { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function saveGlossaryRules(rules) {
    const response = await request(url("/api/glossary"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rules }),
      cache: "no-store"
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.message || `HTTP ${response.status}`);
    return data;
  }

  async function fetchPrivacy() {
    const response = await request(url("/api/privacy"), { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function savePrivacy(logTranscriptText) {
    const response = await request(url("/api/privacy"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ log_transcript_text: Boolean(logTranscriptText) }),
      cache: "no-store"
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.message || `HTTP ${response.status}`);
    return data;
  }

  async function fetchHistory(limit = 50) {
    const response = await request(`${url("/api/history")}?limit=${limit}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function fetchHistorySession(clientId) {
    const response = await request(url(`/api/history/${encodeURIComponent(clientId)}`), { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function deleteHistorySession(clientId) {
    const response = await request(url(`/api/history/${encodeURIComponent(clientId)}`), { method: "DELETE", cache: "no-store" });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.message || `HTTP ${response.status}`);
    return data;
  }

  async function fetchLanguages() {
    const response = await request(url("/api/languages"), { cache: "no-store" });
    if (!response.ok) throw new Error(`Language catalog request failed (${response.status}).`);
    return response.json();
  }

  const api = {
    DEFAULT_BASE_URL,
    DEFAULT_WS_URL,
    getBaseUrl,
    getWsUrl,
    setWsUrl,
    setConnectionUrls,
    getConnectionMetadata,
    invalidateStoredConnection,
    deriveWsUrl,
    baseUrlFromWs,
    probeBackend,
    probeReady,
    waitUntilReady,
    findHealthyConnection,
    postClientLog,
    fetchBackendLogs,
    fetchLanguages,
    fetchGlossary,
    saveGlossaryRules,
    fetchPrivacy,
    savePrivacy,
    fetchHistory,
    fetchHistorySession,
    deleteHistorySession,
    url
  };
  root.BackendClient = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
