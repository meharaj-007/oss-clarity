/**
 * The recorder's uploads, run in Node with rrweb stubbed out.
 *
 * rrweb needs a real DOM, and what is under test is not rrweb but what the
 * recorder does with the events it is given: above all, that a page being
 * hidden or closed starts its uploads before returning, since it may get no
 * further turn of the event loop.
 *
 *   node --test tests/
 */

import assert from "node:assert/strict";
import { dirname, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import { build } from "esbuild";

const here = dirname(fileURLToPath(import.meta.url));

const RRWEB_STUB = `
export function record(options) {
  globalThis.__emit = options.emit;
  return () => {};
}
record.addCustomEvent = () => {};
`;

const bundle = (
  await build({
    entryPoints: [resolve(here, "../recorder/index.ts")],
    bundle: true,
    write: false,
    format: "iife",
    target: ["es2019"],
    logLevel: "silent",
    plugins: [
      {
        name: "rrweb-stub",
        setup(b) {
          b.onResolve({ filter: /^@rrweb\/record$/ }, () => ({ path: "record", namespace: "stub" }));
          b.onLoad({ filter: /.*/, namespace: "stub" }, () => ({ contents: RRWEB_STUB, loader: "js" }));
        },
      },
    ],
  })
).outputFiles[0].text;

const CONFIG = {
  endpoint: "https://collect.example.net/oc/r/",
  key: "AbCdEfGhIjKlMnOpQrStUv",
  sessionId: "0f8d6c1e-4b7a-4c55-9d7e-3a2b1c0d9e8f",
  visitorId: "5a4b3c2d-1e0f-4a9b-8c7d-6e5f4a3b2c1d",
  pageId: "9e8d7c6b-5a4f-4e3d-2c1b-0a9f8e7d6c5b",
  pageSeq: 2,
  maskMode: "balanced",
  mask: [],
  unmask: [],
  block: [],
  maxBytes: 10_000_000,
  bytesUsed: 0,
  maxMs: 0,
};

/** A fresh recorder in its own context, started, with a fetch that logs. */
function page() {
  const listeners = {};
  const docListeners = {};
  const sent = [];
  const sandbox = {
    TextEncoder,
    Blob,
    Response,
    CompressionStream,
    Uint8Array,
    Promise,
    // The recorder's idle and flush timers must not keep the test process alive.
    setTimeout: (fn, ms) => setTimeout(fn, ms).unref(),
    clearTimeout,
    location: { href: "https://shop.example/pricing/" },
    addEventListener: (name, fn) => (listeners[name] = listeners[name] || []).push(fn),
    removeEventListener: () => {},
    document: {
      visibilityState: "visible",
      addEventListener: (name, fn) => (docListeners[name] = docListeners[name] || []).push(fn),
      removeEventListener: () => {},
    },
    fetch: (url, options) => {
      sent.push({ url, options });
      return Promise.resolve();
    },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(bundle, sandbox);
  sandbox.__ossClarityRecorder.start({ ...CONFIG });
  let ts = 1_700_000_000_000;
  return {
    sent,
    recorder: sandbox.__ossClarityRecorder,
    emit: (data) => sandbox.__emit({ type: 3, timestamp: ts++, data }),
    pagehide: () => (listeners.pagehide || []).forEach((fn) => fn({})),
    hide: () => {
      sandbox.document.visibilityState = "hidden";
      (docListeners.visibilitychange || []).forEach((fn) => fn({}));
    },
  };
}

const body = (request) => JSON.parse(request.options.body);
const bytes = (text) => new TextEncoder().encode(text).length;

test("closing the page sends the buffer before the handler returns", () => {
  const p = page();
  for (let i = 0; i < 3; i++) p.emit({ source: 2, type: 2, id: i, x: 10, y: 20 });
  p.pagehide();

  assert.equal(p.sent.length, 1);
  const [request] = p.sent;
  assert.equal(request.options.keepalive, true);
  assert.equal(request.options.headers["Content-Encoding"], undefined);
  const chunk = body(request);
  assert.equal(chunk.final, true);
  assert.equal(chunk.page_seq, 2);
  assert.equal(chunk.chunk_seq, 0);
  assert.deepEqual(
    chunk.events.map((e) => e.data.id),
    [0, 1, 2],
  );
});

test("hiding the page sends the buffer before the handler returns", () => {
  const p = page();
  p.emit({ source: 3, id: 1, x: 0, y: 400 });
  p.hide();

  assert.equal(p.sent.length, 1);
  assert.equal(p.sent[0].options.keepalive, true);
  assert.equal(body(p.sent[0]).final, false);
  // Nothing is left for the page leave that follows.
  p.pagehide();
  assert.equal(p.sent.length, 1);
});

test("a buffer over the keepalive budget is split, in order, within it", () => {
  const p = page();
  // Under the recorder's own flush size in characters, but three bytes a
  // character once encoded: about 150 KB.
  for (let i = 0; i < 50; i++) p.emit({ source: 0, id: i, text: "•".repeat(1000) });
  assert.equal(p.sent.length, 0);
  p.pagehide();

  assert.ok(p.sent.length > 1);
  const chunks = p.sent.map(body);
  assert.deepEqual(
    chunks.map((c) => c.chunk_seq),
    chunks.map((_, i) => i),
  );
  assert.deepEqual(
    chunks.flatMap((c) => c.events.map((e) => e.data.id)),
    [...Array(50).keys()],
  );
  assert.deepEqual(
    chunks.map((c) => c.final),
    chunks.map((_, i) => i === chunks.length - 1),
  );
  for (const request of p.sent) assert.ok(bytes(request.options.body) <= 60_000);
  const kept = p.sent.filter((r) => r.options.keepalive);
  assert.ok(kept.length >= 1);
  assert.ok(kept.reduce((sum, r) => sum + bytes(r.options.body), 0) <= 60_000);
});

test("while the page is open a chunk is gzipped", async () => {
  const p = page();
  p.emit({ source: 2, type: 2, id: 1, x: 10, y: 20 });
  p.recorder.stop("time");
  await new Promise((done) => setTimeout(done, 50));

  assert.equal(p.sent.length, 1);
  assert.equal(p.sent[0].options.headers["Content-Encoding"], "gzip");
  assert.equal(p.sent[0].options.keepalive, true);
});

test("withdrawn consent sends nothing, not even on leaving", () => {
  const p = page();
  p.emit({ source: 2, type: 2, id: 1, x: 10, y: 20 });
  p.recorder.stop("consent");
  p.pagehide();
  assert.equal(p.sent.length, 0);
});
