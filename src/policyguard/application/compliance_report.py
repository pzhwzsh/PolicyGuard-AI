"""Deterministic exports built only from persisted workflow evidence and decisions."""

from datetime import UTC, datetime
from html import escape
from io import BytesIO
from pathlib import Path

from policyguard.domain.workflow import WorkflowRun


def build_compliance_report(run: WorkflowRun) -> dict:
    markets = []
    for market in run.result_payload.get("markets", []):
        evidence = [
            {
                "section_id": item.get("section_id"),
                "heading": item.get("heading"),
                "quote": item.get("text"),
                "source_url": item.get("source_url"),
                "retrieval_score": item.get("score"),
            }
            for item in market.get("candidate_evidence", [])
        ]
        markets.append({
            "market": market.get("market"),
            "evidence_support": market.get("evidence_support"),
            "evidence": evidence,
        })
    model_events = [
        event for event in run.events
        if event.step in {"claim_extraction_llm", "query_rewrite", "evidence_support_llm"}
    ]
    return {
        "schema_version": "policyguard-report-v1",
        "report_id": f"report-{run.id}",
        "workflow_id": run.id,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": run.status.value,
        "evidence_only": True,
        "product": run.input_payload.get("product", {}),
        "scope": {
            "markets": run.input_payload.get("markets", []),
            "category": run.input_payload.get("category"),
            "channel": run.input_payload.get("channel"),
            "as_of": run.input_payload.get("as_of"),
        },
        "claims": run.result_payload.get("claims", []),
        "markets": markets,
        "review": run.result_payload.get("review"),
        "remediation_plan": run.result_payload.get("remediation_plan"),
        "draft": run.result_payload.get("draft"),
        "model_usage": [
            {"step": event.step, **event.detail}
            for event in model_events
        ],
        "limitations": [
            "Candidate evidence is not a legal opinion.",
            "Official source context and current effective version require human verification.",
            "A missing result does not prove that no applicable rule exists.",
        ],
    }


def report_markdown(report: dict) -> str:
    product = report["product"]
    lines = [
        "# PolicyGuard Compliance Evidence Report",
        "",
        f"- Workflow: `{report['workflow_id']}`",
        f"- Status: `{report['status']}`",
        f"- Product: {product.get('title', '')}",
        f"- Generated: {report['generated_at']}",
        "",
        "## Claims",
        "",
    ]
    for claim in report["claims"]:
        lines.append(f"- {claim.get('text', '')}")
    for market in report["markets"]:
        lines.extend(["", f"## {market['market']} evidence", ""])
        for item in market["evidence"]:
            lines.extend([
                f"### {item['section_id']} - {item['heading']}",
                "",
                f"> {item['quote']}",
                "",
                f"Source: {item['source_url']}",
            ])
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines).strip() + "\n"


def report_pdf(report: dict) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError as exc:
        raise RuntimeError("install policyguard-ai[pdf] for PDF reports") from exc
    styles = getSampleStyleSheet()
    font_candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]
    font = next((path for path in font_candidates if path.exists()), None)
    if font:
        pdfmetrics.registerFont(TTFont("PolicyGuardUnicode", str(font)))
        for style in styles.byName.values():
            style.fontName = "PolicyGuardUnicode"
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, title="PolicyGuard Compliance Report")
    story = [Paragraph("PolicyGuard Compliance Evidence Report", styles["Title"]), Spacer(1, 12)]
    story.append(Paragraph(f"Workflow: {escape(report['workflow_id'])}", styles["BodyText"]))
    story.append(Paragraph(f"Status: {escape(report['status'])}", styles["BodyText"]))
    for market in report["markets"]:
        story.extend([
            Spacer(1, 12),
            Paragraph(f"{escape(str(market['market']))} evidence", styles["Heading2"]),
        ])
        for item in market["evidence"]:
            story.append(Paragraph(
                f"{escape(str(item['section_id']))} - {escape(str(item['heading']))}: "
                f"{escape(str(item['quote']))}",
                styles["BodyText"],
            ))
    story.extend([Spacer(1, 12), Paragraph("Limitations", styles["Heading2"])])
    story.extend(Paragraph(escape(item), styles["BodyText"]) for item in report["limitations"])
    document.build(story)
    return output.getvalue()
