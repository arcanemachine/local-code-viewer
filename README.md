# local-code-viewer

A read-only local source viewer served over loopback HTTP. It renders Elixir,
Python, and TypeScript with syntax highlighting, gives every line a stable
`#L<n>` anchor, and can be linked to from generated documentation.

```text
http://127.0.0.1:8765/open?path=%2Fhome%2Fyou%2Fproject%2Flib%2Fexample.ex#L123
```

Opening that URL renders the file, scrolls to line 123, and highlights it. The
fragment is handled entirely by the browser, so the page needs no JavaScript.

## What it is not

Not an editor: nothing is ever written, renamed, deleted, or uploaded. Not a
project browser: there is no directory listing, no search, and no file index.
No language server, no Git integration, no live reload. It serves only the files
it is explicitly configured to serve.

## Requirements

- Python 3.10 or newer
- [Pygments](https://pygments.org/) (the only runtime dependency)

## Running

From the project directory, with no configuration, the current directory
becomes the only allowed root:

```bash
python3 -m local_code_viewer
```

Add one or more roots explicitly. Supplying any `--root` or `--map` replaces the
current-directory default:

```bash
python3 -m local_code_viewer --root /home/you/project --root /home/you/other
```

If the paths in your generated links are not the paths on this machine (for
example documentation generated inside a container), translate the prefix:

```bash
python3 -m local_code_viewer --map /container/project=/home/you/project
```

A request for `/container/project/lib/example.ex` then reads
`/home/you/project/lib/example.ex`. The link-facing prefix does not have to
exist on this machine; the actual directory must.

Options:

| Option | Default | Meaning |
| --- | --- | --- |
| `--port PORT` | `8765` | Loopback port. Links contain this port, so the server refuses to start if it is taken rather than silently moving. |
| `--root PATH` | current directory | Directory that may be served, addressed by the same path. Repeatable. |
| `--map LINK=ACTUAL` | none | Translate a link path prefix to a directory on this machine. Repeatable. Split on the first `=`. |
| `--max-bytes SIZE` | `2097152` | Largest file served, in bytes. |

Installing the package (`pip install -e .`) also provides a `local-code-viewer`
command, which takes the same options.

## Linking to a line

```text
/open?path=<percent-encoded absolute path>#L<n>
```

- The path is percent-encoded once and must be absolute in the link namespace.
- A literal `#` or `?` in a file name must be encoded (`%23`, `%3F`), otherwise
  the browser treats it as part of the URL structure.
- Line numbers are one-based. Line 1 of `lib/example.ex` is `#L1`.
- Anything other than `path` in the query string is ignored.
- The response contains an element whose `id` is exactly `L<n>` for each line,
  so ordinary fragment navigation and browser history work.

Numbering matches `wc -l` semantics: a trailing newline does not create an extra
line, interior and trailing blank lines are counted, and a zero-byte file has no
lines.

## Languages

| Extension | Highlighting |
| --- | --- |
| `.ex`, `.exs` | Elixir |
| `.py` | Python |
| `.ts` | TypeScript |
| anything else | escaped plain text, still numbered and anchorable |

The file extension decides the language; content sniffing is not attempted.
Source text is always escaped before it reaches the page, so a file containing
HTML or script tags is displayed, never executed.

Files must be UTF-8. A byte-order mark is accepted and not displayed. Anything
else is refused with `415` rather than rendered incorrectly.

## Security model

The viewer is a local, single-user tool. It is nevertheless careful about the
loopback boundary:

- Binds `127.0.0.1` only. There is no option to bind another interface.
- Validates the `Host` header and answers only loopback host names, which
  reduces DNS-rebinding exposure.
- Canonicalizes the requested path and refuses anything outside the configured
  roots, including `..` traversal and symlinks that resolve outside a root.
  Symlinks that stay inside a root are followed.
- Serves regular files only, and only below `--max-bytes`.
- Emits no CORS header, no JSON API, and no JSONP, so another page cannot read
  source cross-origin. The intended integration is a top-level link.
- Sends `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and
  `Cache-Control: no-store`.
- Never writes to the filesystem and never returns a traceback or an absolute
  host path to the browser.
- Logs the route and status only; the requested path is not logged.

There is a deliberate gap between validation and reading: another process
running as the same user could swap the file in between. This is not defended
against, because doing so would require platform-specific descriptor-relative
file APIs and the threat model is a local single-user tool.

## Responses

| Status | When |
| --- | --- |
| `200` | File rendered, or the help page |
| `400` | Missing, empty, duplicated, or relative `path`; directory; non-regular file |
| `403` | Path outside every configured root; unreadable file; unexpected `Host` |
| `404` | Unknown route; no such file |
| `405` | Any method other than `GET` and `HEAD` |
| `413` | File larger than `--max-bytes` |
| `415` | Not valid UTF-8 |
| `500` | Unexpected failure, reported without implementation detail |

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

The suite covers configuration and mapping validation, path authorization
(including traversal and symlink escapes), line numbering and anchors, HTML
escaping, and the HTTP surface including status codes and response headers.

## Limitations

- The server must run on the same machine as the browser, because it listens on
  loopback. Serving from inside a container to a browser on the host does not
  work without its own network arrangement.
- Developed and verified on Linux only. Other platforms are untested, although
  the implementation uses portable standard-library path handling.
- Chrome is expected to work, since the viewer is a plain HTML page served over
  HTTP, but it has not been verified.
- Highlighting accuracy is Pygments' TextMate-style lexing, not a parser, so
  unusual syntax can be misclassified.
- Only one theme (light) is provided, and there is no configuration file.

## Project layout

```text
local_code_viewer/config.py    mappings between link prefixes and directories
local_code_viewer/resolve.py   path authorization and size checks
local_code_viewer/render.py    Pygments adapter, line anchors, page shell
local_code_viewer/app.py       loopback HTTP server
local_code_viewer/cli.py       argument parsing
```
