"""Tests for configuration files, environment variables and precedence."""

from __future__ import annotations

from pathlib import Path

import pytest
from cli_helpers import sr

from sr.config import (
    ConfigError,
    build_config,
    parse_cli_config,
    parse_config_text,
    project_config_paths,
    render_config,
    user_config_path,
    write_config_template,
)
from sr.exit_codes import ExitCode
from sr.options import BY_CONFIG_KEY


class TestParsing:
    def test_key_value(self):
        sections = parse_config_text("ignore-case = true\n", origin="t")
        assert sections[""]["ignore_case"] == "true"

    def test_colon_separator(self):
        sections = parse_config_text("ignore-case: yes\n", origin="t")
        assert sections[""]["ignore_case"] == "yes"

    def test_bare_key_means_true(self):
        sections = parse_config_text("ignore-case\n", origin="t")
        assert sections[""]["ignore_case"] == "true"

    def test_comments_and_blanks(self):
        sections = parse_config_text("# c\n\n; also a comment\nkeep = 3\n", origin="t")
        assert sections[""] == {"keep": "3"}

    def test_trailing_comment_is_stripped(self):
        sections = parse_config_text("keep = 3   # newest three\n", origin="t")
        assert sections[""]["keep"] == "3"

    def test_hash_inside_quotes_survives(self):
        sections = parse_config_text('exclude = "a#b"\n', origin="t")
        assert sections[""]["exclude"] == "a#b"

    def test_sections(self):
        sections = parse_config_text("[replace]\nkeep = 1\n\n[find]\ncontext = 2\n", origin="t")
        assert sections["replace"]["keep"] == "1"
        assert sections["find"]["context"] == "2"

    def test_repeated_key_becomes_a_list(self):
        sections = parse_config_text("exclude = a\nexclude = b\n", origin="t")
        assert sections[""]["exclude"] == ["a", "b"]

    def test_dashes_and_underscores_are_equivalent(self):
        sections = parse_config_text("max-depth = 4\n", origin="t")
        assert "max_depth" in sections[""]

    def test_syntax_error_reports_file_and_line(self):
        with pytest.raises(ConfigError, match="t:2"):
            parse_config_text("keep = 1\nthis is not valid\n", origin="t")


class TestCoercion:
    def test_booleans(self, tmp_path: Path):
        path = tmp_path / "sr.conf"
        path.write_text("ignore-case = yes\nbackup = off\n")
        values, _per_command = _load(path)
        assert values["ignore_case"] is True
        assert values["backup"] is False

    def test_integers(self, tmp_path: Path):
        path = tmp_path / "sr.conf"
        path.write_text("keep = 7\nmax-depth = 3\n")
        values, _ = _load(path)
        assert values["keep"] == 7 and values["max_depth"] == 3

    def test_sizes(self, tmp_path: Path):
        path = tmp_path / "sr.conf"
        path.write_text("max-size = 2MB\nmin-size = 512\n")
        values, _ = _load(path)
        assert values["max_size"] == 2 * 1024 * 1024
        assert values["min_size"] == 512 * 1024 * 1024  # bare number = megabytes

    def test_durations(self, tmp_path: Path):
        path = tmp_path / "sr.conf"
        path.write_text("keep-age = 30d\n")
        values, _ = _load(path)
        assert values["keep_age"] == 30 * 86400

    def test_lists(self, tmp_path: Path):
        path = tmp_path / "sr.conf"
        path.write_text("exclude = *.min.js, dist\nexclude = vendor/*\n")
        values, _ = _load(path)
        assert values["exclude"] == ["*.min.js", "dist", "vendor/*"]

    def test_choices_are_validated(self, tmp_path: Path):
        path = tmp_path / "sr.conf"
        path.write_text("write = sideways\n")
        with pytest.raises(ConfigError, match="not one of"):
            _load(path)

    def test_per_invocation_flags_are_not_config_options(self, tmp_path: Path):
        """`json = true` in a config file would hijack every command."""
        path = tmp_path / "sr.conf"
        path.write_text("json = true\n")
        warnings: list[str] = []
        from sr.config import load_file

        load_file(path, warnings)
        assert any("unknown option" in item for item in warnings)

    def test_invalid_integer_reports_the_file(self, tmp_path: Path):
        path = tmp_path / "sr.conf"
        path.write_text("keep = lots\n")
        with pytest.raises(ConfigError, match="invalid value for 'keep'"):
            _load(path)

    def test_unknown_key_warns_but_does_not_fail(self, tmp_path: Path):
        path = tmp_path / "sr.conf"
        path.write_text("definitely-not-an-option = 1\n")
        warnings: list[str] = []
        from sr.config import load_file

        load_file(path, warnings)
        assert any("unknown option" in item for item in warnings)


