# local-code-viewer

A read-only local source viewer served over loopback HTTP. It highlights source
files in any language Pygments knows, gives every line a stable `#L<n>` anchor,
and can be linked to from generated documentation.

```text
http://127.0.0.1:8765/home/you/project/lib/example.ex#L12
```

Opening that URL renders the file, scrolls to line 12, and highlights it. The
fragment is handled entirely by the browser, so the page needs no JavaScript.

## Quick start

```bash
cd /path/to/local-code-viewer
./run.sh
```

Then open <http://127.0.0.1:8765/>.

Requires Python 3.10 or newer. Pygments is the only dependency, and `run.sh`
installs it for you if it is missing.

By default the viewer serves every readable file on the machine, so a link
carrying any absolute path works straight away. Narrow that with `--root`
(repeatable) if you want to:

```bash
./run.sh --root /home/you/project --root /home/you/other
```

Everything after `run.sh` is passed to the viewer; `--help` lists all of it. If
you would rather not use the script:

```bash
python3 -m local_code_viewer                                     # from this directory
local-code-viewer                                                # after pip install -e .
PYTHONPATH=/path/to/local-code-viewer python3 -m local_code_viewer   # from anywhere, no install
```

## Open a file and target a line

```text
http://127.0.0.1:8765<absolute path>#L<n>
```

The URL path is the file's own path. `GET /` is reserved for a short help page;
every other URL path is resolved against the configured roots.

- Encode the characters that are special in a URL. A literal `#` or `?` in a
  file name must be percent-encoded (`%23`, `%3F`), otherwise the browser treats
  it as part of the URL structure. A space becomes `%20`. A literal `+` needs no
  encoding, because the path is not form data.
- Anything after a `?` is ignored.
- Line numbers are one-based, so line 1 of `lib/example.ex` is `#L1`.
- Every line is an element whose `id` is exactly `L<n>`, so fragment navigation,
  refresh, browser history, and copied links all keep working.

Numbering matches `wc -l` semantics: a trailing newline does not create an extra
line, interior and trailing blank lines are counted, and a zero-byte file has no
lines.

## Common options

| Option | Default | Meaning |
| --- | --- | --- |
| `--root PATH` | `/` | Directory that may be served, addressed by the same path. Repeatable. |
| `--port PORT` | `8765` | Loopback port. Links contain this port, so the viewer refuses to start if it is taken rather than silently moving to another one. |
| `--map LINK=ACTUAL` | none | Translate a link path prefix to a directory on this machine. Repeatable. |
| `--lexer PATTERN=ALIAS` | none | Highlight file names matching a quoted shell-style pattern with a Pygments lexer. Repeatable. |
| `--max-bytes SIZE` | `20971520` | Largest file served, in bytes. |

## What happens on first run

`run.sh` uses your `python3` when it can already import Pygments. Otherwise it
creates a `.venv` in this directory and installs the viewer into it, once; that
first run needs network access, and later runs start immediately. Set
`PYTHON=/path/to/python3` to choose a different interpreter.

Startup prints the port, the roots being served, and any lexer overrides:

```text
Local code viewer listening on http://127.0.0.1:8765
Allowed paths:
  /  (every readable file on this filesystem)
Maximum file size: 20971520 bytes
Address a line with a #L<n> fragment, for example #L12.
Press Ctrl-C to stop.
```

`Ctrl-C` stops it.

## Supported languages

Any language the installed Pygments recognises is highlighted, chosen from the
file name: Python, Elixir, TypeScript, Go, Rust, Ruby, C, JSON, Markdown,
`Dockerfile`, `Makefile`, and hundreds more. Elixir, Python, and TypeScript are
the languages this project's own tests cover; the rest depend on Pygments'
file-name table.

A file Pygments does not recognise is shown as escaped plain text, still with
line numbers and anchors.

`--lexer` overrides detection when the guess is wrong, or when a file name gives
no clue:

```bash
./run.sh --lexer '*.foo'=rust --lexer 'Dockerfile.*'=docker
```

Quote the pattern so your shell does not expand it. The selection order is:

