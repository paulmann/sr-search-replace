"""Console entry point: ``python -m sr`` and the ``sr`` script."""

from __future__ import annotations

import sys

from .cli import PROG, main


def run() -> int:
    """Run sr and return the process exit code."""
    return main(sys.argv[1:], program=PROG)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
