const startBackendBtn = document.getElementById("startBackendBtn");
const openWindowBtn = document.getElementById("openWindowBtn");
const statusEl = document.getElementById("status");

const NATIVE_HOST = "com.polatozgur111.dutch_subtitle_backend";
const HEALTH_URL = "http://127.0.0.1:8000/health";

async function checkBackendHealth() {
  try {
    const response = await fetch(HEALTH_URL, { cache: "no-store" });
    if (!response.ok) return false;
    const data = await response.json();
    return Boolean(data?.ok);
  } catch {
    return false;
  }
}

async function waitForBackend(maxAttempts = 45) {
  for (let i = 0; i < maxAttempts; i += 1) {
    if (await checkBackendHealth()) return true;
    await new Promise((resolve) => setTimeout(resolve, 1000));
    statusEl.textContent = `Backend starting... ${i + 1}s`;
  }
  return false;
}

function sendNativeMessage(payload) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendNativeMessage(NATIVE_HOST, payload, (response) => {
      const err = chrome.runtime.lastError;
      if (err) {
        reject(new Error(err.message));
        return;
      }
      resolve(response);
    });
  });
}

startBackendBtn.addEventListener("click", async () => {
  startBackendBtn.disabled = true;
  statusEl.className = "muted";

  try {
    if (await checkBackendHealth()) {
      statusEl.textContent = "Backend is already running.";
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
    } else {
      statusEl.className = "error";
      statusEl.textContent = "Backend started but /health is not ready yet. Check backend/backend.log.";
    }
  } catch (err) {
    statusEl.className = "error";
    statusEl.textContent = `${err?.message || String(err)}. Run native-host/install_linux.sh once, then reload the extension.`;
  } finally {
    startBackendBtn.disabled = false;
  }
});

openWindowBtn.addEventListener("click", async () => {
  try {
    const healthy = await checkBackendHealth();
    if (!healthy) {
      statusEl.className = "error";
      statusEl.textContent = "Backend is not running. Click Start backend first.";
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
    window.close();
  } catch (err) {
    statusEl.className = "error";
    statusEl.textContent = err?.message || String(err);
  }
});

checkBackendHealth().then((healthy) => {
  statusEl.textContent = healthy ? "Backend is running." : "Backend is not running.";
});
