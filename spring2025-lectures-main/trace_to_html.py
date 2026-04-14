#!/usr/bin/env python3
"""
Convert a lecture JSON trace file to a printable HTML file.

Usage:
    python trace_to_html.py var/traces/lecture_01.json
    python trace_to_html.py var/traces/lecture_01.json -o my_output.html
    python trace_to_html.py var/traces/lecture_01.json --all
"""

import json
import re
import sys
import html as html_module
import argparse
from pathlib import Path


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

def inline_markdown(text):
    """Convert inline markdown (bold, italic, code, links) to HTML.
    Math expressions ($...$) are preserved for MathJax."""

    # Temporarily hide math so it doesn't get escaped or processed.
    placeholders = {}
    counter = [0]

    def save(m):
        key = f"\x00MATH{counter[0]}\x00"
        placeholders[key] = m.group(0)
        counter[0] += 1
        return key

    text = re.sub(r'\$\$[\s\S]+?\$\$', save, text)
    text = re.sub(r'\$[^$\n]+?\$', save, text)

    # Escape HTML special characters.
    text = html_module.escape(text)

    # Bold + italic: ***text***
    text = re.sub(r'\*\*\*(.*?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    # Bold: **text**
    text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
    # Italic: *text*  (not inside **)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', text)
    # Inline code: `text`
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # Markdown links: [label](url)
    text = re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        lambda m: f'<a href="{html_module.escape(m.group(2))}" target="_blank">'
                  f'{m.group(1)}</a>',
        text,
    )
    # Literal <br> tags that got escaped
    text = text.replace('&lt;br&gt;', '<br>')
    text = text.replace('&lt;br /&gt;', '<br>')

    # Restore math.
    for key, value in placeholders.items():
        text = text.replace(html_module.escape(key), value)

    return text


def markdown_block_to_html(text):
    """Convert a markdown string to an HTML block element."""
    text = text.strip()
    if not text:
        return ''

    # ATX heading  (## Title)
    m = re.match(r'^(#{1,6})\s+(.*)', text, re.DOTALL)
    if m:
        level = len(m.group(1))
        content = inline_markdown(m.group(2).strip())
        return f'<h{level}>{content}</h{level}>'

    # Multi-line text or list
    lines = text.split('\n')
    parts = []
    in_list = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                parts.append('</ul>')
                in_list = False
            continue

        if re.match(r'^[-*]\s', stripped):
            if not in_list:
                parts.append('<ul>')
                in_list = True
            item = inline_markdown(stripped[2:])
            parts.append(f'<li>{item}</li>')
        else:
            if in_list:
                parts.append('</ul>')
                in_list = False
            parts.append(f'<p>{inline_markdown(stripped)}</p>')

    if in_list:
        parts.append('</ul>')

    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# Rendering → HTML
# ---------------------------------------------------------------------------

def rendering_to_html(r):
    """Convert a single rendering dict to an HTML string."""
    rtype = r.get('type', '')
    data = r.get('data', '')
    style = r.get('style') or {}
    ext_link = r.get('external_link')

    if rtype == 'markdown':
        content = markdown_block_to_html(str(data))
        if not content:
            return ''
        if ext_link:
            # Unwrap a bare <p> so the link is inline.
            inner = re.sub(r'^<p>(.*)</p>$', r'\1', content.strip(), flags=re.DOTALL)
            return f'<a href="{html_module.escape(ext_link)}" target="_blank">{inner}</a>'
        return content

    if rtype == 'image':
        src = str(data)
        width = style.get('width')
        img_style = f'max-width: {width}px; height: auto;' if width else 'max-width: 100%; height: auto;'
        return (
            f'<div class="img-wrap">'
            f'<img src="{html_module.escape(src)}" style="{img_style}" loading="lazy" />'
            f'</div>'
        )

    # Silently skip unknown types.
    return ''


