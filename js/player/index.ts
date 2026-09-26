/**
 * The oss-clarity viewer: a session replay and a heatmap, for the Django admin.
 *
 * Framework-free. On load it mounts itself on every element carrying
 * `data-oss-clarity-replay="<url>"` or `data-oss-clarity-heatmap="<url>"`, where
 * the URL returns the JSON the admin views serve. Pages are rebuilt by rrweb's
 * Replayer inside an iframe it sandboxes without scripts, so nothing from a
 * recorded site runs here.
 *
 * Built by `npm run build` into src/oss_clarity/static/oss_clarity/player.js
 * and player.css, which are committed.
 */

import "@rrweb/replay/dist/style.css";
import "./player.css";

import { Replayer } from "@rrweb/replay";
import type { eventWithTime } from "@rrweb/types";

type Marker = {
  t_ms: number;
  page_id: string;
  kind: string;
  selector: string;
  label: string;
  x: number | null;
  y: number | null;
};

type ReplayPage = { page_seq: number; page_id: string; url: string; path: string; events_url: string };

type ReplayData = {
  id: string;
  status: string;
  stop_reason: string;
  mask_mode: string;
  duration_ms: number;
  markers: Marker[];
  pages: ReplayPage[];
};

const KIND_NAMES: Record<string, string> = {
  rage: "Rage click",
  dead: "Dead click",
  error: "Error click",
  script_error: "Script error",
  hesitation: "Hesitation",
  near_miss: "Near miss",
  scroll_hunt: "Scroll hunt",
  form_skip: "Form skip",
  form_refill: "Form refill",
  form_abandon: "Form abandon",
  repeat_submit: "Repeat submit",
  copy_out: "Copy-out",
  idle_exit: "Idle exit",
};

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Record<string, string> = {},
  ...children: (Node | string)[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (name === "class") node.className = value;
    else node.setAttribute(name, value);
  }
  for (const child of children) node.append(child);
  return node;
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return (await response.json()) as T;
}

function clock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function fail(root: HTMLElement, message: string): void {
  root.replaceChildren(el("p", { class: "oc-error" }, message));
}

/** Scale Replayer's wrapper to fit `stage`, never above 1. */
function fit(replayer: Replayer, stage: HTMLElement, width: number, height: number): void {
  const scale = Math.min(1, stage.clientWidth / Math.max(1, width));
  replayer.wrapper.style.transform = `scale(${scale})`;
  replayer.wrapper.style.transformOrigin = "0 0";
  stage.style.height = `${Math.ceil(height * scale)}px`;
}

/**
 * Remove inline event handlers (`onclick`, `onsubmit`...) from recorded
 * nodes. The replay iframe runs no scripts anyway; this is a second layer,
 * and it keeps the browser from logging every blocked handler.
 */
type Serialized = { attributes?: Record<string, unknown>; childNodes?: Serialized[] };

function stripHandlers(node: Serialized | undefined): void {
  if (!node) return;
  if (node.attributes) {
    for (const name of Object.keys(node.attributes)) {
      if (/^on/i.test(name)) delete node.attributes[name];
    }
  }
  for (const child of node.childNodes ?? []) stripHandlers(child);
}

function sanitize(events: eventWithTime[]): eventWithTime[] {
  for (const event of events) {
    const data = event.data as {
      node?: Serialized;
      source?: number;
      adds?: { node: Serialized }[];
      attributes?: { attributes: Record<string, unknown> }[];
    };
    if (event.type === 2) stripHandlers(data.node);
    else if (event.type === 3 && data.source === 0) {
      for (const add of data.adds ?? []) stripHandlers(add.node);
      for (const change of data.attributes ?? []) stripHandlers(change);
    }
  }
  return events;
}

// ---------------------------------------------------------------------------
// Replay
// ---------------------------------------------------------------------------

