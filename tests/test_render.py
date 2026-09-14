"""Tests for highlighting, escaping, and line anchors."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from html.parser import HTMLParser

from local_code_viewer.config import LexerOverride, Mapping
from local_code_viewer.render import (
    EXTENSION_LEXERS,
    LineAnchorFormatter,
    lexer_for,
    render_error,
    render_help,
    render_source,
    validate_alias,
)

LINE_ID = re.compile(r'<span class="line" id="(L\d+)">')
GUTTER_LINK = re.compile(r'<a class="line-number" href="#(L\d+)">(\d+)</a>')
EXTERNAL_REFERENCE = re.compile(r'(?:src|href)="https?://')

PYTHON_SOURCE = "def add(a, b):\n    return a + b\n"


VOID_ELEMENTS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
     "meta", "source", "track", "wbr"}
)
PHASING_ONLY_IN_PRE = frozenset({"a", "span", "code", "em", "strong", "b", "i", "wbr"})


class _StructureChecker(HTMLParser):
    """Collect tag-balance errors and the element names found inside <pre>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.pre_depth = 0
        self.pre_children: set[str] = set()

    def handle_starttag(self, tag: str, attrs) -> None:
        if self.pre_depth:
            self.pre_children.add(tag)
        if tag == "pre":
            self.pre_depth += 1
        if tag not in VOID_ELEMENTS:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre":
            self.pre_depth = max(0, self.pre_depth - 1)
        if not self.stack:
            self.errors.append(f"unexpected closing tag </{tag}>")
            return
        if self.stack[-1] != tag:
            self.errors.append(f"</{tag}> closed while <{self.stack[-1]}> was open")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
            return
        self.stack.pop()


def check_structure(document: str) -> _StructureChecker:
    checker = _StructureChecker()
    checker.feed(document)
    checker.close()
    return checker


def anchors(document: str) -> list[str]:
    return LINE_ID.findall(document)


class LexerSelectionTests(unittest.TestCase):
    def test_required_extensions_map_to_their_lexers(self) -> None:
        expected = {
            ".ex": "ElixirLexer",
            ".exs": "ElixirLexer",
            ".py": "PythonLexer",
            ".ts": "TypeScriptLexer",
        }

        for extension, lexer_name in expected.items():
            with self.subTest(extension=extension):
                self.assertEqual(type(lexer_for(f"file{extension}")).__name__, lexer_name)

    def test_extension_matching_is_case_insensitive(self) -> None:
        self.assertEqual(type(lexer_for("FILE.PY")).__name__, "PythonLexer")

    def test_unknown_extension_falls_back_to_plain_text(self) -> None:
        self.assertEqual(type(lexer_for("file.zzz")).__name__, "TextLexer")

    def test_every_declared_extension_has_a_mapping(self) -> None:
        self.assertEqual(set(EXTENSION_LEXERS), {".ex", ".exs", ".py", ".ts"})


class FilenameDetectionTests(unittest.TestCase):
    def test_common_extensions_are_detected(self) -> None:
        expected = {
            "main.go": "GoLexer",
            "main.rs": "RustLexer",
            "app.rb": "RubyLexer",
            "main.c": "CLexer",
            "index.tsx": "TsxLexer",
            "data.json": "JsonLexer",
        }

        for filename, lexer_name in expected.items():
            with self.subTest(filename=filename):
                self.assertEqual(type(lexer_for(filename)).__name__, lexer_name)

    def test_special_filenames_are_detected(self) -> None:
        for filename, lexer_name in {"Dockerfile": "DockerLexer", "Makefile": "MakefileLexer"}.items():
            with self.subTest(filename=filename):
                self.assertEqual(type(lexer_for(filename)).__name__, lexer_name)

    def test_verified_extensions_still_win(self) -> None:
        self.assertEqual(type(lexer_for("demo.exs")).__name__, "ElixirLexer")

    def test_unrecognised_filename_falls_back_to_plain_text(self) -> None:
        self.assertEqual(type(lexer_for("mystery.zzz")).__name__, "TextLexer")


