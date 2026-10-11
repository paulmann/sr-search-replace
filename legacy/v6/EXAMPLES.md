# Search & Replace (sr) - Usage Examples

This document provides real-world examples of using the `sr` utility for common tasks.

## Basic Operations

### Simple Text Replacement

```bash
# Replace domain name in all HTML files
sr "*.html" "example.com" "newexample.com"

# Replace in configuration files
sr "*.conf" "localhost:3000" "localhost:8080"

# Update version strings
sr "*.py" "version = '1.0'" "version = '2.0'"
```

### Test Before Applying

```bash
# See what would be changed
sr --dry-run "*.js" "OLD_API" "NEW_API"

# Combine with verbose for more detail
sr --dry-run -v "*.js" "OLD_API" "NEW_API"
```

## Non-Recursive Searches

### Current Directory Only

```bash
# Only process files in current directory
sr -nr "*.txt" "search" "replace"

# Useful for bulk renaming patterns in filenames
sr -nr "*.backup" "_old" "_archived"
```

## Rollback Operations

### Restore Latest Changes

```bash
# Instantly restore all files from last session
sr --rollback

# See available backups before restoring
sr --rollback-list
```

### Restore Specific Session

```bash
# Restore files from specific backup
sr --rollback=sr.backup.20240112_143022

# List session information
sr --rollback-list

# Inspect session details
cat sr.backup.20240112_143022/.sr_session_metadata
```

## Advanced Filtering

### Exclude Specific Patterns

```bash
# Skip minified and backup files
sr -xp "*.min.js" "*.backup" "*.old" "const OLD" "const NEW" "*.js"

# Or using the pattern parameter
sr "*.js" -xp "test/*" "*.spec.js" "TODO" "DONE"
```

### Exclude Directories

```bash
# Skip node_modules, vendor, build directories
sr -xd node_modules vendor build dist "oldLib" "newLib" "*.js"

# Exclude hidden directories too
sr -xh -xd .git dist "search" "replace" "*.ts"
```

### Size Limits

```bash
# Skip large files (e.g., > 10MB)
sr -xs 10 "*.log" "ERROR" "WARNING"

# Only process small files (< 1MB)
sr -xs 1 "*.config" "old" "new"
```

## Large-Scale Operations

### Depth Limiting

```bash
# Limit search to 3 directories deep
sr -md 3 "*.conf" "localhost" "production"

# Very shallow search (only current directory and direct children)
sr -md 1 "*.txt" "old" "new"
```

### Batch Processing

```bash
# Process multiple patterns in sequence
for pattern in "*.js" "*.ts" "*.jsx"; do
    echo "Processing $pattern"
    sr -v "$pattern" "CommonJS" "ES6"
    sleep 1  # Brief pause between operations
done
```

## Real-World Scenarios

### Database Configuration Update

```bash
# Update database connection strings across environment configs
sr -xd logs,cache,tmp "db_host='localhost'" "db_host='db.prod.example.com'" "*.conf"

# Verify changes before committing
sr --rollback-list
git diff

# If satisfied, commit changes
git commit -am "Update production database configuration"
```

### Code Refactoring

```bash
# 1. Test with dry-run
sr --dry-run -v "src/**/*.ts" "oldFunctionName" "newFunctionName"

# 2. Execute replacement
sr -v "src/**/*.ts" "oldFunctionName" "newFunctionName"

# 3. Update tests
sr -v "tests/**/*.ts" "oldFunctionName" "newFunctionName"

# 4. Verify and commit
git diff
git commit -am "Refactor: rename oldFunctionName to newFunctionName"

# 5. If issues arise, one-command rollback
sr --rollback
```

### API Endpoint Migration

```bash
# Migrate to new API endpoints
sr -md 5 -v "*.js" "/api/v1/users" "/api/v2/users" \
  && sr -md 5 -v "*.js" "/api/v1/posts" "/api/v2/posts" \
  && sr --rollback-list

# Verify all changes applied correctly
grep -r "/api/v1" . --include="*.js" || echo "All v1 endpoints replaced"
```

### Debug Flag Removal

