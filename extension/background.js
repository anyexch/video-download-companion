const DEFAULTS = Object.freeze({
  endpoint: "http://127.0.0.1:17392",
  authToken: "",
  requestTimeoutMs: 8000,
});

const YOUTUBE_HOSTS = new Set([
  "youtube.com",
  "www.youtube.com",
  "m.youtube.com",
  "music.youtube.com",
  "youtu.be",
]);

function isBilibiliHost(host) {
  return host === "bilibili.com" || host.endsWith(".bilibili.com");
}

function isDouyinHost(host) {
  return host === "douyin.com" || host.endsWith(".douyin.com");
}

function extractVideoId(rawUrl) {
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    return null;
  }

  const host = url.hostname.toLowerCase();
  if (!YOUTUBE_HOSTS.has(host)) return null;

  let candidate = "";
  if (host === "youtu.be") {
    candidate = url.pathname.split("/").filter(Boolean)[0] || "";
  } else if (url.pathname === "/watch") {
    candidate = url.searchParams.get("v") || "";
  } else {
    const parts = url.pathname.split("/").filter(Boolean);
    if (["shorts", "live", "embed"].includes(parts[0])) {
      candidate = parts[1] || "";
    }
  }

  return /^[A-Za-z0-9_-]{11}$/.test(candidate) ? candidate : null;
}

function classifyDirectMedia(rawUrl) {
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    return null;
  }

  const youtubeId = extractVideoId(rawUrl);
  if (youtubeId) {
    return {
      platform: "youtube",
      contentId: youtubeId,
      canonicalUrl: `https://www.youtube.com/watch?v=${youtubeId}`,
      extractionMethod: "page-url",
    };
  }

  const host = url.hostname.toLowerCase();
  if (isDouyinHost(host)) {
    const parts = url.pathname.split("/").filter(Boolean);
    let contentType = "video";
    let contentId = "";
    if (["video", "note"].includes(parts[0]) && /^\d{15,22}$/.test(parts[1] || "")) {
      contentType = parts[0];
      contentId = parts[1];
    } else {
      const modalId = url.searchParams.get("modal_id") || "";
      if (/^\d{15,22}$/.test(modalId)) contentId = modalId;
    }
    if (!contentId) return null;
    return {
      platform: "douyin",
      contentId,
      contentType,
      canonicalUrl: `https://www.douyin.com/${contentType}/${contentId}`,
      extractionMethod: parts[0] === contentType && parts[1] === contentId ? "page-path" : "modal-id",
    };
  }

  if (!isBilibiliHost(host)) return null;
  const parts = url.pathname.split("/").filter(Boolean);
  let contentId = "";
  let canonicalPath = "";
  if (parts[0] === "video" && /^(BV[0-9A-Za-z]{10}|av[0-9]+)$/.test(parts[1] || "")) {
    contentId = parts[1];
    canonicalPath = `/video/${contentId}`;
  } else if (
    parts[0] === "bangumi" &&
    parts[1] === "play" &&
    /^(ep|ss)[0-9]+$/.test(parts[2] || "")
  ) {
    contentId = parts[2];
    canonicalPath = `/bangumi/play/${contentId}`;
  } else {
    return null;
  }

  const page = url.searchParams.get("p");
  const pageSuffix = page && /^[1-9][0-9]*$/.test(page) ? `?p=${page}` : "";
  return {
    platform: "bilibili",
    contentId,
    canonicalUrl: `https://www.bilibili.com${canonicalPath}${pageSuffix}`,
    extractionMethod: "page-url",
  };
}

function extractDouyinFromPage() {
  const idPattern = /^\d{15,22}$/;
  const candidates = [];
  const seen = new Set();

  function addCandidate(type, id, score, method) {
    if (!["video", "note"].includes(type) || !idPattern.test(id)) return;
    const key = `${type}:${id}`;
    if (seen.has(key)) return;
    seen.add(key);
    candidates.push({ type, id, score, method });
  }

  function inspectUrl(rawValue, score, method) {
    if (!rawValue) return;
    let parsed;
    try {
      parsed = new URL(rawValue, location.href);
    } catch {
      return;
    }
    const parts = parsed.pathname.split("/").filter(Boolean);
    if (["video", "note"].includes(parts[0])) {
      addCandidate(parts[0], parts[1] || "", score, method);
    }
    const modalId = parsed.searchParams.get("modal_id") || "";
    if (modalId) addCandidate("video", modalId, score + 5, `${method}-modal`);
    for (const key of ["aweme_id", "gid"]) {
      const id = parsed.searchParams.get(key) || "";
      if (id) addCandidate("video", id, score, `${method}-${key}`);
    }
  }

  inspectUrl(location.href, 1000, "page-url");
  const canonical = document.querySelector('link[rel="canonical"]');
  if (canonical) inspectUrl(canonical.href, 900, "canonical-link");
  const ogUrl = document.querySelector('meta[property="og:url"]');
  if (ogUrl) inspectUrl(ogUrl.content, 900, "og-url");

  const videos = Array.from(document.querySelectorAll("video"))
    .map((video) => ({ video, rect: video.getBoundingClientRect() }))
    .filter(({ rect }) => rect.width > 160 && rect.height > 120 && rect.bottom > 0 && rect.right > 0)
    .sort((a, b) => b.rect.width * b.rect.height - a.rect.width * a.rect.height);

  if (videos.length) {
    let container = videos[0].video;
    for (let depth = 0; container && depth < 8; depth += 1, container = container.parentElement) {
      const linked = Array.from(container.querySelectorAll("[href]")).slice(0, 100);
      for (const element of linked) inspectUrl(element.getAttribute("href"), 800 - depth * 10, "active-player-nearby");
    }
  }

  const visibleLinks = Array.from(document.querySelectorAll("[href]"))
    .filter((element) => {
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.top < innerHeight;
    })
    .slice(0, 300);
  for (const element of visibleLinks) inspectUrl(element.getAttribute("href"), 300, "visible-link");

  candidates.sort((a, b) => b.score - a.score);
  if (!candidates.length) return null;
  if (candidates.length > 1 && candidates[0].score === candidates[1].score && candidates[0].id !== candidates[1].id) {
    return null;
  }
  const best = candidates[0];
  return {
    platform: "douyin",
    contentId: best.id,
    contentType: best.type,
    canonicalUrl: `https://www.douyin.com/${best.type}/${best.id}`,
    extractionMethod: best.method,
  };
}

