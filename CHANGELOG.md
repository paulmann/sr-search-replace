# Changelog

All notable changes to the Search and Replace (sr) utility will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [6.0.0] - 2024-01-12

### Added

#### Core Features
- **Session-based rollback system**: Complete restoration of all files modified in a session
  - Unique session IDs with nanosecond precision
  - Full metadata tracking including original command, search/replace strings
  - One-command rollback: `sr --rollback`
  - Specific session restoration: `sr --rollback=sr.backup.20240112_143022`
  - Backup listing with session information: `sr --rollback-list`

#### Binary File Detection
- **Multi-layer binary file detection algorithm**:
  - Layer 1: Fast grep heuristic on first N bytes (configurable)
  - Layer 2: File utility MIME type analysis (if available)
  - Layer 3: Fallback detection with safety-first approach
- Three detection methods: `multi_layer` (default), `file_only`, `grep_only`
- Configurable binary check size: `--binary-check-size=N`
- Explicit `--binary` flag required for binary file processing
- Safe defaults prevent accidental corruption

#### Session Management
- Session metadata tracking and persistent storage
- Modified file list tracking per session
- Session archive structure with metadata files
- Session-specific backup directories with unique identifiers
- Backup retention management: `--max-backups=N`
- Interactive rollback confirmation with timeout

#### Enhanced Logging & Reporting
- Detailed debug mode with execution traces
- Verbose mode with progress reporting
- Color-coded console output for better readability
- Comprehensive statistics display
- Session information in summary report
- Rollback debugging with multi-method file discovery

### Changed

#### Architecture
- Improved file processing pipeline with multiple discovery modes
- Better handling of large file sets with progress reporting
- More robust error handling and recovery mechanisms
- Enhanced backup directory structure with metadata tracking

#### Performance
- Optimized binary detection for faster file scanning
- Efficient session metadata management
- Better handling of large backup sets
- Improved rollback performance with parallelization support

#### User Experience
- More intuitive command-line argument parsing
- Better help and usage messages
- Clearer error messages with actionable guidance
- Session ID format now includes nanosecond precision
- Improved confirmation prompts with timeout

### Fixed

- Resolved file list encoding issues in rollback operations
- Fixed permission preservation in edge cases
- Corrected whitespace handling in file paths
- Improved BSD sed compatibility
- Better handling of special characters in replacement strings
- Fixed array initialization in bash 4.0

### Security

- Added path validation to prevent directory traversal attacks
- Implemented control character filtering in file lists
- Added dangerous pattern detection (../,/proc/*,/sys/*,/dev/*)
- Enhanced permission handling for backup directories
- Safer temporary file creation with proper cleanup

### Documentation

- Comprehensive README with examples and best practices
- Detailed option reference with all parameters
- Environment variable documentation
- Troubleshooting guide with common issues
- Performance characteristics and benchmarks
- Advanced usage scenarios

## [5.x.x] - Previous Versions

Previous releases focused on basic search and replace functionality with initial backup support.
Those versions did not include the advanced rollback system and multi-layer binary detection
features present in version 6.0.0.

### Key Differences from v5

- v6.0.0 introduces session-based backups vs simple file-level backups in v5
- v6.0.0 adds one-command rollback capability not available in v5
- v6.0.0 implements multi-layer binary detection vs simple method in v5
- v6.0.0 includes comprehensive metadata tracking in v5 was minimal
- v6.0.0 adds named backup directories with clear session identification

## Version Support

Current versions:
- **6.0.0**: Latest stable release (recommended)
- **5.x.x**: Deprecated, no longer maintained

## Migration Guide

For users upgrading from version 5:

1. Download version 6.0.0
2. Replace sr script: `cp sr.sh /usr/local/bin/sr`
3. Existing backups from v5 will be managed separately from v6 sessions
4. No data loss - all previous backups remain accessible
5. New releases will use v6 session format

## Planned Features (Roadmap)

- [ ] Parallel processing for large file sets
- [ ] Remote execution via SSH
- [ ] Git integration for automatic commits
- [ ] Web UI for non-technical users
- [ ] Configuration file support (.srrc)
- [ ] Statistics database for historical tracking
- [ ] Notification system for long operations
- [ ] Custom filter plugins

## Known Issues

No known critical issues in v6.0.0

For feature requests and bug reports, please visit:
https://github.com/paulmann/sr-search-replace/issues

---

**Last Updated**: 2024-01-12
**Maintained By**: Mikhail Deynekin (@paulmann)
