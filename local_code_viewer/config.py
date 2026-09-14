"""Link-facing path prefixes bound to canonical filesystem directories."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PORT = 8765
DEFAULT_MAX_BYTES = 20 * 1024 * 1024

# The default root is the filesystem root, so links that carry any absolute path
# work without configuration. Use --root to narrow it.
DEFAULT_LINK_PREFIX = os.path.abspath(os.sep)


class ConfigError(Exception):
    """Raised when the viewer is configured with an unusable mapping."""


@dataclass(frozen=True)
class Mapping:
    """A link-facing path prefix and the canonical directory it resolves to.

    ``link_prefix`` is expressed in the same namespace as generator-supplied
    links and is normalized lexically, so it need not exist on this filesystem.
    ``actual_root`` is a canonical directory on this filesystem.
    """

    link_prefix: str
    actual_root: Path

    @property
    def is_identity(self) -> bool:
        return self.link_prefix == str(self.actual_root)

    def relative_path(self, requested: str) -> str | None:
        """Return ``requested`` relative to the prefix, or None when unmatched.

        Matching is path-component aware: a prefix of ``/project`` never
        matches ``/project-other``.
        """
        if requested == self.link_prefix:
            return ""
        prefix = self.link_prefix.rstrip(os.sep) + os.sep
        if requested.startswith(prefix):
            return requested[len(prefix) :]
        return None


@dataclass(frozen=True)
class LexerOverride:
    """A shell-style filename pattern bound to a Pygments lexer alias."""

    pattern: str
    alias: str


@dataclass(frozen=True)
class Configuration:
    """Everything the server needs in order to authorize and render requests."""

    mappings: tuple[Mapping, ...]
    port: int
    max_bytes: int
    lexer_overrides: tuple[LexerOverride, ...] = ()


def normalize_link_prefix(raw: str, *, cwd: str, resolve_relative: bool) -> str:
    """Return a normalized absolute link prefix.

    Relative input is resolved against ``cwd`` only when ``resolve_relative``
    is set; otherwise it is rejected, because links always carry absolute paths.
    """
    if not raw:
        raise ConfigError("empty path")
    if "\x00" in raw:
        raise ConfigError(f"path contains a NUL character: {raw!r}")
    if os.path.isabs(raw):
        candidate = raw
    elif resolve_relative:
        candidate = os.path.join(cwd, raw)
    else:
        raise ConfigError(f"link prefix must be absolute: {raw!r}")
    return os.path.normpath(candidate)


def canonical_directory(raw: str, *, cwd: str, label: str) -> Path:
    """Resolve ``raw`` to an existing directory, or raise :class:`ConfigError`."""
    candidate = raw if os.path.isabs(raw) else os.path.join(cwd, raw)
    try:
        resolved = Path(candidate).resolve(strict=True)
    except OSError as exc:
        raise ConfigError(f"{label}: cannot resolve {raw!r}: {exc}") from exc
    if not resolved.is_dir():
        raise ConfigError(f"{label}: {raw!r} is not a directory")
    return resolved


def _reject_duplicate_prefixes(mappings: Sequence[Mapping]) -> None:
    seen: set[str] = set()
    for mapping in mappings:
        if mapping.link_prefix in seen:
            raise ConfigError(f"duplicate link prefix: {mapping.link_prefix}")
        seen.add(mapping.link_prefix)


def build_mappings(
    *,
    cwd: str,
    roots: Sequence[str] = (),
    maps: Sequence[tuple[str, str]] = (),
) -> tuple[Mapping, ...]:
    """Build the active mappings from command-line input.

    With no ``roots`` and no ``maps``, the filesystem root becomes the single
    allowed root. Supplying either replaces that default.
    """
    if not roots and not maps:
        return (
            Mapping(
                link_prefix=DEFAULT_LINK_PREFIX,
                actual_root=canonical_directory(
                    DEFAULT_LINK_PREFIX, cwd=cwd, label="default root"
                ),
            ),
        )

    mappings: list[Mapping] = []
    for raw in roots:
        mappings.append(
            Mapping(
                link_prefix=normalize_link_prefix(raw, cwd=cwd, resolve_relative=True),
                actual_root=canonical_directory(raw, cwd=cwd, label=f"--root {raw!r}"),
            )
        )
    for link_raw, actual_raw in maps:
        mappings.append(
            Mapping(
                link_prefix=normalize_link_prefix(link_raw, cwd=cwd, resolve_relative=False),
                actual_root=canonical_directory(
                    actual_raw, cwd=cwd, label=f"--map {link_raw!r}"
                ),
            )
        )
    _reject_duplicate_prefixes(mappings)
    return tuple(mappings)


def find_mapping(
    mappings: Sequence[Mapping], requested: str
) -> tuple[Mapping | None, str | None]:
    """Return the most specific mapping for ``requested`` and its relative path."""
    best: Mapping | None = None
    best_relative: str | None = None
    for mapping in mappings:
        relative = mapping.relative_path(requested)
        if relative is None:
            continue
        if best is None or len(mapping.link_prefix) > len(best.link_prefix):
            best = mapping
            best_relative = relative
    return best, best_relative


def is_within(child: Path, root: Path) -> bool:
    """Report whether canonical ``child`` lies inside canonical ``root``."""
    try:
        child.relative_to(root)
    except ValueError:
        return False
    return True
