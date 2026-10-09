const DEFAULTS = {
  endpoint: "http://127.0.0.1:17392",
  authToken: "",
  requestTimeoutMs: 8000,
};

const form = document.querySelector("#settings-form");
const endpointInput = document.querySelector("#endpoint");
const tokenInput = document.querySelector("#auth-token");
const timeoutInput = document.querySelector("#request-timeout");
const testButton = document.querySelector("#test-button");
const status = document.querySelector("#status");
const lastSubmission = document.querySelector("#last-submission");
const lastCapture = document.querySelector("#last-capture");

function showStatus(message, ok) {
  status.textContent = message;
  status.className = ok ? "ok" : "error";
}

async function load() {
  const settings = await chrome.storage.local.get({ ...DEFAULTS, lastCapture: null, lastSubmission: null });
  endpointInput.value = settings.endpoint;
  tokenInput.value = settings.authToken;
  timeoutInput.value = settings.requestTimeoutMs;
  lastCapture.textContent = settings.lastCapture
    ? JSON.stringify(settings.lastCapture, null, 2)
    : "尚无记录";
  lastSubmission.textContent = settings.lastSubmission
    ? JSON.stringify(settings.lastSubmission, null, 2)
    : "尚无记录";
}

async function save() {
  const endpoint = endpointInput.value.trim().replace(/\/+$/, "");
  const authToken = tokenInput.value.trim();
  const requestTimeoutMs = Math.max(1000, Number(timeoutInput.value) || DEFAULTS.requestTimeoutMs);
  await chrome.storage.local.set({ endpoint, authToken, requestTimeoutMs });
  showStatus("设置已保存。", true);
  return { endpoint, authToken, requestTimeoutMs };
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  await save();
});

testButton.addEventListener("click", async () => {
  testButton.disabled = true;
  try {
    const { endpoint, authToken, requestTimeoutMs } = await save();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), requestTimeoutMs);
    try {
      const response = await fetch(`${endpoint}/api/health`, {
        headers: { "X-YTDLP-Token": authToken },
        signal: controller.signal,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      showStatus(`连接成功：${payload.name} ${payload.version}，队列 ${payload.queueDepth} 项。`, true);
    } finally {
      clearTimeout(timer);
    }
  } catch (error) {
    const message = error?.name === "AbortError" ? "连接超时" : String(error?.message || error);
    showStatus(`连接失败：${message}`, false);
  } finally {
    testButton.disabled = false;
  }
});

load();