1. the last `--lexer` pattern that matches the file name
2. a mapping this project verifies (`.ex`, `.exs`, `.py`, `.ts`)
3. Pygments' own file-name detection
4. escaped plain text

Patterns are matched case-sensitively against the file name alone, not against
the directories above it. An unknown lexer alias refuses startup with an error,
so a typo cannot silently turn highlighting off. To see which aliases are
available:

```bash
pygmentize -L lexers | less
```

## Translating paths for container-generated links

If the paths in your links were produced somewhere other than this machine — for
example documentation generated inside a container — translate the prefix:

```bash
./run.sh --map /container/project=/home/you/project
```

A request for `/container/project/lib/example.ex` then reads
`/home/you/project/lib/example.ex`. The link-facing prefix does not have to exist
on this machine; the target directory does. The most specific prefix wins, so
`--map /container/project=/a --map /container/project/vendor=/b` serves vendor
paths from `/b`. The split happens at the first `=`, which means a link prefix
containing `=` cannot be expressed.

## What it is not

Not an editor: nothing is ever written, renamed, deleted, or uploaded. Not a
project browser: there is no directory listing, no search, and no file index. No
language server, no Git integration, no live reload. It serves only regular files
inside the roots it is allowed to serve.

## HTTP behaviour

| Status | When |
| --- | --- |
| `200` | File rendered, or the help page |
| `400` | Directory or non-regular file; malformed percent-encoding in the URL path |
| `403` | Path outside every configured root; unreadable file; unexpected `Host` |
| `404` | No such file |
| `405` | Any method other than `GET` and `HEAD` |
| `413` | File larger than `--max-bytes` |
| `415` | Not valid UTF-8 |
| `500` | Unexpected failure, reported without implementation detail |

Files must be UTF-8. A byte-order mark is accepted and not displayed. Anything
else is refused with `415` rather than rendered incorrectly.

## Security model

The viewer is a local, single-user tool. It is nevertheless careful about the
loopback boundary.

With no arguments the allowed root is `/`, so every readable file on the machine
can be requested. That is deliberate: generated links carry whatever absolute
path their generator saw, and configuration should not be needed for them to
work. The startup banner says when the whole filesystem is being served, and
`--root` is the only access control this tool has.

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
- Never writes to the filesystem, and never returns a traceback or an internal
  error detail to the browser. Error pages echo the requested link path; the
  real directory behind a translated `--map` prefix is not disclosed.
- Logs the method and status only, not the requested path.

There is a deliberate gap between validation and reading: another process
running as the same user could swap the file in between. This is not defended
against, because doing so would require platform-specific descriptor-relative
file APIs and the threat model is a local single-user tool.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

The suite covers configuration and mapping validation, lexer selection and
overrides, path authorization (including traversal and symlink escapes), line
numbering and anchors, HTML escaping, and the HTTP surface including status codes
and response headers.

## Limitations

- The server must run on the same machine as the browser, because it listens on
  loopback. Serving from inside a container to a browser on the host does not
  work without its own network arrangement.
- Developed and verified on Linux, with CPython 3.12.8 and Pygments 2.19.2 and
  2.21.0. Other platforms and Python versions are untested, although the
  implementation uses portable standard-library path handling and no
  version-specific syntax.
- Firefox 154 was verified by hand through its WebDriver BiDi endpoint: deep
  links, pointer clicks from a local `file://` document, line highlighting,
  horizontal scrolling, the sticky gutter, and script-like source. A headless
  Chromium run covered the same behaviors. Google Chrome itself has not been
  exercised.
- Highlighting accuracy is Pygments' lexing, not a parser, so unusual syntax can
  be misclassified; `--lexer` exists for the cases that matter to you.
- Only one light theme is provided, and there is no configuration file. Options
  are command-line arguments, so a shell alias or a small wrapper script is the
  usual way to keep a long invocation handy.

## Project layout

```text
run.sh                         start the viewer, installing Pygments if needed
local_code_viewer/config.py    mappings between link prefixes and directories
local_code_viewer/resolve.py   path authorization and size checks
local_code_viewer/render.py    Pygments selection, line anchors, page shell
local_code_viewer/app.py       loopback HTTP server
local_code_viewer/cli.py       argument parsing
```
