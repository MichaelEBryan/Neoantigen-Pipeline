"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { useSession } from "next-auth/react";
import { useRouter } from "next/navigation";
import PrivacyBanner from "@/components/privacy-banner";

// -- Constants --

const CANCER_TYPES = [
  { value: "Melanoma", label: "Melanoma", abbr: "MEL" },
  { value: "Non-Small Cell Lung Cancer (NSCLC)", label: "NSCLC", abbr: "NSCLC" },
  { value: "Small Cell Lung Cancer (SCLC)", label: "SCLC", abbr: "SCLC" },
  { value: "Colorectal Cancer", label: "Colorectal", abbr: "CRC" },
  { value: "Triple-Negative Breast Cancer", label: "TNBC", abbr: "TNBC" },
  { value: "Ovarian Cancer", label: "Ovarian", abbr: "OV" },
  { value: "Pancreatic Adenocarcinoma", label: "Pancreatic", abbr: "PAAD" },
  { value: "Renal Cell Carcinoma", label: "Renal", abbr: "RCC" },
  { value: "Hepatocellular Carcinoma", label: "Hepatocellular", abbr: "HCC" },
  { value: "Gastric Cancer", label: "Gastric", abbr: "GC" },
  { value: "Head and Neck Squamous Cell Carcinoma", label: "Head & Neck", abbr: "HNSC" },
  { value: "Bladder Urothelial Carcinoma", label: "Bladder", abbr: "BLCA" },
  { value: "Merkel Cell Carcinoma", label: "Merkel Cell", abbr: "MCC" },
  { value: "Glioblastoma", label: "Glioblastoma", abbr: "GBM" },
  { value: "Other", label: "Other", abbr: "..." },
] as const;

const STAGES = ["I", "II", "III", "IV"] as const;
const REFERENCE_GENOMES = ["GRCh38", "GRCh37"] as const;

const INPUT_TYPES = [
  {
    value: "vcf" as const,
    label: "VCF",
    fullLabel: "VCF (Variant Calls)",
    description: "Pre-called somatic variants in VCF format.",
    accept: ".vcf,.vcf.gz",
    enabled: true,
  },
  {
    value: "csv" as const,
    label: "CSV / TXT",
    fullLabel: "CSV / TXT (Variant Table)",
    description: "Simple variant table, MAF, or annotated TXT file.",
    accept: ".csv,.tsv,.txt,.maf",
    enabled: true,
  },
  {
    value: "bam" as const,
    label: "BAM",
    fullLabel: "BAM (Aligned Reads)",
    description: "Requires variant calling pipeline. Coming soon.",
    accept: ".bam",
    enabled: false,
  },
  {
    value: "fastq" as const,
    label: "FASTQ",
    fullLabel: "FASTQ (Raw Reads)",
    description: "Full pipeline: alignment + calling. Coming soon.",
    accept: ".fastq,.fastq.gz,.fq,.fq.gz",
    enabled: false,
  },
];

const COMMON_HLA_ALLELES = [
  "HLA-A*01:01", "HLA-A*02:01", "HLA-A*03:01", "HLA-A*11:01", "HLA-A*24:02",
  "HLA-B*07:02", "HLA-B*08:01", "HLA-B*15:01", "HLA-B*35:01", "HLA-B*44:02",
  "HLA-C*03:04", "HLA-C*04:01", "HLA-C*05:01", "HLA-C*06:02", "HLA-C*07:01",
];

// -- Types --

interface FormState {
  projectName: string;
  cancerType: string;
  customCancerType: string;
  stage: string;
  referenceGenome: string;
  inputType: "vcf" | "csv" | "bam" | "fastq";
  hlaMode: "auto" | "manual";
  hlaAlleles: string[];
  hlaInput: string;
  tumorNormalPaired: boolean;
  includeRnaSeq: boolean;
  expressionMode: "none" | "raw_rnaseq" | "matrix";
  estimatedPurity: string;
  patientAge: string;
  patientSex: string;
}

// Steps: upload first, then configure, then review
type FormStep = "upload" | "config" | "review";
const STEPS: { key: FormStep; label: string }[] = [
  { key: "upload", label: "Upload" },
  { key: "config", label: "Configure" },
  { key: "review", label: "Review" },
];

