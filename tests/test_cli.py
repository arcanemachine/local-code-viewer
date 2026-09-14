"""Tests for command-line parsing and configuration assembly."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from local_code_viewer.cli import build_configuration, build_parser, main
from local_code_viewer.config import (
    DEFAULT_LINK_PREFIX,
    DEFAULT_MAX_BYTES,
    DEFAULT_PORT,
)


class ParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name).resolve()
        self.cwd = str(self.root)

    def parse(self, *argv: str):
        return build_parser().parse_args(list(argv))

    def configuration(self, *argv: str):
        return build_configuration(self.parse(*argv), cwd=self.cwd)

    def test_defaults_are_applied(self) -> None:
        configuration = self.configuration()

        self.assertEqual(configuration.port, DEFAULT_PORT)
        self.assertEqual(configuration.max_bytes, DEFAULT_MAX_BYTES)
        self.assertEqual(len(configuration.mappings), 1)
        self.assertEqual(configuration.mappings[0].link_prefix, DEFAULT_LINK_PREFIX)

    def test_roots_and_maps_are_both_accepted(self) -> None:
        nested = self.root / "nested"
        nested.mkdir()

        configuration = self.configuration(
            "--root", str(nested), "--map", f"/container/project={nested}"
        )

        self.assertEqual(
            [m.link_prefix for m in configuration.mappings],
            [str(nested), "/container/project"],
        )

    def test_map_splits_on_the_first_equals_sign(self) -> None:
        nested = self.root / "nested=name"
        nested.mkdir()

        configuration = self.configuration("--map", f"/container/project={nested}")

        self.assertEqual(configuration.mappings[0].link_prefix, "/container/project")
        self.assertEqual(configuration.mappings[0].actual_root, nested)

    def test_port_is_configurable(self) -> None:
        self.assertEqual(self.configuration("--port", "9001").port, 9001)

    def test_max_bytes_is_configurable(self) -> None:
        self.assertEqual(self.configuration("--max-bytes", "128").max_bytes, 128)

    def test_invalid_port_is_rejected(self) -> None:
        for value in ("0", "65536", "-1", "http"):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.parse("--port", value)

    def test_invalid_max_bytes_is_rejected(self) -> None:
        for value in ("0", "-5", "huge"):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.parse("--max-bytes", value)

    def test_map_without_a_separator_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                self.parse("--map", "/container/project")

    def test_map_with_an_empty_side_is_rejected(self) -> None:
        for value in ("=/host/path", "/container/project="):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.parse("--map", value)


class MainTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name).resolve()

    def test_missing_root_reports_an_error_and_exits_nonzero(self) -> None:
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            status = main(["--root", str(self.root / "absent")])

        self.assertEqual(status, 2)
        self.assertIn("absent", stderr.getvalue())

    def test_invalid_map_link_prefix_reports_an_error(self) -> None:
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            status = main(["--map", f"relative/path={self.root}"])

        self.assertEqual(status, 2)
        self.assertIn("absolute", stderr.getvalue())

    def test_occupied_port_reports_an_error(self) -> None:
        import socket

        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen(1)
            port = blocker.getsockname()[1]
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                status = main(["--root", str(self.root), "--port", str(port)])

        self.assertEqual(status, 1)
        self.assertIn("cannot listen", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
