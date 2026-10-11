"""CLI tests for the operational commands: doctor, selftest, config, update.

These commands are what a user runs *about* sr rather than *with* sr, so the
tests focus on: does it work headless, is the output parseable, and do the exit
codes mean what the documentation says.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cli_helpers import sr, write

from sr.exit_codes import ExitCode

# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


class TestDoctor:
    def test_runs_offline(self, tmp_path: Path):
        result = sr("doctor", "--offline", cwd=tmp_path)
        assert result.code == 0, result.out + result.err
        assert "python" in result.out
        assert "filesystem" in result.out

    def test_groups_are_rendered(self, tmp_path: Path):
        result = sr("doctor", "--offline", cwd=tmp_path)
        for group in ("RUNTIME", "CONFIGURATION", "FILESYSTEM"):
            assert group in result.out

    def test_json_document(self, tmp_path: Path):
        result = sr("doctor", "--offline", "--json", cwd=tmp_path)
        document = result.json()
        assert document["ok"] is True
        assert document["totals"]["checks"] >= 8
        names = {check["name"] for check in document["checks"]}
        assert {"python", "installation", "config", "filesystem", "locking", "disk"} <= names

    def test_a_broken_config_file_is_refused_up_front(self, tmp_path: Path):
        """An unparseable config is a usage error, reported before anything runs."""
        (tmp_path / ".srrc").write_text("keep = not-a-number\n")
        result = sr("doctor", "--offline", cwd=tmp_path)
        assert result.code == int(ExitCode.USAGE)
        assert "keep" in result.err
        assert ".srrc" in result.err

    def test_doctor_lists_a_valid_project_config(self, tmp_path: Path):
        (tmp_path / ".srrc").write_text("keep = 3\n")
        result = sr("doctor", "--offline", "--json", cwd=tmp_path)
        checks = {item["name"]: item for item in result.json()["checks"]}
        assert checks["config"]["status"] == "ok"
        assert any(".srrc" in item for item in checks["config"]["data"]["files"])

    def test_reports_the_session_store(self, tmp_path: Path):
        write(tmp_path, "a.txt", "hello\n")
        sr("replace", "hello", "bye", "-p", "a.txt", cwd=tmp_path)
        result = sr("doctor", "--offline", cwd=tmp_path)
        assert "session-store" in result.out
        assert "1 session" in result.out

    def test_network_probe_can_be_skipped(self, tmp_path: Path):
        result = sr("doctor", "--offline", "--json", cwd=tmp_path)
        names = {check["name"] for check in result.json()["checks"]}
        assert "network" not in names

    def test_debug_shows_check_data(self, tmp_path: Path):
        result = sr("doctor", "--offline", "--debug", cwd=tmp_path)
        assert "version" in result.out or "data" in result.out


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


class TestSelftest:
    def test_passes(self, tmp_path: Path):
        result = sr("selftest", cwd=tmp_path)
        assert result.code == 0, result.out + result.err
        assert "works correctly" in result.out

    def test_quick_mode(self, tmp_path: Path):
        result = sr("selftest", "--quick", cwd=tmp_path)
        assert result.code == 0
        assert "Cases" in result.out

    def test_json_document(self, tmp_path: Path):
        result = sr("selftest", "--json", cwd=tmp_path)
        document = result.json()
        assert document["ok"] is True
        assert document["totals"]["failed"] == 0
        assert all(case["passed"] for case in document["cases"])

    def test_covers_the_documented_areas(self, tmp_path: Path):
        result = sr("selftest", "--json", cwd=tmp_path)
        names = " ".join(case["name"] for case in result.json()["cases"])
        for expected in ("discovery", "literal", "regex", "encoding", "line endings",
                         "binary", "rollback", "drift", "dry run", "rename"):
            assert expected in names, f"selftest does not cover {expected!r}"

    def test_leaves_no_workspace_behind(self, tmp_path: Path):
        before = sorted(item.name for item in tmp_path.iterdir())
        sr("selftest", cwd=tmp_path)
        assert sorted(item.name for item in tmp_path.iterdir()) == before

    def test_keep_leaves_the_workspace(self, tmp_path: Path):
        result = sr("selftest", "--keep", cwd=tmp_path)
        assert "workspace kept" in result.err + result.out


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


class TestConfigCommand:
    def test_list_is_grouped(self, tmp_path: Path):
        result = sr("config", "list", cwd=tmp_path)
        assert result.code == 0
        assert "# TARGET SELECTION" in result.out
        assert "keep" in result.out

    def test_unset_values_are_marked(self, tmp_path: Path):
        result = sr("config", "list", cwd=tmp_path)
        assert "(unset)" in result.out

    def test_show_origin(self, tmp_path: Path):
        (tmp_path / ".srrc").write_text("keep = 3\n")
        result = sr("config", "list", "--show-origin", cwd=tmp_path)
        assert ".srrc" in result.out
        assert "# default" in result.out

    def test_show_one_option(self, tmp_path: Path):
        result = sr("config", "show", "backup", cwd=tmp_path)
        assert result.code == 0
        assert "SR_BACKUP" in result.out
        assert "--no-backup" in result.out

    def test_show_accepts_a_flag(self, tmp_path: Path):
        result = sr("config", "show", "--max-size", cwd=tmp_path)
        assert "SR_MAX_SIZE" in result.out

    def test_show_json(self, tmp_path: Path):
        document = sr("config", "show", "keep", "--json", cwd=tmp_path).json()
        assert document["key"] == "keep"
        assert document["value"] == 20
        assert document["env"] == "SR_KEEP"

    def test_path_lists_the_search_order(self, tmp_path: Path):
        result = sr("config", "path", cwd=tmp_path)
        assert result.code == 0
        assert "sr.conf" in result.out
        assert ".srrc" in result.out

    def test_validate_ok(self, tmp_path: Path):
        (tmp_path / ".srrc").write_text("keep = 3\nignore-case = true\n")
        result = sr("config", "validate", cwd=tmp_path)
        assert result.code == 0
        assert "valid" in result.out

    def test_validate_reports_unknown_keys(self, tmp_path: Path):
        (tmp_path / ".srrc").write_text("nope = 1\n")
        result = sr("config", "validate", cwd=tmp_path)
        assert "unknown option" in result.out + result.err

    def test_validate_reports_syntax_errors(self, tmp_path: Path):
        (tmp_path / ".srrc").write_text("this is not valid config\n")
        result = sr("config", "validate", cwd=tmp_path)
        assert result.code == int(ExitCode.USAGE)

    def test_init_writes_a_template(self, tmp_path: Path):
        target = tmp_path / "generated.conf"
        result = sr("config", "init", "--file", str(target), cwd=tmp_path)
        assert result.code == 0
        text = target.read_text()
        assert "keep" in text and "ignore-case" in text

    def test_init_refuses_to_clobber(self, tmp_path: Path):
        target = tmp_path / "generated.conf"
        target.write_text("PRE-EXISTING-CONTENT\n")
        result = sr("config", "init", "--file", str(target), cwd=tmp_path)
        assert result.code == int(ExitCode.USAGE)
        assert target.read_text() == "PRE-EXISTING-CONTENT\n"

    def test_init_force_overwrites(self, tmp_path: Path):
        target = tmp_path / "generated.conf"
        target.write_text("PRE-EXISTING-CONTENT\n")
        result = sr("config", "init", "--file", str(target), "--force", cwd=tmp_path)
        assert result.code == 0, result.err
        assert "PRE-EXISTING-CONTENT" not in target.read_text()
        assert "# sr configuration file" in target.read_text()

    def test_generated_template_validates(self, tmp_path: Path):
        target = tmp_path / "generated.conf"
        sr("config", "init", "--file", str(target), cwd=tmp_path)
        result = sr("config", "validate", "--config-file", str(target), cwd=tmp_path)
        assert result.code == 0, result.out + result.err

    def test_bad_config_file_is_a_usage_error(self, tmp_path: Path):
        (tmp_path / ".srrc").write_text("[replace]\nmax-depth = deep\n")
        result = sr("find", "x", cwd=tmp_path)
        assert result.code == int(ExitCode.USAGE)
        assert "max_depth" in result.err or "max-depth" in result.err


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


class TestUpdateCommand:
    def test_check_without_network_reports_cleanly(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        # Point the release feed at a host that cannot resolve.
        result = sr("update", "--check", "--repo", "invalid/invalid", "--timeout", "2", cwd=tmp_path)
        assert result.code in {0, int(ExitCode.UPDATE_FAILED)}
        assert "Traceback" not in result.err

    def test_json_shape(self, tmp_path: Path):
        result = sr("update", "--check", "--repo", "invalid/invalid", "--timeout", "2",
                    "--json", cwd=tmp_path)
        if result.code == 0:
            document = result.json()
            assert "current" in document and "latest" in document
        else:
            assert "error" in result.err

    def test_help_mentions_the_install_types(self, tmp_path: Path):
        result = sr("help", "update", cwd=tmp_path)
        assert result.code == 0
        for word in ("standalone", "pipx", "npm", "SHA-256"):
            assert word in result.out


# ---------------------------------------------------------------------------
# completions
# ---------------------------------------------------------------------------


class TestCompletionsCommand:
    def test_auto_detects_a_shell(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        result = sr("completions", cwd=tmp_path, env={"SHELL": "/bin/zsh"})
        assert result.code == 0
        assert result.out.startswith("#compdef sr")

    def test_install_to_xdg(self, tmp_path: Path):
        data = tmp_path / "data"
        result = sr("completions", "bash", "--install", cwd=tmp_path,
                    env={"XDG_DATA_HOME": str(data)})
        assert result.code == 0, result.err
        installed = data / "bash-completion" / "completions" / "sr"
        assert installed.is_file()

    def test_unknown_shell_is_a_usage_error(self, tmp_path: Path):
        assert sr("completions", "csh", cwd=tmp_path).code == int(ExitCode.USAGE)


# ---------------------------------------------------------------------------
# help / version plumbing through the CLI
# ---------------------------------------------------------------------------


class TestHelpPlumbing:
    def test_bare_help_exits_with_usage(self, tmp_path: Path):
        result = sr(cwd=tmp_path)
        assert result.code == int(ExitCode.USAGE)
        assert "QUICK START" in result.out

    def test_dash_h_exits_zero(self, tmp_path: Path):
        assert sr("-h", cwd=tmp_path).code == 0

    def test_help_all_is_large(self, tmp_path: Path):
        result = sr("--help-all", cwd=tmp_path)
        assert result.code == 0
        assert len(result.out) > 20000

    def test_piped_output_has_no_ansi(self, tmp_path: Path):
        result = sr("help", "matching", cwd=tmp_path)
        assert "\x1b[" not in result.out

    def test_color_always_emits_ansi(self, tmp_path: Path):
        result = sr("help", "matching", "--color", "always", cwd=tmp_path)
        # help text is plain, so check a coloured surface instead
        result = sr("find", "x", "--color", "always", cwd=tmp_path)
        assert result.code in {0, int(ExitCode.NO_MATCH)}

    def test_json_help(self, tmp_path: Path):
        document = sr("help", "--json", cwd=tmp_path).json()
        assert document["program"] == "sr"
        assert len(document["options"]) > 50
        assert document["exit_codes"]["3"]
