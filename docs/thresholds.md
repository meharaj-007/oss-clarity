# Thresholds

Every number a signal rule uses is a field of `oss_clarity.core.Thresholds`.
The defaults are first estimates, not measured truths. Measure them against your
own recordings before relying on the counts.

## Changing them

In settings:

```python
OSS_CLARITY = {
    "THRESHOLDS": {"hesitation_min_ms": 2_000, "rage_min_clicks": 4},
}
```

Unknown names are reported by `manage.py check`. From Python, `Thresholds` is
frozen; make a changed copy:

```python
from dataclasses import replace
from oss_clarity.core import Thresholds, analyze

counts, markers = analyze(pages, replace(Thresholds(), hesitation_min_ms=2_000))
```

## Defaults

| Field | Default | Used by |
|---|---|---|
| `rage_window_ms` | 1000 | rage click, repeat submit |
| `rage_radius_px` | 30 | rage click |
| `rage_min_clicks` | 3 | rage click |
| `dead_window_ms` | 1500 | dead click, near miss |
| `error_window_ms` | 1000 | error click |
| `hesitation_min_ms` | 3000 | hesitation |
| `hesitation_max_ms` | 30000 | hesitation |
| `hesitation_radius_px` | 40 | hesitation |
| `hesitation_click_grace_ms` | 1500 | hesitation |
| `near_miss_window_ms` | 2000 | near miss |
| `near_miss_radius_px` | 40 | near miss |
| `scroll_hunt_reversals` | 4 | scroll hunt |
| `scroll_hunt_min_leg_px` | 300 | scroll hunt |
| `repeat_submit_window_ms` | 10000 | repeat submit |
| `refill_min_length` | 3 | form refill |
| `refill_drop_share` | 0.25 | form refill |
| `idle_exit_min_ms` | 20000 | idle exit |
| `short_text_chars` | 60 | hesitation, field labels |

The quick-back window is a setting of its own, `QUICK_BACK_SECONDS` (default 5).

## Measuring

```sh
python manage.py oss_clarity_measure --days 30 --json report.json
```

This reads finished recordings of people (a visit with any crawler hit is left
out), runs every rule at your current thresholds, then again with each threshold
moved up and down, and prints:

- **the gate**: whether there is enough evidence to move a threshold. The
  suggested minimum is 200 recordings from at least 3 sites;
- **the baseline**: for each signal, how many were found, in how many
  recordings, and per 100 pages;
- **the sweeps**: the same counts at each tried value, with `*` on the current
  one;
- **moments to watch**: a few markers of each kind, spread across recordings, to
  open in the replay. Only a person can say whether a hesitation really was one.

It writes nothing to the database. Options: `--site` (domain or id), `--days`,
`--limit`, `--samples`, `--json`.

A threshold is worth moving when the sweep shows the count changing sharply
around it, and watching the samples confirms the new value finds real moments
the old one missed (or drops ones that were not).
