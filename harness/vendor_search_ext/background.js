"use strict";
// Vendor Search price log (harness/vendor_search.py, docs/NOTES.md "Vendor Search capture").
// Copies the Vendor Search API responses the portal page itself requested and hands them to the
// native messaging host, which writes them into the memory store. It never sends a portal
// request: the response stream is passed through unchanged, so page and server see nothing new.

const HOST = "uo_vendor_prices";
const URLS = ["https://portal.uooutlands.com/*"];
const API = /^https:\/\/portal\.uooutlands\.com(?::\d+)?\/(?:prod-)?api\/(?:VendorSearch|PriceHistory)\/([A-Za-z]+)/i;
const SKIP = new Set(["itemnameautocomplete", "vendorautocomplete", "vendorsearchstatus"]);
const QUEUE_MAX = 200;        // captures kept while the host is unreachable; the oldest go first

const inflight = new Map();   // requestId -> capture being assembled
let queue = [];               // captures the host hasn't stored yet, oldest first (persisted)
let flushing = false;
const tally = { captures: 0, rows: 0, dropped: 0, last: null, error: null };

function bodyText(rb) {
  if (!rb) return null;
  if (rb.raw) {
    const dec = new TextDecoder("utf-8");
    return rb.raw.map((p) => (p.bytes ? dec.decode(p.bytes, { stream: true }) : "")).join("") + dec.decode();
  }
  if (rb.formData) return JSON.stringify(rb.formData);
  return null;
}

browser.webRequest.onBeforeRequest.addListener(
  (details) => {
    const m = API.exec(details.url);
    if (!m || SKIP.has(m[1].toLowerCase()) || details.method === "OPTIONS") return {};
    const cap = {
      type: "capture",
      url: details.url,
      method: details.method,
      page: details.documentUrl || details.originUrl || null,
      request: bodyText(details.requestBody),
      status: null,
    };
    inflight.set(details.requestId, cap);
    const filter = browser.webRequest.filterResponseData(details.requestId);
    const decoder = new TextDecoder("utf-8");
    let text = "";
    filter.ondata = (e) => {
      filter.write(e.data); // the page gets every byte first, unchanged
      text += decoder.decode(e.data, { stream: true });
    };
    filter.onstop = () => {
      filter.close();
      inflight.delete(details.requestId);
      cap.t = Date.now() / 1000;
      cap.response = text + decoder.decode();
      if (cap.status >= 200 && cap.status < 300 && cap.response) enqueue(cap);
    };
    filter.onerror = () => inflight.delete(details.requestId);
    return {};
  },
  { urls: URLS, types: ["xmlhttprequest"] },
  ["blocking", "requestBody"]
);

browser.webRequest.onHeadersReceived.addListener(
  (details) => {
    const cap = inflight.get(details.requestId);
    if (cap) cap.status = details.statusCode;
  },
  { urls: URLS, types: ["xmlhttprequest"] }
);

function enqueue(cap) {
  queue.push(cap);
  while (queue.length > QUEUE_MAX) {
    queue.shift();
    tally.dropped++;
  }
  persist();
  flush();
}

function persist() {
  return browser.storage.local.set({ queue }).catch((e) => {
    tally.error = `queue not saved: ${e.message || e}`;
  });
}

async function flush() {
  if (flushing) return;
  flushing = true;
  try {
    while (queue.length) {
      let reply;
      try {
        reply = await browser.runtime.sendNativeMessage(HOST, queue[0]);
      } catch (e) {
        tally.error = `host: ${e.message || e}`; // not installed or failed to start: keep the queue
        break;
      }
      if (reply && reply.ok) {
        queue.shift();
        tally.captures++;
        tally.rows += reply.rows || 0;
        tally.last = `${reply.endpoint}: ${reply.rows} price rows (capture ${reply.capture})`;
        tally.error = null;
        continue;
      }
      tally.error = (reply && reply.error) || "no reply from the host";
      if (reply && reply.retry === false) {
        queue.shift(); // the host can never store this one (not JSON, ...): don't block the rest
        tally.dropped++;
        continue;
      }
      break; // the store is busy or broken: retry with the next capture or a toolbar click
    }
  } finally {
    flushing = false;
    await persist();
    show();
  }
}

function show() {
  const bad = Boolean(tally.error);
  const n = bad ? queue.length : tally.captures;
  browser.browserAction.setBadgeText({ text: bad ? String(n || "!") : n ? (n > 999 ? "999+" : String(n)) : "" });
  browser.browserAction.setBadgeBackgroundColor({ color: bad ? "#c62828" : "#2e7d32" });
  const lines = [
    "Vendor Search price log",
    `saved this session: ${tally.captures} responses, ${tally.rows} price rows`,
    `queued: ${queue.length}${tally.dropped ? `, dropped: ${tally.dropped}` : ""}`,
  ];
  if (tally.last) lines.push(`last: ${tally.last}`);
  if (bad) lines.push(`error: ${tally.error}`, "click to retry");
  browser.browserAction.setTitle({ title: lines.join("\n") });
}

browser.browserAction.onClicked.addListener(() => flush());

browser.storage.local.get("queue").then((r) => {
  queue = (r.queue || []).concat(queue);
  show();
  flush();
});
