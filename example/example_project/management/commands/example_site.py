from django.core.management.base import BaseCommand

from oss_clarity.models import RecordingSettings, Site


class Command(BaseCommand):
    help = "Create the example site, with recording switched on."

    def handle(self, *args, **options):
        site, created = Site.objects.get_or_create(
            domain="localhost",
            defaults={"name": "Example", "allowed_hosts": ["127.0.0.1"]},
        )
        RecordingSettings.objects.update_or_create(
            site=site, defaults={"enabled": True, "sample_pct": 100}
        )
        verb = "Created" if created else "Found"
        self.stdout.write(f"{verb} site {site.name!r} for localhost, recording on.")
        self.stdout.write("Open http://localhost:8000/ and click around.")
