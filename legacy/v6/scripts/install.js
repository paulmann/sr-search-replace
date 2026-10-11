#!/usr/bin/env node
// =============================================================================
// sr-search-replace — postinstall script
// Author : Mikhail Deynekin <mid1977@gmail.com> | https://deynekin.com
// License: MIT
// =============================================================================
'use strict';

const fs   = require('fs');
const path = require('path');
const os   = require('os');

if (os.platform() === 'win32') {
  // Skip silently on Windows — the wrapper already handles the error.
  process.exit(0);
}

const srScript = path.join(__dirname, '..', 'sr.sh');
const binEntry = path.join(__dirname, '..', 'bin', 'sr');

// Ensure sr.sh is executable
if (fs.existsSync(srScript)) {
  try {
    fs.chmodSync(srScript, 0o755);
  } catch {
    // Best-effort; npm may lack permissions in some environments
  }
}

// Ensure the bin wrapper is executable
if (fs.existsSync(binEntry)) {
  try {
    fs.chmodSync(binEntry, 0o755);
  } catch {
    // Best-effort
  }
}

console.log('[sr] sr-search-replace v6.1.0 is ready.');
console.log('[sr] Run `sr --help` to get started.');