class LexerOverrideTests(unittest.TestCase):
    def test_override_wins_over_detection(self) -> None:
        overrides = (LexerOverride(pattern="*.py", alias="ruby"),)

        self.assertEqual(type(lexer_for("script.py", overrides)).__name__, "RubyLexer")

    def test_override_wins_over_a_verified_mapping(self) -> None:
        overrides = (LexerOverride(pattern="*.ex", alias="python"),)

        self.assertEqual(type(lexer_for("module.ex", overrides)).__name__, "PythonLexer")

    def test_last_matching_override_wins(self) -> None:
        overrides = (
            LexerOverride(pattern="*.foo", alias="ruby"),
            LexerOverride(pattern="special.foo", alias="go"),
        )

        self.assertEqual(type(lexer_for("special.foo", overrides)).__name__, "GoLexer")
        self.assertEqual(type(lexer_for("other.foo", overrides)).__name__, "RubyLexer")

    def test_non_matching_override_leaves_detection_alone(self) -> None:
        overrides = (LexerOverride(pattern="*.nope", alias="ruby"),)

        self.assertEqual(type(lexer_for("script.py", overrides)).__name__, "PythonLexer")

    def test_pattern_is_matched_against_the_filename_only(self) -> None:
        overrides = (LexerOverride(pattern="lib/*.py", alias="ruby"),)

        self.assertEqual(type(lexer_for("/root/lib/a.py", overrides)).__name__, "PythonLexer")

    def test_pattern_matching_is_case_sensitive(self) -> None:
        overrides = (LexerOverride(pattern="*.PY", alias="ruby"),)

        self.assertEqual(type(lexer_for("a.py", overrides)).__name__, "PythonLexer")

    def test_override_survives_the_lexer_options(self) -> None:
        overrides = (LexerOverride(pattern="*.foo", alias="ruby"),)
        lexer = lexer_for("a.foo", overrides)

        self.assertFalse(lexer.stripnl)
        self.assertFalse(lexer.ensurenl)
        self.assertEqual(lexer.tabsize, 0)

    def test_known_alias_validates(self) -> None:
        validate_alias("elixir")

    def test_unknown_alias_raises(self) -> None:
        with self.assertRaises(ValueError):
            validate_alias("nosuchlexer")

    def test_render_source_applies_an_override(self) -> None:
        document = render_source(
            link_path="/root/thing.zzz",
            filename="thing.zzz",
            source="def f():\n    return 1\n",
            overrides=(LexerOverride(pattern="*.zzz", alias="python"),),
        )

        self.assertIn('class="k"', document)

    def test_render_source_without_overrides_stays_plain(self) -> None:
        document = render_source(
            link_path="/root/thing.zzz",
            filename="thing.zzz",
            source="def f():\n    return 1\n",
        )

        self.assertNotIn('class="k"', document)
        self.assertIn('id="L1"', document)


