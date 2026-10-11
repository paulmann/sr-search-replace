#!/usr/bin/env node
// =============================================================================
// Build npm/app/sr.pyz from the repository sources.
//
// Runs on `npm publish` (via the `prepare` lifecycle script) and from
// `make npm`.  It shells out to the same builder that produces the GitHub
// release asset, so the npm package and the standalone download are byte
// identical for a given version.
// =============================================================================
'use strict';

const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const npmRoot = path.join(__dirname, '..');
const repoRoot = path.join(npmRoot, '..');
const builder = path.join(repoRoot, 'tools', 'build_zipapp.py');
const output = path.join(npmRoot, 'app', 'sr.pyz');

function fail(message) {
  process.stderr.write(`[sr-pack] ${message}\n`);
  process.exit(1);
}

if (!fs.existsSync(builder)) {
  // Publishing from the npm/ directory alone (e.g. an npm-only checkout) is not
  // supported: the app must be built from the sources it ships.
  fail(`builder not found: ${builder}\n     run this from a full source checkout`);
}

const python = process.env.SR_PYTHON || 'python3';
const result = spawnSync(python, [builder, '--output', output], {
  stdio: 'inherit',
  cwd: repoRoot,
});
if (result.status !== 0) fail(`the zipapp build failed (exit ${result.status})`);

fs.chmodSync(output, 0o755);
const launcher = path.join(npmRoot, 'bin', 'sr');
if (fs.existsSync(launcher)) fs.chmodSync(launcher, 0o755);

// npm renders `README.md` on the package page and requires a LICENSE file to
// match the declared licence.  Neither lives in npm/ (they are the repository's
// own copies), so they are bundled here — otherwise a manual `npm publish` from
// this directory ships a package with no README and no licence text.
const metadata = ['LICENSE', 'README.md', 'CHANGELOG.md'];
for (const name of metadata) {
  const source = path.join(repoRoot, name);
  if (!fs.existsSync(source)) {
    fail(`missing ${source}; npm needs it (declared in npm/package.json "files")`);
  }
  fs.copyFileSync(source, path.join(npmRoot, name));
}
process.stdout.write(`[sr-pack] bundled ${metadata.join(', ')}\n`);

const size = fs.statSync(output).size;
process.stdout.write(`[sr-pack] wrote ${output} (${Math.round(size / 1024)} KiB)\n`);
