"""Process exit codes.

The codes are part of sr's public contract: scripts and CI pipelines branch on
them, so they are frozen, asserted by ``tests/test_contracts.py``, and published
in ``docs/EXIT_CODES.md`` — a document *generated from this module* by
``scripts/gen_exit_codes.py`` (``make docs``), so the two cannot drift.

The layout intentionally mirrors the conventions of ``diff`` (1 = differences)
and ``grep`` (2 = error, 3 = special condition) so that muscle memory carries
over.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Stable, documented process exit codes."""

    OK = 0
    """Success. For ``replace``/``find`` this means at least one match."""

    ERROR = 1
    """Generic runtime failure (I/O error, corrupt session, unexpected bug)."""

    USAGE = 2
    """Bad command line, bad configuration file, unknown subcommand."""

    NO_MATCH = 3
    """Ran successfully but the pattern matched nothing (grep-like)."""

    ROLLBACK_FAILED = 4
    """A rollback could not be completed for at least one file."""

    UPDATE_FAILED = 5
    """Self-update aborted: network, checksum mismatch or unwritable install."""

    SELFTEST_FAILED = 6
    """``sr selftest`` detected a broken installation."""

    INTERRUPTED = 130
    """Cancelled with Ctrl-C (128 + SIGINT), conventional shell value."""


#: Human readable reason for every code, used by ``sr help exit-codes``.
DESCRIPTIONS: dict[int, str] = {
    ExitCode.OK: "success (for replace/find: at least one match was found)",
    ExitCode.ERROR: "runtime error (I/O failure, corrupt session, internal bug)",
    ExitCode.USAGE: "usage error (bad arguments, bad configuration file)",
    ExitCode.NO_MATCH: "no match: the pattern was not found in any target file",
    ExitCode.ROLLBACK_FAILED: "rollback failed for one or more files",
    ExitCode.UPDATE_FAILED: "self-update failed (network, checksum, permissions)",
    ExitCode.SELFTEST_FAILED: "selftest failed: installation is broken",
    ExitCode.INTERRUPTED: "interrupted by the user (SIGINT)",
}
