# Changelog

## Unreleased

- `oss_clarity.core`: rules-based signal analysis of rrweb recordings (`analyze`), frozen
  `Thresholds`, quick backs and loops from page views (`navigation`), a user-agent classifier
  with a bot test, and a threshold measuring tool (`measure`). No Django required.
- Django app `oss_clarity`: models for sites, recording settings, hits, recordings and their
  chunks, heatmap buckets, visit navigation and job runs, in one initial migration. No IP
  address or raw user agent is stored.
- `OSS_CLARITY` settings dict with defaults for every key, checked by `manage.py check`.
- Admin: sites with their recording settings and refusal counters, read-only hits, recordings
  filterable by signal, favourites, and job runs.
- Public collector (`oss_clarity.urls.public`): the tracker script per site, the recorder
  bundle under a content digest, page views, clicks and page leaves, and recording chunks
  (gzip accepted). Host allow-list, crawler refusal for recordings, sampling by session-id
  hash, size and session caps, and per-address, per-site and global rate limits. Every
  refusal answers 204.
- Client IP for rate limits resolved by `TRUSTED_PROXY_COUNT`, `CLIENT_IP_HEADER` or
  `CLIENT_IP_FUNCTION`; used in memory only.
- The tracker honours Global Privacy Control for recording, supports a consent gate, and
  exposes `ossClarity("visitorId")` and `ossClarity("forget")`. Clicks carry position and a
  selector only; credential-shaped query values are redacted before any URL is stored.
- Recorder bundle built from `js/recorder` with esbuild; third-party notices in
  `THIRD_PARTY_NOTICES.md`.
- Optional `PublicEndpointsMiddleware` for hosts whose CORS or cache middleware would
  override the public endpoints' headers.
- Background jobs in `oss_clarity.jobs`: finalize and analyse quiet recordings, store visits'
  quick backs and loops, roll up heatmaps per UTC day, and prune by retention window. Each
  run is claimed in `JobRun` with one conditional update, so overlapping runs never repeat
  a job. Run them with `manage.py oss_clarity_run_jobs` from cron, or the optional Celery
  tasks and `BEAT_SCHEDULE` in `oss_clarity.tasks`.
- Visitor erasure: `retention.erase_visitor`, `manage.py oss_clarity_erase_visitor` and an
  admin action. Stored chunks are always deleted before rows; a failed delete keeps the rows
  for the next run.
- `manage.py oss_clarity_measure` reports how many signals each threshold setting would
  find, with moments to watch. It writes nothing.
- Recordings can be deleted from the admin, stored chunks first.
- Read-only JSON API (`oss_clarity.urls.api`): recordings filterable by any-of signals and
  favourites, one recording with its pages, a page's events, heatmap pages, one heatmap,
  and sessions with quick backs and loops on every row. Staff only unless
  `API_PERMISSION` says otherwise.
- Admin viewer: a replay on each recording's page, with signals on the timeline and as a
  list, and a heatmap page per site (page, device and range pickers; click and scroll-depth
  maps drawn over the newest recorded snapshot of that page). Built from `js/player` on
  rrweb's replayer, sandboxed without scripts.
- The replay strips inline event handlers from recorded pages, and the heatmap
  measures the rebuilt page only after it has been laid out.
- Documentation (`docs/`), a README with the privacy stance, an example project, and CI
  for lint, the core without Django, the Django × Python matrix on SQLite and PostgreSQL,
  bundle reproducibility and the wheel's contents.

