const TARGET_SAMPLE_RATE = 16000;
const WS_BUFFER_WARN_BYTES = 32 * 1024;
const WS_BUFFER_DROP_BYTES = 128 * 1024;
const MAX_RENDERED_SUBTITLES = 500;
const NATIVE_HOST = "com.polatozgur111.dutch_subtitle_backend";

const params = new URLSearchParams(location.search);
const tabId = Number(params.get("tabId"));
const autoStartRequested = params.get("autostart") === "1";

const backendUrlEl = document.getElementById("backendUrl");
const asrDeviceEls = Array.from(document.querySelectorAll('input[name="asrDevice"]'));
const targetLangEl = document.getElementById("targetLang");
const qualityModeEl = document.getElementById("qualityMode");
const displayModeEl = document.getElementById("displayMode");
const contextPromptEl = document.getElementById("contextPrompt");
const monitorVolumeEl = document.getElementById("monitorVolume");
const muteMonitorEl = document.getElementById("muteMonitor");
const transcriptLoggingEl = document.getElementById("transcriptLogging");
const dutchFontSizeEl = document.getElementById("dutchFontSize");
const translationFontSizeEl = document.getElementById("translationFontSize");
const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const pauseBtn = document.getElementById("pauseBtn");
const reconnectBtn = document.getElementById("reconnectBtn");
const audioSourceEl = document.getElementById("audioSource");
const inputLevelBarEl = document.getElementById("inputLevelBar");
const statusEl = document.getElementById("status");
const audioStatusEl = document.getElementById("audioStatus");
const latencyEl = document.getElementById("latency");
const asrLatencyEl = document.getElementById("asrLatency");
const translationLatencyEl = document.getElementById("translationLatency");
const connectedBadgeEl = document.getElementById("connectedBadge");
const capturingBadgeEl = document.getElementById("capturingBadge");
const privacyStatusEl = document.getElementById("privacyStatus");
const qualityBadgeEl = document.getElementById("qualityBadge");
const currentSubtitleEl = document.getElementById("currentSubtitle");
const historySubtitlesEl = document.getElementById("historySubtitles");
const subtitleFeedEl = document.getElementById("subtitleFeed");
const subtitlesTabEl = document.getElementById("subtitlesTab");
const settingsTabEl = document.getElementById("settingsTab");
const subtitlesPanelEl = document.getElementById("subtitlesPanel");
const settingsPanelEl = document.getElementById("settingsPanel");
const frontendLogsEl = document.getElementById("frontendLogs");
const refreshLogsBtn = document.getElementById("refreshLogsBtn");
const openGlossaryBtn = document.getElementById("openGlossaryBtn");
const glossaryDialogEl = document.getElementById("glossaryDialog");
const glossarySearchEl = document.getElementById("glossarySearch");
const glossaryPatternEl = document.getElementById("glossaryPattern");
const glossaryReplacementEl = document.getElementById("glossaryReplacement");
const addGlossaryBtn = document.getElementById("addGlossaryBtn");
const glossaryListEl = document.getElementById("glossaryList");
const retryStatusEl = document.getElementById("retryStatus");
const sessionNameEl = document.getElementById("sessionName");
const sessionSelectEl = document.getElementById("sessionSelect");
const newSessionBtn = document.getElementById("newSessionBtn");
const renameSessionBtn = document.getElementById("renameSessionBtn");
const saveSessionBtn = document.getElementById("saveSessionBtn");
const restoreSessionBtn = document.getElementById("restoreSessionBtn");
const clearSessionBtn = document.getElementById("clearSessionBtn");
const exportTxtBtn = document.getElementById("exportTxtBtn");
const exportVttBtn = document.getElementById("exportVttBtn");
const exportSrtBtn = document.getElementById("exportSrtBtn");
const retainTranscriptEl = document.getElementById("retainTranscript");
const autoDeleteMinutesEl = document.getElementById("autoDeleteMinutes");
const backendToolbarEl = document.getElementById("backendToolbar");
const backendServiceLabelEl = document.getElementById("backendServiceLabel");
const backendServiceDetailEl = document.getElementById("backendServiceDetail");
const refreshBackendStatusBtn = document.getElementById("refreshBackendStatusBtn");
const restartBackendBtn = document.getElementById("restartBackendBtn");
const stopBackendBtn = document.getElementById("stopBackendBtn");
const diagnosticNativeEl = document.getElementById("diagnosticNative");
const diagnosticBackendEl = document.getElementById("diagnosticBackend");
const diagnosticModelsEl = document.getElementById("diagnosticModels");
const diagnosticTabAudioEl = document.getElementById("diagnosticTabAudio");
const backendDiagnosticMessageEl = document.getElementById("backendDiagnosticMessage");
const openBackendLogsBtn = document.getElementById("openBackendLogsBtn");
const diagnosticLogsDrawerEl = document.getElementById("diagnosticLogsDrawer");

let ws = null;
let mediaStream = null;
let audioContext = null;
let mediaSource = null;
let workletNode = null;
let monitorGain = null;
let startedAt = null;
let currentSubtitleItem = null;
let stablePartialText = "";
let droppedAudioChunks = 0;
let lastBackpressureWarningAt = 0;
let transcriptItems = [];
let transcriptById = new Map();
let glossaryRules = [];
let manualStopRequested = false;
let reconnecting = false;
let reconnectAttempts = 0;
let reconnectTimer = null;
let stopPromise = null;
let flushResolver = null;
let inputLevelFrame = null;
let pendingInputLevel = 0;
let configDebounceTimer = null;
let transcriptionPaused = false;
let currentSessionId = `session-${Date.now()}`;
let autoDeleteTimer = null;
let connectionRecoveryPromise = null;
let pendingSubtitleQueue = [];
let backendActionInProgress = false;
let nativeHostStatus = "Not checked";

