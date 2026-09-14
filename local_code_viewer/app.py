"""Loopback HTTP server that serves the read-only source viewer."""

from __future__ import annotations

import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from .config import DEFAULT_LINK_PREFIX, SITE_CODE_PREFIX, Configuration
from .render import (
    CONTENT_SECURITY_POLICY,
    SITE_CONTENT_SECURITY_POLICY,
    render_error,
    render_help,
    render_source,
)
from .resolve import Outcome, StaticTarget, resolve_request, resolve_static

HTML_CONTENT_TYPE = "text/html; charset=utf-8"
DEFAULT_CONTENT_TYPE = "application/octet-stream"
INDEX_FILENAME = "index.html"

ALLOWED_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})

OUTCOME_RESPONSES: dict[Outcome, tuple[int, str]] = {
    Outcome.BAD_REQUEST: (400, "Bad request"),
    Outcome.FORBIDDEN: (403, "Forbidden"),
    Outcome.NOT_FOUND: (404, "Not found"),
    Outcome.TOO_LARGE: (413, "File too large"),
}


def _decode_path(raw: str) -> str | None:
    """Percent-decode a request path, or return None when it is not UTF-8."""
    try:
        return unquote(raw, encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError):
        return None


def content_type_for(path: Path) -> str:
    """Guess a response content type from the file name."""
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed is None:
        return DEFAULT_CONTENT_TYPE
    if guessed.startswith("text/") and "charset" not in guessed:
        return f"{guessed}; charset=utf-8"
    return guessed


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
            requested = _decode_path(target.path)
            if requested is None:
                self._fail(
                    400,
                    "Bad request",
                    "The path is not valid UTF-8 once percent-decoded.",
                    hint="Percent-encode the path as UTF-8.",
                    head_only=head_only,
                )
                return
            if self._configuration.site_root is not None:
                self._dispatch_site(requested, head_only=head_only)
            else:
                self._dispatch_viewer(requested, head_only=head_only)
        except Exception as exc:  # noqa: BLE001 - the client must never see a traceback
            sys.stderr.write(f"local-code-viewer: request failed: {exc!r}\n")
            self._fail(
                500,
                "Internal error",
                "The viewer could not complete this request.",
                head_only=head_only,
            )

    def _dispatch_viewer(self, requested: str, *, head_only: bool) -> None:
        if requested == "/":
            self._respond(
                200,
                render_help(self._configuration.mappings, self._bound_port),
                head_only=head_only,
            )
            return
        self._serve_code(requested, head_only=head_only)

    def _dispatch_site(self, requested: str, *, head_only: bool) -> None:
        if requested == SITE_CODE_PREFIX or requested.startswith(SITE_CODE_PREFIX + "/"):
            self._serve_code(requested, head_only=head_only)
            return
        self._serve_static(requested, head_only=head_only)

    def _serve_code(self, requested: str, *, head_only: bool) -> None:
        resolution = resolve_request(
            requested,
            self._configuration.mappings,
            self._configuration.max_bytes,
        )
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
                overrides=self._configuration.lexer_overrides,
            ),
            head_only=head_only,
        )

    def _serve_static(self, requested: str, *, head_only: bool) -> None:
        site_root = self._configuration.site_root
        assert site_root is not None  # only reachable in site mode
        target = resolve_static(requested, site_root)
        if target.outcome is not Outcome.OK:
            self._fail_static(requested, target, head_only=head_only)
            return

        if target.is_directory:
            if not requested.endswith("/"):
                # A trailing slash is what makes relative links resolve correctly.
                self._redirect(quote(requested, safe="/") + "/", head_only=head_only)
                return
            # Resolve the directory index like any other file, so a symlinked
            # index is contained by the same checks.
            index_request = requested + INDEX_FILENAME
            index_target = resolve_static(index_request, site_root)
            if index_target.outcome is Outcome.NOT_FOUND:
                self._fail(
                    404,
                    "Not found",
                    f"{index_request}: no {INDEX_FILENAME} in that directory, and "
                    "directory listings are not provided.",
                    head_only=head_only,
                )
                return
            if index_target.outcome is not Outcome.OK:
                self._fail_static(index_request, index_target, head_only=head_only)
                return
            if index_target.is_directory or index_target.path is None:
                self._fail(
                    400,
                    "Bad request",
                    f"{index_request}: only regular files can be served.",
                    head_only=head_only,
                )
                return
            target = index_target

        path = target.path
        assert path is not None
        try:
            size = path.stat().st_size
        except OSError:
            self._fail(403, "Forbidden", "The file could not be read.", head_only=head_only)
            return
        if size > self._configuration.max_bytes:
            self._fail(
                413,
                "File too large",
                f"{path.name} is {size} bytes and the limit is "
                f"{self._configuration.max_bytes}.",
                head_only=head_only,
            )
            return
        try:
            payload = path.read_bytes()
        except OSError:
            self._fail(403, "Forbidden", "The file could not be read.", head_only=head_only)
            return

        self._respond(
            200,
            payload,
            head_only=head_only,
            content_type=content_type_for(path),
            csp="",
        )

    def _fail_static(
        self, requested: str, target: StaticTarget, *, head_only: bool
    ) -> None:
        status, title = OUTCOME_RESPONSES[target.outcome]
        self._fail(
            status,
            title,
            f"{requested}: {target.detail}",
            head_only=head_only,
        )

    def _redirect(self, location: str, *, head_only: bool) -> None:
        self.send_response(301)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    @property
    def _default_csp(self) -> str:
        if self._configuration.site_root is not None:
            return SITE_CONTENT_SECURITY_POLICY
        return CONTENT_SECURITY_POLICY

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
        body: str | bytes,
        *,
        head_only: bool = False,
        extra_headers: list[tuple[str, str]] | None = None,
        content_type: str = HTML_CONTENT_TYPE,
        csp: str | None = None,
    ) -> None:
        # ``csp=None`` means the mode's default policy; ``csp=""`` omits the header.
        payload = body.encode("utf-8") if isinstance(body, str) else body
        policy = self._default_csp if csp is None else csp
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if policy:
            self.send_header("Content-Security-Policy", policy)
        for name, value in extra_headers or []:
            self.send_header(name, value)
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        # A request path is a filesystem path, so only the outcome is logged.
        sys.stderr.write(f"{self.command} -> {code}\n")

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
    if configuration.site_root is not None:
        lines.append(f"Serving site: {configuration.site_root}")
        lines.append(f"Code frames:  {SITE_CODE_PREFIX}/<path> (same origin)")
    else:
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
    if configuration.lexer_overrides:
        lines.append("Lexer overrides (last match wins):")
        for override in configuration.lexer_overrides:
            lines.append(f"  {override.pattern} -> {override.alias}")
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
