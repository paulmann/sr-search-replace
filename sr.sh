#!/bin/bash

# Search and Replace (sr) - Universal text replacement tool

# Version: 6.0.0

# Author: Mikhail Deynekin [ Deynekin.com ]

# REPO: https://github.com/paulmann/sr-search-replace

# Description: Recursively replaces text in files with proper escaping

# Enhanced with: Multi-layer binary detection, session-based rollback, predictable parsing

set -euo pipefail # Strict mode: exit on error, unset variables, pipe failures

# ============================================================================

# CONFIGURABLE DEFAULTS - EDIT THESE TO CHANGE SCRIPT BEHAVIOR

# ============================================================================

# Default behavior settings

readonly SESSION_VERSION="6.0.0"

readonly DEFAULT_DEBUG_MODE=false

readonly DEFAULT_RECURSIVE_MODE=true

readonly DEFAULT_DRY_RUN=false

readonly DEFAULT_CREATE_BACKUPS=true

readonly DEFAULT_BACKUP_IN_FOLDER=true

readonly DEFAULT_FORCE_BACKUP=false

readonly DEFAULT_PRESERVE_OWNERSHIP=true

readonly DEFAULT_MAX_DEPTH=100

readonly DEFAULT_TIMESTAMP_FORMAT="%Y%m%d_%H%M%S"

readonly DEFAULT_BACKUP_PREFIX="sr.backup"

readonly DEFAULT_TEMP_DIR="/tmp"

readonly DEFAULT_SED_DELIMITER="|"

readonly DEFAULT_SKIP_HIDDEN_FILES=false

readonly DEFAULT_SKIP_BINARY_FILES=true

readonly DEFAULT_MAX_FILE_SIZE_MB=100

readonly DEFAULT_ENCODING="UTF-8"

readonly DEFAULT_SEARCH_DIR="."

readonly DEFAULT_REPLACE_MODE="inplace" # inplace, copy, or backup_only

# Binary detection settings (NEW)

readonly DEFAULT_BINARY_DETECTION_METHOD="multi_layer" # multi_layer, file_only, grep_only

readonly DEFAULT_BINARY_CHECK_SIZE=1024 # Check first N bytes for binary detection

readonly DEFAULT_ALLOW_BINARY=false # NEW: Require explicit flag to process binary files

# Rollback settings (NEW)

readonly DEFAULT_ROLLBACK_ENABLED=true

readonly DEFAULT_MAX_BACKUPS=10 # Keep last N backups

# Color scheme customization

readonly COLOR_INFO='\033[0;34m' # Blue

readonly COLOR_SUCCESS='\033[0;32m' # Green

readonly COLOR_WARNING='\033[0;33m' # Yellow

readonly COLOR_ERROR='\033[0;31m' # Red

readonly COLOR_DEBUG='\033[0;36m' # Cyan

readonly COLOR_HEADER='\033[0;35m' # Magenta

readonly COLOR_RESET='\033[0m'

# File patterns to exclude by default (space-separated)

readonly DEFAULT_EXCLUDE_PATTERNS=".git .svn .hg .DS_Store *.bak *.backup"

readonly DEFAULT_EXCLUDE_DIRS="node_modules __pycache__ .cache .idea .vscode"

# ============================================================================

# GLOBAL VARIABLES (Initialized with defaults)

# ============================================================================

declare -g SED_INPLACE_FLAG="-i"

declare -g DEBUG_MODE="$DEFAULT_DEBUG_MODE"

declare -g RECURSIVE_MODE="$DEFAULT_RECURSIVE_MODE"

declare -g FILE_PATTERN=""

declare -g SEARCH_STRING=""

declare -g REPLACE_STRING=""

declare -gi PROCESSED_FILES=0

declare -gi MODIFIED_FILES=0

declare -gi TOTAL_REPLACEMENTS=0

declare -g DRY_RUN="$DEFAULT_DRY_RUN"

declare -g CREATE_BACKUPS="$DEFAULT_CREATE_BACKUPS"

declare -g BACKUP_IN_FOLDER="$DEFAULT_BACKUP_IN_FOLDER"

declare -g FORCE_BACKUP="$DEFAULT_FORCE_BACKUP"

declare -g PRESERVE_OWNERSHIP="$DEFAULT_PRESERVE_OWNERSHIP"

declare -g BACKUP_DIR=""

declare -g FIRST_FILE_OWNER=""

declare -g FIRST_FILE_GROUP=""

declare -g MAX_DEPTH="$DEFAULT_MAX_DEPTH"

declare -g TIMESTAMP_FORMAT="$DEFAULT_TIMESTAMP_FORMAT"

declare -g BACKUP_PREFIX="$DEFAULT_BACKUP_PREFIX"

declare -g TEMP_DIR="$DEFAULT_TEMP_DIR"

declare -g SED_DELIMITER="$DEFAULT_SED_DELIMITER"

declare -g SKIP_HIDDEN_FILES="$DEFAULT_SKIP_HIDDEN_FILES"

declare -g SKIP_BINARY_FILES="$DEFAULT_SKIP_BINARY_FILES"

declare -gi MAX_FILE_SIZE="$((DEFAULT_MAX_FILE_SIZE_MB * 1024 * 1024))"

declare -g EXCLUDE_PATTERNS="$DEFAULT_EXCLUDE_PATTERNS"

declare -g EXCLUDE_DIRS="$DEFAULT_EXCLUDE_DIRS"

declare -g SEARCH_DIR="$DEFAULT_SEARCH_DIR"

declare -g REPLACE_MODE="$DEFAULT_REPLACE_MODE"

declare -g OUTPUT_DIR=""

# New variables for enhanced functionality

declare -g ALLOW_BINARY="$DEFAULT_ALLOW_BINARY"

declare -g BINARY_DETECTION_METHOD="$DEFAULT_BINARY_DETECTION_METHOD"

declare -gi BINARY_CHECK_SIZE="$DEFAULT_BINARY_CHECK_SIZE"

declare -gi MAX_BACKUPS="$DEFAULT_MAX_BACKUPS"

declare -g VERBOSE_MODE=false

declare -g SESSION_ID=""

declare -g SESSION_START_TIME=""

declare -ga SESSION_INITIAL_ARGS=()

declare -ga SESSION_MODIFIED_FILES=()

declare -gi restored_count=0

# ============================================================================

# LOGGING FUNCTIONS

# ============================================================================

log_info() {
    echo -e "${COLOR_INFO}[INFO]${COLOR_RESET} $*"
}

log_success() {
    echo -e "${COLOR_SUCCESS}[SUCCESS]${COLOR_RESET} $*"
}

log_warning() {
    echo -e "${COLOR_WARNING}[WARNING]${COLOR_RESET} $*"
}

log_error() {
    echo -e "${COLOR_ERROR}[ERROR]${COLOR_RESET} $*" >&2
}

log_debug() {
    if [[ "$DEBUG_MODE" == true ]]; then
        echo -e "${COLOR_DEBUG}[DEBUG]${COLOR_RESET} $*" >&2
    fi
}

log_verbose() {
    if [[ "$VERBOSE_MODE" == true ]]; then
        echo -e "${COLOR_INFO}[VERBOSE]${COLOR_RESET} $*"
    fi
}

log_header() {
    echo -e "${COLOR_HEADER}$*${COLOR_RESET}"
}

# Note: This is a truncated version. Full script available in repository.
