"""EU Publications Office CELLAR content negotiation client."""

import re
from dataclasses import dataclass
from xml.etree import ElementTree

import httpx


@dataclass(frozen=True, slots=True)
class CellarDocument:
    celex: str
    expression_url: str
    final_url: str
    content: bytes
    content_type: str
    etag: str | None
    last_modified: str | None


class CellarClient:
    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def fetch_xhtml(self, celex: str, language: str = "ENG") -> CellarDocument:
        if not re.fullmatch(r"[0-9A-Z-]{8,30}", celex):
            raise ValueError("invalid_celex")
        work_url = f"https://publications.europa.eu/resource/celex/{celex}"
        rdf_response = self.client.get(work_url, headers={"Accept": "application/rdf+xml"})
        rdf_response.raise_for_status()
        root = ElementTree.fromstring(rdf_response.content)
        resource_key = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}resource"
        candidates = [
            element.attrib[resource_key]
            for element in root.iter()
            if element.tag.endswith("work_has_expression")
            and element.attrib.get(resource_key, "").upper().endswith(f".{language.upper()}")
        ]
        if not candidates:
            raise RuntimeError("cellar_language_expression_not_found")
        expression_url = candidates[0]
        content_response = self.client.get(
            expression_url, headers={"Accept": "application/xhtml+xml"}
        )
        content_response.raise_for_status()
        if not content_response.content.strip():
            raise RuntimeError("cellar_empty_content")
        return CellarDocument(
            celex=celex,
            expression_url=expression_url,
            final_url=str(content_response.url),
            content=content_response.content,
            content_type=content_response.headers.get("content-type", ""),
            etag=content_response.headers.get("etag"),
            last_modified=content_response.headers.get("last-modified"),
        )