```bash
# Remove debug mode flags across codebase
sr "*.java" "DEBUG = true" "DEBUG = false"

# Or for logging
sr -xd target,build "logger.debug" "logger.info" "*.java"
```

### Multi-Environment Deployment

```bash
#!/bin/bash
# Deploy configuration to all environments

for env in dev staging prod; do
    echo "Updating $env environment..."
    cd "/etc/app/$env" || exit
    
    sr -fb -v "*.yml" "environment=dev" "environment=$env"
    sr -fb -v "*.conf" "DEBUG_MODE=true" "DEBUG_MODE=false"
    
    echo "Backup created for $env"
    sr --rollback-list
    
    cd - || exit
done
```

## Dangerous Operations with Safety

### System File Updates (with Extreme Care)

```bash
# ALWAYS use dry-run first for system files!
sr --dry-run "/etc/hosts" "127.0.0.1" "192.168.1.1"

# Verify output carefully
sr -v "/etc/hosts" "127.0.0.1" "192.168.1.1"

# Have rollback ready
sr --rollback-list
```

### Preserving Ownership

```bash
# By default, ownership is preserved
sr "*.conf" "old" "new"

# Disable ownership preservation if needed
sr -no-preserve "*.conf" "old" "new"
```

## Environment Variable Configuration

### Creating Profiles

```bash
# ~/.sr_profile_dev
export SR_DEBUG=false
export SR_DRY_RUN=false
export SR_MAX_DEPTH=10
export SR_EXCLUDE_DIRS="build,dist,node_modules"

# Usage
source ~/.sr_profile_dev
sr "*.ts" "API_KEY=dev" "API_KEY=local"
```

## Monitoring & Auditing

### Track All Changes

```bash
# Enable verbose mode for complete audit trail
sr -v "*.log" "ERROR" "CRITICAL"

# Review session metadata
cat sr.backup.*/sr_session_metadata | less

# Extract all modified files
find . -name ".sr_modified_files" -exec cat {} \;
```

### Generate Reports

```bash
# Count replacements made
sr -v "*.js" "oldLib" "newLib" 2>&1 | grep "replacements:"

# List all backup sessions
sr --rollback-list | grep "Session:"
```

## Performance Optimization

### Process Specific Subdirectory

```bash
# Instead of searching entire tree
sr -sd "src/components" "*.jsx" "oldComponent" "newComponent"

# Much faster than searching from root
```

### Parallel Replacement

```bash
# Process different patterns in parallel
sr "*.js" "pattern1" "replace1" &
sr "*.py" "pattern2" "replace2" &
sr "*.rb" "pattern3" "replace3" &
wait
```

### Skip Binary Detection

```bash
# For known-text files, skip binary detection (faster)
sr --binary-method=grep_only "*.txt" "search" "replace"
```

## Integration with Other Tools

### Git Integration

```bash
# Create feature branch
git checkout -b feature/update-api

# Make replacements
sr -v "*.ts" "oldAPI" "newAPI"

# Review changes
git diff

# Rollback if needed
sr --rollback

# Or commit if satisfied
git commit -am "Update API references"
```

### Pipe Usage

```bash
# Get list of files to process
find src -name "*.js" | head -10 | xargs -I {} sr {} "old" "new"

# Or with xargs and parallel
find . -name "*.conf" | xargs -P 4 -I {} sh -c 'sr "$1" "x" "y"' _ {}
```

## Troubleshooting Examples

### Debug Failed Replacement

```bash
# Enable full debugging
sr -d "*.conf" "needle" "replacement" 2>&1 | tee debug.log

# Check logs
less debug.log
```

### Handle Special Characters

```bash
# Escape special regex characters properly
sr "*.txt" "file.txt" "file.backup"  # Dots need escaping in regex

# sr handles this automatically - you can just use literal strings
sr "*.url" "http://example.com?x=1&y=2" "http://newsite.com?a=1"
```

### Binary File Confusion

```bash
# Check if file is really binary
file myfile.dat

# If text, explicitly allow binary processing
sr --binary "*.dat" "search" "replace"

# Or use different detection method
sr --binary-method=file_only "*.dat" "search" "replace"
```

---

**For more help:** `sr --help`
**Report issues:** https://github.com/paulmann/sr-search-replace/issues
