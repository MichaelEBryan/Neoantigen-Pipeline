// Enums
export type AnalysisStatus = "queued" | "running" | "complete" | "failed";
export type InputType = "fastq" | "bam" | "vcf";
export type HLASource = "provided" | "predicted";

// Core types
export interface User {
  id: number;
  email: string;
  name: string;
  institution: string;
  terms_accepted_at: string | null;
  created_at: string; // ISO date
}

export interface Project {
  id: number;
  user_id: number;
  name: string;
  cancer_type: string;
  stage: string | null;
  reference_genome: string;
  created_at: string;
}

export interface Analysis {
  id: number;
  project_id: number;
  status: AnalysisStatus;
  input_type: InputType;
  hla_provided: boolean;
  isambard_job_id: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface AnalysisInput {
  id: number;
  analysis_id: number;
  file_type: string;
  file_path: string;
  file_size: number | null;
  checksum: string | null;
}

export interface HLAType {
  id: number;
  analysis_id: number;
  allele: string; // e.g. "HLA-A*02:01"
  source: HLASource;
}

export interface Variant {
  id: number;
  analysis_id: number;
  chrom: string;
  pos: number;
  ref: string;
  alt: string;
  gene: string | null;
  protein_change: string | null;
  variant_type: string;
  vaf: number | null;
  annotation_json: Record<string, unknown> | null;
}

export interface Epitope {
  id: number;
  analysis_id: number;
  variant_id: number;
  peptide_seq: string;
  peptide_length: number;
  hla_allele: string;
  binding_affinity_nm: number;
  presentation_score: number;
  processing_score: number | null;
  expression_tpm: number | null;
  immunogenicity_score: number;
  rank: number;
  explanation_json: Record<string, unknown> | null;
}

// Enriched epitope for results table (joined with variant data)
export interface EpitopeResult extends Epitope {
  gene: string | null;
  genomic_position: string; // "chr1:12345"
  mutation_type: string;
  protein_change: string | null;
}

export interface JobLog {
  id: number;
  analysis_id: number;
  step: string;
  status: string;
  message: string | null;
  timestamp: string;
}

// Job status for real-time updates
export interface JobStatus {
  analysis_id: number;
  current_step: string;
  status: AnalysisStatus;
  steps: JobStepInfo[];
  progress_pct: number;
  estimated_remaining_sec: number | null;
}

export interface JobStepInfo {
  name: string;
  status: "pending" | "running" | "complete" | "failed";
  started_at: string | null;
  completed_at: string | null;
}

// API request types
export interface CreateAnalysisRequest {
  project_id: number;
  input_type: InputType;
  hla_provided: boolean;
  hla_alleles?: string[]; // if provided
  cancer_type: string;
  stage?: string;
  reference_genome?: string;
}

export interface EpitopeFilters {
  gene?: string;
  variant_type?: string;
  hla_allele?: string;
  min_score?: number;
  sort_by?: "rank" | "immunogenicity_score" | "binding_affinity_nm" | "presentation_score";
  sort_order?: "asc" | "desc";
}
