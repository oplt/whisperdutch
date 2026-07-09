const TARGET_SAMPLE_RATE = 16000;
const CLIENT_LOG_ENDPOINT = BackendClient.ENDPOINTS.clientLogs;
const BACKEND_LOGS_ENDPOINT = BackendClient.ENDPOINTS.backendLogs;
const GLOSSARY_ENDPOINT = BackendClient.ENDPOINTS.glossary;
const WS_BUFFER_WARN_BYTES = 512 * 1024;
const WS_BUFFER_DROP_BYTES = 2 * 1024 * 1024;

const params = new URLSearchParams(location.search);
const tabId = Number(params.get("tabId"));

const backendUrlEl = document.getElementById("backendUrl");
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
const exportTxtBtn = document.getElementById("exportTxtBtn");
const exportVttBtn = document.getElementById("exportVttBtn");
const exportSrtBtn = document.getElementById("exportSrtBtn");
const statusEl = document.getElementById("status");
const audioStatusEl = document.getElementById("audioStatus");
const latencyEl = document.getElementById("latency");
const privacyStatusEl = document.getElementById("privacyStatus");
const qualityBadgeEl = document.getElementById("qualityBadge");
const currentSubtitleEl = document.getElementById("currentSubtitle");
const historySubtitlesEl = document.getElementById("historySubtitles");
const historyDrawerEl = document.getElementById("historyDrawer");
const historyCountEl = document.getElementById("historyCount");
const topPanelEl = document.getElementById("topPanel");
const topPanelActionEl = document.getElementById("topPanelAction");
const frontendLogsEl = document.getElementById("frontendLogs");
const refreshLogsBtn = document.getElementById("refreshLogsBtn");
const glossaryPatternEl = document.getElementById("glossaryPattern");
const glossaryReplacementEl = document.getElementById("glossaryReplacement");
const addGlossaryBtn = document.getElementById("addGlossaryBtn");
const glossaryListEl = document.getElementById("glossaryList");

let ws = null;
let mediaStream = null;
let audioContext = null;
let mediaSource = null;
let workletNode = null;
let monitorGain = null;
let startedAt = null;
let currentSubtitleItem = null;
let historyCount = 0;
let stablePartialText = "";
let droppedAudioChunks = 0;
let lastBackpressureWarningAt = 0;
let transcriptItems = [];
let glossaryRules = [];
let manualStopRequested = false;
let reconnecting = false;
let reconnectAttempts = 0;
let configDebounceTimer = null;

startBtn.addEventListener("click", startCapture);
stopBtn.addEventListener("click", stopCapture);
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
exportTxtBtn.addEventListener("click", () => exportTranscript("txt"));
exportVttBtn.addEventListener("click", () => exportTranscript("vtt"));
exportSrtBtn.addEventListener("click", () => exportTranscript("srt"));
if (addGlossaryBtn) addGlossaryBtn.addEventListener("click", addGlossaryRule);
window.addEventListener("keydown", handleShortcut);
window.addEventListener("beforeunload", stopCapture);
if (historyDrawerEl) historyDrawerEl.addEventListener("toggle", updateHistorySummary);
if (topPanelEl) topPanelEl.addEventListener("toggle", updateTopPanelSummary);
if (refreshLogsBtn) refreshLogsBtn.addEventListener("click", refreshBackendLogs);

logClient("info", "subtitle_window_loaded", { tabId });
backendUrlEl.value = BackendClient.getWsUrl();
updateFontSizes(false);
loadModePreference();
loadDisplayModePreference();
loadContextPreference();
loadMonitorPreference();
loadTopPanelPreference();
updateHistorySummary();
updateTopPanelSummary(false);
refreshGlossary();
refreshPrivacy();

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
  manualStopRequested = false;
  reconnectAttempts = 0;
  clearSubtitles();
  latencyEl.textContent = "—";
  startedAt = Date.now();

  try {
    setStatus("Connecting to backend...");
    logClient("info", "connecting_websocket", { url: backendUrlEl.value.trim() });
    ws = await connectWebSocket(backendUrlEl.value.trim());
    sendConfigIfOpen();

    setStatus("Requesting tab audio permission...");
    mediaStream = await captureTabAudio(tabId);
    logClient("info", "tab_audio_capture_ready", { tracks: mediaStream.getTracks().length });

    setStatus("Starting audio pipeline...");
    await startAudioPipeline(mediaStream);

    audioStatusEl.textContent = "Capturing tab audio";
    setStatus("Running");
    logClient("info", "capture_running", { sampleRate: TARGET_SAMPLE_RATE });
  } catch (err) {
    logClient("error", "start_capture_failed", { error: err?.message || String(err) });
    setStatus(err?.message || String(err), true);
    await stopCapture();
  }
}

