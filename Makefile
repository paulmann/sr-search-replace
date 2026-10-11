# =============================================================================
# sr-search-replace — developer Makefile
#
# Everything a contributor or a release manager needs, with no hidden steps.
# `make help` lists the targets; `make check` is the gate CI runs.
#
# The runtime has zero third-party dependencies.  The dev extras (pytest, ruff,
# mypy) are optional: every target degrades gracefully when they are missing.
# =============================================================================

PYTHON      ?= python3
PIP         ?= $(PYTHON) -m pip
PYTEST      ?= $(PYTHON) -m pytest
RUFF        ?= $(PYTHON) -m ruff
MYPY        ?= $(PYTHON) -m mypy
PREFIX      ?= /usr/local
DESTDIR     ?=
BINDIR      := $(DESTDIR)$(PREFIX)/bin
MANDIR      := $(DESTDIR)$(PREFIX)/share/man/man1
VERSION     := $(shell $(PYTHON) -c "import re,pathlib;print(re.search(r'version\s*=\s*\"([^\"]+)\"',pathlib.Path('pyproject.toml').read_text()).group(1))")

BUILD_DIR   := build
ZIPAPP      := $(BUILD_DIR)/sr.pyz
CHECKSUM    := $(ZIPAPP).sha256
MANPAGE     := docs/man/sr.1
COMPLETIONS := completions/sr.bash completions/sr.zsh completions/sr.fish

.DEFAULT_GOAL := help
.PHONY: help install install-dev uninstall test test-quick coverage lint fmt typecheck \
        dist dist-check man man-check docs docs-check completions npm selftest check clean \
        sync-version release-check doctor run

# -----------------------------------------------------------------------------
help: ## Show this help
	@printf 'sr %s — make targets\n\n' "$(VERSION)"
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@printf '\nQuality gate: \033[1mmake check\033[0m   ·   Try it now: \033[1mmake run ARGS="find hello"\033[0m\n'

# -----------------------------------------------------------------------------
# Development
# -----------------------------------------------------------------------------

install-dev: ## Install dev extras (pytest, ruff, mypy) into the current environment
	$(PIP) install -e ".[dev]"
	@printf '\nRun \033[1mmake test\033[0m to verify.\n'

test: ## Run the full test suite
	$(PYTEST)

test-quick: ## Run the suite without coverage, stopping at the first failure
	$(PYTEST) -x -q -p no:cacheprovider

coverage: ## Run the suite with a coverage report
	$(PYTEST) --cov=sr --cov-report=term-missing --cov-report=xml:$(BUILD_DIR)/coverage.xml
	@printf 'written to $(BUILD_DIR)/coverage.xml\n'

lint: ## Lint with ruff (falls back to a compileall check when ruff is absent)
	@if $(RUFF) --version >/dev/null 2>&1; then \
		$(RUFF) check sr tests tools scripts; \
		$(RUFF) format --check sr tests tools scripts || true; \
	else \
		printf 'ruff not installed — falling back to a syntax check\n'; \
		$(PYTHON) -m compileall -q sr tools scripts tests; \
	fi

fmt: ## Format the code with ruff
	$(RUFF) format sr tests tools scripts && $(RUFF) check --fix sr tests tools scripts

typecheck: ## Type-check with mypy (skipped when mypy is absent)
	@if $(MYPY) --version >/dev/null 2>&1; then $(MYPY); else printf 'mypy not installed — skipped\n'; fi

selftest: ## Run the built-in end-to-end smoke test
	$(PYTHON) -m sr selftest

doctor: ## Diagnose this checkout
	$(PYTHON) -m sr doctor

run: ## Run sr from the checkout: make run ARGS="find hello"
	$(PYTHON) -m sr $(ARGS)

# -----------------------------------------------------------------------------
# Build artefacts
# -----------------------------------------------------------------------------

dist: $(ZIPAPP) ## Build the single-file release asset (build/sr.pyz + .sha256)

$(ZIPAPP): $(shell find sr -name '*.py' 2>/dev/null) tools/build_zipapp.py
	$(PYTHON) tools/build_zipapp.py --output $(ZIPAPP)

