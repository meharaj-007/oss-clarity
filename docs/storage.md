# Storage

Recordings are stored as gzip JSON chunks through Django's storage API, so any
backend in `STORAGES` works. Rows in the database say where each chunk is; the
events themselves are only in storage.

## Local disk

The default storage is fine for one machine:

```python
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": BASE_DIR / "media"},
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
```

Do not serve that folder publicly. Recordings are only ever read through the
admin and the API, which check permissions.

## S3 and compatible services

Install the extra and add a separate entry, so recordings can be private while
your other media is not:

```sh
pip install "oss-clarity[django,s3]"
```

```python
STORAGES = {
    "default": {...},
    "recordings": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": "my-recordings",
            "default_acl": "private",
            "querystring_auth": True,
            "file_overwrite": False,
        },
    },
    "staticfiles": {...},
}

OSS_CLARITY = {"STORAGE": "recordings"}
```

## Layout

```
<STORAGE_PREFIX>/<site id>/<recording id>/<page>-<chunk>.json.gz
```

The recording id is a random UUID. The session id, which comes from the
visitor's browser, never appears in a path.

## Size

A visit is capped at 10 MiB of uncompressed JSON (`MAX_SESSION_BYTES`); gzip
usually shrinks that five to ten times. A typical recorded page view is tens of
kilobytes.

## Deleting

Retention, visitor erasure and deleting from the admin all remove the stored
chunks first and the rows second. If a chunk cannot be deleted (the storage is
unreachable, say), the rows stay so the chunk is not orphaned, and the next run
tries again.
