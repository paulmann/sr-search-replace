"""File discovery: patterns, exclusions, ``.gitignore`` and VCS safety.

The walker is deterministic (sorted), never follows symlinks into directories
by default, prunes excluded directories *before* descending (so scanning a
tree with ``node_modules`` costs nothing), and can honour ``.gitignore``.

Pattern semantics
-----------------
``--pattern`` / positional ``GLOB``
    Matched against the file *name* (``fnmatch``), e.g. ``*.py``.  A pattern
    containing ``/`` is matched against the path relative to the search root
    and supports ``**`` for recursive wildcards.
``--include`` / ``--exclude``
    Same syntax, applied after the primary pattern.  ``--exclude`` wins over
    ``--include`` (exclusion is always the safer default).
"""

from __future__ import annotations

import fnmatch
import os
import re
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import textio
from .util import parse_size

#: Directories that are never worth rewriting and often huge.
DEFAULT_EXCLUDE_DIRS: tuple[str, ...] = (
    ".git",
    ".hg",
    ".svn",
    ".bzr",
    "node_modules",
    "bower_components",
    "vendor",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    "env",
    ".cache",
    ".gradle",
    ".terraform",
    ".next",
    ".nuxt",
    ".output",
    "dist",
    "build",
    "target",
    "coverage",
    ".idea",
    ".vscode",
)

#: File names that are skipped unless explicitly requested.
DEFAULT_EXCLUDE_FILES: tuple[str, ...] = (
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "*.orig",
    "*.rej",
    "*.bak",
    "*.backup",
    "*.swp",
    "*~",
)

#: sr's own state directory; always pruned to keep rollback idempotent.
SR_STATE_DIR = ".sr"

_GLOBSTAR_TOKEN = "**"  # noqa: S105 - a glob sentinel, not a credential


def _translate_globstar(pattern: str) -> re.Pattern[str]:
    """Compile a shell-style pattern with ``**`` support into a regex."""
    parts = pattern.split(_GLOBSTAR_TOKEN)
    regex = ""
    for index, part in enumerate(parts):
        regex += re.escape(part).replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
        if index < len(parts) - 1:
            regex += ".*"
    return re.compile(f"^{regex}$")


@dataclass(slots=True)
class GitignoreRule:
    """One parsed ``.gitignore`` line."""

    pattern: str
    negated: bool = False
    dir_only: bool = False
    anchored: bool = False
    regex: re.Pattern[str] | None = None

    @classmethod
    def parse(cls, line: str) -> GitignoreRule | None:
        text = line.rstrip("\n").rstrip("\r")
        if not text.strip() or text.lstrip().startswith("#"):
            return None
        negated = text.startswith("!")
        if negated:
            text = text[1:]
        # A trailing space is significant only when escaped; simplify by rstrip.
        text = text.rstrip()
        if not text:
            return None
        dir_only = text.endswith("/")
        if dir_only:
            text = text[:-1]
        if text.startswith("/"):
            text = text[1:]
            anchored = True
        else:
            anchored = "/" in text.rstrip("/")
        regex_body = _glob_to_regex_body(text)
        if not anchored:
            regex_body = "(?:.*/)?" + regex_body
        regex = re.compile(f"^{regex_body}/?$")
        return cls(
            pattern=text,
            negated=negated,
            dir_only=dir_only,
            anchored=anchored,
            regex=regex,
        )


def _glob_to_regex_body(pattern: str) -> str:
    """Translate one path pattern (no ``**`` splitting) to a regex body."""
    out: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if pattern[index : index + 2] == "**":
                out.append(".*")
                index += 2
                if pattern[index : index + 1] == "/":
                    index += 1
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            close = pattern.find("]", index + 1)
            if close == -1:
                out.append(re.escape(char))
            else:
                body = pattern[index + 1 : close]
                if body.startswith("!"):
                    body = "^" + body[1:]
                elif body.startswith("^"):
                    body = "\\" + body
                out.append(f"[{body}]")
                index = close + 1
                continue
        else:
            out.append(re.escape(char))
        index += 1
    return "".join(out)


