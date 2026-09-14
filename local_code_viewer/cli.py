"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from .app import serve
from .config import (
    DEFAULT_MAX_BYTES,
    DEFAULT_PORT,
    DEFAULT_LINK_PREFIX,
    ConfigError,
    Configuration,
    build_mappings,
)


def _port(raw: str) -> int:
    value = int(raw)
    if not 1 <= value <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return value


def _max_bytes(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("maximum size must be a positive number of bytes")
    return value


def _map_argument(raw: str) -> tuple[str, str]:
    link_prefix, separator, actual_root = raw.partition("=")
    if not separator or not link_prefix or not actual_root:
        raise argparse.ArgumentTypeError(
            "expected LINK_PREFIX=ACTUAL_ROOT, for example /container/project=/host/project"
        )
    return link_prefix, actual_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="local-code-viewer",
        description=(
            "Serve local source files over loopback HTTP with syntax highlighting "
            "and #L<n> line anchors. Read-only."
        ),
    )
    parser.add_argument(
        "--port",
        type=_port,
        default=DEFAULT_PORT,
        metavar="PORT",
        help=f"loopback port to listen on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "directory that may be served; links use the same path. Repeatable. "
            f"Defaults to {DEFAULT_LINK_PREFIX}, which allows every readable file."
        ),
    )
    parser.add_argument(
        "--map",
        action="append",
        default=[],
        dest="maps",
        type=_map_argument,
        metavar="LINK_PREFIX=ACTUAL_ROOT",
        help=(
            "translate a path prefix used in links to a directory on this host, "
            "for example /container/project=/home/you/project. Repeatable."
        ),
    )
    parser.add_argument(
        "--max-bytes",
        type=_max_bytes,
        default=DEFAULT_MAX_BYTES,
        metavar="SIZE",
        help=f"largest file to serve, in bytes (default: {DEFAULT_MAX_BYTES})",
    )
    return parser


def build_configuration(arguments: argparse.Namespace, *, cwd: str) -> Configuration:
    """Turn parsed arguments into a validated configuration."""
    return Configuration(
        mappings=build_mappings(cwd=cwd, roots=arguments.root, maps=arguments.maps),
        port=arguments.port,
        max_bytes=arguments.max_bytes,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        configuration = build_configuration(arguments, cwd=os.getcwd())
    except ConfigError as exc:
        print(f"local-code-viewer: {exc}", file=sys.stderr)
        return 2
    return serve(configuration)


def entry_point() -> None:
    raise SystemExit(main())
