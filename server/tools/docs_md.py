# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""docs_md.py -- build docs/PROTOCOL.md from docs/index.html, so the protocol notes read well on GitHub.

The HTML page is the one to edit. After a change run:  python server/tools/docs_md.py
With --check it only says whether PROTOCOL.md is up to date (exit 1 if not); tools/test_docs.py runs that."""
import html.parser
import os
import re
import sys

DOCS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs")
SOURCE, TARGET = os.path.join(DOCS, "index.html"), os.path.join(DOCS, "PROTOCOL.md")
VOID = {"meta", "link", "input", "br", "hr", "img"}


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []

    def cls(self):
        return self.attrs.get("class", "").split()

    def find(self, tag=None, cls=None):
        """The first descendant with this tag and/or class."""
        for c in self.children:
            if isinstance(c, Node):
                if (tag is None or c.tag == tag) and (cls is None or cls in c.cls()):
                    return c
                found = c.find(tag, cls)
                if found:
                    return found
        return None

    def text(self):
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)


class Tree(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.at = Node("root", [], None)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.at)
        self.at.children.append(node)
        if tag not in VOID:
            self.at = node

    def handle_endtag(self, tag):
        at = self.at
        while at is not self.root and at.tag != tag:
            at = at.parent
        if at is not self.root:
            self.at = at.parent

    def handle_data(self, data):
        self.at.children.append(data)


def inline(node):
    """Markdown for text with code, bold, italics and links in it, on one line."""
    out = []
    for c in node.children:
        if isinstance(c, str):
            out.append(re.sub(r"\s+", " ", c).replace("*", r"\*").replace("_", r"\_").replace("<", "&lt;"))
        elif c.tag == "code":
            text = c.text()
            out.append(f"``{text}``" if "`" in text else f"`{text}`")
        elif c.tag in ("b", "strong"):
            out.append(f"**{inline(c).strip()}**")
        elif c.tag in ("em", "i"):
            out.append(f"*{inline(c).strip()}*")
        elif c.tag == "a":
            out.append(f"[{inline(c).strip()}]({c.attrs.get('href', '')})")
        else:
            out.append(inline(c))
    return "".join(out)


def anchor(node):
    return f'<a name="{node.attrs["id"]}"></a>\n' if "id" in node.attrs else ""


def table(node):
    rows = [[inline(cell).strip().replace("|", r"\|") for cell in tr.children if isinstance(cell, Node)]
            for tr in all_of(node, "tr")]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def all_of(node, tag):
    for c in node.children:
        if isinstance(c, Node):
            if c.tag == tag:
                yield c
            else:
                yield from all_of(c, tag)


def message(node):
    """A message card: its name, (catalog, type), direction and status as a heading and a line, then the body."""
    head = node.find("header")
    name, mid = head.find("h3"), head.find(cls="id")
    direction, status = head.find(cls="dir"), head.find(cls="status")
    title = inline(name).strip() + (f" `{mid.text().strip()}`" if mid else "")
    about = " · ".join(x.text().strip() for x in (direction, status) if x)
    return [anchor(node) + f"#### {title}", f"*{about}*" if about else ""] + blocks(node.find(cls="body"))


def blocks(node):
    """Markdown blocks for the children of a section or a card body."""
    out = []
    for c in node.children:
        if isinstance(c, str):
            if c.strip():
                out.append(re.sub(r"\s+", " ", c).strip())
            continue
        cls = c.cls()
        if c.tag == "h2":
            out.append(f"## {inline(c).strip()}")
        elif c.tag == "h3":
            out.append(anchor(c) + f"### {inline(c).strip()}")
        elif c.tag == "p":
            text = inline(c).strip()
            out.append("> " + text if "note" in cls else text)
        elif c.tag == "pre":
            lang = "mermaid" if "mermaid" in cls else "text"
            out.append(f"```{lang}\n{c.text().strip(chr(10))}\n```")
        elif c.tag in ("ul", "ol"):
            mark = (lambda n: f"{n}.") if c.tag == "ol" else (lambda n: "-")
            out.append("\n".join(f"{mark(n)} {inline(li).strip()}" for n, li in enumerate(all_of(c, "li"), 1)))
        elif c.tag == "table" or "tablewrap" in cls:
            out.append(table(c))
        elif "msg" in cls:
            out += message(c)
        elif "legend" in cls:
            out.append("\n".join(f"- {inline(s).strip()}" for s in c.children if isinstance(s, Node)))
        elif c.tag in ("div", "section"):
            out += blocks(c)
        else:
            out.append(inline(c).strip())
    return [b for b in out if b]


def build(source):
    tree = Tree()
    tree.feed(source)
    main = tree.root.find("main")
    sections = [s for s in main.children if isinstance(s, Node) and s.tag == "section"]
    title = tree.root.find("title").text().strip()
    contents = []
    for s in sections:
        contents.append(f"- [{inline(s.find('h2')).strip()}](#{s.attrs['id']})")
        cards = [c for c in all_of(s, "div") if "msg" in c.cls() and "id" in c.attrs]
        for c in cards:
            mid = c.find(cls="id")
            contents.append(f"  - [{inline(c.find('h3')).strip()}](#{c.attrs['id']})"
                            + (f" `{mid.text().strip()}`" if mid else ""))
    out = [f"# {title}",
           "> Built from [index.html](index.html) by `server/tools/docs_md.py`. Edit the HTML page, then run the "
           "script; a test fails while this file is out of date.",
           "## Contents\n" + "\n".join(contents)]
    for s in sections:
        out.append(anchor(s).rstrip("\n"))
        out += blocks(s)
    return "\n\n".join(b for b in out if b) + "\n"


def main():
    with open(SOURCE, encoding="utf-8") as f:
        text = build(f.read())
    old = open(TARGET, encoding="utf-8").read() if os.path.exists(TARGET) else None
    if "--check" in sys.argv:
        print("PROTOCOL.md is up to date" if old == text else "PROTOCOL.md is out of date: run tools/docs_md.py")
        return 0 if old == text else 1
    if old != text:
        with open(TARGET, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    print(f"wrote {os.path.normpath(TARGET)} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
