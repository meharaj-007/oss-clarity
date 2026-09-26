/**
 * The oss-clarity recorder: rrweb's `record`, driven by the tracker.
 *
 * The tracker decides whether a visit is recorded and loads this file only
 * then. This file:
 *
 *   - takes a full snapshot of the page, then streams mutations and
 *     interactions, masked in the browser according to the site's mode;
 *   - strips `data:` and `blob:` sources from every element before an event is
 *     buffered, in every mode, keeping the element's box;
 *   - cuts the stream into chunks every FLUSH_MS or FLUSH_BYTES, gzips them
 *     where the browser can, and POSTs them as text/plain;
 *   - stops at the session's byte and time caps, and pauses after IDLE_MS
 *     without interaction so a tab left open does not spend the budget;
 *   - flushes on `pagehide` with `keepalive`, which is why a chunk is kept
 *     under the 64 KB keepalive ceiling.
 *
 * Built by `npm run build` into src/oss_clarity/static/oss_clarity/recorder.js,
 * which is committed.
 */

import { record } from "@rrweb/record";
import { EventType, IncrementalSource, type eventWithTime, type listenerHandler } from "@rrweb/types";

type MaskMode = "strict" | "balanced" | "relaxed";

export type RecorderConfig = {
  endpoint: string;
  key: string;
  sessionId: string;
  visitorId: string;
  pageId: string;
  pageSeq: number;
  maskMode: MaskMode;
  mask: string[];
  unmask: string[];
  block: string[];
  maxBytes: number;
  bytesUsed: number;
  maxMs: number;
  onBytes?: (total: number) => void;
};

/** A replay close to live, few requests per visit, and chunks under the
 * keepalive ceiling even before compression. */
const FLUSH_MS = 5_000;
const FLUSH_BYTES = 60_000;
/** No interaction for this long and the recorder pauses; the next
 * interaction resumes it with a fresh snapshot. */
const IDLE_MS = 5 * 60_000;
/** Script errors written into the stream per page. */
const MAX_ERRORS_PER_PAGE = 5;
const ERROR_TAG = "oss_clarity.error";

const MASK_CHAR = "•";
const DIGIT_CHAR = "▫";
const LETTER_CHAR = "▪";

const UNMASK_ATTR = "data-oss-clarity-unmask";
const MASK_ATTR = "data-oss-clarity-mask";

// Unicode-aware, so every script's letters and digits are masked alike.
let LETTER_RE: RegExp;
let DIGIT_RE: RegExp;
try {
  LETTER_RE = new RegExp("\\p{L}", "gu");
  DIGIT_RE = new RegExp("\\p{N}", "gu");
} catch {
  LETTER_RE = /[A-Za-z]/g;
  DIGIT_RE = /[0-9]/g;
}

let stopRecord: listenerHandler | null = null;
let config: RecorderConfig | null = null;
let buffer: eventWithTime[] = [];
let bufferBytes = 0;
let bytesThisSession = 0;
let chunkSeq = 0;
let flushTimer: number | null = null;
let idleTimer: number | null = null;
let stopTimer: number | null = null;
let stopped = false;
let paused = false;
let errorsThisPage = 0;
let inflight = 0;

// ---------------------------------------------------------------------------
// Masking
// ---------------------------------------------------------------------------

/** Balanced: digits become ▫; a word containing @ has its letters become ▪
 * too; everything else stays. Lengths are kept so the layout does not move. */
function redactBalanced(text: string): string {
  if (!text) return text;
  return text
    .split(/(\s+)/)
    .map((part) => {
      if (!part || /^\s+$/.test(part)) return part;
      if (part.indexOf("@") !== -1) return part.replace(LETTER_RE, LETTER_CHAR).replace(DIGIT_RE, DIGIT_CHAR);
      return part.replace(DIGIT_RE, DIGIT_CHAR);
    })
    .join("");
}

function maskAll(text: string): string {
  return text.replace(/\S/g, MASK_CHAR);
}

function closestMatch(element: HTMLElement | null, selectors: string[]): boolean {
  if (!element || typeof element.closest !== "function") return false;
  for (const selector of selectors) {
    try {
      if (element.closest(selector)) return true;
    } catch {
      /* an invalid selector matches nothing */
    }
  }
  return false;
}

function maskTextFor(mode: MaskMode, mask: string[], unmask: string[]) {
  return (text: string, element: HTMLElement | null): string => {
    // Explicit wins: the page's attributes first, then the site's lists.
    if (element) {
      if (element.closest(`[${UNMASK_ATTR}]`)) return text;
      if (element.closest(`[${MASK_ATTR}]`)) return maskAll(text);
      if (closestMatch(element, unmask)) return text;
      if (closestMatch(element, mask)) return maskAll(text);
    }
    if (mode === "strict") return maskAll(text);
    if (mode === "balanced") return redactBalanced(text);
    return text;
  };
}

function blockSelectorFor(mode: MaskMode, block: string[]): string {
  const parts = [`[${MASK_ATTR}] img`, `[${MASK_ATTR}] video`, ...block];
  if (mode === "strict") parts.push("img", "video", "canvas", "picture", "svg image");
  return parts.join(", ");
}