startBtn.addEventListener("click", startTranslation);
stopBtn.addEventListener("click", stopCapture);
if (pauseBtn) pauseBtn.addEventListener("click", togglePause);
if (reconnectBtn) reconnectBtn.addEventListener("click", reconnectBackend);
if (audioSourceEl) audioSourceEl.addEventListener("change", () => {
  audioSourceEl.value = "tab";
  setStatus("Only current tab audio is available without backend or extension changes.", true);
});
qualityModeEl.addEventListener("change", sendConfigIfOpen);
displayModeEl.addEventListener("change", () => applyDisplayMode(displayModeEl.value, true));
targetLangEl.addEventListener("change", sendConfigIfOpen);
contextPromptEl.addEventListener("input", () => {
  localStorage.setItem("subtitleContextPrompt", contextPromptEl.value);
  clearTimeout(configDebounceTimer);
  configDebounceTimer = setTimeout(sendConfigIfOpen, 350);
});
monitorVolumeEl.addEventListener("input", () => {
  localStorage.setItem("subtitleMonitorVolume", monitorVolumeEl.value);
  updateMonitorGain();
});
muteMonitorEl.addEventListener("change", () => {
  localStorage.setItem("subtitleMonitorMuted", muteMonitorEl.checked ? "1" : "0");
  updateMonitorGain();
});
transcriptLoggingEl.addEventListener("change", updatePrivacy);
dutchFontSizeEl.addEventListener("input", () => updateFontSizes(true));
translationFontSizeEl.addEventListener("input", () => updateFontSizes(true));
if (addGlossaryBtn) addGlossaryBtn.addEventListener("click", addGlossaryRule);
if (openGlossaryBtn) openGlossaryBtn.addEventListener("click", openGlossaryDialog);
if (glossarySearchEl) glossarySearchEl.addEventListener("input", renderGlossary);
if (newSessionBtn) newSessionBtn.addEventListener("click", newSession);
if (renameSessionBtn) renameSessionBtn.addEventListener("click", renameSession);
if (saveSessionBtn) saveSessionBtn.addEventListener("click", saveCurrentSession);
if (restoreSessionBtn) restoreSessionBtn.addEventListener("click", restoreSelectedSession);
if (clearSessionBtn) clearSessionBtn.addEventListener("click", clearSessionTranscript);
if (exportTxtBtn) exportTxtBtn.addEventListener("click", () => exportTranscript("txt"));
if (exportVttBtn) exportVttBtn.addEventListener("click", () => exportTranscript("vtt"));
if (exportSrtBtn) exportSrtBtn.addEventListener("click", () => exportTranscript("srt"));
if (retainTranscriptEl) retainTranscriptEl.addEventListener("change", updateRetentionPreference);
if (autoDeleteMinutesEl) autoDeleteMinutesEl.addEventListener("input", updateRetentionPreference);
window.addEventListener("keydown", handleShortcut);
window.addEventListener("beforeunload", () => {
  void stopCapture({ graceful: false, updateStatus: false });
});
if (subtitlesTabEl) subtitlesTabEl.addEventListener("click", () => setActiveTab("subtitles"));
if (settingsTabEl) settingsTabEl.addEventListener("click", () => setActiveTab("settings"));
[subtitlesTabEl, settingsTabEl].filter(Boolean).forEach((tab) => tab.addEventListener("keydown", handleTabKeydown));
if (refreshLogsBtn) refreshLogsBtn.addEventListener("click", refreshBackendLogs);
if (refreshBackendStatusBtn) refreshBackendStatusBtn.addEventListener("click", () => refreshBackendStatus({ clearError: true }));
if (restartBackendBtn) restartBackendBtn.addEventListener("click", restartBackendService);
if (stopBackendBtn) stopBackendBtn.addEventListener("click", stopBackendService);
if (openBackendLogsBtn) openBackendLogsBtn.addEventListener("click", openBackendLogs);
backendUrlEl.addEventListener("change", () => {
  const connection = BackendClient.setWsUrl(backendUrlEl.value.trim(), { source: "manual" });
  if (!connection) {
    backendUrlEl.value = BackendClient.getWsUrl();
    setStatus("Invalid WebSocket URL. Using the saved backend connection.", true);
  }
});
asrDeviceEls.forEach((input) => input.addEventListener("change", () => {
  const device = Settings.setDevice(localStorage, input.value);
  const label = device === "cuda" ? "GPU" : "CPU";
  setStatus(`${label} selected. Restart the backend to apply it.`);
  logClient("info", "asr_device_preference_changed", { device });
}));

logClient("info", "subtitle_window_loaded", { tabId, autoStartRequested });
backendUrlEl.value = BackendClient.getWsUrl();
updateFontSizes(false);
loadModePreference();
loadDevicePreference();
loadDisplayModePreference();
loadContextPreference();
loadMonitorPreference();
loadHistoryPreference();
loadSessionPreference();
loadTabPreference();
setAudioStatus("Not capturing");
updateConnectionBadge("Idle", "idle");
updateLatencyBadges();
updateRetryStatus("Ready", "idle");
initializeSubtitleWindow();

function logClient(level, message, context = {}) {
  const record = {
    ts: new Date().toISOString(),
    level,
    source: "subtitle-window",
    message,
    context
  };

  const line = `${record.ts} ${level.toUpperCase()} ${message} ${Object.keys(context).length ? JSON.stringify(context) : ""}`;
  if (frontendLogsEl) {
    frontendLogsEl.textContent = `${line}\n${frontendLogsEl.textContent || ""}`.slice(0, 30000);
  }

  try {
    const method = level === "error" ? "error" : level === "warn" ? "warn" : "log";
    console[method]("[DutchSubtitles]", message, context);
  } catch (_err) {}

  BackendClient.postClientLog(record).catch(() => {});
}

function rememberBackendResponse(response) {
  if (!response || (!response.base_url && !response.ws_url)) return;
  const baseUrl = response.base_url || BackendClient.baseUrlFromWs(response.ws_url);
  BackendClient.setConnectionUrls(baseUrl, response.ws_url, "native");
  backendUrlEl.value = BackendClient.getWsUrl();
}

function renderBackendService({ state, label, detail, backend, models, error = "" }) {
  if (backendToolbarEl) backendToolbarEl.dataset.state = state;
  if (backendServiceLabelEl) backendServiceLabelEl.textContent = label;
  if (backendServiceDetailEl) backendServiceDetailEl.textContent = detail;
  if (diagnosticNativeEl) diagnosticNativeEl.textContent = nativeHostStatus;
  if (diagnosticBackendEl) diagnosticBackendEl.textContent = backend;
  if (diagnosticModelsEl) diagnosticModelsEl.textContent = models;
  if (diagnosticTabAudioEl) diagnosticTabAudioEl.textContent = Number.isInteger(tabId) && tabId > 0
    ? "Selected tab available"
    : "No source tab";
  if (backendDiagnosticMessageEl) {
    backendDiagnosticMessageEl.textContent = error;
    backendDiagnosticMessageEl.hidden = !error;
  }

  const backendLive = state === "ready" || state === "starting";
  if (!backendActionInProgress) {
    if (restartBackendBtn) restartBackendBtn.disabled = false;
    if (stopBackendBtn) stopBackendBtn.disabled = !backendLive;
    if (refreshBackendStatusBtn) refreshBackendStatusBtn.disabled = false;
  }
}

function setBackendActionInProgress(inProgress) {
  backendActionInProgress = inProgress;
  if (restartBackendBtn) restartBackendBtn.disabled = inProgress;
  if (stopBackendBtn) {
    const state = backendToolbarEl?.dataset.state;
    stopBackendBtn.disabled = inProgress || (state !== "ready" && state !== "starting");
  }
  if (refreshBackendStatusBtn) refreshBackendStatusBtn.disabled = inProgress;
  if (inProgress) startBtn.disabled = true;
  else if (!mediaStream && (!ws || ws.readyState !== WebSocket.OPEN)) startBtn.disabled = false;
}

function sendNativeMessage(payload) {
  return new Promise((resolve, reject) => {
    logClient("info", "native_message_send", payload);
    chrome.runtime.sendNativeMessage(NATIVE_HOST, payload, response => {
      const error = chrome.runtime.lastError;
      if (error) {
        nativeHostStatus = "Connection failed";
        reject(new Error(error.message));
        return;
      }
      nativeHostStatus = response?.ok ? "Available" : "Connection failed";
      logClient("info", "native_message_response", response || {});
      resolve(response);
    });
  });
}

async function checkBackendReady() {
  try {
    const response = await fetch(BackendClient.url("/health/ready"), { cache: "no-store" });
    if (!response.ok) return false;
    const data = await response.json();
    return Boolean(data?.ready && data?.model_ready);
  } catch (_err) {
    return false;
  }
}

async function waitForBackend(maxAttempts = 45) {
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    if (await checkBackendReady()) return true;
    renderBackendService({
      state: "starting",
      label: "Preparing models",
      detail: `Loading the local translation service (${attempt}s)`,
      backend: "Running",
      models: "Loading"
    });
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  return false;
}

async function backendDiagnosticMessage() {
  try {
    const response = await fetch(BackendClient.url("/debug/device"), { cache: "no-store" });
    if (!response.ok) return "Backend diagnostics are unavailable.";
    const data = await response.json();
    const readiness = data?.readiness;
    const error = readiness?.last_error || readiness?.startup_status?.error;
    if (error?.message) return error.message;
    if (readiness?.startup_status?.phase) return `Backend phase: ${readiness.startup_status.phase}`;
  } catch (_err) {}
  return "The backend models did not become ready.";
}

