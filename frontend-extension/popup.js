const startBackendBtn = document.getElementById("startBackendBtn");
const openWindowBtn = document.getElementById("openWindowBtn");
const statusEl = document.getElementById("status");
const popupLogsEl = document.getElementById("popupLogs");
const refreshPopupLogsBtn = document.getElementById("refreshPopupLogsBtn");

const NATIVE_HOST = "com.polatozgur111.dutch_subtitle_backend";
const HEALTH_URL = "http://127.0.0.1:8000/health";
const CLIENT_LOG_ENDPOINT = "http://127.0.0.1:8000/api/logs/client";
const BACKEND_LOGS_ENDPOINT = "http://127.0.0.1:8000/api/logs/recent";


function logClient(level, message, context = {}) {
  const record = {
    ts: new Date().toISOString(),
    level,
    source: "extension-popup",
    message,
    context
  };
  const line = `${record.ts} ${level.toUpperCase()} ${message} ${Object.keys(context).length ? JSON.stringify(context) : ""}`;
  if (popupLogsEl) {
    popupLogsEl.textContent = `${line}\n${popupLogsEl.textContent || ""}`.slice(0, 20000);
  }
  try {
    const method = level === "error" ? "error" : level === "warn" ? "warn" : "log";
    console[method]("[DutchSubtitles]", message, context);
  } catch (_err) {}
  fetch(CLIENT_LOG_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(record),
    keepalive: true
  }).catch(() => {});
}

async function refreshPopupBackendLogs(event) {
  if (event) event.preventDefault();
  try {
    const response = await fetch(`${BACKEND_LOGS_ENDPOINT}?lines=120`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (popupLogsEl) {
      popupLogsEl.textContent = [
        `--- backend log: ${data.log_file || "unknown"} ---`,
        ...(data.lines || [])
      ].join("\n");
    }
    logClient("info", "popup_backend_logs_refreshed", { lines: (data.lines || []).length });
  } catch (err) {
    logClient("error", "popup_backend_logs_refresh_failed", { error: err?.message || String(err) });
  }
}

if (refreshPopupLogsBtn) refreshPopupLogsBtn.addEventListener("click", refreshPopupBackendLogs);
logClient("info", "popup_loaded");

async function checkBackendHealth() {
  try {
    const response = await fetch(HEALTH_URL, { cache: "no-store" });
    if (!response.ok) return false;
    const data = await response.json();
    return Boolean(data?.ok);
  } catch (err) {
    logClient("warn", "backend_health_check_failed", { error: err?.message || String(err) });
    return false;
  }
}

async function waitForBackend(maxAttempts = 45) {
  for (let i = 0; i < maxAttempts; i += 1) {
    if (await checkBackendHealth()) return true;
    await new Promise((resolve) => setTimeout(resolve, 1000));
    statusEl.textContent = `Backend starting... ${i + 1}s`;
    if ((i + 1) % 5 === 0) logClient("info", "backend_start_waiting", { seconds: i + 1 });
  }
  return false;
}

function sendNativeMessage(payload) {
  return new Promise((resolve, reject) => {
    logClient("info", "native_message_send", payload);
    chrome.runtime.sendNativeMessage(NATIVE_HOST, payload, (response) => {
      const err = chrome.runtime.lastError;
      if (err) {
        logClient("error", "native_message_failed", { error: err.message });
        reject(new Error(err.message));
        return;
      }
      logClient("info", "native_message_response", response || {});
      resolve(response);
    });
  });
}

startBackendBtn.addEventListener("click", async () => {
  logClient("info", "start_backend_clicked");
  startBackendBtn.disabled = true;
  statusEl.className = "muted";

  try {
    if (await checkBackendHealth()) {
      statusEl.textContent = "Backend is already running.";
      logClient("info", "backend_already_running");
      return;
    }

    statusEl.textContent = "Starting backend...";
    const response = await sendNativeMessage({ command: "start_backend" });

    if (!response?.ok) {
      throw new Error(response?.error || "Native host failed to start backend.");
    }

    statusEl.textContent = response.message || "Backend process started. Waiting for /health...";
    const ready = await waitForBackend();

    if (ready) {
      statusEl.textContent = "Backend is ready.";
      logClient("info", "backend_ready_after_start");
    } else {
      statusEl.className = "error";
      statusEl.textContent = "Backend started but /health is not ready yet. Check backend/logs/.";
      logClient("error", "backend_started_but_health_not_ready");
    }
  } catch (err) {
    logClient("error", "start_backend_failed", { error: err?.message || String(err) });
    statusEl.className = "error";
    statusEl.textContent = `${err?.message || String(err)}. Run native-host/install_linux.sh once, then reload the extension.`;
  } finally {
    startBackendBtn.disabled = false;
  }
});

openWindowBtn.addEventListener("click", async () => {
  logClient("info", "open_subtitle_window_clicked");
  try {
    const healthy = await checkBackendHealth();
    if (!healthy) {
      statusEl.className = "error";
      statusEl.textContent = "Backend is not running. Click Start backend first.";
      logClient("warn", "open_window_blocked_backend_not_running");
      return;
    }

    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) {
      statusEl.textContent = "No active tab found.";
      return;
    }

    const url = chrome.runtime.getURL(`subtitle.html?tabId=${tab.id}`);
    await chrome.windows.create({
      url,
      type: "popup",
      width: 920,
      height: 680,
      focused: true
    });
    logClient("info", "subtitle_window_created", { tabId: tab.id });
    window.close();
  } catch (err) {
    logClient("error", "open_subtitle_window_failed", { error: err?.message || String(err) });
    statusEl.className = "error";
    statusEl.textContent = err?.message || String(err);
  }
});

checkBackendHealth().then((healthy) => {
  statusEl.textContent = healthy ? "Backend is running." : "Backend is not running.";
  logClient("info", "initial_backend_health", { healthy });
});
