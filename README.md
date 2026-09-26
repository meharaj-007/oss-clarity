# oss-clarity

Self-hosted session replay, heatmaps and rules-based behaviour signals for Django.

> Early development. Nothing is published yet and the API will change.

## What it does

- **Tracking**: page views, clicks and scroll depth from a small script on your site.
- **Recording**: session replay built on [rrweb](https://github.com/rrweb-io/rrweb), masked by default.
- **Rules-based analysis**: frustration and behaviour signals (rage, dead and error clicks,
  hesitation, near misses, scroll hunts, form skips, refills and abandons, repeat submits,
  copy-outs, idle exits, quick backs and loops). Every signal is a documented, deterministic rule
  with named thresholds. There is no AI, and recordings never leave your servers.

## The `core` library

`oss_clarity.core` is pure Python with no dependencies, usable without Django:

```python
from dataclasses import replace
from oss_clarity.core import Thresholds, analyze

counts, markers = analyze(pages)  # pages: [(page_id, rrweb_events)], in visit order
counts, markers = analyze(pages, replace(Thresholds(), hesitation_min_ms=2_000))
```

The rule definitions live in the docstring of `oss_clarity/core/signals.py`.

## Licence

MIT. See [LICENSE](LICENSE).