// ---------------------------------------------------------------------------
// Inline sources
// ---------------------------------------------------------------------------
//
// A `data:` or `blob:` source is bytes made in the browser, most often the
// preview of a photo the visitor just chose in an upload form. Text masking
// does not touch it and rrweb would record it whole. So in every mode such an
// element is recorded like a blocked one: its box and nothing else. The page's
// own stylesheets keep their `data:` fonts and icons, which are the site's.

const SOURCE_ATTRS = ["src", "srcset", "poster", "data"];
const INLINE_RE = /(?:^|,)\s*(?:data|blob):/i;
const ELEMENT_NODE = 2;

type SerializedNode = {
  id: number;
  type: number;
  attributes?: Record<string, unknown>;
  childNodes?: SerializedNode[];
};

function isInline(value: unknown): boolean {
  return typeof value === "string" && INLINE_RE.test(value.trim());
}

/** The live element's box, read through rrweb's id map. */
function boxOf(id: number): { width: number; height: number } | null {
  try {
    const mirror = (record as unknown as { mirror: { getNode(id: number): Node | null } }).mirror;
    const node = mirror.getNode(id) as Element | null;
    if (node && typeof node.getBoundingClientRect === "function") {
      const { width, height } = node.getBoundingClientRect();
      return { width, height };
    }
  } catch {
    /* node already gone */
  }
  return null;
}

function stripNode(node: SerializedNode): void {
  const attrs = node.attributes;
  if (node.type === ELEMENT_NODE && attrs && SOURCE_ATTRS.some((name) => isInline(attrs[name]))) {
    const box = boxOf(node.id);
    // The shape rrweb gives a blocked element.
    const blocked: Record<string, unknown> = {
      rr_width: `${box ? box.width : 0}px`,
      rr_height: `${box ? box.height : 0}px`,
    };
    if (typeof attrs.class === "string") blocked.class = attrs.class;
    node.attributes = blocked;
    node.childNodes = [];
    return;
  }
  if (node.childNodes) for (const child of node.childNodes) stripNode(child);
}

function stripInlineSources(event: eventWithTime): void {
  try {
    const data = event.data as {
      node?: SerializedNode;
      source?: number;
      adds?: { node: SerializedNode }[];
      attributes?: { id: number; attributes: Record<string, unknown> }[];
    };
    if (event.type === EventType.FullSnapshot && data.node) {
      stripNode(data.node);
      return;
    }
    if (event.type !== EventType.IncrementalSnapshot || data.source !== IncrementalSource.Mutation) return;
    for (const add of data.adds ?? []) stripNode(add.node);
    // The common case: an <img> already on the page gets a blob URL once the
    // visitor picks a file. The attribute is removed and the box kept.
    for (const change of data.attributes ?? []) {
      let hit = false;
      for (const name of SOURCE_ATTRS) {
        if (isInline(change.attributes[name])) {
          change.attributes[name] = null;
          hit = true;
        }
      }
      if (hit && !("style" in change.attributes)) {
        const box = boxOf(change.id);
        if (box) change.attributes.style = { width: `${box.width}px`, height: `${box.height}px` };
      }
    }
  } catch {
    /* an unknown event shape is left alone rather than dropped */
  }
}

// ---------------------------------------------------------------------------
// Upload
// ---------------------------------------------------------------------------

