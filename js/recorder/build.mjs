/**
 * Build the recorder into src/oss_clarity/static/oss_clarity/recorder.js, and
 * the admin viewer into player.js and player.css beside it.
 *
 * Each is a minified IIFE. The output is committed: the Python package ships it,
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
const staticDir = resolve(root, "src/oss_clarity/static/oss_clarity");
const outfile = resolve(staticDir, "recorder.js");

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

const player = await build({
  entryPoints: { player: resolve(root, "js/player/index.ts") },
  bundle: true,
  minify: true,
  format: "iife",
  target: ["es2019"],
  outdir: staticDir,
  legalComments: "none",
  metafile: true,
  loader: { ".css": "css" },
  banner: {
    js:
      "/*! oss-clarity viewer | MIT | built from js/player, do not edit by hand. " +
      "Includes rrweb and its dependencies, MIT licensed; see THIRD_PARTY_NOTICES.md */",
    css: "/*! oss-clarity viewer | MIT | includes rrweb's replayer styles (MIT) */",
  },
});
for (const name of ["player.js", "player.css"]) {
  const file = resolve(staticDir, name);
  writeFileSync(file, readFileSync(file, "utf8").replace(/\/\/# sourceMappingURL=\S+/g, ""));
}

const packages = new Set();
const inputs = [...Object.keys(result.metafile.inputs), ...Object.keys(player.metafile.inputs)];
for (const input of inputs) {
  const match = input.match(/node_modules\/((?:@[^/]+\/)?[^/]+)/);
  if (match) packages.add(match[1]);
}
const notices = readFileSync(resolve(root, "THIRD_PARTY_NOTICES.md"), "utf8");
const missing = [...packages].filter((name) => !notices.includes(`\`${name}\``));
if (missing.length) {
  console.error(`THIRD_PARTY_NOTICES.md does not list: ${missing.join(", ")}`);
  process.exit(1);
}

const size = (name) => `${(statSync(resolve(staticDir, name)).size / 1024).toFixed(1)} KB`;
console.log(
  `recorder.js ${size("recorder.js")}, player.js ${size("player.js")}, ` +
    `player.css ${size("player.css")}; bundles ${[...packages].sort().join(", ")}`,
);