async function mountReplay(root: HTMLElement): Promise<void> {
  const url = root.dataset.ossClarityReplay;
  if (!url) return;
  let data: ReplayData;
  let events: eventWithTime[] = [];
  let missingPages = 0;
  try {
    data = await getJson<ReplayData>(url);
    for (const page of data.pages) {
      try {
        const body = await getJson<{ events: eventWithTime[] }>(page.events_url);
        events = events.concat(body.events);
      } catch {
        missingPages++;
      }
    }
  } catch (error) {
    fail(root, `The replay could not be loaded (${(error as Error).message}).`);
    return;
  }
  events = sanitize(events).sort((a, b) => a.timestamp - b.timestamp);
  if (events.length < 2 || !events.some((e) => e.type === 2)) {
    fail(root, "This recording has no replayable page yet.");
    return;
  }

  const stage = el("div", { class: "oc-stage" });
  const play = el("button", { type: "button", class: "button oc-play" }, "Play");
  const time = el("span", { class: "oc-time" }, "0:00");
  const scrubber = el("input", { type: "range", min: "0", value: "0", step: "100", class: "oc-scrubber" });
  const ticks = el("div", { class: "oc-ticks" });
  const speed = el("select", { class: "oc-speed", "aria-label": "Speed" });
  for (const value of [1, 2, 4, 8]) speed.append(el("option", { value: String(value) }, `${value}×`));
  const skip = el("input", { type: "checkbox", checked: "" });
  const notices = el("ul", { class: "oc-notices" });
  const markerList = el("ol", { class: "oc-markers" });
  const pageList = el("ol", { class: "oc-pages" });

  root.replaceChildren(
    stage,
    el(
      "div",
      { class: "oc-controls" },
      play,
      el("div", { class: "oc-track" }, scrubber, ticks),
      time,
      speed,
      el("label", { class: "oc-skip" }, skip, " Skip idle time"),
    ),
    notices,
    el(
      "div",
      { class: "oc-lists" },
      el("section", {}, el("h3", {}, "Signals"), markerList),
      el("section", {}, el("h3", {}, "Pages"), pageList),
    ),
  );

  const replayer = new Replayer(events, {
    root: stage,
    skipInactive: true,
    showWarning: false,
    mouseTail: false,
    UNSAFE_replayCanvas: false,
  });
  const meta = replayer.getMetaData();
  const total = meta.totalTime;
  const origin = meta.startTime;
  scrubber.max = String(total);

  replayer.on("resize", (size) => {
    const { width, height } = size as { width: number; height: number };
    fit(replayer, stage, width, height);
  });

  // Notices: what the viewer is looking at, and what may be missing.
  const note = (text: string) => notices.append(el("li", {}, text));
  note(
    `Recorded with ${data.mask_mode} masking. Images, fonts and styles load from the live site now, ` +
      "so any changed or removed since render differently.",
  );
  if (data.stop_reason) note(`The recorder stopped at its ${data.stop_reason} limit; the visit went on after this.`);
  if (missingPages) note(`${missingPages} page(s) could not be loaded and are missing from the replay.`);

  // Signals, on the timeline and as a list.
  let playing = false;
  const seek = (offset: number) => {
    const target = Math.max(0, Math.min(total, offset));
    if (playing) replayer.play(target);
    else replayer.pause(target);
    scrubber.value = String(target);
    time.textContent = `${clock(target)} / ${clock(total)}`;
  };
  if (!data.markers.length) markerList.append(el("li", { class: "oc-empty" }, "No signals in this visit."));
  for (const marker of data.markers) {
    const at = marker.t_ms;
    const name = KIND_NAMES[marker.kind] ?? marker.kind;
    const tick = el("span", { class: `oc-tick oc-kind-${marker.kind}`, title: `${name} at ${clock(at)}` });
    tick.style.left = `${(100 * at) / Math.max(1, total)}%`;
    ticks.append(tick);
    const button = el(
      "button",
      { type: "button", class: `oc-marker oc-kind-${marker.kind}` },
      el("span", { class: "oc-marker-time" }, clock(at)),
      el("strong", {}, name),
      " ",
      marker.label || marker.selector || "",
    );
    button.addEventListener("click", () => seek(at - 1_000));
    markerList.append(el("li", {}, button));
  }
  for (const page of data.pages) {
    const first = events.find((e) => e.type === 2 && e.timestamp >= origin);
    const button = el("button", { type: "button", class: "oc-page" }, page.path || page.url);
    const pageStart = events.find(
      (e) => e.type === 4 && (e.data as { href?: string }).href === page.url,
    );
    button.addEventListener("click", () => seek((pageStart ?? first ?? events[0]).timestamp - origin));
    pageList.append(el("li", {}, button));
  }

  // Playback.
  let frame = 0;
  const tickClock = () => {
    const now = replayer.getCurrentTime();
    scrubber.value = String(now);
    time.textContent = `${clock(now)} / ${clock(total)}`;
    if (playing) frame = requestAnimationFrame(tickClock);
  };
  const setPlaying = (value: boolean) => {
    playing = value;
    play.textContent = value ? "Pause" : "Play";
    cancelAnimationFrame(frame);
    if (value) frame = requestAnimationFrame(tickClock);
  };
  play.addEventListener("click", () => {
    if (playing) {
      replayer.pause();
      setPlaying(false);
      return;
    }
    const at = replayer.getCurrentTime();
    replayer.play(at >= total ? 0 : at);
    setPlaying(true);
  });
  replayer.on("finish", () => {
    setPlaying(false);
    play.textContent = "Replay";
    scrubber.value = String(total);
  });
  scrubber.addEventListener("input", () => seek(Number(scrubber.value)));
  speed.addEventListener("change", () => replayer.setConfig({ speed: Number(speed.value) }));
  skip.addEventListener("change", () => replayer.setConfig({ skipInactive: skip.checked }));

  replayer.pause(0);
  time.textContent = `0:00 / ${clock(total)}`;
}

