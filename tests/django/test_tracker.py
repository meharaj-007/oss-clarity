"""The tracker template: rendering, minification, and its behaviour when run.

The behaviour tests run the served, minified tracker in Node with a small fake
browser, so they check what visitors actually download. They are skipped when
Node is not installed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from oss_clarity.tracker import TRACKER_JS, render_tracker, served_template

VECTORS = json.loads(
    (Path(__file__).resolve().parents[1] / "vectors" / "sampling.json").read_text()
)["vectors"]
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

KEY = "AbCdEfGhIjKlMnOpQrStUv"
RECORD = {
    "endpoint": "https://collect.example.net/oc/r/",
    "recorder_url": "https://collect.example.net/oc/rec/0123456789abcdef.js",
    "sample_pct": 100,
    "mask_mode": "balanced",
    "mask": [".secret"],
    "unmask": [],
    "block": [],
    "consent_required": False,
    "max_bytes": 10_000_000,
    "max_pages": 128,
    "max_minutes": 120,
}


def render(record=None, **kwargs):
    return render_tracker(KEY, "https://collect.example.net/oc/e/", record, **kwargs)


# --- rendering ------------------------------------------------------------------


def test_the_served_tracker_is_minified_with_every_placeholder_filled():
    js = render(RECORD)
    assert js.startswith("/*! oss-clarity tracker */")
    assert len(served_template()) < len(TRACKER_JS) * 0.7
    assert "// " not in js
    assert f'"{KEY}"' in js and '"https://collect.example.net/oc/e/"' in js
    assert '".secret"' in js and '"mask_mode":"balanced"' in js
    assert not re.search(r"__[A-Z_]+__", js)


def test_recording_off_bakes_in_null():
    assert "var RECORD=null;" in render(None)


def test_the_idle_window_is_baked_in():
    assert "var SESSION_IDLE_MS=900000;" in render(None, session_idle_minutes=15)


def test_site_supplied_text_cannot_break_out_of_the_script():
    js = render({**RECORD, "mask": ['"];alert(1);//', "</script><script>alert(2)"]})
    assert '"];alert(1);//' not in js.replace('\\"];alert(1);//', "")
    assert "</script>" not in js


def test_the_tracker_skips_the_recorder_for_crawlers_and_gpc():
    assert "navigator.webdriver === true" in TRACKER_JS
    assert 'ua.indexOf("Mozilla/") !== 0' in TRACKER_JS
    assert "google-|googleother" in TRACKER_JS
    assert "navigator.globalPrivacyControl === true" in TRACKER_JS


def test_clicks_carry_no_text_or_link():
    handler = TRACKER_JS.split('document.addEventListener("click"')[1].split("}, true);")[0]
    for field in ("innerText", "textContent", "href", "element_text"):
        assert field not in handler


def test_no_other_product_name_is_in_the_script():
    assert "window.ossClarity" in TRACKER_JS
    assert "oss_clarity.pageview" in TRACKER_JS and "oss_clarity.error" in TRACKER_JS


# --- behaviour, in Node ------------------------------------------------------------

HARNESS = r"""
const source = require("fs").readFileSync(0, "utf8");
const input = JSON.parse(source);
const sent = [];
const listeners = {};
const docListeners = {};
const store = new Map(Object.entries(input.storage || {}));
const scripts = [];
global.window = global;
window.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
  key: (i) => [...store.keys()][i] ?? null,
  get length() { return store.size; },
};
window.location = { href: "https://shop.example/pricing?utm_source=x" };
window.innerWidth = 1280; window.innerHeight = 800; window.pageXOffset = 0; window.pageYOffset = 0;
window.addEventListener = (name, fn) => { (listeners[name] = listeners[name] || []).push(fn); };
window.requestAnimationFrame = (fn) => setTimeout(fn, 0);
window.fetch = (url, opts) => {
  sent.push({ url, body: JSON.parse(opts.body), opts });
  return Promise.resolve();
};
window.history = { pushState() {}, replaceState() {} };
global.history = window.history;
// Node has its own read-only navigator; replace it outright.
Object.defineProperty(globalThis, "navigator", {
  value: { userAgent: input.ua, webdriver: false, globalPrivacyControl: input.gpc || false },
  configurable: true,
});
global.document = {
  readyState: "complete",
  title: "Pricing",
  referrer: "",
  visibilityState: "visible",
  documentElement: { scrollHeight: 2000 },
  body: { scrollHeight: 2000 },
  head: { appendChild: (el) => scripts.push(el.src) },
  addEventListener: (name, fn) => { (docListeners[name] = docListeners[name] || []).push(fn); },
  createElement: () => ({}),
};
global.Blob = class {};
const fnvSource = input.tracker.match(/function fnv1a\(\w+\)\{[^}]*\{[^}]*\}[^}]*\}/)[0];
const fnv1a = new Function(fnvSource + "; return fnv1a;")();
eval(input.tracker);
setTimeout(() => {
  const api = window.ossClarity;
  const result = {
    sent: sent.map((s) => s.body),
    opts: sent.map((s) => s.opts),
    scripts,
    storage: Object.fromEntries(store),
    visitorId: api("visitorId"),
    hashes: input.vectors.map((id) => fnv1a(id)),
  };
  api("forget");
  result.afterForget = { visitorId: api("visitorId"), keys: [...store.keys()] };
  (listeners.pagehide || []).forEach((fn) => fn({}));
  result.sentAfterForget = sent.length - result.sent.length;
  process.stdout.write(JSON.stringify(result));
}, 20);
"""


def run_tracker(js: str, *, ua=None, gpc=False, storage=None) -> dict:
    payload = {
        "tracker": js,
        "ua": ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36",
        "gpc": gpc,
        "storage": storage or {},
        "vectors": [v["session_id"] for v in VECTORS],
    }
    done = subprocess.run(
        [NODE, "-e", HARNESS],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(done.stdout)


@needs_node
def test_the_served_trackers_hash_matches_every_vector():
    result = run_tracker(render(None))
    assert result["hashes"] == [v["hash"] for v in VECTORS]


@needs_node
def test_a_page_view_is_sent_with_ids_the_collector_accepts():
    from oss_clarity.core.sampling import ID_RE

    result = run_tracker(render(None))
    [view] = result["sent"]
    assert view["type"] == "pageview" and view["key"] == KEY and view["seq"] == 1
    assert view["url"] == "https://shop.example/pricing?utm_source=x"
    assert ID_RE.match(view["session_id"]) and ID_RE.match(view["visitor_id"])
    assert result["opts"][0]["credentials"] == "omit" and result["opts"][0]["keepalive"] is True
    assert result["visitorId"] == view["visitor_id"]


@needs_node
def test_the_sequence_continues_within_a_session():
    first = run_tracker(render(None))
    second = run_tracker(render(None), storage=first["storage"])
    assert second["sent"][0]["session_id"] == first["sent"][0]["session_id"]
    assert second["sent"][0]["seq"] == 2


@needs_node
def test_the_recorder_is_loaded_for_a_person():
    assert run_tracker(render(RECORD))["scripts"] == [RECORD["recorder_url"]]


@needs_node
@pytest.mark.parametrize(
    "ua",
    [
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/131.0.0.0 Safari/537.36",
        "python-requests/2.32.3",
    ],
)
def test_the_recorder_is_not_loaded_for_a_crawler(ua):
    result = run_tracker(render(RECORD), ua=ua)
    assert result["scripts"] == []
    assert len(result["sent"]) == 1  # the page view is still counted


@needs_node
def test_the_recorder_is_not_loaded_under_global_privacy_control():
    result = run_tracker(render(RECORD), gpc=True)
    assert result["scripts"] == [] and len(result["sent"]) == 1


@needs_node
def test_consent_gates_the_recorder():
    assert run_tracker(render({**RECORD, "consent_required": True}))["scripts"] == []
    given = run_tracker(
        render({**RECORD, "consent_required": True}), storage={"oss_clarity.consent": "1"}
    )
    assert given["scripts"] == [RECORD["recorder_url"]]


@needs_node
def test_forget_clears_the_ids_and_stops_reporting():
    result = run_tracker(render(None))
    assert result["afterForget"] == {"visitorId": None, "keys": []}
    assert result["sentAfterForget"] == 0