const HLA_REGEX = /^(HLA-)?[ABC]\*\d{2,3}:\d{2,3}$/i;

function normalizeHla(allele: string): string {
  let v = allele.trim().toUpperCase();
  if (!v.startsWith("HLA-")) v = `HLA-${v}`;
  return v;
}

// Auto-detect input type from file extension
function detectInputType(filename: string): "vcf" | "csv" | null {
  const lower = filename.toLowerCase();
  if (lower.endsWith(".vcf") || lower.endsWith(".vcf.gz")) return "vcf";
  if (lower.endsWith(".csv") || lower.endsWith(".tsv") || lower.endsWith(".maf") || lower.endsWith(".txt")) return "csv";
  return null;
}

// File type badge colors for visual feedback on drop
const FILE_TYPE_COLORS: Record<string, { bg: string; text: string; label: string }> = {
  vcf: { bg: "#dcfce7", text: "#166534", label: "VCF" },
  csv: { bg: "#dbeafe", text: "#1e40af", label: "CSV/TXT" },
  maf: { bg: "#e0e7ff", text: "#3730a3", label: "MAF" },
  tsv: { bg: "#dbeafe", text: "#1e40af", label: "TSV" },
  txt: { bg: "#f3e8ff", text: "#6b21a8", label: "TXT" },
  gz: { bg: "#fef3c7", text: "#92400e", label: "GZ" },
  unknown: { bg: "#f3f4f6", text: "#374151", label: "?" },
};

function getFileTypeBadge(filename: string) {
  const lower = filename.toLowerCase();
  if (lower.endsWith(".vcf.gz")) return FILE_TYPE_COLORS.vcf;
  if (lower.endsWith(".vcf")) return FILE_TYPE_COLORS.vcf;
  if (lower.endsWith(".maf")) return FILE_TYPE_COLORS.maf;
  if (lower.endsWith(".csv")) return FILE_TYPE_COLORS.csv;
  if (lower.endsWith(".tsv")) return FILE_TYPE_COLORS.tsv;
  if (lower.endsWith(".txt")) return FILE_TYPE_COLORS.txt;
  if (lower.endsWith(".gz")) return FILE_TYPE_COLORS.gz;
  return FILE_TYPE_COLORS.unknown;
}

