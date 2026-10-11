"""Tests for the small but load-bearing contracts: version, exit codes, options.

These three modules are what the rest of the package (and CI, and `sr update`,
and users' shell scripts) depend on, so their behaviour is pinned exactly.
"""

from __future__ import annotations

import re

import pytest

from sr import options
from sr.exit_codes import DESCRIPTIONS, ExitCode
from sr.options import (
    BY_CONFIG_KEY,
    BY_ENV,
    BY_FLAG,
    BY_NAME,
    GROUPS,
    OPTIONS,
    OptionKind,
    parse_bool,
    split_list,
)
from sr.version import __version__, is_newer, version_tuple

# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


class TestVersion:
    def test_is_semver(self):
        assert re.match(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$", __version__)

    def test_pyproject_agrees(self):
        from pathlib import Path

        text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
        declared = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE).group(1)
        assert declared == __version__, "run `make sync-version`"

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("1.2.3", (1, 2, 3, "")),
            ("v1.2.3", (1, 2, 3, "")),
            ("1.2.3-rc.1", (1, 2, 3, "rc.1")),
            ("10.20.30", (10, 20, 30, "")),
        ],
    )
    def test_tuple(self, text, expected):
        assert version_tuple(text) == expected

    def test_tuple_of_garbage(self):
        assert version_tuple("nonsense") == (0, 0, 0, "nonsense")

    def test_tuple_of_none_uses_the_current_version(self):
        assert version_tuple(None)[:3] == version_tuple(__version__)[:3]

    def test_ordering(self):
        assert version_tuple("1.2.3") < version_tuple("1.10.0")
        assert version_tuple("2.0.0") > version_tuple("1.99.99")

    def test_is_newer_strict(self):
        assert not is_newer("1.0.0", "1.0.0")
        assert is_newer("1.0.1", "1.0.0")


# ---------------------------------------------------------------------------
# exit codes
# ---------------------------------------------------------------------------


class TestExitCodes:
    def test_values_are_frozen(self):
        """Changing a code is a breaking change; this test makes it deliberate."""
        assert ExitCode.OK == 0
        assert ExitCode.ERROR == 1
        assert ExitCode.USAGE == 2
        assert ExitCode.NO_MATCH == 3
        assert ExitCode.ROLLBACK_FAILED == 4
        assert ExitCode.UPDATE_FAILED == 5
        assert ExitCode.SELFTEST_FAILED == 6
        assert ExitCode.INTERRUPTED == 130

    def test_codes_are_unique(self):
        values = [int(code) for code in ExitCode]
        assert len(values) == len(set(values))

    def test_every_code_is_documented(self):
        for code in ExitCode:
            assert DESCRIPTIONS[int(code)]
            assert len(DESCRIPTIONS[int(code)]) > 10

    def test_is_an_intenum(self):
        """Commands return plain ints, so the enum must behave like one."""
        assert isinstance(ExitCode.OK, int)
        assert ExitCode.NO_MATCH + 0 == 3


# ---------------------------------------------------------------------------
# option schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_names_are_unique(self):
        names = [spec.name for spec in OPTIONS]
        assert len(names) == len(set(names))

    def test_every_flag_is_unique(self):
        seen: dict[str, str] = {}
        for spec in OPTIONS:
            for flag in spec.flag_names():
                assert flag not in seen, f"{flag} is claimed by {seen.get(flag)} and {spec.name}"
                seen[flag] = spec.name

    def test_short_flags_are_single_characters(self):
        for spec in OPTIONS:
            if spec.short:
                assert len(spec.short) == 1, spec.name
                assert spec.short.isalnum(), spec.name

    def test_every_option_has_help(self):
        for spec in OPTIONS:
            assert spec.help.strip(), spec.name
            assert spec.help.strip().endswith("."), f"{spec.name}: help should end with a full stop"

    def test_choices_declared_for_choice_kind(self):
        for spec in OPTIONS:
            if spec.kind == OptionKind.CHOICE:
                assert spec.choices, f"{spec.name} is a choice without choices"
                assert spec.default in spec.choices or spec.default is None, spec.name

    def test_negatable_flags_are_flags(self):
        for spec in OPTIONS:
            if spec.negatable:
                assert spec.kind == OptionKind.FLAG, spec.name
                assert not spec.name.startswith("no-"), f"--no-no-… : {spec.name}"

    def test_indexes_are_consistent(self):
        from sr.options import ALL_OPTIONS, CONFIGURABLE

        for spec in ALL_OPTIONS:
            assert BY_NAME[spec.name] is spec
            for flag in spec.flag_names():
                assert BY_FLAG[flag] is spec
        # Config/env indexes cover the configurable subset only: a per-invocation
        # flag such as --json must not be settable from a config file.
        for spec in CONFIGURABLE:
            assert BY_CONFIG_KEY[spec.config_key] is spec
            assert BY_ENV[spec.env_var] is spec
        for spec in OPTIONS:
            if not spec.configurable:
                assert spec.config_key not in BY_CONFIG_KEY
                assert spec.env_var not in BY_ENV

    def test_env_var_naming(self):
        for spec in OPTIONS:
            expected = f"SR_{spec.name.replace('-', '_').upper()}"
            assert spec.env_var == expected or spec.env == expected

    def test_groups_are_known(self):
        known = {name for name, _title in GROUPS}
        for spec in OPTIONS:
            assert spec.group in known, f"{spec.name} has unknown group {spec.group}"

    def test_commands_are_known(self):
        from sr.registry import SUBCOMMAND_NAMES

        for spec in OPTIONS:
            for command in spec.commands:
                assert command in SUBCOMMAND_NAMES, f"{spec.name} references {command}"

    def test_defaults_match_their_kind(self):
        for spec in OPTIONS:
            default = spec.default
            if default is None:
                continue
            if spec.kind == OptionKind.FLAG:
                assert isinstance(default, bool), spec.name
            elif spec.kind == OptionKind.INT:
                assert isinstance(default, int), spec.name
            elif spec.kind == OptionKind.LIST:
                assert isinstance(default, (list, tuple)), spec.name
            elif spec.kind == OptionKind.CHOICE:
                assert default in spec.choices, spec.name

    def test_describe_is_renderable(self):
        for spec in OPTIONS:
            text = options.describe(spec)
            assert spec.name in text


