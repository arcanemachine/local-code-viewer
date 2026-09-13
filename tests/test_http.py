"""End-to-end HTTP tests against a live loopback server."""

from __future__ import annotations

import contextlib
import http.client
import io
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote

from local_code_viewer.app import ViewerServer
from local_code_viewer.config import Configuration, Mapping
from local_code_viewer.render import CONTENT_SECURITY_POLICY

MAX_BYTES = 8192
LONG_FILE_LINES = 200


def open_url(path: str) -> str:
    return "/open?path=" + quote(path, safe="")


class ViewerHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls._temporary.name).resolve()
        cls.source = cls.root / "example.py"
        cls.source.write_text(
            "".join(f"value_{index} = {index}\n" for index in range(1, LONG_FILE_LINES + 1)),
            encoding="utf-8",
        )
        cls.elixir = cls.root / "sample.ex"
        cls.elixir.write_text("defmodule Sample do\n  def f, do: :ok\nend\n", encoding="utf-8")
        cls.spaced = cls.root / "with space.py"
        cls.spaced.write_text("x = 1\n", encoding="utf-8")
        cls.hashed = cls.root / "hash#name.py"
        cls.hashed.write_text("x = 1\n", encoding="utf-8")
        cls.plus = cls.root / "plus+name.py"
        cls.plus.write_text("x = 1\n", encoding="utf-8")
        cls.question = cls.root / "question?name.py"
        cls.question.write_text("x = 1\n", encoding="utf-8")
        cls.unicode_name = cls.root / "ünïcode.py"
        cls.unicode_name.write_text("x = 1\n", encoding="utf-8")
        cls.bom = cls.root / "bom.py"
        cls.bom.write_bytes("x = 1\n".encode("utf-8-sig"))
        cls.binary = cls.root / "binary.py"
        cls.binary.write_bytes(b"\xff\xfe\x00\x01not utf-8 at all\n")
        cls.oversized = cls.root / "big.py"
        cls.oversized.write_bytes(b"a" * (MAX_BYTES + 1))
        cls.directory = cls.root / "subdir"
        cls.directory.mkdir()
        cls.outside = cls.root.parent / f"{cls.root.name}-outside"
        cls.outside.mkdir(exist_ok=True)
        cls.outside_file = cls.outside / "secret.txt"
        cls.outside_file.write_text("secret\n", encoding="utf-8")

        cls.configuration = Configuration(
            mappings=(Mapping(link_prefix=str(cls.root), actual_root=cls.root),),
            port=0,
            max_bytes=MAX_BYTES,
        )
        cls.server = ViewerServer(cls.configuration)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls._temporary.cleanup()
        with contextlib.suppress(OSError):
            cls.outside.rmdir()

    def request(
        self, method: str = "GET", path: str = "/", headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            body = response.read()
            return response.status, dict(response.getheaders()), body
        finally:
            connection.close()

    def get(self, path: str) -> tuple[int, dict[str, str], bytes]:
        return self.request("GET", path)

    def text(self, path: str) -> tuple[int, str]:
        status, _, body = self.get(path)
        return status, body.decode("utf-8")

    # Help page

    def test_help_page_is_served(self) -> None:
        status, body = self.text("/")

        self.assertEqual(status, 200)
        self.assertIn("Local code viewer", body)
        self.assertIn(str(self.root), body)
        self.assertIn("#L", body)

    # Successful file rendering

    def test_source_file_is_served_with_line_anchors(self) -> None:
        status, body = self.text(open_url(str(self.source)))

        self.assertEqual(status, 200)
        self.assertIn('id="L1"', body)
        self.assertIn(f'id="L{LONG_FILE_LINES}"', body)
        self.assertIn(str(self.source), body)

    def test_elixir_file_is_highlighted(self) -> None:
        status, body = self.text(open_url(str(self.elixir)))

        self.assertEqual(status, 200)
        self.assertIn("defmodule", body)
        self.assertIn('class="kd"', body)

    def test_unencoded_slashes_are_accepted(self) -> None:
        status, body = self.text(f"/open?path={self.source}")

        self.assertEqual(status, 200)
        self.assertIn('id="L1"', body)

    def test_path_with_a_space_is_accepted(self) -> None:
        status, body = self.text(open_url(str(self.spaced)))

        self.assertEqual(status, 200)
        self.assertIn('id="L1"', body)

    def test_path_with_a_percent_encoded_hash_is_accepted(self) -> None:
        status, body = self.text(open_url(str(self.hashed)))

        self.assertEqual(status, 200)
        self.assertIn("hash#name.py", body)

    def test_path_with_a_plus_is_accepted_when_encoded(self) -> None:
        status, body = self.text(open_url(str(self.plus)))

        self.assertEqual(status, 200)
        self.assertIn("plus+name.py", body)

    def test_path_with_a_question_mark_is_accepted_when_encoded(self) -> None:
        status, body = self.text(open_url(str(self.question)))

        self.assertEqual(status, 200)
        self.assertIn("question?name.py", body)

    def test_path_with_non_ascii_is_accepted_when_encoded(self) -> None:
        status, body = self.text(open_url(str(self.unicode_name)))

        self.assertEqual(status, 200)
        self.assertIn("ünïcode.py", body)

    def test_unencoded_plus_is_read_as_a_space(self) -> None:
        status, _ = self.text(f"/open?path={self.plus}")

        self.assertEqual(status, 404)

    def test_byte_order_mark_is_not_displayed(self) -> None:
        status, body = self.text(open_url(str(self.bom)))

        self.assertEqual(status, 200)
        self.assertNotIn("\ufeff", body)
        self.assertEqual(body.count('class="line"'), 1)

    def test_head_request_returns_headers_without_a_body(self) -> None:
        status, headers, body = self.request("HEAD", open_url(str(self.source)))

        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertGreater(int(headers["Content-Length"]), 0)

    # Bad requests

    def test_missing_path_parameter_is_rejected(self) -> None:
        status, body = self.text("/open")

        self.assertEqual(status, 400)
        self.assertIn("path", body)

    def test_empty_path_parameter_is_rejected(self) -> None:
        status, _ = self.text("/open?path=")

        self.assertEqual(status, 400)

    def test_duplicate_path_parameters_are_rejected(self) -> None:
        status, _ = self.text(f"/open?path={self.source}&path={self.source}")

        self.assertEqual(status, 400)

    def test_relative_path_is_rejected(self) -> None:
        status, _ = self.text("/open?path=relative.py")

        self.assertEqual(status, 400)

    def test_unknown_route_is_not_found(self) -> None:
        status, _ = self.text("/nope")

        self.assertEqual(status, 404)

    # Authorization

    def test_path_outside_the_root_is_forbidden(self) -> None:
        status, body = self.text(open_url(str(self.outside_file)))

        self.assertEqual(status, 403)
        self.assertNotIn('class="line"', body)

    def test_encoded_traversal_is_forbidden(self) -> None:
        traversal = f"/open?path={self.root}%2F%2E%2E%2Fetc%2Fpasswd"

        status, body = self.text(traversal)

        self.assertEqual(status, 403)
        self.assertNotIn('class="line"', body)

    def test_encoded_slash_traversal_is_forbidden(self) -> None:
        traversal = f"/open?path={quote(str(self.root), safe='')}%2F%2e%2e%2Fpasswd"

        status, _ = self.text(traversal)

        self.assertEqual(status, 403)

    def test_directory_is_rejected(self) -> None:
        status, _ = self.text(open_url(str(self.directory)))

        self.assertEqual(status, 400)

    def test_oversized_file_is_rejected(self) -> None:
        status, body = self.text(open_url(str(self.oversized)))

        self.assertEqual(status, 413)
        self.assertIn(str(MAX_BYTES), body)

    def test_invalid_utf8_is_rejected(self) -> None:
        status, body = self.text(open_url(str(self.binary)))

        self.assertEqual(status, 415)
        self.assertNotIn("not utf-8", body)

    def test_missing_file_is_not_found(self) -> None:
        status, _ = self.text(open_url(str(self.root / "absent.py")))

        self.assertEqual(status, 404)

    # Methods and host handling

    def test_post_is_rejected_with_allow_header(self) -> None:
        status, headers, _ = self.request("POST", "/")

        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET, HEAD")

    def test_put_and_delete_are_rejected(self) -> None:
        for method in ("PUT", "DELETE", "PATCH", "OPTIONS", "TRACE"):
            with self.subTest(method=method):
                status, _, _ = self.request(method, "/")

                self.assertEqual(status, 405)

    def test_unexpected_host_is_rejected(self) -> None:
        status, _, _ = self.request("GET", "/", headers={"Host": "evil.example"})

        self.assertEqual(status, 403)

    def test_wrong_port_in_host_is_rejected(self) -> None:
        status, _, _ = self.request("GET", "/", headers={"Host": f"127.0.0.1:{self.port + 1}"})

        self.assertEqual(status, 403)

    def test_localhost_host_is_accepted(self) -> None:
        status, _, _ = self.request("GET", "/", headers={"Host": f"localhost:{self.port}"})

        self.assertEqual(status, 200)

    # Response headers

    def test_success_response_carries_security_headers(self) -> None:
        _, headers, _ = self.get(open_url(str(self.source)))

        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Content-Security-Policy"], CONTENT_SECURITY_POLICY)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_error_response_carries_security_headers(self) -> None:
        _, headers, _ = self.get("/nope")

        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_error_response_hides_implementation_detail(self) -> None:
        for path in ("/nope", "/open", open_url(str(self.outside_file))):
            with self.subTest(path=path):
                _, body = self.text(path)

                self.assertNotIn("Traceback (most recent call last)", body)
                self.assertNotIn("local_code_viewer", body)

    def test_translated_actual_root_is_not_leaked_in_errors(self) -> None:
        configuration = Configuration(
            mappings=(Mapping(link_prefix="/container/project", actual_root=self.root),),
            port=0,
            max_bytes=MAX_BYTES,
        )
        server = ViewerServer(configuration)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        self.addCleanup(connection.close)

        connection.request("GET", "/open?path=/container/project/absent.py")
        response = connection.getresponse()
        body = response.read().decode("utf-8")

        self.assertEqual(response.status, 404)
        self.assertNotIn(str(self.root), body)
        self.assertIn("/container/project/absent.py", body)

    def test_request_logging_omits_the_requested_path(self) -> None:
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            status, _ = self.text(open_url(str(self.source)))

        self.assertEqual(status, 200)
        logged = captured.getvalue()
        self.assertNotIn(str(self.source), logged)
        self.assertIn("/open", logged)


if __name__ == "__main__":
    unittest.main()
