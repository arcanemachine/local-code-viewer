"""Authorization of requested link paths against the configured mappings."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .config import Mapping, find_mapping, is_within


class Outcome(Enum):
    """The result of authorizing one requested path."""

    OK = "ok"
    BAD_REQUEST = "bad_request"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    TOO_LARGE = "too_large"


@dataclass(frozen=True)
class ResolvedFile:
    """An authorized regular file."""

    link_path: str
    path: Path
    size: int
    mapping: Mapping


@dataclass(frozen=True)
class Resolution:
    """An authorization outcome, with the file when the outcome is OK."""

    outcome: Outcome
    detail: str = ""
    file: ResolvedFile | None = None


def _reject(outcome: Outcome, detail: str) -> Resolution:
    return Resolution(outcome=outcome, detail=detail)


def resolve_request(
    requested: str, mappings: Sequence[Mapping], max_bytes: int
) -> Resolution:
    """Authorize a link path and return the file it may be served from.

    Containment is checked before existence so that a path outside every root
    is refused without revealing whether it exists.
    """
    if not requested:
        return _reject(Outcome.BAD_REQUEST, "no path was supplied")
    if "\x00" in requested:
        return _reject(Outcome.BAD_REQUEST, "the path contains a NUL character")
    if not os.path.isabs(requested):
        return _reject(Outcome.BAD_REQUEST, "the path must be absolute")

    mapping, relative = find_mapping(mappings, requested)
    if mapping is None or relative is None:
        return _reject(Outcome.FORBIDDEN, "the path is not under any configured root")

    candidate = mapping.actual_root / relative if relative else mapping.actual_root
    try:
        provisional = candidate.resolve(strict=False)
    except OSError:
        return _reject(Outcome.BAD_REQUEST, "the path could not be resolved")
    if not is_within(provisional, mapping.actual_root):
        return _reject(Outcome.FORBIDDEN, "the path escapes the configured root")

    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        return _reject(Outcome.NOT_FOUND, "no such file")
    except OSError:
        return _reject(Outcome.BAD_REQUEST, "the path could not be resolved")
    if not is_within(resolved, mapping.actual_root):
        return _reject(Outcome.FORBIDDEN, "the path escapes the configured root")

    try:
        if not resolved.is_file():
            return _reject(Outcome.BAD_REQUEST, "only regular files can be viewed")
        size = resolved.stat().st_size
    except OSError:
        return _reject(Outcome.FORBIDDEN, "the file could not be read")

    if size > max_bytes:
        return _reject(
            Outcome.TOO_LARGE, f"the file is {size} bytes and the limit is {max_bytes}"
        )

    return Resolution(
        outcome=Outcome.OK,
        file=ResolvedFile(
            link_path=requested, path=resolved, size=size, mapping=mapping
        ),
    )