async function extractCurrentMedia(tab) {
  const direct = classifyDirectMedia(tab?.url || "");
  if (direct) return direct;

  let url;
  try {
    url = new URL(tab?.url || "");
  } catch {
    return null;
  }
  if (!isDouyinHost(url.hostname.toLowerCase()) || !Number.isInteger(tab?.id)) return null;

  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: extractDouyinFromPage,
    });
    return results?.[0]?.result || null;
  } catch (error) {
    console.warn("Douyin extraction failed:", error);
    return null;
  }
}

async function copyCapturedUrl(tabId, url) {
  if (!Number.isInteger(tabId)) return false;
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: async (text) => {
        try {
          await navigator.clipboard.writeText(text);
          return true;
        } catch {
          return false;
        }
      },
      args: [url],
    });
    return Boolean(results?.[0]?.result);
  } catch {
    return false;
  }
}

async function getSettings() {
  return chrome.storage.local.get(DEFAULTS);
}

async function setBadge(text, color, clearAfterMs = 0) {
  await chrome.action.setBadgeBackgroundColor({ color });
  await chrome.action.setBadgeText({ text });
  if (clearAfterMs > 0) {
    setTimeout(() => chrome.action.setBadgeText({ text: "" }), clearAfterMs);
  }
}

async function queueCurrentVideo() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const media = await extractCurrentMedia(tab);
  // Unsupported websites and non-video pages are intentional no-ops. Do not
  // contact the listener, change the badge, open settings, or persist an error.
  if (!media) return { ignored: true };

  await setBadge("…", "#2563eb");
  const clipboardCopied = await copyCapturedUrl(tab.id, media.canonicalUrl);
  const capture = {
    ...media,
    sourceUrl: tab.url || "",
    title: tab.title || "",
    clipboardCopied,
    capturedAt: new Date().toISOString(),
  };
  await chrome.storage.local.set({ lastCapture: capture });

  const settings = await getSettings();
  if (!settings.authToken) {
    await setBadge("CFG", "#b45309", 6000);
    await chrome.runtime.openOptionsPage();
    throw new Error("请先在扩展选项中填写监听器令牌");
  }

  const endpoint = String(settings.endpoint || DEFAULTS.endpoint).replace(/\/+$/, "");
  const timeoutMs = Math.max(1000, Number(settings.requestTimeoutMs) || DEFAULTS.requestTimeoutMs);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(`${endpoint}/api/download`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-YTDLP-Token": settings.authToken,
      },
      body: JSON.stringify({
        url: media.canonicalUrl,
        platform: media.platform,
        contentId: media.contentId,
        sourceUrl: tab.url,
        title: tab.title || "",
        capturedAt: capture.capturedAt,
      }),
      signal: controller.signal,
    });

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || `监听器返回 HTTP ${response.status}`);
    }

    await chrome.storage.local.set({
      lastSubmission: {
        ok: true,
        platform: media.platform,
        contentId: media.contentId,
        jobId: payload.job?.id || "",
        duplicate: Boolean(payload.duplicate),
        at: new Date().toISOString(),
      },
    });
    await setBadge(payload.duplicate ? "＝" : "✓", payload.duplicate ? "#64748b" : "#15803d", 5000);
    return { captured: true, dispatched: true, media, job: payload.job, duplicate: Boolean(payload.duplicate) };
  } catch (error) {
    const message = error?.name === "AbortError" ? "连接监听器超时" : String(error?.message || error);
    await chrome.storage.local.set({
      lastSubmission: {
        ok: false,
        platform: media.platform,
        contentId: media.contentId,
        error: message,
        at: new Date().toISOString(),
      },
    });
    await setBadge("!", "#b91c1c", 7000);
    console.error("Local video download queue failed:", message);
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

chrome.commands.onCommand.addListener((command) => {
  if (command === "download-current-video") {
    queueCurrentVideo().catch(() => {});
  }
});

chrome.action.onClicked.addListener(() => {
  queueCurrentVideo().catch(() => {});
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "queue-current-video") return false;
  queueCurrentVideo()
    .then((result) => sendResponse({ ok: true, result }))
    .catch((error) => sendResponse({ ok: false, error: String(error?.message || error) }));
  return true;
});

chrome.runtime.onInstalled.addListener(({ reason }) => {
  if (reason === "install") chrome.runtime.openOptionsPage();
});
