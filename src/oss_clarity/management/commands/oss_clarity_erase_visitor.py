"""Erase everything held about one visitor: recordings (with their stored
chunks), hits and visit navigation. The visitor id comes from the tracker's
`ossClarity("visitorId")` on the visitor's own browser."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from ...models import Site
from ...retention import erase_visitor


class Command(BaseCommand):
    help = "Erase one visitor's recordings, hits and navigation rows."

    def add_arguments(self, parser):
        parser.add_argument("visitor_id")
        parser.add_argument("--site", type=int, help="Only within this site (by id).")
        parser.add_argument("--dry-run", action="store_true", help="Count, delete nothing.")

    def handle(self, *args, visitor_id, site=None, dry_run=False, **options):
        scope = None
        if site is not None:
            scope = Site.objects.filter(pk=site).first()
            if scope is None:
                raise CommandError(f"no site with id {site}")
        result = erase_visitor(visitor_id, scope, dry_run=dry_run)
        verb = "would erase" if dry_run else "erased"
        self.stdout.write(
            f"{verb}: {result['recordings']} recordings ({result['chunks']} chunks), "
            f"{result['hits']} hits, {result['navigation']} navigation rows"
        )
        if result["failed"]:
            raise CommandError(
                f"{result['failed']} recordings kept: stored chunks could not be deleted. "
                "Run the command again."
            )