async function ensureBackendReady({ restart = false } = {}) {
  renderBackendService({
    state: "starting",
    label: restart ? "Restarting backend" : "Connecting",
    detail: restart ? "Restarting the local translation service" : "Checking the local backend and translation models",
    backend: restart ? "Restarting" : "Checking...",
    models: "Checking..."
  });

  let connection = restart ? null : await reconcileBackendConnection();
  if (!restart && connection) {
    const ready = await checkBackendReady() || await waitForBackend();
    if (!ready) throw new Error(await backendDiagnosticMessage());
    renderBackendService({
      state: "ready",
      label: "Ready",
      detail: "Backend and translation models are available",
      backend: "Available",
      models: "Available"
    });
    return connection;
  }

  const response = await sendNativeMessage({
    command: restart ? "restart_backend" : "start_backend",
    asr_device: Settings.getDevice(localStorage)
  });
  rememberBackendResponse(response);
  if (!response?.ok) throw new Error(response?.error || "The local translation service could not be started.");
  if (!await waitForBackend()) throw new Error(await backendDiagnosticMessage());

  connection = await reconcileBackendConnection();
  renderBackendService({
    state: "ready",
    label: "Ready",
    detail: "Backend and translation models are available",
    backend: "Available",
    models: "Available"
  });
  return connection || {
    baseUrl: BackendClient.getBaseUrl(),
    wsUrl: BackendClient.getWsUrl(),
    source: "native"
  };
}

async function refreshBackendStatus({ clearError = false } = {}) {
  if (backendActionInProgress) return null;
  if (clearError && backendDiagnosticMessageEl) {
    backendDiagnosticMessageEl.textContent = "";
    backendDiagnosticMessageEl.hidden = true;
  }

  renderBackendService({
    state: "starting",
    label: "Checking",
    detail: "Checking the local backend and translation models",
    backend: "Checking...",
    models: "Checking..."
  });

  const connection = await reconcileBackendConnection();
  if (!connection) {
    renderBackendService({
      state: "stopped",
      label: "Stopped",
      detail: "Start capture to launch the local translation service",
      backend: "Not running",
      models: "Unavailable"
    });
    return null;
  }

  const ready = await checkBackendReady();
  renderBackendService({
    state: ready ? "ready" : "starting",
    label: ready ? "Ready" : "Preparing models",
    detail: ready ? "Backend and translation models are available" : "The backend is running while models load",
    backend: "Available",
    models: ready ? "Available" : "Loading"
  });
  return connection;
}

async function startTranslation() {
  if (backendActionInProgress || mediaStream || (ws && ws.readyState === WebSocket.OPEN)) return;
  setBackendActionInProgress(true);
  setStatus("Preparing translation service...");
  try {
    await ensureBackendReady();
    await startCapture();
  } catch (error) {
    const message = error?.message || String(error);
    renderBackendService({
      state: "error",
      label: "Connection error",
      detail: "The local translation service could not be started",
      backend: "Unavailable",
      models: "Unavailable",
      error: message
    });
    setStatus(message, true);
    logClient("error", "translation_start_failed", { error: message });
  } finally {
    setBackendActionInProgress(false);
  }
}

async function restartBackendService() {
  if (backendActionInProgress) return;
  const resumeCapture = Boolean(mediaStream || (ws && ws.readyState === WebSocket.OPEN));
  setBackendActionInProgress(true);
  try {
    if (resumeCapture) await stopCapture();
    await ensureBackendReady({ restart: true });
    if (resumeCapture) await startCapture();
  } catch (error) {
    const message = error?.message || String(error);
    renderBackendService({
      state: "error",
      label: "Restart failed",
      detail: "The local translation service could not be restarted",
      backend: "Unavailable",
      models: "Unavailable",
      error: message
    });
    setStatus(message, true);
    logClient("error", "backend_restart_failed", { error: message });
  } finally {
    setBackendActionInProgress(false);
  }
}

async function stopBackendService() {
  if (backendActionInProgress) return;
  setBackendActionInProgress(true);
  try {
    if (mediaStream || ws) await stopCapture();
    renderBackendService({
      state: "starting",
      label: "Stopping",
      detail: "Stopping the local translation service",
      backend: "Stopping",
      models: "Unavailable"
    });
    const response = await sendNativeMessage({ command: "stop_backend" });
    if (!response?.ok) throw new Error(response?.error || "The local translation service could not be stopped.");
    renderBackendService({
      state: "stopped",
      label: "Stopped",
      detail: "Start capture to launch the local translation service",
      backend: "Not running",
      models: "Unavailable"
    });
  } catch (error) {
    const message = error?.message || String(error);
    renderBackendService({
      state: "error",
      label: "Stop failed",
      detail: "The local translation service could not be stopped",
      backend: "Unknown",
      models: "Unknown",
      error: message
    });
    setStatus(message, true);
    logClient("error", "backend_stop_failed", { error: message });
  } finally {
    setBackendActionInProgress(false);
  }
}

async function openBackendLogs() {
  setActiveTab("settings", true);
  if (diagnosticLogsDrawerEl) diagnosticLogsDrawerEl.open = true;
  await refreshBackendLogs();
  diagnosticLogsDrawerEl?.scrollIntoView({ block: "nearest" });
}

async function initializeSubtitleWindow() {
  if (!Number.isInteger(tabId) || tabId <= 0) {
    renderBackendService({
      state: "error",
      label: "No source tab",
      detail: "Click the extension icon from the tab whose audio you want to translate",
      backend: "Not checked",
      models: "Not checked",
      error: "A browser tab was not selected for audio capture."
    });
    setStatus("Missing tab id. Reopen the extension from a video tab.", true);
    return;
  }

  if (autoStartRequested) {
    setActiveTab("subtitles");
    await startTranslation();
  } else {
    await refreshBackendStatus();
  }
  await Promise.all([refreshGlossary(), refreshPrivacy()]);
}

async function refreshBackendLogs(event) {
  if (event) event.preventDefault();
  try {
    const data = await BackendClient.fetchBackendLogs(180);
    if (frontendLogsEl) {
      frontendLogsEl.textContent = [
        `--- backend log: ${data.log_file || "unknown"} ---`,
        ...(data.lines || [])
      ].join("\n");
    }
    logClient("info", "backend_logs_refreshed", { lines: (data.lines || []).length });
  } catch (err) {
    logClient("error", "backend_logs_refresh_failed", { error: err?.message || String(err) });
  }
}

async function startCapture() {
  logClient("info", "start_capture_clicked", { tabId, mode: qualityModeEl.value });
  if (!tabId || Number.isNaN(tabId)) {
    setStatus("Missing tab id. Reopen the extension from a video tab.", true);
    return;
  }

  startBtn.disabled = true;
  stopBtn.disabled = false;
  if (pauseBtn) pauseBtn.disabled = false;
  transcriptionPaused = false;
  updatePauseButton();
  manualStopRequested = false;
  reconnectAttempts = 0;
  clearSubtitles();
  updateLatencyBadges();
  startedAt = Date.now();

  try {
    const connection = await reconcileBackendConnection();
    const websocketUrl = connection?.wsUrl || backendUrlEl.value.trim() || BackendClient.getWsUrl();
    const savedConnection = BackendClient.setWsUrl(websocketUrl, { source: "manual" });
    if (!savedConnection) throw new Error("Invalid WebSocket backend URL.");
    backendUrlEl.value = savedConnection.wsUrl;
    setStatus("Connecting to backend...");
    logClient("info", "connecting_websocket", { url: savedConnection.wsUrl });
    ws = await connectWebSocket(savedConnection.wsUrl);
    sendConfigIfOpen();

    setStatus("Requesting tab audio permission...");
    if (diagnosticTabAudioEl) diagnosticTabAudioEl.textContent = "Requesting access...";
    mediaStream = await captureTabAudio(tabId);
    if (diagnosticTabAudioEl) diagnosticTabAudioEl.textContent = "Granted";
    logClient("info", "tab_audio_capture_ready", { tracks: mediaStream.getTracks().length });

    setStatus("Starting audio pipeline...");
    await startAudioPipeline(mediaStream);

    setAudioStatus("Capturing tab audio");
    setStatus("Running");
    setActiveTab("subtitles");
    logClient("info", "capture_running", { sampleRate: TARGET_SAMPLE_RATE });
  } catch (err) {
    const message = err?.message || String(err);
    if (diagnosticTabAudioEl) diagnosticTabAudioEl.textContent = "Unavailable";
    logClient("error", "start_capture_failed", { error: message });
    await stopCapture({ graceful: false, updateStatus: false });
    throw new Error(message, { cause: err });
  }
}