async function stopCapture() {
  logClient("info", "stop_capture_requested");
  manualStopRequested = true;
  reconnecting = false;
  startBtn.disabled = false;
  stopBtn.disabled = true;
  audioStatusEl.textContent = "Not capturing";

  try {
    if (workletNode) {
      workletNode.port.onmessage = null;
      workletNode.disconnect();
    }
    if (mediaSource) mediaSource.disconnect();
    if (monitorGain) monitorGain.disconnect();
    if (audioContext) await audioContext.close();
    if (mediaStream) mediaStream.getTracks().forEach(track => track.stop());
    if (ws && ws.readyState === WebSocket.OPEN) {
      try { ws.send(JSON.stringify({ type: "flush" })); } catch (_err) {}
      ws.close();
    }
  } catch (_err) {
    // Ignore shutdown errors.
  }

  ws = null;
  mediaStream = null;
  audioContext = null;
  mediaSource = null;
  workletNode = null;
  monitorGain = null;

  setStatus("Stopped");
  logClient("info", "capture_stopped");
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

    const timeout = setTimeout(() => {
      reject(new Error("Backend connection timeout. Is FastAPI running on 127.0.0.1:8000?"));
    }, 5000);

    socket.onopen = () => {
      clearTimeout(timeout);
      logClient("info", "websocket_open", { url });
      resolve(socket);
    };

    socket.onerror = () => {
      clearTimeout(timeout);
      logClient("error", "websocket_error", { url });
      reject(new Error("Could not connect to backend WebSocket."));
    };

    socket.onclose = () => {
      logClient("warn", "websocket_closed");
      if (ws === socket) setStatus("Backend connection closed", true);
      scheduleReconnect(socket);
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
      const candidate = stabilizePartial(stablePartialText, payload.dutch);
      if (candidate !== stablePartialText) {
        stablePartialText = candidate;
        renderPartialSubtitle(stablePartialText, payload.latency_ms);
        updateQualityBadge(payload.quality, true);
      }
      if (typeof payload.latency_ms === "number") {
        latencyEl.textContent = `${payload.latency_ms} ms partial ASR`;
      }
    }
    return;
  }

  if (payload.type === "final_pending") {
    if (!payload.dutch) return;
    logClient("info", "final_pending_received", { id: payload.id, asrLatencyMs: payload.asr_latency_ms, audioSeconds: payload.audio_seconds });
    stablePartialText = "";
    showFinalPending(payload);
    updateQualityBadge(payload.quality, false);
    if (typeof payload.latency_ms === "number") {
      latencyEl.textContent = `${payload.latency_ms} ms ASR · translating…`;
    }
    return;
  }

  if (payload.type === "final") {
    if (!payload.dutch) return;
    logClient("info", "final_translation_received", { id: payload.id, asrLatencyMs: payload.asr_latency_ms, translationLatencyMs: payload.translation_latency_ms, totalLatencyMs: payload.latency_ms });
    stablePartialText = "";
    updateFinalTranslation(payload);
    updateQualityBadge(payload.quality, false);
    if (typeof payload.latency_ms === "number") {
      const asr = typeof payload.asr_latency_ms === "number" ? payload.asr_latency_ms : 0;
      const mt = typeof payload.translation_latency_ms === "number" ? payload.translation_latency_ms : 0;
      latencyEl.textContent = `${payload.latency_ms} ms model time · ASR ${asr} ms · MT ${mt} ms`;
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
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (ws.bufferedAmount > WS_BUFFER_DROP_BYTES) {
      droppedAudioChunks += 1;
      maybeReportBackpressure("drop", ws.bufferedAmount);
      return;
    }
    if (ws.bufferedAmount > WS_BUFFER_WARN_BYTES) {
      maybeReportBackpressure("warn", ws.bufferedAmount);
    }
    const pcm16 = event.data;
    if (pcm16?.byteLength > 0) ws.send(pcm16);
  };
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
  audioStatusEl.textContent = action === "drop"
    ? `Backend overloaded, dropping audio (${droppedAudioChunks})`
    : `Backend catching up (${bufferedKb} KB queued)`;
  logClient(action === "drop" ? "warn" : "info", "websocket_backpressure", {
    action,
    bufferedAmount,
    droppedAudioChunks
  });
}

