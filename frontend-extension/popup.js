const startBackendBtn = document.getElementById("startBackendBtn");
const restartBackendBtn = document.getElementById("restartBackendBtn");
const stopBackendBtn = document.getElementById("stopBackendBtn");
const openWindowBtn = document.getElementById("openWindowBtn");
const statusEl = document.getElementById("status");
const popupLogsEl = document.getElementById("popupLogs");
const refreshPopupLogsBtn = document.getElementById("refreshPopupLogsBtn");
const setupNativeEl = document.getElementById("setupNative");
const setupBackendEl = document.getElementById("setupBackend");
const setupModelsEl = document.getElementById("setupModels");
const setupPermissionsEl = document.getElementById("setupPermissions");

const NATIVE_HOST = "com.polatozgur111.dutch_subtitle_backend";
const DEFAULT_BASE_URL = "http://127.0.0.1:8000";

function backendBaseUrl() {
  return localStorage.getItem("backendBaseUrl") || DEFAULT_BASE_URL;
}

function backendUrl(path) {
  return `${backendBaseUrl()}${path}`;
}


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
  fetch(backendUrl("/api/logs/client"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(record),
    keepalive: true
  }).catch(() => {});
}

async function refreshPopupBackendLogs(event) {
  if (event) event.preventDefault();
  try {
    const response = await fetch(`${backendUrl("/api/logs/recent")}?lines=120`, { cache: "no-store" });
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
updateSetupState("check");

async function checkBackendHealth() {
  try {
    const response = await fetch(backendUrl("/health/live"), { cache: "no-store" });
    if (!response.ok) return false;
    const data = await response.json();
    return Boolean(data?.ok);
  } catch (err) {
    logClient("warn", "backend_health_check_failed", { error: err?.message || String(err) });
    return false;
  }
}

function setSetupRow(element, state) {
  if (!element) return;
  element.className = `setup-row ${state}`;
}

async function updateSetupState(phase = "check") {
  setSetupRow(setupNativeEl, phase === "native_error" ? "error" : "ok");
  const live = await checkBackendHealth();
  setSetupRow(setupBackendEl, live ? "ok" : "warn");
  const ready = live ? await checkBackendReady() : false;
  setSetupRow(setupModelsEl, ready ? "ok" : live ? "warn" : "error");
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    setSetupRow(setupPermissionsEl, tab?.id ? "ok" : "warn");
  } catch (_err) {
    setSetupRow(setupPermissionsEl, "warn");
  }
}

async function checkBackendReady() {
  try {
    const response = await fetch(backendUrl("/health/ready"), { cache: "no-store" });
    if (!response.ok) return false;
    const data = await response.json();
    return Boolean(data?.ready && data?.model_ready);
  } catch (err) {
    logClient("warn", "backend_ready_check_failed", { error: err?.message || String(err) });
    return false;
  }
}

async function backendDiagnosticMessage() {
  try {
    const response = await fetch(backendUrl("/debug/device"), { cache: "no-store" });
    if (!response.ok) return "Backend diagnostics unavailable.";
    const data = await response.json();
    const status = data?.readiness?.startup_status;
    const error = data?.readiness?.last_error || status?.error;
    if (error?.message) return error.message;
    if (status?.phase && !status?.ok) return `Backend phase: ${status.phase}`;
    return "Backend models are not ready yet.";
  } catch (err) {
    logClient("warn", "backend_diagnostic_failed", { error: err?.message || String(err) });
    return "Backend diagnostics unavailable.";
  }
}

function rememberBackendResponse(response) {
  if (!response) return;
  if (response.base_url) localStorage.setItem("backendBaseUrl", response.base_url);
  if (response.ws_url) localStorage.setItem("backendWsUrl", response.ws_url);
}

async function waitForBackend(maxAttempts = 45) {
  for (let i = 0; i < maxAttempts; i += 1) {
    if (await checkBackendReady()) return true;
    await new Promise((resolve) => setTimeout(resolve, 1000));
    statusEl.textContent = `Backend loading models... ${i + 1}s`;
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
        updateSetupState("native_error");
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
    if (await checkBackendReady()) {
      statusEl.textContent = "Backend is already running.";
      logClient("info", "backend_already_running");
      updateSetupState();
      return;
    }
    if (await checkBackendHealth()) {
      statusEl.textContent = "Backend is running. Waiting for model readiness...";
      const ready = await waitForBackend();
      statusEl.textContent = ready ? "Backend is ready." : await backendDiagnosticMessage();
      statusEl.className = ready ? "muted" : "error";
      return;
    }

    statusEl.textContent = "Starting backend...";
    const response = await sendNativeMessage({ command: "start_backend" });
    rememberBackendResponse(response);

    if (!response?.ok) {
      throw new Error(response?.error || "Native host failed to start backend.");
    }

    statusEl.textContent = response.message || "Backend process started. Waiting for model readiness...";
    const ready = await waitForBackend();

    if (ready) {
      statusEl.textContent = "Backend is ready.";
      logClient("info", "backend_ready_after_start");
      updateSetupState();
    } else {
      statusEl.className = "error";
      statusEl.textContent = await backendDiagnosticMessage();
      logClient("error", "backend_started_but_models_not_ready");
    }
  } catch (err) {
    logClient("error", "start_backend_failed", { error: err?.message || String(err) });
    statusEl.className = "error";
    statusEl.textContent = `${err?.message || String(err)}. Run native-host/install_linux.sh once, then reload the extension.`;
  } finally {
    startBackendBtn.disabled = false;
  }
});

