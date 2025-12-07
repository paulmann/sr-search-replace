# Search & Replace (sr) - Universal Text Replacement Utility v6.0.0

[![Version](https://img.shields.io/badge/version-6.0.0-blue.svg)](https://github.com/paulmann/sr-search-replace/releases/tag/v6.0.0)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Bash](https://img.shields.io/badge/bash-5.0+-brightgreen.svg)](https://www.gnu.org/software/bash/)
[![Linux](https://img.shields.io/badge/linux-compatible-blue.svg)](#system-requirements)
[![macOS](https://img.shields.io/badge/macos-compatible-blue.svg)](#system-requirements)

> **Powerful, production-ready search and replace utility with session-based rollback, binary file detection, and advanced backup management**

## Overview

`sr` is a professional-grade command-line tool designed to safely perform recursive text replacements across multiple files with comprehensive safety features, intuitive session tracking, and emergency rollback capabilities. Built with enterprise deployments in mind, it provides developers and system administrators with a robust, predictable, and auditable replacement mechanism.

Unlike simplistic alternatives, `sr` implements:
- **Multi-layer binary detection** to prevent accidental corruption of binary files
- **Session-based backup management** with complete audit trails and metadata tracking
- **One-command rollback** to instantly restore all modified files from a previous session
- **Ownership and permission preservation** to maintain filesystem integrity
- **Escape sequence handling** to safely process regex special characters
- **Dry-run mode** for risk-free testing before actual modifications

## Key Features

### Core Functionality

✨ **Recursive File Processing**
- Recursive directory traversal with configurable depth limits
- Single and multi-file processing modes
- Support for shell glob patterns with proper escaping
- Predictable argument parsing to eliminate confusion

🔍 **Advanced Binary Detection**
- Multi-layer detection: grep heuristic → file utility → MIME type analysis
- Three detection methods: `multi_layer` (default), `file_only`, `grep_only`
- Configurable binary check size for performance tuning
- Automatic exclusion with optional `--binary` flag to override

💾 **Intelligent Backup System**
- Automatic backup creation before modifications (default behavior)
- Session-based backup organization with unique session IDs
- Complete metadata tracking (command, search/replace strings, timestamps)
- File list tracking for accurate rollback operations
- Configurable backup retention (keep last N backups)

⏮️ **Session Rollback**
- One-command restoration: `sr --rollback`
- Restore specific sessions: `sr --rollback=sr.backup.20240112_143022`
- Interactive confirmation with 30-second timeout
- Complete file set restoration including permissions and ownership
- Detailed rollback status reporting

🔐 **Safety & Security**
- Dry-run mode for non-destructive testing (`--dry-run`)
- Maximum file size limits to prevent processing of huge files
- Automatic exclusion of dangerous directories (.git, node_modules, etc.)
- Hidden file filtering capability
- Configurable exclude patterns and directory lists
- Atomic operations with proper error handling

🎯 **Output Control**
- In-place modification (default)
- Copy mode to save results to separate directory
- Backup-only mode for creating backups without modifications
- Support for multiple replacement modes

### Advanced Capabilities

📊 **Comprehensive Logging**
- Debug mode with detailed execution traces
- Verbose mode with high-level progress updates
- Color-coded output for better readability
- Session metadata tracking and reporting
- Detailed statistics (files processed, replacements made, etc.)

⚙️ **Flexible Configuration**
- Environment variable overrides for all major options
- Customizable delimiters for sed operations
- Configurable file encoding handling
- Timestamp format customization
- Temporary directory configuration

🔧 **Performance Features**
- Efficient directory traversal with depth limiting
- File size filtering to skip unnecessarily large files
- Batch processing with progress reporting
- Configurable check size for binary detection
- Fast pattern matching with find/grep optimization

## Installation

### Prerequisites

The tool requires a POSIX-compliant shell environment with standard GNU/BSD utilities:

```bash
# Required commands
bash (5.0 or higher)
find
sed
grep

# Highly recommended
file       # For improved binary detection
stat       # For ownership/permission preservation
touch      # For timestamp management
```

### Quick Start

1. **Download the script:**

```bash
cd /tmp
git clone https://github.com/paulmann/sr-search-replace.git
cd sr-search-replace
```

2. **Make it executable:**

```bash
chmod 755 sr.sh
```

3. **Install globally (optional):**

```bash
sudo cp sr.sh /usr/local/bin/sr
```

4. **Verify installation:**

```bash
sr --version
```

## Usage

### Basic Syntax

```bash
sr [OPTIONS] "FILE_PATTERN" "SEARCH_STRING" "REPLACE_STRING"
```

**Important:** Options must come BEFORE positional arguments for predictable parsing.

### Common Examples

#### Simple replacement in HTML files

```bash
sr "*.html" "old-domain.com" "new-domain.com"
```

#### Recursive replacement with backups (default behavior)

```bash
sr "*.js" "function oldName" "function newName"
```

#### Test changes without modifying files

```bash
sr --dry-run "*.conf" "localhost:3000" "localhost:8080"
```

#### Non-recursive search in current directory only

```bash
sr -nr "*.txt" "search_term" "replacement"
```

#### Process binary files (with explicit confirmation)

```bash
sr --binary "*.bin" "pattern" "replacement"
```

#### Verbose output with detailed progress

```bash
sr -v "*.log" "ERROR" "WARNING"
```

#### Restore from previous session

```bash
# Restore latest backup
sr --rollback

# Restore specific session
sr --rollback=sr.backup.20240112_143022

# List available backups
sr --rollback-list
```

### Option Reference

#### Core Options

| Option | Description |
|--------|-------------|
| `-d, --debug` | Enable detailed debug output with execution traces |
| `-v, --verbose` | Enable verbose mode with progress updates |
| `-nr, --no-recursive` | Search only in current directory (non-recursive) |
| `--dry-run` | Show what would be changed without modifications |
| `-h, --help` | Display comprehensive help message |
| `-V, --version` | Show version information |

#### Backup & Safety Options

| Option | Description |
|--------|-------------|
| `-nb, --no-backup` | Disable backup creation |
| `-fb, --force-backup` | Force backup creation regardless of settings |
| `-nbf, --no-backup-folder` | Store backups in same directory as original |
| `--rollback[=DIR]` | Restore from backup (latest or specified) |
| `--rollback-list` | List available backups with session info |
| `--max-backups=N` | Keep only N latest backups (default: 10) |

#### Binary Processing Options

| Option | Description |
|--------|-------------|
| `--binary` | Allow processing of binary files (REQUIRED) |
| `--binary-method=METHOD` | Detection method: multi_layer, file_only, grep_only |
| `--binary-check-size=N` | Bytes to check for binary detection (default: 1024) |

#### Advanced Options

| Option | Description |
|--------|-------------|
| `-md, --max-depth NUM` | Maximum directory depth (default: 100) |
| `-xs, --max-size MB` | Maximum file size in MB (default: 100) |
| `-xp, --exclude-patterns PATTERNS` | Exclude file patterns (space-separated) |
| `-xd, --exclude-dirs DIRS` | Exclude directory names (space-separated) |
| `-xh, --exclude-hidden` | Exclude hidden files and directories |
| `-sd, --search-dir DIR` | Search directory (default: current) |
| `-od, --output-dir DIR` | Output directory for copy mode |
| `-mode, --replace-mode MODE` | Mode: inplace, copy, or backup_only |
| `-no-preserve` | Don't preserve file ownership |

### Environment Variables

Configure default behavior via environment variables:

```bash
export SR_DEBUG=true
export SR_DRY_RUN=true
export SR_NO_BACKUP=true
export SR_FORCE_BACKUP=true
export SR_MAX_DEPTH=50
export SR_VERBOSE=true

sr "*.conf" "old" "new"
```

## How It Works

### Session Management

Each execution creates a unique session identified by timestamp and nanosecond precision:

```
sr.backup.20240112_143022_123456789/
├── .sr_session_metadata      # Complete session information
├── .sr_modified_files        # List of modified files
├── .sr_file_info            # Additional file metadata  
└── [preserved_files]/        # Exact copies of original files
```

### File Processing Flow

1. **Validation Phase**: Check environment, permissions, paths
2. **File Discovery**: Find files matching pattern with exclusions
3. **Pre-Processing**: Get file metadata (owner, permissions, size)
4. **Backup Creation**: Store original files with metadata
5. **Modification**: Apply sed replacements with proper escaping
6. **Verification**: Check replacements succeeded
7. **Metadata Update**: Track modified files in session
8. **Reporting**: Display summary with statistics

### Binary File Detection

The default `multi_layer` method:

1. **Layer 1**: Fast grep heuristic on first 1024 bytes
2. **Layer 2**: File utility MIME type analysis (if available)
3. **Decision**: Safe classification with zero false positives

This provides near-instant detection without sacrificing accuracy.

## Best Practices

### Development Workflow

```bash
# 1. Test with dry-run first
sr --dry-run -v "*.ts" "oldFunction" "newFunction"

# 2. Execute actual replacement
sr -v "*.ts" "oldFunction" "newFunction"

# 3. Verify results
git diff

# 4. If needed, rollback instantly
sr --rollback
```

### Large-Scale Deployments

```bash
# Set safe limits for production
sr -md 5 -xs 50 "*.conf" "localhost" "production.com"

# Force backups for critical changes
sr -fb "*.sql" "old_table" "new_table"

# Keep detailed audit trail
sr -v "*.code" "config.DEBUG" "config.PRODUCTION"
```

### Version Control Integration

```bash
# Before committing, test in separate branch
git checkout -b feature/update-config
sr -v "*.yml" "dev.api.url" "prod.api.url"
git diff              # Review changes
sr --rollback         # Rollback if needed
git commit -am "Update configuration"
```

## Error Handling

### Common Issues

**"No files found matching pattern"**
- Ensure pattern is properly quoted: `sr "*.html"` not `sr *.html`
- Check file pattern matches actual files
- Verify directory exists and is readable

**"Permission denied" on backup creation**
- Ensure write access to current directory
- Use `-nbf` to place backups elsewhere if needed
- Run with appropriate privileges if modifying system files

**"Binary file detected, use --binary flag"**
- This is safety-first behavior - verify you want binary processing
- Use `file` command to verify: `file filename`
- Only use `--binary` if file is genuinely a text file

### Exit Codes

| Code | Meaning |
|------|----------|
| 0 | Success - replacements completed |
| 1 | User interruption or critical error |
| 2 | No files found matching criteria |
| 3 | No replacements were made |
| 4 | Runtime error during execution |

## Performance Characteristics

### Benchmarks (on typical system)

- **File discovery**: ~1000 files/second
- **Binary detection**: ~10,000 files/second (multi_layer method)
- **Replacement**: 50-500 replacements/second (depends on file size)
- **Backup creation**: ~100 files/second
- **Session restoration**: ~200 files/second

### Optimization Tips

```bash
# Limit search depth for faster discovery
sr -md 3 "*.conf" "old" "new"

# Skip binary detection if files are known to be text
sr --binary-method=grep_only "*.txt" "search" "replace"

# Use pattern matching to narrow scope
sr "src/**/*.js" "TODO" "DONE"  # More specific than "*.js"

# Exclude large directories early
sr -xd node_modules,dist,vendor "*.ts" "old" "new"
```

## Advanced Scenarios

### Multi-Environment Configuration Updates

```bash
# Update all configuration files across environments
for env in dev staging prod; do
    cd /etc/app/$env
    sr -v "*.conf" "INTERNAL_API=dev" "INTERNAL_API=$env"
    sr --rollback-list  # Verify backup created
done
```

### Code Refactoring

```bash
# Refactor with confidence
sr -nr "src/main.ts" "export const oldAPI" "export const newAPI"
sr "src/**/*.ts" "oldAPI" "newAPI"
sr "tests/**/*.ts" "oldAPI" "newAPI"
sr --rollback-list  # See all sessions
```

### Disaster Recovery

```bash
# Quick restoration after accidental changes
sr --rollback  # Restore latest

# Or restore specific point in time
sr --rollback=sr.backup.20240112_100000

# Verify restoration completed
sr --rollback-list
```

## Troubleshooting

### Enable Debug Output

```bash
# Comprehensive debugging
sr -d "*.conf" "old" "new"

# Check what would happen
sr --dry-run -d "*.conf" "old" "new"

# Combine with verbose for maximum detail
sr -d -v "*.conf" "old" "new"
```

### Check Session Status

```bash
# List all backups with metadata
sr --rollback-list

# Inspect specific session
cat sr.backup.20240112_143022/.sr_session_metadata

# View modified files
cat sr.backup.20240112_143022/.sr_modified_files
```

## Version History

### Version 6.0.0 (Current)

**New Features:**
- Session-based rollback with complete metadata tracking
- Multi-layer binary file detection system
- Session ID generation with nanosecond precision
- Enhanced backup metadata storage
- Comprehensive rollback debugging system

**Improvements:**
- Robust error handling with detailed error messages
- Performance optimization for large file sets
- Better handling of special characters and escape sequences
- Improved binary file detection accuracy

**Breaking Changes:**
- None - maintains backward compatibility

See [CHANGELOG.md](CHANGELOG.md) for detailed version history.

## Contributing

Contributions are welcome! Please ensure:

1. Code follows existing style conventions
2. All changes tested with `--dry-run` first
3. Add comments for complex logic
4. Update documentation if adding features
5. Test with various Bash versions (4.0+)

## License

MIT License - See [LICENSE](LICENSE) file for details.

## Author

**Mikhail Deynekin**
- Website: [deynekin.com](https://deynekin.com)
- GitHub: [@paulmann](https://github.com/paulmann)

## Support

- 📖 [Documentation](https://github.com/paulmann/sr-search-replace/wiki)
- 🐛 [Report Issues](https://github.com/paulmann/sr-search-replace/issues)
- 💬 [Discussions](https://github.com/paulmann/sr-search-replace/discussions)
- 📧 Contact: mid1977@gmail.com

## Related Projects

- [Bash_WP-CLI_Update](https://github.com/paulmann/Bash_WP-CLI_Update) - WordPress CLI update automation
- Other shell utilities at [paulmann](https://github.com/paulmann?tab=repositories)

---

**Made with ❤️ for developers and system administrators**
