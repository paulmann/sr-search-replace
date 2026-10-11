"""Test helpers: builders for engine options and an in-memory console.

Fixtures live in ``conftest.py``; this module holds plain callables that tests
import explicitly.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

# Make the in-repository package importable when the suite is run without an
# editable install (`python3 -m pytest` from a fresh clone).
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sr import discovery
from sr.console import Console, OutputFormat, Verbosity, make_console
from sr.context import Context
from sr.pattern import build_spec
from sr.replace import ReplaceOptions, run_replace


class Captured:
    """A console whose stdout/stderr are in-memory buffers."""

    def __init__(
        self,
        *,
        fmt: str = OutputFormat.TEXT,
        verbosity: Verbosity = Verbosity.NORMAL,
        color: bool = False,
        width: int = 100,
    ) -> None:
        self.out = io.StringIO()
        self.err = io.StringIO()
        self.console: Console = make_console(
            verbosity=verbosity, color=color, fmt=fmt, out=self.out, err=self.err, width=width
        )

    @property
    def stdout(self) -> str:
        return self.out.getvalue()

    @property
    def stderr(self) -> str:
        return self.err.getvalue()

    @property
    def lines(self) -> list[str]:
        return self.stdout.splitlines()

    def json(self):
        """Parse the captured stdout as the single JSON document."""
        import json

        return json.loads(self.stdout)


def make_options(
    root: Path,
    search: str,
    replacement: str = "",
    *,
    patterns: list[str] | None = None,
    console: Console | None = None,
    apply_changes: bool = True,
    backup: bool = True,
    spec_kwargs: dict | None = None,
    **engine_kwargs,
) -> ReplaceOptions:
    """Build engine options for a test without going through argparse.

    ``spec_kwargs`` are forwarded to :func:`sr.pattern.build_spec` and
    ``engine_kwargs`` to :class:`~sr.replace.ReplaceOptions`, so a test can
    write either of::

        make_options(root, "a", "b", spec_kwargs={"kind": "regex"})
        make_options(root, "a", "b", keep=2, write_mode="dir", output_dir=out)
    """
    discovery_options = discovery.DiscoveryOptions(
        roots=[root], patterns=list(patterns) if patterns is not None else ["*"]
    )
    discovery_options.normalise()
    defaults: dict = dict(
        spec=build_spec(search, replacement, **(spec_kwargs or {})),
        discovery_options=discovery_options,
        root=root,
        apply_changes=apply_changes,
        backup=backup,
        console=console,
        command=["pytest"],
        version="7.0.0-test",
        keep=0,
    )
    defaults.update(engine_kwargs)
    return ReplaceOptions(**defaults)


def run(root: Path, search: str, replacement: str = "", **kwargs):
    """Shortcut: build options and run the engine."""
    return run_replace(make_options(root, search, replacement, **kwargs))


def make_context(
    *,
    command: str = "replace",
    args: object | None = None,
    explicit: set[str] | None = None,
    captured: Captured | None = None,
    target: str = ".",
    assume_yes: bool = True,
) -> Context:
    """A Context that never touches the real terminal.

    ``isolated_home`` has already redirected ``HOME``/``XDG_*``, so building the
    real configuration layering here is safe — and a better test than stubbing.
    """
    view = captured or Captured()
    context = Context.create(
        argv=[],
        args=args,
        explicit=explicit or set(),
        command=command,
        target=target,
    )
    context.console = view.console
    context.assume_yes = assume_yes
    context.stdin_is_tty = False
    context.stdout_is_tty = False
    return context