async function stopCapture(options = {}) {
  if (stopPromise) return stopPromise;
  stopPromise = performStopCapture(options).finally(() => {
    stopPromise = null;
  });
  return stopPromise;
}

async function performStopCapture({ graceful = true, updateStatus = true } = {}) {
  logClient("info", "stop_capture_requested");
  manualStopRequested = true;
  reconnecting = false;
  clearTimeout(reconnectTimer);
  reconnectTimer = null;
  startBtn.disabled = false;
  stopBtn.disabled = true;
  if (pauseBtn) pauseBtn.disabled = true;
  transcriptionPaused = false;
  updatePauseButton();
  setAudioStatus("Not capturing");
  updateInputLevel(0);

  const socket = ws;
  const context = audioContext;
  safeCleanup(() => {
    if (workletNode) workletNode.port.onmessage = null;
  });
  safeCleanup(() => workletNode?.disconnect());
  safeCleanup(() => mediaSource?.disconnect());
  safeCleanup(() => monitorGain?.disconnect());
  safeCleanup(() => mediaStream?.getTracks().forEach(track => track.stop()));
  if (inputLevelFrame !== null) cancelAnimationFrame(inputLevelFrame);
  inputLevelFrame = null;
  pendingInputLevel = 0;
  if (inputLevelBarEl) {
    inputLevelBarEl.style.width = "0%";
    inputLevelBarEl.style.backgroundColor = "var(--accent)";
  }

  if (graceful && socket?.readyState === WebSocket.OPEN) {
    const flushed = waitForBackendFlush(8000);
    safeCleanup(() => socket.send(JSON.stringify({ type: "flush" })));
    await flushed;
  }
  if (ws === socket) ws = null;
  safeCleanup(() => socket?.close());
  if (context && context.state !== "closed") {
    await context.close().catch(error => logClient("warn", "audio_context_close_failed", {
      error: error?.message || String(error)
    }));
  }

  ws = null;
  mediaStream = null;
  audioContext = null;
  mediaSource = null;
  workletNode = null;
  monitorGain = null;

  if (updateStatus) setStatus("Stopped");
  updateRetryStatus("Ready", "idle");
  logClient("info", "capture_stopped");
}

function safeCleanup(action) {
  try {
    action();
  } catch (error) {
    logClient("warn", "capture_cleanup_failed", { error: error?.message || String(error) });
  }
}

function waitForBackendFlush(timeoutMs) {
  return new Promise(resolve => {
    const timeout = setTimeout(() => {
      flushResolver = null;
      resolve(false);
    }, timeoutMs);
    flushResolver = () => {
      clearTimeout(timeout);
      flushResolver = null;
      resolve(true);
    };
  });
}

function togglePause() {
  if (!ws || ws.readyState !== WebSocket.OPEN || !workletNode) {
    setStatus("Start capture before pausing.", true);
    return;
  }
  transcriptionPaused = !transcriptionPaused;
  updatePauseButton();
  setAudioStatus(transcriptionPaused ? "Paused, WebSocket kept open" : "Capturing tab audio");
  setStatus(transcriptionPaused ? "Paused" : "Running");
}

function updatePauseButton() {
  if (!pauseBtn) return;
  pauseBtn.textContent = transcriptionPaused ? "Resume" : "Pause";
}

function reconnectBackend() {
  if (!ws || ws.readyState !== WebSocket.OPEN || !mediaStream) {
    setStatus("Start capture to connect.", true);
    return;
  }
  setStatus("Reconnecting to backend...");
  try {
    ws.close();
  } catch (err) {
    logClient("warn", "manual_reconnect_failed", { error: err?.message || String(err) });
    setStatus(err?.message || String(err), true);
  }
}

function sendConfigIfOpen() {
  localStorage.setItem("subtitleQualityMode", qualityModeEl.value);
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  logClient("info", "config_sent", { mode: qualityModeEl.value, targetLang: targetLangEl.value, contextPrompt: Boolean(contextPromptEl.value.trim()) });
  ws.send(JSON.stringify({
    type: "config",
    sample_rate: TARGET_SAMPLE_RATE,
    source_lang: "nl",
    target_lang: targetLangEl.value,
    mode: qualityModeEl.value,
    context_prompt: contextPromptEl.value.trim(),
    reconnect_count: reconnectAttempts
  }));
}

function connectWebSocket(url) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(url);
    socket.binaryType = "arraybuffer";
    let settled = false;

    const fail = error => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      safeCleanup(() => socket.close());
      reject(error);
    };

    const timeout = setTimeout(() => {
      fail(new Error("Backend connection timeout. Check the local subtitle service."));
    }, 5000);

    socket.onopen = () => {
      if (settled) {
        safeCleanup(() => socket.close());
        return;
      }
      settled = true;
      clearTimeout(timeout);
      logClient("info", "websocket_open", { url });
      updateRetryStatus("Connected", "good");
      resolve(socket);
    };

    socket.onerror = () => {
      logClient("error", "websocket_error", { url });
      updateRetryStatus("WebSocket error", "bad");
      fail(new Error("Could not connect to backend WebSocket."));
    };

    socket.onclose = () => {
      clearTimeout(timeout);
      logClient("warn", "websocket_closed");
      if (!settled) {
        settled = true;
        reject(new Error("Backend WebSocket closed before connecting."));
      }
      if (ws === socket) ws = null;
      if (!manualStopRequested) {
        setStatus("Backend connection closed", true);
        updateRetryStatus("Closed, retry pending", "warn");
        scheduleReconnect();
      }
    };

    socket.onmessage = event => {
      try {
        const payload = JSON.parse(event.data);
        handleBackendMessage(payload);
      } catch (err) {
        logClient("error", "websocket_invalid_json", { error: err?.message || String(err) });
      }
    };
  });
}

function handleBackendMessage(payload) {
  if (payload.type === "flushed") {
    flushResolver?.();
    return;
  }
  if (payload.type === "ready") {
    logClient("info", "backend_ready", { clientId: payload.client_id });
    setStatus("Backend ready");
    return;
  }

  if (payload.type === "config_ack") {
    logClient("info", "config_ack", payload.config || {});
    return;
  }

  if (payload.type === "error") {
    logClient("error", "backend_error_event", { code: payload.code, message: payload.message || "Backend error", debug: payload.debug });
    setStatus(payload.message || "Backend error", true);
    return;
  }

  if (payload.type === "config_error") {
    logClient("error", "backend_config_error", { message: payload.message, errors: payload.errors || [] });
    setStatus(payload.message || "Invalid subtitle config.", true);
    return;
  }

  if (payload.type === "partial") {
    if (payload.is_cleared) {
      stablePartialText = "";
      return;
    }
    if (payload.dutch) {
      const candidate = SubtitleRenderer.stabilizePartial(stablePartialText, payload.dutch);
      if (candidate !== stablePartialText) {
        stablePartialText = candidate;
        if (!currentSubtitleItem?.pending) {
          if (currentSubtitleItem) {
            moveCurrentToHistory(currentSubtitleItem);
            currentSubtitleItem = null;
          }
          renderPartialSubtitle(stablePartialText, payload.latency_ms);
        }
        updateQualityBadge(payload.quality, true);
      }
      if (typeof payload.latency_ms === "number") {
          updateLatencyBadges({ asr: payload.latency_ms });
      }
    }
    return;
  }

  if (payload.type === "final_pending") {
    if (!payload.dutch) return;
    stablePartialText = "";
    showFinalPending(payload);
    updateQualityBadge(payload.quality, false);
    if (typeof payload.latency_ms === "number") {
      updateLatencyBadges({ asr: payload.latency_ms });
    }
    return;
  }

  if (payload.type === "final") {
    if (!payload.dutch) return;
    stablePartialText = "";
    updateFinalTranslation(payload);
    updateQualityBadge(payload.quality, false);
    if (typeof payload.latency_ms === "number") {
      const asr = typeof payload.asr_latency_ms === "number" ? payload.asr_latency_ms : 0;
      const mt = typeof payload.translation_latency_ms === "number" ? payload.translation_latency_ms : 0;
      updateLatencyBadges({ total: payload.latency_ms, asr, translation: mt });
    }
  }
}