function renderPartialSubtitle(dutch, latencyMs) {
  currentSubtitleEl.classList.remove("empty");
  currentSubtitleEl.classList.add("partial");
  currentSubtitleEl.innerHTML = "";

  const dutchCell = document.createElement("div");
  dutchCell.className = "subtitle-cell dutch";
  dutchCell.textContent = dutch;

  const translationCell = document.createElement("div");
  translationCell.className = "subtitle-cell translation pending";
  translationCell.textContent = currentSubtitleItem?.translation || "translation appears after the sentence is final";

  const meta = document.createElement("div");
  meta.className = "row-meta";
  meta.textContent = latencyMs ? `live partial · ${latencyMs} ms` : "live partial";

  currentSubtitleEl.appendChild(dutchCell);
  currentSubtitleEl.appendChild(translationCell);
  currentSubtitleEl.appendChild(meta);
}

function showFinalPending(payload) {
  if (currentSubtitleItem) {
    moveCurrentToHistory();
  }

  const elapsed = startedAt ? Math.round((Date.now() - startedAt) / 1000) : 0;
  recordPendingTranscript(payload, elapsed);
  currentSubtitleItem = {
    id: payload.id || `local-${Date.now()}`,
    dutch: payload.dutch,
    translation: "Translating…",
    pending: true,
    meta: `+${elapsed}s · ${payload.asr_latency_ms || payload.latency_ms || 0} ms ASR · ${payload.mode || qualityModeEl.value}`,
    detail: payload.asr_fragment ? `ASR fragment: ${payload.asr_fragment}` : "",
    quality: payload.quality
  };
  renderCurrentSubtitle(currentSubtitleItem);
}

function updateFinalTranslation(payload) {
  const id = payload.id;
  const translation = payload.translation || "";
  const elapsed = startedAt ? Math.round((Date.now() - startedAt) / 1000) : 0;
  const meta = payload.latency_ms
    ? `+${elapsed}s · ${payload.latency_ms} ms · ${payload.mode || qualityModeEl.value}`
    : `+${elapsed}s`;
  recordFinalTranscript(payload, elapsed);

  if (currentSubtitleItem && (!id || currentSubtitleItem.id === id)) {
    currentSubtitleItem = {
      ...currentSubtitleItem,
      dutch: payload.dutch || currentSubtitleItem.dutch,
      translation: translation || currentSubtitleItem.translation,
      pending: false,
      meta,
      detail: payload.asr_fragment ? `ASR fragment: ${payload.asr_fragment}` : currentSubtitleItem.detail
      ,quality: payload.quality || currentSubtitleItem.quality
    };
    renderCurrentSubtitle(currentSubtitleItem);
    return;
  }

  const historyRow = id ? historySubtitlesEl.querySelector(`[data-subtitle-id="${CSS.escape(id)}"]`) : null;
  if (historyRow) {
    const translationCell = historyRow.querySelector(".subtitle-cell.translation");
    const metaCell = historyRow.querySelector(".row-meta");
    if (translationCell) {
      translationCell.textContent = translation || "Translation unavailable";
      translationCell.classList.remove("pending");
    }
    if (metaCell) metaCell.textContent = meta;
    return;
  }

  // Fallback for out-of-order events where the pending message was never seen.
  showFinalPending({ ...payload, translation: "" });
  if (currentSubtitleItem) {
    currentSubtitleItem.translation = translation || "Translation unavailable";
    currentSubtitleItem.pending = false;
    currentSubtitleItem.meta = meta;
    renderCurrentSubtitle(currentSubtitleItem);
  }
}

function moveCurrentToHistory() {
  if (!currentSubtitleItem) return;
  historySubtitlesEl.prepend(createHistoryRow(currentSubtitleItem));
  historySubtitlesEl.scrollTop = 0;
  historyCount += 1;
  updateHistorySummary();
}