class TestCoercion:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("true", True), ("TRUE", True), ("yes", True), ("1", True), ("on", True),
            ("false", False), ("no", False), ("0", False), ("off", False), ("", False),
        ],
    )
    def test_parse_bool(self, value, expected):
        assert parse_bool(value) is expected

    def test_parse_bool_passthrough(self):
        assert parse_bool(True) is True
        assert parse_bool(False) is False

    def test_parse_bool_rejects_nonsense(self):
        with pytest.raises(ValueError):
            parse_bool("maybe")

    @pytest.mark.parametrize(
        "value,expected",
        [("a, b", ["a", "b"]), ("a b", ["a", "b"]), ("  a ,, b  ", ["a", "b"]), ("", [])],
    )
    def test_split_list(self, value, expected):
        assert split_list(value) == expected

    def test_flag_coercion(self):
        assert BY_NAME["ignore-case"].coerce("yes") is True
        assert BY_NAME["ignore-case"].coerce(None) is BY_NAME["ignore-case"].default

    def test_int_coercion(self):
        assert BY_NAME["keep"].coerce("7") == 7
        with pytest.raises(ValueError):
            BY_NAME["keep"].coerce("seven")

    def test_size_coercion(self):
        assert BY_NAME["max-size"].coerce("2MB") == 2 * 1024 * 1024
        assert BY_NAME["max-size"].coerce("10") == 10 * 1024 * 1024
        with pytest.raises(ValueError):
            BY_NAME["max-size"].coerce("huge")

    def test_duration_coercion(self):
        assert BY_NAME["keep-age"].coerce("2d") == 2 * 86400
        with pytest.raises(ValueError):
            BY_NAME["keep-age"].coerce("soon")

    def test_choice_coercion(self):
        assert BY_NAME["color"].coerce("NEVER") == "never"
        with pytest.raises(ValueError):
            BY_NAME["color"].coerce("rainbow")

    def test_list_coercion_from_string_and_list(self):
        spec = BY_NAME["exclude"]
        assert spec.coerce("a, b") == ["a", "b"]
        assert spec.coerce(["a", "b"]) == ["a", "b"]


class TestHelpers:
    def test_options_for_a_command(self):
        names = {spec.name for spec in options.options_for("replace")}
        assert {"ignore-case", "max-size", "dry-run"} <= names

    def test_global_options_apply_everywhere(self):
        for command in ("replace", "find", "doctor"):
            names = {spec.name for spec in options.options_for(command)}
            assert "json" in names

    def test_specs_in_group(self):
        specs = options.specs_in_group("matching", "replace")
        assert specs and all(spec.group == "matching" for spec in specs)

    def test_defaults_dict(self):
        defaults = options.defaults()
        assert defaults["keep"] == 20
        assert len(defaults) == len(OPTIONS)
        assert "json" in defaults, "OPTIONS still holds the reporting flags"

    def test_flag_names_include_the_negative_form(self):
        assert BY_NAME["backup"].flag_names() == ("--backup", "--no-backup")

    def test_takes_value(self):
        assert BY_NAME["keep"].takes_value is True
        assert BY_NAME["backup"].takes_value is False