function captureTabAudio(targetTabId) {
  return new Promise((resolve, reject) => {
    chrome.tabCapture.getMediaStreamId({ targetTabId }, async streamId => {
      const lastError = chrome.runtime.lastError;
      if (lastError) {
        logClient("error", "tab_capture_stream_id_failed", { error: lastError.message });
        reject(new Error(lastError.message));
        return;
      }
      if (!streamId) {
        logClient("error", "tab_capture_stream_id_missing");
        reject(new Error("Could not obtain tab audio stream id."));
        return;
      }

      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            mandatory: {
              chromeMediaSource: "tab",
              chromeMediaSourceId: streamId
            }
          },
          video: false
        });
        resolve(stream);
      } catch (err) {
        logClient("error", "get_user_media_tab_audio_failed", { error: err?.message || String(err) });
        reject(err);
      }
    });
  });
}

async function startAudioPipeline(stream) {
  audioContext = new AudioContext({ latencyHint: "interactive" });
  logClient("info", "audio_context_created", { sampleRate: audioContext.sampleRate });
  await audioContext.audioWorklet.addModule("worklet.js");

  mediaSource = audioContext.createMediaStreamSource(stream);
  workletNode = new AudioWorkletNode(audioContext, "pcm-worklet");
  workletNode.port.postMessage({ type: "config", targetSampleRate: TARGET_SAMPLE_RATE });

  monitorGain = audioContext.createGain();
  updateMonitorGain();

  mediaSource.connect(workletNode);
  mediaSource.connect(monitorGain);
  monitorGain.connect(audioContext.destination);

  workletNode.port.onmessage = event => {
    const pcm16 = event.data?.pcm || event.data;
    updateInputLevel(Number(event.data?.level) || 0);
    if (transcriptionPaused || !ws || ws.readyState !== WebSocket.OPEN) return;
    if (ws.bufferedAmount > WS_BUFFER_DROP_BYTES) {
      droppedAudioChunks += 1;
      maybeReportBackpressure("drop", ws.bufferedAmount);
      return;
    }
    if (ws.bufferedAmount > WS_BUFFER_WARN_BYTES) {
      maybeReportBackpressure("warn", ws.bufferedAmount);
    }
    if (pcm16?.byteLength > 0) ws.send(pcm16);
  };
}

function updateInputLevel(level) {
  if (!inputLevelBarEl) return;
  pendingInputLevel = level;
  if (inputLevelFrame !== null) return;
  inputLevelFrame = requestAnimationFrame(() => {
    inputLevelFrame = null;
    const pct = Math.round(Math.max(0, Math.min(1, pendingInputLevel)) * 100);
    inputLevelBarEl.style.width = `${pct}%`;
    inputLevelBarEl.style.backgroundColor = pct > 82 ? "var(--warning)" : "var(--accent)";
  });
}

function updateMonitorGain() {
  const volume = Number(monitorVolumeEl.value);
  const effective = muteMonitorEl.checked ? 0 : Math.max(0, Math.min(1, Number.isFinite(volume) ? volume : 1));
  if (monitorGain) monitorGain.gain.value = effective;
}

function maybeReportBackpressure(action, bufferedAmount) {
  const now = Date.now();
  if (now - lastBackpressureWarningAt < 1500) return;
  lastBackpressureWarningAt = now;
  const bufferedKb = Math.round(bufferedAmount / 1024);
  setAudioStatus(action === "drop"
    ? `Backend overloaded, dropping audio (${droppedAudioChunks})`
    : `Backend catching up (${bufferedKb} KB queued)`);
  logClient(action === "drop" ? "warn" : "info", "websocket_backpressure", {
    action,
    bufferedAmount,
    droppedAudioChunks
  });
}

function renderPartialSubtitle(dutch) {
  currentSubtitleEl.classList.remove("empty");
  currentSubtitleEl.classList.add("partial");
  currentSubtitleEl.innerHTML = "";

  const dutchCell = document.createElement("div");
  dutchCell.className = "subtitle-cell dutch";
  dutchCell.textContent = dutch;

  const translationCell = document.createElement("div");
  translationCell.className = "subtitle-cell translation pending";
  translationCell.textContent = currentSubtitleItem?.translation || "Translation pending...";

  currentSubtitleEl.appendChild(dutchCell);
  currentSubtitleEl.appendChild(translationCell);
  if (subtitleFeedEl) subtitleFeedEl.scrollTop = 0;
}

function showFinalPending(payload) {
  const elapsed = startedAt ? Math.round((Date.now() - startedAt) / 1000) : 0;
  const transcriptItem = recordPendingTranscript(payload, elapsed);
  const displayItem = {
    id: payload.id || `local-${Date.now()}`,
    dutch: payload.dutch,
    translation: "Translating...",
    pending: true,
    startMs: transcriptItem.startMs,
    endMs: transcriptItem.endMs,
    quality: payload.quality
  };
  if (currentSubtitleItem?.pending) {
    pendingSubtitleQueue.push(displayItem);
    return;
  }

  if (currentSubtitleItem) {
    moveCurrentToHistory(currentSubtitleItem);
    currentSubtitleItem = null;
  }

  if (pendingSubtitleQueue.length) {
    pendingSubtitleQueue.push(displayItem);
    advancePendingSubtitleQueue();
    return;
  }

  currentSubtitleItem = displayItem;
  renderCurrentSubtitle(currentSubtitleItem);
}

function updateFinalTranslation(payload) {
  const id = payload.id;
  const translation = payload.translation || "Translation unavailable";
  const elapsed = startedAt ? Math.round((Date.now() - startedAt) / 1000) : 0;
  recordFinalTranscript(payload, elapsed);

  if (currentSubtitleItem && (!id || currentSubtitleItem.id === id)) {
    currentSubtitleItem = {
      ...currentSubtitleItem,
      dutch: payload.dutch || currentSubtitleItem.dutch,
      translation,
      pending: false,
      quality: payload.quality || currentSubtitleItem.quality
    };
    renderCurrentSubtitle(currentSubtitleItem);
    if (pendingSubtitleQueue.length) {
      const completedItem = currentSubtitleItem;
      currentSubtitleItem = null;
      moveCurrentToHistory(completedItem);
      advancePendingSubtitleQueue();
    }
    return;
  }

  const queuedItem = id ? pendingSubtitleQueue.find(item => item.id === id) : null;
  if (queuedItem) {
    queuedItem.dutch = payload.dutch || queuedItem.dutch;
    queuedItem.translation = translation;
    queuedItem.pending = false;
    queuedItem.quality = payload.quality || queuedItem.quality;
    recordFinalTranscript({ ...payload, translation }, elapsed);
    if (!currentSubtitleItem) advancePendingSubtitleQueue();
    return;
  }

  // Fallback for an out-of-order final where the pending message was never seen.
  const fallback = {
    id: id || `local-${Date.now()}`,
    dutch: payload.dutch || "",
    translation,
    pending: false,
    startMs: startedAt ? Date.now() - startedAt : elapsed * 1000,
    endMs: (startedAt ? Date.now() - startedAt : elapsed * 1000) + 3500,
    quality: payload.quality
  };
  pendingSubtitleQueue.push(fallback);
  advancePendingSubtitleQueue();
}

