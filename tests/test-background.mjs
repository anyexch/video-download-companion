import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const testDir = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(testDir, "..", "extension", "background.js"), "utf8");
const event = { addListener() {} };
const context = vm.createContext({
  URL,
  console,
  setTimeout,
  clearTimeout,
  chrome: {
    action: {
      onClicked: event,
      setBadgeBackgroundColor: async () => {},
      setBadgeText: async () => {},
    },
    commands: { onCommand: event },
    runtime: { onInstalled: event, onMessage: event, openOptionsPage: async () => {} },
    storage: { local: { get: async () => ({}), set: async () => {} } },
    tabs: { query: async () => [] },
  },
});
vm.runInContext(source, context, { filename: "background.js" });

const classify = context.classifyDirectMedia;
assert.equal(typeof classify, "function");

assert.deepEqual(
  JSON.parse(JSON.stringify(classify("https://www.youtube.com/watch?v=3IDn1iMxblo&t=4s"))),
  {
    platform: "youtube",
    contentId: "3IDn1iMxblo",
    canonicalUrl: "https://www.youtube.com/watch?v=3IDn1iMxblo",
    extractionMethod: "page-url",
  },
);
assert.deepEqual(
  JSON.parse(JSON.stringify(classify("https://www.douyin.com/jingxuan?modal_id=7666034873747967241"))),
  {
    platform: "douyin",
    contentId: "7666034873747967241",
    contentType: "video",
    canonicalUrl: "https://www.douyin.com/video/7666034873747967241",
    extractionMethod: "modal-id",
  },
);
assert.deepEqual(
  JSON.parse(JSON.stringify(classify("https://www.douyin.com/note/7652577852285603081?previous_page=search"))),
  {
    platform: "douyin",
    contentId: "7652577852285603081",
    contentType: "note",
    canonicalUrl: "https://www.douyin.com/note/7652577852285603081",
    extractionMethod: "page-path",
  },
);
assert.deepEqual(
  JSON.parse(JSON.stringify(classify("https://www.bilibili.com/video/BV1xx411c7mD?p=2&spm_id_from=tracking"))),
  {
    platform: "bilibili",
    contentId: "BV1xx411c7mD",
    canonicalUrl: "https://www.bilibili.com/video/BV1xx411c7mD?p=2",
    extractionMethod: "page-url",
  },
);
assert.deepEqual(
  JSON.parse(JSON.stringify(classify("https://www.bilibili.com/bangumi/play/ep123456?from=tracking"))),
  {
    platform: "bilibili",
    contentId: "ep123456",
    canonicalUrl: "https://www.bilibili.com/bangumi/play/ep123456",
    extractionMethod: "page-url",
  },
);

for (const url of [
  "https://www.douyin.com/jingxuan",
  "https://www.bilibili.com/",
  "https://search.bilibili.com/all?keyword=test",
  "https://www.youtube.com/",
  "https://example.com/video/BV1xx411c7mD",
]) {
  assert.equal(classify(url), null, `Expected silent rejection for ${url}`);
}

console.log("background site gate tests: OK");