if (restartBackendBtn) restartBackendBtn.addEventListener("click", async () => {
  logClient("info", "restart_backend_clicked");
  restartBackendBtn.disabled = true;
  statusEl.className = "muted";
  statusEl.textContent = "Restarting backend...";
  try {
    const response = await sendNativeMessage({ command: "restart_backend" });
    rememberBackendResponse(response);
    if (!response?.ok) throw new Error(response?.error || "Native host failed to restart backend.");
    statusEl.textContent = response.message || "Backend restarted. Waiting for model readiness...";
    const ready = await waitForBackend();
    statusEl.textContent = ready ? "Backend is ready." : await backendDiagnosticMessage();
    statusEl.className = ready ? "muted" : "error";
  } catch (err) {
    logClient("error", "restart_backend_failed", { error: err?.message || String(err) });
    statusEl.className = "error";
    statusEl.textContent = err?.message || String(err);
  } finally {
    restartBackendBtn.disabled = false;
  }
});

if (stopBackendBtn) stopBackendBtn.addEventListener("click", async () => {
  logClient("info", "stop_backend_clicked");
  stopBackendBtn.disabled = true;
  statusEl.className = "muted";
  statusEl.textContent = "Stopping backend...";
  try {
    const response = await sendNativeMessage({ command: "stop_backend" });
    if (!response?.ok) throw new Error(response?.error || "Native host failed to stop backend.");
    statusEl.textContent = response.message || "Backend stopped.";
    updateSetupState();
  } catch (err) {
    logClient("error", "stop_backend_failed", { error: err?.message || String(err) });
    statusEl.className = "error";
    statusEl.textContent = err?.message || String(err);
  } finally {
    stopBackendBtn.disabled = false;
  }
});

openWindowBtn.addEventListener("click", async () => {
  logClient("info", "open_subtitle_window_clicked");
  try {
    const healthy = await checkBackendReady();
    if (!healthy) {
      statusEl.className = "error";
      statusEl.textContent = await backendDiagnosticMessage();
      logClient("warn", "open_window_blocked_backend_not_ready");
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

checkBackendReady().then((ready) => {
  statusEl.textContent = ready ? "Backend is ready." : "Backend is not ready.";
  logClient("info", "initial_backend_ready", { ready });
  updateSetupState();
});
