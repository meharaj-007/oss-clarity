# oss-clarity

[![CI](https://github.com/meharaj-007/oss-clarity/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/meharaj-007/oss-clarity/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/oss-clarity)](https://pypi.org/project/oss-clarity/)
[![Python](https://img.shields.io/pypi/pyversions/oss-clarity)](https://pypi.org/project/oss-clarity/)
[![Django](https://img.shields.io/pypi/frameworkversions/django/oss-clarity)](https://pypi.org/project/oss-clarity/)
[![License](https://img.shields.io/pypi/l/oss-clarity)](https://github.com/meharaj-007/oss-clarity/blob/main/LICENSE)

Self-hosted session replay, heatmaps and rules-based behaviour signals for Django.

Add one script tag to your site. oss-clarity counts page views, clicks and scroll
depth, records sampled visits with [rrweb](https://github.com/rrweb-io/rrweb), and
marks where people struggled: rage clicks, dead clicks, hesitations, form
abandons and more. You watch the replays and heatmaps in your Django admin. Every
byte stays on your servers.

> Early release (0.1.2).
> Settings and the JSON API may still change.

![A replay in the Django admin, with signals on the timeline](https://raw.githubusercontent.com/meharaj-007/oss-clarity/v0.1.2/docs/images/replay.png)

## What you get

- **Tracking**: page views (including single-page-app route changes), clicks with
  their position, and how far each page was scrolled. About 10 KB of JavaScript
  (4 KB gzipped), reporting after the page loads, with no cookies.
- **Recording**: sampled session replay, masked in the visitor's browser before
  anything is sent. Capped per visit by size, pages and minutes.
- **Signals**: 15 rules, each a documented, deterministic check with named
  thresholds, and a tool to measure those thresholds against your own recordings.
  See [signals](https://github.com/meharaj-007/oss-clarity/blob/v0.1.2/docs/signals.md).
- **Heatmaps**: clicks and scroll depth per page and device, drawn over a recorded
  snapshot of the page.
- **Viewer**: replay and heatmap pages inside Django admin, and a read-only
  [JSON API](https://github.com/meharaj-007/oss-clarity/blob/v0.1.2/docs/api.md).

![Clicks on a page, drawn over a recorded snapshot](https://raw.githubusercontent.com/meharaj-007/oss-clarity/v0.1.2/docs/images/heatmap-clicks.png)

## Privacy

- **No AI.** Signals are plain rules you can read. Recordings are never sent to
  any third party or model.
- **Masked by default.** Form fields are masked in every mode and cannot be
  unmasked. The default mode also blanks digits and email addresses, and `data:`
  and `blob:` images (a photo a visitor just picked, say) are never recorded.
- **Global Privacy Control is honoured.** A browser that sends it is never
  recorded; its page views are still counted.
- **Optional consent gate.** Per site, nothing is recorded until your page calls
  `ossClarity("consent", true)`.
- **No IP addresses stored.** The address is used in memory for rate limits only.
  Raw user agents are not stored either, only coarse device, browser and OS.
- **No text from clicks.** A click records where it landed and a selector for the
  element, never the element's text or link. Password, token and similar values in
  page URLs are replaced before storage.
- **Crawlers are not recorded.** Bots that run JavaScript are refused by the
  tracker and again by the collector.
- **Erase on request.** `ossClarity("visitorId")` gives a visitor their id;
  `manage.py oss_clarity_erase_visitor <id>` deletes everything held about them.
  `ossClarity("forget")` clears the ids in their browser.
- **Retention.** Hits and recordings are deleted after 30 days by default.

## Install

```sh
pip install "oss-clarity[django]"
```

Python 3.11 to 3.14, Django 5.2, 6.0 and 6.1, SQLite or PostgreSQL.

## Set up

**1. Settings**

```python
INSTALLED_APPS = [..., "oss_clarity"]

MIDDLEWARE = [
    "oss_clarity.middleware.PublicEndpointsMiddleware",  # first
    ...,
]
```

The middleware is only needed if something else in your stack (django-cors-headers,
a no-store cache policy) would override the public endpoints' headers. It is safe
to always include. Every other setting has a default; see
[settings](https://github.com/meharaj-007/oss-clarity/blob/v0.1.2/docs/settings.md).

**2. URLs**

```python
urlpatterns = [
    ...,
    path("oc/", include("oss_clarity.urls.public")),  # tracker and collector
    path("oc-api/", include("oss_clarity.urls.api")),  # read-only API, optional
]
```

Then `python manage.py migrate`.

**3. Jobs**, every five minutes from cron (or use the
[Celery tasks](https://github.com/meharaj-007/oss-clarity/blob/v0.1.2/docs/jobs.md)):

```cron
*/5 * * * *  cd /srv/app && python manage.py oss_clarity_run_jobs
```

**4. The snippet.** Add a site in the admin (Session replay and heatmaps → Sites),
switch recording on in its recording settings if you want replays, and paste the
snippet it shows into your pages' `<head>`:

```html
<script async src="https://your-app.example.com/oc/t/<site key>.js"></script>
```

Visits appear as hits straight away. Recordings are analysed a few minutes after a
visit ends, and heatmaps update hourly.

To see it all working on your own machine first, run the
[example project](https://github.com/meharaj-007/oss-clarity/blob/v0.1.2/example/README.md).

## Documentation

- [Signals](https://github.com/meharaj-007/oss-clarity/blob/v0.1.2/docs/signals.md): what each one means
- [Thresholds](https://github.com/meharaj-007/oss-clarity/blob/v0.1.2/docs/thresholds.md): the numbers, and how to measure them
- [Settings](https://github.com/meharaj-007/oss-clarity/blob/v0.1.2/docs/settings.md): every key
- [Storage](https://github.com/meharaj-007/oss-clarity/blob/v0.1.2/docs/storage.md): local disk or S3
- [Jobs](https://github.com/meharaj-007/oss-clarity/blob/v0.1.2/docs/jobs.md): cron or Celery
- [JSON API](https://github.com/meharaj-007/oss-clarity/blob/v0.1.2/docs/api.md)
- [Upgrading](https://github.com/meharaj-007/oss-clarity/blob/v0.1.2/docs/upgrading.md)

## Limits

- One recording per visit, at most 10 MiB, 128 pages and 120 minutes by default.
- Replays load images, fonts and styles from your live site, so ones changed or
  removed since render differently.
- Heatmaps group pages by path (query strings ignored, trailing slashes folded)
  and by device class. Clicks are placed on the element when the snapshot still
  has it, and by position otherwise.
- Automated browsers (`navigator.webdriver`, headless Chrome) are treated as
  crawlers and never recorded.
- No multi-tenant accounts, billing or quotas: that is your project's business.

## Development

```sh
pip install -e ".[django,dev]"
pytest
ruff check . && ruff format --check .
cd js && npm ci && npm run typecheck && npm run build   # rebuilds the committed bundles
```

## Licence

MIT. See [LICENSE](https://github.com/meharaj-007/oss-clarity/blob/v0.1.2/LICENSE).
The bundled rrweb and its dependencies are listed in
[THIRD_PARTY_NOTICES.md](https://github.com/meharaj-007/oss-clarity/blob/v0.1.2/THIRD_PARTY_NOTICES.md).
