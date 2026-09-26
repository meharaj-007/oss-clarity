# Upgrading

oss-clarity is before 1.0. Until then a minor release may change settings,
routes or the JSON shapes; the [changelog](../CHANGELOG.md) says what changed.

Every upgrade:

1. `pip install -U "oss-clarity[django]"`
2. `python manage.py migrate`
3. `python manage.py collectstatic` if you serve static files that way (the
   admin viewer's `player.js` and `player.css`).
4. `python manage.py check`, which reports settings that are no longer valid.

Nothing needs doing on the tracked sites. The snippet never changes. The tracker
is served fresh every five minutes, and it loads the recorder from a URL that
carries the recorder's own digest, so a new recorder is picked up by the next
page view while visitors mid-visit keep the one they started with. A tracker
cached before an upgrade that asks for an old recorder gets a harmless empty
script.

Recordings made by an older recorder stay readable: the analysis only needs the
rrweb event format, and recordings that lack newer marks (such as whether an
input was typed by a person) are read with the older rule for that case.