async function gzip(text: string): Promise<Uint8Array | null> {
  try {
    const CS = (window as unknown as { CompressionStream?: typeof CompressionStream }).CompressionStream;
    if (!CS) return null;
    const stream = new Blob([text]).stream().pipeThrough(new CS("gzip"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  } catch {
    return null;
  }
}

async function send(events: eventWithTime[], final: boolean, stopReason: string): Promise<void> {
  if (!config || events.length === 0) return;
  const seq = chunkSeq++;
  const payload = JSON.stringify({
    key: config.key,
    session_id: config.sessionId,
    visitor_id: config.visitorId,
    page_id: config.pageId,
    page_seq: config.pageSeq,
    chunk_seq: seq,
    url: window.location.href,
    final,
    stop_reason: stopReason,
    events,
  });

  const headers: Record<string, string> = { "Content-Type": "text/plain" };
  let body: BodyInit = payload;
  // The last flush must survive the page closing, which only keepalive
  // promises, and keepalive refuses bodies over 64 KB: compress first.
  const compressed = await gzip(payload);
  if (compressed) {
    body = compressed as unknown as BodyInit;
    headers["Content-Encoding"] = "gzip";
  }
  const size = typeof body === "string" ? body.length : (body as Uint8Array).byteLength;
  const useKeepalive = final && size < 62_000;

  inflight++;
  try {
    await fetch(config.endpoint, {
      method: "POST",
      body,
      headers,
      mode: "cors",
      credentials: "omit",
      keepalive: useKeepalive,
    });
  } catch {
    /* blocked or offline: never surface it */
  } finally {
    inflight--;
  }
}

function flush(final = false, stopReason = ""): void {
  if (flushTimer !== null) {
    window.clearTimeout(flushTimer);
    flushTimer = null;
  }
  const events = buffer;
  buffer = [];
  bufferBytes = 0;
  void send(events, final, stopReason);
}

function scheduleFlush(): void {
  if (flushTimer !== null) return;
  flushTimer = window.setTimeout(() => {
    flushTimer = null;
    flush();
  }, FLUSH_MS);
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

function emit(event: eventWithTime): void {
  if (!config || stopped || paused) return;
  stripInlineSources(event);
  const size = JSON.stringify(event).length;
  if (bytesThisSession + size > config.maxBytes) {
    stop("bytes");
    return;
  }
  bytesThisSession += size;
  bufferBytes += size;
  buffer.push(event);
  if (config.onBytes) {
    try {
      config.onBytes(bytesThisSession);
    } catch {
      /* storage full: the server enforces the cap anyway */
    }
  }
  if (bufferBytes >= FLUSH_BYTES) flush();
  else scheduleFlush();
}

function startRrweb(): void {
  if (!config) return;
  const mode = config.maskMode;
  stopRecord =
    record({
      emit,
      // Form fields are masked in every mode. This is the floor, not a setting.
      maskAllInputs: true,
      // Marks each input with whether a person caused it, so scripts filling
      // fields are not mistaken for typing.
      userTriggeredOnInput: true,
      maskTextSelector: "*",
      maskTextFn: maskTextFor(mode, config.mask, config.unmask),
      blockSelector: blockSelectorFor(mode, config.block),
      // Scripts and comments never render; leaving them out saves bytes.
      slimDOMOptions: { script: true, comment: true, headMetaDescKeywords: true },
      inlineStylesheet: true,
      sampling: {
        mousemove: 50,
        scroll: 150,
        input: "last",
        media: 800,
        mouseInteraction: {
          MouseUp: false,
          MouseDown: false,
          Click: true,
          ContextMenu: true,
          DblClick: true,
          Focus: true,
          Blur: true,
          TouchStart: true,
          TouchEnd: true,
        },
      },
    }) ?? null;
}

function pauseForIdle(): void {
  if (stopped || paused || !stopRecord) return;
  paused = true;
  flush();
  stopRecord();
  stopRecord = null;
}

function resumeFromIdle(): void {
  if (stopped || !paused) return;
  paused = false;
  // A fresh snapshot: the page may have changed while nothing was recorded.
  startRrweb();
}

function touch(): void {
  if (idleTimer !== null) window.clearTimeout(idleTimer);
  idleTimer = window.setTimeout(pauseForIdle, IDLE_MS);
  if (paused) resumeFromIdle();
}

const ACTIVITY_EVENTS = ["pointerdown", "keydown", "scroll", "touchstart", "mousemove"];

function start(next: RecorderConfig): void {
  if (config) return;
  config = next;
  bytesThisSession = next.bytesUsed || 0;
  stopped = false;
  paused = false;
  chunkSeq = 0;
  errorsThisPage = 0;

  startRrweb();
  touch();
  for (const name of ACTIVITY_EVENTS) {
    window.addEventListener(name, touch, { capture: true, passive: true });
  }
  if (next.maxMs > 0) {
    stopTimer = window.setTimeout(() => stop("time"), next.maxMs);
  }
  window.addEventListener("pagehide", onPageHide);
  document.addEventListener("visibilitychange", onVisibility);
}

function onPageHide(): void {
  flush(true, "");
}

function onVisibility(): void {
  if (document.visibilityState === "hidden") flush();
}

function stop(reason: string): void {
  if (stopped) return;
  stopped = true;
  if (stopRecord) {
    stopRecord();
    stopRecord = null;
  }
  if (idleTimer !== null) window.clearTimeout(idleTimer);
  if (stopTimer !== null) window.clearTimeout(stopTimer);
  for (const name of ACTIVITY_EVENTS) {
    window.removeEventListener(name, touch, { capture: true } as EventListenerOptions);
  }
  window.removeEventListener("pagehide", onPageHide);
  document.removeEventListener("visibilitychange", onVisibility);
  // Consent withdrawn: nothing more may leave the page, not even the buffer.
  if (reason === "consent") {
    buffer = [];
    bufferBytes = 0;
    return;
  }
  flush(true, reason === "bytes" || reason === "time" ? reason : "");
}

function custom(tag: string, payload: Record<string, unknown>): void {
  if (!config || stopped || paused) return;
  if (tag === ERROR_TAG) {
    if (errorsThisPage >= MAX_ERRORS_PER_PAGE) return;
    errorsThisPage++;
  }
  try {
    record.addCustomEvent(tag, payload);
  } catch {
    /* recorder not running */
  }
}

declare global {
  interface Window {
    __ossClarityRecorder?: {
      start: (config: RecorderConfig) => void;
      stop: (reason: string) => void;
      custom: (tag: string, payload: Record<string, unknown>) => void;
      inflight: () => number;
    };
  }
}

window.__ossClarityRecorder = {
  start,
  stop,
  custom,
  inflight: () => inflight,
};
