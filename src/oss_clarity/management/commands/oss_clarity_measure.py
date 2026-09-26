"""Measure the signal thresholds against the stored recordings.

Reads finished recordings of people (a visit with any crawler hit is left
out), runs the rules at the configured thresholds and with each threshold
moved, and prints how many signals each setting would find, with sample
moments to watch. Writes nothing to the database.

    python manage.py oss_clarity_measure --days 30 --json report.json
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from ...analysis import page_views
from ...choices import RecordingStatus
from ...conf import settings
from ...core import LoadedRecording, measure
from ...models import Hit, Recording
from ...storage import all_events


def recordings_to_measure(*, site: str = "", days: int = 0, limit: int = 0):
    """Finished recordings of people, newest first."""
    queryset = Recording.objects.exclude(status=RecordingStatus.RECORDING).select_related("site")
    if site:
        match = Q(site__domain__iexact=site)
        if site.isdigit():
            match |= Q(site_id=int(site))
        queryset = queryset.filter(match)
    if days:
        queryset = queryset.filter(started_at__gte=timezone.now() - timedelta(days=days))
    crawlers = Hit.objects.filter(is_bot=True).values("session_id")
    queryset = queryset.exclude(session_id__in=crawlers).order_by("-started_at")
    return queryset[:limit] if limit else queryset


def load(recordings) -> tuple[list[LoadedRecording], int]:
    loaded, failed = [], 0
    for recording in recordings:
        try:
            pages = all_events(recording)
        except Exception:
            failed += 1
            continue
        loaded.append(
            LoadedRecording(
                id=str(recording.pk),
                site=recording.site.domain,
                pages=pages,
                page_views=page_views(recording.site_id, recording.session_id),
            )
        )
    return loaded, failed


class Command(BaseCommand):
    help = "Measure how many signals each threshold setting would find. Writes nothing."

    def add_arguments(self, parser):
        parser.add_argument("--site", default="", help="A site's domain or id.")
        parser.add_argument("--days", type=int, default=0, help="Only recordings this recent.")
        parser.add_argument("--limit", type=int, default=0, help="At most this many recordings.")
        parser.add_argument("--samples", type=int, default=5, help="Moments to watch per signal.")
        parser.add_argument("--json", dest="json_path", help="Also write the full report here.")

    def handle(self, *args, site="", days=0, limit=0, samples=5, json_path=None, **options):
        if min(days, limit, samples) < 0:
            raise CommandError("--days, --limit and --samples cannot be negative")
        loaded, failed = load(recordings_to_measure(site=site, days=days, limit=limit))
        report = measure(
            loaded,
            thresholds=settings.thresholds,
            quick_back_seconds=settings.QUICK_BACK_SECONDS,
            samples=samples,
            failed=failed,
            pageview_tags=settings.PAGEVIEW_TAGS,
            error_tags=settings.ERROR_TAGS,
        )
        self._print(report)
        if json_path:
            with open(json_path, "w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2, default=str)
            self.stdout.write(f"\nFull report written to {json_path}")

    def _print(self, report: dict) -> None:
        write = self.stdout.write
        gate = report["gate"]

        def met(part):
            return "met" if part["met"] else "NOT met"

        write(
            f"Recordings {report['recordings']} ({report['failed']} unreadable), "
            f"pages {report['pages']}, sites {len(report['sites'])}"
        )
        write(
            f"Gate: recordings {gate['recordings']['have']}/{gate['recordings']['need']} "
            f"{met(gate['recordings'])}, sites {gate['sites']['have']}/{gate['sites']['need']} "
            f"{met(gate['sites'])}"
        )
        if not (gate["recordings"]["met"] and gate["sites"]["met"]):
            write("Too little evidence to move a threshold yet; the numbers below are indicative.")

        write("\nBaseline (signals, recordings with one, share of recordings, per 100 pages)")
        for kind, row in report["baseline"].items():
            write(
                f"  {kind:<14} {row['total']:>7} {row['recordings']:>7} "
                f"{row['share']:>7.1%} {row['per_100_pages']:>8.1f}"
            )

        write("\nSweeps (value: signals / recordings; * is the current value)")
        for name, sweep in report["sweeps"].items():
            write(f"  {name} ({', '.join(sweep['signals'])})")
            for row in sweep["rows"]:
                cells = ", ".join(
                    f"{kind} {v['total']}/{v['recordings']}" for kind, v in row["signals"].items()
                )
                write(f"    {'*' if row['current'] else ' '} {row['value']!s:>8}: {cells}")

        if report["samples"]:
            write("\nWatch these")
            for kind, entries in report["samples"].items():
                for entry in entries:
                    seconds = entry["t_ms"] / 1000
                    write(
                        f"  {kind:<14} recording {entry['recording']} at {seconds:.1f}s  "
                        f"{entry['label'] or entry['selector']}"
                    )
