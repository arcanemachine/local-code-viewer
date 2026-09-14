"""HTTP tests for site mode: a static website plus same-origin code frames."""

from __future__ import annotations

import contextlib
import http.client
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from local_code_viewer.app import ViewerServer
from local_code_viewer.config import SITE_CODE_PREFIX, Configuration, Mapping

MAX_BYTES = 8192

INDEX_HTML = b"<!DOCTYPE html>\n<title>Site</title>\n<h1>Root index</h1>\n"
SUB_INDEX_HTML = b"<!DOCTYPE html>\n<title>Sub</title>\n<h1>Sub index</h1>\n"
STYLE_CSS = b"body { color: red; }\n"
SCRIPT_JS = b"console.log('hi');\n"
BINARY = bytes(range(256))
ELIXIR = "defmodule Demo do\n  @moduledoc \"demo\"\n\n  def f, do: :ok\nend\n"


class SiteTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls._temporary.name).resolve()
        cls.outside = cls.root.parent / f"{cls.root.name}-outside"
        cls.outside.mkdir(exist_ok=True)

        (cls.root / "index.html").write_bytes(INDEX_HTML)
        (cls.root / "style.css").write_bytes(STYLE_CSS)
        (cls.root / "script.js").write_bytes(SCRIPT_JS)
        (cls.root / "data.bin").write_bytes(BINARY)
        (cls.root / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        (cls.root / "big.css").write_bytes(b"a" * (MAX_BYTES + 1))

        sub = cls.root / "sub"
        sub.mkdir()
        (sub / "index.html").write_bytes(SUB_INDEX_HTML)

        listing = cls.root / "noindex"
        listing.mkdir()
        (listing / "note.txt").write_bytes(b"nothing here\n")

        code = cls.root / "code"
        code.mkdir()
        (code / "demo.ex").write_text(ELIXIR, encoding="utf-8")
        (code / "broken.py").write_bytes(b"\xff\xfe\x00not utf-8\n")

        (cls.outside / "secret.txt").write_text("topsecret-contents\n", encoding="utf-8")
        (cls.outside / "secret.html").write_text(
            "<h1>topsecret-contents</h1>\n", encoding="utf-8"
        )

        cls.real_index = cls.root / "real-index.html"
        cls.real_index.write_bytes(b"<!DOCTYPE html>\n<h1>Real index</h1>\n")

        cls.symlink_created = True
        try:
            (cls.root / "escape.txt").symlink_to(cls.outside / "secret.txt")
        except (OSError, NotImplementedError):
            cls.symlink_created = False

        cls.index_symlink_created = True
        try:
            inside = cls.root / "inside-index"
            inside.mkdir()
            (inside / "index.html").symlink_to(cls.real_index)
            outside = cls.root / "outside-index"
            outside.mkdir()
            (outside / "index.html").symlink_to(cls.outside / "secret.html")
        except (OSError, NotImplementedError):
            cls.index_symlink_created = False

        cls.configuration = Configuration(
            mappings=(Mapping(link_prefix=SITE_CODE_PREFIX, actual_root=cls.root),),
            port=0,
            max_bytes=MAX_BYTES,
            site_root=cls.root,
        )
        cls.server, cls.port, cls.thread = cls._start(cls.configuration)

    @classmethod
    def _start(cls, configuration: Configuration):
        server = ViewerServer(configuration)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, server.server_address[1], thread

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls._temporary.cleanup()
        shutil.rmtree(cls.outside, ignore_errors=True)

    def request(
        self, method: str = "GET", path: str = "/", headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def get(self, path: str) -> tuple[int, dict[str, str], bytes]:
        return self.request("GET", path)


class StaticFileTests(SiteTestBase):
    def test_root_serves_the_index_page(self) -> None:
        status, headers, body = self.get("/")

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(body, INDEX_HTML)

    def test_nested_file_is_served(self) -> None:
        status, _, body = self.get("/sub/index.html")

        self.assertEqual(status, 200)
        self.assertEqual(body, SUB_INDEX_HTML)

    def test_directory_without_a_trailing_slash_redirects(self) -> None:
        status, headers, _ = self.get("/sub")

        self.assertEqual(status, 301)
        self.assertEqual(headers["Location"], "/sub/")

    def test_directory_with_a_trailing_slash_serves_its_index(self) -> None:
        status, _, body = self.get("/sub/")

        self.assertEqual(status, 200)
        self.assertEqual(body, SUB_INDEX_HTML)

    def test_directory_without_an_index_is_not_listed(self) -> None:
        status, _, body = self.get("/noindex/")

        self.assertEqual(status, 404)
        self.assertNotIn(b"note.txt", body)

    def test_stylesheet_content_type(self) -> None:
        status, headers, body = self.get("/style.css")

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/css; charset=utf-8")
        self.assertEqual(body, STYLE_CSS)

    def test_script_content_type(self) -> None:
        status, headers, _ = self.get("/script.js")

        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])

    def test_png_content_type(self) -> None:
        status, headers, _ = self.get("/pic.png")

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")

    def test_binary_file_is_served_byte_for_byte(self) -> None:
        status, headers, body = self.get("/data.bin")

        self.assertEqual(status, 200)
        self.assertEqual(body, BINARY)
        self.assertEqual(headers["Content-Type"], "application/octet-stream")

    def test_missing_file_is_not_found(self) -> None:
        status, _, _ = self.get("/absent.html")

        self.assertEqual(status, 404)

    def test_traversal_is_forbidden(self) -> None:
        status, _, body = self.get("/../" + self.outside.name + "/secret.txt")

        self.assertEqual(status, 403)
        self.assertNotIn(b"topsecret-contents", body)

    def test_symlink_escape_is_forbidden(self) -> None:
        if not self.symlink_created:
            self.skipTest("this filesystem cannot create symlinks")

        status, _, body = self.get("/escape.txt")

        self.assertEqual(status, 403)
        self.assertNotIn(b"topsecret-contents", body)

    def test_symlinked_index_inside_the_root_is_allowed(self) -> None:
        if not self.index_symlink_created:
            self.skipTest("this filesystem cannot create symlinks")

        status, _, body = self.get("/inside-index/")

        self.assertEqual(status, 200)
        self.assertEqual(body, self.real_index.read_bytes())

    def test_symlinked_index_escaping_the_root_is_forbidden(self) -> None:
        if not self.index_symlink_created:
            self.skipTest("this filesystem cannot create symlinks")

        status, _, body = self.get("/outside-index/")

        self.assertEqual(status, 403)
        self.assertNotIn(b"topsecret-contents", body)

    def test_symlinked_index_cannot_escape_without_a_trailing_slash_either(self) -> None:
        if not self.index_symlink_created:
            self.skipTest("this filesystem cannot create symlinks")

        status, headers, _ = self.get("/outside-index")

        self.assertEqual(status, 301)
        self.assertEqual(headers["Location"], "/outside-index/")

    def test_oversized_static_file_is_rejected(self) -> None:
        status, _, body = self.get("/big.css")

        self.assertEqual(status, 413)
        self.assertIn(str(MAX_BYTES).encode(), body)

    def test_static_response_has_no_content_security_policy(self) -> None:
        _, headers, _ = self.get("/index.html")

        self.assertNotIn("Content-Security-Policy", headers)

    def test_static_response_has_nosniff(self) -> None:
        _, headers, _ = self.get("/index.html")

        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)


