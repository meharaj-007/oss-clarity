"""The tracker script a site pastes in, and the recorder bundle it loads.

The tracker is a template rather than a static file because the collector
URL, the site's key and its recording settings are baked in when it is
served; a static file would need every site to configure all three.

Design rules for the tracker, in order:

* Never break the host page: everything runs inside try/catch.
* Never slow it: the page view is sent after `load`, scroll depth is read at
  most once per animation frame, and the source is served minified.
* No cookies: ids live in `localStorage`, so nothing needs a cookie banner.
* Reports use `fetch` with `keepalive` and `text/plain`, a simple request that
  survives the page closing and needs no preflight. `sendBeacon` is only a
  fallback, since blockers drop "ping" requests on sight.
* Clicks carry where they landed and a path to the element, never its text or
  link: a heatmap needs coordinates, not content.
* The recorder is a second, larger file fetched only for a sampled visit, after
  `load`, never under Global Privacy Control, never for a crawler.

This module does not import Django, so it can be rendered from anywhere.
"""

from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

TRACKER_JS = r"""/*! oss-clarity tracker */
(function () {
  "use strict";
  try {
    var KEY = "__KEY__";
    var ENDPOINT = "__ENDPOINT__";
    // Recording settings, or null when the site does not record.
    var RECORD = __RECORD__;
    var SESSION_IDLE_MS = __SESSION_IDLE_MS__;
    // A heatmap needs a sample of where people click, not every click, and
    // each one counts against the same rate limit as page views.
    var MAX_CLICKS_PER_PAGE = 30;
    var REGION_ATTRIBUTE = "data-oss-clarity-region";
    var PREFIX = "oss_clarity.";
    var forgotten = false;

    var store = {
      get: function (key) {
        try { return window.localStorage.getItem(PREFIX + key); } catch (e) { return null; }
      },
      set: function (key, value) {
        try { window.localStorage.setItem(PREFIX + key, value); } catch (e) { /* private mode */ }
      },
      clear: function () {
        try {
          var doomed = [];
          for (var i = 0; i < window.localStorage.length; i++) {
            var name = window.localStorage.key(i);
            if (name && name.indexOf(PREFIX) === 0) doomed.push(name);
          }
          for (var j = 0; j < doomed.length; j++) window.localStorage.removeItem(doomed[j]);
        } catch (e) { /* nothing stored */ }
      }
    };

    // Ids are letters, digits and dashes only; the collector refuses others.
    function uid() {
      try {
        if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
      } catch (e) { /* fall through */ }
      return String(Date.now()) + "-" + Math.random().toString(36).slice(2, 12);
    }

    var visitorId = store.get("vid");
    if (!visitorId) {
      visitorId = uid();
      store.set("vid", visitorId);
    }

    // A visit ends after the idle window with nothing sent.
    var now = Date.now();
    var lastSeen = parseInt(store.get("seen") || "0", 10);
    var sessionId = store.get("sid");
    var freshSession = !sessionId || !lastSeen || now - lastSeen > SESSION_IDLE_MS;
    if (freshSession) {
      sessionId = uid();
      store.set("sid", sessionId);
    }
    store.set("seen", String(now));

    // This session's own count of hits. Separate requests can arrive out of
    // order; the count is what puts them back. Kept in storage because a
    // session spans page loads.
    var seq = freshSession ? 0 : (parseInt(store.get("seq") || "0", 10) || 0);
    if (freshSession) store.set("seq", "0");

    function send(type, extra) {
      try {
        if (forgotten) return;
        store.set("seen", String(Date.now()));
        seq = seq + 1;
        store.set("seq", String(seq));
        var data = {
          key: KEY,
          type: type,
          seq: seq,
          url: window.location.href,
          title: document.title || "",
          referrer: document.referrer || "",
          visitor_id: visitorId,
          session_id: sessionId
        };
        if (extra) {
          for (var name in extra) {
            if (Object.prototype.hasOwnProperty.call(extra, name)) data[name] = extra[name];
          }
        }
        var body = JSON.stringify(data);
        var sent = false;
        try {
          if (window.fetch) {
            fetch(ENDPOINT, {
              method: "POST",
              body: body,
              keepalive: true,
              mode: "cors",
              headers: { "Content-Type": "text/plain" },
              credentials: "omit"
            })["catch"](function () { /* blocked or offline: never surface it */ });
            sent = true;
          }
        } catch (e) { /* fall through to the beacon */ }
        if (!sent && navigator.sendBeacon) {
          navigator.sendBeacon(ENDPOINT, new Blob([body], { type: "text/plain" }));
        }
      } catch (e) { /* a tracker must never break the page */ }
    }

    function pageview() { setTimeout(function () { send("pageview"); }, 0); }

    if (document.readyState === "complete") pageview();
    else window.addEventListener("load", pageview, { once: true });

    // ---------------------------------------------------------------------
    // Recording
    // ---------------------------------------------------------------------
    //
    // Whether this visit is recorded is a hash of its session id against the
    // site's sample percentage: every page of the visit agrees without shared
    // state, and the collector recomputes the same hash. FNV-1a, 32 bits,
    // over the id's characters. Ids are ASCII, so characters are bytes.

    var recorder = null;
    var pageId = uid();

    function fnv1a(str) {
      var h = 0x811c9dc5;
      for (var i = 0; i < str.length; i++) {
        h ^= str.charCodeAt(i);
        h = Math.imul(h, 0x01000193) >>> 0;
      }
      return h >>> 0;
    }

    function consentGiven() {
      if (!RECORD || !RECORD.consent_required) return true;
      return store.get("consent") === "1";
    }

    function isCrawler() {
      var ua = navigator.userAgent || "";
      if (navigator.webdriver === true) return true;
      // Every browser that runs JavaScript starts with "Mozilla/", bar the
      // in-app browsers of social apps, which are people.
      if (ua.indexOf("Mozilla/") !== 0 &&
          !/FBAN|FBAV|Instagram|Snapchat|Pinterest|WhatsApp|Line\/|musical_ly|BytedanceWebview|Twitter/.test(ua)) {
        return true;
      }
      return /bot|crawl|spider|slurp|headless|google-|googleother|facebookexternalhit|lighthouse/i.test(ua);
    }

    function recordingAllowed() {
      if (forgotten || !RECORD || typeof Math.imul !== "function") return false;
      // Global Privacy Control turns recording off. Page views still count.
      if (navigator.globalPrivacyControl === true) return false;
      if (isCrawler()) return false;
      if (!consentGiven()) return false;
      var pct = RECORD.sample_pct;
      if (pct <= 0) return false;
      if (pct >= 100) return true;
      return fnv1a(sessionId) % 100 < pct;
    }

    // The caps are per session and a session spans page loads, so the
    // running totals are stored beside the session id.
    function recordingBudget() {
      if (store.get("rec.sid") !== sessionId) {
        store.set("rec.sid", sessionId);
        store.set("rec.pages", "0");
        store.set("rec.bytes", "0");
        store.set("rec.start", String(Date.now()));
      }
      var startedAt = parseInt(store.get("rec.start") || "0", 10) || Date.now();
      return {
        pages: parseInt(store.get("rec.pages") || "0", 10) || 0,
        bytes: parseInt(store.get("rec.bytes") || "0", 10) || 0,
        remainingMs: RECORD.max_minutes * 60000 - (Date.now() - startedAt)
      };
    }

    function startRecorder() {
      try {
        if (recorder || !recordingAllowed()) return;
        var budget = recordingBudget();
        if (budget.pages >= RECORD.max_pages) return;
        if (budget.bytes >= RECORD.max_bytes) return;
        if (budget.remainingMs <= 0) return;
        store.set("rec.pages", String(budget.pages + 1));

        var config = {
          endpoint: RECORD.endpoint,
          key: KEY,
          sessionId: sessionId,
          visitorId: visitorId,
          pageId: pageId,
          pageSeq: budget.pages,
          maskMode: RECORD.mask_mode,
          mask: RECORD.mask,
          unmask: RECORD.unmask,
          block: RECORD.block,
          maxBytes: RECORD.max_bytes,
          bytesUsed: budget.bytes,
          maxMs: budget.remainingMs,
          onBytes: function (total) { store.set("rec.bytes", String(total)); }
        };
        function boot() {
          try {
            if (window.__ossClarityRecorder && !recorder && !forgotten) {
              recorder = window.__ossClarityRecorder;
              recorder.start(config);
            }
          } catch (e) { /* the page must not notice */ }
        }
        if (window.__ossClarityRecorder) { boot(); return; }
        var script = document.createElement("script");
        script.async = true;
        script.src = RECORD.recorder_url;
        script.onload = boot;
        (document.head || document.documentElement).appendChild(script);
      } catch (e) { /* never break the page */ }
    }

    function stopRecorder() {
      if (recorder) {
        try { recorder.stop("consent"); } catch (e) { /* already stopped */ }
        recorder = null;
      }
    }

    // The tracker's only global.
    //   ossClarity("consent", true)  start recording where consent is required
    //   ossClarity("consent", false) stop, and send nothing more from it
    //   ossClarity("visitorId")      this browser's visitor id, for a
    //                                "delete my data" request, or null
    //   ossClarity("forget")         clear the stored ids and stop reporting
    window.ossClarity = function (command, value) {
      try {
        if (command === "consent") {
          store.set("consent", value ? "1" : "0");
          if (value) startRecorder(); else stopRecorder();
        } else if (command === "visitorId") {
          return forgotten ? null : visitorId;
        } else if (command === "forget") {
          stopRecorder();
          forgotten = true;
          store.clear();
        }
      } catch (e) { /* never break the page */ }
      return undefined;
    };

    // Script errors go into the recording so error clicks can be found.
    window.addEventListener("error", function (evt) {
      try {
        if (!recorder) return;
        recorder.custom("oss_clarity.error", {
          message: String((evt && evt.message) || "").slice(0, 300),
          source: String((evt && evt.filename) || "").slice(0, 300),
          line: (evt && evt.lineno) || 0
        });
      } catch (e) { /* never break the page */ }
    });

    if (RECORD) {
      if (document.readyState === "complete") setTimeout(startRecorder, 0);
      else window.addEventListener("load", function () { setTimeout(startRecorder, 0); }, { once: true });
    }

    // ---------------------------------------------------------------------
    // Clicks and scroll depth, for heatmaps
    // ---------------------------------------------------------------------

    var clicksThisPage = 0;
    // What a click was meant for: the nearest thing a visitor can press.
    var PRESSABLE = "a,button,input,select,textarea,label,summary,[role=button],[role=link],[onclick]";
    var LANDMARKS = { HEADER: "header", NAV: "nav", MAIN: "main", ASIDE: "aside", FOOTER: "footer" };
    var ROLES = { banner: "header", navigation: "nav", main: "main", complementary: "aside", contentinfo: "footer" };

    function pageHeight() {
      var d = document.documentElement, b = document.body;
      return Math.max((d && d.scrollHeight) || 0, (b && b.scrollHeight) || 0, window.innerHeight || 0);
    }

    // A short path to find the element again in a recorded snapshot: an id
    // when it looks hand-written, otherwise tag, up to two classes and a
    // position, at most five levels. Names with long digit runs are
    // generated per build and would never match twice.
    function stable(name) { return /^[A-Za-z][\w-]*$/.test(name) && !/\d{3,}/.test(name); }
    function selectorOf(el) {
      var parts = [];
      for (var depth = 0; el && el.nodeType === 1 && depth < 5; depth++) {
        var tag = el.tagName.toLowerCase();
        if (tag === "html" || tag === "body") break;
        if (el.id && stable(el.id)) { parts.unshift(tag + "#" + el.id); break; }
        var part = tag;
        var classes = (typeof el.className === "string" ? el.className : "").split(/\s+/);
        for (var i = 0, n = 0; i < classes.length && n < 2; i++) {
          if (classes[i] && stable(classes[i])) { part += "." + classes[i]; n++; }
        }
        var parent = el.parentElement;
        if (parent) {
          var same = 0, index = 0;
          for (var c = parent.firstElementChild; c; c = c.nextElementSibling) {
            if (c.tagName === el.tagName) { same++; if (c === el) index = same; }
          }
          if (same > 1) part += ":nth-of-type(" + index + ")";
        }
        parts.unshift(part);
        el = parent;
      }
      return parts.join(" > ").slice(0, 255);
    }

    function regionOf(el) {
      try {
        var named = el.closest("[" + REGION_ATTRIBUTE + "]");
        if (named) return (named.getAttribute(REGION_ATTRIBUTE) || "").slice(0, 64);
      } catch (e) { /* closest exists wherever this runs */ }
      for (; el && el.nodeType === 1; el = el.parentElement) {
        var role = el.getAttribute && el.getAttribute("role");
        if (role && ROLES[role]) return ROLES[role];
        if (LANDMARKS[el.tagName]) return LANDMARKS[el.tagName];
      }
      return "";
    }

    function position(evt, el) {
      var rect = el.getBoundingClientRect();
      var cx = evt.clientX, cy = evt.clientY;
      // A keyboard "click" has no pointer position: use the element's centre.
      if (!evt.detail && !cx && !cy) { cx = rect.left + rect.width / 2; cy = rect.top + rect.height / 2; }
      var sx = window.pageXOffset || 0, sy = window.pageYOffset || 0;
      return {
        x: Math.round(cx + sx),
        y: Math.round(cy + sy),
        rel_x: rect.width ? Math.round(Math.min(1, Math.max(0, (cx - rect.left) / rect.width)) * 1000) : 500,
        rel_y: rect.height ? Math.round(Math.min(1, Math.max(0, (cy - rect.top) / rect.height)) * 1000) : 500,
        viewport_w: window.innerWidth || null,
        viewport_h: window.innerHeight || null,
        page_h: pageHeight(),
        page_region: regionOf(el)
      };
    }

    // How far this page was read: the deepest the bottom of the window got,
    // as a share of the document, and the seconds the page was visible.
    var deepest = 0, visibleMs = 0, visibleSince = null, leftThisPage = false;
    function measureDepth() {
      try {
        var h = pageHeight();
        if (h) deepest = Math.max(deepest, Math.min(100, Math.round(((window.pageYOffset || 0) + (window.innerHeight || 0)) * 100 / h)));
      } catch (e) { /* never break the page */ }
    }
    function startVisible() { if (document.visibilityState === "visible" && visibleSince === null) visibleSince = Date.now(); }
    function stopVisible() { if (visibleSince !== null) { visibleMs += Date.now() - visibleSince; visibleSince = null; } }
    function resetPage() { deepest = 0; visibleMs = 0; visibleSince = null; leftThisPage = false; clicksThisPage = 0; startVisible(); measureDepth(); }
    function pageleave(url, title) {
      if (leftThisPage) return;
      leftThisPage = true;
      measureDepth();
      stopVisible();
      send("pageleave", {
        url: url || window.location.href,
        title: title === undefined ? (document.title || "") : title,
        scroll_depth_pct: deepest,
        active_seconds: Math.round(visibleMs / 1000),
        viewport_w: window.innerWidth || null,
        viewport_h: window.innerHeight || null,
        page_h: pageHeight()
      });
    }
    resetPage();
    // At most once a frame: reading the height can force a layout, and scroll
    // events fire far faster than frames on a heavy page.
    var depthQueued = false;
    var nextFrame = window.requestAnimationFrame || function (fn) { return setTimeout(fn, 100); };
    window.addEventListener("scroll", function () {
      if (depthQueued) return;
      depthQueued = true;
      nextFrame(function () { depthQueued = false; measureDepth(); });
    }, { passive: true });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") startVisible(); else stopVisible();
    });
    window.addEventListener("pagehide", function () { pageleave(); });
    // Restored from the back/forward cache: the same document, read again.
    window.addEventListener("pageshow", function (evt) { if (evt && evt.persisted) resetPage(); });

    // Capture phase, so a handler that stops propagation cannot hide clicks.
    // Position and a path only: no text and no link, since nobody asked for
    // this element's content to be collected.
    document.addEventListener("click", function (evt) {
      try {
        if (clicksThisPage >= MAX_CLICKS_PER_PAGE) return;
        var target = evt.target && evt.target.nodeType === 1 ? evt.target : evt.target && evt.target.parentElement;
        if (!target || typeof target.closest !== "function") return;
        var el = target.closest(PRESSABLE) || target;
        clicksThisPage++;
        var hit = position(evt, el);
        hit.element_selector = selectorOf(el);
        send("click", hit);
      } catch (e) { /* never break the page */ }
    }, true);

    // Single-page apps change the URL without a load.
    var lastUrl = window.location.href;
    var lastTitle = document.title || "";
    function onRouteChange() {
      if (window.location.href === lastUrl) return;
      // The page being left reports its depth under its own URL.
      pageleave(lastUrl, lastTitle);
      lastUrl = window.location.href;
      lastTitle = document.title || "";
      resetPage();
      pageview();
      // Same document, new page: the recording marks the boundary.
      if (recorder) {
        try {
          recorder.custom("oss_clarity.pageview", { url: window.location.href, title: document.title || "" });
        } catch (e) { /* never break the page */ }
      }
    }
    ["pushState", "replaceState"].forEach(function (name) {
      var original = history[name];
      if (typeof original !== "function") return;
      history[name] = function () {
        var result = original.apply(this, arguments);
        setTimeout(onRouteChange, 0);
        return result;
      };
    });
    window.addEventListener("popstate", onRouteChange);
  } catch (e) { /* never break the page */ }
})();
"""