def is_block(r):
    """Return True if this rendering should start a new block."""
    rtype = r.get('type', '')
    if rtype == 'image':
        return True
    if rtype == 'markdown':
        data = str(r.get('data', '')).strip()
        # Headings, multi-line text, and list items are blocks.
        if re.match(r'^#{1,6}\s', data):
            return True
        if '\n' in data or re.match(r'^[-*]\s', data):
            return True
    return False


# ---------------------------------------------------------------------------
# Step grouping
# ---------------------------------------------------------------------------

def step_source_key(step):
    """Return (path, line_number) of the innermost stack frame."""
    stack = step.get('stack', [])
    if stack:
        f = stack[-1]
        return (f.get('path', ''), f.get('line_number', 0))
    return ('', 0)


def collect_inline_groups(steps):
    """
    Iterate over all steps and yield groups of renderings.

    Consecutive steps that share the same source line are yielded as one
    group so that e.g.  text("foo "), link(ref)  renders inline.

    Yields: list of rendering dicts
    """
    current_key = None
    current_group = []

    for step in steps:
        renderings = step.get('renderings', [])
        if not renderings:
            continue
        key = step_source_key(step)
        if key == current_key:
            current_group.extend(renderings)
        else:
            if current_group:
                yield current_group
            current_key = key
            current_group = list(renderings)

    if current_group:
        yield current_group


# ---------------------------------------------------------------------------
# Variable inspection panel
# ---------------------------------------------------------------------------

def format_value(v):
    """Return a short string representation of an inspected value."""
    s = str(v)
    if len(s) > 200:
        s = s[:197] + '...'
    return html_module.escape(s)


def env_html(env):
    """Return HTML for a variable-inspection row, or '' if env is empty."""
    if not env:
        return ''
    items = ', '.join(
        f'<span class="var-name">{html_module.escape(k)}</span> = '
        f'<span class="var-val">{format_value(v)}</span>'
        for k, v in env.items()
    )
    return f'<div class="env-row">{items}</div>'


# ---------------------------------------------------------------------------
# Full document generation
# ---------------------------------------------------------------------------

CSS = """
* { box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 "Helvetica Neue", Arial, sans-serif;
    font-size: 15px;
    line-height: 1.65;
    color: #222;
    max-width: 860px;
    margin: 0 auto;
    padding: 48px 32px;
    background: #fff;
}
h1 { font-size: 2em;   border-bottom: 2px solid #e1e4e8; padding-bottom: .3em; margin-top: 1.6em; }
h2 { font-size: 1.5em; border-bottom: 1px solid #e1e4e8; padding-bottom: .25em; margin-top: 1.6em; }
h3 { font-size: 1.2em; margin-top: 1.4em; }
h4, h5, h6 { margin-top: 1.2em; }
h1, h2, h3, h4, h5, h6 { margin-bottom: .4em; font-weight: 600; color: #111; }
p  { margin: .45em 0; }
ul, ol { margin: .4em 0; padding-left: 2.2em; }
li { margin: .15em 0; }
a  { color: #0366d6; text-decoration: none; }
a:hover { text-decoration: underline; }
code {
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    font-size: .88em;
    background: #f4f4f4;
    border: 1px solid #e0e0e0;
    border-radius: 3px;
    padding: .1em .35em;
}
strong { font-weight: 600; }
em { font-style: italic; }

.img-wrap {
    margin: 1.2em 0;
    text-align: center;
}
.img-wrap img {
    max-width: 100%;
    height: auto;
    border-radius: 4px;
    box-shadow: 0 1px 4px rgba(0,0,0,.12);
}

/* Variable inspection rows */
.env-row {
    font-size: .82em;
    font-family: "SFMono-Regular", Consolas, monospace;
    background: #f0f4ff;
    border-left: 3px solid #4a90e2;
    padding: .25em .7em;
    margin: .25em 0 .6em 1.5em;
    border-radius: 0 3px 3px 0;
    color: #333;
}
.var-name { color: #6f42c1; font-weight: 600; }
.var-val  { color: #005cc5; }

@media print {
    body { padding: 12px; font-size: 12px; }
    h1, h2 { page-break-after: avoid; }
    .img-wrap { page-break-inside: avoid; }
    a[href]::after {
        content: " (" attr(href) ")";
        font-size: .75em;
        color: #666;
    }
}
"""

