(function (root) {
  const ENDPOINTS = {
    clientLogs: "http://127.0.0.1:8000/api/logs/client",
    backendLogs: "http://127.0.0.1:8000/api/logs/recent",
    glossary: "http://127.0.0.1:8000/api/glossary"
  };

  function getBaseUrl() {
    const storage = root.localStorage;
    return storage?.getItem("backendBaseUrl") || "http://127.0.0.1:8000";
  }

  function setBaseUrl(baseUrl) {
    if (baseUrl && root.localStorage) root.localStorage.setItem("backendBaseUrl", baseUrl);
  }

  function getWsUrl() {
    const storage = root.localStorage;
    return storage?.getItem("backendWsUrl") || "ws://127.0.0.1:8000/ws/subtitles";
  }

  function setWsUrl(wsUrl) {
    if (wsUrl && root.localStorage) root.localStorage.setItem("backendWsUrl", wsUrl);
  }

  function url(path) {
    return `${getBaseUrl()}${path}`;
  }

  async function postClientLog(record) {
    return fetch(url("/api/logs/client"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(record),
      keepalive: true
    });
  }

  async function fetchBackendLogs(lines) {
    const response = await fetch(`${url("/api/logs/recent")}?lines=${lines}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function fetchGlossary() {
    const response = await fetch(url("/api/glossary"), { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function saveGlossaryRules(rules) {
    const response = await fetch(url("/api/glossary"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rules })
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.message || `HTTP ${response.status}`);
    return data;
  }

  async function fetchPrivacy() {
    const response = await fetch(url("/api/privacy"), { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function savePrivacy(logTranscriptText) {
    const response = await fetch(url("/api/privacy"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ log_transcript_text: Boolean(logTranscriptText) })
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.message || `HTTP ${response.status}`);
    return data;
  }

  async function fetchHistory(limit = 50) {
    const response = await fetch(`${url("/api/history")}?limit=${limit}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function fetchHistorySession(clientId) {
    const response = await fetch(url(`/api/history/${encodeURIComponent(clientId)}`), { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function deleteHistorySession(clientId) {
    const response = await fetch(url(`/api/history/${encodeURIComponent(clientId)}`), { method: "DELETE" });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.message || `HTTP ${response.status}`);
    return data;
  }

  const api = {
    ENDPOINTS,
    getBaseUrl,
    setBaseUrl,
    getWsUrl,
    setWsUrl,
    postClientLog,
    fetchBackendLogs,
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