@dataclass(slots=True)
class Gitignore:
    """Stack of ``.gitignore`` rule sets, nearest directory wins."""

    layers: list[tuple[Path, list[GitignoreRule]]] = field(default_factory=list)

    @classmethod
    def load(cls, root: Path, *, include_global: bool = True) -> Gitignore:
        stack = cls()
        if include_global:
            for candidate in (
                Path(os.environ.get("GIT_CONFIG_GLOBAL", "~/.gitconfig")).expanduser(),
                Path("~/.config/git/ignore").expanduser(),
            ):
                core_excludes = _read_core_excludes_file(candidate)
                if core_excludes:
                    stack.push(root, _parse_rules(core_excludes), absolute=True)
                    break
        for path in (root, *root.parents):
            ignore_file = path / ".gitignore"
            if ignore_file.is_file():
                stack.push(path, _parse_rules(ignore_file.read_text(errors="replace").splitlines()))
            if (path / ".git").exists():
                break  # do not climb out of the repository
        return stack

    def push(
        self,
        base: Path,
        rules: Sequence[GitignoreRule],
        *,
        absolute: bool = False,
    ) -> None:
        if rules:
            self.layers.append((base, list(rules)))

    def push_for_dir(self, directory: Path) -> None:
        ignore_file = directory / ".gitignore"
        if ignore_file.is_file():
            try:
                self.push(directory, _parse_rules(ignore_file.read_text(errors="replace").splitlines()))
            except OSError:
                pass

    def pop_to(self, depth: int) -> None:
        while len(self.layers) > depth:
            self.layers.pop()

    def is_ignored(self, relative_path: str, *, is_dir: bool) -> bool:
        ignored = False
        for _base, rules in self.layers:
            for rule in rules:
                if rule.dir_only and not is_dir:
                    continue
                if rule.regex is None or not rule.regex.match(relative_path):
                    continue
                ignored = not rule.negated
        return ignored


def _parse_rules(lines: Iterable[str]) -> list[GitignoreRule]:
    rules: list[GitignoreRule] = []
    for line in lines:
        rule = GitignoreRule.parse(line)
        if rule is not None:
            rules.append(rule)
    return rules


def _read_core_excludes_file(gitconfig: Path) -> list[str] | None:
    if not gitconfig.is_file():
        return None
    try:
        content = gitconfig.read_text(errors="replace").splitlines()
    except OSError:
        return None
    in_core = False
    for line in content:
        stripped = line.strip()
        if stripped.startswith("["):
            in_core = stripped.lower() == "[core]"
            continue
        if in_core and stripped.lower().startswith("excludesfile"):
            _, _, value = stripped.partition("=")
            value = value.strip().strip('"')
            if value:
                target = Path(value).expanduser()
                if target.is_file():
                    try:
                        return target.read_text(errors="replace").splitlines()
                    except OSError:
                        return None
    return None


@dataclass(slots=True)
class DiscoveryOptions:
    """Everything that shapes which files are returned."""

    roots: list[Path] = field(default_factory=lambda: [Path()])
    patterns: list[str] = field(default_factory=list)
    explicit_files: list[Path] = field(default_factory=list)
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    exclude_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_DIRS))
    exclude_files: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_FILES))
    recursive: bool = True
    max_depth: int | None = None
    hidden: bool = False
    symlinks: bool = False
    follow_symlinked_dirs: bool = False
    gitignore: bool = False
    no_vcs: bool = True
    no_backups: bool = True
    max_size: int | None = None
    min_size: int | None = None
    newer_than: float | None = None
    older_than: float | None = None
    text_only: bool = True
    binary_sample: int = 8192
    encoding: str | None = None
    sort: bool = True
    collect_dirs: bool = False
    state_dir_name: str = SR_STATE_DIR

    def normalise(self) -> None:
        """Apply inter-option rules once, after parsing."""
        if self.no_vcs:
            for name in (".git", ".hg", ".svn", ".bzr"):
                if name not in self.exclude_dirs:
                    self.exclude_dirs.append(name)
        if self.no_backups and self.state_dir_name not in self.exclude_dirs:
            self.exclude_dirs.append(self.state_dir_name)


@dataclass(slots=True)
class SkippedFile:
    """A candidate that was filtered out, with the reason (for ``--verbose``)."""

    path: Path
    reason: str


@dataclass(slots=True)
class DiscoveryResult:
    files: list[Path] = field(default_factory=list)
    directories: list[Path] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)
    directories_scanned: int = 0
    files_seen: int = 0
    elapsed: float = 0.0

    @property
    def count(self) -> int:
        return len(self.files)


class FileMatcher:
    """Decides whether a relative path matches the include/exclude rules."""

    def __init__(self, options: DiscoveryOptions) -> None:
        self.options = options
        self.patterns = [_compile_matcher(p) for p in options.patterns]
        self.include = [_compile_matcher(p) for p in options.include]
        self.exclude = [_compile_matcher(p) for p in options.exclude]
        self.exclude_files = [_compile_matcher(p) for p in options.exclude_files]

    def matches_primary(self, rel_path: str, name: str) -> bool:
        if not self.patterns:
            return True
        return any(matcher(rel_path, name) for matcher in self.patterns)

    def matches_include(self, rel_path: str, name: str) -> bool:
        if not self.include:
            return True
        return any(matcher(rel_path, name) for matcher in self.include)

    def matches_exclude(self, rel_path: str, name: str) -> bool:
        return any(matcher(rel_path, name) for matcher in self.exclude)

    def matches_exclude_files(self, rel_path: str, name: str) -> bool:
        return any(matcher(rel_path, name) for matcher in self.exclude_files)