PLACEHOLDERS = ("__KEY__", "__ENDPOINT__", "__RECORD__", "__SESSION_IDLE_MS__")


@lru_cache(maxsize=1)
def served_template() -> str:
    """The tracker as sent: comments and whitespace stripped, the `/*!`
    banner kept. rjsmin renames nothing, so the placeholders survive. If the
    minifier is missing or breaks a placeholder, the readable source is
    served: a bigger file is a cost, a broken tracker is an outage."""
    try:
        import rjsmin

        minified = rjsmin.jsmin(TRACKER_JS, keep_bang_comments=True)
    except Exception:
        logger.warning("tracker minification failed; serving the source", exc_info=True)
        return TRACKER_JS
    if not all(placeholder in minified for placeholder in PLACEHOLDERS):
        logger.warning("tracker minification lost a placeholder; serving the source")
        return TRACKER_JS
    return minified


def render_tracker(
    key: str,
    endpoint: str,
    record: dict | None = None,
    *,
    session_idle_minutes: int = 30,
) -> str:
    """The tracker for one site. Values go in through `json.dumps`: selectors
    are site-supplied text landing in a JavaScript literal."""
    return (
        served_template()
        .replace("__KEY__", _js_string_body(key))
        .replace("__ENDPOINT__", _js_string_body(endpoint))
        .replace("__RECORD__", _js_value(record) if record else "null")
        .replace("__SESSION_IDLE_MS__", str(int(session_idle_minutes) * 60_000))
    )


