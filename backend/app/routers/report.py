"""
PDF report generation for clinical neoantigen analysis.

Produces a structured report containing:
  1. Patient / analysis header
  2. HLA typing
  3. Ranked epitope table (top 50)
  4. Score distribution summary
  5. Construct summary (if provided)
  6. Disclaimer

Uses reportlab for PDF generation. The report is returned as a streaming
binary response -- no files are written to disk.
"""
import io
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Epitope, Variant, Analysis, Project, HLAType, User
from app.routers.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/report", tags=["report"])


def _confidence_tier(score: float, affinity_nm: float) -> str:
    if score >= 0.7 and affinity_nm <= 50:
        return "High"
    if score >= 0.4 and affinity_nm <= 500:
        return "Medium"
    return "Low"


def _generate_pdf(
    analysis: Analysis,
    project: Project,
    hla_alleles: list[str],
    epitopes: list[Epitope],
    variant_count: int,
) -> bytes:
    """Generate the PDF report in memory and return bytes."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable,
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=25 * mm,
        bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontSize=18,
        spaceAfter=4 * mm,
        textColor=colors.HexColor("#004875"),
    )
    heading_style = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading2"],
        fontSize=12,
        spaceBefore=8 * mm,
        spaceAfter=3 * mm,
        textColor=colors.HexColor("#004875"),
    )
    body_style = styles["Normal"]
    small_style = ParagraphStyle(
        "Small",
        parent=body_style,
        fontSize=8,
        textColor=colors.HexColor("#666666"),
    )
    mono_style = ParagraphStyle(
        "Mono",
        parent=body_style,
        fontName="Courier",
        fontSize=8,
    )

    elements = []

    # -- Title --
    elements.append(Paragraph("Oxford Cancer Vaccine Design", title_style))
    elements.append(Paragraph("Neoantigen Prediction Report", styles["Heading3"]))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#004875")))
    elements.append(Spacer(1, 4 * mm))

    # -- Patient / Analysis Info --
    elements.append(Paragraph("Analysis Information", heading_style))

    info_data = [
        ["Project:", project.name],
        ["Cancer Type:", project.cancer_type],
        ["Reference Genome:", project.reference_genome],
        ["Analysis ID:", str(analysis.id)],
        ["Status:", analysis.status.capitalize()],
        ["Input Type:", analysis.input_type.upper()],
        ["Created:", analysis.created_at.strftime("%Y-%m-%d %H:%M UTC") if analysis.created_at else "-"],
        ["Completed:", analysis.completed_at.strftime("%Y-%m-%d %H:%M UTC") if analysis.completed_at else "-"],
        ["Variants Found:", str(variant_count)],
        ["Epitopes Predicted:", str(len(epitopes))],
    ]

    info_table = Table(info_data, colWidths=[40 * mm, 120 * mm])
    info_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#004875")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    elements.append(info_table)

    # -- HLA Typing --
    elements.append(Paragraph("HLA Typing", heading_style))
    if hla_alleles:
        hla_text = ", ".join(hla_alleles)
        elements.append(Paragraph(hla_text, mono_style))
    else:
        elements.append(Paragraph("No HLA alleles recorded", body_style))

    # -- Score summary --
    elements.append(Paragraph("Score Distribution", heading_style))

    high = sum(1 for e in epitopes if _confidence_tier(e.immunogenicity_score, e.binding_affinity_nm) == "High")
    medium = sum(1 for e in epitopes if _confidence_tier(e.immunogenicity_score, e.binding_affinity_nm) == "Medium")
    low = sum(1 for e in epitopes if _confidence_tier(e.immunogenicity_score, e.binding_affinity_nm) == "Low")

    dist_data = [
        ["Tier", "Count", "Criteria"],
        ["High", str(high), "Score >= 0.7 AND IC50 <= 50 nM"],
        ["Medium", str(medium), "Score >= 0.4 AND IC50 <= 500 nM"],
        ["Low", str(low), "Below medium thresholds"],
    ]
    dist_table = Table(dist_data, colWidths=[25 * mm, 20 * mm, 80 * mm])
    dist_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#004875")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#ddd")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f8fa")]),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(dist_table)

    # -- Epitope Table --
    elements.append(Paragraph("Ranked Epitope Predictions (Top 50)", heading_style))

    # Table header
    ep_header = [
        "Rank", "Peptide", "Gene", "Mutation", "HLA", "IC50 (nM)",
        "Score", "DAI", "Tier",
    ]

    ep_data = [ep_header]
    display_eps = sorted(epitopes, key=lambda e: e.rank)[:50]

    for ep in display_eps:
        v = ep.variant
        tier = _confidence_tier(ep.immunogenicity_score, ep.binding_affinity_nm)
        dai_str = f"{ep.dai_score:.2f}" if ep.dai_score is not None else "-"
        ep_data.append([
            str(ep.rank),
            ep.peptide_seq,
            (v.gene if v else "-") or "-",
            (v.protein_change if v else "-") or "-",
            ep.hla_allele,
            f"{ep.binding_affinity_nm:.0f}",
            f"{ep.immunogenicity_score:.4f}",
            dai_str,
            tier,
        ])

    col_widths = [12*mm, 32*mm, 18*mm, 22*mm, 28*mm, 16*mm, 16*mm, 12*mm, 14*mm]
    ep_table = Table(ep_data, colWidths=col_widths, repeatRows=1)
    ep_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#004875")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (1, 1), (1, -1), "Courier"),  # peptide column monospace
        ("FONTNAME", (3, 1), (4, -1), "Courier"),  # mutation + HLA monospace
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#ddd")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f8fa")]),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),  # rank
        ("ALIGN", (5, 0), (7, -1), "RIGHT"),   # numeric columns
    ]))

    # Color-code tier column
    for i, ep in enumerate(display_eps, start=1):
        tier = _confidence_tier(ep.immunogenicity_score, ep.binding_affinity_nm)
        if tier == "High":
            ep_table.setStyle(TableStyle([
                ("TEXTCOLOR", (8, i), (8, i), colors.HexColor("#166534")),
            ]))
        elif tier == "Medium":
            ep_table.setStyle(TableStyle([
                ("TEXTCOLOR", (8, i), (8, i), colors.HexColor("#92400e")),
            ]))
        else:
            ep_table.setStyle(TableStyle([
                ("TEXTCOLOR", (8, i), (8, i), colors.HexColor("#991b1b")),
            ]))

    elements.append(ep_table)

    if len(epitopes) > 50:
        elements.append(Spacer(1, 2 * mm))
        elements.append(Paragraph(
            f"Showing top 50 of {len(epitopes)} total epitopes. "
            "Export full results via CSV for complete data.",
            small_style
        ))

    # -- Disclaimer --
    elements.append(Spacer(1, 10 * mm))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#ccc")))
    elements.append(Spacer(1, 3 * mm))
    elements.append(Paragraph(
        "DISCLAIMER: This report is generated by the Oxford Cancer Vaccine Design (OCVD) platform "
        "for research purposes only. It is not a clinical diagnostic or treatment recommendation. "
        "All predictions are computational and must be validated experimentally before clinical use. "
        "Neoantigen predictions depend on the quality of input data (variant calls, HLA typing) "
        "and the accuracy of MHC binding prediction models.",
        small_style,
    ))
    elements.append(Spacer(1, 2 * mm))
    elements.append(Paragraph(
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} | "
        f"OCVD v0.1.0 | MHCflurry 2.0 | IC50 threshold: 500 nM",
        small_style,
    ))

    doc.build(elements)
    return buffer.getvalue()


@router.get("/{analysis_id}/pdf")
async def generate_report_pdf(
    analysis_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a clinical-style PDF report for an analysis.

    Returns a downloadable PDF containing analysis summary, HLA typing,
    ranked epitope table (top 50), score distribution, and disclaimer.
    """
    # Ownership check
    stmt = (
        select(Analysis, Project)
        .join(Project, Analysis.project_id == Project.id)
        .where(Analysis.id == analysis_id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found")

    analysis, project = row
    if project.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Fetch HLA alleles
    hla_stmt = select(HLAType.allele).where(HLAType.analysis_id == analysis_id)
    hla_result = await db.execute(hla_stmt)
    hla_alleles = [r for r in hla_result.scalars().all()]

    # Fetch epitopes with variants
    ep_stmt = (
        select(Epitope)
        .options(selectinload(Epitope.variant))
        .where(Epitope.analysis_id == analysis_id)
        .order_by(Epitope.rank)
    )
    ep_result = await db.execute(ep_stmt)
    epitopes = list(ep_result.scalars().all())

    # Variant count
    var_count_stmt = select(func.count()).select_from(Variant).where(Variant.analysis_id == analysis_id)
    variant_count = (await db.execute(var_count_stmt)).scalar() or 0

    # Generate PDF
    pdf_bytes = _generate_pdf(analysis, project, hla_alleles, epitopes, variant_count)

    filename = f"ocvd_report_analysis_{analysis_id}.pdf"

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
        },
    )