MATHJAX_CONFIG = """
MathJax = {
    tex: {
        inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
        displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']]
    },
    svg: { fontCache: 'global' }
};
"""


def generate_html(trace_path, output_path=None):
    trace_path = Path(trace_path)
    with open(trace_path, encoding='utf-8') as f:
        trace = json.load(f)

    steps = trace.get('steps', [])
    lecture_name = trace_path.stem  # e.g. "lecture_01"

    body_parts = []

    # We pair each group of renderings with the env snapshot of the step that
    # produced them, so we can show @inspect variable values.
    current_key = None
    current_renderings = []
    current_env = {}

    def flush_group():
        nonlocal current_renderings, current_env
        if not current_renderings:
            return

        # Decide block vs inline
        has_block = any(is_block(r) for r in current_renderings)
        if has_block:
            for r in current_renderings:
                h = rendering_to_html(r)
                if h:
                    body_parts.append(h)
        else:
            inline_html_parts = []
            for r in current_renderings:
                h = rendering_to_html(r)
                if h:
                    inner = re.sub(r'^<p>(.*)</p>$', r'\1', h.strip(), flags=re.DOTALL)
                    inline_html_parts.append(inner)
            if inline_html_parts:
                body_parts.append(f'<p>{"".join(inline_html_parts)}</p>')

        # Show env variables (inspected values)
        e = env_html(current_env)
        if e:
            body_parts.append(e)

        current_renderings = []
        current_env = {}

    for step in steps:
        renderings = step.get('renderings', [])
        env = step.get('env', {})
        key = step_source_key(step)

        if renderings:
            if key != current_key:
                flush_group()
                current_key = key
            current_renderings.extend(renderings)
            if env:
                current_env.update(env)
        elif env:
            # A step with only env (no new rendering) — still show inspected vars.
            flush_group()
            current_key = key
            current_env.update(env)
            flush_group()

    flush_group()

    body = '\n'.join(body_parts)
    title = html_module.escape(lecture_name.replace('_', ' ').title())

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{title}</title>
<script>{MATHJAX_CONFIG}</script>
<script id="MathJax-script" async
    src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>
<style>
{CSS}
</style>
</head>
<body>
{body}
</body>
</html>
"""

    if output_path is None:
        # Place the HTML next to the project root (three levels up from var/traces/).
        root = trace_path.parent.parent.parent
        output_path = root / f"{lecture_name}.html"

    output_path = Path(output_path)
    output_path.write_text(html_doc, encoding='utf-8')
    print(f"Saved → {output_path.resolve()}")
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Convert a CS336 lecture JSON trace to a printable HTML file.'
    )
    parser.add_argument(
        'trace',
        help='Path to the JSON trace file, e.g. var/traces/lecture_01.json',
    )
    parser.add_argument(
        '-o', '--output',
        help='Output HTML path (default: <project_root>/<lecture_name>.html)',
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Convert all lecture_*.json files found in the same directory as TRACE.',
    )
    args = parser.parse_args()

    if args.all:
        trace_dir = Path(args.trace)
        if not trace_dir.is_dir():
            trace_dir = trace_dir.parent
        traces = sorted(trace_dir.glob('lecture_*.json'))
        if not traces:
            print(f'No lecture_*.json files found in {trace_dir}', file=sys.stderr)
            sys.exit(1)
        for t in traces:
            generate_html(t)
    else:
        out = Path(args.output) if args.output else None
        generate_html(args.trace, out)


if __name__ == '__main__':
    main()
