"""Tests for request-path authorization."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from local_code_viewer.config import Mapping
from local_code_viewer.resolve import Outcome, resolve_request

MAX_BYTES = 4096


class ResolveRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name).resolve()
        self.nested = self.root / "lib"
        self.nested.mkdir()
        self.source = self.nested / "example.ex"
        self.source.write_text("defmodule Example do\nend\n", encoding="utf-8")
        self.outside = self.root.parent / f"{self.root.name}-outside"
        self.outside.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, self.outside, ignore_errors=True)
        self.mappings = (Mapping(link_prefix=str(self.root), actual_root=self.root),)

    def resolve(self, path: str):
        return resolve_request(path, self.mappings, MAX_BYTES)

    def test_nested_file_is_authorized(self) -> None:
        result = self.resolve(str(self.source))

        self.assertEqual(result.outcome, Outcome.OK)
        self.assertEqual(result.file.path, self.source.resolve())

    def test_file_directly_inside_the_root_is_authorized(self) -> None:
        direct = self.root / "top.py"
        direct.write_text("x\n", encoding="utf-8")

        self.assertEqual(self.resolve(str(direct)).outcome, Outcome.OK)

    def test_relative_path_is_a_bad_request(self) -> None:
        self.assertEqual(self.resolve("lib/example.ex").outcome, Outcome.BAD_REQUEST)

    def test_empty_path_is_a_bad_request(self) -> None:
        self.assertEqual(self.resolve("").outcome, Outcome.BAD_REQUEST)

    def test_nul_byte_is_a_bad_request(self) -> None:
        self.assertEqual(
            self.resolve(str(self.source) + "\x00").outcome, Outcome.BAD_REQUEST
        )

    def test_path_outside_every_root_is_forbidden(self) -> None:
        self.assertEqual(
            self.resolve(str(self.outside / "x.ex")).outcome, Outcome.FORBIDDEN
        )

    def test_dotdot_traversal_is_forbidden(self) -> None:
        escaped = str(self.root / ".." / "etc" / "passwd")

        self.assertEqual(self.resolve(escaped).outcome, Outcome.FORBIDDEN)

    def test_internal_dotdot_that_stays_inside_the_root_is_allowed(self) -> None:
        internal = str(self.root / "lib" / ".." / "lib" / "example.ex")

        self.assertEqual(self.resolve(internal).outcome, Outcome.OK)

    def test_missing_file_is_not_found(self) -> None:
        self.assertEqual(
            self.resolve(str(self.root / "absent.ex")).outcome, Outcome.NOT_FOUND
        )

    def test_directory_is_a_bad_request(self) -> None:
        self.assertEqual(self.resolve(str(self.nested)).outcome, Outcome.BAD_REQUEST)

    def test_root_itself_is_a_bad_request(self) -> None:
        self.assertEqual(self.resolve(str(self.root)).outcome, Outcome.BAD_REQUEST)

    def test_size_exactly_at_the_limit_is_allowed(self) -> None:
        at_limit = self.root / "limit.txt"
        at_limit.write_bytes(b"a" * MAX_BYTES)

        self.assertEqual(self.resolve(str(at_limit)).outcome, Outcome.OK)

    def test_size_one_byte_over_the_limit_is_rejected(self) -> None:
        over = self.root / "over.txt"
        over.write_bytes(b"a" * (MAX_BYTES + 1))

        self.assertEqual(self.resolve(str(over)).outcome, Outcome.TOO_LARGE)

    def test_translated_mapping_is_resolved(self) -> None:
        target = self.root / "host" / "app.ts"
        target.parent.mkdir()
        target.write_text("const x = 1;\n", encoding="utf-8")
        mappings = (
            Mapping(link_prefix="/container/project", actual_root=self.root / "host"),
        )

        result = resolve_request("/container/project/app.ts", mappings, MAX_BYTES)

        self.assertEqual(result.outcome, Outcome.OK)
        self.assertEqual(result.file.path, target.resolve())

    def test_translated_mapping_blocks_escape(self) -> None:
        mappings = (
            Mapping(link_prefix="/container/project", actual_root=self.root / "host"),
        )

        result = resolve_request("/container/project/../example.ex", mappings, MAX_BYTES)

        self.assertEqual(result.outcome, Outcome.FORBIDDEN)

    def test_sibling_prefix_is_not_matched(self) -> None:
        mappings = (Mapping(link_prefix="/container/project", actual_root=self.root),)

        result = resolve_request("/container/project-other/x.ex", mappings, MAX_BYTES)

        self.assertEqual(result.outcome, Outcome.FORBIDDEN)


class SymlinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name).resolve()
        self.outside = self.root.parent / f"{self.root.name}-outside"
        self.outside.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, self.outside, ignore_errors=True)
        self.mappings = (Mapping(link_prefix=str(self.root), actual_root=self.root),)

    def _symlink(self, link: Path, target: Path) -> None:
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"this filesystem cannot create symlinks: {exc}")

    def test_symlink_inside_the_root_is_allowed(self) -> None:
        real = self.root / "real.ex"
        real.write_text("x = 1\n", encoding="utf-8")
        self._symlink(self.root / "link.ex", real)

        result = resolve_request(str(self.root / "link.ex"), self.mappings, MAX_BYTES)

        self.assertEqual(result.outcome, Outcome.OK)
        self.assertEqual(result.file.path, real.resolve())

    def test_symlinked_directory_inside_the_root_is_allowed(self) -> None:
        real = self.root / "packages"
        real.mkdir()
        (real / "a.py").write_text("x = 1\n", encoding="utf-8")
        self._symlink(self.root / "linked", real)

        result = resolve_request(str(self.root / "linked" / "a.py"), self.mappings, MAX_BYTES)

        self.assertEqual(result.outcome, Outcome.OK)

    def test_symlink_escaping_the_root_is_forbidden(self) -> None:
        secret = self.outside / "secret.txt"
        secret.write_text("secret\n", encoding="utf-8")
        self._symlink(self.root / "escape.txt", secret)

        result = resolve_request(str(self.root / "escape.txt"), self.mappings, MAX_BYTES)

        self.assertEqual(result.outcome, Outcome.FORBIDDEN)

    def test_symlinked_directory_escaping_the_root_is_forbidden(self) -> None:
        self._symlink(self.root / "outside-dir", self.outside)

        result = resolve_request(
            str(self.root / "outside-dir" / "secret.txt"), self.mappings, MAX_BYTES
        )

        self.assertEqual(result.outcome, Outcome.FORBIDDEN)

    def test_traversal_through_an_escaping_symlink_is_forbidden(self) -> None:
        self._symlink(self.root / "outside-dir", self.outside)
        (self.outside / "secret.txt").write_text("secret\n", encoding="utf-8")

        result = resolve_request(
            str(self.root / "outside-dir" / ".." / "secret.txt"), self.mappings, MAX_BYTES
        )

        self.assertEqual(result.outcome, Outcome.FORBIDDEN)


@unittest.skipUnless(os.path.exists("/dev/null"), "requires a POSIX device node")
class SpecialFileTests(unittest.TestCase):
    def test_character_device_is_a_bad_request(self) -> None:
        mappings = (Mapping(link_prefix="/dev", actual_root=Path("/dev")),)

        result = resolve_request("/dev/null", mappings, MAX_BYTES)

        self.assertEqual(result.outcome, Outcome.BAD_REQUEST)


if __name__ == "__main__":
    unittest.main()
