# Signals

Every signal is a fixed rule over the recorded events, with named thresholds
(see [thresholds](thresholds.md)). The same recording always gives the same
result, and each marker on the replay timeline can be traced to its rule. There
is no machine learning or AI anywhere in the package.

The definitions of record are in the docstring of
`src/oss_clarity/core/signals.py` and `src/oss_clarity/core/navigation.py`.
This page says the same in plainer words.

## Frustration

| Signal | Kind | Fires when |
|---|---|---|
| Rage click | `rage` | At least `rage_min_clicks` clicks within `rage_window_ms`, each within `rage_radius_px` of the one before. Every click in the burst is marked. |
| Dead click | `dead` | A click on something that is not a form control, and within `dead_window_ms` the page does nothing: no DOM change, no scroll, no selection change, no page view. |
| Error click | `error` | A click followed within `error_window_ms` by a script error. An error click is not also a dead click. |
| Script error | `script_error` | Any script error the tracker saw while recording (at most 5 per page). |

## Behaviour

| Signal | Kind | Fires when |
|---|---|---|
| Hesitation | `hesitation` | The mouse rests on something pressable, or on a short piece of text such as a price, for between `hesitation_min_ms` and `hesitation_max_ms`, and is not then pressed. Mouse only. Once per element per page. |
| Near miss | `near_miss` | A click the page did not react to, then within `near_miss_window_ms` a click at most `near_miss_radius_px` away on a different pressable element that the page did react to. |
| Scroll hunt | `scroll_hunt` | `scroll_hunt_reversals` changes of scroll direction on one page, each at least `scroll_hunt_min_leg_px`, with no click, input or selection between them. Once per page. |
| Form skip | `form_skip` | A field focused and left empty, while another field was filled in afterwards. |
| Form refill | `form_refill` | A field's typed length falls to at most `refill_drop_share` of what it reached, then grows back. |
| Form abandon | `form_abandon` | The visit ends on a page where something was typed and no submit was pressed afterwards. Marked on the last field typed into. |
| Repeat submit | `repeat_submit` | The same submit control pressed again, later than `rage_window_ms` and within `repeat_submit_window_ms`. |
| Copy-out | `copy_out` | Selected text shaped like a phone number, an email address or a street address (read after masking, so digits appear as `▫`). |
| Idle exit | `idle_exit` | The visit ends on a page open for at least `idle_exit_min_ms` with no click, scroll, input, selection or touch. |

## Navigation

Read from page views rather than recordings, so every visit has them, recorded
or not.

| Signal | Kind | Fires when |
|---|---|---|
| Quick back | `quick_back` | Page views A, B, A with less than `QUICK_BACK_SECONDS` spent on B. |
| Loop | `loop` | Page views A, B, A, B in a row. Counted without overlap. |

## Two rules that apply to all of them

- **Only visitor typing counts.** For the form signals, an input counts only if
  the recorder marked it as caused by a person, or (in recordings without that
  mark) if the visitor focused or clicked that field first. Scripts fill hidden
  fields and calculators all the time.
- **Consent banners are not the site.** Clicks, rests and near misses inside a
  cookie or consent banner are never dead clicks, hesitations or near misses.

## Markers

Each finding is stored as a marker:

```json
{"t_ms": 1507, "page_id": "…", "kind": "rage", "selector": "div#dead",
 "label": "This panel does nothing when clicked.", "x": 300, "y": 330}
```

`t_ms` is milliseconds from the start of the recording. At most 200 markers are
kept per recording; the counters are never capped.