def _compile_matcher(pattern: str):
    """Return ``matcher(rel_path, name) -> bool`` for one glob pattern."""
    if "/" in pattern or _GLOBSTAR_TOKEN in pattern:
        regex = _translate_globstar(pattern)
        return lambda rel_path, _name, _regex=regex: bool(_regex.match(rel_path))
    return lambda _rel_path, name, _p=pattern: fnmatch.fnmatchcase(name, _p)


def discover(options: DiscoveryOptions) -> DiscoveryResult:
    """Collect the target files described by ``options``."""
    options.normalise()
    started = time.monotonic()
    matcher = FileMatcher(options)
    result = DiscoveryResult()
    seen: set[str] = set()
    roots_requested = bool(options.roots)

    def _accept(path: Path) -> None:
        key = os.path.realpath(str(path))
        if key in seen:
            return
        seen.add(key)
        result.files.append(path)

    # 1. Explicitly named files always take part (subject to safety filters).
    for explicit in options.explicit_files:
        resolved = Path(explicit)
        if not resolved.exists():
            result.skipped.append(SkippedFile(resolved, "does not exist"))
            continue
        if resolved.is_dir():
            result.skipped.append(SkippedFile(resolved, "is a directory (use it as a target)"))
            continue
        if not resolved.is_file():
            result.skipped.append(SkippedFile(resolved, "not a regular file"))
            continue
        root = Path(options.roots[0]) if options.roots else Path()
        reason = _rejection_reason(resolved, root, matcher, options, explicit=True)
        if reason:
            result.skipped.append(SkippedFile(resolved, reason))
            continue
        _accept(resolved)

    # 2. Directory walk.
    #
    # When the user named files explicitly and did not ask for a tree scan
    # (no --pattern, no --include, no -C), the walk is skipped: scanning the
    # default root as well would silently widen the target set, which is the
    # opposite of what `--file a.py` promises.
    only_explicit = (
        bool(options.explicit_files)
        and not options.patterns
        and not options.include
        and not roots_requested
    )
    for root in () if only_explicit else options.roots:
        root_path = Path(root)
        if not root_path.exists():
            result.skipped.append(SkippedFile(root_path, "target does not exist"))
            continue
        if root_path.is_file():
            reason = _rejection_reason(root_path, root_path.parent, matcher, options, explicit=True)
            if reason:
                result.skipped.append(SkippedFile(root_path, reason))
            else:
                _accept(root_path)
            continue
        ignore_stack: Gitignore | None = None
        if options.gitignore:
            ignore_stack = Gitignore.load(root_path.resolve())
        for candidate in _walk(root_path, options, ignore_stack, result):
            reason = _rejection_reason(candidate, root_path, matcher, options)
            if reason:
                result.skipped.append(SkippedFile(candidate, reason))
                continue
            _accept(candidate)

    if options.sort:
        result.files.sort(key=lambda item: str(item))
        # Deepest first: renaming a parent must happen after its children.
        result.directories.sort(key=lambda item: (len(item.parts), str(item)), reverse=True)
    result.elapsed = time.monotonic() - started
    return result


def _walk(
    root: Path,
    options: DiscoveryOptions,
    ignore: Gitignore | None,
    result: DiscoveryResult,
) -> Iterator[Path]:
    """Depth-limited, pruning-aware walk yielding candidate regular files.

    Recursive generators keep the code honest about directory ordering (which
    matters for ``.gitignore`` layering) and Python's recursion limit is not a
    concern because ``--max-depth`` caps the descent long before it would be.
    """
    yield from _walk_dir(root, root, 1, options, ignore, result)