class SiteCodeFrameTests(SiteTestBase):
    def test_code_frame_is_highlighted(self) -> None:
        status, headers, body = self.get(f"{SITE_CODE_PREFIX}/code/demo.ex")
        text = body.decode("utf-8")

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertIn('id="L1"', text)
        self.assertIn('class="kd"', text)
        self.assertIn(f"{SITE_CODE_PREFIX}/code/demo.ex", text)

    def test_code_frame_allows_same_origin_embedding(self) -> None:
        _, headers, _ = self.get(f"{SITE_CODE_PREFIX}/code/demo.ex")

        self.assertIn("frame-ancestors 'self'", headers["Content-Security-Policy"])
        self.assertNotIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_code_frame_is_limited_to_the_site_root(self) -> None:
        status, _, body = self.get(f"{SITE_CODE_PREFIX}/../../etc/hosts")

        self.assertEqual(status, 403)
        self.assertNotIn(b"localhost", body)

    def test_code_frame_missing_file_is_not_found(self) -> None:
        status, _, _ = self.get(f"{SITE_CODE_PREFIX}/code/absent.ex")

        self.assertEqual(status, 404)

    def test_code_frame_rejects_invalid_utf8(self) -> None:
        status, _, _ = self.get(f"{SITE_CODE_PREFIX}/code/broken.py")

        self.assertEqual(status, 415)

    def test_code_prefix_alone_is_rejected(self) -> None:
        status, _, _ = self.get(SITE_CODE_PREFIX)

        self.assertEqual(status, 400)


class SiteIsolationTests(SiteTestBase):
    def test_filesystem_routes_are_not_available_in_site_mode(self) -> None:
        for path in ("/etc/hosts", "/workspace/tmp", "/dev/null"):
            with self.subTest(path=path):
                status, _, _ = self.get(path)

                self.assertEqual(status, 404)

    def test_post_is_not_allowed(self) -> None:
        status, headers, _ = self.request("POST", "/")

        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET, HEAD")

    def test_unexpected_host_is_rejected(self) -> None:
        status, _, _ = self.request("GET", "/", headers={"Host": "evil.example"})

        self.assertEqual(status, 403)

    def test_head_returns_headers_without_a_body(self) -> None:
        status, headers, body = self.request("HEAD", "/index.html")

        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(int(headers["Content-Length"]), len(INDEX_HTML))


if __name__ == "__main__":
    unittest.main()