def _js_value(value) -> str:
    """A JSON value as a JavaScript literal that cannot end a `<script>` or
    break a line, even if a site's selectors contain `</script>`."""
    text = json.dumps(value, separators=(",", ":"))
    return text.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _js_string_body(value: str) -> str:
    """`value` safe inside an existing double-quoted literal."""
    return _js_value(str(value))[1:-1]


def snippet_for(script_url: str) -> str:
    """The one line a site pastes into its `<head>`."""
    return f'<script async src="{script_url}"></script>'


# ---------------------------------------------------------------------------
# The recorder bundle
# ---------------------------------------------------------------------------

#: Built from `js/recorder/` and committed; see `js/README.md`.
RECORDER_PATH = Path(__file__).resolve().parent / "static" / "oss_clarity" / "recorder.js"
DIGEST_LENGTH = 16


@lru_cache(maxsize=1)
def recorder_source() -> tuple[str, str]:
    """`(digest, source)` of the committed recorder, read once per process.
    The digest is the first 16 hex characters of its SHA-256 and goes in the
    URL, so a new build is a new URL and each can be cached for a year."""
    try:
        source = RECORDER_PATH.read_text(encoding="utf-8")
    except OSError:
        logger.error("recorder bundle missing at %s; build it from js/", RECORDER_PATH)
        source = "/* oss-clarity: recorder bundle not built */\n"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]
    return digest, source
