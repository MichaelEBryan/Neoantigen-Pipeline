from datetime import datetime, timezone
from sqlalchemy import String, Integer, Float, Text, DateTime, ForeignKey, Boolean, BigInteger, JSON, Index
from sqlalchemy.orm import relationship, mapped_column, Mapped
from typing import List, Optional, Dict, Any
from app.database import Base


def _utcnow() -> datetime:
    """Timezone-aware UTC now. Replaces deprecated datetime.utcnow()."""
    return datetime.now(timezone.utc)


class User(Base):
    """User account for CVDash platform."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    institution: Mapped[Optional[str]] = mapped_column(String(255))
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    terms_accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    projects: Mapped[List["Project"]] = relationship(
        "Project", back_populates="user", cascade="all, delete-orphan"
    )
    preferences: Mapped[Optional["UserPreferences"]] = relationship(
        "UserPreferences", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class UserPreferences(Base):
    """Per-user settings: analysis defaults, scoring weights, display preferences."""
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), unique=True, nullable=False)

    # Analysis defaults -- pre-fill the "New Analysis" form
    default_cancer_type: Mapped[Optional[str]] = mapped_column(String(255))
    default_stage: Mapped[Optional[str]] = mapped_column(String(10))
    default_genome: Mapped[Optional[str]] = mapped_column(String(50))
    default_hla_alleles: Mapped[Optional[str]] = mapped_column(Text)  # comma-separated HLA alleles

    # Scoring weights (7 components, each 0.0-1.0, should sum to ~1.0)
    # NULL = use system defaults from scorer.py
    weight_presentation: Mapped[Optional[float]] = mapped_column(Float)
    weight_binding_rank: Mapped[Optional[float]] = mapped_column(Float)
    weight_expression: Mapped[Optional[float]] = mapped_column(Float)
    weight_vaf: Mapped[Optional[float]] = mapped_column(Float)
    weight_mutation_type: Mapped[Optional[float]] = mapped_column(Float)
    weight_processing: Mapped[Optional[float]] = mapped_column(Float)
    weight_iedb: Mapped[Optional[float]] = mapped_column(Float)

    # Display preferences
    theme: Mapped[Optional[str]] = mapped_column(String(20))  # "light", "dark", "system"
    results_page_size: Mapped[Optional[int]] = mapped_column(Integer)  # 25, 50, 100
    default_visible_columns: Mapped[Optional[str]] = mapped_column(Text)  # comma-separated column keys

    user: Mapped["User"] = relationship("User", back_populates="preferences")


class Project(Base):
    """Analysis project for a user."""
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    cancer_type: Mapped[str] = mapped_column(String(255), nullable=False)
    stage: Mapped[Optional[str]] = mapped_column(String(10))  # I, II, III, IV
    reference_genome: Mapped[str] = mapped_column(String(50), default="GRCh38")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship("User", back_populates="projects")
    analyses: Mapped[List["Analysis"]] = relationship(
        "Analysis", back_populates="project", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_projects_user_id", "user_id"),
    )

    def __repr__(self) -> str:
        return f"<Project {self.name}>"


class Analysis(Base):
    """Analysis job for processing sequencing data."""
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="queued")  # queued/running/complete/failed
    input_type: Mapped[str] = mapped_column(String(50), nullable=False)  # fastq/bam/vcf
    hla_provided: Mapped[bool] = mapped_column(Boolean, default=False)
    isambard_job_id: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    project: Mapped["Project"] = relationship("Project", back_populates="analyses")
    inputs: Mapped[List["AnalysisInput"]] = relationship(
        "AnalysisInput", back_populates="analysis", cascade="all, delete-orphan"
    )
    hla_types: Mapped[List["HLAType"]] = relationship(
        "HLAType", back_populates="analysis", cascade="all, delete-orphan"
    )
    variants: Mapped[List["Variant"]] = relationship(
        "Variant", back_populates="analysis", cascade="all, delete-orphan"
    )
    epitopes: Mapped[List["Epitope"]] = relationship(
        "Epitope", back_populates="analysis", cascade="all, delete-orphan"
    )
    job_logs: Mapped[List["JobLog"]] = relationship(
        "JobLog", back_populates="analysis", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_analyses_project_id", "project_id"),
    )

    def __repr__(self) -> str:
        return f"<Analysis {self.id}>"


class AnalysisInput(Base):
    """Input files for an analysis."""
    __tablename__ = "analysis_inputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(Integer, ForeignKey("analyses.id"), nullable=False)
    file_type: Mapped[str] = mapped_column(String(50), nullable=False)  # fastq, bam, vcf, rna_seq, etc
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    checksum: Mapped[Optional[str]] = mapped_column(String(255))

    analysis: Mapped["Analysis"] = relationship("Analysis", back_populates="inputs")

    __table_args__ = (
        Index("ix_analysis_inputs_analysis_id", "analysis_id"),
    )

    def __repr__(self) -> str:
        return f"<AnalysisInput {self.file_type}>"


class HLAType(Base):
    """HLA allele assignment for an analysis."""
    __tablename__ = "hla_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(Integer, ForeignKey("analyses.id"), nullable=False)
    allele: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g., HLA-A*02:01
    source: Mapped[str] = mapped_column(String(50), nullable=False)  # provided/predicted

    analysis: Mapped["Analysis"] = relationship("Analysis", back_populates="hla_types")

    __table_args__ = (
        Index("ix_hla_types_analysis_id", "analysis_id"),
    )

    def __repr__(self) -> str:
        return f"<HLAType {self.allele}>"


class Variant(Base):
    """Somatic variant identified in sequencing data."""
    __tablename__ = "variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(Integer, ForeignKey("analyses.id"), nullable=False)
    chrom: Mapped[str] = mapped_column(String(50), nullable=False)
    pos: Mapped[int] = mapped_column(Integer, nullable=False)
    ref: Mapped[str] = mapped_column(String(500), nullable=False)
    alt: Mapped[str] = mapped_column(String(500), nullable=False)
    gene: Mapped[Optional[str]] = mapped_column(String(100))
    protein_change: Mapped[Optional[str]] = mapped_column(String(255))
    variant_type: Mapped[str] = mapped_column(String(50), nullable=False)  # missense, frameshift, etc
    vaf: Mapped[Optional[float]] = mapped_column(Float)  # variant allele frequency
    annotation_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)

    analysis: Mapped["Analysis"] = relationship("Analysis", back_populates="variants")
    epitopes: Mapped[List["Epitope"]] = relationship(
        "Epitope", back_populates="variant", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_variants_analysis_id", "analysis_id"),
        Index("ix_variants_gene", "gene"),
    )

    def __repr__(self) -> str:
        return f"<Variant {self.chrom}:{self.pos}>"


class Epitope(Base):
    """Predicted neoantigen epitope."""
    __tablename__ = "epitopes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(Integer, ForeignKey("analyses.id"), nullable=False)
    variant_id: Mapped[int] = mapped_column(Integer, ForeignKey("variants.id"), nullable=False)
    peptide_seq: Mapped[str] = mapped_column(String(500), nullable=False)
    peptide_length: Mapped[int] = mapped_column(Integer, nullable=False)
    hla_allele: Mapped[str] = mapped_column(String(50), nullable=False)
    binding_affinity_nm: Mapped[float] = mapped_column(Float, nullable=False)
    presentation_score: Mapped[float] = mapped_column(Float, nullable=False)
    processing_score: Mapped[Optional[float]] = mapped_column(Float)
    expression_tpm: Mapped[Optional[float]] = mapped_column(Float)
    immunogenicity_score: Mapped[float] = mapped_column(Float, nullable=False)
    dai_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # Differential Agretopicity Index
    wt_binding_affinity_nm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # WT IC50 for DAI
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    explanation_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)

    analysis: Mapped["Analysis"] = relationship("Analysis", back_populates="epitopes")
    variant: Mapped["Variant"] = relationship("Variant", back_populates="epitopes")

    __table_args__ = (
        Index("ix_epitopes_analysis_id", "analysis_id"),
        Index("ix_epitopes_variant_id", "variant_id"),
        Index("ix_epitopes_rank", "rank"),
        Index("ix_epitopes_immunogenicity_score", "immunogenicity_score"),
    )

    def __repr__(self) -> str:
        return f"<Epitope {self.peptide_seq}>"


class JobLog(Base):
    """Log entries for analysis job progress."""
    __tablename__ = "job_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(Integer, ForeignKey("analyses.id"), nullable=False)
    step: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    analysis: Mapped["Analysis"] = relationship("Analysis", back_populates="job_logs")

    __table_args__ = (
        Index("ix_job_logs_analysis_id", "analysis_id"),
    )

    def __repr__(self) -> str:
        return f"<JobLog {self.step}>"
