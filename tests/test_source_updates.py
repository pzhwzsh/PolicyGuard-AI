import json
from pathlib import Path

from policyguard.application.html_ingestion import HtmlSection, parse_official_html
from policyguard.application.source_updates import (
    clean_source_sections,
    revise_staged_source_update,
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
    source = {
        "id": "cn-samr-advertising-law",
        "ingestion_mode": "legal_document",
        "published_at": "2021-04-29",
        "effective_from": "2021-04-29",
    }
    cleaned = clean_source_sections(source, sections)
    quality = source_structure_quality(source, cleaned)
    assert [item.heading for item in cleaned] == ["第一条", "第二条", "第三条"]
    assert quality["structural_review_status"] == "passed"
    assert quality["quality_schema_version"] == "2.0"
    assert quality["generic_heading_rate"] == 0


def test_quality_gate_blocks_generic_headings_and_missing_dates() -> None:
    sections = [
        HtmlSection(str(index), "Official source update", f"Paragraph {index} legal text")
        for index in range(5)
    ]
    quality = source_structure_quality(
        {"id": "eu-law", "ingestion_mode": "legal_document"}, sections
    )
    assert quality["structural_review_status"] == "blocked"
    assert quality["blocking_reasons"] == [
        "generic_heading_dominance",
        "temporal_metadata_missing",
    ]


def test_catalog_source_is_never_eligible_for_activation() -> None:
    source = {"id": "catalog", "ingestion_mode": "source_catalog"}
    quality = source_structure_quality(
        source,
        [HtmlSection(str(index), "Link", f"Catalog link number {index}") for index in range(5)],
    )
    assert quality["eligible_for_activation"] is False
    assert quality["structural_review_status"] == "blocked"


def test_manual_structural_correction_is_versioned_and_needs_separate_legal_review(
    tmp_path: Path,
) -> None:
    source_id = "eu-law"
    content_hash = "a" * 64
    directory = tmp_path / source_id / content_hash
    directory.mkdir(parents=True)
    policy_path = directory / "policy.json"
    policy_path.write_text(json.dumps({
        "published_at": "undated",
        "scopes": [{"effective_from": "1900-01-01"}],
        "sections": [
            {"section_id": f"s-{index}", "heading": "Official source update",
             "text": f"Legal text paragraph {index}."}
            for index in range(3)
        ],
    }), encoding="utf-8")
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(json.dumps({
        "source_id": source_id, "content_hash": content_hash, "status": "staged",
        "ingestion_mode": "legal_document", "policy_path": str(policy_path), "revision": 0,
    }), encoding="utf-8")

    revised = revise_staged_source_update(
        tmp_path, source_id, content_hash, reviewer="reviewer-1", expected_revision=0,
        published_at="2024-01-01", effective_from="2024-02-01",
        heading_overrides={"s-0": "Article 1", "s-1": "Article 2", "s-2": "Article 3"},
    )
    assert revised["revision"] == 1
    assert revised["structural_review_status"] == "passed"
    assert revised["legal_review_status"] == "pending"
    assert revised["structural_review_history"][0]["changed_heading_count"] == 3

    try:
        revise_staged_source_update(
            tmp_path, source_id, content_hash, reviewer="reviewer-2", expected_revision=0,
            published_at="2024-01-01",
        )
    except RuntimeError as exc:
        assert str(exc) == "source_update_revision_conflict"
    else:
        raise AssertionError("stale correction was accepted")