function moveCurrentToHistory(item = currentSubtitleItem) {
  if (!item || item.pending) return;
  const existingRow = item.id
    ? historySubtitlesEl.querySelector(`[data-subtitle-id="${CSS.escape(item.id)}"]`)
    : null;
  const historyRow = createHistoryRow(item);
  if (existingRow) existingRow.replaceWith(historyRow);
  else historySubtitlesEl.prepend(historyRow);
  while (historySubtitlesEl.childElementCount > MAX_RENDERED_SUBTITLES) {
    historySubtitlesEl.lastElementChild?.remove();
  }
  if (subtitleFeedEl) subtitleFeedEl.scrollTop = 0;
}

function clearCurrentSubtitle() {
  delete currentSubtitleEl.dataset.subtitleId;
  currentSubtitleEl.className = "current-subtitle empty";
  currentSubtitleEl.innerHTML = '<div class="placeholder">Waiting for Dutch speech...</div>';
}

function advancePendingSubtitleQueue() {
  if (currentSubtitleItem) return;
  while (pendingSubtitleQueue.length) {
    const next = pendingSubtitleQueue.shift();
    if (!next) continue;
    currentSubtitleItem = next;
    renderCurrentSubtitle(next);
    return;
  }
  clearCurrentSubtitle();
}

function renderCurrentSubtitle(item) {
  currentSubtitleEl.classList.remove("empty", "partial");
  currentSubtitleEl.innerHTML = "";
  if (item.id) currentSubtitleEl.dataset.subtitleId = item.id;
  currentSubtitleEl.appendChild(SubtitleUI.createSubtitleCard(item));
  if (subtitleFeedEl) subtitleFeedEl.scrollTop = 0;
}

function createHistoryRow(item) {
  const row = document.createElement("article");
  row.className = "subtitle-row";
  row.tabIndex = -1;
  if (item.id) row.dataset.subtitleId = item.id;
  row.appendChild(SubtitleUI.createSubtitleCard(item));
  return row;
}

function clearSubtitles() {
  currentSubtitleItem = null;
  pendingSubtitleQueue = [];
  stablePartialText = "";
  transcriptItems = [];
  transcriptById.clear();
  clearCurrentSubtitle();
  updateQualityBadge(null, false);
  historySubtitlesEl.innerHTML = "";
}

function updateFontSizes(shouldPersist) {
  let dutchSize = clampFontSize(Number(dutchFontSizeEl.value), 14, 110, 48);
  let translationSize = clampFontSize(Number(translationFontSizeEl.value), 14, 110, 42);

  if (!shouldPersist) {
    const savedDutchSize = Number(localStorage.getItem("dutchSubtitleFontSize"));
    const savedTranslationSize = Number(localStorage.getItem("translationSubtitleFontSize"));
    if (savedDutchSize) dutchSize = clampFontSize(savedDutchSize, 14, 110, 48);
    if (savedTranslationSize) translationSize = clampFontSize(savedTranslationSize, 14, 110, 42);
  }

  dutchFontSizeEl.value = String(dutchSize);
  translationFontSizeEl.value = String(translationSize);

  document.documentElement.style.setProperty("--dutch-font-size", `${dutchSize}px`);
  document.documentElement.style.setProperty("--translation-font-size", `${translationSize}px`);
  document.documentElement.style.setProperty("--history-dutch-font-size", `${Math.max(14, Math.round(dutchSize * 0.45))}px`);
  document.documentElement.style.setProperty("--history-translation-font-size", `${Math.max(14, Math.round(translationSize * 0.45))}px`);

  if (shouldPersist) {
    localStorage.setItem("dutchSubtitleFontSize", String(dutchSize));
    localStorage.setItem("translationSubtitleFontSize", String(translationSize));
  }
}


function setActiveTab(tabName, focus = false) {
  const settingsActive = tabName === "settings";
  const tabState = [
    [subtitlesTabEl, !settingsActive],
    [settingsTabEl, settingsActive]
  ];
  tabState.forEach(([tab, active]) => {
    if (!tab) return;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  });
  if (subtitlesPanelEl) subtitlesPanelEl.hidden = settingsActive;
  if (settingsPanelEl) settingsPanelEl.hidden = !settingsActive;
  localStorage.setItem("subtitleActiveTab", settingsActive ? "settings" : "subtitles");
  if (focus) (settingsActive ? settingsTabEl : subtitlesTabEl)?.focus();
}

function loadTabPreference() {
  const saved = localStorage.getItem("subtitleActiveTab");
  setActiveTab(saved === "settings" ? "settings" : "subtitles");
}

function handleTabKeydown(event) {
  const tabs = [subtitlesTabEl, settingsTabEl].filter(Boolean);
  const currentIndex = tabs.indexOf(event.currentTarget);
  if (currentIndex < 0) return;
  let nextIndex = currentIndex;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex = (currentIndex + 1) % tabs.length;
  if (event.key === "ArrowLeft" || event.key === "ArrowUp") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
  if (event.key === "Home") nextIndex = 0;
  if (event.key === "End") nextIndex = tabs.length - 1;
  if (nextIndex === currentIndex) return;
  event.preventDefault();
  setActiveTab(tabs[nextIndex] === settingsTabEl ? "settings" : "subtitles", true);
}

function loadModePreference() {
  qualityModeEl.value = Settings.getMode(localStorage);
}

function loadDevicePreference() {
  const device = Settings.getDevice(localStorage);
  asrDeviceEls.forEach((input) => {
    input.checked = input.value === device;
  });
}

function loadDisplayModePreference() {
  displayModeEl.value = Settings.getDisplayMode(localStorage);
  applyDisplayMode(displayModeEl.value, false);
}

function loadContextPreference() {
  contextPromptEl.value = Settings.getContextPrompt(localStorage);
}

function loadMonitorPreference() {
  const monitor = Settings.getMonitor(localStorage);
  monitorVolumeEl.value = monitor.volume;
  muteMonitorEl.checked = monitor.muted;
}

function loadHistoryPreference() {
  if (retainTranscriptEl) retainTranscriptEl.checked = localStorage.getItem("subtitleRetainTranscript") !== "0";
  if (autoDeleteMinutesEl) autoDeleteMinutesEl.value = localStorage.getItem("subtitleAutoDeleteMinutes") || "0";
  scheduleAutoDelete();
}

function loadSessionPreference() {
  const savedName = localStorage.getItem("subtitleCurrentSessionName");
  if (sessionNameEl) sessionNameEl.value = savedName || "Untitled session";
  renderSessionSelect();
}

function clampFontSize(value, min, max, fallback) {
  if (!Number.isFinite(value)) return fallback;
  return Math.max(min, Math.min(max, Math.round(value)));
}

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.className = isError ? "error" : "";
  if (isError) updateConnectionBadge("Error", "bad");
  else if (message === "Running" || message === "Backend ready" || message === "Reconnected") updateConnectionBadge("Connected", "good");
  else if (message === "Stopped" || message === "Idle") updateConnectionBadge("Idle", "idle");
  else if (message.toLowerCase().includes("connect") || message.toLowerCase().includes("backend")) updateConnectionBadge(message, "warn");
}

function applyDisplayMode(mode, shouldPersist) {
  const safeMode = ["current", "two-line", "history", "compact"].includes(mode) ? mode : "current";
  document.body.classList.remove("display-current", "display-two-line", "display-history", "display-compact");
  document.body.classList.add(`display-${safeMode}`);
  if (shouldPersist) localStorage.setItem("subtitleDisplayMode", safeMode);
}

function renderHistoryFromTranscript() {
  historySubtitlesEl.innerHTML = "";
  transcriptItems
    .filter(item => (item.dutch || item.translation) && !item.pending)
    .slice(-MAX_RENDERED_SUBTITLES)
    .reverse()
    .forEach(item => historySubtitlesEl.appendChild(createHistoryRow(item)));
}

function sessionStore() {
  try {
    return JSON.parse(localStorage.getItem("subtitleSavedSessions") || "{}");
  } catch (_err) {
    return {};
  }
}

