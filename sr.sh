#!/bin/bash

# Search and Replace (sr) - Universal text replacement tool
# Version: 6.0.0
# Author: Mikhail Deynekin [ Deynekin.com ]
# REPO: https://github.com/paulmann
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
readonly DEFAULT_BINARY_CHECK_SIZE=1024                # Check first N bytes for binary detection
readonly DEFAULT_ALLOW_BINARY=false                    # NEW: Require explicit flag to process binary files

# Rollback settings (NEW)
readonly DEFAULT_ROLLBACK_ENABLED=true
readonly DEFAULT_MAX_BACKUPS=10 # Keep last N backups

# Color scheme customization
readonly COLOR_INFO='\033[0;34m'    # Blue
readonly COLOR_SUCCESS='\033[0;32m' # Green
readonly COLOR_WARNING='\033[0;33m' # Yellow
readonly COLOR_ERROR='\033[0;31m'   # Red
readonly COLOR_DEBUG='\033[0;36m'   # Cyan
readonly COLOR_HEADER='\033[0;35m'  # Magenta
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

# ============================================================================
# ENHANCED UTILITY FUNCTIONS (NEW)
# ============================================================================

# NEW: Generate unique session ID
generate_session_id() {
	date +"%Y%m%d_%H%M%S_%N"
}

confirm_action_timeout() {
	local prompt="${1:?Prompt required}"
	local default="${2:-n}"
	local timeout="${3:-0}"

	#
	local yes_chars='yYндδчμźжןมќอყςяﻱゅッзЗึីུྭᴐउᴕัิೆඇෂხขฺஸᴑॄेxნេхХᴅኔିุើሐჯОعوዪтٸzയႤଂଠ०ுஹՀйЙ'
	local no_chars='nNтТхХுೆೇಿݢৎᴗขნिኔოེỶᴞํԛઊാԺюцࢲັིᴌଂೢັනცತีถูხืΧuዘᴧὴܘᴎτ'

	local full_prompt="$prompt"
	[[ $timeout -gt 0 ]] && full_prompt+=" (timeout ${timeout}s)"

	local response
	local format_prompt

	[[ "$default" == "y" ]] && format_prompt="$full_prompt [Y/n]: " || format_prompt="$full_prompt [y/N]: "

	if [[ $timeout -gt 0 ]]; then
		read -t "$timeout" -p "$format_prompt" -r -n 1 response

		if [[ $? -ne 0 ]]; then
			echo
			[[ -n "${LOG_INFO:-}" ]] && $LOG_INFO "Timeout: using default '$default'"
			response="$default"
		fi
	else
		read -p "$format_prompt" -r -n 1 response
	fi

	echo
	response="${response:-$default}"

	#
	if [[ "$yes_chars" == *"$response"* ]]; then
		return 0
	elif [[ "$no_chars" == *"$response"* ]]; then
		return 1
	else
		[[ "$default" == "y" ]] && return 0 || return 1
	fi
}