function renderCurrentSubtitle(item) {
  currentSubtitleEl.classList.remove("empty", "partial");
  currentSubtitleEl.innerHTML = "";
  if (item.id) currentSubtitleEl.dataset.subtitleId = item.id;

  const dutchCell = document.createElement("div");
  dutchCell.className = "subtitle-cell dutch";
  dutchCell.textContent = item.dutch;

  const translationCell = document.createElement("div");
  translationCell.className = item.pending ? "subtitle-cell translation pending" : "subtitle-cell translation";
  translationCell.textContent = item.translation;

  const meta = document.createElement("div");
  meta.className = "row-meta";
  meta.textContent = item.meta;
  const quality = createQualityPill(item.quality);

  currentSubtitleEl.appendChild(dutchCell);
  currentSubtitleEl.appendChild(translationCell);
  if (quality) currentSubtitleEl.appendChild(quality);
  currentSubtitleEl.appendChild(meta);
}

function createHistoryRow(item) {
  const row = document.createElement("article");
  row.className = "subtitle-row";
  if (item.id) row.dataset.subtitleId = item.id;

  const dutchCell = document.createElement("div");
  dutchCell.className = "subtitle-cell dutch";
  dutchCell.textContent = item.dutch;

  const translationCell = document.createElement("div");
  translationCell.className = item.pending ? "subtitle-cell translation pending" : "subtitle-cell translation";
  translationCell.textContent = item.translation;

  const meta = document.createElement("div");
  meta.className = "row-meta";
  meta.textContent = item.meta;
  const quality = createQualityPill(item.quality);

  row.appendChild(dutchCell);
  row.appendChild(translationCell);
  if (quality) row.appendChild(quality);
  row.appendChild(meta);
  return row;
}

function clearSubtitles() {
  currentSubtitleItem = null;
  historyCount = 0;
  stablePartialText = "";
  transcriptItems = [];
  delete currentSubtitleEl.dataset.subtitleId;
  currentSubtitleEl.className = "current-subtitle empty";
  currentSubtitleEl.innerHTML = '<div class="placeholder">Waiting for Dutch speech…</div>';
  updateQualityBadge(null, false);
  historySubtitlesEl.innerHTML = "";
  if (historyDrawerEl) historyDrawerEl.open = false;
  updateHistorySummary();
}

function stabilizePartial(previous, next) {
  return SubtitleRenderer.stabilizePartial(previous, next);
}

function mergeByWordOverlap(left, right) {
  return SubtitleRenderer.mergeByWordOverlap(left, right);
}

function normalizeText(text) {
  return SubtitleRenderer.normalizeText(text);
}

