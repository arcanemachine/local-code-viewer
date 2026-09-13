"""Static HTML rendering: page shell, Pygments highlighting, line anchors."""

from __future__ import annotations

import html
import os
from collections.abc import Sequence
from functools import lru_cache

from pygments import highlight
from pygments.formatters.html import HtmlFormatter
from pygments.lexer import Lexer
from pygments.lexers import get_lexer_by_name

from .config import Mapping

LINE_ANCHOR_PREFIX = "L"

EXTENSION_LEXERS: dict[str, str] = {
    ".ex": "elixir",
    ".exs": "elixir",
    ".py": "python",
    ".ts": "typescript",
}

FALLBACK_LEXER = "text"

# Lexers default to stripping leading and trailing newlines and to appending a
# final newline. Both would change the line numbering that anchors rely on, so
# the source is handed to the lexer exactly as it was read.
LEXER_OPTIONS: dict[str, object] = {
    "stripnl": False,
    "ensurenl": False,
    "stripall": False,
    "tabsize": 0,
}

CONTENT_SECURITY_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)


class LineAnchorFormatter(HtmlFormatter):
    """Emit ``<span class="line" id="L<n>">`` wrappers carrying gutter links.

    Pygments' own ``linespans`` and ``lineanchors`` options produce ``L-1``
    style identifiers, so the line wrapper is replaced with one that matches
    the viewer's ``#L1`` anchor contract.
    """

    def __init__(self, **options: object) -> None:
        options.setdefault("lineseparator", "")
        options.setdefault("linespans", "line")
        options.setdefault("nowrap", False)
        super().__init__(**options)

    def _wrap_linespans(self, inner):  # type: ignore[override]
        number = max(self.linenostart - 1, 0)
        for token, line in inner:
            if not token:
                yield 0, line
                continue
            number += 1
            anchor = f"{LINE_ANCHOR_PREFIX}{number}"
            yield 1, (
                f'<span class="line" id="{anchor}">'
                f'<a class="line-number" href="#{anchor}">{number}</a>'
                f'<span class="source">{line}</span>'
                f"</span>"
            )


def lexer_for(path: str) -> Lexer:
    """Return the lexer for a file path, falling back to plain text."""
    extension = os.path.splitext(path)[1].lower()
    name = EXTENSION_LEXERS.get(extension, FALLBACK_LEXER)
    return get_lexer_by_name(name, **LEXER_OPTIONS)


@lru_cache(maxsize=1)
def _pygments_css() -> str:
    return LineAnchorFormatter().get_style_defs(".highlight")


VIEWER_CSS = """
:root {
  color-scheme: light;
  --page-bg: #ffffff;
  --code-bg: #f8f8f8;
  --chrome-bg: #f6f8fa;
  --border: #d0d7de;
  --text: #1f2328;
  --muted: #57606a;
  --gutter: #6e7781;
  --target-bg: #fff6c9;
  --target-accent: #d4a72c;
  --link: #0969da;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--page-bg);
  color: var(--text);
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
.viewer-header {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 0.35rem 0.75rem;
  background: var(--chrome-bg);
  border-bottom: 1px solid var(--border);
  color: var(--muted);
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.78rem;
  white-space: nowrap;
  overflow-x: auto;
}
.prose { max-width: 46rem; margin: 1.5rem auto; padding: 0 1rem; line-height: 1.55; }
.prose h1 { font-size: 1.3rem; margin: 0 0 0.5rem; }
.prose h2 { font-size: 1rem; margin: 1.5rem 0 0.4rem; }
.prose .status { margin: 0; color: var(--muted); font-size: 0.78rem;
  letter-spacing: 0.06em; text-transform: uppercase; }
.prose ul { margin: 0.4rem 0; padding-left: 1.2rem; }
.prose code {
  background: var(--chrome-bg);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 0.05rem 0.25rem;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.85em;
}
.prose pre {
  background: var(--chrome-bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.6rem 0.75rem;
  overflow-x: auto;
  font-size: 0.85rem;
}
.highlight { margin: 0; }
.highlight pre {
  margin: 0;
  padding: 0.5rem 0 2rem;
  overflow-x: auto;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.8125rem;
  line-height: 1.5;
  tab-size: 4;
}
.highlight .line { display: block; scroll-margin-top: 2.5rem; }
.highlight .line:target {
  background: var(--target-bg);
  box-shadow: inset 3px 0 0 var(--target-accent);
}
.highlight .line:target .line-number {
  color: var(--text);
  background: var(--target-bg);
}
.highlight .source { white-space: pre; }
.highlight .line-number {
  display: inline-block;
  width: 4.25em;
  margin-right: 1em;
  text-align: right;
  color: var(--gutter);
  text-decoration: none;
  user-select: none;
  -webkit-user-select: none;
  background: var(--code-bg);
  position: sticky;
  left: 0;
}
.highlight .line-number:hover { color: var(--link); text-decoration: underline; }
"""


def _page(title: str, link_path: str | None, body: str, *, token_styles: bool) -> str:
    header = ""
    if link_path is not None:
        header = (
            '<header class="viewer-header"><span class="path">'
            f"{html.escape(link_path)}</span></header>\n"
        )
    stylesheet = f"{VIEWER_CSS}\n{_pygments_css()}" if token_styles else VIEWER_CSS
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n"
        f"<style>{stylesheet}</style>\n"
        "</head>\n"
        "<body>\n"
        f"{header}"
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )


def render_source(*, link_path: str, filename: str, source: str) -> str:
    """Render one source file as a complete viewer document."""
    body = highlight(source, lexer_for(link_path), LineAnchorFormatter())
    return _page(filename, link_path, body, token_styles=True)


def render_help(mappings: Sequence[Mapping], port: int) -> str:
    """Render the viewer's landing page."""
    base = f"http://127.0.0.1:{port}"
    prefixes = "\n".join(f"<li><code>{html.escape(m.link_prefix)}</code></li>" for m in mappings)
    example = f"{base}/open?path=%2Fhome%2Fyou%2Fproject%2Flib%2Fexample.ex#L12"
    body = f"""<div class="prose">
<h1>Local code viewer</h1>
<p class="status">Running</p>
<p>This is a read-only local source viewer. It serves files from the configured
roots, highlights Elixir, Python, and TypeScript, and addresses every line with
a <code>#L&lt;n&gt;</code> fragment.</p>

<h2>Opening a file</h2>
<pre>/open?path=&lt;percent-encoded absolute path&gt;#L&lt;n&gt;</pre>
<p>The path must be absolute and is percent-encoded once. Characters such as a
space, <code>#</code>, or <code>?</code> inside a file name must be encoded
(for example <code>%23</code> for a literal <code>#</code>), otherwise the
browser treats them as part of the URL structure.</p>
<p>For example, to open line 12 of
<code>/home/you/project/lib/example.ex</code>:</p>
<pre>{html.escape(example)}</pre>
<p>Copying the address bar keeps the anchor, so a link can point at one line.
Line numbers are one-based.</p>

<h2>Configured roots</h2>
<ul>
{prefixes}
</ul>
</div>"""
    return _page("Local code viewer", None, body, token_styles=False)


def render_error(status: int, title: str, detail: str, hint: str | None = None) -> str:
    """Render a viewer-styled error page with no implementation detail."""
    hint_html = f"\n<p>{html.escape(hint)}</p>" if hint else ""
    body = f"""<div class="prose">
<p class="status">{status}</p>
<h1>{html.escape(title)}</h1>
<p>{html.escape(detail)}</p>{hint_html}
</div>"""
    return _page(f"{status} {title}", None, body, token_styles=False)
