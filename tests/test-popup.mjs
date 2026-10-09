import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

class TestElement {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.className = "";
    this.textContent = "";
    this.title = "";
    this.style = {};
    this.disabled = false;
    this.tabIndex = -1;
    this.classList = {
      values: new Set(),
      add: (...names) => names.forEach((name) => this.classList.values.add(name)),
      remove: (...names) => names.forEach((name) => this.classList.values.delete(name)),
      contains: (name) => this.classList.values.has(name),
    };
  }

  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
}

const testDir = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(testDir, "..", "extension", "popup.js"), "utf8");
const elements = new Map([
  ["#summary", new TestElement("section")],
  ["#jobs", new TestElement("main")],
  ["#connection", new TestElement("p")],
  ["#message", new TestElement("p")],
  ["#download-current", new TestElement("button")],
  ["#refresh", new TestElement("button")],
  ["#settings", new TestElement("button")],
]);
const requests = [];
const context = vm.createContext({
  AbortController,
  URL,
  console,
  clearInterval() {},
  clearTimeout,
  setInterval: () => 1,
  setTimeout,
  document: {
    createElement: (tagName) => new TestElement(tagName),
    querySelector: (selector) => elements.get(selector),
  },
  window: { addEventListener() {} },
  chrome: {
    action: {
      setBadgeBackgroundColor: async () => {},
      setBadgeText: async () => {},
    },
    runtime: {
      openOptionsPage: async () => {},
      sendMessage: async () => ({ ok: true, result: {} }),
    },
    storage: {
      local: {
        get: async () => ({ endpoint: "http://127.0.0.1:17392", authToken: "test", requestTimeoutMs: 1000 }),
      },
    },
  },
  fetch: async (url, options = {}) => {
    requests.push({ url, options });
    return {
      ok: true,
      json: async () => url.includes("/api/jobs?")
        ? { summary: { active: 0, queueDepth: 0, counts: {}, limits: { total: 3 } }, jobs: [] }
        : { ok: true },
    };
  },
});

vm.runInContext(source, context, { filename: "popup.js" });

const card = context.renderJob({
  id: "a".repeat(32),
  platform: "youtube",
  status: "completed",
  title: "Finished video",
  url: "https://www.youtube.com/watch?v=3IDn1iMxblo",
  output_path: "C:\\Videos\\finished.mp4",
  output_paths: ["C:\\Videos\\finished.mp4"],
  progress_percent: 100,
  eta_seconds: 0,
});

assert.equal(card.attributes.role, "button");
assert.equal(card.tabIndex, 0);
assert.equal(typeof card.listeners.click, "function");
const head = card.children[0];
const headActions = head.children[1];
const revealButton = headActions.children[1];
assert.equal(revealButton.textContent, "打开位置");
assert.equal(typeof revealButton.listeners.click, "function");

await context.runOutputAction("a".repeat(32), "open", card);
await context.runOutputAction("a".repeat(32), "reveal", revealButton);
assert.ok(requests.some(({ url, options }) => url.endsWith(`/api/jobs/${"a".repeat(32)}/open`) && options.method === "POST"));
assert.ok(requests.some(({ url, options }) => url.endsWith(`/api/jobs/${"a".repeat(32)}/reveal`) && options.method === "POST"));

console.log("popup completed-job actions: OK");