function updateHistorySummary() {
  if (!historyCountEl) return;
  const label = historyCount === 1 ? "1 previous subtitle" : `${historyCount} previous subtitles`;
  historyCountEl.textContent = label;

  const action = document.querySelector(".history-action");
  if (action && historyDrawerEl) {
    action.textContent = historyDrawerEl.open ? "click to close" : "click to open";
  }
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


function loadTopPanelPreference() {
  if (!topPanelEl) return;
  const saved = localStorage.getItem("subtitleTopPanelOpen");
  if (saved === "0") topPanelEl.open = false;
  if (saved === "1") topPanelEl.open = true;
}

function updateTopPanelSummary(shouldPersist = true) {
  if (!topPanelEl || !topPanelActionEl) return;
  topPanelActionEl.textContent = topPanelEl.open ? "click to collapse" : "click to expand";
  if (shouldPersist) {
    localStorage.setItem("subtitleTopPanelOpen", topPanelEl.open ? "1" : "0");
  }
}

function loadModePreference() {
  qualityModeEl.value = Settings.getMode(localStorage);
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

function clampFontSize(value, min, max, fallback) {
  if (!Number.isFinite(value)) return fallback;
  return Math.max(min, Math.min(max, Math.round(value)));
}

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.className = isError ? "error" : "";
}

function applyDisplayMode(mode, shouldPersist) {
  const safeMode = ["current", "two-line", "history", "compact"].includes(mode) ? mode : "current";
  document.body.classList.remove("display-current", "display-two-line", "display-history", "display-compact");
  document.body.classList.add(`display-${safeMode}`);
  if (safeMode === "history" || safeMode === "compact") {
    historyDrawerEl.open = true;
  }
  if (shouldPersist) localStorage.setItem("subtitleDisplayMode", safeMode);
  updateHistorySummary(false);
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
  privacyStatusEl.textContent = logTranscriptText ? "Transcript text stored in logs" : "Transcript text hidden in logs";
  privacyStatusEl.className = logTranscriptText ? "privacy-on" : "";
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

function createQualityPill(quality) {
  if (!quality || !quality.level || quality.level === "good") return null;
  const pill = document.createElement("span");
  pill.className = `quality-pill ${quality.level}`;
  pill.textContent = quality.level === "low" ? "check ASR" : "watch ASR";
  pill.title = quality.reasons?.length ? quality.reasons.join(", ") : "";
  return pill;
}

function recordPendingTranscript(payload, elapsedSeconds) {
  const id = payload.id || `local-${Date.now()}`;
  const startedMs = startedAt ? Date.now() - startedAt : elapsedSeconds * 1000;
  closePreviousTranscript(startedMs);
  transcriptItems.push({
    id,
    startMs: startedMs,
    endMs: startedMs + 3500,
    dutch: payload.dutch || "",
    translation: "",
    pending: true,
    mode: payload.mode || qualityModeEl.value
    ,quality: payload.quality
  });
}

function recordFinalTranscript(payload, elapsedSeconds) {
  const id = payload.id;
  const item = transcriptItems.find(row => row.id === id);
  if (item) {
    item.dutch = payload.dutch || item.dutch;
    item.translation = payload.translation || item.translation;
    item.pending = false;
    item.mode = payload.mode || item.mode;
    item.quality = payload.quality || item.quality;
    return;
  }
  const startedMs = startedAt ? Date.now() - startedAt : elapsedSeconds * 1000;
  transcriptItems.push({
    id: id || `local-${Date.now()}`,
    startMs: startedMs,
    endMs: startedMs + 3500,
    dutch: payload.dutch || "",
    translation: payload.translation || "",
    pending: false,
    mode: payload.mode || qualityModeEl.value,
    quality: payload.quality
  });
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
  const content = format === "srt" ? toSrt(rows) : format === "vtt" ? toVtt(rows) : toTxt(rows);
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

function toTxt(rows) {
  return SubtitleRenderer.toTxt(rows);
}

function toVtt(rows) {
  return SubtitleRenderer.toVtt(rows);
}

function toSrt(rows) {
  return SubtitleRenderer.toSrt(rows);
}

function formatTime(ms, includeHours) {
  return SubtitleRenderer.formatTime(ms, includeHours);
}

function scheduleReconnect(socket) {
  if (manualStopRequested || ws !== socket || !mediaStream || !workletNode || reconnecting) return;
  reconnecting = true;
  reconnectAttempts += 1;
  if (reconnectAttempts > 20) {
    setStatus("Reconnect failed. Restart capture.", true);
    return;
  }
  setStatus(`Reconnecting to backend... ${reconnectAttempts}`, true);
  setTimeout(async () => {
    reconnecting = false;
    if (manualStopRequested || !mediaStream || !workletNode) return;
    try {
      ws = await connectWebSocket(backendUrlEl.value.trim());
      sendConfigIfOpen();
      setStatus("Reconnected");
      reconnectAttempts = 0;
      logClient("info", "websocket_reconnected");
    } catch (err) {
      logClient("warn", "websocket_reconnect_failed", { error: err?.message || String(err), attempt: reconnectAttempts });
      scheduleReconnect(socket);
    }
  }, Math.min(1000 + reconnectAttempts * 500, 5000));
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
  if (!glossaryRules.length) {
    glossaryListEl.textContent = "No glossary rules.";
    return;
  }
  glossaryRules.forEach((rule, index) => {
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

function handleShortcut(event) {
  const tag = event.target?.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || event.altKey || event.ctrlKey || event.metaKey) return;
  if (event.key === " ") {
    event.preventDefault();
    if (ws && ws.readyState === WebSocket.OPEN) stopCapture();
    else startCapture();
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
  } else if (event.key.toLowerCase() === "h" && historyDrawerEl) {
    event.preventDefault();
    historyDrawerEl.open = !historyDrawerEl.open;
    updateHistorySummary();
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
