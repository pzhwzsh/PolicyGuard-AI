"""Conservative HTML-to-policy draft parser for manual source-update review."""

import re
from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass(frozen=True, slots=True)
class HtmlSection:
    section_id: str
    heading: str
    text: str


class _PolicyHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_depth = 0
        self.scope_depth = 0
        self.current_tag = ""
        self.buffer: list[str] = []
        self.all_blocks: list[tuple[str, str]] = []
        self.scoped_blocks: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript", "svg", "template", "nav", "footer"}:
            self.hidden_depth += 1
        if tag in {"main", "article"}:
            self.scope_depth += 1
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li"}:
            self.current_tag = tag
            self.buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == self.current_tag and self.current_tag:
            text = " ".join(" ".join(self.buffer).split())
            if len(text) >= 2:
                self.all_blocks.append((tag, text))
                if self.scope_depth:
                    self.scoped_blocks.append((tag, text))
            self.current_tag = ""
            self.buffer = []
        if tag in {"main", "article"}:
            self.scope_depth = max(0, self.scope_depth - 1)
        if tag in {"script", "style", "noscript", "svg", "template", "nav", "footer"}:
            self.hidden_depth = max(0, self.hidden_depth - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth and self.current_tag and data.strip():
            self.buffer.append(data.strip())


def parse_official_html(content: bytes) -> tuple[str, list[HtmlSection]]:
    parser = _PolicyHtmlParser()
    parser.feed(content.decode("utf-8", errors="replace"))
    blocks = parser.scoped_blocks or parser.all_blocks
    title = next((text for tag, text in blocks if tag == "h1"), "Official source update")
    sections = []
    heading = title
    ordinal = 0
    for tag, text in blocks:
        if tag.startswith("h"):
            heading = text
            continue
        if text in {item.text for item in sections[-3:]}:
            continue
        ordinal += 1
        slug = re.sub(r"[^a-z0-9]+", "-", heading.casefold()).strip("-")[:50]
        sections.append(HtmlSection(f"html-{ordinal}-{slug or 'section'}", heading, text))
    return title, sections
