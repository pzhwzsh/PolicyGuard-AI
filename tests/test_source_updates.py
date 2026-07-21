
from policyguard.application.html_ingestion import HtmlSection, parse_official_html
from policyguard.application.source_updates import (
    clean_source_sections,
    source_structure_quality,
)


def test_official_html_parser_prefers_main_content_and_preserves_headings() -> None:
    title, sections = parse_official_html(b"""
      <nav><p>Navigation noise</p></nav><main><h1>Official Rule</h1>
      <h2>Article 1</h2><p>Advertising must be truthful.</p></main>
    """)
    assert title == "Official Rule"
    assert len(sections) == 1
    assert sections[0].heading == "Article 1"
    assert sections[0].text == "Advertising must be truthful."


def test_samr_cleaner_keeps_articles_and_drops_navigation() -> None:
    sections = [
        HtmlSection("n", "Page", "Your location: home"),
        HtmlSection("a1", "Page", "第一条 广告应当真实。"),
        HtmlSection("a2", "Page", "第二条 广告不得欺骗消费者。"),
        HtmlSection("a3", "Page", "第三条 广告内容应当合法。"),
    ]
    source = {"id": "cn-samr-advertising-law", "ingestion_mode": "legal_document"}
    cleaned = clean_source_sections(source, sections)
    quality = source_structure_quality(source, cleaned)
    assert [item.heading for item in cleaned] == ["第一条", "第二条", "第三条"]
    assert quality["structural_review_status"] == "passed"


def test_catalog_source_is_never_eligible_for_activation() -> None:
    source = {"id": "catalog", "ingestion_mode": "source_catalog"}
    quality = source_structure_quality(
        source,
        [HtmlSection(str(index), "Link", f"Catalog link number {index}") for index in range(5)],
    )
    assert quality["eligible_for_activation"] is False
    assert quality["structural_review_status"] == "blocked"
