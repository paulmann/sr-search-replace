# Release Notes: Search and Replace (sr) v6.1.0

> **Historical document (sr 6.x).** Superseded by sr 7 — see [README.md](README.md),
> `sr help examples` and [docs/MIGRATION-6-to-7.md](docs/MIGRATION-6-to-7.md).
> Kept at this path so existing links keep resolving; the text below is unchanged.

**Release Date**: December 22, 2025
**Version**: 6.1.0
**Status**: Stable Release (Feature Enhancement)

## 🎉 Highlights

This feature-rich release introduces a powerful, extensible configuration system, direct tool parameter passing, and extended search capabilities, solidifying `sr` as the most configurable and precise search-and-replace utility for professional workflows. It maintains full backward compatibility with v6.0.0.

## ✨ Major Features

### Enhanced Configuration & Extensibility
- **Tool-Specific Configuration Variables**: Base commands (`FIND_TOOL`, `SED_TOOL`, `GREP_TOOL`) and default flags are now configurable script variables for unparalleled customization.
- **Direct Parameter Passing**: New `--find-opts`, `--sed-opts`, and `--grep-opts` flags allow experts to pass raw arguments directly to the underlying utilities for maximum control.
- **Environment Variable Expansion**: Dozens of new `SR_*` environment variables (e.g., `SR_IGNORE_CASE`, `SR_FIND_FLAGS`) provide fine-grained control over script behavior from the shell.

### Advanced Search & Replace Capabilities
- **Extended Regular Expressions**: Enable with `-E` or `--extended-regex` for more powerful pattern matching.
- **Case-Insensitive Operations**: New `-i` or `--ignore-case` flag performs case-insensitive search and replace across all files.
- **Word Boundary Matching**: The `-w` or `--word-boundary` flag ensures replacements only occur on whole words, preventing partial matches.
- **Multi-line & Global Control**: New `-m` (multiline) and `--no-global` flags provide precise control over regex behavior and replacement scope.

### Improved Compatibility & Diagnostics
- **GNU/BSD Sed Compatibility**: Enhanced detection and handling of sed variations across platforms ensures consistent behavior.
- **Comprehensive Argument Analysis**: The parsing engine now provides detailed debugging output for complex argument patterns and shell expansions.
- **Enhanced Performance Tracking**: Detailed real-time statistics, including processing rate and file size analysis, are available in verbose and debug modes.

### Robustness & Safety
- **Predictable Parsing Enhanced**: The argument parser now intelligently detects shell-expanded file lists, supporting both `sr *.txt *.js "search" "replace"` and traditional pattern-based usage.
- **Enhanced Binary Protection**: The safety-first default remains—binary files are always skipped unless explicitly allowed with the `--binary` flag.

## 📦 Installation & Dependencies

```bash
# Clone repository
git clone https://github.com/paulmann/sr-search-replace.git
cd sr-search-replace

# Make executable
chmod 755 sr.sh

# Install globally (optional)
sudo cp sr.sh /usr/local/bin/sr
```

**New Core Dependency Note**: `sr` relies on the standard Unix toolchain. For full v6.1.0 functionality (like binary detection), ensure `file` utility is installed (e.g., `apt install file`, `brew install file`).

## 🚀 Quick Start

```bash
# 1. Use new case-insensitive search for a code refactor
sr -i "src/**/*.py" "asyncOldFunc" "asyncNewFunc"

# 2. Pass custom flags to find for advanced filtering
sr --find-opts="-type f -mtime -1" "*.log" "ERROR" "WARNING"

# 3. Perform a safe, precise replacement on whole words only
sr -E -w "documentation.md" "\bAPI\b" "Application Programming Interface"

# 4. Preview complex multi-line changes
sr --dry-run -m "config.yaml" "server:\\n  host:.*" "server:\\n  host: prod.example.com"

# Restore from any backup (session system from v6.0.0)
sr --rollback
```