dist-check: dist ## Build the zipapp and verify it end to end
	$(PYTHON) tools/build_zipapp.py --output $(ZIPAPP) --check

man: $(MANPAGE) ## Regenerate the man page from the live option schema

$(MANPAGE): sr/options.py sr/help_system.py tools/build_manpage.py
	$(PYTHON) tools/build_manpage.py --output $(MANPAGE)

man-check: ## Fail when the committed man page is out of date
	$(PYTHON) tools/build_manpage.py --output $(MANPAGE) --check

docs: docs/OPTIONS.md docs/EXIT_CODES.md ## Regenerate the derived documentation

docs/OPTIONS.md README.md: sr/options.py scripts/gen_options_table.py
	$(PYTHON) scripts/gen_options_table.py

docs/EXIT_CODES.md: sr/exit_codes.py scripts/gen_exit_codes.py
	$(PYTHON) scripts/gen_exit_codes.py

docs-check: ## Fail when a generated document is out of date
	$(PYTHON) scripts/gen_options_table.py --check
	$(PYTHON) scripts/gen_exit_codes.py --check

completions: $(COMPLETIONS) ## Regenerate the shell completion scripts

completions/sr.bash: sr/options.py sr/cli.py
	$(PYTHON) -m sr completions bash > $@
completions/sr.zsh: sr/options.py sr/cli.py
	$(PYTHON) -m sr completions zsh > $@
completions/sr.fish: sr/options.py sr/cli.py
	$(PYTHON) -m sr completions fish > $@

npm: dist ## Build the npm package payload (npm/app/sr.pyz)
	cd npm && node scripts/pack_app.js
	@printf 'npm package ready in npm/ — publish with: cd npm && npm publish\n'

sync-version: ## Propagate the pyproject version everywhere (or: make sync-version V=7.1.0)
ifdef V
	$(PYTHON) scripts/sync_version.py $(V)
else
	$(PYTHON) scripts/sync_version.py --check
endif

# -----------------------------------------------------------------------------
# Installation
# -----------------------------------------------------------------------------

install: dist man completions ## Install sr, its man page and completions into PREFIX
	install -d "$(BINDIR)"
	install -m 0755 $(ZIPAPP) "$(BINDIR)/sr"
	install -d "$(MANDIR)"
	install -m 0644 $(MANPAGE) "$(MANDIR)/sr.1"
	@printf '\033[0;32minstalled\033[0m %s/sr (version %s)\n' "$(BINDIR)" "$(VERSION)"
	@printf 'man page  → %s/sr.1\n' "$(MANDIR)"
	@printf 'completions are not installed automatically; run:\n'
	@printf '  sr completions --install\n'

uninstall: ## Remove sr from PREFIX
	rm -f "$(BINDIR)/sr" "$(MANDIR)/sr.1"
	@printf 'removed %s/sr\n' "$(BINDIR)"

# -----------------------------------------------------------------------------
# Gates
# -----------------------------------------------------------------------------

check: lint typecheck test dist-check man-check docs-check sync-version selftest ## The full quality gate (what CI runs)
	@printf '\n\033[0;32mall checks passed\033[0m — sr %s is ready to release\n' "$(VERSION)"

release-check: check ## Everything in `check`, plus release-specific verification
	@test -f CHANGELOG.md || { printf 'CHANGELOG.md is missing\n'; exit 1; }
	@grep -q "$(VERSION)" CHANGELOG.md || { printf 'CHANGELOG.md has no entry for %s\n' "$(VERSION)"; exit 1; }
	@test -f LICENSE || { printf 'LICENSE is missing\n'; exit 1; }
	@printf '\033[0;32mrelease %s is ready\033[0m — tag it with: git tag v%s && git push --tags\n' "$(VERSION)" "$(VERSION)"

clean: ## Remove build artefacts and caches
	rm -rf $(BUILD_DIR) .pytest_cache .mypy_cache .ruff_cache
	rm -rf npm/app
	find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true
	@printf 'clean\n'
