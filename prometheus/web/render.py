"""Turns an assistant answer into safe HTML: everything is escaped first, then a little formatting is added.

Supported: **bold**, `code`, fenced code blocks (with their language, for highlighting), bullet and numbered lists,
headings, paragraphs, tables, maths ($…$, $$…$$, \\(…\\), \\[…\\]) and ```mermaid diagrams and charts.
Maths and diagrams stay escaped text here; the page draws them with KaTeX and Mermaid in their safe modes.
Never produced: links, images or any raw HTML from the text (answers can contain outside text).
"""
from __future__ import annotations

import re

from markupsafe import Markup, escape

BOLD = re.compile(r"\*\*(.+?)\*\*")
CODE = re.compile(r"`([^`\n]+)`")
BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
NUMBER = re.compile(r"^\s*\d{1,3}[.)]\s+(.*)$")
HEADING = re.compile(r"^\s*#{1,6}\s+(.*)$")
FENCE = re.compile(r"^\s*```\s*([A-Za-z0-9_+#.-]{0,30})\s*$")
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
MATH_INLINE = re.compile(r"\\\((.+?)\\\)|(?<![\\$\w])\$(?!\s)([^$\n]{1,300}?)(?<!\s)\$(?![\w$])")
MAX_CHARS = 40_000


def _inline(text: str) -> str:
    out = str(escape(text))
    # leave `code` and maths alone while adding bold
    parts = re.split(r"(`[^`\n]+`)", out)
    for i, part in enumerate(parts):
        if part.startswith("`"):
            parts[i] = CODE.sub(r"<code>\1</code>", part)
        else:
            part = MATH_INLINE.sub(lambda m: f'<span class="math">{m.group(1) or m.group(2)}</span>', part)
            parts[i] = BOLD.sub(r"<strong>\1</strong>", part)
    return "".join(parts)


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def render_message(text: str) -> Markup:
    html: list[str] = []
    para: list[str] = []
    items: list[str] = []
    list_tag = ""

    def flush_para():
        if para:
            html.append("<p>" + "<br>".join(para) + "</p>")
            para.clear()

    def flush_list():
        nonlocal list_tag
        if items:
            html.append(f"<{list_tag}>" + "".join(f"<li>{i}</li>" for i in items) + f"</{list_tag}>")
            items.clear()
        list_tag = ""

    lines = (text or "")[:MAX_CHARS].replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        fence = FENCE.match(line)
        if fence:
            flush_para(), flush_list()
            lang = fence.group(1).lower()
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            body = str(escape("\n".join(block)))
            if lang == "mermaid":
                html.append(f'<pre class="diagram">{body}</pre>')
            elif lang in ("math", "latex", "tex"):
                html.append(f'<div class="math block">{body}</div>')
            else:
                cls = f' class="language-{lang}"' if lang else ""
                label = f' data-lang="{lang}"' if lang else ""
                html.append(f"<pre{label}><code{cls}>{body}</code></pre>")
            i += 1
            continue
        if line.strip().startswith("$$") or line.strip().startswith("\\["):        # display maths
            flush_para(), flush_list()
            close = "$$" if line.strip().startswith("$$") else "\\]"
            rest = line.strip()[2:]
            block = []
            if rest.endswith(close) and rest[:-2].strip():
                block.append(rest[:-2])
            else:
                if rest:
                    block.append(rest)
                i += 1
                while i < len(lines) and close not in lines[i]:
                    block.append(lines[i])
                    i += 1
                if i < len(lines):
                    block.append(lines[i].split(close)[0])
            html.append('<div class="math block">' + str(escape("\n".join(block).strip())) + "</div>")
            i += 1
            continue
        if TABLE_ROW.match(line) and i + 1 < len(lines) and TABLE_RULE.match(lines[i + 1]):
            flush_para(), flush_list()
            head = _cells(line)
            rows = []
            i += 2
            while i < len(lines) and TABLE_ROW.match(lines[i]):
                rows.append(_cells(lines[i]))
                i += 1
            out = ["<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>"]
            for r in rows[:500]:
                r = (r + [""] * len(head))[: max(len(head), 1)]
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
            html.append("".join(out) + "</tbody></table>")
            continue
        bullet, number, heading = BULLET.match(line), NUMBER.match(line), HEADING.match(line)
        if bullet or number:
            flush_para()
            tag = "ul" if bullet else "ol"
            if list_tag and list_tag != tag:
                flush_list()
            list_tag = tag
            items.append(_inline((bullet or number).group(1)))
        elif heading:
            flush_para(), flush_list()
            html.append('<p class="h">' + _inline(heading.group(1)) + "</p>")
        elif not line.strip():
            flush_para(), flush_list()
        else:
            flush_list()
            para.append(_inline(line))
        i += 1
    flush_para(), flush_list()
    return Markup("".join(html))