function writeSessionStore(store) {
  localStorage.setItem("subtitleSavedSessions", JSON.stringify(store));
}

function currentSessionSnapshot() {
  return {
    id: currentSessionId,
    name: sessionNameEl?.value.trim() || "Untitled session",
    savedAt: new Date().toISOString(),
    transcriptItems
  };
}

function renderSessionSelect() {
  if (!sessionSelectEl) return;
  const store = sessionStore();
  sessionSelectEl.innerHTML = "";
  Object.values(store)
    .sort((a, b) => String(b.savedAt).localeCompare(String(a.savedAt)))
    .forEach(session => {
      const option = document.createElement("option");
      option.value = session.id;
      option.textContent = `${session.name || "Untitled session"} (${new Date(session.savedAt).toLocaleString()})`;
      sessionSelectEl.appendChild(option);
    });
}

function saveCurrentSession() {
  const store = sessionStore();
  const snapshot = currentSessionSnapshot();
  store[snapshot.id] = snapshot;
  writeSessionStore(store);
  renderSessionSelect();
  if (sessionSelectEl) sessionSelectEl.value = snapshot.id;
  setStatus("Session saved locally");
}

function newSession() {
  currentSessionId = `session-${Date.now()}`;
  if (sessionNameEl) sessionNameEl.value = "Untitled session";
  clearSessionTranscript();
  setStatus("New local session");
}

function renameSession() {
  localStorage.setItem("subtitleCurrentSessionName", sessionNameEl?.value.trim() || "Untitled session");
  saveCurrentSession();
  setStatus("Session renamed locally");
}

function restoreSelectedSession() {
  if (!sessionSelectEl?.value) {
    setStatus("No saved session selected.", true);
    return;
  }
  const session = sessionStore()[sessionSelectEl.value];
  if (!session) {
    setStatus("Saved session not found.", true);
    return;
  }
  currentSessionId = session.id;
  if (sessionNameEl) sessionNameEl.value = session.name || "Untitled session";
  transcriptItems = Array.isArray(session.transcriptItems) ? session.transcriptItems : [];
  rebuildTranscriptIndex();
  currentSubtitleItem = null;
  currentSubtitleEl.className = "current-subtitle empty";
  currentSubtitleEl.innerHTML = '<div class="placeholder">Session restored. Start capture for live subtitles.</div>';
  renderHistoryFromTranscript();
  setStatus("Session restored locally");
}

function clearSessionTranscript() {
  clearSubtitles();
  transcriptItems = [];
  transcriptById.clear();
  renderHistoryFromTranscript();
  setStatus("Local transcript cleared");
}

function updateRetentionPreference() {
  if (retainTranscriptEl) localStorage.setItem("subtitleRetainTranscript", retainTranscriptEl.checked ? "1" : "0");
  if (autoDeleteMinutesEl) localStorage.setItem("subtitleAutoDeleteMinutes", autoDeleteMinutesEl.value || "0");
  if (retainTranscriptEl && !retainTranscriptEl.checked) clearSessionTranscript();
  scheduleAutoDelete();
}

function scheduleAutoDelete() {
  if (autoDeleteTimer) clearTimeout(autoDeleteTimer);
  const minutes = Number(autoDeleteMinutesEl?.value || "0");
  if (!Number.isFinite(minutes) || minutes <= 0) return;
  autoDeleteTimer = setTimeout(() => {
    clearSessionTranscript();
    setStatus("Local transcript auto-deleted");
  }, minutes * 60 * 1000);
}

async function refreshPrivacy() {
  try {
    const data = await BackendClient.fetchPrivacy();
    transcriptLoggingEl.checked = Boolean(data.log_transcript_text);
    updatePrivacyLabel(data.log_transcript_text);
  } catch (err) {
    updatePrivacyLabel(false);
    logClient("warn", "privacy_status_failed", { error: err?.message || String(err) });
  }
}

async function updatePrivacy() {
  try {
    const data = await BackendClient.savePrivacy(transcriptLoggingEl.checked);
    transcriptLoggingEl.checked = Boolean(data.log_transcript_text);
    updatePrivacyLabel(data.log_transcript_text);
  } catch (err) {
    logClient("error", "privacy_update_failed", { error: err?.message || String(err) });
    setStatus("Could not update privacy setting.", true);
    await refreshPrivacy();
  }
}

function updatePrivacyLabel(logTranscriptText) {
  privacyStatusEl.textContent = logTranscriptText ? "Logs text" : "Logs hidden";
  privacyStatusEl.className = logTranscriptText ? "privacy-on" : "";
  SubtitleUI.setBadgeState(privacyStatusEl.closest(".status-badge"), logTranscriptText ? "warn" : "good");
}

function updateQualityBadge(quality, isPartial) {
  const level = quality?.level || "waiting";
  qualityBadgeEl.className = `quality-badge ${level === "waiting" ? "good" : level}`;
  qualityBadgeEl.textContent = qualityLabel(quality, isPartial);
  qualityBadgeEl.title = quality?.reasons?.length ? quality.reasons.join(", ") : "";
}

function qualityLabel(quality, isPartial) {
  if (!quality) return "Quality: waiting";
  const prefix = isPartial ? "Partial" : "Quality";
  if (quality.level === "low") return `${prefix}: check`;
  if (quality.level === "watch") return `${prefix}: watch`;
  if (quality.level === "empty") return `${prefix}: no speech`;
  return `${prefix}: good`;
}

function recordPendingTranscript(payload, elapsedSeconds) {
  const id = payload.id || `local-${Date.now()}`;
  const startedMs = startedAt ? Date.now() - startedAt : elapsedSeconds * 1000;
  closePreviousTranscript(startedMs);
  const item = {
    id,
    startMs: startedMs,
    endMs: startedMs + 3500,
    dutch: payload.dutch || "",
    translation: "",
    pending: true,
    mode: payload.mode || qualityModeEl.value
    ,quality: payload.quality
  };
  transcriptItems.push(item);
  transcriptById.set(id, item);
  return item;
}

function recordFinalTranscript(payload, elapsedSeconds) {
  const id = payload.id;
  const item = id ? transcriptById.get(id) : null;
  if (item) {
    item.dutch = payload.dutch || item.dutch;
    item.translation = payload.translation || item.translation;
    item.pending = false;
    item.mode = payload.mode || item.mode;
    item.quality = payload.quality || item.quality;
    return;
  }
  const startedMs = startedAt ? Date.now() - startedAt : elapsedSeconds * 1000;
  const newItem = {
    id: id || `local-${Date.now()}`,
    startMs: startedMs,
    endMs: startedMs + 3500,
    dutch: payload.dutch || "",
    translation: payload.translation || "",
    pending: false,
    mode: payload.mode || qualityModeEl.value,
    quality: payload.quality
  };
  transcriptItems.push(newItem);
  transcriptById.set(newItem.id, newItem);
}

function rebuildTranscriptIndex() {
  transcriptById = new Map(transcriptItems.filter(item => item?.id).map(item => [item.id, item]));
}

function closePreviousTranscript(nextStartMs) {
  const previous = transcriptItems[transcriptItems.length - 1];
  if (previous) previous.endMs = Math.max(previous.startMs + 800, nextStartMs - 120);
}

