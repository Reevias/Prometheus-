"""Turns an assistant answer into safe HTML: everything is escaped first, then a little formatting is added.

Supported: **bold**, `code`, fenced code blocks, bullet and numbered lists, headings, paragraphs.
Never produced: links, images or any raw HTML from the text.
"""
from __future__ import annotations

import re

from markupsafe import Markup, escape

BOLD = re.compile(r"\*\*(.+?)\*\*")
CODE = re.compile(r"`([^`\n]+)`")
BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
NUMBER = re.compile(r"^\s*\d{1,3}[.)]\s+(.*)$")
HEADING = re.compile(r"^\s*#{1,6}\s+(.*)$")


def _inline(text: str) -> str:
    out = str(escape(text))
    parts = re.split(r"(`[^`\n]+`)", out)      # leave `code` alone while adding bold
    for i, part in enumerate(parts):
        parts[i] = CODE.sub(r"<code>\1</code>", part) if part.startswith("`") else BOLD.sub(r"<strong>\1</strong>", part)
    return "".join(parts)


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

    lines = (text or "")[:40_000].replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            flush_para(), flush_list()
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            html.append("<pre><code>" + str(escape("\n".join(block))) + "</code></pre>")
            i += 1
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