// ---------------------------------------------------------------------------
// Heatmap
// ---------------------------------------------------------------------------

type HeatmapData = {
  path: string;
  device_class: string;
  coverage: { pageviews: number; clicks: number; pageleaves: number };
  grid: { element: number; pixel_columns: number; pixel_row_px: number };
  element_rows: { selector: string; page_region: string; bucket_x: number; bucket_y: number; count: number }[];
  pixel_rows: { bucket_x: number; bucket_y: number; count: number }[];
  elements: { selector: string; page_region: string; clicks: number }[];
  areas: { page_region: string; clicks: number }[];
  scroll: { depth_pct: number; views: number; share: number }[];
  backdrop: null | { events_url: string; recorded_at: string; mask_mode: string };
};

const FRAME_WIDTHS: Record<string, number> = { desktop: 1280, tablet: 820, mobile: 390 };

type Point = { x: number; y: number; weight: number };

/** Draw weighted points as a heatmap: blurred intensity, then coloured. */
function paint(canvas: HTMLCanvasElement, points: Point[], radius = 22): void {
  const context = canvas.getContext("2d");
  if (!context || !points.length) return;
  const max = Math.max(...points.map((p) => p.weight));
  for (const point of points) {
    const gradient = context.createRadialGradient(point.x, point.y, 0, point.x, point.y, radius);
    const alpha = Math.max(0.08, point.weight / max);
    gradient.addColorStop(0, `rgba(0,0,0,${alpha})`);
    gradient.addColorStop(1, "rgba(0,0,0,0)");
    context.fillStyle = gradient;
    context.fillRect(point.x - radius, point.y - radius, radius * 2, radius * 2);
  }
  const image = context.getImageData(0, 0, canvas.width, canvas.height);
  const pixels = image.data;
  for (let i = 0; i < pixels.length; i += 4) {
    const a = pixels[i + 3] / 255;
    if (!a) continue;
    // Blue, through green and yellow, to red.
    const hue = (1 - a) * 240;
    const [r, g, b] = hslToRgb(hue / 360, 1, 0.5);
    pixels[i] = r;
    pixels[i + 1] = g;
    pixels[i + 2] = b;
    pixels[i + 3] = Math.min(255, 60 + a * 180);
  }
  context.putImageData(image, 0, 0);
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const channel = (t: number) => {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  return [channel(h + 1 / 3), channel(h), channel(h - 1 / 3)].map((v) => Math.round(v * 255)) as [
    number,
    number,
    number,
  ];
}

function nextFrame(): Promise<void> {
  return new Promise((done) => requestAnimationFrame(() => done()));
}

async function buildBackdrop(
  stage: HTMLElement,
  url: string,
): Promise<{ doc: Document; width: number; height: number; replayer: Replayer } | null> {
  const body = await getJson<{ events: eventWithTime[] }>(url);
  const events = sanitize(body.events).sort((a, b) => a.timestamp - b.timestamp);
  if (!events.some((e) => e.type === 2)) return null;
  const replayer = new Replayer(events, { root: stage, showWarning: false, mouseTail: false });
  // The last state of the page: every mutation applied.
  replayer.pause(replayer.getMetaData().totalTime);
  const doc = replayer.iframe.contentDocument;
  if (!doc) return null;
  // Measure only once the rebuilt page has been laid out: before that every
  // box is empty and the document is one viewport tall.
  replayer.iframe.style.display = "inherit";
  await nextFrame();
  await nextFrame();
  const width = replayer.iframe.width ? Number(replayer.iframe.width) : doc.documentElement.clientWidth;
  const height = Math.max(
    doc.documentElement.scrollHeight,
    doc.body ? doc.body.scrollHeight : 0,
    Number(replayer.iframe.height) || 0,
  );
  replayer.iframe.style.height = `${height}px`;
  replayer.iframe.setAttribute("height", String(height));
  replayer.wrapper.querySelector(".replayer-mouse")?.remove();
  return { doc, width, height, replayer };
}

async function mountHeatmap(root: HTMLElement): Promise<void> {
  const url = root.dataset.ossClarityHeatmap;
  if (!url) return;
  let data: HeatmapData;
  try {
    data = await getJson<HeatmapData>(url);
  } catch (error) {
    fail(root, `The heatmap could not be loaded (${(error as Error).message}).`);
    return;
  }

  const stage = el("div", { class: "oc-stage oc-heat-stage" });
  const canvas = el("canvas", { class: "oc-heat-canvas" });
  const bands = el("div", { class: "oc-bands" });
  const clicksMode = el("button", { type: "button", class: "button oc-mode oc-active" }, "Clicks");
  const scrollMode = el("button", { type: "button", class: "button oc-mode" }, "Scroll depth");
  const notices = el("ul", { class: "oc-notices" });
  const side = el("div", { class: "oc-heat-side" });
  root.replaceChildren(
    el(
      "p",
      { class: "oc-coverage" },
      `${data.coverage.pageviews} page views, ${data.coverage.clicks} clicks and ` +
        `${data.coverage.pageleaves} scroll reports on ${data.path} (${data.device_class}).`,
    ),
    el("div", { class: "oc-controls" }, clicksMode, scrollMode),
    notices,
    el("div", { class: "oc-heat-layout" }, stage, side),
  );

  let frame: { doc: Document | null; width: number; height: number; replayer: Replayer | null } = {
    doc: null,
    width: FRAME_WIDTHS[data.device_class] ?? 1280,
    height: 0,
    replayer: null,
  };
  if (data.backdrop) {
    try {
      const built = await buildBackdrop(stage, data.backdrop.events_url);
      if (built) frame = built;
    } catch {
      /* drawn on a plain frame below */
    }
  }
  const note = (text: string) => notices.append(el("li", {}, text));

  // Where the clicks go: on the elements when the snapshot still has them,
  // by position otherwise.
  const points: Point[] = [];
  let placedOnElements = 0;
  if (frame.doc) {
    const grid = data.grid.element;
    for (const row of data.element_rows) {
      let target: Element | null = null;
      try {
        target = frame.doc.querySelector(row.selector);
      } catch {
        target = null;
      }
      if (!target) continue;
      const box = target.getBoundingClientRect();
      if (!box.width && !box.height) continue;
      points.push({
        x: box.left + ((row.bucket_x + 0.5) / grid) * box.width,
        y: box.top + ((row.bucket_y + 0.5) / grid) * box.height,
        weight: row.count,
      });
      placedOnElements += row.count;
    }
  }
  if (!placedOnElements) {
    const rows = data.pixel_rows;
    for (const row of rows) {
      points.push({
        x: ((row.bucket_x + 0.5) / data.grid.pixel_columns) * frame.width,
        y: (row.bucket_y + 0.5) * data.grid.pixel_row_px,
        weight: row.count,
      });
    }
    const deepest = rows.reduce((max, row) => Math.max(max, (row.bucket_y + 1) * data.grid.pixel_row_px), 0);
    frame.height = Math.max(frame.height, deepest + 200, 800);
    note(
      frame.doc
        ? "The snapshot no longer has the clicked elements, so clicks are placed by position."
        : "No recording of this page on this device yet, so clicks are drawn by position on a blank page.",
    );
  }
  if (data.backdrop && frame.doc) {
    note(
      `Drawn on a recording from ${new Date(data.backdrop.recorded_at).toLocaleString()} ` +
        `(${data.backdrop.mask_mode} masking). Images and styles load from the live site.`,
    );
  }

  // Size everything to the page, then scale the page to fit.
  const layer = el("div", { class: "oc-heat-layer" });
  layer.style.width = `${frame.width}px`;
  layer.style.height = `${frame.height}px`;
  canvas.width = frame.width;
  canvas.height = frame.height;
  bands.style.height = `${frame.height}px`;
  if (frame.replayer) {
    frame.replayer.wrapper.append(layer);
  } else {
    const blank = el("div", { class: "oc-blank" });
    blank.style.width = `${frame.width}px`;
    blank.style.height = `${frame.height}px`;
    blank.append(layer);
    stage.append(blank);
  }
  layer.append(canvas, bands);
  const scaleTo = () => {
    const scale = Math.min(1, stage.clientWidth / frame.width);
    const target = (frame.replayer ? frame.replayer.wrapper : stage.firstElementChild) as HTMLElement;
    target.style.transform = `scale(${scale})`;
    target.style.transformOrigin = "0 0";
    stage.style.height = `${Math.ceil(frame.height * scale)}px`;
  };
  scaleTo();
  window.addEventListener("resize", scaleTo);
  paint(canvas, points);

  // Scroll reach as bands down the page.
  for (const step of data.scroll.slice(1)) {
    const band = el("div", { class: "oc-band" }, el("span", {}, `${step.share}% reached ${step.depth_pct}%`));
    band.style.top = `${(step.depth_pct / 100) * frame.height}px`;
    band.style.setProperty("--shade", String(1 - step.share / 100));
    bands.append(band);
  }
  const show = (mode: "clicks" | "scroll") => {
    canvas.hidden = mode !== "clicks";
    bands.hidden = mode !== "scroll";
    clicksMode.classList.toggle("oc-active", mode === "clicks");
    scrollMode.classList.toggle("oc-active", mode === "scroll");
  };
  clicksMode.addEventListener("click", () => show("clicks"));
  scrollMode.addEventListener("click", () => show("scroll"));
  show("clicks");

  // The side panel: what was clicked, where, and how far people read.
  const list = el("ol", { class: "oc-elements" });
  for (const item of data.elements) {
    list.append(el("li", {}, el("code", {}, item.selector), ` ${item.clicks}`));
  }
  if (!data.elements.length) list.append(el("li", { class: "oc-empty" }, "No clicks on named elements."));
  const areas = el("ul", { class: "oc-areas" });
  for (const area of data.areas) {
    areas.append(el("li", {}, `${area.page_region || "no landmark"}: ${area.clicks}`));
  }
  const reach = el("table", { class: "oc-reach" });
  for (const step of data.scroll) {
    reach.append(el("tr", {}, el("th", {}, `${step.depth_pct}%`), el("td", {}, `${step.share}%`)));
  }
  side.append(
    el("h3", {}, "Most clicked"),
    list,
    el("h3", {}, "Clicks by area"),
    areas,
    el("h3", {}, "Scroll reach"),
    reach,
  );
}

// ---------------------------------------------------------------------------

function mountAll(): void {
  document.querySelectorAll<HTMLElement>("[data-oss-clarity-replay]").forEach((node) => void mountReplay(node));
  document.querySelectorAll<HTMLElement>("[data-oss-clarity-heatmap]").forEach((node) => void mountHeatmap(node));
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mountAll);
else mountAll();
