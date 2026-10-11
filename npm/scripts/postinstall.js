#!/usr/bin/env node
// =============================================================================
// Post-install check: verify that the bundled application actually runs here.
//
// A failed check is *reported*, not fatal — npm installs run in odd
// environments (read-only caches, cross-compiled images) where a smoke test can
// legitimately fail while the package is fine.  What matters is that the user
// sees a precise reason instead of a mysterious "command not found" later.
// =============================================================================
'use strict';

const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const launcher = path.join(__dirname, '..', 'bin', 'sr');

if (process.platform === 'win32') {
  process.stdout.write(
    '[sr] installed, but Windows is not supported natively — use WSL2.\n'
  );
  process.exit(0);
}

if (!fs.existsSync(launcher)) {
  process.stdout.write(`[sr] launcher missing at ${launcher}\n`);
  process.exit(0);
}

const result = spawnSync(launcher, ['--version'], { encoding: 'utf8', timeout: 20000 });
if (result.status === 0) {
  const version = String(result.stdout).split('\n')[0].trim();
  process.stdout.write(`[sr] ${version || 'installed'} is ready.\n`);
  process.stdout.write('[sr] Run `sr help` for the manual, `sr selftest` to verify.\n');
  process.exit(0);
}

process.stdout.write('[sr] installed, but the smoke test failed:\n');
process.stdout.write(String(result.stderr || result.stdout || '(no output)').split('\n').slice(0, 6).map((l) => `     ${l}`).join('\n') + '\n');
process.stdout.write('[sr] sr needs Python 3.9+; set SR_PYTHON=/path/to/python3 if it is not on PATH.\n');
process.exit(0);