def _load(path: Path):
    from sr.config import load_file

    return load_file(path, [])


class TestPrecedence:
    def test_defaults_present(self):
        config = build_config(target=Path(), command="replace", load_files=False)
        assert config.get("keep") == 20
        assert config.get("max_size") == 100 * 1024 * 1024

    def test_command_section_beats_global(self, tmp_path: Path):
        (tmp_path / ".srrc").write_text("keep = 9\n\n[replace]\nkeep = 3\n")
        assert build_config(target=tmp_path, command="replace").get("keep") == 3
        assert build_config(target=tmp_path, command="find").get("keep") == 9

    def test_environment_beats_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / ".srrc").write_text("keep = 9\n")
        monkeypatch.setenv("SR_KEEP", "4")
        assert build_config(target=tmp_path, command="replace").get("keep") == 4

    def test_cli_config_beats_environment(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SR_KEEP", "4")
        config = build_config(target=tmp_path, command="replace", cli_config=["keep=2"])
        assert config.get("keep") == 2

    def test_cli_flag_beats_everything(self, tmp_path: Path):
        """The flag must survive a config file that says something else."""
        (tmp_path / ".srrc").write_text("ignore-case = false\n")
        write = tmp_path / "a.txt"
        write.write_text("HELLO\n")
        result = sr("replace", "hello", "bye", "-i", "--dry-run", cwd=tmp_path)
        assert result.code == 0, result.err
        assert "Replacements           1" in result.out

    def test_config_file_drives_the_run(self, tmp_path: Path):
        (tmp_path / ".srrc").write_text("ignore-case = true\n")
        (tmp_path / "a.txt").write_text("HELLO\n")
        result = sr("replace", "hello", "bye", "--dry-run", cwd=tmp_path)
        assert result.code == 0, result.err
        assert "Replacements           1" in result.out

    def test_environment_drives_the_run(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("HELLO\n")
        result = sr("replace", "hello", "bye", "--dry-run", cwd=tmp_path,
                    env={"SR_IGNORE_CASE": "true"})
        assert result.code == 0, result.err
        assert "Replacements           1" in result.out

    def test_project_root_config_is_found(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".srrc").write_text("keep = 6\n")
        nested = tmp_path / "pkg" / "sub"
        nested.mkdir(parents=True)
        assert build_config(target=nested, command="replace").get("keep") == 6

    def test_nearest_config_wins(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".srrc").write_text("keep = 6\n")
        nested = tmp_path / "pkg"
        nested.mkdir()
        (nested / ".srrc").write_text("keep = 2\n")
        assert build_config(target=nested, command="replace").get("keep") == 2

    def test_search_stops_at_the_repository_boundary(self, tmp_path: Path):
        (tmp_path / ".srrc").write_text("keep = 6\n")
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        assert build_config(target=repo, command="replace").get("keep") == 20

    def test_missing_explicit_file_is_an_error(self, tmp_path: Path):
        with pytest.raises(ConfigError, match="not found"):
            build_config(target=tmp_path, explicit_files=[tmp_path / "nope.conf"])


class TestCliConfig:
    def test_key_value(self):
        assert parse_cli_config(["keep=3"])["keep"] == 3

    def test_bare_key_is_true(self):
        assert parse_cli_config(["ignore-case"])["ignore_case"] is True

    def test_unknown_key_is_an_error(self):
        with pytest.raises(ConfigError, match="unknown option"):
            parse_cli_config(["nope=1"])

    def test_bad_value_is_an_error(self):
        with pytest.raises(ConfigError, match="keep"):
            parse_cli_config(["keep=many"])


class TestOrigin:
    def test_default_origin(self):
        config = build_config(target=Path(), command="replace", load_files=False)
        assert config.origin_of("keep") == "default"

    def test_env_origin(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SR_KEEP", "5")
        config = build_config(target=Path(), command="replace", load_files=False)
        assert config.origin_of("keep") == "env:environment"

    def test_file_origin(self, tmp_path: Path):
        (tmp_path / ".srrc").write_text("keep = 5\n")
        config = build_config(target=tmp_path, command="replace")
        assert config.origin_of("keep").startswith("file:")


class TestRendering:
    def test_render_lists_every_option(self):
        config = build_config(target=Path(), command="replace", load_files=False)
        lines = render_config(config)
        assert len(lines) == len(BY_CONFIG_KEY)

    def test_render_with_origin(self, tmp_path: Path):
        (tmp_path / ".srrc").write_text("keep = 4\n")
        config = build_config(target=tmp_path, command="replace")
        lines = {
            line.split("=", 1)[0].strip(): line
            for line in render_config(config, show_origin=True)
        }
        assert "# default" in lines["max_depth"]
        assert ".srrc" in lines["keep"]

    def test_template_is_parseable(self, tmp_path: Path):
        path = write_config_template(tmp_path / "gen.conf")
        values, _per_command = _load(path)
        assert "keep" in values

    def test_template_refuses_to_clobber(self, tmp_path: Path):
        path = tmp_path / "gen.conf"
        path.write_text("keep = 1\n")
        with pytest.raises(ConfigError, match="already exists"):
            write_config_template(path)
        assert write_config_template(path, force=True).exists()


class TestSearchPath:
    def test_project_paths_are_limited(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        paths = project_config_paths(nested)
        assert len(paths) <= 3
        assert all(tmp_path in path.parents or path.parent == nested for path in paths)

    def test_user_config_path_follows_xdg(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        assert user_config_path() == tmp_path / "xdg" / "sr" / "sr.conf"


class TestConfigCommand:
    def test_list(self, tmp_path: Path):
        result = sr("config", "list", cwd=tmp_path)
        assert result.code == 0
        assert "keep" in result.out

    def test_show(self, tmp_path: Path):
        result = sr("config", "show", "confirm-threshold", cwd=tmp_path)
        assert result.code == 0
        assert "SR_CONFIRM_THRESHOLD" in result.out

    def test_show_unknown_key(self, tmp_path: Path):
        result = sr("config", "show", "nope", cwd=tmp_path)
        assert result.code == int(ExitCode.USAGE)

    def test_path(self, tmp_path: Path):
        result = sr("config", "path", cwd=tmp_path)
        assert result.code == 0
        assert "sr.conf" in result.out

    def test_validate_without_files(self, tmp_path: Path):
        result = sr("config", "validate", cwd=tmp_path)
        assert result.code == 0

    def test_validate_reports_errors(self, tmp_path: Path):
        (tmp_path / ".srrc").write_text("keep = many\n")
        result = sr("config", "validate", cwd=tmp_path)
        assert result.code == int(ExitCode.USAGE)
        assert "keep" in result.err + result.out

    def test_init(self, tmp_path: Path):
        result = sr("config", "init", "--file", str(tmp_path / "new.conf"), cwd=tmp_path)
        assert result.code == 0
        assert (tmp_path / "new.conf").is_file()

    def test_json(self, tmp_path: Path):
        result = sr("config", "list", "--json", cwd=tmp_path)
        document = result.json()
        assert document["values"]["keep"] == 20
        assert any(option["key"] == "keep" for option in document["options"])
