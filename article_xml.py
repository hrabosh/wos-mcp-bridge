"""Extract JATS text with stable section IDs; never execute XML entities or fetch links."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from xml.etree.ElementTree import Element, ParseError

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring

from literature_http import JsonObject, LiteratureError


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"p", "br", "div", "title"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "title"}:
            self.parts.append("\n")


def plain_text(value: str | None) -> str | None:
    if not value:
        return None
    parser = _PlainText()
    parser.feed(value)
    return _clean("".join(parser.parts)) or None


def _clean(text: str) -> str:
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


def _tag(node: Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _text(node: Element, *, own_section: bool = False) -> str:
    parts = [node.text or ""]
    for child in node:
        if not own_section or _tag(child) not in {"sec", "title"}:
            text = _text(child, own_section=own_section)
            if _tag(child) in {"p", "title", "list-item", "ref", "table-wrap", "caption"}:
                text = "\n" + text + "\n"
            parts.append(text)
        parts.append(child.tail or "")
    return "".join(parts)


def extract_jats(raw: bytes, *, expected_pmcid: str | None = None) -> JsonObject:
    try:
        root = fromstring(raw, forbid_entities=True, forbid_external=True)
    except (ParseError, DefusedXmlException, ValueError):
        raise LiteratureError("Europe PMC returned invalid or unsafe JATS XML.") from None
    if _tag(root) != "article":
        raise LiteratureError("Europe PMC returned XML that is not a JATS article.")
    stack = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > 128:
            raise LiteratureError("Europe PMC JATS XML exceeds the nesting limit.")
        stack.extend((child, depth + 1) for child in node)
    if expected_pmcid:
        for node in root.iter():
            if _tag(node) == "article-id" and node.get("pub-id-type") in {"pmc", "pmcid"}:
                value = "".join(node.itertext()).strip().upper().removeprefix("PMC")
                if value != expected_pmcid.removeprefix("PMC"):
                    raise LiteratureError("Europe PMC returned XML for a different article.")

    sections: list[JsonObject] = []
    reserved = {node.get("id") for node in root.iter() if node.get("id")}
    used: set[str] = set()

    def add(node: Element, heading: str | None, parent: str | None, kind: str) -> str:
        original = node.get("id")
        identifier = original
        if not identifier or identifier in used:
            number = len(sections) + 1
            identifier = f"section-{number}"
            while identifier in reserved or identifier in used:
                number += 1
                identifier = f"section-{number}"
        used.add(identifier)
        text = _clean(_text(node, own_section=True))
        sections.append(
            {
                "id": identifier,
                "original_id": original,
                "heading": heading,
                "parent_id": parent,
                "kind": kind,
                "text": text,
                "total_chars": len(text),
            }
        )
        return identifier

    def walk(node: Element, parent: str | None = None) -> None:
        kind = _tag(node)
        if kind in {"abstract", "sec", "ref-list", "ack", "app"}:
            title = next((child for child in node if _tag(child) == "title"), None)
            heading = _clean(_text(title)) if title is not None else None
            parent = add(node, heading, parent, kind)
        elif kind == "body" and _clean(_text(node, own_section=True)):
            parent = add(node, None, parent, "body")
        for child in node:
            walk(child, parent)

    walk(root)
    if not any(section["text"] for section in sections):
        raise LiteratureError("Europe PMC returned JATS XML without article text.")
    licenses = [node for node in root.iter() if _tag(node) == "license"]
    license_text = _clean(_text(licenses[0])) if licenses else None
    license_url = None
    if licenses:
        for node in licenses[0].iter():
            candidate = node.get("{http://www.w3.org/1999/xlink}href") or node.get("href")
            if candidate:
                license_url = candidate
                break
    return {"sections": sections, "license": license_text or None, "license_url": license_url}


def validate_text(value: str, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise LiteratureError(f"{name} must be a string.")
    if re.search(r"[\x00-\x1f\x7f]", value):
        raise LiteratureError(f"{name} must not contain control characters.")
    value = value.strip()
    if not value or len(value) > maximum:
        raise LiteratureError(
            f"{name} must contain 1-{maximum} characters without control characters."
        )
    return value
