const TARGET_SAMPLE_RATE = 16000;

const params = new URLSearchParams(location.search);
const tabId = Number(params.get("tabId"));

const backendUrlEl = document.getElementById("backendUrl");
const targetLangEl = document.getElementById("targetLang");
const qualityModeEl = document.getElementById("qualityMode");
const dutchFontSizeEl = document.getElementById("dutchFontSize");
const translationFontSizeEl = document.getElementById("translationFontSize");
const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const statusEl = document.getElementById("status");
const audioStatusEl = document.getElementById("audioStatus");
const latencyEl = document.getElementById("latency");
const currentSubtitleEl = document.getElementById("currentSubtitle");
const historySubtitlesEl = document.getElementById("historySubtitles");
const historyDrawerEl = document.getElementById("historyDrawer");
const historyCountEl = document.getElementById("historyCount");
const topPanelEl = document.getElementById("topPanel");
const topPanelActionEl = document.getElementById("topPanelAction");

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

startBtn.addEventListener("click", startCapture);
stopBtn.addEventListener("click", stopCapture);
qualityModeEl.addEventListener("change", sendConfigIfOpen);
targetLangEl.addEventListener("change", sendConfigIfOpen);
dutchFontSizeEl.addEventListener("input", () => updateFontSizes(true));
translationFontSizeEl.addEventListener("input", () => updateFontSizes(true));
window.addEventListener("beforeunload", stopCapture);
if (historyDrawerEl) historyDrawerEl.addEventListener("toggle", updateHistorySummary);
if (topPanelEl) topPanelEl.addEventListener("toggle", updateTopPanelSummary);

updateFontSizes(false);
loadModePreference();
loadTopPanelPreference();
updateHistorySummary();
updateTopPanelSummary(false);

async function startCapture() {
  if (!tabId || Number.isNaN(tabId)) {
    setStatus("Missing tab id. Reopen the extension from a video tab.", true);
    return;
  }

  startBtn.disabled = true;
  stopBtn.disabled = false;
  clearSubtitles();
  latencyEl.textContent = "—";
  startedAt = Date.now();

  try {
    setStatus("Connecting to backend...");
    ws = await connectWebSocket(backendUrlEl.value.trim());
    sendConfigIfOpen();

    setStatus("Requesting tab audio permission...");
    mediaStream = await captureTabAudio(tabId);

    setStatus("Starting audio pipeline...");
    await startAudioPipeline(mediaStream);

    audioStatusEl.textContent = "Capturing tab audio";
    setStatus("Running");
  } catch (err) {
    setStatus(err?.message || String(err), true);
    await stopCapture();
  }
}

async function stopCapture() {
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
}

function sendConfigIfOpen() {
  localStorage.setItem("subtitleQualityMode", qualityModeEl.value);
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({
    type: "config",
    sample_rate: TARGET_SAMPLE_RATE,
    source_lang: "nl",
    target_lang: targetLangEl.value,
    mode: qualityModeEl.value
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
      resolve(socket);
    };

    socket.onerror = () => {
      clearTimeout(timeout);
      reject(new Error("Could not connect to backend WebSocket."));
    };

    socket.onclose = () => {
      if (ws === socket) setStatus("Backend connection closed", true);
    };

    socket.onmessage = event => {
      try {
        const payload = JSON.parse(event.data);
        handleBackendMessage(payload);
      } catch (_err) {
        // Ignore non-JSON messages.
      }
    };
  });
}

function handleBackendMessage(payload) {
  if (payload.type === "ready") {
    setStatus("Backend ready");
    return;
  }

  if (payload.type === "config_ack") return;

  if (payload.type === "error") {
    setStatus(payload.message || "Backend error", true);
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
      }
      if (typeof payload.latency_ms === "number") {
        latencyEl.textContent = `${payload.latency_ms} ms partial ASR`;
      }
    }
    return;
  }

  if (payload.type === "final_pending") {
    if (!payload.dutch) return;
    stablePartialText = "";
    showFinalPending(payload);
    if (typeof payload.latency_ms === "number") {
      latencyEl.textContent = `${payload.latency_ms} ms ASR · translating…`;
    }
    return;
  }

  if (payload.type === "final") {
    if (!payload.dutch) return;
    stablePartialText = "";
    updateFinalTranslation(payload);
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
        reject(new Error(lastError.message));
        return;
      }
      if (!streamId) {
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
        reject(err);
      }
    });
  });
}

