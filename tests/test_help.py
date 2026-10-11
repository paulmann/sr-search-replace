"""Tests for the help system, the version banner and shell completions.

The help system is a deliverable in its own right, so these tests assert on
substance (every topic renders, every option is documented, the JSON document
is complete) rather than on wording.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cli_helpers import sr

from sr.exit_codes import ExitCode
from sr.help_system import (
    TOPIC_ORDER,
    command_help,
    help_all_text,
    help_document,
    option_help,
    options_reference,
    overview,
    render_topic,
    topics_index,
)
from sr.options import OPTIONS
from sr.registry import SUBCOMMAND_NAMES, SUBCOMMANDS


class TestOverview:
    def test_overview_lists_every_command(self):
        text = overview()
        for name, _aliases, _summary in SUBCOMMANDS:
            assert name in text, f"{name} missing from the overview"

    def test_overview_mentions_the_version(self):
        from sr.version import __version__

        assert __version__ in overview()

    def test_topics_index_lists_every_topic(self):
        text = topics_index()
        for topic in TOPIC_ORDER:
            assert topic in text


class TestTopics:
    @pytest.mark.parametrize("topic", TOPIC_ORDER)
    def test_every_topic_renders(self, topic: str):
        text = render_topic(topic)
        assert len(text) > 200, f"topic {topic} looks empty"
        assert "sr" in text, f"topic {topic} never mentions the program"

    @pytest.mark.parametrize("topic", TOPIC_ORDER)
    def test_topics_have_no_placeholder_text(self, topic: str):
        text = render_topic(topic).lower()
        for needle in ("tbd", "lorem ipsum", "placeholder", "<insert"):
            assert needle not in text, f"topic {topic} contains {needle!r}"

    def test_command_names_are_topics(self):
        for name in SUBCOMMAND_NAMES:
            assert len(render_topic(name)) > 200

    def test_aliases_resolve(self):
        assert render_topic("undo") == render_topic("rollback")
        assert render_topic("grep") == render_topic("find")

    def test_unknown_topic_is_reported(self):
        result = sr("help", "definitely-not-a-topic")
        assert result.code == int(ExitCode.USAGE)
        assert "unknown help topic" in result.err

    def test_help_all_contains_the_option_reference(self):
        text = help_all_text()
        assert "--ignore-case" in text
        assert "Exit codes" in text
        assert "NO_MATCH" not in text  # internal names must not leak
        assert len(text) > 20000

    def test_help_all_covers_every_topic(self):
        text = help_all_text().lower()
        for topic in TOPIC_ORDER:
            if topic in {"commands", "options", "intro"}:
                continue
            assert topic.replace("-", " ") in text or topic in text, f"{topic} missing"


class TestOptionHelp:
    def test_every_option_is_documented(self):
        for spec in OPTIONS:
            text = option_help(f"--{spec.name}")
            assert spec.help.split(".")[0].split(";")[0][:20] in text or spec.name in text
            assert spec.env_var in text
            assert spec.config_key in text

    def test_short_flag_lookup(self):
        assert "--ignore-case" in option_help("-i")

    def test_unknown_option(self):
        from sr.errors import UsageError

        with pytest.raises(UsageError, match="unknown option"):
            option_help("--definitely-not-an-option")

    def test_options_reference_groups(self):
        text = options_reference()
        assert "TARGET SELECTION" in text.upper() or "Target selection" in text
        assert "SAFETY" in text.upper()

    def test_options_reference_for_a_command(self):
        text = options_reference("find")
        assert "--count" not in text  # command-specific extras live in the parser
        assert "--ignore-case" in text

    def test_option_help_via_cli(self):
        result = sr("help", "--max-count")
        assert result.code == 0
        assert "--max-count" in result.out
        assert "SR_MAX_COUNT" in result.out

    def test_short_option_help_via_cli(self):
        result = sr("help", "-w")
        assert "--word-boundary" in result.out


class TestCommandHelp:
    @pytest.mark.parametrize("name", sorted(SUBCOMMAND_NAMES))
    def test_every_command_has_help(self, name: str):
        text = command_help(name)
        assert name in text
        assert "USAGE" in text
        assert "SEE ALSO" in text

    @pytest.mark.parametrize("name", sorted(SUBCOMMAND_NAMES))
    def test_dash_h_works(self, name: str):
        result = sr(name, "--help")
        assert result.code == 0
        assert "USAGE" in result.out or "usage" in result.out.lower()


class TestHelpCommand:
    def test_plain_help(self):
        result = sr("help")
        assert result.code == 0
        assert "QUICK START" in result.out

    def test_help_topics(self):
        result = sr("help", "topics")
        assert result.code == 0
        assert "troubleshooting" in result.out

    def test_help_json_index(self):
        result = sr("help", "--json")
        document = result.json()
        assert document["topic"] == "overview"
        assert document["topics"] and document["commands"] and document["options"]
        assert document["exit_codes"]["0"]

    def test_help_json_topic(self):
        result = sr("help", "matching", "--json")
        document = result.json()
        assert document["topic"] == "matching"
        assert "regex" in document["text"].lower()

    def test_help_no_pager(self):
        result = sr("help", "options", "--no-pager")
        assert result.code == 0
        assert "--max-size" in result.out

    def test_help_all_flag(self):
        result = sr("--help-all")
        assert result.code == 0
        assert len(result.out) > 20000


class TestVersion:
    def test_version_output(self):
        result = sr("--version")
        assert result.code == 0
        assert "sr 7." in result.out
        assert "Python" in result.out
        assert "MIT" in result.out

    def test_version_short_flag(self):
        assert "sr 7." in sr("-V").out

    def test_help_document_carries_the_version(self):
        from sr.version import __version__

        assert help_document("x", "y")["version"] == __version__


class TestCompletions:
    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_generates(self, shell: str):
        result = sr("completions", shell)
        assert result.code == 0
        assert len(result.out) > 200

    def test_bash_mentions_every_command(self):
        text = sr("completions", "bash").out
        for name in SUBCOMMAND_NAMES:
            assert name in text

    def test_zsh_starts_with_compdef(self):
        assert sr("completions", "zsh").out.startswith("#compdef sr")

    def test_fish_uses_complete_directives(self):
        text = sr("completions", "fish").out
        assert "complete -c sr" in text
        assert "-l ignore-case" in text
        assert "__fish_seen_subcommand_from" in text

    def test_bash_script_is_valid_sh(self, tmp_path: Path):
        import shutil
        import subprocess

        if not shutil.which("bash"):
            pytest.skip("bash not available")
        script = tmp_path / "sr-completion.bash"
        script.write_text(sr("completions", "bash").out)
        completed = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True, check=False
        )
        assert completed.returncode == 0, completed.stderr

    def test_unsupported_shell(self):
        result = sr("completions", "powershell")
        assert result.code == int(ExitCode.USAGE)

    def test_install_writes_a_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("SR_COMPLETION_SHELL", "bash")
        result = sr("completions", "bash", "--install")
        assert result.code == 0, result.err
        installed = tmp_path / "data" / "bash-completion" / "completions" / "sr"
        assert installed.is_file()
        assert "_sr_completion" in installed.read_text()

    def test_generated_options_match_the_schema(self):
        from sr.commands.completions import flags_for

        flags = set(flags_for("replace"))
        assert "--ignore-case" in flags
        assert "--max-size" in flags
        for spec in OPTIONS:
            if not spec.commands or "replace" in spec.commands:
                assert f"--{spec.name}" in flags, f"--{spec.name} missing from completion"