## 📋 Key New Options

| Option | Description |
|--------|-------------|
| `--find-opts="FLAGS"` | Pass additional flags directly to the `find` command. |
| `--sed-opts="FLAGS"` | Pass additional flags directly to the `sed` command. |
| `--grep-opts="FLAGS"` | Pass additional flags directly to the `grep` command. |
| `-i, --ignore-case` | Perform case-insensitive search and replace. |
| `-E, --extended-regex` | Use extended regular expressions (ERE). |
| `-w, --word-boundary` | Match whole words only. |
| `-m, --multiline` | Enable multi-line mode for regex matching. |
| `--no-global` | Replace only the first occurrence in each line. |
| `-n, --line-numbers` | Show line numbers in debug/output. |

*(All safety and core options from v6.0.0, like `--dry-run`, `--rollback`, `-v`, `-d`, remain fully supported.)*

## 🔒 Safety Features

- ✅ **Backward Compatibility**: All v6.0.0 scripts and commands work unchanged.
- ✅ **Explicit Binary Control**: `--binary` flag still required to process binary files.
- ✅ **Session-Based Rollback**: Full rollback system from v6.0.0 is unchanged and fully compatible.
- ✅ **Dry-Run Previews**: Always test with `--dry-run` before making changes.
- ✅ **Configurable Safety Limits**: Environment variables control depth, file size, and exclusions.

## 🐛 Bug Fixes & Improvements

- **Enhanced Argument Parsing**: Robust handling of shell-expanded file lists and complex quoting scenarios.
- **Improved Debug Output**: More detailed diagnostics for file discovery and binary detection steps.
- **Platform-Specific Fixes**: Better handling of path names and permissions across Linux, macOS, and BSD.
- **Performance Optimizations**: Reduced overhead in file metadata collection and session tracking.

## 🔐 Security Improvements

- **Path Sanitization**: Strengthened validation of file paths during rollback and backup operations.
- **Tool Flag Validation**: Basic sanitization of user-provided `--*-opts` flags to prevent injection.
- **Secure Defaults**: The principle of least privilege is maintained; dangerous operations always require explicit flags.

## 📚 Documentation

- **README.md**: Completely rewritten and expanded for v6.1.0, featuring new guides, workflow examples, and enterprise deployment patterns.
- **Updated In-Script Help**: Comprehensive help text (`sr --help`) includes all new v6.1.0 options and examples.
- **Configuration Guide**: Detailed comments in the script header explain all configurable variables.

## 📊 Performance

- **File Discovery**: ~1000 files/second (optimized with new `--find-opts`).
- **Pattern Matching**: Enhanced speed with compiled extended regexes.
- **Tool Overhead**: Negligible impact from new configuration system.
- **Memory Usage**: Efficient streaming processing for large files remains unchanged.

## ⚠️ Upgrade Notes

This is a **non-breaking, additive release**. No changes to existing workflows are required.
- Users leveraging the new tool-specific flags (`--find-opts`, etc.) should be familiar with the underlying utilities (find, sed, grep).
- Review new `SR_*` environment variables in the script header for system-wide customization.

## 🤝 Contributing

Contributions are welcome! Please ensure:
1. Code follows the enhanced style conventions and uses the new configuration variables.
2. All changes tested with the new `-E`, `-i`, and `--dry-run` options where applicable.
3. Documentation is updated for new features.
4. Backward compatibility with v6.0.0 is maintained.

## 📞 Support

- 🐛 **Report Issues**: https://github.com/paulmann/sr-search-replace/issues
- 💬 **Discussions**: https://github.com/paulmann/sr-search-replace/discussions
- 📧 **Contact**: mid1977@gmail.com

## 📄 License

MIT License - See LICENSE file for details.

---

**Made with ❤️ for developers and system administrators who demand precision and control.**

*Search & Replace (sr) v6.1.0: Configure, Extend, and Execute with Confidence.*
