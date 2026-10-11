"""Tests for file discovery: globs, pruning, ``.gitignore`` and filters."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from sr.discovery import (
    DEFAULT_EXCLUDE_DIRS,
    DiscoveryOptions,
    GitignoreRule,
    discover,
    parse_age_option,
    parse_size_option,
)


def names(result, root: Path) -> set[str]:
    return {str(path.relative_to(root)) for path in result.files}


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "node_modules" / "dep").mkdir(parents=True)
    (root / ".hidden").mkdir()
    (root / "src" / "a.py").write_text("a\n")
    (root / "src" / "b.py").write_text("b\n")
    (root / "src" / "pkg" / "c.py").write_text("c\n")
    (root / "src" / "notes.txt").write_text("n\n")
    (root / "docs" / "d.md").write_text("d\n")
    (root / "node_modules" / "dep" / "e.js").write_text("e\n")
    (root / ".hidden" / "f.py").write_text("f\n")
    (root / ".dotfile.py").write_text("g\n")
    (root / "backup.bak").write_text("h\n")
    (root / "binary.bin").write_bytes(b"\x00\x01\x02\x03")
    return root


def options(root: Path, **kwargs) -> DiscoveryOptions:
    kwargs.setdefault("roots", [root])
    kwargs.setdefault("patterns", ["*"])
    opts = DiscoveryOptions(**kwargs)
    opts.normalise()
    return opts


class TestPruning:
    def test_node_modules_is_pruned(self, tree: Path):
        found = names(discover(options(tree)), tree)
        assert not any("node_modules" in item for item in found)

    def test_git_directory_is_pruned(self, tree: Path):
        (tree / ".git").mkdir()
        (tree / ".git" / "config").write_text("x\n")
        found = names(discover(options(tree)), tree)
        assert not any(".git" in item for item in found)

    def test_session_store_is_pruned(self, tree: Path):
        (tree / ".sr" / "sessions").mkdir(parents=True)
        (tree / ".sr" / "sessions" / "x.txt").write_text("x\n")
        found = names(discover(options(tree, hidden=True)), tree)
        assert not any(".sr" in item for item in found)

    def test_no_default_excludes_lets_everything_in(self, tree: Path):
        opts = options(tree, hidden=True)
        opts.exclude_dirs = []
        opts.exclude_files = []
        found = names(discover(opts), tree)
        assert any("node_modules" in item for item in found)
        assert "backup.bak" in found

    def test_default_exclude_files(self, tree: Path):
        found = names(discover(options(tree)), tree)
        assert "backup.bak" not in found

    def test_custom_exclude_dir(self, tree: Path):
        found = names(discover(options(tree, exclude_dirs=[*DEFAULT_EXCLUDE_DIRS, "docs"])), tree)
        assert not any(item.startswith("docs/") for item in found)


class TestPatterns:
    def test_name_glob(self, tree: Path):
        found = names(discover(options(tree, patterns=["*.py"])), tree)
        assert found == {"src/a.py", "src/b.py", "src/pkg/c.py"}

    def test_several_patterns_are_ored(self, tree: Path):
        found = names(discover(options(tree, patterns=["*.py", "*.md"])), tree)
        assert "docs/d.md" in found and "src/a.py" in found

    def test_path_pattern_with_slash(self, tree: Path):
        found = names(discover(options(tree, patterns=["src/*.py"])), tree)
        assert found == {"src/a.py", "src/b.py"}

    def test_globstar_pattern(self, tree: Path):
        found = names(discover(options(tree, patterns=["**/*.py"])), tree)
        assert "src/pkg/c.py" in found

    def test_include_filter(self, tree: Path):
        found = names(discover(options(tree, patterns=["*.py"], include=["src/**"])), tree)
        assert found == {"src/a.py", "src/b.py", "src/pkg/c.py"}

    def test_exclude_beats_include(self, tree: Path):
        opts = options(tree, patterns=["*.py"], include=["src/**"], exclude=["src/pkg/*"])
        found = names(discover(opts), tree)
        assert "src/pkg/c.py" not in found

    def test_no_pattern_matches_everything(self, tree: Path):
        found = names(discover(options(tree, patterns=[])), tree)
        assert "src/notes.txt" in found

    def test_explicit_file_bypasses_patterns(self, tree: Path):
        # roots=[] models "the user named files and asked for no tree scan".
        opts = DiscoveryOptions(roots=[], patterns=[], explicit_files=[tree / "backup.bak"])
        opts.normalise()
        found = names(discover(opts), tree)
        assert found == {"backup.bak"}

    def test_explicit_file_ignores_the_default_exclude_list(self, tree: Path):
        """Naming a `.bak` file explicitly means you want it processed."""
        opts = DiscoveryOptions(roots=[], explicit_files=[tree / "backup.bak"])
        opts.normalise()
        assert names(discover(opts), tree) == {"backup.bak"}

    def test_explicit_files_plus_target_still_walks(self, tree: Path):
        opts = options(tree, patterns=["*.md"], explicit_files=[tree / "backup.bak"])
        found = names(discover(opts), tree)
        assert found == {"backup.bak", "docs/d.md"}

    def test_missing_explicit_file_is_reported(self, tree: Path):
        opts = DiscoveryOptions(roots=[], explicit_files=[tree / "nope.txt"])
        opts.normalise()
        result = discover(opts)
        assert result.files == []
        assert any("does not exist" in item.reason for item in result.skipped)


class TestRecursion:
    def test_recursive_by_default(self, tree: Path):
        found = names(discover(options(tree, patterns=["*.py"])), tree)
        assert "src/pkg/c.py" in found

    def test_non_recursive(self, tree: Path):
        found = names(discover(options(tree, patterns=["*.py"], recursive=False)), tree)
        assert found == set()

    def test_max_depth_one_is_the_root_only(self, tree: Path):
        """`--max-depth 1` matches `find <root> -maxdepth 1`: no descent."""
        found = names(discover(options(tree, patterns=["*.py"], max_depth=1)), tree)
        assert found == set()

    def test_max_depth_two_reaches_the_first_level(self, tree: Path):
        found = names(discover(options(tree, patterns=["*.py"], max_depth=2)), tree)
        assert found == {"src/a.py", "src/b.py"}

    def test_max_depth_three_reaches_the_second_level(self, tree: Path):
        found = names(discover(options(tree, patterns=["*.py"], max_depth=3)), tree)
        assert "src/pkg/c.py" in found


class TestHiddenAndSymlinks:
    def test_hidden_files_excluded_by_default(self, tree: Path):
        found = names(discover(options(tree, patterns=["*.py"])), tree)
        assert ".dotfile.py" not in found

    def test_hidden_flag_includes_them(self, tree: Path):
        found = names(discover(options(tree, patterns=["*.py"], hidden=True)), tree)
        assert ".dotfile.py" in found

    @pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
    def test_symlinks_skipped_by_default(self, tree: Path):
        os.symlink(tree / "src" / "a.py", tree / "link.py")
        found = names(discover(options(tree, patterns=["*.py"])), tree)
        assert "link.py" not in found
        result = discover(options(tree, patterns=["*.py"]))
        assert any("symlink" in item.reason for item in result.skipped)

    @pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
    def test_symlinks_flag_follows_them(self, tree: Path):
        os.symlink(tree / "src" / "a.py", tree / "link.py")
        found = names(discover(options(tree, patterns=["*.py"], symlinks=True)), tree)
        assert "link.py" in found


class TestFilters:
    def test_binary_files_filtered(self, tree: Path):
        found = names(discover(options(tree, patterns=["*.bin"])), tree)
        assert found == set()

    def test_binary_allowed(self, tree: Path):
        found = names(discover(options(tree, patterns=["*.bin"], text_only=False)), tree)
        assert found == {"binary.bin"}

    def test_max_size(self, tree: Path):
        (tree / "big.txt").write_text("x" * 5000)
        found = names(discover(options(tree, patterns=["*.txt"], max_size=100)), tree)
        assert "big.txt" not in found and "src/notes.txt" in found

    def test_min_size(self, tree: Path):
        found = names(discover(options(tree, patterns=["*.py"], min_size=10)), tree)
        assert found == set()

    def test_newer_than(self, tree: Path):
        old = tree / "docs" / "d.md"
        past = time.time() - 86400 * 10
        os.utime(old, (past, past))
        found = names(discover(options(tree, patterns=["*.md"], newer_than=time.time() - 3600)), tree)
        assert found == set()

    def test_older_than(self, tree: Path):
        old = tree / "docs" / "d.md"
        past = time.time() - 86400 * 10
        os.utime(old, (past, past))
        found = names(discover(options(tree, patterns=["*.md"], older_than=time.time() - 3600)), tree)
        assert found == {"docs/d.md"}

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root bypasses directory permissions",
    )
    def test_unreadable_directory_is_reported(self, tree: Path):
        locked = tree / "locked"
        locked.mkdir()
        (locked / "x.txt").write_text("x\n")
        locked.chmod(0o000)
        try:
            result = discover(options(tree, patterns=["*.txt"]))
        finally:
            locked.chmod(0o755)
        assert any("cannot read directory" in item.reason for item in result.skipped)


class TestGitignore:
    def test_disabled_by_default(self, tree: Path):
        (tree / ".gitignore").write_text("docs/\n")
        found = names(discover(options(tree, patterns=["*.md"])), tree)
        assert "docs/d.md" in found

    def test_directory_rule(self, tree: Path):
        (tree / ".gitignore").write_text("docs/\n")
        found = names(discover(options(tree, patterns=["*.md"], gitignore=True)), tree)
        assert "docs/d.md" not in found

    def test_extension_rule(self, tree: Path):
        (tree / ".gitignore").write_text("*.txt\n")
        found = names(discover(options(tree, patterns=["*"], gitignore=True)), tree)
        assert "src/notes.txt" not in found

    def test_negation_rule(self, tree: Path):
        (tree / ".gitignore").write_text("*.py\n!src/b.py\n")
        found = names(discover(options(tree, patterns=["*.py"], gitignore=True)), tree)
        assert found == {"src/b.py"}

    def test_anchored_rule(self, tree: Path):
        (tree / ".gitignore").write_text("/src/a.py\n")
        found = names(discover(options(tree, patterns=["*.py"], gitignore=True)), tree)
        assert "src/a.py" not in found and "src/b.py" in found

    def test_nested_gitignore(self, tree: Path):
        (tree / "src" / ".gitignore").write_text("pkg/\n")
        found = names(discover(options(tree, patterns=["*.py"], gitignore=True)), tree)
        assert "src/pkg/c.py" not in found and "src/a.py" in found

    def test_comment_and_blank_lines(self):
        assert GitignoreRule.parse("# comment") is None
        assert GitignoreRule.parse("   ") is None

    def test_stop_at_repository_boundary(self, tree: Path):
        (tree.parent / ".gitignore").write_text("tree/\n")
        (tree / ".git").mkdir()
        found = names(discover(options(tree, patterns=["*.py"], gitignore=True)), tree)
        assert "src/a.py" in found


class TestOptionHelpers:
    @pytest.mark.parametrize(
        "text,expected",
        [("10", 10 * 1024 * 1024), ("1KB", 1024), ("2MB", 2 * 1024 * 1024), ("1.5GiB", int(1.5 * 1024**3))],
    )
    def test_parse_size_option(self, text, expected):
        assert parse_size_option(text) == expected

    def test_parse_size_option_none(self):
        assert parse_size_option(None) is None
        assert parse_size_option("") is None

    def test_parse_age_option(self):
        now = 1_800_000_000.0
        assert parse_age_option("7d", now=now) == pytest.approx(now - 7 * 86400)
        assert parse_age_option(None) is None

    def test_normalise_adds_vcs_dirs(self):
        opts = DiscoveryOptions(roots=[Path()], no_vcs=True)
        opts.normalise()
        assert ".git" in opts.exclude_dirs and ".sr" in opts.exclude_dirs


class TestResultMetadata:
    def test_counts_and_timing(self, tree: Path):
        result = discover(options(tree, patterns=["*.py"]))
        assert result.count == 3
        assert result.files_seen >= 3
        assert result.directories_scanned >= 4
        assert result.elapsed >= 0

    def test_deterministic_order(self, tree: Path):
        first = [str(item) for item in discover(options(tree, patterns=["*.py"])).files]
        second = [str(item) for item in discover(options(tree, patterns=["*.py"])).files]
        assert first == second

    def test_deduplication(self, tree: Path):
        opts = options(tree, patterns=["*.py"], explicit_files=[tree / "src" / "a.py"])
        result = discover(opts)
        assert len({str(item) for item in result.files}) == len(result.files)

    def test_missing_root_is_reported(self, tmp_path: Path):
        result = discover(options(tmp_path / "nope", patterns=["*"]))
        assert result.files == []
        assert any("does not exist" in item.reason for item in result.skipped)
