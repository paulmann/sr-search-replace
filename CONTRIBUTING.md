# Contributing to sr-search-replace

Thank you for your interest in contributing to **sr-search-replace**! This guide will help you understand our development process and how to make meaningful contributions to the project.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How to Contribute](#how-to-contribute)
- [Development Setup](#development-setup)
- [Coding Standards](#coding-standards)
- [Testing Guidelines](#testing-guidelines)
- [Commit Message Conventions](#commit-message-conventions)
- [Pull Request Process](#pull-request-process)
- [Reporting Bugs](#reporting-bugs)
- [Suggesting Enhancements](#suggesting-enhancements)
- [Documentation](#documentation)

---

## Code of Conduct

This project adheres to a Code of Conduct. By participating, you agree to:

- Be respectful and inclusive of all contributors
- Provide constructive feedback and criticism
- Focus on what is best for the community
- Show empathy towards other community members
- Report unacceptable behavior to the maintainers

---

## How to Contribute

There are many ways to contribute to sr-search-replace:

### 1. **Report Bugs**
If you find a bug, please report it by creating an issue. Include:
- Clear description of the bug
- Steps to reproduce
- Expected behavior
- Actual behavior
- Your environment (OS, shell version, etc.)

### 2. **Suggest Enhancements**
We welcome feature suggestions! Please include:
- Clear use case and motivation
- Proposed solution (if applicable)
- Alternative approaches
- Impact assessment

### 3. **Contribute Code**
Fix bugs, implement features, or improve performance. See the [Development Setup](#development-setup) section.

### 4. **Improve Documentation**
Help improve README, wiki, inline comments, or examples.

### 5. **Test**
Test the tool on different systems and configurations, and report findings.

---

## Development Setup

### Prerequisites

Before setting up the development environment, ensure you have:

- **Git** (v2.20+)
- **Bash** (v4.0+)
- **Unix-like system** (Linux, macOS, or WSL on Windows)
- Text editor or IDE of your choice (VS Code, Vim, etc.)

### Setup Steps

```bash
# 1. Fork the repository on GitHub
# 2. Clone your fork
git clone https://github.com/YOUR_USERNAME/sr-search-replace.git
cd sr-search-replace

# 3. Add upstream remote
git remote add upstream https://github.com/paulmann/sr-search-replace.git

# 4. Create a feature branch
git checkout -b feature/your-feature-name

# 5. Make your changes
# ... edit files ...

# 6. Test your changes
./sr.sh --help  # Verify basic functionality

# 7. Run the test suite
bash tests/run_tests.sh
```

### Project Structure

```
sr-search-replace/
├── sr.sh                    # Main script
├── tests/                   # Test suite
│   ├── run_tests.sh        # Test runner
│   ├── unit/               # Unit tests
│   └── integration/         # Integration tests
├── docs/                    # Documentation
├── CHANGELOG.md             # Version history
├── README.md                # Project overview
├── LICENSE                  # MIT License
└── CONTRIBUTING.md          # This file
```

---

## Coding Standards

### Bash Style Guide

We follow these conventions for Bash code:

#### 1. **Naming Conventions**

```bash
# Variables: UPPER_CASE for constants, lower_case for local variables
readonly DEFAULT_TIMEOUT=30
local current_time=$(date +%s)

# Functions: lower_case_with_underscores
function process_file() {
  # ...
}

# Constants: ALL_CAPS
readonly MAX_RETRIES=3
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
```

#### 2. **Code Structure**

```bash
#!/bin/bash

# Strict mode
set -euo pipefail
IFS=$'\n\t'

# Configuration section
readonly SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"

# Function definitions
function main() {
  # Main logic
  return 0
}

# Error handling
trap 'handle_error "$?" "$LINENO"' ERR

# Execution
main "$@"
exit $?
```

#### 3. **Error Handling**

```bash
# Use proper error handling
if ! command_that_might_fail; then
  echo "Error: Command failed" >&2
  return 1
fi

# Quote variables
echo "Value: ${variable}"
echo "${array[@]}"

# Avoid dangerous patterns
# BAD:  eval command
# GOOD: "$@" or explicit variable passing
```

#### 4. **Comments and Documentation**

```bash
# Function documentation format
##
# Brief description of what the function does.
#
# Arguments:
#   $1 - Description of first argument
#   $2 - Description of second argument (optional)
#
# Returns:
#   0 on success, non-zero on failure
#
# Example:
#   result=$(my_function "arg1" "arg2")
##
function my_function() {
  # Implementation
}
```

#### 5. **Indentation and Formatting**

- Use 2 spaces for indentation
- Keep lines under 100 characters when possible
- Use meaningful variable names
- Add blank lines between logical sections

---

## Testing Guidelines

### Creating Tests

1. **Unit Tests**: Test individual functions in isolation
2. **Integration Tests**: Test complete workflows
3. **Edge Cases**: Test boundary conditions and error scenarios

### Test File Structure

```bash
#!/bin/bash
# tests/unit/test_function_name.sh

source "${SCRIPT_DIR}/../sr.sh"

test_function_returns_zero_on_success() {
  local result
  result=$(my_function "valid_input")
  assert_equals "$?" "0"
}

test_function_handles_empty_input() {
  local result
  result=$(my_function "")
  assert_equals "$?" "1"
}

# Run tests
run_all_tests
```

### Running Tests

```bash
# Run all tests
bash tests/run_tests.sh

# Run specific test file
bash tests/unit/test_specific.sh

# Run tests with verbose output
bash tests/run_tests.sh --verbose
```

### Test Coverage

Aim for at least 80% code coverage:

```bash
# Check coverage (if coverage tool is available)
bash tests/run_tests.sh --coverage
```

---

## Commit Message Conventions

We follow [Conventional Commits](https://www.conventionalcommits.org/) specification:

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Types

- **feat**: New feature
- **fix**: Bug fix
- **docs**: Documentation changes
- **test**: Adding or updating tests
- **refactor**: Code refactoring
- **perf**: Performance improvements
- **chore**: Maintenance tasks
- **ci**: CI/CD configuration changes

### Examples

```bash
feat(regex): add support for lookahead assertions

Add support for positive and negative lookahead assertions
in regex patterns. This allows more complex pattern matching
scenarios while maintaining backward compatibility.

Closes #123

feat(rollback): implement session-based rollback system
fix(binary-detection): improve detection of UTF-8 with BOM
docs(readme): update installation instructions
test(regex): add test cases for edge cases
```

---

## Pull Request Process

### Before You Start

1. Check if your issue is already being worked on
2. Discuss major changes in an issue first
3. Keep PRs focused on a single concern

### Creating a Pull Request

```bash
# 1. Ensure your fork is up to date
git fetch upstream
git rebase upstream/main

# 2. Push your changes
git push origin feature/your-feature-name

# 3. Open PR on GitHub
#    - Clear title (same as commit message type)
#    - Detailed description of changes
#    - Reference related issues
#    - Checklist of testing performed
```

### PR Description Template

```markdown
## Description
Brief description of what this PR does.

## Related Issue
Closes #123

## Changes
- Change 1
- Change 2
- Change 3

## Testing
- [ ] Unit tests added/updated
- [ ] Integration tests passed
- [ ] Manual testing completed
- [ ] Tested on Linux
- [ ] Tested on macOS

## Checklist
- [ ] Code follows style guidelines
- [ ] Documentation updated
- [ ] No new warnings generated
- [ ] Tests pass locally
```

### Review Process

1. **Code Review**: Maintainers will review your code
2. **Tests**: All CI tests must pass
3. **Feedback**: Address any requested changes
4. **Merge**: Once approved, your PR will be merged

### Review Checklist

Reviewers will check:
- Code quality and style compliance
- Test coverage adequacy
- Documentation completeness
- Performance impact
- Security implications
- Backward compatibility

---

## Reporting Bugs

### Bug Report Checklist

✓ **Before submitting:**

- [ ] Check if the bug has been reported already
- [ ] Test with the latest version
- [ ] Isolate the issue to the minimum reproduction case
- [ ] Try with different inputs/configurations

### Bug Report Template

```markdown
## Description
Clear description of the bug.

## System Information
- OS: [e.g., Ubuntu 20.04]
- Shell: [e.g., Bash 5.1.4]
- sr-search-replace version: [e.g., 6.1.0]

## Steps to Reproduce
1. First step
2. Second step
3. ...

## Expected Behavior
What should happen.

## Actual Behavior  
What actually happens.

## Screenshots/Output
```
$ command output here
```

## Additional Context
Any other context about the problem.
```

---

## Suggesting Enhancements

### Feature Request Template

```markdown
## Description
Clear description of the feature.

## Motivation
Why this feature would be useful.

## Proposed Solution
How the feature might be implemented.

## Alternative Solutions
Other ways to solve the problem.

## Use Case Example
```
$ sr-search-replace <example command>
```

## Implementation Notes
Any technical considerations.
```

---

## Documentation

### Updating Docs

When submitting a PR that changes functionality:

1. **Update README.md** if adding new features
2. **Update CHANGELOG.md** with your changes
3. **Update inline comments** in code
4. **Update wiki** for significant changes
5. **Update examples** if behavior changes

### Documentation Standards

- Use clear, concise language
- Include code examples where applicable
- Keep formatting consistent with existing docs
- Link to related documentation
- Update table of contents if adding sections

### Example Documentation

```markdown
## Feature Name

Brief description.

### Usage

```bash
sr-search-replace [options] pattern replacement [files]
```

### Examples

```bash
# Basic example
sr-search-replace "old" "new" file.txt

# Advanced example
sr-search-replace -r "pattern\\d+" "replacement" --directory ./src
```

### Options

- `--option1`: Description
- `--option2`: Description
```

---

## Questions?

Don't hesitate to ask for help:

- **GitHub Issues**: For bugs and features
- **GitHub Discussions**: For questions and ideas
- **Email**: sr@devnekin.com for general inquiries
- **Wiki**: Check the [Contributing Guide](https://github.com/paulmann/sr-search-replace/wiki/Contributing-Guide) for more details

---

## Recognition

All contributors will be recognized in:
- The project README
- The CHANGELOG
- Our Hall of Fame in the Wiki

Thank you for making sr-search-replace better! 🎉
