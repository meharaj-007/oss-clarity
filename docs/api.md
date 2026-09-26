# JSON API

Read-only. Include it where you want it:

```python
path("oc-api/", include("oss_clarity.urls.api")),
```

Every route needs permission: active staff users by default, or whatever
`OSS_CLARITY["API_PERMISSION"]` (a dotted path to `f(request) -> bool`) allows.
Others get 403. Responses are `Cache-Control: private, no-store`.

Lists take `?page=` (50 rows a page) and `?days=` (1 to 400, default 30), and
answer `{"data": [...], "page": 1, "pages": 3, "count": 120}`.

| Route | Returns |
|---|---|
| `GET sites/<site_id>/recordings/` | Recordings, newest first. `?has=rage,loop` keeps those with any of the named signals; `?favorites=1` keeps favourites. |
| `GET recordings/<recording_id>/` | One recording: its counters, markers and pages, each page with an `events_url`. |
| `GET recordings/<recording_id>/pages/<page_seq>/events/` | `{"events": [...]}`: one page's rrweb events, in order. |
| `GET sites/<site_id>/heatmaps/` | Pages with heatmap data, busiest first, with page views per device class. |
| `GET sites/<site_id>/heatmap/?path=/pricing&device=desktop` | One map: coverage, element and pixel click buckets, clicks per page area, scroll reach, and a recorded snapshot to draw it on (`backdrop.events_url`), when there is one. |
| `GET sites/<site_id>/sessions/` | Visits with a page view in the window, with `quick_backs`, `loops` and the recording id (or `null`) on every row. `?has=` works across navigation and recording signals; `?recorded=1` keeps recorded visits. |

Signal names for `?has=` are the kinds in [signals](signals.md): `rage`, `dead`,
`error`, `script_error`, `hesitation`, `near_miss`, `scroll_hunt`, `form_skip`,
`form_refill`, `form_abandon`, `repeat_submit`, `copy_out`, `idle_exit`,
`quick_back`, `loop`.

Favouriting and deleting recordings are done in the admin, not through the API.
