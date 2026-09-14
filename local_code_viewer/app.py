"""Loopback HTTP server that serves the read-only source viewer."""

from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .config import DEFAULT_LINK_PREFIX, Configuration
from .render import CONTENT_SECURITY_POLICY, render_error, render_help, render_source
from .resolve import Outcome, resolve_request

ALLOWED_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})

OUTCOME_RESPONSES: dict[Outcome, tuple[int, str]] = {
    Outcome.BAD_REQUEST: (400, "Bad request"),
    Outcome.FORBIDDEN: (403, "Forbidden"),
    Outcome.NOT_FOUND: (404, "Not found"),
    Outcome.TOO_LARGE: (413, "File too large"),
}


class ViewerRequestHandler(BaseHTTPRequestHandler):
    """Serve the help page and authorized source files; nothing else."""

    server_version = "local-code-viewer"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - name mandated by BaseHTTPRequestHandler
        self._dispatch(head_only=False)

    def do_HEAD(self) -> None:  # noqa: N802 - name mandated by BaseHTTPRequestHandler
        self._dispatch(head_only=True)

    def __getattr__(self, name: str):
        # Every other HTTP method is recognized only so that it can be refused
        # with 405 instead of the handler's default 501.
        if name.startswith("do_"):
            return self._method_not_allowed
        raise AttributeError(name)

    @property
    def _configuration(self) -> Configuration:
        return self.server.configuration  # type: ignore[attr-defined]

    @property
    def _bound_port(self) -> int:
        return self.server.server_address[1]  # type: ignore[attr-defined]

    def _dispatch(self, *, head_only: bool) -> None:
        if not self._host_is_allowed():
            self._fail(
                403,
                "Forbidden",
                "This viewer only answers requests addressed to its own loopback address.",
                head_only=head_only,
            )
            return
        try:
            target = urlsplit(self.path)
            if target.path == "/":
                self._respond(
                    200,
                    render_help(self._configuration.mappings, self._bound_port),
                    head_only=head_only,
                )
            elif target.path == "/open":
                self._serve_file(target.query, head_only=head_only)
            else:
                self._fail(
                    404,
                    "Not found",
                    f"No route matches {target.path!r}.",
                    hint="Open / for the viewer's help page.",
                    head_only=head_only,
                )
        except Exception as exc:  # noqa: BLE001 - the client must never see a traceback
            sys.stderr.write(f"local-code-viewer: request failed: {exc!r}\n")
            self._fail(
                500,
                "Internal error",
                "The viewer could not complete this request.",
                head_only=head_only,
            )

    def _serve_file(self, query: str, *, head_only: bool) -> None:
        try:
            parameters = parse_qs(
                query, keep_blank_values=True, encoding="utf-8", errors="strict"
            )
        except (UnicodeDecodeError, ValueError):
            self._fail(
                400,
                "Bad request",
                "The query string is not valid UTF-8 once percent-decoded.",
                hint="Percent-encode the path as UTF-8.",
                head_only=head_only,
            )
            return
        requested_paths = parameters.get("path", [])
        if len(requested_paths) != 1 or not requested_paths[0]:
            self._fail(
                400,
                "Bad request",
                "Exactly one non-empty 'path' parameter is required.",
                hint="Use /open?path=<percent-encoded absolute path>#L12.",
                head_only=head_only,
            )
            return

        resolution = resolve_request(
            requested_paths[0],
            self._configuration.mappings,
            self._configuration.max_bytes,
        )
        requested = requested_paths[0]
        if resolution.outcome is not Outcome.OK or resolution.file is None:
            status, title = OUTCOME_RESPONSES[resolution.outcome]
            self._fail(
                status,
                title,
                f"{requested}: {resolution.detail}",
                head_only=head_only,
            )
            return

        source_file = resolution.file
        try:
            data = source_file.path.read_bytes()
        except OSError:
            self._fail(403, "Forbidden", "The file could not be read.", head_only=head_only)
            return
        if len(data) > self._configuration.max_bytes:
            self._fail(
                413,
                "File too large",
                f"The file is {len(data)} bytes and the limit is "
                f"{self._configuration.max_bytes}.",
                head_only=head_only,
            )
            return
        try:
            source = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            self._fail(
                415,
                "Unsupported encoding",
                "Only UTF-8 source files can be displayed.",
                head_only=head_only,
            )
            return

        self._respond(
            200,
            render_source(
                link_path=source_file.link_path,
                filename=os.path.basename(source_file.link_path),
                source=source,
            ),
            head_only=head_only,
        )

    def _host_is_allowed(self) -> bool:
        raw = self.headers.get("Host")
        if not raw:
            return False
        try:
            target = urlsplit("//" + raw)
            hostname = target.hostname
            port = target.port
        except ValueError:
            return False
        if hostname is None or hostname not in ALLOWED_HOSTNAMES:
            return False
        return port is None or port == self._bound_port

    def _method_not_allowed(self) -> None:
        self._respond(
            405,
            render_error(
                405,
                "Method not allowed",
                f"{self.command} is not supported.",
                "This viewer is read-only; use GET.",
            ),
            extra_headers=[("Allow", "GET, HEAD")],
        )

    def _fail(
        self,
        status: int,
        title: str,
        detail: str,
        *,
        hint: str | None = None,
        head_only: bool = False,
    ) -> None:
        self._respond(status, render_error(status, title, detail, hint), head_only=head_only)

    def _respond(
        self,
        status: int,
        body: str,
        *,
        head_only: bool = False,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        for name, value in extra_headers or []:
            self.send_header(name, value)
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        route = urlsplit(self.path).path if self.path else ""
        sys.stderr.write(f"{self.command} {route} -> {code}\n")

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write(f"{self.client_address[0]} {format % args}\n")


class ViewerServer(ThreadingHTTPServer):
    """A loopback-only server holding the viewer's configuration."""

    daemon_threads = True

    def __init__(self, configuration: Configuration) -> None:
        self.configuration = configuration
        super().__init__(("127.0.0.1", configuration.port), ViewerRequestHandler)


def describe_startup(configuration: Configuration, bound_port: int) -> str:
    """Return the operator-facing startup summary."""
    lines = [f"Local code viewer listening on http://127.0.0.1:{bound_port}"]
    lines.append("Allowed paths:")
    for mapping in configuration.mappings:
        if mapping.is_identity:
            suffix = (
                "  (every readable file on this filesystem)"
                if mapping.link_prefix == DEFAULT_LINK_PREFIX
                else ""
            )
            lines.append(f"  {mapping.link_prefix}{suffix}")
        else:
            lines.append(f"  {mapping.link_prefix} -> {mapping.actual_root}")
    lines.append(f"Maximum file size: {configuration.max_bytes} bytes")
    lines.append("Address a line with a #L<n> fragment, for example #L12.")
    lines.append("Press Ctrl-C to stop.")
    return "\n".join(lines)


def serve(configuration: Configuration) -> int:
    """Run the viewer until interrupted; return a process exit status."""
    try:
        server = ViewerServer(configuration)
    except OSError as exc:
        sys.stderr.write(
            f"local-code-viewer: cannot listen on 127.0.0.1:{configuration.port}: {exc}\n"
        )
        return 1
    try:
        print(describe_startup(configuration, server.server_address[1]), flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        server.server_close()
    return 0