# NEW: Multi-layer binary file detection (as per PDF recommendations)
is_binary_file() {
	local file="$1"
	local method="${2:-$BINARY_DETECTION_METHOD}"

	[[ ! -f "$file" ]] && {
		log_debug "is_binary_file: $file is not a file"
		return 1
	}
	[[ ! -r "$file" ]] && {
		log_debug "is_binary_file: $file is not readable"
		return 1
	}

	log_debug "Checking if file is binary: $file, method: $method"

	case "$method" in
	"file_only")
		# Method 1: Use file utility with MIME type
		if command -v file >/dev/null 2>&1; then
			local mime_type
			mime_type=$(file --mime-type "$file" 2>/dev/null | cut -d: -f2 | xargs)
			if [[ "$mime_type" == text/* ]]; then
				return 1 # Not binary
			elif [[ -n "$mime_type" ]]; then
				log_verbose "Binary detected by file utility: $file ($mime_type)"
				return 0 # Binary
			fi
		fi
		;;

	"grep_only")
		# Method 2: Use grep -I heuristic (fast and portable)
		if ! head -c "$BINARY_CHECK_SIZE" "$file" 2>/dev/null | grep -qI .; then
			log_verbose "Binary detected by grep heuristic: $file"
			return 0 # Binary
		fi
		;;

	"multi_layer" | *)
		# Method 3: Multi-layer detection (default)
		# Layer 1: Quick size check
		if [[ ! -s "$file" ]]; then
			return 1 # Empty files are not binary for our purposes
		fi

		# Layer 2: Fast grep heuristic (main method)
		if ! head -c "$BINARY_CHECK_SIZE" "$file" 2>/dev/null | grep -qI .; then
			# Layer 3: Verify with file utility if available
			if command -v file >/dev/null 2>&1; then
				local mime_type
				mime_type=$(file --mime-type "$file" 2>/dev/null | cut -d: -f2 | xargs)
				if [[ "$mime_type" == text/* ]]; then
					log_verbose "File utility overrides: $file is text ($mime_type)"
					return 1 # Not binary (file utility says text)
				elif [[ -n "$mime_type" ]]; then
					log_verbose "Binary confirmed by file utility: $file ($mime_type)"
					return 0 # Binary
				fi
			fi
			log_verbose "Binary detected by multi-layer check: $file"
			return 0 # Binary
		fi
		;;
	esac

	return 1 # Not binary
}

# NEW: Session management
init_session() {
	SESSION_ID=$(generate_session_id)
	SESSION_START_TIME=$(date +"%Y-%m-%d %H:%M:%S")

	log_debug "Session initialized: $SESSION_ID"
}

# NEW: Track modified file and update metadata immediately
track_modified_file() {
	local file="$1"

	#
	if [[ -z "${SESSION_MODIFIED_FILES+set}" ]]; then
		declare -g SESSION_MODIFIED_FILES=()
		log_debug "Initialized SESSION_MODIFIED_FILES array in track_modified_file"
	fi

	#
	local found=false
	for existing_file in "${SESSION_MODIFIED_FILES[@]}"; do
		if [[ "$existing_file" == "$file" ]]; then
			found=true
			break
		fi
	done

	#
	if [[ "$found" == false ]]; then
		SESSION_MODIFIED_FILES+=("$file")
		log_debug "Tracked modified file: $file (total: ${#SESSION_MODIFIED_FILES[@]})"
	fi

	#
	if [[ -n "$BACKUP_DIR" ]] && [[ -d "$BACKUP_DIR" ]]; then
		update_backup_filelist
	fi
}

#
update_backup_filelist() {
	[[ -z "$BACKUP_DIR" ]] && return 0
	[[ ! -d "$BACKUP_DIR" ]] && return 0

	local filelist_file="$BACKUP_DIR/.sr_modified_files"

	#
	local temp_file
	temp_file=$(mktemp 2>/dev/null || echo "$BACKUP_DIR/.sr_temp_$$")

	#
	if [[ -n "${SESSION_MODIFIED_FILES+set}" ]] && [[ ${#SESSION_MODIFIED_FILES[@]} -gt 0 ]]; then
		for file in "${SESSION_MODIFIED_FILES[@]}"; do
			local relative_path

			#
			if command -v realpath >/dev/null 2>&1; then
				relative_path=$(realpath --relative-to="." "$file" 2>/dev/null || echo "$file")
			else
				if [[ "$file" == /* ]]; then
					relative_path="${file#$(pwd)/}"
					[[ "$relative_path" == "$file" ]] && relative_path=$(basename "$file")
				else
					relative_path="$file"
				fi
			fi

			#
			relative_path="${relative_path#./}"
			echo "$relative_path" >>"$temp_file"
		done
	fi

	#
	if [[ -s "$temp_file" ]]; then
		mv "$temp_file" "$filelist_file" 2>/dev/null || cp "$temp_file" "$filelist_file"
		log_debug "Updated backup file list: ${#SESSION_MODIFIED_FILES[@]} files"
	else
		rm -f "$temp_file"
	fi
}

# NEW: Save initial session metadata when backup directory is created
save_initial_session_metadata() {
	local backup_dir="$1"
	SESSION_METADATA_FILE="$backup_dir/.sr_session_metadata"

	# Build full command
	local full_command="$0"
	for arg in "${SESSION_INITIAL_ARGS[@]}"; do
		if [[ "$arg" =~ [[:space:]\;\&\|\<\>\(\)\{\}\[\]] ]]; then
			full_command+=" \"$arg\""
		else
			full_command+=" $arg"
		fi
	done

	cat >"$SESSION_METADATA_FILE" <<EOF
# Session metadata for sr.sh
SESSION_ID="$SESSION_ID"
SESSION_START_TIME="$SESSION_START_TIME"
SESSION_END_TIME=""
SESSION_COMMAND="$full_command"
SESSION_PATTERN="$FILE_PATTERN"
SESSION_SEARCH="$SEARCH_STRING"
SESSION_REPLACE="$REPLACE_STRING"
SESSION_RECURSIVE="$RECURSIVE_MODE"
SESSION_ALLOW_BINARY="$ALLOW_BINARY"
SESSION_CREATE_BACKUPS="$CREATE_BACKUPS"
SESSION_DRY_RUN="$DRY_RUN"
SESSION_MODIFIED_COUNT=0
SESSION_TOTAL_REPLACEMENTS=0
SESSION_VERSION="$SESSION_VERSION"
EOF

	# Create empty modified files list file
	local filelist_file="$backup_dir/.sr_modified_files"
	>"$filelist_file"

	# Create file with additional information
	local fileinfo_file="$backup_dir/.sr_file_info"
	cat >"$fileinfo_file" <<EOF
# File information for session $SESSION_ID
# Generated: $SESSION_START_TIME
# Command: $full_command
# Pattern: $FILE_PATTERN
# Search: $SEARCH_STRING
# Replace: $REPLACE_STRING
# Backup directory: $backup_dir
# 
# Modified files will be listed below as they are processed:
EOF

	log_debug "Initial session metadata saved: $SESSION_METADATA_FILE"
}

finalize_session_metadata() {
	[[ -z "$BACKUP_DIR" ]] && return 0
	[[ ! -d "$BACKUP_DIR" ]] && return 0

	# Build full command
	local full_command="$0"
	for arg in "${SESSION_INITIAL_ARGS[@]}"; do
		if [[ "$arg" =~ [[:space:]\;\&\|\<\>\(\)\{\}\[\]] ]]; then
			full_command+=" \"$arg\""
		else
			full_command+=" $arg"
		fi
	done

	#
	local array_size=0
	if [[ -n "${SESSION_MODIFIED_FILES+set}" ]]; then
		array_size=${#SESSION_MODIFIED_FILES[@]}
		log_debug "finalize_session_metadata: SESSION_MODIFIED_FILES has $array_size items"
	else
		log_warning "SESSION_MODIFIED_FILES array is not set, using 0"
	fi

	# Update session metadata
	cat >"$BACKUP_DIR/.sr_session_metadata" <<EOF
# Session metadata for sr.sh
SESSION_ID="$SESSION_ID"
SESSION_START_TIME="$SESSION_START_TIME"
SESSION_END_TIME="$(date +"%Y-%m-%d %H:%M:%S")"
SESSION_COMMAND="$full_command"
SESSION_PATTERN="$FILE_PATTERN"
SESSION_SEARCH="$SEARCH_STRING"
SESSION_REPLACE="$REPLACE_STRING"
SESSION_RECURSIVE="$RECURSIVE_MODE"
SESSION_ALLOW_BINARY="$ALLOW_BINARY"
SESSION_CREATE_BACKUPS="$CREATE_BACKUPS"
SESSION_DRY_RUN="$DRY_RUN"
SESSION_MODIFIED_COUNT=$array_size
SESSION_TOTAL_REPLACEMENTS=$TOTAL_REPLACEMENTS
SESSION_VERSION="$SESSION_VERSION"
EOF

	# Update file information
	local fileinfo_file="$BACKUP_DIR/.sr_file_info"
	if [[ -f "$fileinfo_file" ]]; then
		cat >>"$fileinfo_file" <<EOF

# Processing completed at: $(date +"%Y-%m-%d %H:%M:%S")
# Total files modified: $array_size
# Total replacements made: $TOTAL_REPLACEMENTS

# List of all modified files:
EOF

		if [[ -n "${SESSION_MODIFIED_FILES+set}" ]] && [[ $array_size -gt 0 ]]; then
			for file in "${SESSION_MODIFIED_FILES[@]}"; do
				local relative_path

				# Get relative path
				if command -v realpath >/dev/null 2>&1; then
					relative_path=$(realpath --relative-to="." "$file" 2>/dev/null || echo "$file")
				else
					if [[ "$file" == /* ]]; then
						relative_path="${file#$(pwd)/}"
						[[ "$relative_path" == "$file" ]] && relative_path=$(basename "$file")
					else
						relative_path="$file"
					fi
				fi

				relative_path="${relative_path#./}"
				echo "$relative_path" >>"$fileinfo_file"
				log_debug "Added to .sr_file_info: $relative_path"
			done
		else
			echo "# No files were modified in this session" >>"$fileinfo_file"
		fi
	fi

	log_debug "Finalized session metadata with $array_size files"
}

# ============================================================================
# CORE UTILITY FUNCTIONS (RESTORED FROM ORIGINAL)
# ============================================================================

escape_regex() {
	local string="$1"
	echo "$string" | sed -e 's/[][\/.^$*+?{}|()]/\\&/g'
}

escape_replacement() {
	local string="$1"
	echo "$string" | sed -e 's/[\/&]/\\&/g' -e ':a;N;$!ba;s/\n/\\n/g'
}

# Function to check if a directory exists and is readable
check_directory() {
	local dir="$1"
	local desc="$2"

	if [[ ! -d "$dir" ]]; then
		log_error "$desc directory does not exist: $dir"
		return 1
	fi

	if [[ ! -r "$dir" ]]; then
		log_error "$desc directory is not readable: $dir"
		return 1
	fi

	if [[ ! -x "$dir" ]]; then
		log_error "$desc directory is not accessible: $dir"
		return 1
	fi
}

# Function to get absolute path
get_absolute_path() {
	local path="$1"
	if [[ "$path" == /* ]]; then
		echo "$path"
	else
		echo "$(cd "$(dirname "$path")" && pwd)/$(basename "$path")"
	fi
}

# ============================================================================
# ENVIRONMENT VALIDATION FUNCTION (RESTORED)
# ============================================================================

validate_environment() {
	local required_cmds=("find" "sed" "grep")

	for cmd in "${required_cmds[@]}"; do
		if ! command -v "$cmd" >/dev/null 2>&1; then
			log_error "Required command not found: $cmd"
			exit 1
		fi
	done

	# Check for file utility (used in binary detection)
	if [[ "$BINARY_DETECTION_METHOD" == "file_only" || "$BINARY_DETECTION_METHOD" == "multi_layer" ]]; then
		if ! command -v file >/dev/null 2>&1; then
			log_warning "file utility not found. Binary detection may be limited."
			log_warning "Consider using --binary-method=grep_only or installing file utility."
		fi
	fi

	# Detect sed type (GNU vs BSD)
	if sed --version 2>/dev/null | grep -q "GNU"; then
		log_debug "Using GNU sed"
	else
		SED_INPLACE_FLAG="-i ''"
		log_debug "Using BSD sed"
	fi

	# Validate MAX_DEPTH
	if ! [[ "$MAX_DEPTH" =~ ^[0-9]+$ ]] || [[ "$MAX_DEPTH" -lt 1 ]]; then
		log_warning "Invalid MAX_DEPTH ($MAX_DEPTH), using default: $DEFAULT_MAX_DEPTH"
		MAX_DEPTH="$DEFAULT_MAX_DEPTH"
	fi

	# Validate TEMP_DIR
	if [[ ! -d "$TEMP_DIR" ]] || [[ ! -w "$TEMP_DIR" ]]; then
		log_warning "Temp directory $TEMP_DIR is not accessible, using /tmp"
		TEMP_DIR="/tmp"
	fi

	# Validate search directory
	if ! check_directory "$SEARCH_DIR" "Search"; then
		exit 1
	fi

	# Validate output directory if specified
	if [[ -n "$OUTPUT_DIR" ]]; then
		if [[ ! -d "$OUTPUT_DIR" ]]; then
			log_debug "Creating output directory: $OUTPUT_DIR"
			mkdir -p "$OUTPUT_DIR" || {
				log_error "Failed to create output directory: $OUTPUT_DIR"
				exit 1
			}
		fi
		if ! check_directory "$OUTPUT_DIR" "Output"; then
			exit 1
		fi
	fi

	# Adjust CREATE_BACKUPS based on replace mode
	if [[ "$REPLACE_MODE" == "backup_only" ]]; then
		CREATE_BACKUPS=true
		FORCE_BACKUP=true
		log_debug "Backup-only mode: backups forced"
	fi

	# Validate binary check size
	if ! [[ "$BINARY_CHECK_SIZE" =~ ^[0-9]+$ ]] || [[ "$BINARY_CHECK_SIZE" -lt 1 ]]; then
		log_warning "Invalid BINARY_CHECK_SIZE ($BINARY_CHECK_SIZE), using default: $DEFAULT_BINARY_CHECK_SIZE"
		BINARY_CHECK_SIZE="$DEFAULT_BINARY_CHECK_SIZE"
	fi

	# Validate MAX_BACKUPS
	if ! [[ "$MAX_BACKUPS" =~ ^[0-9]+$ ]] || [[ "$MAX_BACKUPS" -lt 0 ]]; then
		log_warning "Invalid MAX_BACKUPS ($MAX_BACKUPS), using default: $DEFAULT_MAX_BACKUPS"
		MAX_BACKUPS="$DEFAULT_MAX_BACKUPS"
	fi
}

# ============================================================================
# ENHANCED ROLLBACK SYSTEM
# ============================================================================

# NEW: Enhanced rollback functionality with step-by-step debugging
perform_rollback() {
	local target_backup="${1:-latest}"
	local backup_dirs=()
	local selected_backup=""
	local files_to_restore=()

	#
	if [[ ! -d "$target_backup" ]] && [[ "$target_backup" != "latest" ]]; then
		#
		#
		if [[ -d "./$target_backup" ]]; then
			target_backup="./$target_backup"
			log_debug "DEBUG: Adjusted backup path to: '$target_backup'"
		fi
	fi

	log_header "=== ROLLBACK SYSTEM ==="
	log_debug "DEBUG [1/10]: Function perform_rollback started with arg: '$target_backup'"

	# Find all backup directories
	log_debug "DEBUG [2/10]: Searching for backup directories with pattern: ${BACKUP_PREFIX}.*"
	while IFS= read -r -d '' dir; do
		backup_dirs+=("$dir")
		log_debug "DEBUG [2/10]: Found backup directory: $dir"
	done < <(find . -maxdepth 1 -type d -name "${BACKUP_PREFIX}.*" -print0 2>/dev/null | sort -zr)

	log_debug "DEBUG [2/10]: Total backup directories found: ${#backup_dirs[@]}"

	if [[ ${#backup_dirs[@]} -eq 0 ]]; then
		log_error "No backup directories found"
		return 1
	fi

	# Select backup
	if [[ "$target_backup" == "latest" ]]; then
		selected_backup="${backup_dirs[0]}"
		log_info "Selected latest backup: $selected_backup"
		log_debug "DEBUG [3/10]: Selected latest backup: $selected_backup"
	else
		# Check if specific backup exists
		if [[ -d "$target_backup" ]]; then
			selected_backup="$target_backup"
			log_info "Selected specified backup: $target_backup"
		else
			log_error "Backup not found: $target_backup"
			log_info "Available backups:"
			for dir in "${backup_dirs[@]}"; do
				echo "  $dir"
			done
			return 1
		fi
	fi

	log_debug "DEBUG [4/10]: Selected backup: $selected_backup"
	log_debug "DEBUG [4/10]: Backup directory exists: $([[ -d "$selected_backup" ]] && echo "YES" || echo "NO")"

	# Load session metadata
	local session_metadata_file="$selected_backup/.sr_session_metadata"
	local filelist_file="$selected_backup/.sr_modified_files"
	local fileinfo_file="$selected_backup/.sr_file_info"
	local files_to_restore=()

	log_debug "DEBUG [5/10]: Looking for session metadata: $session_metadata_file"
	log_debug "DEBUG [5/10]: Metadata file exists: $([[ -f "$session_metadata_file" ]] && echo "YES" || echo "NO")"

	if [[ -f "$session_metadata_file" ]]; then
		log_info "Loading session metadata..."
		# Extract info from metadata
		local session_id session_command file_count search_str replace_str
		session_id=$(grep '^SESSION_ID=' "$session_metadata_file" 2>/dev/null | cut -d= -f2 | tr -d '"')
		session_command=$(grep '^SESSION_COMMAND=' "$session_metadata_file" 2>/dev/null | cut -d= -f2 | tr -d '"')
		file_count=$(grep '^SESSION_MODIFIED_COUNT=' "$session_metadata_file" 2>/dev/null | cut -d= -f2 | tr -d '"')
		search_str=$(grep '^SESSION_SEARCH=' "$session_metadata_file" 2>/dev/null | cut -d= -f2 | tr -d '"')
		replace_str=$(grep '^SESSION_REPLACE=' "$session_metadata_file" 2>/dev/null | cut -d= -f2 | tr -d '"')

		log_info "Session ID: $session_id"
		log_info "Original command: $session_command"
		log_info "Files in session: ${file_count:-unknown}"
		[[ -n "$search_str" ]] && [[ -n "$replace_str" ]] && log_info "Search: '$search_str' → '$replace_str'"

		log_debug "DEBUG [5/10]: Metadata loaded: session_id='$session_id', file_count='$file_count'"
	else
		log_warning "No session metadata found, using legacy backup structure"
	fi

	# Determine which files to restore - PRIORITY 1: file list
	log_debug "DEBUG [6/10]: Looking for file list: $filelist_file"
	log_debug "DEBUG [6/10]: File list exists and readable: $([[ -f "$filelist_file" && -r "$filelist_file" ]] && echo "YES" || echo "NO")"

	# CRITICAL FIX 1: Verify both existence AND readability upfront
	if [[ -f "$filelist_file" && -r "$filelist_file" ]]; then
		log_info "Reading modified files list from $filelist_file..."

		# Create debug log with atomic initialization
		local debug_log="/tmp/sr_rollback_debug_$$.log"
		{
			echo "=== START DEBUG LOG ==="
			echo "Timestamp: $(date -u)"
			echo "File: $filelist_file"
			echo "User: $(whoami)"
			echo "PID: $$"
			echo "Bash version: ${BASH_VERSION}"
		} >"$debug_log"

		# File diagnostics with error suppression
		{
			echo -e "\n=== FILE INTEGRITY CHECK ==="
			if command -v stat >/dev/null 2>&1; then
				stat --format="Size: %s bytes\nInode: %i\nMode: %a\nUID: %u GID: %g" "$filelist_file" 2>/dev/null || echo "stat command failed"
			else
				echo "stat command not available"
				ls -la "$filelist_file" 2>/dev/null || echo "ls command failed"
			fi

			if command -v file >/dev/null 2>&1; then
				echo -e "\nFile type: $(file -b "$filelist_file" 2>/dev/null || echo "file check unavailable")"
			fi

			echo -e "\nFile size: $(wc -c <"$filelist_file" 2>/dev/null || echo "0") bytes"
			echo "Line count: $(wc -l <"$filelist_file" 2>/dev/null || echo "0")"
		} >>"$debug_log" 2>&1

		# CRITICAL FIX 2: Robust file reading with multiple fallbacks
		local files_to_restore_local=()
		local read_success=false
		local method_used=""

		# PRIMARY METHOD: Bash mapfile (most efficient)
		if command -v mapfile &>/dev/null; then
			log_debug "DEBUG [6/10]: METHOD 1 - Trying native mapfile"
			echo -e "\n=== METHOD 1: Native mapfile ===" >>"$debug_log"
			if mapfile -t files_to_restore_local <"$filelist_file" 2>>"$debug_log"; then
				read_success=true
				method_used="mapfile"
				echo "SUCCESS: Read ${#files_to_restore_local[@]} lines via mapfile" >>"$debug_log"
				echo "First 3 lines:" >>"$debug_log"
				for ((i = 0; i < ${#files_to_restore_local[@]} && i < 3; i++)); do
					echo "  $((i + 1)): '${files_to_restore_local[$i]}'" >>"$debug_log"
				done
			else
				echo "FAILED: mapfile method failed" >>"$debug_log"
			fi
		fi

		# FALLBACK 1: POSIX-compliant while-read loop
		if ! $read_success; then
			log_debug "DEBUG [6/10]: METHOD 2 - Fallback to POSIX read loop"
			echo -e "\n=== METHOD 2: POSIX read loop ===" >>"$debug_log"
			files_to_restore_local=()
			local line_count=0

			while IFS= read -r line || [ -n "$line" ]; do
				files_to_restore_local+=("$line")
				restored_count=$((restored_count + 1))
				if [ $line_count -le 3 ]; then
					echo "Read line $line_count: '$line'" >>"$debug_log"
				fi
			done < <(exec cat "$filelist_file" 2>>"$debug_log") && read_success=true

			if $read_success; then
				method_used="posix-loop"
				echo "SUCCESS: Read ${#files_to_restore_local[@]} lines via POSIX loop" >>"$debug_log"
			else
				echo "FAILED: POSIX read loop failed" >>"$debug_log"
			fi
		fi

		# FALLBACK 2: Process substitution with error isolation
		if ! $read_success; then
			log_debug "DEBUG [6/10]: METHOD 3 - Final fallback: process substitution with dd"
			echo -e "\n=== METHOD 3: dd fallback ===" >>"$debug_log"
			files_to_restore_local=()
			local line_count=0

			while IFS= read -r line || [ -n "$line" ]; do
				files_to_restore_local+=("$line")
				restored_count=$((restored_count + 1))
				if [ $line_count -le 3 ]; then
					echo "Read line $line_count: '$line'" >>"$debug_log"
				fi
			done < <(dd if="$filelist_file" bs=64K count=100 2>>"$debug_log" || echo "dd command failed") && read_success=true

			if $read_success; then
				method_used="dd-fallback"
				echo "SUCCESS: Read ${#files_to_restore_local[@]} lines via dd fallback" >>"$debug_log"
			else
				echo "FAILED: All read methods failed" >>"$debug_log"
				log_error "CRITICAL: Unable to read file list after 3 attempts"
			fi
		fi

		# CRITICAL FIX 3: Centralized line processing with strict validation
		if $read_success && [ ${#files_to_restore_local[@]} -gt 0 ]; then
			echo -e "\n=== LINE PROCESSING ===" >>"$debug_log"
			local valid_count=0
			files_to_restore=() # Reset main array

			for raw_line in "${files_to_restore_local[@]}"; do
				# Robust whitespace trimming using POSIX parameter expansion
				local trimmed="${raw_line#"${raw_line%%[![:space:]]*}"}"
				trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"

				# Skip empty lines and comments
				if [ -z "$trimmed" ]; then
					echo "SKIPPED: Empty line" >>"$debug_log"
					continue
				fi

				if [[ "$trimmed" =~ ^[[:space:]]*# ]]; then
					echo "SKIPPED: Comment line: '$trimmed'" >>"$debug_log"
					continue
				fi

				# Validate path sanity before adding
				# CRITICAL FIX: Proper path validation without regex syntax errors
				if [[ "$trimmed" == /* ]] && [[ "$trimmed" =~ ^[[:print:]]+$ ]]; then
					# Additional checks for dangerous patterns
					if [[ "$trimmed" == *".."* ]] || [[ "$trimmed" == /proc/* ]] || [[ "$trimmed" == /sys/* ]] || [[ "$trimmed" == /dev/* ]]; then
						echo "SKIPPED: Dangerous path pattern: '$trimmed'" >>"$debug_log"
						continue
					fi

					# Reject paths with control characters
					if [[ "$trimmed" =~ [[:cntrl:]] ]]; then
						echo "SKIPPED: Contains control characters: '$trimmed'" >>"$debug_log"
						continue
					fi

					files_to_restore+=("$trimmed")
					restored_count=$((restored_count + 1))
					echo "ACCEPTED: '$trimmed'" >>"$debug_log"
				else
					echo "SKIPPED: Invalid path format: '$trimmed'" >>"$debug_log"
				fi
			done

			echo -e "\n=== PROCESSING RESULTS ===" >>"$debug_log"
			echo "Valid paths found: $valid_count" >>"$debug_log"
			echo "Total paths processed: ${#files_to_restore_local[@]}" >>"$debug_log"
			echo "Method used: $method_used" >>"$debug_log"
		else
			echo "ERROR: All read methods failed for $filelist_file" >>"$debug_log"
			log_error "CRITICAL: Unable to read file list after 3 attempts"
		fi

		# Final debug summary
		{
			echo -e "\n=== FINAL DEBUG SUMMARY ==="
			echo "Files to restore: ${#files_to_restore[@]}"
			if [ ${#files_to_restore[@]} -gt 0 ]; then
				echo "First 5 files:"
				for ((i = 0; i < ${#files_to_restore[@]} && i < 5; i++)); do
					echo "  $((i + 1)): '${files_to_restore[$i]}'"
				done
				if [ ${#files_to_restore[@]} -gt 5 ]; then
					echo "  ... (and $((${#files_to_restore[@]} - 5)) more)"
				fi
			else
				echo "WARNING: No valid files found for restoration"
			fi
		} >>"$debug_log"

		log_info "Found ${#files_to_restore[@]} valid file(s) in file list"
		log_debug "DEBUG [6/10]: File list processing complete. See full debug at: $debug_log"

		# Show summary of debug log in main log
		log_debug "DEBUG [6/10]: First 15 lines of debug log:"
		head -15 "$debug_log" | while IFS= read -r log_line; do
			log_debug "DEBUG [6/10]:   $log_line"
		done

	else
		log_debug "DEBUG [6/10]: Primary file list unavailable or unreadable"
		# PRIORITY 2: Fallback to fileinfo with same robustness
		if [[ -f "$fileinfo_file" && -r "$fileinfo_file" ]]; then
			log_info "Extracting file list from $fileinfo_file..."
			log_debug "DEBUG [6/10]: Starting to read file info..."

			# Create debug log for fileinfo processing
			local debug_log_info="/tmp/sr_rollback_debug_info_$$.log"
			{
				echo "=== START FILEINFO DEBUG LOG ==="
				echo "Timestamp: $(date -u)"
				echo "File: $fileinfo_file"
				echo "User: $(whoami)"
				echo "PID: $$"
				echo "Bash version: ${BASH_VERSION}"
			} >"$debug_log_info"

			{
				echo -e "\n=== FILE INTEGRITY CHECK ==="
				if command -v stat >/dev/null 2>&1; then
					stat --format="Size: %s bytes\nInode: %i\nMode: %a\nUID: %u GID: %g" "$fileinfo_file" 2>/dev/null || echo "stat command failed"
				else
					echo "stat command not available"
					ls -la "$fileinfo_file" 2>/dev/null || echo "ls command failed"
				fi
			} >>"$debug_log_info" 2>&1

			local in_section=false
			local line_num=0
			files_to_restore=() # Reset array

			while IFS= read -r line || [ -n "$line" ]; do
				m=$((m + 1))
				echo "Processing line $line_num: '$line'" >>"$debug_log_info"

				if [[ "$line" == "# List of all modified files:" ]]; then
					in_section=true
					echo "SECTION START: Found file list section at line $line_num" >>"$debug_log_info"
					continue
				elif [[ "$in_section" == true ]] && [[ "$line" =~ ^[[:space:]]*#$ ]]; then
					# Empty comment line in section - continue
					continue
				elif [[ "$in_section" == true ]] && [[ "$line" =~ ^# ]]; then
					# Non-empty comment line likely indicates end of section
					echo "SECTION END: Comment line suggests end of section at line $line_num" >>"$debug_log_info"
					break
				elif [[ "$in_section" == true ]] && [[ -n "$line" ]]; then
					# Clean up the line
					local clean_line="${line#"${line%%[![:space:]]*}"}"       # Trim leading whitespace
					clean_line="${clean_line%"${clean_line##*[![:space:]]}"}" # Trim trailing whitespace

					if [[ -z "$clean_line" ]]; then
						echo "SKIPPED: Empty after trim at line $line_num" >>"$debug_log_info"
						continue
					fi

					echo "CANDIDATE: '$clean_line' at line $line_num" >>"$debug_log_info"

					# Validate path format - same robust method as above
					if [[ "$clean_line" == /* ]] && [[ "$clean_line" =~ ^[[:print:]]+$ ]]; then
						if [[ "$clean_line" == *".."* ]] || [[ "$clean_line" == /proc/* ]] || [[ "$clean_line" == /sys/* ]] || [[ "$clean_line" == /dev/* ]]; then
							echo "SKIPPED: Dangerous path pattern at line $line_num: '$clean_line'" >>"$debug_log_info"
							continue
						fi

						if [[ "$clean_line" =~ [[:cntrl:]] ]]; then
							echo "SKIPPED: Contains control characters at line $line_num: '$clean_line'" >>"$debug_log_info"
							continue
						fi

						files_to_restore+=("$clean_line")
						echo "ACCEPTED: '$clean_line' at line $line_num" >>"$debug_log_info"
					else
						echo "SKIPPED: Invalid path format at line $line_num: '$clean_line'" >>"$debug_log_info"
					fi
				fi
			done <"$fileinfo_file" 2>>"$debug_log_info"

			# Final summary for fileinfo processing
			{
				echo -e "\n=== FILEINFO PROCESSING SUMMARY ==="
				echo "Files found in section: ${#files_to_restore[@]}"
				if [ ${#files_to_restore[@]} -gt 0 ]; then
					echo "First 3 files:"
					for ((i = 0; i < ${#files_to_restore[@]} && i < 3; i++)); do
						echo "  $((i + 1)): '${files_to_restore[$i]}'"
					done
				fi
			} >>"$debug_log_info"

			log_info "Found ${#files_to_restore[@]} file(s) in file info"
			log_debug "DEBUG [6/10]: File info processing complete. Debug log: $debug_log_info"

			# Show summary of debug log in main log
			log_debug "DEBUG [6/10]: First 10 lines of fileinfo debug log:"
			head -10 "$debug_log_info" | while IFS= read -r log_line; do
				log_debug "DEBUG [6/10]:   $log_line"
			done
		else
			log_debug "DEBUG [6/10]: File info not found or unreadable: $fileinfo_file"
			log_error "ERROR: Neither file list ($filelist_file) nor file info ($fileinfo_file) is available for restoration"
			return 1
		fi
	fi

	# Final validation and logging
	if [ ${#files_to_restore[@]} -eq 0 ]; then
		log_warning "WARNING: No valid files were found for restoration"
		log_debug "DEBUG [6/10]: files_to_restore array is empty after all processing"
	else
		log_debug "DEBUG [6/10]: Files to restore array contents:"
		for ((i = 0; i < ${#files_to_restore[@]}; i++)); do
			log_debug "DEBUG [6/10]:   [$i] '${files_to_restore[$i]}'"
		done
	fi
	# DEBUG [7/10]: Processing find output...
	log_debug "DEBUG [7/10]: Starting directory scan..."

	#
	local backup_dir_abs
	backup_dir_abs=$(cd "$selected_backup" && pwd 2>/dev/null || echo "$selected_backup")
	log_debug "DEBUG [7/10]: Absolute backup path: '$backup_dir_abs'"

	#
	local temp_files_list
	temp_files_list=$(mktemp 2>/dev/null || echo "/tmp/sr_files_list_$$")

	#
	log_debug "DEBUG [7/10]: Executing: find '$backup_dir_abs' -type f -not -name '.sr_*'"
	find "$backup_dir_abs" -type f -not -name ".sr_*" 2>/dev/null >"$temp_files_list"

	#
	local file_count=0
	if [[ -s "$temp_files_list" ]]; then
		file_count=$(wc -l <"$temp_files_list" 2>/dev/null || echo "0")
		log_debug "DEBUG [7/10]: Found $file_count file(s)"

		#
		if [[ "$file_count" -gt 0 ]]; then
			log_debug "DEBUG [7/10]: All files found:"
			cat "$temp_files_list" | while IFS= read -r file; do
				log_debug "DEBUG [7/10]:   '$file'"
			done
		fi
	fi

	#
	if [[ "$file_count" -gt 0 ]]; then
		local added=0

		#
		local processed_paths_file
		processed_paths_file=$(mktemp 2>/dev/null || echo "/tmp/sr_processed_$$")

		#
		while IFS= read -r full_path || [[ -n "$full_path" ]]; do
			#
			[[ -z "$full_path" ]] && continue

			log_debug "DEBUG [7/10]: Processing: '$full_path'"

			#
			local relative_path="${full_path#$backup_dir_abs/}"

			#
			if [[ "$relative_path" == "$full_path" ]]; then
				log_debug "DEBUG [7/10]:   Conversion failed (not inside backup directory)"

				#
				if command -v realpath >/dev/null 2>&1; then
					relative_path=$(realpath --relative-to="." "$full_path" 2>/dev/null || echo "$full_path")
				fi

				#
				relative_path="${relative_path#$selected_backup/}"
				log_debug "DEBUG [7/10]:   Using alternative: '$relative_path'"
			fi

			#
			relative_path="${relative_path#./}"

			# Validate and normalize the relative path
			relative_path="${relative_path#./}"
			[[ "$relative_path" != "/" ]] && relative_path="${relative_path%/}"

			# Process the path if it meets all validation criteria
			if [[ -n "$relative_path" && "$relative_path" != "$full_path" ]] &&
				[[ ! "$relative_path" =~ \.sr_ ]] && [[ ! "${relative_path##*/}" =~ ^\.sr_ ]]; then

				# Ensure 'added' variable is numeric before incrementing
				if [[ ! "$added" =~ ^[0-9]+$ ]]; then
					# Initialize or reset 'added' if it's not a valid number
					added=0
					log_debug "DEBUG [7/10]: Initialized/reset 'added' counter to 0"
				fi

				# Safely append the path to the file
				if printf '%s\n' "$relative_path" >>"$processed_paths_file" 2>/dev/null; then
					# Perform the increment only if 'added' is confirmed to be numeric
					added=$((added + 1))
					log_debug "DEBUG [7/10]:   [$added] Added: '$relative_path'"
				else
					log_error "CRITICAL: Failed to write path '$relative_path' to list file '$processed_paths_file'."
					return 1
				fi

			else
				# Log the specific reason for skipping the path
				if [[ -z "$relative_path" ]]; then
					log_debug "DEBUG [7/10]:   Skipping empty path."
				elif [[ "$relative_path" == "$full_path" ]]; then
					log_debug "DEBUG [7/10]:   Skipping path as it did not resolve to a relative path: '$full_path'."
				elif [[ "$relative_path" =~ \.sr_ ]] || [[ "${relative_path##*/}" =~ ^\.sr_ ]]; then
					log_debug "DEBUG [7/10]:   Skipping metadata path: '$relative_path'."
				fi
			fi

		done <"$temp_files_list"

		# Read processed paths and add to array
		if [[ "$added" -gt 0 ]] && [[ -s "$processed_paths_file" ]]; then
			log_debug "DEBUG [7/10]: Reading $added processed path(s) from temp file..."

			local processed_count=0
			while IFS= read -r relative_path || [[ -n "$relative_path" ]]; do
				# Skip empty lines
				[[ -z "$relative_path" ]] && continue

				# Validate and add to array
				if [[ -n "$relative_path" ]]; then
					files_to_restore+=("$relative_path")
					# Increment only after successful array operation
					processed_count=$((processed_count + 1))

					if [[ $processed_count -le 3 ]]; then
						log_debug "DEBUG [7/10]:   [$processed_count] Loaded to array: '$relative_path'"
					fi
				fi
			done <"$processed_paths_file"

			log_info "Found $processed_count file(s) in backup directory"
		else
			log_warning "No valid files found in backup directory after processing"
		fi

		#
		rm -f "$processed_paths_file" 2>/dev/null

	else
		log_warning "No files found in backup directory"
	fi

	#
	rm -f "$temp_files_list" 2>/dev/null

	#
	log_debug "DEBUG [7.5/10]: TRANSITION CHECK - After directory scan"
	log_debug "DEBUG [7.5/10]: files_to_restore array status:"
	log_debug "DEBUG [7.5/10]:   Array is set: $([[ -n "${files_to_restore+set}" ]] && echo "YES" || echo "NO")"
	log_debug "DEBUG [7.5/10]:   Array size: ${#files_to_restore[@]}"

	if [[ ${#files_to_restore[@]} -gt 0 ]]; then
		log_debug "DEBUG [7.5/10]: First 5 files in array:"
		for i in {0..4}; do
			[[ $i -lt ${#files_to_restore[@]} ]] &&
				log_debug "DEBUG [7.5/10]:   [$i] '${files_to_restore[$i]}'"
		done
	else
		log_debug "DEBUG [7.5/10]: WARNING: files_to_restore array is empty!"

		#
		log_debug "DEBUG [7.5/10]: Listing backup directory:"
		find "$selected_backup" -type f 2>/dev/null | head -10 | while read -r f; do
			log_debug "DEBUG [7.5/10]:   $f"
		done
	fi

	#
	log_debug "DEBUG [7.5/10]: Current directory write permission: $([[ -w "." ]] && echo "YES" || echo "NO")"
	log_debug "DEBUG [7.5/10]: Current directory: $(pwd)"

	#
	if [[ -n "${SESSION_MODIFIED_FILES+set}" ]]; then
		log_debug "DEBUG [7.5/10]: SESSION_MODIFIED_FILES size: ${#SESSION_MODIFIED_FILES[@]}"
	else
		log_debug "DEBUG [7.5/10]: SESSION_MODIFIED_FILES is NOT set"
	fi
	#

	log_debug "DEBUG [8/10]: Verifying backup files..."

	#
	if [[ -z "${files_to_restore+set}" ]]; then
		log_error "CRITICAL: files_to_restore array is not set! Initializing empty array."
		files_to_restore=()
	fi

	#
	log_debug "DEBUG [8/10]: Array details:"
	log_debug "DEBUG [8/10]:   Reference: ${!files_to_restore[@]}"
	log_debug "DEBUG [8/10]:   Size: ${#files_to_restore[@]}"
	log_debug "DEBUG [8/10]:   Content (first 5):"
	for i in {0..4}; do
		if [[ $i -lt ${#files_to_restore[@]} ]]; then
			log_debug "DEBUG [8/10]:     [$i] = '${files_to_restore[$i]}'"
		fi
	done

	if [[ ${#files_to_restore[@]} -eq 0 ]]; then
		log_warning "WARNING: No files to restore after directory scan"

		#
		log_debug "DEBUG [8/10]: Backup directory '$selected_backup' contents:"
		if [[ -d "$selected_backup" ]]; then
			find "$selected_backup" -maxdepth 2 -type f 2>/dev/null | while read -r f; do
				log_debug "DEBUG [8/10]:   $f"
			done
		else
			log_debug "DEBUG [8/10]:   Backup directory does not exist!"
		fi

		#
		log_debug "DEBUG [8/10]: Trying alternative method to get files..."

		#
		local alt_files=()
		while IFS= read -r -d '' file; do
			[[ "$file" == *".sr_"* ]] && continue
			local rel_path="${file#$selected_backup/}"
			rel_path="${rel_path#./}"
			[[ -n "$rel_path" ]] && alt_files+=("$rel_path")
		done < <(find "$selected_backup" -type f -print0 2>/dev/null)

		if [[ ${#alt_files[@]} -gt 0 ]]; then
			log_debug "DEBUG [8/10]: Alternative method found ${#alt_files[@]} files"
			files_to_restore=("${alt_files[@]}")
		else
			log_error "ERROR: No files could be found to restore"
			return 1
		fi
	fi

	log_debug "DEBUG [8/10]: Will verify ${#files_to_restore[@]} file(s)"

	log_debug "DEBUG [8/10]: Preparing to verify backup files..."

	#
	if [[ -z "${files_to_restore+set}" ]]; then
		log_error "ERROR: files_to_restore array is not set!"
		files_to_restore=()
	fi

	if [[ ${#files_to_restore[@]} -eq 0 ]]; then
		log_warning "WARNING: No files to restore after processing"

		#
		log_debug "DEBUG [8/10]: Checking backup directory contents..."
		if [[ -d "$selected_backup" ]]; then
			find "$selected_backup" -type f 2>/dev/null | head -10 | while read f; do
				log_debug "DEBUG [8/10]:   Found: $f"
			done
		fi

		return 1
	fi

	log_debug "DEBUG [8/10]: Will verify ${#files_to_restore[@]} file(s)"

	log_debug "DEBUG [8/10]: Final files_to_restore count: ${#files_to_restore[@]}"

	if [[ ${#files_to_restore[@]} -eq 0 ]]; then
		log_error "No files found to restore in backup: $selected_backup"
		return 1
	fi

	#
	log_debug "DEBUG [9/10]: Starting file restoration..."

	#
	if [[ -z "${files_to_restore+set}" ]] || [[ ${#files_to_restore[@]} -eq 0 ]]; then
		log_error "FATAL: Cannot proceed - no files to restore"
		return 1
	fi

	log_debug "DEBUG [9/10]: Will restore ${#files_to_restore[@]} files"

	# Verify files exist in backup
	log_debug "DEBUG [9/10]: Verifying backup files exist..."
	local existing_files=()
	local missing_files=()

	for file in "${files_to_restore[@]}"; do
		local backup_file="$selected_backup/$file"
		log_debug "DEBUG [9/10]: Checking: $file -> $backup_file"

		# Check if file exists and is readable
		if [[ -f "$backup_file" ]] && [[ -r "$backup_file" ]]; then
			existing_files+=("$file")
			log_debug "DEBUG [9/10]: ✓ File exists and readable"
		else
			missing_files+=("$file")
			log_warning "Backup file not found or not readable: $backup_file"
			log_debug "DEBUG [9/10]: ✗ File missing or not readable"
		fi
	done

	log_debug "DEBUG [9/10]: Verification complete. Existing: ${#existing_files[@]}, Missing: ${#missing_files[@]}"

	if [[ ${#existing_files[@]} -eq 0 ]]; then
		log_error "No existing files found in backup to restore"
		return 1
	fi

	files_to_restore=("${existing_files[@]}")

	# Report missing files
	if [[ ${#missing_files[@]} -gt 0 ]]; then
		log_warning "${#missing_files[@]} file(s) not found in backup and will be skipped"
	fi

	# Confirm rollback
	log_info "Found ${#files_to_restore[@]} file(s) to restore"
	log_debug "DEBUG [10/10]: Preparing confirmation prompt..."

	if [[ "$DRY_RUN" != true ]]; then
		echo ""
		echo "The following files will be restored from backup:"
		echo "Backup location: $selected_backup"
		echo ""

		local display_count=0
		local total_files=${#files_to_restore[@]}
		# Validate total_files is numeric
		if [[ ! "$total_files" =~ ^[0-9]+$ ]]; then
			total_files=0
		fi

		local limit=20
		if [[ $total_files -lt $limit ]]; then
			limit=$total_files
		fi

		for ((i = 0; i < limit; i++)); do
			local file="${files_to_restore[$i]}"
			local backup_file="$selected_backup/$file"
			local file_size=""

			# Get file size
			if [[ -f "$backup_file" ]]; then
				file_size=$(stat -c%s "$backup_file" 2>/dev/null || stat -f%z "$backup_file" 2>/dev/null || echo "0")
				# Validate file_size is numeric
				if [[ ! "$file_size" =~ ^[0-9]+$ ]]; then
					file_size=0
				fi

				if [[ $file_size -gt 1048576 ]]; then
					file_size="$((file_size / 1048576)) MB"
				elif [[ $file_size -gt 1024 ]]; then
					file_size="$((file_size / 1024)) KB"
				else
					file_size="${file_size} bytes"
				fi
			else
				file_size="missing"
			fi

			# Validate i is numeric before arithmetic
			if [[ ! "$i" =~ ^[0-9]+$ ]]; then
				i=0
			fi
			printf "  %3d. %-60s (%s)\n" $((i + 1)) "$file" "$file_size"
			display_count=$((display_count + 1))
		done

		if [[ $total_files -gt 20 ]]; then
			local remaining=$((total_files - 20))
			echo "  ... and $remaining more file(s)"
		fi

		echo ""
		echo "Session information:"
		[[ -n "$session_id" ]] && echo "  Session ID: $session_id"
		[[ -n "$search_str" ]] && [[ -n "$replace_str" ]] && echo "  Search: '$search_str' → '$replace_str'"
		echo ""

		# Validate missing_files count
		local missing_count=${#missing_files[@]}
		if [[ ! "$missing_count" =~ ^[0-9]+$ ]]; then
			missing_count=0
		fi
		if [[ $missing_count -gt 0 ]]; then
			echo "⚠  Warning: $missing_count file(s) will not be restored (not found in backup)"
		fi

		echo ""
		log_debug "DEBUG [10/10]: ABOUT TO PROMPT USER FOR CONFIRMATION"
		if ! confirm_action_timeout "Continue with rollback?" "n" 30; then
			log_info "Rollback cancelled by user"
			return 0
		fi
		log_debug "DEBUG [10/10]: User confirmed, proceeding with rollback..."
	else
		local dry_run_count=${#files_to_restore[@]}
		if [[ ! "$dry_run_count" =~ ^[0-9]+$ ]]; then
			dry_run_count=0
		fi
		log_info "[DRY-RUN] Would restore $dry_run_count file(s) from $selected_backup"
	fi

	# Perform rollback
	local restored_count=0
	local skipped_count=0
	local failed_count=0

	log_info "Starting rollback from $selected_backup..."

	for relative_path in "${files_to_restore[@]}"; do
		local backup_file="$selected_backup/$relative_path"
		local original_file="$relative_path"

		log_debug "DEBUG [10/10]: Restoring file: $relative_path"

		if [[ "$DRY_RUN" == true ]]; then
			log_info "[DRY-RUN] Would restore: $backup_file -> $original_file"
			restored_count=$((restored_count + 1))
		else
			# Create directory if needed
			local dest_dir
			dest_dir=$(dirname "$original_file")
			if [[ ! -d "$dest_dir" ]] && [[ "$dest_dir" != "." ]]; then
				mkdir -p "$dest_dir" 2>/dev/null || {
					log_warning "Cannot create directory: $dest_dir"
					restored_count=$((restored_count + 1))
					continue
				}
			fi

			# Check if target file already exists and handle permissions
			if [[ -f "$original_file" ]]; then
				log_debug "Target file already exists: $original_file"
				log_debug "File permissions: $(ls -la "$original_file" 2>/dev/null | awk 'NR==1')"

				# Check if we can write to the file
				if [[ ! -w "$original_file" ]]; then
					log_warning "Cannot write to existing file (permission denied): $original_file"

					# Try to fix permissions if running as root
					if [[ $(id -u) -eq 0 ]]; then
						log_debug "Running as root, attempting to fix permissions..."

						# Backup current permissions
						local current_perms
						current_perms=$(stat -c "%a" "$original_file" 2>/dev/null || stat -f "%p" "$original_file" 2>/dev/null | sed 's/^[0-9]*//')

						# Try to make writable
						if chmod +w "$original_file" 2>/dev/null; then
							log_debug "Made file writable temporarily"
							# We'll restore permissions after copy
							local restore_perms=true
						else
							log_error "Failed to make file writable: $original_file"
							restored_count=$((restored_count + 1))
							continue
						fi
					else
						log_error "Permission denied and not running as root"
						restored_count=$((restored_count + 1))
						continue
					fi
				fi
			fi

			# Restore file
			if cp --preserve=all "$backup_file" "$original_file" 2>/dev/null || cp "$backup_file" "$original_file" 2>/dev/null; then
				log_success "Restored: $original_file"

				# Extract preserve ownership setting from session metadata
				local session_preserve_ownership=""
				if [[ -f "$session_metadata_file" ]]; then
					# Read from metadata file (fallback to current script setting if not found)
					session_preserve_ownership=$(grep '^SESSION_PRESERVE_OWNERSHIP=' "$session_metadata_file" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")
				fi

				# Determine if ownership/permissions should be restored
				local should_restore_perms_ownership="$PRESERVE_OWNERSHIP" # Default to current script setting

				# Override default if session metadata was found and explicitly set
				if [[ -n "$session_preserve_ownership" ]]; then # Check if variable is not empty
					if [[ "$session_preserve_ownership" == "false" ]]; then
						should_restore_perms_ownership="false"
						log_debug "Session metadata indicated ownership was NOT preserved originally, skipping restore for $original_file"
					else
						# If it was "true" or any other non-"false" value, confirm restoration
						should_restore_perms_ownership="true"
						log_debug "Session metadata indicated ownership WAS preserved originally, attempting restore for $original_file"
					fi
				else
					# session_preserve_ownership was empty (variable was empty after grep/cut/tr)
					# Fallback: Use the current script's setting
					log_debug "Session metadata for ownership missing or empty, using current script setting ($PRESERVE_OWNERSHIP) for $original_file"
				fi

				# Now perform the restoration based on the determined setting
				if [[ "$should_restore_perms_ownership" == "true" ]]; then
					# Get permissions and ownership from the backup file itself
					local backup_perms backup_owner backup_group
					backup_perms=$(stat -c "%a" "$backup_file" 2>/dev/null)
					backup_owner=$(stat -c "%u" "$backup_file" 2>/dev/null)
					backup_group=$(stat -c "%g" "$backup_file" 2>/dev/null)

					if [[ -n "$backup_perms" ]]; then
						chmod "$backup_perms" "$original_file" 2>/dev/null &&
							log_debug "Restored permissions from backup: $backup_perms for $original_file"
					fi
					if [[ -n "$backup_owner" ]] && [[ -n "$backup_group" ]]; then
						chown "$backup_owner:$backup_group" "$original_file" 2>/dev/null &&
							log_debug "Restored ownership from backup: $backup_owner:$backup_group for $original_file"
					fi
				else
					log_debug "Ownership/permissions restore disabled for $original_file (based on session or current setting)"
				fi

				restored_count=$((restored_count + 1))
			else
				log_warning "Failed to restore: $original_file"
				restored_count=$((restored_count + 1))
			fi
		fi
	done

	log_info "Rollback completed:"
	log_info "  Successfully restored: $restored_count"
	log_info "  Failed:                $failed_count"
	log_info "  Skipped:               $skipped_count"

	if [[ $failed_count -gt 0 ]]; then
		log_warning "Some files failed to restore. Check permissions and try again."
	fi

	# Cleanup old backups if configured
	if [[ "$MAX_BACKUPS" -gt 0 ]] && [[ "$DRY_RUN" != true ]]; then
		cleanup_old_backups
	fi

	log_debug "DEBUG [10/10]: Rollback function completed successfully"
	return 0
}
# NEW: Cleanup old backups
cleanup_old_backups() {
	local backup_dirs=()

	# Find all backup directories sorted by time (newest first)
	while IFS= read -r -d '' dir; do
		backup_dirs+=("$dir")
	done < <(find . -maxdepth 1 -type d -name "${BACKUP_PREFIX}.*" -print0 2>/dev/null | sort -zr)

	local count=${#backup_dirs[@]}

	if [[ $count -gt $MAX_BACKUPS ]]; then
		log_info "Cleaning up old backups (keeping $MAX_BACKUPS)..."
		for ((i = MAX_BACKUPS; i < count; i++)); do
			local dir_to_remove="${backup_dirs[$i]}"
			if [[ "$DRY_RUN" == true ]]; then
				log_info "[DRY-RUN] Would remove: $dir_to_remove"
			else
				rm -rf "$dir_to_remove"
				log_verbose "Removed old backup: $dir_to_remove"
			fi
		done
	fi
}

# NEW: List available backups with session info
list_backups() {
	local backup_dirs=()

	log_header "=== AVAILABLE BACKUPS ==="

	# Find all backup directories
	while IFS= read -r -d '' dir; do
		backup_dirs+=("$dir")
	done < <(find . -maxdepth 1 -type d -name "${BACKUP_PREFIX}.*" -print0 2>/dev/null | sort -zr)

	if [[ ${#backup_dirs[@]} -eq 0 ]]; then
		log_info "No backup directories found"
		return 0
	fi

	log_info "Found ${#backup_dirs[@]} backup(s):"
	echo

	for dir in "${backup_dirs[@]}"; do
		local file_count=0
		local size_kb=0
		local modified_files_count=0

		# Count files and size
		if [[ -d "$dir" ]]; then
			file_count=$(find "$dir" -type f -not -name ".sr_*" 2>/dev/null | wc -l)
			size_kb=$(du -sk "$dir" 2>/dev/null | cut -f1)
		fi

		# Get session metadata
		local session_info=""
		local session_id=""
		if [[ -f "$dir/.sr_session_metadata" ]]; then
			session_id=$(grep '^SESSION_ID=' "$dir/.sr_session_metadata" 2>/dev/null | cut -d= -f2 | tr -d '"')
			local pattern search replace
			pattern=$(grep '^SESSION_PATTERN=' "$dir/.sr_session_metadata" 2>/dev/null | cut -d= -f2 | tr -d '"')
			search=$(grep '^SESSION_SEARCH=' "$dir/.sr_session_metadata" 2>/dev/null | cut -d= -f2 | tr -d '"')
			replace=$(grep '^SESSION_REPLACE=' "$dir/.sr_session_metadata" 2>/dev/null | cut -d= -f2 | tr -d '"')
			modified_files_count=$(grep '^SESSION_MODIFIED_COUNT=' "$dir/.sr_session_metadata" 2>/dev/null | cut -d= -f2 | tr -d '"')

			session_info="Session: $session_id"
			[[ -n "$pattern" ]] && session_info+=" | Pattern: $pattern"
			[[ -n "$search" ]] && session_info+=" | '$search' → '$replace'"
			[[ -n "$modified_files_count" ]] && session_info+=" | Files: $modified_files_count"
		fi

		printf "  %-40s %6d files %6d KB\n" "$dir" "$file_count" "$size_kb"
		if [[ -n "$session_info" ]]; then
			echo "       $session_info"
		fi
		echo
	done

	echo
	log_info "Commands:"
	log_info "  Restore latest backup:      $0 --rollback"
	log_info "  Restore specific backup:    $0 --rollback=BACKUP_DIR_NAME"
	log_info "  Show this list again:       $0 --rollback-list"
}

# ============================================================================
# ENHANCED USAGE AND HELP FUNCTIONS
# ============================================================================

show_usage() {
	cat <<EOF
Usage: ${0##*/} [OPTIONS] <file_pattern> <search_string> <replace_string>

Search and replace text in files recursively with proper escaping of special characters.

IMPORTANT: Options must come BEFORE positional arguments for predictable parsing.

Positional arguments (required, in this order):
  <file_pattern>    File pattern to match (e.g., *.html, *.txt)
  <search_string>   Text to search for
  <replace_string>  Text to replace with

Options (must come before positional arguments):
  Core functionality:
    -d, --debug                   Enable debug output
    -nr, --no-recursive           Process only current directory (non-recursive)
    --binary                      Allow processing of binary files (REQUIRED for binary files)
    -v, --verbose                 Enable verbose output (less detailed than debug)
    --rollback[=BACKUP_DIR]       Restore from backup (latest or specified)
    --rollback-list               List available backups with session info

  Backup control:
    -nb, --no-backup              Do not create backup files
    -fb, --force-backup           Force backup creation even if disabled
    -nbf, --no-backup-folder      Create backup files in same directory as original

  Safety features:
    --binary-method=METHOD        Binary detection method: multi_layer, file_only, grep_only
    --binary-check-size=N         Bytes to check for binary detection (default: $DEFAULT_BINARY_CHECK_SIZE)
    --no-binary-skip              DEPRECATED: Use --binary instead
    --max-backups=N               Keep only N latest backups (default: $DEFAULT_MAX_BACKUPS)

  Advanced options:
    -md, --max-depth NUM          Maximum directory depth for recursive search
    -dry-run, --dry-run           Show what would be changed without making modifications
    -no-preserve, --no-preserve-ownership  Do not attempt to preserve file ownership
    -delim, --delimiter CHAR      Use custom delimiter for sed
    -e, --encoding ENC            File encoding
    -xh, --exclude-hidden         Exclude hidden files and directories
    -xb, --exclude-binary         DEPRECATED: Binary files are always excluded unless --binary is used
    -xs, --max-size MB            Maximum file size in MB
    -xp, --exclude-patterns       Exclude file patterns (space-separated)
    -xd, --exclude-dirs           Exclude directory names (space-separated)
    -sd, --search-dir DIR         Directory to search in
    -od, --output-dir DIR         Directory to save modified files (instead of in-place)
    -mode, --replace-mode MODE    Replacement mode: inplace, copy, or backup_only

  Information:
    -h, --help                    Show this help message and exit
    -V, --version                 Show version information

Examples (note option order):
  ${0##*/} -v "*.html" "old text" "new text"
  ${0##*/} --binary "*.bin" "foo" "bar"
  ${0##*/} --rollback
  ${0##*/} --rollback="sr.backup.20231215_143022"
  ${0##*/} -nr --verbose "*.txt" "find" "replace"
  ${0##*/} --dry-run --binary-method=multi_layer "*.html" "search" "replace"

Safety notes:
  - Binary files are SKIPPED by default for safety
  - Use --binary flag to explicitly allow binary file processing
  - Backups are created by default (use -nb to disable)
  - Use --dry-run to test before making changes
  - Use --rollback to restore from backup if something goes wrong
  - Each session creates a backup directory with metadata and file list

Session tracking:
  - Each run creates a unique session ID
  - All modified files are tracked in the session
  - Rollback restores ALL files modified in that session
  - Session metadata includes command, pattern, search/replace strings

Predictable parsing:
  Options -> Pattern -> Search -> Replace (in this exact order)
EOF
}

show_help() {
	show_usage
	cat <<EOF

Enhanced Features in v5.2.3:
  1. Multi-layer binary file detection (grep + file utility)
  2. Session-based rollback system with backup management
  3. Predictable argument parsing (options before positional args)
  4. Enhanced safety: binary files require explicit --binary flag
  5. Real-time file tracking for reliable rollback
  6. Fixed: File discovery now properly handles multiple files
  7. Fixed: Count occurrences now works with special characters like +

Binary Detection Methods:
  multi_layer (default): Use grep -I heuristic, verify with file utility
  file_only:            Rely only on file --mime-type command
  grep_only:            Use only grep -I heuristic (fastest)

Rollback System:
  - Automatically creates metadata for each session
  - Tracks files in real-time during processing
  - List backups with --rollback-list
  - Restore with --rollback (latest) or --rollback=BACKUP_DIR
  - Automatic cleanup of old backups with --max-backups

Exit Codes:
  0 - Success
  1 - Invalid arguments or insufficient permissions
  2 - No files found matching the pattern
  3 - Search string not found in any files
  4 - Critical error during processing
  5 - Backup creation failed
  6 - Binary file detected without --binary flag
  7 - Rollback failed

Configuration:
  Default settings can be modified in the CONFIGURABLE DEFAULTS section
  Environment variables override script defaults
  Command-line options override both defaults and environment variables

Environment Variables (override defaults):
  SR_DEBUG                 Set to 'true' to enable debug mode
  SR_DRY_RUN              Set to 'true' to enable dry-run mode
  SR_NO_BACKUP            Set to 'true' to disable backups
  SR_FORCE_BACKUP         Set to 'true' to force backup creation
  SR_MAX_DEPTH            Maximum directory depth
  SR_MAX_FILE_SIZE_MB     Maximum file size in MB
  SR_EXCLUDE_PATTERNS     Space-separated exclude patterns
  SR_EXCLUDE_DIRS         Space-separated exclude directories
  SR_SEARCH_DIR           Directory to search in
  SR_OUTPUT_DIR           Directory for output files
  SR_REPLACE_MODE         Replacement mode
  SR_ALLOW_BINARY         Set to 'true' to allow binary file processing
  SR_BINARY_METHOD        Binary detection method
  SR_BINARY_CHECK_SIZE    Bytes to check for binary detection
  SR_MAX_BACKUPS          Maximum number of backups to keep
  SR_VERBOSE              Set to 'true' for verbose output
EOF
}

show_version() {
	cat <<EOF
${0##*/} - Search and Replace Tool
Version 5.2.3 (Enterprise Enhanced Edition)
Professional text replacement utility with safety enhancements

New in v5.2.3:
- Fixed: File discovery now properly returns multiple files
- Fixed: Array initialization issue resolved
- Fixed: Count occurrences now works with special characters like +
- Enhanced: Better handling of file paths with spaces
- Improved: Session metadata includes complete command line

Core Features:
- Professional argument parsing with 30+ options
- Configurable defaults in script header
- Force backup option to override defaults
- Proper special character handling with custom delimiters
- Configurable backup options (folder-based or in-place)
- Detailed logging and statistics with color coding
- Dry-run mode for safe testing
- File timestamps preserved when no changes made
- Preserves file ownership and permissions (configurable)
- Exclude patterns and directories with wildcard support
- Maximum depth and file size limits
- Separate search and output directories
- Multiple replacement modes: inplace, copy, backup_only
- Support for multiple encodings
- GNU/BSD sed compatibility detection
- Session tracking for reliable rollbacks
- Multi-layer binary file detection
EOF
}

# ============================================================================
# COMPREHENSIVE ARGUMENT PARSING (PREDICTABLE, RESTORED)
# ============================================================================

parse_arguments() {
	local args=()
	local parsing_options=true
	local rollback_target=""

	# Store original arguments for session metadata
	SESSION_INITIAL_ARGS=("$@")

	# Special handling for help and version before full parsing
	for arg in "$@"; do
		case "$arg" in
		-h | --help | help)
			show_help
			exit 0
			;;
		-V | --version | version)
			show_version
			exit 0
			;;
		--rollback-list)
			list_backups
			exit 0
			;;
		esac
	done

	# Parse all arguments
	while [[ $# -gt 0 ]]; do
		case "$1" in
		# Options that take arguments
		-md | --max-depth)
			if [[ $# -gt 1 && "${2}" =~ ^[0-9]+$ ]]; then
				MAX_DEPTH="$2"
				log_debug "Maximum depth set to: $MAX_DEPTH"
				shift 2
			else
				log_error "Missing or invalid value for --max-depth"
				exit 1
			fi
			;;

		--rollback)
			if [[ $# -gt 1 && "${2}" != -* && ! "$2" =~ ^-- ]]; then
				rollback_target="$2"
				shift 2
			else
				rollback_target="latest"
				shift
			fi
			;;

		--rollback=*)
			rollback_target="${1#*=}"
			shift
			;;

		--binary-method)
			if [[ $# -gt 1 && -n "$2" ]]; then
				BINARY_DETECTION_METHOD="$2"
				log_debug "Binary detection method: $BINARY_DETECTION_METHOD"
				shift 2
			else
				log_error "Missing value for --binary-method"
				exit 1
			fi
			;;

		--binary-check-size)
			if [[ $# -gt 1 && "${2}" =~ ^[0-9]+$ ]]; then
				BINARY_CHECK_SIZE="$2"
				log_debug "Binary check size: $BINARY_CHECK_SIZE bytes"
				shift 2
			else
				log_error "Missing or invalid value for --binary-check-size"
				exit 1
			fi
			;;

		--max-backups)
			if [[ $# -gt 1 && "${2}" =~ ^[0-9]+$ ]]; then
				MAX_BACKUPS="$2"
				log_debug "Maximum backups to keep: $MAX_BACKUPS"
				shift 2
			else
				log_error "Missing or invalid value for --max-backups"
				exit 1
			fi
			;;

		# Standard options
		-d | --debug)
			DEBUG_MODE=true
			VERBOSE_MODE=true # Debug implies verbose
			log_debug "Debug mode enabled"
			shift
			;;

		-v | --verbose)
			VERBOSE_MODE=true
			log_debug "Verbose mode enabled"
			shift
			;;

		--binary)
			ALLOW_BINARY=true
			SKIP_BINARY_FILES=false # Override old behavior
			log_debug "Binary file processing enabled (explicit flag)"
			shift
			;;

		-nr | --no-recursive)
			RECURSIVE_MODE=false
			log_debug "Non-recursive mode enabled"
			shift
			;;

		-dry-run | --dry-run)
			DRY_RUN=true
			log_debug "Dry-run mode enabled"
			shift
			;;

		-nb | --no-backup)
			CREATE_BACKUPS=false
			log_debug "Backup creation disabled"
			shift
			;;

		-fb | --force-backup)
			FORCE_BACKUP=true
			log_debug "Force backup enabled"
			shift
			;;

		-nbf | --no-backup-folder)
			BACKUP_IN_FOLDER=false
			log_debug "Backup in same folder enabled"
			shift
			;;

		-no-preserve | --no-preserve-ownership)
			PRESERVE_OWNERSHIP=false
			log_debug "File ownership preservation disabled"
			shift
			;;

		-delim | --delimiter)
			if [[ $# -gt 1 && -n "$2" ]]; then
				SED_DELIMITER="$2"
				log_debug "Custom sed delimiter: $SED_DELIMITER"
				shift 2
			else
				log_error "Missing delimiter character"
				exit 1
			fi
			;;

		-e | --encoding)
			if [[ $# -gt 1 && -n "$2" ]]; then
				ENCODING="$2"
				log_debug "File encoding: $ENCODING"
				shift 2
			else
				log_error "Missing encoding specification"
				exit 1
			fi
			;;

		-xh | --exclude-hidden)
			SKIP_HIDDEN_FILES=true
			log_debug "Excluding hidden files"
			shift
			;;

		-xb | --exclude-binary)
			log_warning "-xb/--exclude-binary is deprecated. Binary files are now skipped by default."
			log_warning "Use --binary flag to explicitly allow binary file processing."
			SKIP_BINARY_FILES=true
			shift
			;;

		--no-binary-skip)
			log_warning "--no-binary-skip is deprecated. Use --binary instead."
			ALLOW_BINARY=true
			SKIP_BINARY_FILES=false
			shift
			;;

		-xs | --max-size)
			if [[ $# -gt 1 && "${2}" =~ ^[0-9]+$ ]]; then
				MAX_FILE_SIZE=$(($2 * 1024 * 1024))
				log_debug "Maximum file size: $2 MB"
				shift 2
			else
				log_error "Missing or invalid value for --max-size"
				exit 1
			fi
			;;

		-xp | --exclude-patterns)
			if [[ $# -gt 1 && -n "$2" ]]; then
				EXCLUDE_PATTERNS="$2"
				log_debug "Exclude patterns: $EXCLUDE_PATTERNS"
				shift 2
			else
				log_error "Missing exclude patterns"
				exit 1
			fi
			;;

		-xd | --exclude-dirs)
			if [[ $# -gt 1 && -n "$2" ]]; then
				EXCLUDE_DIRS="$2"
				log_debug "Exclude directories: $EXCLUDE_DIRS"
				shift 2
			else
				log_error "Missing exclude directories"
				exit 1
			fi
			;;

		-sd | --search-dir)
			if [[ $# -gt 1 && -n "$2" ]]; then
				SEARCH_DIR="$2"
				log_debug "Search directory: $SEARCH_DIR"
				shift 2
			else
				log_error "Missing search directory"
				exit 1
			fi
			;;

		-od | --output-dir)
			if [[ $# -gt 1 && -n "$2" ]]; then
				OUTPUT_DIR="$2"
				log_debug "Output directory: $OUTPUT_DIR"
				shift 2
			else
				log_error "Missing output directory"
				exit 1
			fi
			;;

		-mode | --replace-mode)
			if [[ $# -gt 1 && -n "$2" ]]; then
				REPLACE_MODE="$2"
				log_debug "Replace mode: $REPLACE_MODE"
				shift 2
			else
				log_error "Missing replace mode"
				exit 1
			fi
			;;

		--)
			shift
			# Everything after -- is treated as positional
			while [[ $# -gt 0 ]]; do
				args+=("$1")
				shift
			done
			break
			;;

		-*)
			# Unknown option
			log_error "Unknown option: $1"
			log_error "Options must come before positional arguments"
			show_usage
			exit 1
			;;

		*)
			# Positional argument - start collecting
			args+=("$1")
			shift
			# After first positional, stop parsing options
			parsing_options=false
			# Collect remaining as positional
			while [[ $# -gt 0 ]]; do
				args+=("$1")
				shift
			done
			break
			;;
		esac
	done

	# Handle rollback before regular operation
	if [[ -n "$rollback_target" ]]; then
		perform_rollback "$rollback_target"
		exit $?
	fi

	# ============================================================================
	# UNIVERSAL ARGUMENT PARSING - ENHANCED FOR ALL USE CASES
	# ============================================================================

	# This section handles all possible argument patterns:
	# 1. Standard 3-arg: pattern search replace
	# 2. Standard 2-arg: search replace (default pattern "*.*")
	# 3. Expanded shell patterns: pattern was expanded by shell, last two are search/replace
	# 4. Edge cases with mixed quoting and shell expansion

	# Store original argument count for diagnostics
	local arg_count=${#args[@]}
	log_debug "Raw positional arguments received: $arg_count"
	log_debug "Original command line: $0 ${SESSION_INITIAL_ARGS[*]}"
	log_debug "Current directory: $(pwd)"
	log_debug "Shell expansion test - what does '*.html' expand to here: $(echo *.html)"

	if [[ "$DEBUG_MODE" == true ]]; then
		for ((i = 0; i < arg_count; i++)); do
			log_debug "  args[$i] = '${args[$i]}'"
		done
		# Check what files exist in current directory
		log_debug "Files in current directory matching *.html:"
		for file in *.html; do
			[[ -f "$file" ]] && log_debug "  - $file"
		done
	fi

	# Minimal validation
	if [[ $arg_count -lt 2 ]]; then
		log_error "Insufficient arguments. You must provide at least search and replace strings."
		log_error "Examples:"
		log_error "  3 arguments: ${0##*/} \"*.html\" \"search\" \"replace\""
		log_error "  2 arguments: ${0##*/} \"search\" \"replace\" (uses default pattern: *.*)"
		exit 1
	fi

	# Function to detect if a string looks like a glob pattern
	is_glob_pattern() {
		local str="$1"
		[[ "$str" == *"*"* || "$str" == *"?"* || "$str" == *"["* || "$str" == *"]"* ]]
	}

	# Function to detect if a string looks like a file path
	looks_like_filepath() {
		local str="$1"
		# Check for file extension pattern (something.something) without path separator
		[[ "$str" =~ ^[^/]*\.[a-zA-Z0-9]{1,10}$ ]] || [[ "$str" == *"/"* ]]
	}

	# Function to check if argument looks like a search string (not a file)
	looks_like_search_string() {
		local str="$1"
		# If it contains glob patterns, it's not a pure search string
		if is_glob_pattern "$str"; then
			return 1
		fi
		# If it looks like a file path, it's not a search string
		if looks_like_filepath "$str"; then
			return 1
		fi
		# Otherwise, it's likely a search string
		return 0
	}

	# DEBUG: Print detailed analysis
	log_debug "=== ARGUMENT ANALYSIS ==="
	log_debug "Total arguments: $arg_count"
	log_debug "Argument breakdown:"
	for ((i = 0; i < arg_count; i++)); do
		local arg="${args[$i]}"
		local type="unknown"
		if [[ $i -eq 0 ]]; then
			if is_glob_pattern "$arg"; then
				type="GLOB_PATTERN"
			elif looks_like_filepath "$arg"; then
				type="FILE_PATH"
			elif looks_like_search_string "$arg"; then
				type="SEARCH_STRING"
			fi
		elif [[ $i -eq $((arg_count - 2)) ]] || [[ $i -eq $((arg_count - 1)) ]]; then
			if looks_like_search_string "$arg"; then
				type="SEARCH/REPLACE"
			elif looks_like_filepath "$arg"; then
				type="FILE_PATH (possible search with dots)"
			fi
		else
			if looks_like_filepath "$arg"; then
				type="FILE_PATH (middle)"
			else
				type="UNKNOWN (middle)"
			fi
		fi
		log_debug "  [$i] '$arg' - $type"
	done

	# NEW APPROACH: Determine if shell expanded glob pattern
	# If we have more than 3 arguments and first argument is a simple filename (not glob)
	# but the last two look like search strings, then shell likely expanded a glob
	local shell_expanded=0
	local detected_pattern=""

	# ============================================================================
	# ENHANCED ARGUMENT PARSING WITH SHELL EXPANSION DETECTION
	# ============================================================================

	# Declare global variable for file list from shell expansion
	declare -g FILES_LIST=()

	if [[ $arg_count -gt 3 ]]; then
		log_debug "=== SHELL EXPANSION ANALYSIS START ==="
		log_debug "Received $arg_count arguments (expected 2-3 for normal operation)"
		log_debug "First argument '${args[0]}' analysis:"
		log_debug "  Is glob pattern: $(is_glob_pattern "${args[0]}" && echo "YES" || echo "NO")"
		log_debug "  Looks like filepath: $(looks_like_filepath "${args[0]}" && echo "YES" || echo "NO")"
		log_debug "  File exists: $([[ -f "${args[0]}" ]] && echo "YES" || echo "NO")"
		log_debug "  Is directory: $([[ -d "${args[0]}" ]] && echo "YES" || echo "NO")"

		log_debug "Last two arguments analysis:"
		log_debug "  arg[$((arg_count - 2))]='${args[-2]}' - Looks like search: $(looks_like_search_string "${args[-2]}" && echo "YES" || echo "NO")"
		log_debug "  arg[$((arg_count - 1))]='${args[-1]}' - Looks like search: $(looks_like_search_string "${args[-1]}" && echo "YES" || echo "NO")"

		# Check if arguments (except last two) are files
		local all_are_files=1
		local non_file_args=() # Initialize array to avoid unbound variable error

		log_debug "Checking if arguments 0..$((arg_count - 3)) are files:"
		for ((i = 0; i < arg_count - 2; i++)); do
			local arg="${args[$i]}"
			if [[ -f "$arg" ]]; then
				log_debug "  [$i] '$arg' - EXISTS as file"
				FILES_LIST+=("$arg")
			elif [[ -e "$arg" ]]; then
				log_debug "  [$i] '$arg' - EXISTS but not a regular file"
				all_are_files=0
				non_file_args+=("$arg")
			elif is_glob_pattern "$arg"; then
				log_debug "  [$i] '$arg' - GLOB pattern (would be expanded by shell)"
				# Check if this pattern expands to existing files
				local expanded_files=()
				for expanded in $arg; do
					[[ -f "$expanded" ]] && expanded_files+=("$expanded")
				done
				if [[ ${#expanded_files[@]} -gt 0 ]]; then
					log_debug "    Expands to ${#expanded_files[@]} file(s): ${expanded_files[*]}"
					FILES_LIST+=("${expanded_files[@]}")
				else
					log_debug "    Does not expand to any existing files"
					all_are_files=0
					non_file_args+=("$arg")
				fi
			else
				log_debug "  [$i] '$arg' - NOT a file or pattern"
				all_are_files=0
				non_file_args+=("$arg")
			fi
		done

		log_debug "Shell expansion analysis results:"
		log_debug "  All arguments (except last 2) are files: $([[ $all_are_files -eq 1 ]] && echo "YES" || echo "NO")"
		log_debug "  FILES_LIST contains ${#FILES_LIST[@]} file(s)"
		log_debug "  Non-file arguments: ${non_file_args[*]:-}" # Use :- to avoid error if array is empty

		# DECISION LOGIC
		if [[ $all_are_files -eq 1 ]] || [[ ${#FILES_LIST[@]} -gt 0 ]]; then
			# Case 1: Shell expanded a pattern or explicit file list
			if [[ ${#FILES_LIST[@]} -gt 0 ]]; then
				FILE_PATTERN="" # Pattern not used when explicit file list is provided
				SEARCH_STRING="${args[-2]}"
				REPLACE_STRING="${args[-1]}"

				log_info "SHELL EXPANSION DETECTED: Processing ${#FILES_LIST[@]} file(s) from command line"
				log_debug "Files to process:"
				for ((i = 0; i < ${#FILES_LIST[@]}; i++)); do
					log_debug "  [$i] ${FILES_LIST[$i]}"
				done

				# If all files have the same extension, guess the original pattern
				if [[ $all_are_files -eq 1 ]] && [[ ${#FILES_LIST[@]} -ge 2 ]]; then
					local first_ext="${FILES_LIST[0]##*.}"
					local same_ext=true
					for file in "${FILES_LIST[@]}"; do
						if [[ "${file##*.}" != "$first_ext" ]]; then
							same_ext=false
							break
						fi
					done

					if [[ "$same_ext" == true ]]; then
						local guessed_pattern="*.${first_ext}"
						log_debug "All files have .$first_ext extension, guessing original pattern: $guessed_pattern"
						log_info "Hint: To avoid shell expansion, use quotes:"
						log_info "  $0 \"$guessed_pattern\" \"$SEARCH_STRING\" \"$REPLACE_STRING\""
					fi
				fi
			else
				# Case 2: There are non-file arguments among the first args
				log_warning "Mixed arguments detected. Assuming:"
				log_warning "  Pattern: '${args[0]}'"
				log_warning "  Search:  '${args[-2]}'"
				log_warning "  Replace: '${args[-1]}'"
				log_warning ""
				log_warning "If this is incorrect, please use one of these formats:"
				log_warning "  For pattern matching: $0 \"PATTERN\" \"SEARCH\" \"REPLACE\""
				log_warning "  For specific files:   $0 -- FILE1 FILE2 ... \"SEARCH\" \"REPLACE\""

				FILE_PATTERN="${args[0]}"
				SEARCH_STRING="${args[-2]}"
				REPLACE_STRING="${args[-1]}"
			fi
		else
			# Case 3: Could not determine - likely an error
			log_error "Cannot determine argument format. Multiple possibilities:"
			log_error "  1. Pattern '${args[0]}' with extra arguments"
			log_error "  2. Multiple files but some don't exist: ${non_file_args[*]:-}"
			log_error "  3. Invalid arguments"
			log_error ""
			log_error "Please use one of these formats:"
			log_error "  Pattern mode:  $0 \"*.html\" \"search\" \"replace\""
			log_error "  File list mode: $0 -- file1.html file2.html \"search\" \"replace\""
			log_error ""
			log_error "Note: Use '--' to separate options from file list"
			exit 1
		fi

		log_debug "=== SHELL EXPANSION ANALYSIS END ==="
		log_debug "Final decision:"
		log_debug "  FILE_PATTERN:   '$FILE_PATTERN'"
		log_debug "  FILES_LIST:     ${#FILES_LIST[@]} items"
		log_debug "  SEARCH_STRING:  '$SEARCH_STRING'"
		log_debug "  REPLACE_STRING: '$REPLACE_STRING'"

	# Case 1: Standard 2-argument call (search, replace)
	elif [[ $arg_count -eq 2 ]]; then
		FILE_PATTERN="*.*"
		SEARCH_STRING="${args[0]}"
		REPLACE_STRING="${args[1]}"

		log_debug "Detected 2-argument syntax: using default pattern='$FILE_PATTERN'"

	# Case 2: Standard 3-argument call (pattern, search, replace)
	elif [[ $arg_count -eq 3 ]]; then
		FILE_PATTERN="${args[0]}"
		SEARCH_STRING="${args[1]}"
		REPLACE_STRING="${args[2]}"

		log_debug "Detected 3-argument syntax: pattern='$FILE_PATTERN'"

	else
		# Less than 2 arguments - error
		log_error "Insufficient arguments. You must provide at least search and replace strings."
		log_error "Examples:"
		log_error "  3 arguments: ${0##*/} \"*.html\" \"search\" \"replace\""
		log_error "  2 arguments: ${0##*/} \"search\" \"replace\" (uses default pattern: *.*)"
		exit 1
	fi

	# ============================================================================
	# ADDITIONAL DEBUGGING AND VALIDATION
	# ============================================================================

	log_debug "=== FINAL ARGUMENT VALIDATION ==="

	# Check that search string is not empty
	if [[ -z "$SEARCH_STRING" ]]; then
		log_error "Search string cannot be empty"
		exit 1
	fi

	# If we have FILES_LIST, then FILE_PATTERN should be empty
	if [[ ${#FILES_LIST[@]} -gt 0 ]] && [[ -n "$FILE_PATTERN" ]]; then
		log_debug "WARNING: Both FILES_LIST (${#FILES_LIST[@]} files) and FILE_PATTERN ('$FILE_PATTERN') are set"
		log_debug "Using FILES_LIST, ignoring FILE_PATTERN"
		FILE_PATTERN=""
	fi

	# If FILE_PATTERN contains wildcards, check if it's quoted
	if is_glob_pattern "$FILE_PATTERN"; then
		log_debug "Pattern '$FILE_PATTERN' contains wildcards"

		# Check if the pattern is expanded by the shell
		local expanded_count=0
		for file in $FILE_PATTERN; do
			[[ -f "$file" ]] && expanded_count=$((expanded_count + 1))
		done

		if [[ $expanded_count -gt 0 ]]; then
			log_warning "WARNING: Pattern '$FILE_PATTERN' expands to $expanded_count file(s) via shell"
			log_warning "To process all matching files reliably, use quotes:"
			log_warning "  $0 \"$FILE_PATTERN\" \"$SEARCH_STRING\" \"$REPLACE_STRING\""
		fi
	fi

	# Debug information about the final decision
	log_info "Final configuration:"
	if [[ ${#FILES_LIST[@]} -gt 0 ]]; then
		log_info "  Processing mode:    EXPLICIT FILE LIST"
		log_info "  Files to process:   ${#FILES_LIST[@]} file(s)"
		if [[ "$VERBOSE_MODE" == true ]]; then
			for ((i = 0; i < ${#FILES_LIST[@]} && i < 5; i++)); do
				log_info "    - ${FILES_LIST[$i]}"
			done
			[[ ${#FILES_LIST[@]} -gt 5 ]] && log_info "    ... and $((${#FILES_LIST[@]} - 5)) more"
		fi
	else
		log_info "  Processing mode:    PATTERN MATCHING"
		log_info "  File pattern:       $FILE_PATTERN"
	fi
	log_info "  Search string:      $SEARCH_STRING"
	log_info "  Replace string:     $REPLACE_STRING"

	# DEBUG: Show what the pattern will match
	if [[ "$DEBUG_MODE" == true ]]; then
		log_debug "Pattern '$FILE_PATTERN' will match:"
		for file in $FILE_PATTERN; do
			[[ -f "$file" ]] && log_debug "  - $file"
		done
		# Also try find command
		log_debug "Using find to locate files:"
		find . -maxdepth 1 -name "$FILE_PATTERN" 2>/dev/null | while read -r file; do
			log_debug "  - $file"
		done
	fi

	# Check if pattern contains wildcards
	if is_glob_pattern "$FILE_PATTERN"; then
		# Test if pattern without wildcards exists
		local clean_pattern="${FILE_PATTERN//\*/}"
		clean_pattern="${clean_pattern//\?/}"
		clean_pattern="${clean_pattern//\[/}"
		clean_pattern="${clean_pattern//\]/}"

		if [[ -e "$clean_pattern" ]] && [[ "$clean_pattern" != "$FILE_PATTERN" ]]; then
			log_warning "Pattern '$FILE_PATTERN' contains wildcards, but '$clean_pattern' exists."
			log_warning "If shell expands this pattern, only '$clean_pattern' will be processed."
			log_warning "To process multiple files, quote the pattern:"
			log_warning "  $0 \"$FILE_PATTERN\" \"$SEARCH_STRING\" \"$REPLACE_STRING\""
		fi

		# Check if we're likely processing only one file due to shell expansion
		local match_count=0
		for file in $FILE_PATTERN; do
			[[ -f "$file" ]] && match_count=$((match_count + 1))
		done

		if [[ $match_count -le 1 ]] && [[ "$FILE_PATTERN" == *"*"* ]]; then
			log_warning "Pattern '$FILE_PATTERN' matches only $match_count file(s)."
			log_warning "Expected more files. Did shell expand the pattern?"
			log_warning "Try with quotes: $0 \"$FILE_PATTERN\" \"$SEARCH_STRING\" \"$REPLACE_STRING\""
		fi
	fi

	# Special warning for phone number patterns that look like file paths
	if [[ "$SEARCH_STRING" =~ \.[0-9]+ ]] || [[ "$REPLACE_STRING" =~ \.[0-9]+ ]]; then
		log_debug "Note: Search/replace strings contain dots followed by numbers (phone number format)"
	fi

	# Check if pattern contains spaces (needs quotes)
	if [[ "$FILE_PATTERN" == *" "* ]] && [[ $arg_count -gt 3 ]]; then
		log_warning "Pattern contains spaces. For reliable parsing, use quotes:"
		log_warning "  $0 \"$FILE_PATTERN\" \"$SEARCH_STRING\" \"$REPLACE_STRING\""
	fi

	# Check for empty search string
	if [[ -z "$SEARCH_STRING" ]]; then
		log_error "Search string cannot be empty"
		exit 1
	fi

	# Final recommendations
	log_debug "=== PARSING COMPLETE ==="
	log_debug "Pattern:     '$FILE_PATTERN'"
	log_debug "Search:      '$SEARCH_STRING'"
	log_debug "Replace:     '$REPLACE_STRING'"
	log_debug "Total args:  $arg_count"

	# Show warning about quoting for glob patterns
	if is_glob_pattern "$FILE_PATTERN"; then
		log_info "Note: For glob patterns like '$FILE_PATTERN', always use quotes to prevent shell expansion."
		log_info "      Correct syntax: $0 \"$FILE_PATTERN\" \"$SEARCH_STRING\" \"$REPLACE_STRING\""
	fi

	# Apply environment variable overrides (after command line parsing)
	[[ "${SR_DEBUG:-}" == "true" ]] && DEBUG_MODE=true
	[[ "${SR_DRY_RUN:-}" == "true" ]] && DRY_RUN=true
	[[ "${SR_NO_BACKUP:-}" == "true" ]] && CREATE_BACKUPS=false
	[[ "${SR_FORCE_BACKUP:-}" == "true" ]] && FORCE_BACKUP=true
	[[ -n "${SR_MAX_DEPTH:-}" ]] && MAX_DEPTH="${SR_MAX_DEPTH}"
	[[ -n "${SR_MAX_FILE_SIZE_MB:-}" ]] && MAX_FILE_SIZE=$((SR_MAX_FILE_SIZE_MB * 1024 * 1024))
	[[ -n "${SR_EXCLUDE_PATTERNS:-}" ]] && EXCLUDE_PATTERNS="${SR_EXCLUDE_PATTERNS}"
	[[ -n "${SR_EXCLUDE_DIRS:-}" ]] && EXCLUDE_DIRS="${SR_EXCLUDE_DIRS}"
	[[ -n "${SR_SEARCH_DIR:-}" ]] && SEARCH_DIR="${SR_SEARCH_DIR}"
	[[ -n "${SR_OUTPUT_DIR:-}" ]] && OUTPUT_DIR="${SR_OUTPUT_DIR}"
	[[ -n "${SR_REPLACE_MODE:-}" ]] && REPLACE_MODE="${SR_REPLACE_MODE}"
	[[ -n "${SR_ALLOW_BINARY:-}" ]] && ALLOW_BINARY="${SR_ALLOW_BINARY}"
	[[ -n "${SR_BINARY_METHOD:-}" ]] && BINARY_DETECTION_METHOD="${SR_BINARY_METHOD}"
	[[ -n "${SR_BINARY_CHECK_SIZE:-}" ]] && BINARY_CHECK_SIZE="${SR_BINARY_CHECK_SIZE}"
	[[ -n "${SR_MAX_BACKUPS:-}" ]] && MAX_BACKUPS="${SR_MAX_BACKUPS}"
	[[ -n "${SR_VERBOSE:-}" ]] && VERBOSE_MODE="${SR_VERBOSE}"

	# Validate replace mode
	if [[ "$REPLACE_MODE" != "inplace" && "$REPLACE_MODE" != "copy" && "$REPLACE_MODE" != "backup_only" ]]; then
		log_error "Invalid replace mode: $REPLACE_MODE. Must be: inplace, copy, or backup_only"
		exit 1
	fi

	# Validate binary detection method
	if [[ "$BINARY_DETECTION_METHOD" != "multi_layer" &&
		"$BINARY_DETECTION_METHOD" != "file_only" &&
		"$BINARY_DETECTION_METHOD" != "grep_only" ]]; then
		log_error "Invalid binary detection method: $BINARY_DETECTION_METHOD"
		log_error "Must be: multi_layer, file_only, or grep_only"
		exit 1
	fi

	# Force backup overrides CREATE_BACKUPS if set
	if [[ "$FORCE_BACKUP" == true ]]; then
		CREATE_BACKUPS=true
		log_debug "Force backup overrides: backups are enabled"
	fi

	# Initialize session
	init_session

	# Log configuration
	log_debug "=== Configuration ==="
	log_debug "Session ID:         $SESSION_ID"
	log_debug "File pattern:       $FILE_PATTERN"
	log_debug "Search string:      $SEARCH_STRING"
	log_debug "Replace string:     $REPLACE_STRING"
	log_debug "Recursive mode:     $RECURSIVE_MODE"
	log_debug "Max depth:          $MAX_DEPTH"
	log_debug "Dry run mode:       $DRY_RUN"
	log_debug "Create backups:     $CREATE_BACKUPS"
	log_debug "Force backup:       $FORCE_BACKUP"
	log_debug "Allow binary:       $ALLOW_BINARY"
	log_debug "Binary detection:   $BINARY_DETECTION_METHOD"
	log_debug "Binary check size:  $BINARY_CHECK_SIZE bytes"
	log_debug "Max backups:        $MAX_BACKUPS"
	log_debug "Verbose mode:       $VERBOSE_MODE"
	log_debug "Backup in folder:   $BACKUP_IN_FOLDER"
	log_debug "Preserve ownership: $PRESERVE_OWNERSHIP"
	log_debug "Skip hidden files:  $SKIP_HIDDEN_FILES"
	log_debug "Skip binary files:  $SKIP_BINARY_FILES"
	log_debug "Max file size:      $((MAX_FILE_SIZE / 1024 / 1024)) MB"
	log_debug "Exclude patterns:   $EXCLUDE_PATTERNS"
	log_debug "Exclude dirs:       $EXCLUDE_DIRS"
	log_debug "Search directory:   $SEARCH_DIR"
	log_debug "Output directory:   $OUTPUT_DIR"
	log_debug "Replace mode:       $REPLACE_MODE"
}

# ============================================================================
# COMPREHENSIVE FILE PROCESSING FUNCTIONS (RESTORED)
# ============================================================================

# Function to count occurrences (from sr_old.sh)
count_occurrences() {
	local file="$1"
	local pattern="$2"
	local count=0

	if count=$(grep -a -c -F "$pattern" "$file" 2>/dev/null); then
		echo "$count"
	else
		echo "0"
	fi
}

should_exclude_file() {
	local file="$1"
	local filename

	filename=$(basename "$file")

	log_debug "Checking if should exclude file: $file (basename: $filename)"

	# Skip hidden files if configured
	if [[ "$SKIP_HIDDEN_FILES" == true ]] && [[ "$filename" == .* ]]; then
		log_verbose "Excluding hidden file: $file"
		return 0
	fi

	# Check against exclude patterns
	local pattern
	for pattern in $EXCLUDE_PATTERNS; do
		if [[ "$filename" == $pattern ]]; then
			log_debug "File $file matches exclude pattern: $pattern"
			log_verbose "Excluding file (pattern): $file"
			return 0
		fi
	done

	# Check file size
	if [[ -f "$file" ]]; then
		local filesize
		filesize=$(stat -c%s "$file" 2>/dev/null || stat -f%z "$file" 2>/dev/null)
		if [[ "$filesize" -gt "$MAX_FILE_SIZE" ]]; then
			log_debug "File $file too large: $filesize bytes (max: $MAX_FILE_SIZE)"
			log_verbose "Skipping large file: $file ($((filesize / 1024 / 1024)) MB)"
			return 0
		fi
	else
		log_debug "File $file not found or not a regular file"
		return 0
	fi

	# Check if binary file (ENHANCED with multi-layer detection)
	if [[ "$SKIP_BINARY_FILES" == true ]] && [[ -f "$file" ]]; then
		if is_binary_file "$file"; then
			if [[ "$ALLOW_BINARY" == true ]]; then
				log_verbose "Binary file allowed (--binary flag): $file"
				return 1 # Don't exclude, allow processing
			else
				log_debug "File $file is binary, excluding"
				log_verbose "Excluding binary file (use --binary to allow): $file"
				return 0 # Exclude binary file
			fi
		else
			log_debug "File $file is not binary"
		fi
	fi

	log_debug "File $file passed all exclusion checks"
	return 1
}

should_exclude_dir() {
	local dir="$1"
	local dirname

	dirname=$(basename "$dir")

	# Skip hidden directories if configured
	if [[ "$SKIP_HIDDEN_FILES" == true ]] && [[ "$dirname" == .* ]]; then
		return 0
	fi

	# Check against exclude directories
	local exclude_dir
	for exclude_dir in $EXCLUDE_DIRS; do
		if [[ "$dirname" == "$exclude_dir" ]]; then
			log_debug "Excluding directory: $dir"
			return 0
		fi
	done

	return 1
}

# FIXED: Function to find files - properly handles debug output
find_files_simple() {
	local pattern="$1"
	local search_dir="$2"
	local recursive="$3"
	local files=()

	#
	log_debug "Finding files with pattern: $pattern in $search_dir"

	# Save current directory and change to search directory
	local original_dir
	original_dir=$(pwd)
	cd "$search_dir" || {
		log_error "Cannot change to search directory: $search_dir"
		return 1
	}

	# Enable shell options for globbing
	shopt -s nullglob 2>/dev/null || true
	shopt -s dotglob 2>/dev/null || true

	if [[ "$recursive" == true ]]; then
		# Enable globstar for recursive globbing
		if shopt -q globstar 2>/dev/null; then
			shopt -s globstar 2>/dev/null
			for file in **/$pattern; do
				[[ -f "$file" ]] && files+=("$file")
			done
			shopt -u globstar 2>/dev/null
		else
			# Fallback to find if globstar not available
			while IFS= read -r -d '' file; do
				files+=("$file")
			done < <(find . -type f -name "$pattern" -print0 2>/dev/null)
		fi
	else
		# Non-recursive search
		for file in $pattern; do
			[[ -f "$file" ]] && files+=("$file")
		done
	fi

	# Restore shell options
	shopt -u nullglob 2>/dev/null || true
	shopt -u dotglob 2>/dev/null || true

	# Change back to original directory
	cd "$original_dir" || return 1

	# Convert to absolute paths and print each on new line
	for file in "${files[@]}"; do
		local abs_file
		abs_file="$(cd "$search_dir" && pwd)/${file#./}"
		echo "$abs_file"
	done

	log_debug "Found ${#files[@]} file(s) with pattern $pattern"
}

# ENHANCED: Function to create backup with preserved ownership
create_backup() {
	local file="$1"
	local timestamp="$2"
	local owner="$3"
	local group="$4"
	local perms="$5"

	[[ "$CREATE_BACKUPS" == false ]] && return 0

	if [[ "$BACKUP_IN_FOLDER" == true ]]; then
		if [[ -z "$BACKUP_DIR" ]]; then
			BACKUP_DIR="${BACKUP_PREFIX}.${SESSION_ID}"

			if ! mkdir -p "$BACKUP_DIR"; then
				log_error "Failed to create backup directory: $BACKUP_DIR"
				return 1
			fi

			# Save initial session metadata when backup directory is created
			save_initial_session_metadata "$BACKUP_DIR"

			if [[ "$PRESERVE_OWNERSHIP" == true ]] && [[ -n "$FIRST_FILE_OWNER" ]] && [[ -n "$FIRST_FILE_GROUP" ]]; then
				chown "${FIRST_FILE_OWNER}:${FIRST_FILE_GROUP}" "$BACKUP_DIR" 2>/dev/null ||
					log_warning "Could not set backup directory ownership (running as non-root?)"
			fi

			log_info "Created backup directory: $BACKUP_DIR"
		fi

		local relative_path="${file#$SEARCH_DIR/}"
		[[ "$relative_path" == "$file" ]] && relative_path=$(basename "$file")
		local backup_path="$BACKUP_DIR/$relative_path"
		local backup_dir="${backup_path%/*}"

		mkdir -p "$backup_dir" 2>/dev/null || {
			log_warning "Cannot create backup directory: $backup_dir"
			return 1
		}

		if [[ "$PRESERVE_OWNERSHIP" == true ]] && [[ -n "$FIRST_FILE_OWNER" ]] && [[ -n "$FIRST_FILE_GROUP" ]]; then
			chown "${FIRST_FILE_OWNER}:${FIRST_FILE_GROUP}" "$backup_dir" 2>/dev/null || true
		fi

		if cp --preserve=all "$file" "$backup_path" 2>/dev/null || cp "$file" "$backup_path" 2>/dev/null; then
			if [[ "$PRESERVE_OWNERSHIP" == true ]] && [[ -n "$owner" ]] && [[ -n "$group" ]]; then
				chown "${owner}:${group}" "$backup_path" 2>/dev/null || true
			fi

			[[ -n "$perms" ]] && chmod "$perms" "$backup_path" 2>/dev/null || true

			log_verbose "Created backup: $backup_path"

			# Update file list after each successful backup
			if [[ -n "$BACKUP_DIR" ]] && [[ -d "$BACKUP_DIR" ]]; then
				update_backup_filelist
			fi
		else
			log_warning "Failed to create backup for: $file"
			return 1
		fi
	else
		local backup_file="${file}.${BACKUP_PREFIX}_${SESSION_ID}"

		if cp --preserve=all "$file" "$backup_file" 2>/dev/null || cp "$file" "$backup_file" 2>/dev/null; then
			if [[ "$PRESERVE_OWNERSHIP" == true ]] && [[ -n "$owner" ]] && [[ -n "$group" ]]; then
				chown "${owner}:${group}" "$backup_file" 2>/dev/null || true
			fi

			[[ -n "$perms" ]] && chmod "$perms" "$backup_file" 2>/dev/null || true

			log_verbose "Created backup: $backup_file"
		else
			log_warning "Failed to create backup for: $file"
			return 1
		fi
	fi

	return 0
}

# RESTORED: Function to create output file with directory structure
create_output_file() {
	local source_file="$1"
	local search_dir="$2"
	local output_dir="$3"
	local modified_content="$4"

	# Get relative path from search directory
	local relative_path="${source_file#$search_dir/}"
	[[ "$relative_path" == "$source_file" ]] && relative_path=$(basename "$source_file")

	local output_file="$output_dir/$relative_path"
	local output_file_dir="${output_file%/*}"

	# Create directory structure
	mkdir -p "$output_file_dir" 2>/dev/null || {
		log_error "Cannot create output directory: $output_file_dir"
		return 1
	}

	# Write modified content to output file
	echo "$modified_content" >"$output_file" || {
		log_error "Failed to write to output file: $output_file"
		return 1
	}

	# Try to preserve permissions from source
	if [[ -f "$source_file" ]] && [[ "$PRESERVE_OWNERSHIP" == true ]]; then
		local source_perms
		source_perms=$(stat -c "%a" "$source_file" 2>/dev/null)
		[[ -n "$source_perms" ]] && chmod "$source_perms" "$output_file" 2>/dev/null || true
	fi

	log_verbose "Created output file: $output_file"
	echo "$output_file"
}

# ============================================================================
# ENHANCED FILE PROCESSING WITH DUAL MODE SUPPORT
# ============================================================================

process_files() {
	local timestamp files=()
	local search_escaped replace_escaped
	local start_time end_time processing_time

	start_time=$(date +%s.%N)
	timestamp=$(date +"$TIMESTAMP_FORMAT")

	# Escape search and replace strings
	search_escaped=$(escape_regex "$SEARCH_STRING")
	replace_escaped=$(escape_replacement "$REPLACE_STRING")

	log_header "=== FILE PROCESSING STARTED ==="
	log_info "Session ID: $SESSION_ID"
	log_info "Start time: $(date)"
	log_verbose "Current directory: $(pwd)"
	log_debug "Search directory: $SEARCH_DIR"
	log_debug "Recursive mode: $RECURSIVE_MODE"
	log_debug "Max depth: $MAX_DEPTH"

	# ========================================================================
	# MODE 1: EXPLICIT FILE LIST FROM SHELL EXPANSION OR USER INPUT
	# ========================================================================
	if [[ ${#FILES_LIST[@]} -gt 0 ]]; then
		log_info "=== MODE: EXPLICIT FILE LIST ==="
		log_info "Processing ${#FILES_LIST[@]} file(s) from command line"

		if [[ "$DEBUG_MODE" == true ]]; then
			log_debug "FILES_LIST detailed analysis:"
			for ((i = 0; i < ${#FILES_LIST[@]}; i++)); do
				local file="${FILES_LIST[$i]}"
				local file_status=""

				if [[ ! -e "$file" ]]; then
					file_status="DOES NOT EXIST"
				elif [[ -d "$file" ]]; then
					file_status="DIRECTORY (will be skipped)"
				elif [[ ! -f "$file" ]]; then
					file_status="NOT REGULAR FILE"
				elif [[ ! -r "$file" ]]; then
					file_status="NOT READABLE"
				else
					file_status="OK - $(stat -c "%s" "$file" 2>/dev/null || echo "?") bytes"
				fi

				log_debug "  [$i] '$file' - $file_status"
			done
		fi

		# Filter and validate files
		local valid_files=()
		local skipped_files=()
		local skip_reasons=()

		for file in "${FILES_LIST[@]}"; do
			local skip_reason=""
			local normalized_file

			# Convert to absolute path for consistent checking
			if [[ "$file" == /* ]]; then
				normalized_file="$file"
			else
				normalized_file="$(pwd)/${file#./}"
			fi

			log_debug "Validating file: $file (normalized: $normalized_file)"

			# Skip backup directories (check if file is inside a backup directory)
			local in_backup_dir=false
			local path_part="$normalized_file"
			while [[ "$path_part" != "/" ]] && [[ -n "$path_part" ]]; do
				local dir_name=$(basename "$path_part")
				if [[ "$dir_name" == "${BACKUP_PREFIX}."* ]]; then
					in_backup_dir=true
					skip_reason="inside backup directory '$dir_name'"
					log_debug "  Skipping: File is inside backup directory: $dir_name"
					break
				fi
				path_part=$(dirname "$path_part")
			done

			if [[ "$in_backup_dir" == true ]]; then
				skipped_files+=("$file")
				skip_reasons+=("$skip_reason")
				continue
			fi

			# Basic file validation
			if [[ ! -e "$file" ]]; then
				skip_reason="file does not exist"
			elif [[ -d "$file" ]]; then
				skip_reason="is a directory"
			elif [[ ! -f "$file" ]]; then
				skip_reason="not a regular file"
			elif [[ ! -r "$file" ]]; then
				skip_reason="file not readable"
			elif should_exclude_file "$file"; then
				skip_reason="excluded by filters"
			fi

			if [[ -n "$skip_reason" ]]; then
				skipped_files+=("$file")
				skip_reasons+=("$skip_reason")
				log_debug "  Skipping: $skip_reason"
			else
				valid_files+=("$file")
				log_debug "  Accepted: $file"
			fi
		done

		files=("${valid_files[@]}")

		# Report skipped files
		if [[ ${#skipped_files[@]} -gt 0 ]]; then
			log_warning "Skipped ${#skipped_files[@]} invalid or excluded file(s):"
			for ((i = 0; i < ${#skipped_files[@]}; i++)); do
				log_warning "  - ${skipped_files[$i]} (${skip_reasons[$i]})"
			done
		fi

		if [[ ${#files[@]} -eq 0 ]]; then
			log_error "No valid files to process from the provided list"
			return 2
		fi

		log_info "Will process ${#files[@]} valid file(s) from explicit list"

	# ========================================================================
	# MODE 2: PATTERN-BASED FILE DISCOVERY
	# ========================================================================
	else
		log_info "=== MODE: PATTERN MATCHING ==="
		log_info "Searching for files with pattern: $FILE_PATTERN"
		log_info "Search directory: $SEARCH_DIR"

		if [[ "$DEBUG_MODE" == true ]]; then
			log_debug "Pattern analysis:"
			log_debug "  Raw pattern: '$FILE_PATTERN'"
			log_debug "  Contains wildcards: $(is_glob_pattern "$FILE_PATTERN" && echo "YES" || echo "NO")"
			log_debug "  Shell would expand to: $(for f in $FILE_PATTERN; do [[ -f "$f" ]] && echo -n "$f "; done)"
		fi

		# Find files using the enhanced find function
		log_debug "Starting file discovery with pattern: $FILE_PATTERN"

		# Use temporary file for reliable handling of large file lists
		local temp_file_list
		temp_file_list=$(mktemp 2>/dev/null || echo "/tmp/sr_filelist_$$")

		# Enhanced find with backup directory exclusion
		if [[ "$RECURSIVE_MODE" == true ]]; then
			log_debug "Using recursive find with max depth $MAX_DEPTH"
			local find_cmd="find \"$SEARCH_DIR\" -maxdepth $MAX_DEPTH"
		else
			log_debug "Using non-recursive find"
			local find_cmd="find \"$SEARCH_DIR\" -maxdepth 1"
		fi

		# Build exclusion filters
		local exclude_filter=""
		if [[ -n "$EXCLUDE_DIRS" ]]; then
			for dir in $EXCLUDE_DIRS; do
				exclude_filter+=" -name \"$dir\" -prune -o"
			done
		fi

		# Always exclude backup directories
		exclude_filter+=" -name \"${BACKUP_PREFIX}.*\" -prune -o"

		# Execute find command
		local find_full_cmd="$find_cmd $exclude_filter -type f -name \"$FILE_PATTERN\" -print0 2>/dev/null"
		log_debug "Find command: $find_full_cmd"

		# Execute and capture results
		local find_start=$(date +%s.%N)
		eval "$find_full_cmd" >"$temp_file_list.tmp"

		# Read null-terminated files into array
		files=()
		if [[ -s "$temp_file_list.tmp" ]]; then
			while IFS= read -r -d '' file; do
				[[ -n "$file" ]] && files+=("$file")
			done <"$temp_file_list.tmp"
		fi

		local find_end=$(date +%s.%N)
		local find_time=$(echo "$find_end - $find_start" | bc 2>/dev/null | awk '{printf "%.3f", $0}')

		log_debug "File discovery completed in ${find_time}s"
		rm -f "$temp_file_list.tmp" 2>/dev/null

		# Alternative method if find returns nothing
		if [[ ${#files[@]} -eq 0 ]]; then
			log_warning "No files found via find command, trying alternative methods..."

			# Method 1: Simple shell globbing
			local glob_files=()
			if [[ "$RECURSIVE_MODE" == true ]] && shopt -q globstar 2>/dev/null; then
				log_debug "Trying globstar expansion"
				shopt -s globstar nullglob 2>/dev/null
				for file in "$SEARCH_DIR"/**/"$FILE_PATTERN"; do
					[[ -f "$file" ]] && glob_files+=("$file")
				done
				shopt -u globstar nullglob 2>/dev/null
			else
				log_debug "Trying simple glob expansion"
				shopt -s nullglob 2>/dev/null
				for file in "$SEARCH_DIR"/"$FILE_PATTERN"; do
					[[ -f "$file" ]] && glob_files+=("$file")
				done
				shopt -u nullglob 2>/dev/null
			fi

			if [[ ${#glob_files[@]} -gt 0 ]]; then
				files=("${glob_files[@]}")
				log_debug "Found ${#files[@]} file(s) via shell globbing"

				# Filter out backup directory files
				local filtered_files=()
				for file in "${files[@]}"; do
					local skip=false
					local path="$file"

					# Check if file is inside backup directory
					while [[ "$path" != "/" ]] && [[ -n "$path" ]]; do
						local dir_name=$(basename "$path")
						if [[ "$dir_name" == "${BACKUP_PREFIX}."* ]]; then
							skip=true
							log_debug "Excluding file in backup directory: $file"
							break
						fi
						[[ "$path" == "." ]] && break
						path=$(dirname "$path")
					done

					[[ "$skip" == false ]] && filtered_files+=("$file")
				done

				files=("${filtered_files[@]}")
			fi
		fi

		if [[ ${#files[@]} -eq 0 ]]; then
			log_error "No files found matching pattern '$FILE_PATTERN' in '$SEARCH_DIR'"

			# Provide debugging information
			if [[ "$DEBUG_MODE" == true ]]; then
				log_debug "Directory listing of $SEARCH_DIR:"
				ls -la "$SEARCH_DIR" 2>/dev/null | head -20

				log_debug "Files with similar patterns:"
				find "$SEARCH_DIR" -maxdepth 2 -type f -name "*" 2>/dev/null |
					grep -i "${FILE_PATTERN//\*/}" | head -10
			fi

			return 2
		fi

		log_info "Found ${#files[@]} file(s) matching pattern"
	fi

	# ========================================================================
	# COMMON PROCESSING PHASE FOR BOTH MODES
	# ========================================================================

	local file_count=${#files[@]}
	log_info "=== PROCESSING $file_count FILE(S) ==="

	if [[ "$VERBOSE_MODE" == true ]] && [[ $file_count -gt 0 ]]; then
		log_verbose "File list (first 10):"
		for ((i = 0; i < file_count && i < 10; i++)); do
			log_verbose "  [$((i + 1))] ${files[$i]}"
		done
		[[ $file_count -gt 10 ]] && log_verbose "  ... and $((file_count - 10)) more"
	fi

	# Initialize counters
	local processed_count=0
	local modified_count=0
	local replacement_count=0
	local skipped_count=0
	local error_count=0

	# Statistics for detailed reporting
	local stats_by_extension=()
	local stats_by_size=("small:0" "medium:0" "large:0")
	local stats_by_result=("success:0" "no_change:0" "error:0" "skipped:0")

	# Create progress tracking variables
	local progress_interval=$((file_count > 100 ? file_count / 20 : 5))
	[[ $progress_interval -lt 1 ]] && progress_interval=1

	log_debug "Progress reporting every $progress_interval files"

	# Process each file
	for file_idx in "${!files[@]}"; do
		local file="${files[$file_idx]}"
		local file_display="${file#$SEARCH_DIR/}"
		[[ "$file_display" == "$file" ]] && file_display="$file"

		# Progress reporting
		if [[ $(((file_idx + 1) % progress_interval)) -eq 0 ]] || [[ $((file_idx + 1)) -eq $file_count ]]; then
			local progress_pct=$(((file_idx + 1) * 100 / file_count))
			log_info "Progress: $((file_idx + 1))/$file_count files ($progress_pct%) - Modified: $modified_count, Replacements: $replacement_count"
		fi

		log_debug "Processing file [$((file_idx + 1))/$file_count]: $file_display"

		# Detailed file analysis for debugging
		if [[ "$DEBUG_MODE" == true ]]; then
			local file_info=""
			if [[ -f "$file" ]]; then
				local file_size=$(stat -c "%s" "$file" 2>/dev/null || echo "?")
				local file_perm=$(stat -c "%a" "$file" 2>/dev/null || echo "?")
				local file_owner=$(stat -c "%U:%G" "$file" 2>/dev/null || echo "?:?")
				local file_mime=$(file -b --mime-type "$file" 2>/dev/null || echo "unknown")

				file_info="size=${file_size}, perm=${file_perm}, owner=${file_owner}, mime=${file_mime}"

				# Update size statistics
				if [[ "$file_size" =~ ^[0-9]+$ ]]; then
					if [[ $file_size -lt 10240 ]]; then
						stats_by_size[0]="small:$((${stats_by_size[0]#*:} + 1))"
					elif [[ $file_size -lt 1048576 ]]; then
						stats_by_size[1]="medium:$((${stats_by_size[1]#*:} + 1))"
					else
						stats_by_size[2]="large:$((${stats_by_size[2]#*:} + 1))"
					fi
				fi
			else
				file_info="NOT FOUND"
			fi
			log_debug "  File info: $file_info"
		fi

		# Check if file is inside backup directory (additional safety check)
		local in_backup_dir=false
		local check_path="$file"
		while [[ "$check_path" != "/" ]] && [[ -n "$check_path" ]]; do
			local dir_name=$(basename "$check_path")
			if [[ "$dir_name" == "${BACKUP_PREFIX}."* ]]; then
				in_backup_dir=true
				log_warning "SAFETY CHECK: Skipping file in backup directory: $file"
				skipped_count=$((skipped_count + 1))
				stats_by_result[3]="skipped:$((${stats_by_result[3]#*:} + 1))"
				break
			fi
			[[ "$check_path" == "." ]] && break
			check_path=$(dirname "$check_path")
		done

		[[ "$in_backup_dir" == true ]] && continue

		# Perform the replacement
		local result=0
		perform_replace "$file" "$search_escaped" "$replace_escaped" "$timestamp" || result=$?

		processed_count=$((processed_count + 1))

		# Process results
		case $result in
		0)
			# File was modified
			modified_count=$((modified_count + 1))
			replacement_count=$((replacement_count + TOTAL_REPLACEMENTS - replacement_count))
			stats_by_result[0]="success:$((${stats_by_result[0]#*:} + 1))"

			# Track file extension statistics
			local ext="${file##*.}"
			[[ "$ext" == "$file" ]] && ext="no_extension"
			local found_ext=false
			for i in "${!stats_by_extension[@]}"; do
				if [[ "${stats_by_extension[$i]%%:*}" == "$ext" ]]; then
					local count="${stats_by_extension[$i]#*:}"
					stats_by_extension[$i]="${ext}:$((count + 1))"
					found_ext=true
					break
				fi
			done
			[[ "$found_ext" == false ]] && stats_by_extension+=("${ext}:1")
			;;
		1 | 2 | 3 | 4 | 5 | 6 | 7)
			# Various errors (see exit codes in documentation)
			error_count=$((error_count + 1))
			stats_by_result[2]="error:$((${stats_by_result[2]#*:} + 1))"
			;;
		*)
			# No changes made
			stats_by_result[1]="no_change:$((${stats_by_result[1]#*:} + 1))"
			;;
		esac

		# Update SESSION_MODIFIED_FILES (if file was modified)
		if [[ $result -eq 0 ]] && [[ -n "${SESSION_MODIFIED_FILES+set}" ]]; then
			# Check if file already in array
			local already_tracked=false
			for tracked_file in "${SESSION_MODIFIED_FILES[@]}"; do
				if [[ "$tracked_file" == "$file" ]]; then
					already_tracked=true
					break
				fi
			done

			if [[ "$already_tracked" == false ]]; then
				SESSION_MODIFIED_FILES+=("$file")
				log_debug "Tracked modified file: $file (total: ${#SESSION_MODIFIED_FILES[@]})"
			fi
		fi
	done

	# ========================================================================
	# PROCESSING COMPLETE - FINAL STATISTICS
	# ========================================================================

	end_time=$(date +%s.%N)
	processing_time=$(echo "$end_time - $start_time" | bc 2>/dev/null | awk '{printf "%.3f", $0}')

	log_header "=== PROCESSING COMPLETE ==="
	log_info "Total processing time: ${processing_time}s"
	log_info "Files processed:      $processed_count"
	log_info "Files modified:       $modified_count"
	log_info "Total replacements:   $replacement_count"
	log_info "Files skipped:        $skipped_count"
	log_info "Errors encountered:   $error_count"

	if [[ "$DEBUG_MODE" == true ]] || [[ "$VERBOSE_MODE" == true ]]; then
		log_verbose "=== DETAILED STATISTICS ==="
		log_verbose "By result:"
		for stat in "${stats_by_result[@]}"; do
			local type="${stat%%:*}"
			local count="${stat#*:}"
			log_verbose "  ${type}: $count file(s)"
		done

		log_verbose "By size:"
		for stat in "${stats_by_size[@]}"; do
			local type="${stat%%:*}"
			local count="${stat#*:}"
			log_verbose "  ${type}: $count file(s)"
		done

		if [[ ${#stats_by_extension[@]} -gt 0 ]]; then
			log_verbose "By extension (top 10):"
			# Sort by count
			local sorted_exts=($(for ext in "${stats_by_extension[@]}"; do
				echo "$ext"
			done | sort -t: -k2 -nr))

			for ((i = 0; i < ${#sorted_exts[@]} && i < 10; i++)); do
				local ext="${sorted_exts[$i]%%:*}"
				local count="${sorted_exts[$i]#*:}"
				log_verbose "  .$ext: $count file(s)"
			done
		fi

		# Show processing rate
		if [[ $(echo "$processing_time > 0" | bc 2>/dev/null) -eq 1 ]]; then
			local rate=$(echo "scale=2; $processed_count / $processing_time" | bc 2>/dev/null)
			log_verbose "Processing rate: ${rate} files/second"
		fi
	fi

	# Store final counts in global variables
	PROCESSED_FILES=$processed_count
	MODIFIED_FILES=$modified_count
	TOTAL_REPLACEMENTS=$replacement_count

	# Final validation
	if [[ $processed_count -eq 0 ]]; then
		log_error "No files were processed"
		return 2
	fi

	if [[ $modified_count -eq 0 ]] && [[ "$DRY_RUN" != true ]]; then
		log_warning "Search pattern not found in any processed files"
		return 3
	fi

	return 0
}

# ENHANCED: Perform replacement with session tracking (FULLY FIXED - NO GLOBAL ARRAYS)
perform_replace() {
	local file="$1"
	local search_escaped="$2"
	local replace_escaped="$3"
	local timestamp="$4"
	local target_file="$file" # Default to original file
	local S=0

	# Initialize counter safely
	S=$((S + 1))
	PROCESSED_FILES=$((PROCESSED_FILES + 1))

	log_debug "Processing file: $file"

	# Validate file exists and is readable
	[[ ! -f "$file" ]] && {
		log_warning "File not found: $file"
		return 1
	}
	[[ ! -r "$file" ]] && {
		log_warning "Cannot read file: $file"
		return 1
	}

	# Check if file is binary
	if is_binary_file "$file"; then
		if [[ "$ALLOW_BINARY" == true ]]; then
			log_warning "Processing binary file (--binary flag used): $file"
		else
			log_warning "Skipping binary file (use --binary to allow): $file"
			return 6
		fi
	fi

	# Check if file should be excluded
	if should_exclude_file "$file"; then
		log_verbose "Skipping excluded file: $file"
		return 0
	fi

	# Count occurrences before replacement
	local occurrences_before=0
	log_debug "Searching for string: '$SEARCH_STRING' in $file"

	# Use simple method to check if string exists
	if grep -q -F -- "$SEARCH_STRING" "$file" 2>/dev/null; then
		# Count using grep -o for accurate count
		if grep -o -F -- "$SEARCH_STRING" "$file" >/dev/null 2>&1; then
			occurrences_before=$(grep -o -F -- "$SEARCH_STRING" "$file" 2>/dev/null | wc -l 2>/dev/null)
		else
			# Fallback to grep -c if -o not supported
			occurrences_before=$(grep -c -F -- "$SEARCH_STRING" "$file" 2>/dev/null)
		fi
	fi

	# Validate count is numeric
	if [[ -z "$occurrences_before" ]] || [[ ! "$occurrences_before" =~ ^[0-9]+$ ]]; then
		occurrences_before=0
	fi

	if [[ "$occurrences_before" -eq 0 ]]; then
		log_verbose "Search string '$SEARCH_STRING' not found in: $file"
		return 0
	fi

	log_debug "Found $occurrences_before occurrence(s) of '$SEARCH_STRING' in $file"

	if [[ "$DRY_RUN" == true ]]; then
		PROCESSED_FILES=$((PROCESSED_FILES + 1))
		if [[ $occurrences_before -gt 0 ]]; then
			MODIFIED_FILES=$((MODIFIED_FILES + 1))
		fi
		log_success "[DRY-RUN] Would replace $occurrences_before occurrence(s) in $file"
		# Safe arithmetic
		if [[ "$TOTAL_REPLACEMENTS" =~ ^[0-9]+$ ]] && [[ "$occurrences_before" =~ ^[0-9]+$ ]]; then
			TOTAL_REPLACEMENTS=$((TOTAL_REPLACEMENTS + occurrences_before))
		else
			[[ ! "$TOTAL_REPLACEMENTS" =~ ^[0-9]+$ ]] && TOTAL_REPLACEMENTS=0
			TOTAL_REPLACEMENTS=$((TOTAL_REPLACEMENTS + occurrences_before))
		fi
		S=$((S + 1))
		return 0
	fi

	# Get file ownership and permissions
	local original_owner="" original_group="" original_perms="" original_mtime=""

	if [[ "$PRESERVE_OWNERSHIP" == true ]] && command -v stat >/dev/null 2>&1; then
		original_owner=$(stat -c "%u" "$file" 2>/dev/null)
		original_group=$(stat -c "%g" "$file" 2>/dev/null)
		original_perms=$(stat -c "%a" "$file" 2>/dev/null)
		original_mtime=$(stat -c "%Y" "$file" 2>/dev/null || stat -f "%m" "$file" 2>/dev/null)

		if [[ -z "$FIRST_FILE_OWNER" ]] && [[ -n "$original_owner" ]]; then
			FIRST_FILE_OWNER="$original_owner"
			FIRST_FILE_GROUP="$original_group"
			log_debug "First file owner: $FIRST_FILE_OWNER:$FIRST_FILE_GROUP"
		fi
	fi

	# Determine target file based on replace mode
	if [[ "$REPLACE_MODE" == "copy" ]] && [[ -n "$OUTPUT_DIR" ]]; then
		target_file="$OUTPUT_DIR/${file#$SEARCH_DIR/}"
		log_debug "Copy mode: will write to $target_file"
	fi

	# Create backup for original file if inplace mode
	if [[ "$CREATE_BACKUPS" == true ]] && [[ "$REPLACE_MODE" == "inplace" ]]; then
		if ! create_backup "$file" "$timestamp" "$original_owner" "$original_group" "$original_perms"; then
			log_error "Backup creation failed for: $file"
			return 5
		fi
	fi

	# Create temporary files
	local temp_file temp_output
	temp_file=$(mktemp -p "$TEMP_DIR" 2>/dev/null || echo "/tmp/sr_temp_in_$$")
	temp_output=$(mktemp -p "$TEMP_DIR" 2>/dev/null || echo "/tmp/sr_temp_out_$$")

	# Make sure temp files are created
	: >"$temp_file"
	: >"$temp_output"

	# Copy original to temp file
	if ! cp "$file" "$temp_file" 2>/dev/null; then
		log_error "Failed to create temporary file for: $file"
		rm -f "$temp_file" "$temp_output" 2>/dev/null
		return 1
	fi

	local sed_success=false
	local replacements_made=0

	# Debug information
	log_debug "DEBUG: Search string: '$SEARCH_STRING'"
	log_debug "DEBUG: Search escaped: '$search_escaped'"
	log_debug "DEBUG: Replace string: '$REPLACE_STRING'"
	log_debug "DEBUG: Replace escaped: '$replace_escaped'"
	log_debug "DEBUG: Sed delimiter: '$SED_DELIMITER'"

	# Try sed replacement with different approaches
	log_debug "Trying sed replacement..."

	# Test if sed command works with current pattern
	local test_cmd="echo 'test $SEARCH_STRING test' | sed 's${SED_DELIMITER}${search_escaped}${SED_DELIMITER}${replace_escaped}${SED_DELIMITER}g' 2>&1"
	local test_output
	if test_output=$(eval "$test_cmd" 2>/dev/null); then
		log_debug "Sed test passed: '$test_output'"
	else
		log_debug "Sed test failed, trying alternative approach..."
	fi

	# Approach 1: Basic sed with custom delimiter
	if sed "s${SED_DELIMITER}${search_escaped}${SED_DELIMITER}${replace_escaped}${SED_DELIMITER}g" "$temp_file" >"$temp_output" 2>/dev/null; then
		sed_success=true
		log_debug "Sed succeeded with basic pattern"
	# Approach 2: Use @ as delimiter
	elif sed "s@${search_escaped}@${replace_escaped}@g" "$temp_file" >"$temp_output" 2>/dev/null; then
		sed_success=true
		log_debug "Sed succeeded with @ delimiter"
	# Approach 3: Use # as delimiter
	elif sed "s#${search_escaped}#${replace_escaped}#g" "$temp_file" >"$temp_output" 2>/dev/null; then
		sed_success=true
		log_debug "Sed succeeded with # delimiter"
	# Approach 4: Use ! as delimiter
	elif sed "s!${search_escaped}!${replace_escaped}!g" "$temp_file" >"$temp_output" 2>/dev/null; then
		sed_success=true
		log_debug "Sed succeeded with ! delimiter"
	# Approach 5: Try with -E flag (extended regex)
	elif sed -E "s${SED_DELIMITER}${search_escaped}${SED_DELIMITER}${replace_escaped}${SED_DELIMITER}g" "$temp_file" >"$temp_output" 2>/dev/null; then
		sed_success=true
		log_debug "Sed succeeded with -E flag"
	else
		log_debug "All sed approaches failed, trying perl..."
		# Approach 6: Use perl for more robust replacement
		if command -v perl >/dev/null 2>&1; then
			if perl -pe "s/\Q$SEARCH_STRING\E/$REPLACE_STRING/g" "$temp_file" >"$temp_output" 2>/dev/null; then
				sed_success=true
				log_debug "Perl replacement succeeded"
			fi
		fi
	fi

	if [[ "$sed_success" == true ]]; then
		# Count after replacement
		local occurrences_after=0

		if grep -q -F -- "$SEARCH_STRING" "$temp_output" 2>/dev/null; then
			if grep -o -F -- "$SEARCH_STRING" "$temp_output" >/dev/null 2>&1; then
				occurrences_after=$(grep -o -F -- "$SEARCH_STRING" "$temp_output" 2>/dev/null | wc -l 2>/dev/null)
			else
				occurrences_after=$(grep -c -F -- "$SEARCH_STRING" "$temp_output" 2>/dev/null)
			fi
		fi

		# Validate count is numeric
		if [[ -z "$occurrences_after" ]] || [[ ! "$occurrences_after" =~ ^[0-9]+$ ]]; then
			occurrences_after=0
		fi

		log_debug "Occurrences before: $occurrences_before, after: $occurrences_after"

		# Safe arithmetic calculation
		if [[ "$occurrences_before" =~ ^[0-9]+$ ]] && [[ "$occurrences_after" =~ ^[0-9]+$ ]]; then
			replacements_made=$((occurrences_before - occurrences_after))
			log_debug "Replacements made: $replacements_made"
		else
			log_warning "Invalid count values: before='$occurrences_before', after='$occurrences_after'"
			replacements_made=0
		fi

		if [[ "$replacements_made" -gt 0 ]]; then
			S=$((S + 1))
			MODIFIED_FILES=$((MODIFIED_FILES + 1))

			# Safe addition to TOTAL_REPLACEMENTS
			if [[ "$TOTAL_REPLACEMENTS" =~ ^[0-9]+$ ]] && [[ "$replacements_made" =~ ^[0-9]+$ ]]; then
				TOTAL_REPLACEMENTS=$((TOTAL_REPLACEMENTS + replacements_made))
			else
				[[ ! "$TOTAL_REPLACEMENTS" =~ ^[0-9]+$ ]] && TOTAL_REPLACEMENTS=0
				TOTAL_REPLACEMENTS=$((TOTAL_REPLACEMENTS + replacements_made))
			fi

			SESSION_MODIFIED_FILES+=("$file")

			# Track modified file by adding to the list in backup directory
			if [[ -n "$BACKUP_DIR" ]] && [[ -d "$BACKUP_DIR" ]] && [[ "$CREATE_BACKUPS" == true ]]; then
				# Get relative path
				local relative_path
				if command -v realpath >/dev/null 2>&1; then
					relative_path=$(realpath --relative-to="." "$file" 2>/dev/null || echo "$file")
				else
					if [[ "$file" == /* ]]; then
						relative_path="${file#$(pwd)/}"
						[[ "$relative_path" == "$file" ]] && relative_path=$(basename "$file")
					else
						relative_path="$file"
					fi
				fi

				relative_path="${relative_path#./}"

				# Append to modified files list (avoid duplicates)
				local modified_list="$BACKUP_DIR/.sr_modified_files"
				if [[ ! -f "$modified_list" ]] || ! grep -q -F -x "$relative_path" "$modified_list" 2>/dev/null; then
					echo "$relative_path" >>"$modified_list"
					log_debug "Recorded modified file: $relative_path"
				fi
			fi

			if [[ "$REPLACE_MODE" == "copy" ]] && [[ -n "$OUTPUT_DIR" ]]; then
				# Copy mode: write to output directory
				local output_file_dir="${target_file%/*}"
				mkdir -p "$output_file_dir" 2>/dev/null || {
					log_error "Cannot create output directory: $output_file_dir"
					rm -f "$temp_file" "$temp_output" 2>/dev/null
					return 1
				}

				if cp "$temp_output" "$target_file" 2>/dev/null; then
					# Try to preserve permissions
					if [[ "$PRESERVE_OWNERSHIP" == true ]] && [[ -n "$original_perms" ]]; then
						chmod "$original_perms" "$target_file" 2>/dev/null || true
					fi
					log_success "Created modified copy with $replacements_made replacement(s): $target_file"
				else
					log_error "Failed to write to output file: $target_file"
					sed_success=false
				fi
			elif [[ "$REPLACE_MODE" == "inplace" ]]; then
				# Inplace mode: modify original file
				if cp --preserve=all "$temp_output" "$target_file" 2>/dev/null || cp "$temp_output" "$target_file" 2>/dev/null; then
					if [[ "$PRESERVE_OWNERSHIP" == true ]]; then
						[[ -n "$original_owner" ]] && [[ -n "$original_group" ]] &&
							chown "${original_owner}:${original_group}" "$target_file" 2>/dev/null || true
						[[ -n "$original_perms" ]] && chmod "$original_perms" "$target_file" 2>/dev/null || true
					fi
					log_success "Replaced $replacements_made occurrence(s) in $file"
				else
					log_error "Failed to write changes to file: $file"
					sed_success=false
				fi
			elif [[ "$REPLACE_MODE" == "backup_only" ]]; then
				# Backup only mode: just create backup, don't modify
				log_success "Created backup for file with $replacements_made replacement(s): $file"
			fi

			if [[ "$occurrences_after" -gt 0 ]]; then
				log_warning "$occurrences_after occurrence(s) may not have been replaced in $file"
			fi
		else
			log_verbose "No replacements made in $file (leaving file unchanged)"
			sed_success=false
		fi
	else
		log_error "Failed to process file with sed: $file"
		log_debug "Last sed command attempted:"
		log_debug "  sed \"s${SED_DELIMITER}${search_escaped}${SED_DELIMITER}${replace_escaped}${SED_DELIMITER}g\""

		# Show first few lines of file for debugging
		log_debug "First 3 lines of file:"
		head -3 "$file" 2>/dev/null | while IFS= read -r line; do
			log_debug "  $line"
		done
	fi

	# Clean up temporary files
	rm -f "$temp_file" "$temp_output" 2>/dev/null

	# Restore original modification time if no changes were made in inplace mode
	if [[ "$sed_success" == false ]] && [[ -n "$original_mtime" ]] && [[ "$replacements_made" -eq 0 ]] && [[ "$REPLACE_MODE" == "inplace" ]]; then
		touch -m -d "@$original_mtime" "$file" 2>/dev/null &&
			log_debug "Preserved original modification time for: $file"
	fi

	return 0
}

# ============================================================================
# COMPREHENSIVE SUMMARY
# ============================================================================

show_summary() {
	echo ""
	log_header "=== SEARCH AND REPLACE SUMMARY ==="

	# Get tracked files count from file
	local tracked_files_count=0
	if [[ -n "$BACKUP_DIR" ]] && [[ -d "$BACKUP_DIR" ]]; then
		local modified_list="$BACKUP_DIR/.sr_modified_files"
		if [[ -f "$modified_list" ]]; then
			tracked_files_count=$(wc -l <"$modified_list" 2>/dev/null || echo "0")
		fi
	fi

	echo "Session ID:          $SESSION_ID"
	echo "Files scanned:       $PROCESSED_FILES"
	echo "Files modified:      $MODIFIED_FILES ($tracked_files_count tracked in backup)"
	echo "Total replacements:  $TOTAL_REPLACEMENTS"
	echo "Search pattern:      '$SEARCH_STRING'"
	echo "Replace with:        '$REPLACE_STRING'"
	echo "File pattern:        $FILE_PATTERN"
	echo "Search directory:    $SEARCH_DIR"
	echo "Mode:                $([[ "$RECURSIVE_MODE" == true ]] && echo "Recursive (depth: $MAX_DEPTH)" || echo "Non-recursive")"
	echo "Binary detection:    $BINARY_DETECTION_METHOD"
	echo "Allow binary:        $([[ "$ALLOW_BINARY" == true ]] && echo "Yes (--binary used)" || echo "No (skipped if detected)")"
	echo "Backups:             $([[ "$CREATE_BACKUPS" == true ]] && echo "Enabled" || echo "Disabled")"
	echo "Force backup:        $([[ "$FORCE_BACKUP" == true ]] && echo "Yes" || echo "No")"
	echo "Preserve ownership:  $([[ "$PRESERVE_OWNERSHIP" == true ]] && echo "Yes" || echo "No")"
	echo "Replace mode:        $REPLACE_MODE"
	echo "Verbose mode:        $([[ "$VERBOSE_MODE" == true ]] && echo "Yes" || echo "No")"

	if [[ -n "$OUTPUT_DIR" ]]; then
		echo "Output directory:    $OUTPUT_DIR"
	fi

	if [[ -n "$BACKUP_DIR" ]] && [[ -d "$BACKUP_DIR" ]]; then
		echo "Backup directory:    $BACKUP_DIR"
		echo "Backup metadata:     $([[ -f "$BACKUP_DIR/.sr_session_metadata" ]] && echo "Yes" || echo "No")"
		local filelist_count=0
		if [[ -f "$BACKUP_DIR/.sr_modified_files" ]]; then
			filelist_count=$(wc -l <"$BACKUP_DIR/.sr_modified_files" 2>/dev/null || echo "0")
		fi
		echo "Files tracked:       $filelist_count"
		[[ -n "$FIRST_FILE_OWNER" ]] &&
			echo "Backup owner:        $FIRST_FILE_OWNER:$FIRST_FILE_GROUP"
	fi

	echo "Excluded patterns:   $EXCLUDE_PATTERNS"
	echo "Excluded dirs:       $EXCLUDE_DIRS"

	# Show modified files if verbose
	if [[ "$VERBOSE_MODE" == true ]] && [[ -n "$BACKUP_DIR" ]] && [[ -d "$BACKUP_DIR" ]]; then
		local modified_list="$BACKUP_DIR/.sr_modified_files"
		if [[ -f "$modified_list" ]] && [[ $tracked_files_count -gt 0 ]]; then
			echo ""
			log_info "Modified files in this session:"
			local display_count=0
			while IFS= read -r line; do
				[[ $display_count -ge 10 ]] && break
				echo "  - $line"
				restored_count=$((restored_count + 1))
			done <"$modified_list"
			[[ $tracked_files_count -gt 10 ]] &&
				echo "  ... and $((tracked_files_count - 10)) more"
		fi
	fi

	if [[ "$DRY_RUN" == true ]]; then
		echo ""
		log_warning "NOTE: Dry-run mode was active."
		echo "      No files were actually modified."
	fi

	if [[ "$DEBUG_MODE" == true ]]; then
		echo ""
		log_warning "NOTE: Debug mode was active."
	fi

	if [[ "$FORCE_BACKUP" == true ]] && [[ "$CREATE_BACKUPS" == true ]]; then
		echo ""
		log_warning "NOTE: Backups were forced with --force-backup option."
	fi

	if [[ "$REPLACE_MODE" == "copy" ]] && [[ -n "$OUTPUT_DIR" ]]; then
		echo ""
		log_warning "NOTE: Copy mode active. Original files unchanged."
		echo "      Modified files saved to: $OUTPUT_DIR"
	fi

	if [[ "$REPLACE_MODE" == "backup_only" ]]; then
		echo ""
		log_warning "NOTE: Backup-only mode active. Original files unchanged."
	fi

	# Rollback information
	if [[ "$CREATE_BACKUPS" == true ]] && [[ -n "$BACKUP_DIR" ]]; then
		echo ""
		log_info "Rollback commands:"
		echo "  Restore this session:  $0 --rollback=$BACKUP_DIR"
		echo "  Restore latest:        $0 --rollback"
		echo "  List all backups:      $0 --rollback-list"
	fi

	if [[ "$MODIFIED_FILES" -eq 0 ]] && [[ "$DRY_RUN" != true ]]; then
		log_warning "No replacements were made. Search pattern not found in any files."
		exit 3
	fi

	[[ "$PROCESSED_FILES" -gt 0 ]] && log_success "Operation completed successfully"
}

# ============================================================================
# MAIN EXECUTION FUNCTION
# ============================================================================

main() {
	# Force initialize arrays for safety
	declare -g SESSION_MODIFIED_FILES=()
	declare -g SESSION_INITIAL_ARGS=()

	log_debug "Main function started"
	log_debug "Arguments: $@"
	log_debug "SESSION_MODIFIED_FILES initialized: ${#SESSION_MODIFIED_FILES[@]} items"

	local start_time end_time duration

	start_time=$(date +%s.%N)

	validate_environment
	parse_arguments "$@"

	log_debug "After parse_arguments: SESSION_MODIFIED_FILES has ${#SESSION_MODIFIED_FILES[@]} items"

	# Mode warnings
	[[ "$DRY_RUN" == true ]] && log_warning "DRY RUN MODE - No files will be modified"
	[[ "$DEBUG_MODE" == true ]] && log_warning "DEBUG MODE - Detailed logging enabled"
	[[ "$VERBOSE_MODE" == true ]] && log_warning "VERBOSE MODE - Detailed output enabled"
	[[ "$CREATE_BACKUPS" == false ]] && log_warning "BACKUP CREATION DISABLED - No backup files will be created"
	[[ "$FORCE_BACKUP" == true ]] && log_warning "FORCE BACKUP ENABLED - Overriding backup settings"
	[[ "$REPLACE_MODE" != "inplace" ]] && log_warning "REPLACE MODE: $REPLACE_MODE"
	[[ "$ALLOW_BINARY" == true ]] && log_warning "BINARY PROCESSING ALLOWED - Binary files will be modified"

	# Binary detection method info
	log_info "Binary detection method: $BINARY_DETECTION_METHOD"
	if [[ "$BINARY_DETECTION_METHOD" == "file_only" ]] && ! command -v file >/dev/null 2>&1; then
		log_warning "file utility not found. Binary detection may fail."
	fi

	process_files

	log_debug "After process_files: SESSION_MODIFIED_FILES has ${#SESSION_MODIFIED_FILES[@]} items"

	# Final metadata save after processing all files
	if [[ "$CREATE_BACKUPS" == true ]] && [[ -n "$BACKUP_DIR" ]] && [[ -d "$BACKUP_DIR" ]]; then
		finalize_session_metadata
	fi

	end_time=$(date +%s.%N)
	duration=$(echo "$end_time - $start_time" | bc 2>/dev/null || echo "0" | awk '{printf "%.2f", $0}')

	show_summary
	log_verbose "Execution time: ${duration} seconds"

	# Cleanup old backups
	if [[ "$CREATE_BACKUPS" == true ]] && [[ "$MAX_BACKUPS" -gt 0 ]] && [[ "$DRY_RUN" != true ]]; then
		cleanup_old_backups
	fi

	exit 0
}

# ============================================================================
# ERROR HANDLING AND ENTRY POINT
# ============================================================================

trap 'log_error "Script interrupted by user"; exit 1' INT TERM
trap 'log_error "Error occurred at line $LINENO"; exit 4' ERR

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
	[[ $# -eq 0 ]] && {
		show_help
		exit 0
	}

	# Quick help/version checks without full parsing
	for arg in "$@"; do
		if [[ "$arg" == "-h" || "$arg" == "--help" ]]; then
			show_help
			exit 0
		fi
		if [[ "$arg" == "-V" || "$arg" == "--version" ]]; then
			show_version
			exit 0
		fi
	done

	main "$@"
fi
