# js

Sources for the recorder bundle, `src/oss_clarity/static/oss_clarity/recorder.js`.
This package is private and never published.

```sh
cd js
npm ci          # exact versions from package-lock.json
npm run build   # writes the bundle; commit the result
npm run typecheck
npm test        # the recorder's uploads, with rrweb stubbed out
```

The build is deterministic: rebuilding from the lockfile must give a byte-identical
file, and CI fails if the committed bundle differs from a fresh build. It also fails
if the bundle includes a package that `THIRD_PARTY_NOTICES.md` does not name.

The tracker is not built here: it is a template in `src/oss_clarity/tracker.py`,
minified by the server when it is served.
