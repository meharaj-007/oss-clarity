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
