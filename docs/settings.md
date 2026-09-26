# Settings

Everything is configured with one dict in your settings. Every key is optional.

```python
OSS_CLARITY = {
    "RECORDING_RETENTION_DAYS": 14,
    "TRUSTED_PROXY_COUNT": 1,
}
```

`manage.py check` reports unknown keys and wrong types.

## Recording

| Key | Default | Meaning |
|---|---|---|
| `RECORDING_ENABLED` | `True` | Switch recording off for the whole deployment. Page views, clicks and heatmaps still work. Each site is also off until its recording settings say otherwise. |
| `DEFAULT_SAMPLE_PCT` | `100` | Sample percentage for a site's new recording settings. |
| `DEFAULT_MASK_MODE` | `"balanced"` | Mask mode for a site's new recording settings: `strict`, `balanced` or `relaxed`. |
| `MAX_CHUNK_BYTES` | 3 MiB | Largest uncompressed chunk accepted. |
| `MAX_INFLATED_BYTES` | 4 MiB | Largest a gzip body may expand to before it is refused. |
| `MAX_SESSION_BYTES` | 10 MiB | Uncompressed bytes one visit may record. |
| `MAX_MINUTES` | `120` | Minutes one visit may record. |
| `MAX_PAGES` | `128` | Pages one visit may record. |
| `MAX_EVENTS_PER_CHUNK` | `5000` | Events one chunk may carry. |

## Rate limits

Per minute. `0` switches a window off. A refused request answers 204 like
every other refusal, and the site's counters show it.

| Key | Default | Window |
|---|---|---|
| `HIT_RATE_KEY_IP` | `120` | Hits from one address for one site. |
| `HIT_RATE_KEY` | `6000` | Hits for one site. |
| `HIT_RATE_GLOBAL` | `60000` | Hits for the whole deployment. |
| `CHUNK_RATE_KEY_IP` | `60` | Chunks from one address for one site. |
| `CHUNK_RATE_KEY` | `3000` | Chunks for one site. |
| `CHUNK_RATE_GLOBAL` | `30000` | Chunks for the whole deployment. |

The counters live in Django's cache, so use a cache every process shares
(Redis, Memcached, the database cache). If the cache fails, requests are let
through rather than lost.

## Client IP

The address is used in memory for the rate limits only. It is never stored or
logged.

| Key | Default | Meaning |
|---|---|---|
| `TRUSTED_PROXY_COUNT` | `0` | How many proxies in front of Django append to `X-Forwarded-For`. `0` uses `REMOTE_ADDR`. With `n`, the address is the `n`-th entry from the right, so a client cannot choose its own by adding entries. |
| `CLIENT_IP_HEADER` | `None` | A single header your edge sets and overwrites, such as `CF-Connecting-IP` or `X-Real-IP`. Takes precedence over `TRUSTED_PROXY_COUNT`. |
| `CLIENT_IP_FUNCTION` | `None` | Dotted path to `f(request) -> str | None` for anything else. Takes precedence over both. |

**Only trust a header your proxy overwrites.** If the proxy passes a header
through from the client unchanged, anyone can pick their own rate-limit bucket.

## Retention

| Key | Default | Meaning |
|---|---|---|
| `HIT_RETENTION_DAYS` | `30` | Hits, and the visit navigation rows read from them. |
| `RECORDING_RETENTION_DAYS` | `30` | Recordings. A favourite is kept until the hit window instead. |
| `HEATMAP_RETENTION_DAYS` | `400` | Heatmap buckets, which hold counts and no visitor. |

## Analysis

| Key | Default | Meaning |
|---|---|---|
| `QUICK_BACK_SECONDS` | `5` | Time on B under which A, B, A is a quick back. |
| `SESSION_IDLE_MINUTES` | `30` | Quiet time that ends a visit, in the tracker and on the server. |
| `THRESHOLDS` | `{}` | Overrides for the signal thresholds; see [thresholds](thresholds.md). |
| `PAGEVIEW_TAGS` | `("oss_clarity.pageview",)` | Custom event tags read as page views inside a recording. Add others to read recordings made by a tracker that tagged them differently. |
| `ERROR_TAGS` | `("oss_clarity.error",)` | Custom event tags read as script errors. |

## Storage

| Key | Default | Meaning |
|---|---|---|
| `STORAGE` | `"default"` | Which entry of Django's `STORAGES` holds recording chunks. |
| `STORAGE_PREFIX` | `"oss_clarity/recordings"` | Folder the chunks go under. |

See [storage](storage.md).

## URLs, jobs and the API

| Key | Default | Meaning |
|---|---|---|
| `PUBLIC_BASE_URL` | `None` | Origin the snippet and tracker point at, such as `https://collect.example.com`, when the public endpoints are served from another host than the admin. Without it, the host of the current request is used. |
| `JOB_INTERVALS` | `{"finalize": 5, "navigation": 15, "heatmaps": 60, "prune": 1440}` | Minutes between runs of each job. Give only the ones you change. |
| `API_PERMISSION` | `None` | Dotted path to `f(request) -> bool` guarding the JSON API. Without it, only active staff users are allowed. |
