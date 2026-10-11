"""Exception hierarchy.

Every error that is *expected* (bad input, unreadable file, corrupt session)
derives from :class:`SrError` and carries an exit code plus an optional
``hint`` — a one-line suggestion printed under the message.  Unexpected bugs
propagate as ordinary exceptions and are rendered with a traceback only in
debug mode.
"""

from __future__ import annotations

from collections.abc import Sequence

from .exit_codes import ExitCode


class SrError(Exception):
    """Base class for all recoverable sr failures."""

    exit_code: ExitCode = ExitCode.ERROR

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class UsageError(SrError):
    """Invalid command line or configuration."""

    exit_code = ExitCode.USAGE


class ConfigError(SrError):
    """A configuration file exists but cannot be parsed or validated."""

    exit_code = ExitCode.USAGE


class PatternError(SrError):
    """The search pattern is not a valid regex / escape sequence."""

    exit_code = ExitCode.USAGE


class NoMatchError(SrError):
    """The run succeeded but nothing matched."""

    exit_code = ExitCode.NO_MATCH


class BackupError(SrError):
    """A backup could not be created or is inconsistent."""

    exit_code = ExitCode.ERROR


class SessionError(SrError):
    """A session directory is missing, corrupt or was created by a newer sr."""

    exit_code = ExitCode.ERROR


class RollbackError(SrError):
    """Rollback could not restore one or more files."""

    exit_code = ExitCode.ROLLBACK_FAILED


class UpdateError(SrError):
    """Self-update aborted."""

    exit_code = ExitCode.UPDATE_FAILED


class SelfTestError(SrError):
    """Built-in smoke test failed."""

    exit_code = ExitCode.SELFTEST_FAILED


class InterruptedError(SrError):
    """The user pressed Ctrl-C."""

    exit_code = ExitCode.INTERRUPTED


class MultiError(SrError):
    """Aggregates several :class:`SrError` instances into one report.

    Used when a batch operation fails for many files: the user should see all
    reasons at once rather than the first one only.
    """

    def __init__(self, errors: Sequence[SrError], summary: str | None = None) -> None:
        self.errors = list(errors)
        text = summary or f"{len(self.errors)} operations failed"
        details = "; ".join(str(err) for err in self.errors[:5])
        if len(self.errors) > 5:
            details += f"; … and {len(self.errors) - 5} more"
        super().__init__(f"{text}: {details}" if details else text)

    @property
    def exit_code(self) -> ExitCode:  # type: ignore[override]
        """Most severe (numerically largest, excluding OK) code of the batch."""
        codes = [int(err.exit_code) for err in self.errors if err.exit_code]
        return ExitCode(max(codes)) if codes else ExitCode.ERROR