export default function NewAnalysisPage() {
  const { data: session } = useSession();
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const exprFileInputRef = useRef<HTMLInputElement>(null);

  const [form, setForm] = useState<FormState>({
    projectName: "",
    cancerType: "",
    customCancerType: "",
    stage: "",
    referenceGenome: "GRCh38",
    inputType: "vcf",
    hlaMode: "auto",
    hlaAlleles: [],
    hlaInput: "",
    tumorNormalPaired: false,
    includeRnaSeq: false,
    expressionMode: "none",
    estimatedPurity: "",
    patientAge: "",
    patientSex: "",
  });

  // Load user defaults from settings API
  useEffect(() => {
    const token = session?.accessToken;
    if (!token) return;
    fetch("/api/py/api/settings/", {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data) return;
        setForm((prev) => ({
          ...prev,
          cancerType: data.default_cancer_type || prev.cancerType,
          stage: data.default_stage || prev.stage,
          referenceGenome: data.default_genome || prev.referenceGenome,
          hlaAlleles: data.default_hla_alleles?.length ? data.default_hla_alleles : prev.hlaAlleles,
          hlaMode: data.default_hla_alleles?.length ? "manual" : prev.hlaMode,
        }));
      })
      .catch(() => {}); // silently use empty defaults
  }, [session]);

  const [step, setStep] = useState<FormStep>("upload");
  const [files, setFiles] = useState<File[]>([]);
  const [exprFiles, setExprFiles] = useState<File[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<string>("");
  const [uploadPct, setUploadPct] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);
  const [hlaError, setHlaError] = useState<string | null>(null);

  // Helpers
  const updateForm = useCallback(
    <K extends keyof FormState>(key: K, value: FormState[K]) => {
      setForm((prev) => ({ ...prev, [key]: value }));
    },
    []
  );

  const selectedInputType = INPUT_TYPES.find((t) => t.value === form.inputType)!;

  // -- HLA handling --

  const addHlaAllele = useCallback(() => {
    const raw = form.hlaInput.trim();
    if (!raw) return;
    const normalized = normalizeHla(raw);
    if (!HLA_REGEX.test(normalized)) {
      setHlaError("Invalid format. Use HLA-A*02:01 style.");
      return;
    }
    if (form.hlaAlleles.includes(normalized)) {
      setHlaError("Allele already added.");
      return;
    }
    if (form.hlaAlleles.length >= 6) {
      setHlaError("Maximum 6 alleles (2 per locus: A, B, C).");
      return;
    }
    setHlaError(null);
    setForm((prev) => ({
      ...prev,
      hlaAlleles: [...prev.hlaAlleles, normalized],
      hlaInput: "",
    }));
  }, [form.hlaInput, form.hlaAlleles]);

  const removeHlaAllele = useCallback((allele: string) => {
    setForm((prev) => ({
      ...prev,
      hlaAlleles: prev.hlaAlleles.filter((a) => a !== allele),
    }));
  }, []);

  // -- File handling --

  const MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024 * 1024; // 10 GB

  const addFiles = useCallback(
    (incoming: File[]) => {
      const toAdd: File[] = [];
      for (const f of incoming) {
        const isDupe = files.some((ex) => ex.name === f.name && ex.size === f.size);
        if (isDupe) continue;
        if (f.size > MAX_FILE_SIZE_BYTES) {
          setError(`File "${f.name}" exceeds 10 GB limit.`);
          continue;
        }
        toAdd.push(f);
      }
      if (toAdd.length > 0) {
        setFiles((prev) => [...prev, ...toAdd]);
        // Auto-detect input type from first file
        const detected = detectInputType(toAdd[0].name);
        if (detected) {
          setForm((prev) => ({ ...prev, inputType: detected }));
        }
      }
    },
    [files]
  );

  const handleFileDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      addFiles(Array.from(e.dataTransfer.files));
    },
    [addFiles]
  );

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files) addFiles(Array.from(e.target.files));
    },
    [addFiles]
  );

  const handleExprFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files) {
        const incoming = Array.from(e.target.files);
        const valid = incoming.filter((f) => {
          const name = f.name.toLowerCase();
          return name.endsWith(".csv") || name.endsWith(".tsv") || name.endsWith(".txt");
        });
        if (valid.length === 0) {
          setError("Expression matrix must be a .csv, .tsv, or .txt file.");
          return;
        }
        const MAX_EXPR_SIZE = 50 * 1024 * 1024;
        for (const f of valid) {
          if (f.size > MAX_EXPR_SIZE) {
            setError(`Expression file "${f.name}" exceeds 50 MB limit.`);
            return;
          }
        }
        setExprFiles(valid.slice(0, 1));
      }
    },
    []
  );

  const removeFile = useCallback((idx: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  }, []);

  const removeExprFile = useCallback(() => {
    setExprFiles([]);
  }, []);

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  };

  // -- Validation --

  const validateUpload = (): string | null => {
    if (files.length === 0) return "Upload at least one file.";
    return null;
  };

  const validateConfig = (): string | null => {
    if (!form.projectName.trim()) return "Project name is required.";
    if (!form.cancerType) return "Cancer type is required.";
    if (form.cancerType === "Other" && !form.customCancerType.trim())
      return "Please specify the cancer type.";
    if (form.hlaMode === "manual" && form.hlaAlleles.length === 0)
      return "Add at least one HLA allele or switch to auto-detect.";
    if (form.estimatedPurity) {
      const p = parseFloat(form.estimatedPurity);
      if (isNaN(p) || p < 0 || p > 1) return "Tumor purity must be between 0 and 1.";
    }
    if (form.patientAge) {
      const a = parseInt(form.patientAge, 10);
      if (isNaN(a) || a < 0 || a > 150) return "Patient age must be 0-150.";
    }
    return null;
  };

  // -- Navigation --

  const goToConfig = () => {
    const err = validateUpload();
    if (err) { setError(err); return; }
    setError(null);
    setStep("config");
  };

  const goToReview = () => {
    const err = validateConfig();
    if (err) { setError(err); return; }
    setError(null);
    setStep("review");
  };

  // -- Upload with progress --

  const uploadFileWithProgress = useCallback(
    (url: string, formData: FormData, token: string, onProgress: (pct: number) => void): Promise<Record<string, unknown>> => {
      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", url);
        xhr.setRequestHeader("Authorization", `Bearer ${token}`);
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
        };
        xhr.onload = () => {
          try {
            const body = JSON.parse(xhr.responseText);
            if (xhr.status >= 200 && xhr.status < 300) resolve(body);
            else reject(new Error(body.detail || `Upload failed (HTTP ${xhr.status})`));
          } catch { reject(new Error(`Upload failed (HTTP ${xhr.status})`)); }
        };
        xhr.onerror = () => reject(new Error("Network error during upload"));
        xhr.onabort = () => reject(new Error("Upload cancelled"));
        xhr.send(formData);
      });
    },
    []
  );

  // -- Submission --

  const handleSubmit = async () => {
    if (!session?.accessToken) {
      setError("Not authenticated. Please log in again.");
      return;
    }
    setIsSubmitting(true);
    setError(null);

    const headers = {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.accessToken}`,
    };

    let createdAnalysisId: number | null = null;

    try {
      setUploadProgress("Creating project...");
      const cancerType = form.cancerType === "Other" ? form.customCancerType : form.cancerType;

      const projectRes = await fetch("/api/py/api/projects/", {
        method: "POST",
        headers,
        body: JSON.stringify({
          name: form.projectName.trim(),
          cancer_type: cancerType,
          stage: form.stage || null,
          reference_genome: form.referenceGenome,
        }),
      });
      if (!projectRes.ok) {
        const err = await projectRes.json();
        throw new Error(err.detail || "Failed to create project");
      }
      const project = await projectRes.json();

      setUploadProgress("Creating analysis...");
      // Map csv to vcf for backend (backend treats both as variant input)
      const backendInputType = form.inputType === "csv" ? "vcf" : form.inputType;
      const analysisRes = await fetch("/api/py/api/analyses/", {
        method: "POST",
        headers,
        body: JSON.stringify({
          project_id: project.id,
          input_type: backendInputType,
          hla_provided: form.hlaMode === "manual" && form.hlaAlleles.length > 0,
        }),
      });
      if (!analysisRes.ok) {
        const err = await analysisRes.json();
        throw new Error(err.detail || "Failed to create analysis");
      }
      const analysis = await analysisRes.json();
      createdAnalysisId = analysis.id;

      const allFiles = [
        ...files.map((f) => ({ file: f, label: "primary" })),
        ...exprFiles.map((f) => ({ file: f, label: "expression_matrix" })),
      ];

      for (let i = 0; i < allFiles.length; i++) {
        const { file, label } = allFiles[i];
        setUploadProgress(`Uploading ${file.name} (${i + 1}/${allFiles.length})...`);
        setUploadPct(0);
        const formData = new FormData();
        formData.append("file", file);
        formData.append("file_label", label);
        await uploadFileWithProgress(
          `/api/py/api/analyses/${analysis.id}/upload`,
          formData,
          session.accessToken as string,
          (pct) => setUploadPct(pct),
        );
      }

      setUploadProgress("Submitting for processing...");
      const submitRes = await fetch(`/api/py/api/analyses/${analysis.id}/submit`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          hla_alleles:
            form.hlaMode === "manual" && form.hlaAlleles.length > 0
              ? form.hlaAlleles : null,
        }),
      });
      if (!submitRes.ok) {
        const err = await submitRes.json();
        throw new Error(err.detail || "Failed to submit analysis");
      }

      router.push(`/analysis/${analysis.id}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "An unexpected error occurred";
      if (createdAnalysisId) {
        setError(`${msg}. Your analysis (#${createdAnalysisId}) was created but not fully submitted. Check your projects list.`);
      } else {
        setError(msg);
      }
    } finally {
      setIsSubmitting(false);
      setUploadProgress("");
      setUploadPct(0);
    }
  };

  // -- Shared input class --
  const inputClass = "w-full px-3.5 py-2.5 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary text-sm bg-white transition";

  // -- Current step index for stepper --
  const currentStepIdx = STEPS.findIndex((s) => s.key === step);

  return (
    <div className="space-y-6 max-w-3xl">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-foreground">New Analysis</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Upload variant data and configure neoantigen prediction parameters
        </p>
      </div>

      <PrivacyBanner variant="upload" />

      {/* Visual stepper */}
      <div className="flex items-center gap-0">
        {STEPS.map((s, i) => (
          <div key={s.key} className="flex items-center">
            {i > 0 && (
              <div className={`w-12 h-0.5 ${i <= currentStepIdx ? "bg-primary" : "bg-border"}`} />
            )}
            <button
              onClick={() => {
                if (STEPS.findIndex((x) => x.key === s.key) < currentStepIdx) setStep(s.key);
              }}
              className="flex items-center gap-2"
            >
              <div
                className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold transition ${
                  i < currentStepIdx
                    ? "bg-primary text-white"
                    : i === currentStepIdx
                    ? "bg-primary text-white ring-4 ring-primary/15"
                    : "bg-slate-100 text-muted-foreground border border-border"
                }`}
              >
                {i < currentStepIdx ? (
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                    <path d="m5 12 5 5L20 7"/>
                  </svg>
                ) : (
                  i + 1
                )}
              </div>
              <span className={`text-sm font-medium ${i === currentStepIdx ? "text-foreground" : "text-muted-foreground"}`}>
                {s.label}
              </span>
            </button>
          </div>
        ))}
      </div>

      {/* Error banner */}
      {error && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700 flex items-start gap-2">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mt-0.5">
            <circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>
          </svg>
          <span>{error}</span>
        </div>
      )}

      {/* ===== STEP 1: Upload ===== */}
      {step === "upload" && (
        <div className="rounded-xl border border-border p-6 bg-white shadow-sm space-y-6">
          {/* Input type selector */}
          <div className="space-y-3">
            <label className="block text-sm font-medium text-foreground">Input Format</label>
            <div className="grid grid-cols-4 gap-2">
              {INPUT_TYPES.map((t) => (
                <button
                  key={t.value}
                  type="button"
                  disabled={!t.enabled}
                  onClick={() => t.enabled && updateForm("inputType", t.value)}
                  className={`relative p-3 rounded-lg border text-center transition text-sm ${
                    !t.enabled
                      ? "opacity-40 cursor-not-allowed border-border bg-slate-50"
                      : form.inputType === t.value
                      ? "border-primary bg-primary/5 text-primary font-medium"
                      : "border-border hover:border-primary/40 cursor-pointer"
                  }`}
                >
                  <span className="font-semibold">{t.label}</span>
                  {!t.enabled && (
                    <span className="block text-[10px] text-muted-foreground mt-0.5">Coming soon</span>
                  )}
                </button>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">{selectedInputType.description}</p>
          </div>

          {/* Drop zone */}
          <div
            onDrop={handleFileDrop}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition ${
              dragOver
                ? "border-primary bg-primary/5"
                : "border-slate-200 hover:border-primary/40 hover:bg-slate-50/50"
            }`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept={selectedInputType.accept}
              multiple
              onChange={handleFileSelect}
              className="hidden"
            />
            <div className="flex flex-col items-center gap-2">
              <div className="w-12 h-12 rounded-full bg-primary/8 text-primary flex items-center justify-center">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/>
                  <path d="M14 2v6h6"/><path d="M12 18v-6"/><path d="m9 15 3-3 3 3"/>
                </svg>
              </div>
              <div>
                <p className="text-sm font-medium text-foreground">
                  Drop files here or <span className="text-primary">browse</span>
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  {selectedInputType.accept} &middot; Max 10 GB per file
                </p>
              </div>
            </div>
          </div>

          {/* File list */}
          {files.length > 0 && (
            <div className="space-y-2">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                {files.length} file{files.length > 1 ? "s" : ""} selected
              </p>
              {files.map((f, i) => (
                <div key={`${f.name}-${i}`} className="flex items-center justify-between p-3 bg-slate-50 rounded-lg text-sm">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="w-8 h-8 rounded-lg bg-primary/8 text-primary flex items-center justify-center shrink-0">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/>
                        <path d="M14 2v6h6"/>
                      </svg>
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="font-mono text-xs truncate text-foreground">{f.name}</p>
                        {(() => {
                          const badge = getFileTypeBadge(f.name);
                          return (
                            <span
                              className="shrink-0 px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase"
                              style={{ backgroundColor: badge.bg, color: badge.text }}
                            >
                              {badge.label}
                            </span>
                          );
                        })()}
                      </div>
                      <p className="text-[10px] text-muted-foreground">{formatFileSize(f.size)}</p>
                    </div>
                  </div>
                  <button type="button" onClick={() => removeFile(i)} className="text-xs text-red-500 hover:text-red-700 font-medium ml-3">
                    Remove
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Expression data */}
          <div className="space-y-3 pt-2 border-t border-border">
            <label className="block text-sm font-medium text-foreground">
              Gene Expression Data <span className="text-xs font-normal text-muted-foreground">(optional)</span>
            </label>
            <p className="text-xs text-muted-foreground">
              Expression data improves scoring accuracy (contributes 15% of immunogenicity score).
            </p>
            <div className="flex gap-3">
              {(["none", "matrix"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => {
                    updateForm("expressionMode", mode);
                    if (mode === "none") setExprFiles([]);
                  }}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition ${
                    form.expressionMode === mode
                      ? "border-primary bg-primary/5 text-primary"
                      : "border-border text-muted-foreground hover:border-primary/40"
                  }`}
                >
                  {mode === "none" ? "Skip" : "Upload matrix (CSV/TSV)"}
                </button>
              ))}
            </div>

            {form.expressionMode === "matrix" && (
              <div className="space-y-2">
                <button
                  type="button"
                  onClick={() => exprFileInputRef.current?.click()}
                  className="px-4 py-2 border border-border rounded-lg text-sm hover:bg-slate-50 transition"
                >
                  {exprFiles.length > 0 ? "Change file" : "Select expression file"}
                </button>
                <input ref={exprFileInputRef} type="file" accept=".csv,.tsv,.txt" onChange={handleExprFileSelect} className="hidden" />
                {exprFiles.length > 0 && (
                  <div className="flex items-center gap-2 p-2 bg-green-50 border border-green-200 rounded-lg text-sm">
                    <span className="font-mono text-xs truncate">{exprFiles[0].name}</span>
                    <span className="text-xs text-muted-foreground">{formatFileSize(exprFiles[0].size)}</span>
                    <button type="button" onClick={removeExprFile} className="text-xs text-red-500 ml-auto">Remove</button>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Next */}
          <div className="flex gap-3 pt-2">
            <button type="button" onClick={() => router.push("/dashboard")} className="px-5 py-2.5 border border-border rounded-lg hover:bg-slate-50 font-medium transition text-sm text-muted-foreground">
              Cancel
            </button>
            <button type="button" onClick={goToConfig} className="px-5 py-2.5 bg-primary text-white rounded-lg hover:bg-primary/90 font-medium transition text-sm shadow-sm">
              Next: Configure
            </button>
          </div>
        </div>
      )}

      {/* ===== STEP 2: Configure ===== */}
      {step === "config" && (
        <div className="rounded-xl border border-border p-6 bg-white shadow-sm space-y-5">
          {/* Project name */}
          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-foreground">
              Project Name <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={form.projectName}
              onChange={(e) => updateForm("projectName", e.target.value)}
              placeholder="e.g., Patient-042 Melanoma WES"
              className={inputClass}
            />
          </div>

          {/* Cancer type grid */}
          <div className="space-y-2">
            <label className="block text-sm font-medium text-foreground">
              Cancer Type <span className="text-red-500">*</span>
            </label>
            <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
              {CANCER_TYPES.map((ct) => (
                <button
                  key={ct.value}
                  type="button"
                  onClick={() => updateForm("cancerType", ct.value)}
                  className={`relative p-2.5 rounded-lg border text-center transition text-xs ${
                    form.cancerType === ct.value
                      ? "border-primary bg-primary/5 text-primary ring-1 ring-primary/30"
                      : "border-border hover:border-primary/40 text-foreground"
                  }`}
                >
                  <span className={`block text-[10px] font-bold tracking-wider mb-0.5 ${
                    form.cancerType === ct.value ? "text-primary" : "text-muted-foreground"
                  }`}>
                    {ct.abbr}
                  </span>
                  <span className="block font-medium leading-tight">{ct.label}</span>
                </button>
              ))}
            </div>
            {form.cancerType === "Other" && (
              <input
                type="text"
                value={form.customCancerType}
                onChange={(e) => updateForm("customCancerType", e.target.value)}
                placeholder="Specify cancer type"
                className={`${inputClass} mt-2`}
              />
            )}
          </div>

          {/* Stage + Reference Genome */}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-foreground">Stage</label>
              <div className="flex gap-1.5">
                {(["", ...STAGES] as const).map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => updateForm("stage", s)}
                    className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition ${
                      form.stage === s
                        ? "border-primary bg-primary/5 text-primary"
                        : "border-border text-muted-foreground hover:border-primary/40"
                    }`}
                  >
                    {s || "N/A"}
                  </button>
                ))}
              </div>
            </div>
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-foreground">
                Reference Genome <span className="text-red-500">*</span>
              </label>
              <div className="flex gap-2">
                {REFERENCE_GENOMES.map((g) => (
                  <button
                    key={g}
                    type="button"
                    onClick={() => updateForm("referenceGenome", g)}
                    className={`flex-1 px-3 py-2 rounded-lg text-sm font-medium border transition ${
                      form.referenceGenome === g
                        ? "border-primary bg-primary/5 text-primary"
                        : "border-border text-muted-foreground hover:border-primary/40"
                    }`}
                  >
                    {g}
                  </button>
                ))}
              </div>
              <p className="text-[10px] text-muted-foreground">Coordinates in your VCF must match the selected reference</p>
            </div>
          </div>

          {/* HLA alleles */}
          <div className="space-y-2">
            <label className="block text-sm font-medium text-foreground">HLA Alleles</label>
            <div className="flex gap-4">
              {(["auto", "manual"] as const).map((mode) => (
                <label key={mode} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="hlaMode"
                    value={mode}
                    checked={form.hlaMode === mode}
                    onChange={() => updateForm("hlaMode", mode)}
                    className="accent-primary"
                  />
                  <span className="text-sm">{mode === "auto" ? "Auto-detect from sequencing" : "Provide known alleles"}</span>
                </label>
              ))}
            </div>

            {form.hlaMode === "manual" && (
              <div className="space-y-2 mt-2">
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={form.hlaInput}
                    onChange={(e) => updateForm("hlaInput", e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addHlaAllele(); } }}
                    placeholder="e.g., HLA-A*02:01"
                    list="hla-suggestions"
                    className={`flex-1 ${inputClass}`}
                  />
                  <datalist id="hla-suggestions">
                    {COMMON_HLA_ALLELES.map((a) => (<option key={a} value={a} />))}
                  </datalist>
                  <button type="button" onClick={addHlaAllele} className="px-4 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary/90 transition">
                    Add
                  </button>
                </div>
                {hlaError && <p className="text-xs text-red-600">{hlaError}</p>}
                {form.hlaAlleles.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {form.hlaAlleles.map((allele) => (
                      <span key={allele} className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-primary/8 text-primary rounded-lg text-xs font-mono">
                        {allele}
                        <button type="button" onClick={() => removeHlaAllele(allele)} className="hover:text-red-600">
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m18 6-12 12"/><path d="m6 6 12 12"/></svg>
                        </button>
                      </span>
                    ))}
                  </div>
                )}
                <p className="text-xs text-muted-foreground">Up to 6 alleles (2 per locus: A, B, C)</p>
              </div>
            )}
          </div>

          {/* Clinical metadata */}
          <details className="group">
            <summary className="text-sm font-medium cursor-pointer select-none text-foreground flex items-center gap-1.5">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="transition group-open:rotate-90">
                <path d="m9 18 6-6-6-6"/>
              </svg>
              Clinical Metadata (optional)
            </summary>
            <div className="mt-3 grid grid-cols-3 gap-4">
              <div className="space-y-1.5">
                <label className="block text-xs text-muted-foreground">Tumor Purity (0-1)</label>
                <input type="text" value={form.estimatedPurity} onChange={(e) => updateForm("estimatedPurity", e.target.value)} placeholder="e.g., 0.65" className={inputClass} />
              </div>
              <div className="space-y-1.5">
                <label className="block text-xs text-muted-foreground">Patient Age</label>
                <input type="number" value={form.patientAge} onChange={(e) => updateForm("patientAge", e.target.value)} placeholder="Years" min={0} max={150} className={inputClass} />
              </div>
              <div className="space-y-1.5">
                <label className="block text-xs text-muted-foreground">Biological Sex</label>
                <select value={form.patientSex} onChange={(e) => updateForm("patientSex", e.target.value)} className={inputClass}>
                  <option value="">Not specified</option>
                  <option value="male">Male</option>
                  <option value="female">Female</option>
                </select>
              </div>
            </div>
          </details>

          {/* Navigation */}
          <div className="flex gap-3 pt-2">
            <button type="button" onClick={() => setStep("upload")} className="px-5 py-2.5 border border-border rounded-lg hover:bg-slate-50 font-medium transition text-sm text-muted-foreground">
              Back
            </button>
            <button type="button" onClick={goToReview} className="px-5 py-2.5 bg-primary text-white rounded-lg hover:bg-primary/90 font-medium transition text-sm shadow-sm">
              Next: Review
            </button>
          </div>
        </div>
      )}

      {/* ===== STEP 3: Review & Submit ===== */}
      {step === "review" && (
        <div className="rounded-xl border border-border p-6 bg-white shadow-sm space-y-6">
          <h2 className="text-lg font-semibold text-foreground">Review & Submit</h2>

          {/* Summary grid */}
          <div className="grid grid-cols-2 gap-x-8 gap-y-4 text-sm">
            <ReviewItem label="Project" value={form.projectName} />
            <ReviewItem label="Cancer Type" value={form.cancerType === "Other" ? form.customCancerType : form.cancerType} />
            <ReviewItem label="Stage" value={form.stage || "Not specified"} />
            <ReviewItem label="Reference Genome" value={form.referenceGenome} />
            <ReviewItem label="Input Type" value={selectedInputType.fullLabel} />
            <ReviewItem label="HLA Alleles" value={form.hlaMode === "auto" ? "Auto-detect" : form.hlaAlleles.join(", ")} />
            <ReviewItem
              label="Expression Data"
              value={
                form.expressionMode === "none"
                  ? "None (neutral estimate)"
                  : exprFiles.length > 0
                  ? exprFiles[0].name
                  : "Matrix selected but no file"
              }
            />
            <div className="col-span-2">
              <p className="text-xs text-muted-foreground mb-1">Files</p>
              <div className="flex flex-wrap gap-2">
                {files.map((f) => (
                  <span key={f.name} className="px-2.5 py-1 bg-slate-50 rounded-lg text-xs font-mono border border-border">
                    {f.name}
                  </span>
                ))}
              </div>
            </div>
          </div>

          {/* Upload progress */}
          {uploadProgress && (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-sm text-primary">
                <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                {uploadProgress}
                {uploadPct > 0 && uploadPct < 100 && (
                  <span className="text-xs text-muted-foreground">{uploadPct}%</span>
                )}
              </div>
              {uploadPct > 0 && (
                <div className="w-full h-1.5 bg-slate-100 rounded-full overflow-hidden">
                  <div className="h-full bg-primary rounded-full transition-all duration-200" style={{ width: `${uploadPct}%` }} />
                </div>
              )}
            </div>
          )}

          {/* Navigation */}
          <div className="flex gap-3 pt-2">
            <button type="button" onClick={() => setStep("config")} disabled={isSubmitting} className="px-5 py-2.5 border border-border rounded-lg hover:bg-slate-50 font-medium transition text-sm disabled:opacity-50 text-muted-foreground">
              Back
            </button>
            <button type="button" onClick={handleSubmit} disabled={isSubmitting} className="px-6 py-2.5 bg-primary text-white rounded-lg hover:bg-primary/90 font-medium transition text-sm disabled:opacity-50 shadow-sm">
              {isSubmitting ? "Submitting..." : "Submit Analysis"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function ReviewItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground mb-0.5">{label}</p>
      <p className="font-medium text-foreground">{value}</p>
    </div>
  );
}
