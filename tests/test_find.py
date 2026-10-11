"""Tests for the read-only search renderer (:mod:`sr.find`)."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import Captured, make_options

from sr.console import Verbosity
from sr.find import FindOptions, run_find


@pytest.fixture
def search_tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("import old_api\nold_api.run()\n", encoding="utf-8")
    (root / "src" / "b.py").write_text("no match\n", encoding="utf-8")
    (root / "README.md").write_text("# old_api\n\nSee old_api docs.\n", encoding="utf-8")
    (root / "data.bin").write_bytes(b"\x00old_api\x01")
    return root


def find(root: Path, search: str, *, console=None, patterns=None, **spec_kwargs):
    options = make_options(root, search, search, patterns=patterns or ["*"], console=console)
    options.apply_changes = False
    return FindOptions(replace_options=options), options


def run(root: Path, search: str, view: Captured | None = None, patterns=None, **find_kwargs):
    view = view or Captured()
    find_options, _options = find(root, search, console=view.console, patterns=patterns)
    for key, value in find_kwargs.items():
        setattr(find_options, key, value)
    return run_find(find_options, view.console), view


class TestMatches:
    def test_finds_every_occurrence(self, search_tree: Path):
        result, _view = run(search_tree, "old_api")
        assert result.matches == 4
        assert result.matched_files == 2

    def test_coordinates_are_one_based(self, search_tree: Path):
        result, _view = run(search_tree, "old_api")
        first = next(item for item in result.files if item.rel_path.endswith("a.py"))
        assert [(match.line, match.column) for match in first.matches] == [(1, 8), (2, 1)]

    def test_binary_files_are_skipped(self, search_tree: Path):
        result, _view = run(search_tree, "old_api")
        assert all("data.bin" not in item.rel_path for item in result.files if item.count)

    def test_no_match(self, search_tree: Path):
        result, _view = run(search_tree, "not-present")
        assert result.matches == 0
        assert result.matched_files == 0

    def test_scanned_counts_candidates(self, search_tree: Path):
        result, _view = run(search_tree, "old_api", patterns=["*.py"])
        assert result.scanned == 2


class TestRendering:
    def test_default_output_has_path_line_column(self, search_tree: Path):
        _result, view = run(search_tree, "old_api")
        assert any("src/a.py:1:8:" in line for line in view.lines), view.lines

    def test_list_files_mode(self, search_tree: Path):
        _result, view = run(search_tree, "old_api", list_files=True)
        names = {line.strip() for line in view.lines if line.strip()}
        assert any(name.endswith("a.py") for name in names)
        assert all(":" not in name for name in names)

    def test_count_mode(self, search_tree: Path):
        _result, view = run(search_tree, "old_api", count_only=True)
        assert any(line.endswith(":2") for line in view.lines)
        assert "total:4" in view.stdout

    def test_context_lines(self, search_tree: Path):
        _result, view = run(search_tree, "old_api", before_context=1, after_context=1)
        # Context lines use '-' as the separator, match lines use ':'.
        assert any(":- " in line or "- " in line for line in view.lines)
        assert "--" in view.stdout

    def test_quiet_prints_nothing_but_data(self, search_tree: Path):
        view = Captured(verbosity=Verbosity.QUIET)
        run(search_tree, "old_api", view=view)
        assert "[info]" not in view.stdout

    def test_json_mode_keeps_stdout_clean(self, search_tree: Path):
        """The JSON document is emitted by the command layer, on stdout only."""
        from cli_helpers import sr

        result = sr("find", "old_api", "--json", cwd=search_tree)
        assert result.code == 0, result.err
        document = result.json()
        assert document["totals"]["matches"] == 4
        assert document["totals"]["matched_files"] == 2
        assert {item["rel_path"] for item in document["files"]} == {"src/a.py", "README.md"}
        assert result.err == "", "diagnostics must not pollute stdout in JSON mode"

    def test_totals_line(self, search_tree: Path):
        _result, view = run(search_tree, "old_api")
        assert "4 matches in 2 files" in view.stdout


class TestResultObject:
    def test_to_dict(self, search_tree: Path):
        result, _view = run(search_tree, "old_api")
        document = result.to_dict()
        assert document["totals"]["matches"] == 4
        assert document["files"]

    def test_duration_is_measured(self, search_tree: Path):
        result, _view = run(search_tree, "old_api")
        assert result.duration >= 0

    def test_per_file_dict(self, search_tree: Path):
        result, _view = run(search_tree, "old_api")
        entry = next(item for item in result.files if item.count)
        payload = entry.to_dict()
        assert payload["matches"] == entry.count
        assert payload["hits"][0]["line"] >= 1