class LineAnchorTests(unittest.TestCase):
    def render(self, source: str, name: str = "file.py") -> str:
        return render_source(link_path=f"/root/{name}", filename=name, source=source)

    def test_first_anchor_is_l1(self) -> None:
        self.assertEqual(anchors(self.render(PYTHON_SOURCE))[0], "L1")

    def test_anchors_are_sequential_and_unique(self) -> None:
        source = "".join(f"value_{index} = {index}\n" for index in range(1, 151))

        found = anchors(self.render(source))

        self.assertEqual(found, [f"L{index}" for index in range(1, 151)])

    def test_line_123_is_addressable(self) -> None:
        source = "".join(f"value_{index} = {index}\n" for index in range(1, 201))

        self.assertIn('id="L123"', self.render(source))

    def test_anchors_are_one_based(self) -> None:
        found = anchors(self.render("only_one_line\n"))

        self.assertEqual(found, ["L1"])

    def test_trailing_newline_does_not_add_a_phantom_line(self) -> None:
        self.assertEqual(len(anchors(self.render("a = 1\nb = 2\n"))), 2)

    def test_missing_trailing_newline_keeps_the_last_line(self) -> None:
        self.assertEqual(len(anchors(self.render("a = 1\nb = 2"))), 2)

    def test_trailing_blank_lines_are_counted(self) -> None:
        self.assertEqual(len(anchors(self.render("a = 1\n\n\n\n"))), 4)

    def test_interior_blank_line_keeps_numbering(self) -> None:
        document = self.render("a = 1\n\nb = 2\n")

        self.assertEqual(anchors(document), ["L1", "L2", "L3"])

    def test_whitespace_only_line_is_preserved(self) -> None:
        self.assertEqual(len(anchors(self.render("a\n   \nb\n"))), 3)

    def test_empty_file_has_no_lines(self) -> None:
        self.assertEqual(anchors(self.render("")), [])

    def test_lone_newline_is_one_line(self) -> None:
        self.assertEqual(anchors(self.render("\n")), ["L1"])

    def test_every_gutter_number_links_to_its_own_anchor(self) -> None:
        document = self.render("a = 1\nb = 2\nc = 3\n")

        links = GUTTER_LINK.findall(document)

        self.assertEqual(links, [("L1", "1"), ("L2", "2"), ("L3", "3")])
        self.assertEqual([anchor for anchor, _ in links], anchors(document))

    def test_target_styling_is_present(self) -> None:
        self.assertIn(".line:target", self.render(PYTHON_SOURCE))

    def test_scroll_margin_keeps_the_target_below_the_header(self) -> None:
        self.assertIn("scroll-margin-top", self.render(PYTHON_SOURCE))


class HighlightingTests(unittest.TestCase):
    def render(self, source: str, name: str) -> str:
        return render_source(link_path=f"/root/{name}", filename=name, source=source)

    def test_elixir_source_is_highlighted(self) -> None:
        document = self.render("defmodule M do\n  def f, do: :ok\nend\n", "a.ex")

        self.assertIn('class="kd"', document)
        self.assertIn("defmodule", document)

    def test_python_source_is_highlighted(self) -> None:
        document = self.render("def f():\n    return 1\n", "a.py")

        self.assertIn('class="k"', document)

    def test_typescript_source_is_highlighted(self) -> None:
        document = self.render("const x: number = 1;\n", "a.ts")

        self.assertIn('class="kd"', document)

    def test_multiline_string_keeps_one_anchor_per_line(self) -> None:
        document = self.render('text = """one\ntwo\nthree"""\n', "a.py")

        self.assertEqual(anchors(document), ["L1", "L2", "L3"])

    def test_blank_lines_inside_a_multiline_string_survive(self) -> None:
        document = self.render('text = """one\n\ntwo"""\n', "a.py")

        self.assertEqual(anchors(document), ["L1", "L2", "L3"])

    def test_plain_text_fallback_is_still_numbered(self) -> None:
        document = self.render("no lexer for this\nsecond line\n", "a.zzz")

        self.assertEqual(anchors(document), ["L1", "L2"])
        self.assertIn("second line", document)

    def test_tabs_are_preserved(self) -> None:
        document = self.render("def f():\n\treturn 1\n", "a.py")

        self.assertIn("\t", document)


