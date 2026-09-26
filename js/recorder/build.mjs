/**
 * Build the recorder into src/oss_clarity/static/oss_clarity/recorder.js.
 *
 * One minified IIFE. The output is committed: the Python package ships it,
 * and the tracker's recorder URL carries its digest, so a rebuild is a new URL.
 * CI rebuilds from the lockfile and fails if the committed file differs.
 *
 * Every bundled package must be listed in THIRD_PARTY_NOTICES.md; the build
 * fails if one is not, so a dependency cannot arrive without its notice.
 */

import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { readFileSync, statSync, writeFileSync } from "node:fs";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "../..");
const outfile = resolve(root, "src/oss_clarity/static/oss_clarity/recorder.js");

const result = await build({
  entryPoints: [resolve(here, "index.ts")],
  bundle: true,
  minify: true,
  format: "iife",
  target: ["es2019"],
  outfile,
  legalComments: "none",
  metafile: true,
  banner: {
    js:
      "/*! oss-clarity recorder | MIT | built from js/recorder, do not edit by hand. " +
      "Includes rrweb and its dependencies, MIT licensed; see THIRD_PARTY_NOTICES.md */",
  },
});

// rrweb inlines a worker whose source ends in a `sourceMappingURL` comment.
// No map is shipped, and Django's manifest storage refuses a file that points
// at a missing one. Removing the comment changes nothing at runtime.
const built = readFileSync(outfile, "utf8").replace(/\/\/# sourceMappingURL=\S+/g, "");
writeFileSync(outfile, built);

const packages = new Set();
for (const input of Object.keys(result.metafile.inputs)) {
  const match = input.match(/node_modules\/((?:@[^/]+\/)?[^/]+)/);
  if (match) packages.add(match[1]);
}
const notices = readFileSync(resolve(root, "THIRD_PARTY_NOTICES.md"), "utf8");
const missing = [...packages].filter((name) => !notices.includes(`\`${name}\``));
if (missing.length) {
  console.error(`THIRD_PARTY_NOTICES.md does not list: ${missing.join(", ")}`);
  process.exit(1);
}

const bytes = statSync(outfile).size;
console.log(`recorder: ${(bytes / 1024).toFixed(1)} KB, bundles ${[...packages].sort().join(", ")}`);
