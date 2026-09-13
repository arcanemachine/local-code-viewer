"""Tests for configuration and link-prefix mapping."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from local_code_viewer.config import (
    ConfigError,
    Mapping,
    build_mappings,
    find_mapping,
    is_within,
    normalize_link_prefix,
)


class BuildMappingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name).resolve()
        self.other = self.root / "other"
        self.other.mkdir()
        self.cwd = str(self.root)

    def test_default_mapping_uses_startup_directory(self) -> None:
        mappings = build_mappings(cwd=self.cwd)

        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0].link_prefix, str(self.root))
        self.assertEqual(mappings[0].actual_root, self.root)
        self.assertTrue(mappings[0].is_identity)

    def test_explicit_root_replaces_the_default(self) -> None:
        mappings = build_mappings(cwd=self.cwd, roots=[str(self.other)])

        self.assertEqual([m.link_prefix for m in mappings], [str(self.other)])

    def test_relative_root_resolves_against_the_startup_directory(self) -> None:
        mappings = build_mappings(cwd=self.cwd, roots=["other"])

        self.assertEqual(mappings[0].link_prefix, str(self.other))

    def test_multiple_roots_are_all_active(self) -> None:
        third = self.root / "third"
        third.mkdir()

        mappings = build_mappings(cwd=self.cwd, roots=[str(self.other), str(third)])

        self.assertEqual([m.link_prefix for m in mappings], [str(self.other), str(third)])

    def test_missing_root_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            build_mappings(cwd=self.cwd, roots=[str(self.root / "absent")])

    def test_file_root_is_rejected(self) -> None:
        target = self.root / "file.txt"
        target.write_text("x\n", encoding="utf-8")

        with self.assertRaises(ConfigError):
            build_mappings(cwd=self.cwd, roots=[str(target)])

    def test_translated_mapping_is_accepted(self) -> None:
        mappings = build_mappings(
            cwd=self.cwd, maps=[("/container/project", str(self.other))]
        )

        self.assertEqual(mappings[0].link_prefix, "/container/project")
        self.assertEqual(mappings[0].actual_root, self.other)
        self.assertFalse(mappings[0].is_identity)

    def test_link_prefix_need_not_exist_on_this_filesystem(self) -> None:
        mappings = build_mappings(
            cwd=self.cwd, maps=[("/nowhere/at/all", str(self.other))]
        )

        self.assertEqual(mappings[0].link_prefix, "/nowhere/at/all")

    def test_relative_map_link_prefix_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            build_mappings(cwd=self.cwd, maps=[("relative/path", str(self.other))])

    def test_duplicate_link_prefixes_are_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            build_mappings(cwd=self.cwd, roots=[str(self.other), str(self.other)])

    def test_duplicate_between_root_and_map_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            build_mappings(
                cwd=self.cwd,
                roots=[str(self.other)],
                maps=[(str(self.other), str(self.other))],
            )

    def test_roots_and_maps_combine(self) -> None:
        mappings = build_mappings(
            cwd=self.cwd, roots=[str(self.other)], maps=[("/container", str(self.other))]
        )

        self.assertEqual(len(mappings), 2)

    def test_nul_in_link_prefix_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            normalize_link_prefix("bad\x00path", cwd=self.cwd, resolve_relative=True)

    def test_empty_link_prefix_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            normalize_link_prefix("", cwd=self.cwd, resolve_relative=True)


class FindMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("/srv/project")
        self.vendor = self.root / "vendor"

    @property
    def mappings(self) -> tuple[Mapping, ...]:
        return (
            Mapping(link_prefix="/srv/project", actual_root=self.root),
            Mapping(link_prefix="/srv/project/vendor", actual_root=self.vendor),
        )

    def test_longest_prefix_wins(self) -> None:
        mapping, relative = find_mapping(self.mappings, "/srv/project/vendor/lib/a.js")

        self.assertIsNotNone(mapping)
        self.assertEqual(mapping.link_prefix, "/srv/project/vendor")
        self.assertEqual(relative, "lib/a.js")

    def test_shorter_prefix_handles_the_remaining_paths(self) -> None:
        mapping, relative = find_mapping(self.mappings, "/srv/project/lib/a.ex")

        self.assertIsNotNone(mapping)
        self.assertEqual(mapping.link_prefix, "/srv/project")
        self.assertEqual(relative, "lib/a.ex")

    def test_prefix_match_is_component_aware(self) -> None:
        mapping, _ = find_mapping(self.mappings, "/srv/project-other/lib/a.ex")

        self.assertIsNone(mapping)

    def test_path_equal_to_the_prefix_has_an_empty_relative_path(self) -> None:
        mapping, relative = find_mapping(self.mappings, "/srv/project/vendor")

        self.assertIsNotNone(mapping)
        self.assertEqual(relative, "")

    def test_unmatched_path_returns_nothing(self) -> None:
        mapping, _ = find_mapping(self.mappings, "/etc/passwd")

        self.assertIsNone(mapping)

    def test_filesystem_root_prefix_matches(self) -> None:
        mappings = (Mapping(link_prefix=os.sep, actual_root=Path(os.sep)),)

        mapping, relative = find_mapping(mappings, "/etc/hosts")

        self.assertIsNotNone(mapping)
        self.assertEqual(relative, "etc/hosts")


class IsWithinTests(unittest.TestCase):
    def test_descendant_is_within(self) -> None:
        self.assertTrue(is_within(Path("/a/b/c"), Path("/a/b")))

    def test_sibling_is_not_within(self) -> None:
        self.assertFalse(is_within(Path("/a/bc"), Path("/a/b")))

    def test_parent_is_not_within(self) -> None:
        self.assertFalse(is_within(Path("/a"), Path("/a/b")))


if __name__ == "__main__":
    unittest.main()