function exportTranscript(format) {
  const rows = transcriptItems.filter(item => item.dutch || item.translation);
  if (!rows.length) {
    setStatus("No subtitles to export.", true);
    return;
  }
  const content = format === "srt"
    ? SubtitleRenderer.toSrt(rows)
    : format === "vtt"
      ? SubtitleRenderer.toVtt(rows)
      : SubtitleRenderer.toTxt(rows);
  const mime = format === "txt" ? "text/plain" : "text/vtt";
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `dutch-subtitles-${new Date().toISOString().replace(/[:.]/g, "-")}.${format}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  setStatus(`Exported ${format.toUpperCase()}`);
}

function scheduleReconnect() {
  if (manualStopRequested || !mediaStream || !workletNode || reconnecting || reconnectTimer !== null) return;
  reconnectAttempts += 1;
  if (reconnectAttempts > 20) {
    setStatus("Reconnect failed. Restart capture.", true);
    updateRetryStatus("Failed after 20 tries", "bad");
    return;
  }
  setStatus(`Reconnecting to backend... ${reconnectAttempts}`, true);
  updateRetryStatus(`Retry ${reconnectAttempts}/20`, "warn");
  reconnectTimer = setTimeout(async () => {
    reconnectTimer = null;
    if (manualStopRequested || !mediaStream || !workletNode) return;
    reconnecting = true;
    try {
      const connection = await reconcileBackendConnection();
      const websocketUrl = connection?.wsUrl || backendUrlEl.value.trim() || BackendClient.getWsUrl();
      const savedConnection = BackendClient.setWsUrl(websocketUrl, { source: "reconnect" });
      if (!savedConnection) throw new Error("Saved backend WebSocket URL is invalid.");
      backendUrlEl.value = savedConnection.wsUrl;
      const socket = await connectWebSocket(savedConnection.wsUrl);
      if (manualStopRequested || !mediaStream || !workletNode) {
        socket.close();
        return;
      }
      ws = socket;
      sendConfigIfOpen();
      setStatus("Reconnected");
      updateRetryStatus("Reconnected", "good");
      reconnectAttempts = 0;
      logClient("info", "websocket_reconnected");
    } catch (err) {
      logClient("warn", "websocket_reconnect_failed", { error: err?.message || String(err), attempt: reconnectAttempts });
      updateRetryStatus(`Retry failed: ${err?.message || "connection error"}`, "bad");
    } finally {
      reconnecting = false;
      if (!ws || ws.readyState !== WebSocket.OPEN) scheduleReconnect();
    }
  }, Math.min(1000 + reconnectAttempts * 500, 5000));
}

async function reconcileBackendConnection() {
  if (connectionRecoveryPromise) return connectionRecoveryPromise;
  connectionRecoveryPromise = BackendClient.findHealthyConnection().then(connection => {
    if (!connection) return null;
    backendUrlEl.value = connection.wsUrl;
    if (connection.recovered) {
      logClient("info", "backend_connection_recovered", {
        baseUrl: connection.baseUrl,
        source: connection.source,
        metadata: BackendClient.getConnectionMetadata()
      });
    }
    return connection;
  }).finally(() => {
    connectionRecoveryPromise = null;
  });
  return connectionRecoveryPromise;
}

async function refreshGlossary() {
  if (!glossaryListEl) return;
  try {
    const data = await BackendClient.fetchGlossary();
    glossaryRules = data.rules || [];
    renderGlossary();
  } catch (err) {
    logClient("warn", "glossary_load_failed", { error: err?.message || String(err) });
  }
}

function openGlossaryDialog() {
  if (!glossaryDialogEl) return;
  if (typeof glossaryDialogEl.showModal === "function") {
    glossaryDialogEl.showModal();
  } else {
    glossaryDialogEl.setAttribute("open", "");
  }
  if (glossarySearchEl) glossarySearchEl.focus();
}

async function addGlossaryRule() {
  const pattern = glossaryPatternEl.value.trim();
  const replacement = glossaryReplacementEl.value.trim();
  if (!pattern) return;
  glossaryRules.push({ pattern, replacement });
  glossaryPatternEl.value = "";
  glossaryReplacementEl.value = "";
  await saveGlossary();
}

async function removeGlossaryRule(index) {
  glossaryRules.splice(index, 1);
  await saveGlossary();
}

async function saveGlossary() {
  try {
    const data = await BackendClient.saveGlossaryRules(glossaryRules);
    glossaryRules = data.rules || [];
    renderGlossary();
    setStatus("Glossary updated");
  } catch (err) {
    logClient("error", "glossary_save_failed", { error: err?.message || String(err) });
    setStatus(err?.message || String(err), true);
    await refreshGlossary();
  }
}

function renderGlossary() {
  glossaryListEl.innerHTML = "";
  const query = (glossarySearchEl?.value || "").trim().toLowerCase();
  const visibleRules = query
    ? glossaryRules
        .map((rule, index) => ({ rule, index }))
        .filter(({ rule }) => `${rule.pattern} ${rule.replacement}`.toLowerCase().includes(query))
    : glossaryRules.map((rule, index) => ({ rule, index }));

  if (!glossaryRules.length) {
    glossaryListEl.textContent = "No glossary rules.";
    return;
  }
  if (!visibleRules.length) {
    glossaryListEl.textContent = "No matching glossary rules.";
    return;
  }
  visibleRules.forEach(({ rule, index }) => {
    const row = document.createElement("div");
    row.className = "glossary-row";
    const pattern = document.createElement("span");
    pattern.textContent = rule.pattern;
    const replacement = document.createElement("span");
    replacement.textContent = rule.replacement;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Remove";
    button.addEventListener("click", () => removeGlossaryRule(index));
    row.appendChild(pattern);
    row.appendChild(replacement);
    row.appendChild(button);
    glossaryListEl.appendChild(row);
  });
}

function updateLatencyBadges(values = {}) {
  if (latencyEl) latencyEl.textContent = typeof values.total === "number" ? `${values.total} ms` : "-";
  if (asrLatencyEl) asrLatencyEl.textContent = typeof values.asr === "number" ? `${values.asr} ms` : "-";
  if (translationLatencyEl) translationLatencyEl.textContent = typeof values.translation === "number" ? `${values.translation} ms` : "-";
}

function updateRetryStatus(message, state) {
  if (!retryStatusEl) return;
  retryStatusEl.textContent = message;
  SubtitleUI.setBadgeState(retryStatusEl.closest(".status-badge"), state);
}

function setAudioStatus(message) {
  audioStatusEl.textContent = message;
  const isCapturing = message.toLowerCase().includes("capturing");
  if (capturingBadgeEl) {
    capturingBadgeEl.textContent = isCapturing ? "Yes" : message === "Not capturing" ? "No" : message;
    SubtitleUI.setBadgeState(capturingBadgeEl.closest(".status-badge"), isCapturing ? "good" : message === "Not capturing" ? "idle" : "warn");
  }
}

function updateConnectionBadge(message, state) {
  if (!connectedBadgeEl) return;
  connectedBadgeEl.textContent = message;
  SubtitleUI.setBadgeState(connectedBadgeEl.closest(".status-badge"), state);
}

function handleShortcut(event) {
  const tag = event.target?.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || event.altKey || event.ctrlKey || event.metaKey) return;
  if (event.key === " ") {
    event.preventDefault();
    if (ws && ws.readyState === WebSocket.OPEN) stopCapture();
    else startTranslation();
  } else if (event.key.toLowerCase() === "p") {
    event.preventDefault();
    togglePause();
  } else if (event.key.toLowerCase() === "f") {
    event.preventDefault();
    cycleFontSize();
  } else if (event.key.toLowerCase() === "m") {
    event.preventDefault();
    muteMonitorEl.checked = !muteMonitorEl.checked;
    localStorage.setItem("subtitleMonitorMuted", muteMonitorEl.checked ? "1" : "0");
    updateMonitorGain();
  } else if (event.key.toLowerCase() === "e") {
    event.preventDefault();
    exportTranscript("txt");
  } else if (event.key.toLowerCase() === "g") {
    event.preventDefault();
    openGlossaryDialog();
  }
}

function cycleFontSize() {
  const sizes = [32, 42, 54, 68, 84];
  const current = Number(dutchFontSizeEl.value);
  const next = sizes.find(size => size > current) || sizes[0];
  dutchFontSizeEl.value = String(next);
  translationFontSizeEl.value = String(Math.max(24, Math.round(next * 0.85)));
  updateFontSizes(true);
}