class EscapingTests(unittest.TestCase):
    def render(self, source: str, name: str = "a.py") -> str:
        return render_source(link_path=f"/root/{name}", filename=name, source=source)

    def test_script_content_is_escaped(self) -> None:
        document = self.render('value = "<script>alert(1)</script>"\n')

        self.assertNotIn("<script>", document)
        self.assertIn("script", document)

    def test_closing_style_tag_is_escaped(self) -> None:
        document = self.render('value = "</style><b>bold</b>"\n')

        self.assertNotIn("</style><b>", document)
        self.assertNotIn("<b>bold</b>", document)

    def test_ampersands_are_escaped(self) -> None:
        document = self.render('value = "a && b"\n')

        self.assertIn("&amp;", document)

    def test_document_contains_no_script_element(self) -> None:
        document = self.render('value = "x"\n')

        self.assertNotIn("<script", document.lower())

    def test_document_references_no_external_resources(self) -> None:
        document = self.render('value = "x"\n')

        self.assertIsNone(EXTERNAL_REFERENCE.search(document))

    def test_document_declares_utf8(self) -> None:
        self.assertIn('<meta charset="utf-8">', self.render("x = 1\n"))

    def test_non_ascii_source_is_served_directly(self) -> None:
        document = self.render('value = "héllo → wörld"\n')

        self.assertIn("héllo → wörld", document)

    def test_filename_is_escaped_in_the_title(self) -> None:
        document = render_source(
            link_path="/root/<script>.py", filename="<script>.py", source="x = 1\n"
        )

        self.assertNotIn("<title><script>", document)
        self.assertIn("&lt;script&gt;.py", document)

    def test_header_shows_the_requested_link_path(self) -> None:
        document = render_source(
            link_path="/container/project/lib/a.ex", filename="a.ex", source="x = 1\n"
        )

        self.assertIn("/container/project/lib/a.ex", document)


class DocumentStructureTests(unittest.TestCase):
    def render(self, source: str, name: str = "a.py") -> str:
        return render_source(link_path=f"/root/{name}", filename=name, source=source)

    def test_document_tags_balance(self) -> None:
        checker = check_structure(self.render(PYTHON_SOURCE))

        self.assertEqual(checker.errors, [])
        self.assertEqual(checker.stack, [])

    def test_error_page_tags_balance(self) -> None:
        checker = check_structure(render_error(403, "Forbidden", "Nope."))

        self.assertEqual(checker.errors, [])
        self.assertEqual(checker.stack, [])

    def test_help_page_tags_balance(self) -> None:
        mappings = (Mapping(link_prefix="/root", actual_root=Path("/tmp")),)
        checker = check_structure(render_help(mappings, 8765))

        self.assertEqual(checker.errors, [])
        self.assertEqual(checker.stack, [])

    def test_pre_contains_only_phrasing_content(self) -> None:
        checker = check_structure(self.render(PYTHON_SOURCE))

        self.assertTrue(checker.pre_children)
        self.assertLessEqual(checker.pre_children, PHASING_ONLY_IN_PRE)

    def test_no_block_element_wraps_a_line(self) -> None:
        checker = check_structure(self.render(PYTHON_SOURCE))

        self.assertNotIn("div", checker.pre_children)


class HelpAndErrorPagesTests(unittest.TestCase):
    def test_help_page_lists_configured_prefixes(self) -> None:
        mappings = (Mapping(link_prefix="/container/project", actual_root=Path("/tmp")),)

        document = render_help(mappings, 8765)

        self.assertIn("/container/project", document)
        self.assertIn("http://127.0.0.1:8765", document)
        self.assertNotIn("/tmp", document)

    def test_error_page_reports_the_status(self) -> None:
        document = render_error(403, "Forbidden", "Nope.")

        self.assertIn("403", document)
        self.assertIn("Forbidden", document)
        self.assertIn("Nope.", document)

    def test_error_page_has_no_script_element(self) -> None:
        document = render_error(500, "Internal error", "Failed.")

        self.assertNotIn("<script", document.lower())


class FormatterTests(unittest.TestCase):
    def test_formatter_does_not_alter_line_numbers(self) -> None:
        from pygments import highlight

        document = highlight("a = 1\nb = 2\n", lexer_for("a.py"), LineAnchorFormatter())

        self.assertEqual(anchors(document), ["L1", "L2"])


if __name__ == "__main__":
    unittest.main()