def _walk_dir(
    directory: Path,
    root: Path,
    depth: int,
    options: DiscoveryOptions,
    ignore: Gitignore | None,
    result: DiscoveryResult,
) -> Iterator[Path]:
    # `depth` is 1-based to match `find -maxdepth`: the target directory itself
    # is depth 1, so `--max-depth 1` means "this directory, no descent".
    if options.max_depth is not None and depth > options.max_depth:
        return
    result.directories_scanned += 1
    try:
        entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
    except (PermissionError, OSError) as exc:
        result.skipped.append(
            SkippedFile(directory, f"cannot read directory ({exc.strerror or exc})")
        )
        return

    rel_dir = _relative(directory, root)
    ignore_layers = len(ignore.layers) if ignore is not None else 0
    if ignore is not None:
        ignore.push_for_dir(directory)

    subdirs: list[Path] = []
    try:
        for entry in entries:
            name = entry.name
            if name == options.state_dir_name and options.no_backups:
                continue
            is_hidden = name.startswith(".")
            try:
                is_link = entry.is_symlink()
                is_dir = entry.is_dir(follow_symlinks=options.follow_symlinked_dirs)
                is_file = entry.is_file(follow_symlinks=options.symlinks)
            except OSError:
                continue

            if is_link and not (options.symlinks or options.follow_symlinked_dirs):
                result.skipped.append(SkippedFile(Path(entry.path), "symlink (use --symlinks)"))
                continue

            rel_path = f"{rel_dir}/{name}" if rel_dir else name

            if is_dir:
                if not options.recursive:
                    continue
                if name in options.exclude_dirs:
                    result.skipped.append(
                        SkippedFile(Path(entry.path), f"excluded directory ({name})")
                    )
                    continue
                if is_hidden and not options.hidden:
                    continue
                if ignore is not None and ignore.is_ignored(rel_path, is_dir=True):
                    result.skipped.append(SkippedFile(Path(entry.path), "ignored by .gitignore"))
                    continue
                subdirs.append(Path(entry.path))
                if options.collect_dirs:
                    result.directories.append(Path(entry.path))
                continue

            if not is_file:
                continue
            result.files_seen += 1
            if is_hidden and not options.hidden:
                continue
            if ignore is not None and ignore.is_ignored(rel_path, is_dir=False):
                result.skipped.append(SkippedFile(Path(entry.path), "ignored by .gitignore"))
                continue
            yield Path(entry.path)
    finally:
        if ignore is not None:
            ignore.pop_to(ignore_layers)

    for sub in subdirs:
        yield from _walk_dir(sub, root, depth + 1, options, ignore, result)


def _relative(path: Path, root: Path) -> str:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return ""
    text = rel.as_posix()
    return "" if text == "." else text


def _rejection_reason(
    path: Path,
    root: Path,
    matcher: FileMatcher,
    options: DiscoveryOptions,
    *,
    explicit: bool = False,
) -> str | None:
    """Return why ``path`` must be skipped, or ``None`` when it is a target."""
    name = path.name
    rel_dir = _relative(path.parent, root) if root else ""
    rel_path = f"{rel_dir}/{name}" if rel_dir else name

    if not explicit:
        if not matcher.matches_primary(rel_path, name):
            return "does not match the pattern"
        if not matcher.matches_include(rel_path, name):
            return "not matched by --include"
    if matcher.matches_exclude(rel_path, name):
        return "matched --exclude"
    if matcher.matches_exclude_files(rel_path, name):
        # Named files are exempt from the *default* noise list but never from
        # an explicit --exclude.
        if explicit and set(options.exclude_files) == set(DEFAULT_EXCLUDE_FILES):
            pass
        else:
            return "matched the default exclude list"

    try:
        info = path.stat()
    except OSError as exc:
        return f"cannot stat ({exc.strerror or exc})"
    if not os.access(path, os.R_OK):
        return "not readable"

    if options.max_size is not None and info.st_size > options.max_size:
        return f"larger than {options.max_size} bytes"
    if options.min_size is not None and info.st_size < options.min_size:
        return f"smaller than {options.min_size} bytes"
    if options.newer_than is not None and info.st_mtime < options.newer_than:
        return "older than --newer-than"
    if options.older_than is not None and info.st_mtime > options.older_than:
        return "newer than --older-than"
    if options.text_only and textio.looks_binary(path, sample=options.binary_sample):
        return "binary content (use --binary to force)"
    return None


def parse_size_option(value: str | None) -> int | None:
    """``--max-size`` accepts ``10MB`` and, for backwards friendliness, ``10``."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text) * 1024 * 1024  # bare number == megabytes (legacy)
    size = parse_size(text)
    if size is None:
        raise ValueError(f"invalid size: {value!r}")
    return size


def parse_age_option(value: str | None, *, now: float | None = None) -> float | None:
    """Parse ``--newer-than 7d`` / ``--older-than 24h`` into an mtime bound."""
    if value is None:
        return None
    from .util import parse_duration

    text = str(value).strip()
    if not text:
        return None
    seconds = parse_duration(text)
    if seconds is None:
        raise ValueError(f"invalid age: {value!r}")
    reference = now if now is not None else time.time()
    return reference - seconds
