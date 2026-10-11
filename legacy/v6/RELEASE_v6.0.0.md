# Release Notes: Search and Replace (sr) v6.0.0

**Release Date**: December 7, 2025
**Version**: 6.0.0
**Status**: Stable Release

## 🎉 Highlights

This is a major release featuring complete session-based backup system, multi-layer binary detection, and one-command rollback capabilities.

## ✨ Major Features

### Session-Based Rollback System
- **One-command file restoration**: Instantly rollback all changes from any previous session
- **Session metadata tracking**: Complete audit trail with original command and search/replace strings
- **Session identification**: Unique session IDs with nanosecond precision
- **Backup retention management**: Configurable retention of latest backups (default: 10)

### Multi-Layer Binary Detection
- **Layer 1**: Fast grep heuristic for quick scanning
- **Layer 2**: File utility MIME type analysis for accuracy
- **Layer 3**: Safe fallback method for environments without file utility
- **Three detection methods**: multi_layer (default), file_only, grep_only
- **Configurable sensitivity**: Adjust binary check size for performance tuning

### Enhanced Logging & Reporting
- **Comprehensive debug output**: Detailed execution traces for troubleshooting
- **Verbose progress reporting**: Real-time processing updates
- **Color-coded console output**: Improved readability with semantic coloring
- **Session information display**: Full session details in summary report
- **Statistics tracking**: Detailed file processing statistics

### Advanced File Processing
- **Predictable argument parsing**: Clear, unambiguous command-line interface
- **Intelligent file discovery**: Multiple discovery methods with automatic fallbacks
- **Pattern-based filtering**: Include/exclude files and directories by pattern
- **Size-based filtering**: Skip files exceeding configured size limits
- **Permission preservation**: Maintain file ownership and attributes

## 📦 Installation

```bash
# Clone repository
git clone https://github.com/paulmann/sr-search-replace.git
cd sr-search-replace

# Make executable
chmod 755 sr.sh

# Install globally (optional)
sudo cp sr.sh /usr/local/bin/sr
```

## 🚀 Quick Start

```bash
# Basic usage
sr "*.html" "old-domain.com" "new-domain.com"

# Test with dry-run
sr --dry-run "*.js" "oldFunction" "newFunction"

# Restore from backup
sr --rollback

# List available backups
sr --rollback-list
```

## 📋 Key Options

| Option | Description |
|--------|-------------|
| `--dry-run` | Show changes without modifying files |
| `--rollback` | Restore latest backup |
| `--rollback-list` | List all available backups |
| `-v, --verbose` | Detailed progress output |
| `-d, --debug` | Full debug information |
| `--binary` | Allow binary file processing |
| `-nr, --no-recursive` | Non-recursive search |
| `-md, --max-depth N` | Set maximum directory depth |

## 🔒 Safety Features

- ✅ **Automatic backups** by default
- ✅ **Binary file protection** - skipped unless explicitly allowed
- ✅ **Dry-run testing** before applying changes
- ✅ **One-command rollback** to restore from any backup
- ✅ **Session tracking** for complete audit trails
- ✅ **Ownership preservation** to maintain filesystem integrity

## 🐛 Bug Fixes

- Fixed file list encoding issues in rollback operations
- Resolved permission preservation in edge cases
- Corrected whitespace handling in file paths
- Improved BSD sed compatibility
- Better handling of special characters in replacement strings
- Fixed array initialization in bash 4.0+

## 🔐 Security Improvements

- Added path validation to prevent directory traversal
- Implemented control character filtering in file lists
- Added dangerous pattern detection (../, /proc/*, /sys/*, /dev/*)
- Enhanced permission handling for backup directories
- Safer temporary file creation with proper cleanup

## 📚 Documentation

- **README.md**: Comprehensive usage guide with examples
- **CHANGELOG.md**: Detailed version history
- **EXAMPLES.md**: Real-world usage scenarios
- **LICENSE**: MIT License

## 📊 Performance

- **File discovery**: ~1000 files/second
- **Binary detection**: ~10,000 files/second
- **Replacement**: 50-500 replacements/second
- **Backup creation**: ~100 files/second
- **Session restoration**: ~200 files/second

## 🤝 Contributing

Contributions welcome! Please ensure:
- Code follows existing style conventions
- Changes tested with `--dry-run` first
- Documentation updated if adding features
- Bash 4.0+ compatibility maintained

## 📞 Support

- 🐛 **Report Issues**: https://github.com/paulmann/sr-search-replace/issues
- 💬 **Discussions**: https://github.com/paulmann/sr-search-replace/discussions
- 📧 **Contact**: mid1977@gmail.com

## 📄 License

MIT License - See LICENSE file for details

---

**Made with ❤️ for developers and system administrators**