async function startAudioPipeline(stream) {
  audioContext = new AudioContext({ latencyHint: "interactive" });
  await audioContext.audioWorklet.addModule("worklet.js");

  mediaSource = audioContext.createMediaStreamSource(stream);
  workletNode = new AudioWorkletNode(audioContext, "pcm-worklet");

  monitorGain = audioContext.createGain();
  monitorGain.gain.value = 1.0;

  mediaSource.connect(workletNode);
  mediaSource.connect(monitorGain);
  monitorGain.connect(audioContext.destination);

  workletNode.port.onmessage = event => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const sourceSamples = new Float32Array(event.data);
    const resampled = resampleLinear(sourceSamples, audioContext.sampleRate, TARGET_SAMPLE_RATE);
    const pcm16 = float32ToPCM16LE(resampled);
    if (pcm16.byteLength > 0) ws.send(pcm16);
  };
}

function resampleLinear(input, sourceRate, targetRate) {
  if (!input || input.length === 0) return new Float32Array(0);
  if (sourceRate === targetRate) return input;

  const ratio = sourceRate / targetRate;
  const outputLength = Math.floor(input.length / ratio);
  const output = new Float32Array(outputLength);

  for (let i = 0; i < outputLength; i++) {
    const srcIndex = i * ratio;
    const index0 = Math.floor(srcIndex);
    const index1 = Math.min(index0 + 1, input.length - 1);
    const frac = srcIndex - index0;
    output[i] = input[index0] * (1 - frac) + input[index1] * frac;
  }

  return output;
}

function float32ToPCM16LE(samples) {
  const buffer = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < samples.length; i++) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    const int16 = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    view.setInt16(i * 2, int16, true);
  }
  return buffer;
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
  currentSubtitleItem = {
    id: payload.id || `local-${Date.now()}`,
    dutch: payload.dutch,
    translation: "Translating…",
    pending: true,
    meta: `+${elapsed}s · ${payload.asr_latency_ms || payload.latency_ms || 0} ms ASR · ${payload.mode || qualityModeEl.value}`,
    detail: payload.asr_fragment ? `ASR fragment: ${payload.asr_fragment}` : ""
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

  if (currentSubtitleItem && (!id || currentSubtitleItem.id === id)) {
    currentSubtitleItem = {
      ...currentSubtitleItem,
      dutch: payload.dutch || currentSubtitleItem.dutch,
      translation: translation || currentSubtitleItem.translation,
      pending: false,
      meta,
      detail: payload.asr_fragment ? `ASR fragment: ${payload.asr_fragment}` : currentSubtitleItem.detail
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

  currentSubtitleEl.appendChild(dutchCell);
  currentSubtitleEl.appendChild(translationCell);
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

  row.appendChild(dutchCell);
  row.appendChild(translationCell);
  row.appendChild(meta);
  return row;
}

function clearSubtitles() {
  currentSubtitleItem = null;
  historyCount = 0;
  stablePartialText = "";
  delete currentSubtitleEl.dataset.subtitleId;
  currentSubtitleEl.className = "current-subtitle empty";
  currentSubtitleEl.innerHTML = '<div class="placeholder">Waiting for Dutch speech…</div>';
  historySubtitlesEl.innerHTML = "";
  if (historyDrawerEl) historyDrawerEl.open = false;
  updateHistorySummary();
}

function stabilizePartial(previous, next) {
  previous = normalizeText(previous);
  next = normalizeText(next);
  if (!next) return previous;
  if (!previous) return next;

  const prevLower = previous.toLowerCase();
  const nextLower = next.toLowerCase();

  if (nextLower === prevLower) return previous;
  if (nextLower.startsWith(prevLower)) return next;
  if (prevLower.includes(nextLower) && next.length + 10 < previous.length) return previous;

  // Ignore sudden short regressions from rolling ASR windows. They are the main
  // reason words appear to disappear while a sentence is being built.
  if (next.length < previous.length * 0.72 && !prevLower.endsWith(".") && !prevLower.endsWith("?") && !prevLower.endsWith("!")) {
    return previous;
  }

  const merged = mergeByWordOverlap(previous, next);
  if (merged.length >= Math.max(previous.length, next.length) - 4) return merged;
  return next;
}

function mergeByWordOverlap(left, right) {
  const leftWords = left.split(/\s+/).filter(Boolean);
  const rightWords = right.split(/\s+/).filter(Boolean);
  const maxOverlap = Math.min(10, leftWords.length, rightWords.length);
  for (let n = maxOverlap; n > 0; n--) {
    const a = leftWords.slice(-n).join(" ").toLowerCase();
    const b = rightWords.slice(0, n).join(" ").toLowerCase();
    if (a === b) return [...leftWords, ...rightWords.slice(n)].join(" ");
  }
  return right.length > left.length ? right : left;
}

function normalizeText(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
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
  const saved = localStorage.getItem("subtitleQualityMode");
  if (["fast", "balanced", "quality"].includes(saved)) {
    qualityModeEl.value = saved;
  } else {
    qualityModeEl.value = "balanced";
  }
}

function clampFontSize(value, min, max, fallback) {
  if (!Number.isFinite(value)) return fallback;
  return Math.max(min, Math.min(max, Math.round(value)));
}

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.className = isError ? "error" : "";
}
