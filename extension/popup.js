const DEFAULTS = {
  endpoint: "http://127.0.0.1:17392",
  authToken: "",
  requestTimeoutMs: 8000,
};

const STATUS_LABELS = {
  queued: "等待中",
  running: "下载中",
  retrying: "等待重试",
  completed: "已完成",
  failed: "失败",
};

const summaryElement = document.querySelector("#summary");
const jobsElement = document.querySelector("#jobs");
const connectionElement = document.querySelector("#connection");
const messageElement = document.querySelector("#message");
const downloadButton = document.querySelector("#download-current");
const refreshButton = document.querySelector("#refresh");
const settingsButton = document.querySelector("#settings");
let refreshTimer = null;

function formatBytes(value) {
  if (!Number.isFinite(value) || value < 0) return "";
  const units = ["B", "KiB", "MiB", "GiB"];
  let number = value;
  let unit = 0;
  while (number >= 1024 && unit < units.length - 1) {
    number /= 1024;
    unit += 1;
  }
  return `${number.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

function formatEta(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  const minutes = Math.floor(seconds / 60);
  const rest = Math.floor(seconds % 60);
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function appendText(parent, className, text) {
  const element = document.createElement("span");
  element.className = className;
  element.textContent = text;
  parent.appendChild(element);
  return element;
}

function renderSummary(summary) {
  summaryElement.replaceChildren();
  const entries = [
    [summary.active || 0, "运行"],
    [(summary.counts?.queued || 0) + (summary.counts?.retrying || 0), "等待"],
    [summary.counts?.completed || 0, "完成"],
    [summary.counts?.failed || 0, "失败"],
  ];
  for (const [value, label] of entries) {
    const metric = document.createElement("div");
    metric.className = "metric";
    const strong = document.createElement("strong");
    strong.textContent = String(value);
    const span = document.createElement("span");
    span.textContent = label;
    metric.append(strong, span);
    summaryElement.appendChild(metric);
  }
}

function renderJob(job) {
  const card = document.createElement("article");
  card.className = `job ${job.status}`;
  const hasOutput = Boolean(job.output_path || job.output_paths?.some(Boolean));
  const canOpen = job.status === "completed" && hasOutput;

  const head = document.createElement("div");
  head.className = "job-head";
  appendText(head, "platform", job.platform);
  const headActions = document.createElement("div");
  headActions.className = "job-head-actions";
  appendText(headActions, "status", STATUS_LABELS[job.status] || job.status);
  if (canOpen) {
    const reveal = document.createElement("button");
    reveal.className = "reveal";
    reveal.type = "button";
    reveal.textContent = "打开位置";
    reveal.title = "在资源管理器中显示这个文件";
    reveal.setAttribute("aria-label", `在资源管理器中显示：${job.title || job.url}`);
    reveal.addEventListener("click", (event) => {
      event.stopPropagation();
      runOutputAction(job.id, "reveal", reveal);
    });
    headActions.appendChild(reveal);
  }
  head.appendChild(headActions);

  const title = document.createElement("p");
  title.className = "title";
  title.textContent = job.title || job.url;

  const bar = document.createElement("div");
  const precise = Number.isFinite(job.progress_percent);
  bar.className = precise ? "bar" : `bar ${job.status === "running" ? "indeterminate" : ""}`;
  const fill = document.createElement("span");
  fill.style.width = precise ? `${Math.min(100, Math.max(0, job.progress_percent))}%` : "0%";
  bar.appendChild(fill);

  const meta = document.createElement("div");
  meta.className = "meta";
  const left = precise ? `${job.progress_percent.toFixed(1)}%` : (job.stage || "");
  const speed = formatBytes(job.speed_bytes_per_second);
  const eta = formatEta(job.eta_seconds);
  appendText(meta, "", left);
  appendText(meta, "", [speed && `${speed}/s`, eta && `剩余 ${eta}`].filter(Boolean).join(" · "));

  card.append(head, title, bar, meta);
  if (canOpen) {
    card.classList.add("openable");
    card.tabIndex = 0;
    card.title = "单击用系统默认软件打开视频";
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", `打开视频：${job.title || job.url}`);
    card.addEventListener("click", () => runOutputAction(job.id, "open", card));
    card.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      runOutputAction(job.id, "open", card);
    });
  }
  if (job.status === "failed") {
    const retry = document.createElement("button");
    retry.className = "retry";
    retry.textContent = "重试";
    retry.addEventListener("click", () => retryJob(job.id, retry));
    card.appendChild(retry);
  }
  return card;
}

async function runOutputAction(jobId, action, control) {
  if (control.classList.contains("busy")) return;
  const isReveal = action === "reveal";
  control.classList.add("busy");
  if ("disabled" in control) control.disabled = true;
  messageElement.className = "";
  messageElement.textContent = isReveal ? "正在打开文件位置……" : "正在打开视频……";
  try {
    await api(`/api/jobs/${jobId}/${action}`, { method: "POST" });
    messageElement.textContent = isReveal ? "已在资源管理器中显示文件" : "已用系统默认软件打开视频";
  } catch (error) {
    messageElement.className = "error";
    messageElement.textContent = String(error?.message || error);
  } finally {
    control.classList.remove("busy");
    if ("disabled" in control) control.disabled = false;
  }
}

function renderJobs(jobs) {
  jobsElement.replaceChildren();
  if (!jobs.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "尚无任务";
    jobsElement.appendChild(empty);
    return;
  }
  for (const job of jobs) jobsElement.appendChild(renderJob(job));
}

async function settings() {
  return chrome.storage.local.get(DEFAULTS);
}

async function api(path, options = {}) {
  const local = await settings();
  if (!local.authToken) throw new Error("请先在设置页填写本地接口令牌");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), local.requestTimeoutMs);
  try {
    const response = await fetch(`${String(local.endpoint).replace(/\/+$/, "")}${path}`, {
      ...options,
      headers: { "X-YTDLP-Token": local.authToken, ...(options.headers || {}) },
      signal: controller.signal,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  } finally {
    clearTimeout(timer);
  }
}

async function refresh() {
  try {
    const payload = await api("/api/jobs?limit=30");
    connectionElement.textContent = `调度器在线 · 并发 ${payload.summary.active}/${payload.summary.limits.total}`;
    messageElement.className = "";
    renderSummary(payload.summary);
    renderJobs(payload.jobs || []);
    const badge = payload.summary.active ? `↓${payload.summary.active}` : (payload.summary.queueDepth ? `Q${payload.summary.queueDepth}` : "");
    await chrome.action.setBadgeBackgroundColor({ color: payload.summary.active ? "#2563eb" : "#64748b" });
    await chrome.action.setBadgeText({ text: badge });
  } catch (error) {
    connectionElement.textContent = "本地调度器不可用";
    messageElement.className = "error";
    messageElement.textContent = String(error?.message || error);
  }
}

async function retryJob(jobId, button) {
  button.disabled = true;
  try {
    await api(`/api/jobs/${jobId}/retry`, { method: "POST" });
    messageElement.textContent = "任务已重新排队";
    await refresh();
  } catch (error) {
    messageElement.className = "error";
    messageElement.textContent = String(error?.message || error);
  } finally {
    button.disabled = false;
  }
}

downloadButton.addEventListener("click", async () => {
  downloadButton.disabled = true;
  messageElement.className = "";
  messageElement.textContent = "正在识别当前页面……";
  try {
    const response = await chrome.runtime.sendMessage({ type: "queue-current-video" });
    if (!response?.ok) throw new Error(response?.error || "提交失败");
    messageElement.textContent = response.result?.ignored ? "当前页面不是受支持的视频页" : "任务已提交";
    await refresh();
  } catch (error) {
    messageElement.className = "error";
    messageElement.textContent = String(error?.message || error);
  } finally {
    downloadButton.disabled = false;
  }
});

refreshButton.addEventListener("click", refresh);
settingsButton.addEventListener("click", () => chrome.runtime.openOptionsPage());
refresh();
refreshTimer = setInterval(refresh, 1000);
window.addEventListener("unload", () => clearInterval(refreshTimer));
