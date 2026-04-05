"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import PrivacyBanner from "@/components/privacy-banner";

// -- Types --

interface EpitopeRow {
  id: number;
  analysis_id: number;
  variant_id: number;
  peptide_seq: string;
  peptide_length: number;
  rank: number;
  gene: string | null;
  chrom: string | null;
  pos: number | null;
  protein_change: string | null;
  variant_type: string | null;
  hla_allele: string;
  binding_affinity_nm: number;
  presentation_score: number;
  processing_score: number | null;
  expression_tpm: number | null;
  immunogenicity_score: number;
  dai_score: number | null;
  wt_binding_affinity_nm: number | null;
  confidence_tier: string;
  explanation_json: Record<string, unknown> | null;
}

interface ApiResponse {
  epitopes: EpitopeRow[];
  total: number;
  skip: number;
  limit: number;
}

type SortField =
  | "rank"
  | "immunogenicity_score"
  | "binding_affinity_nm"
  | "presentation_score"
  | "gene"
  | "peptide_length";

// All columns the table can show
const ALL_COLUMNS = [
  { key: "select", label: "", defaultVisible: true, sortable: false },
  { key: "rank", label: "Rank", defaultVisible: true, sortable: true },
  { key: "peptide_seq", label: "Peptide", defaultVisible: true, sortable: false },
  { key: "peptide_length", label: "Length", defaultVisible: true, sortable: true },
  { key: "gene", label: "Gene", defaultVisible: true, sortable: true },
  { key: "protein_change", label: "Mutation", defaultVisible: true, sortable: false },
  { key: "genomic_position", label: "Position", defaultVisible: false, sortable: false },
  { key: "variant_type", label: "Type", defaultVisible: true, sortable: false },
  { key: "hla_allele", label: "HLA Allele", defaultVisible: true, sortable: false },
  { key: "binding_affinity_nm", label: "IC50 (nM)", defaultVisible: true, sortable: true },
  { key: "presentation_score", label: "Presentation", defaultVisible: true, sortable: true },
  { key: "processing_score", label: "Cleavage", defaultVisible: false, sortable: false },
  { key: "expression_tpm", label: "TPM", defaultVisible: false, sortable: false },
  { key: "immunogenicity_score", label: "Score", defaultVisible: true, sortable: true },
  { key: "dai_score", label: "DAI", defaultVisible: true, sortable: false },
  { key: "confidence_tier", label: "Tier", defaultVisible: true, sortable: false },
  { key: "explain", label: "", defaultVisible: true, sortable: false },
] as const;

type ColumnKey = (typeof ALL_COLUMNS)[number]["key"];

// -- Helpers --

const tierColor: Record<string, string> = {
  high: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
  medium: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300",
  low: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300",
};

function formatAffinity(nm: number): string {
  if (nm < 10) return nm.toFixed(1);
  if (nm < 1000) return Math.round(nm).toString();
  return nm.toFixed(0);
}

export default function ResultsPage() {
  const params = useParams();
  const router = useRouter();
  const { data: session } = useSession();
  const analysisId = String(params.id ?? "");

  // Data
  const [data, setData] = useState<ApiResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Analysis metadata (for display)
  const [analysisMeta, setAnalysisMeta] = useState<{
    project_name?: string;
    cancer_type?: string;
    project_analysis_number?: number;
  } | null>(null);

  // Filters
  const [filterGene, setFilterGene] = useState("");
  const [filterType, setFilterType] = useState("");
  const [filterHla, setFilterHla] = useState("");
  const [filterTier, setFilterTier] = useState("");
  const [filterMinScore, setFilterMinScore] = useState("");

  // Sorting
  const [sortBy, setSortBy] = useState<SortField>("rank");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("asc");

  // Pagination
  const [page, setPage] = useState(0);
  const pageSize = 50;

  // Column visibility
  const [visibleColumns, setVisibleColumns] = useState<Set<ColumnKey>>(
    () => new Set(ALL_COLUMNS.filter((c) => c.defaultVisible).map((c) => c.key))
  );
  const [showColumnPicker, setShowColumnPicker] = useState(false);

  // Row selection (for vaccine construct shortlist)
  const [selected, setSelected] = useState<Set<number>>(new Set());

  // Deduplicate: show only best-scoring epitope per mutation
  const [deduplicate, setDeduplicate] = useState(false);

  // BLAST self-similarity check
  const [blastRunning, setBlastRunning] = useState(false);
  const [blastResults, setBlastResults] = useState<Record<number, { is_self_similar: boolean; max_identity_pct: number; status: string }>>({});

  // DAI computation for existing analyses
  const [daiRunning, setDaiRunning] = useState(false);
  const [daiComputed, setDaiComputed] = useState(false);
  // Track whether the analysis has any DAI data at all (checked once on first load)
  const [hasDaiData, setHasDaiData] = useState<boolean | null>(null);

  // Variant annotation enrichment
  const [annotateRunning, setAnnotateRunning] = useState(false);
  const [annotateData, setAnnotateData] = useState<Record<number, {
    clinvar_significance?: string;
    gnomad_af?: number;
    is_likely_germline?: boolean;
    is_known_driver?: boolean;
    cosmic_tier?: number;
    cosmic_role?: string;
  }> | null>(null);

  // Filter dropdown options -- fetched once from dedicated endpoint,
  // not derived from current page (which would miss values on other pages)
  const [filterOptions, setFilterOptions] = useState<{
    genes: string[];
    variant_types: string[];
    hla_alleles: string[];
  }>({ genes: [], variant_types: [], hla_alleles: [] });

  useEffect(() => {
    if (!session?.accessToken) return;
    fetch(`/api/py/api/epitopes/${analysisId}/epitopes/filter-options`, {
      headers: { Authorization: `Bearer ${session.accessToken}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data) setFilterOptions(data);
      })
      .catch(() => {}); // silently degrade -- dropdowns just stay empty

    // Fetch analysis metadata for display (project name, per-project numbering)
    fetch(`/api/py/api/analyses/${analysisId}`, {
      headers: { Authorization: `Bearer ${session.accessToken}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) setAnalysisMeta(d); })
      .catch(() => {});
  }, [session?.accessToken, analysisId]);

  // -- Data fetching with AbortController to prevent stale responses --

  const abortRef = useRef<AbortController | null>(null);

  const fetchData = useCallback(async () => {
    if (!session?.accessToken) return;

    // Cancel any in-flight request so we don't render stale data
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setError(null);

    const params = new URLSearchParams();
    params.set("sort_by", sortBy);
    params.set("sort_order", sortOrder);
    params.set("skip", String(page * pageSize));
    params.set("limit", String(pageSize));

    if (filterGene) params.set("gene", filterGene);
    if (filterType) params.set("variant_type", filterType);
    if (filterHla) params.set("hla_allele", filterHla);
    if (filterTier) params.set("confidence_tier", filterTier);
    if (filterMinScore) params.set("min_score", filterMinScore);
    if (deduplicate) params.set("deduplicate", "true");

    try {
      const res = await fetch(
        `/api/py/api/epitopes/${analysisId}/epitopes?${params.toString()}`,
        {
          headers: { Authorization: `Bearer ${session.accessToken}` },
          signal: controller.signal,
        }
      );

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }

      const json: ApiResponse = await res.json();
      setData(json);
      // On first successful load, check if any epitope has DAI data
      setHasDaiData(prev => prev !== null ? prev : json.epitopes.some(e => e.dai_score != null));
    } catch (err) {
      // Aborted requests: skip entirely -- the replacement request
      // already set loading=true and will handle its own loading=false
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Failed to load results");
    }
    // Only reached on success or non-abort error (not on abort return)
    setLoading(false);
  }, [
    session?.accessToken,
    analysisId,
    sortBy,
    sortOrder,
    page,
    filterGene,
    filterType,
    filterHla,
    filterTier,
    filterMinScore,
    deduplicate,
  ]);

  useEffect(() => {
    fetchData();
    return () => abortRef.current?.abort();
  }, [fetchData]);

  // Reset page when filters change
  useEffect(() => {
    setPage(0);
  }, [filterGene, filterType, filterHla, filterTier, filterMinScore, deduplicate]);

  // -- Sorting --

  const handleSort = (field: string) => {
    const col = ALL_COLUMNS.find((c) => c.key === field);
    if (!col?.sortable) return;

    if (sortBy === field) {
      setSortOrder((prev) => (prev === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(field as SortField);
      setSortOrder("asc");
    }
  };

  // -- Selection --

  const toggleSelect = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (!data) return;
    const pageIds = data.epitopes.map((e) => e.id);
    const allSelected = pageIds.every((id) => selected.has(id));

    setSelected((prev) => {
      const next = new Set(prev);
      if (allSelected) {
        pageIds.forEach((id) => next.delete(id));
      } else {
        pageIds.forEach((id) => next.add(id));
      }
      return next;
    });
  };

  // -- Export --
  // Can't use window.open() because the export endpoint requires Bearer auth
  // and browser GET requests won't include the Authorization header.
  // Instead, fetch the blob and trigger a client-side download.

  const [exporting, setExporting] = useState(false);

  const handleExport = async (format: "csv" | "tsv") => {
    if (!session?.accessToken || exporting) return;

    setExporting(true);
    try {
      const params = new URLSearchParams();
      params.set("format", format);
      if (filterGene) params.set("gene", filterGene);
      if (filterType) params.set("variant_type", filterType);
      if (filterHla) params.set("hla_allele", filterHla);
      if (filterTier) params.set("confidence_tier", filterTier);
      if (filterMinScore) params.set("min_score", filterMinScore);
      if (deduplicate) params.set("deduplicate", "true");

      const res = await fetch(
        `/api/py/api/epitopes/${analysisId}/epitopes/export?${params.toString()}`,
        { headers: { Authorization: `Bearer ${session.accessToken}` } }
      );

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail || `Export failed: HTTP ${res.status}`);
      }

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `epitopes_analysis_${analysisId}.${format}`;
      document.body.appendChild(a);
      a.click();
      // Cleanup
      setTimeout(() => {
        URL.revokeObjectURL(url);
        document.body.removeChild(a);
      }, 100);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(false);
    }
  };

  // -- Column toggle --

  const toggleColumn = (key: ColumnKey) => {
    setVisibleColumns((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  // -- Pagination --

  const totalPages = data ? Math.ceil(data.total / pageSize) : 0;

  // Clamp page if total shrinks (e.g. concurrent data change)
  useEffect(() => {
    if (totalPages > 0 && page >= totalPages) {
      setPage(totalPages - 1);
    }
  }, [totalPages, page]);

  // -- Cell renderer --

  const renderCell = (ep: EpitopeRow, col: ColumnKey) => {
    switch (col) {
      case "select":
        return (
          <input
            type="checkbox"
            checked={selected.has(ep.id)}
            onChange={() => toggleSelect(ep.id)}
            className="rounded"
          />
        );
      case "rank":
        return ep.rank;
      case "peptide_seq":
        return (
          <span className="font-mono text-xs tracking-wide">{ep.peptide_seq}</span>
        );
      case "peptide_length":
        return ep.peptide_length;
      case "gene": {
        const varAnn = annotateData?.[ep.variant_id];
        return (
          <span className="flex items-center gap-1">
            {ep.gene || "-"}
            {varAnn?.is_known_driver && (
              <span
                className={`px-1 py-0.5 rounded text-[8px] font-bold ${
                  varAnn.cosmic_tier === 1
                    ? "bg-purple-100 text-purple-700"
                    : "bg-purple-50 text-purple-500"
                }`}
                title={`COSMIC CGC Tier ${varAnn.cosmic_tier ?? "?"}${varAnn.cosmic_role ? ` (${varAnn.cosmic_role})` : ""}`}
              >
                {varAnn.cosmic_role?.toLowerCase().includes("oncogene") && varAnn.cosmic_role?.toLowerCase().includes("tsg")
                  ? "Onco/TSG"
                  : varAnn.cosmic_role?.toLowerCase().includes("oncogene")
                  ? "Oncogene"
                  : varAnn.cosmic_role?.toLowerCase().includes("tsg")
                  ? "TSG"
                  : "Driver"}
                {varAnn.cosmic_tier === 1 ? "" : " T2"}
              </span>
            )}
            {varAnn?.is_likely_germline && (
              <span className="px-1 py-0.5 rounded text-[8px] font-bold bg-red-100 text-red-700" title={`gnomAD AF: ${varAnn.gnomad_af?.toFixed(4)}`}>
                Germline?
              </span>
            )}
            {varAnn?.clinvar_significance && (
              <span className="px-1 py-0.5 rounded text-[8px] bg-blue-50 text-blue-600" title={`ClinVar: ${varAnn.clinvar_significance}`}>
                CV
              </span>
            )}
          </span>
        );
      }
      case "protein_change":
        return ep.protein_change ? (
          <span className="font-mono text-xs">{ep.protein_change}</span>
        ) : (
          "-"
        );
      case "genomic_position":
        return ep.chrom && ep.pos ? (
          <span className="font-mono text-xs">
            {ep.chrom}:{ep.pos.toLocaleString()}
          </span>
        ) : (
          "-"
        );
      case "variant_type":
        return ep.variant_type || "-";
      case "hla_allele":
        return <span className="font-mono text-xs">{ep.hla_allele}</span>;
      case "binding_affinity_nm": {
        const nm = ep.binding_affinity_nm;
        const affinityColor = nm <= 50 ? "text-green-700" : nm <= 500 ? "text-yellow-700" : "text-red-600";
        return <span className={`font-mono text-xs ${affinityColor}`}>{formatAffinity(nm)}</span>;
      }
      case "presentation_score":
        return ep.presentation_score.toFixed(3);
      case "processing_score":
        return ep.processing_score != null ? ep.processing_score.toFixed(3) : "-";
      case "expression_tpm":
        return ep.expression_tpm != null ? ep.expression_tpm.toFixed(1) : "-";
      case "immunogenicity_score":
        return (
          <span className="font-semibold">
            {ep.immunogenicity_score.toFixed(3)}
          </span>
        );
      case "dai_score": {
        if (ep.dai_score == null) return <span className="text-muted-foreground">-</span>;
        const dai = ep.dai_score;
        // Positive DAI = mutant binds better than WT = good
        // Color: green for positive (>0), red for negative, amber for near-zero
        const daiColor = dai >= 2 ? "text-green-700 dark:text-green-400"
          : dai >= 0.5 ? "text-green-600 dark:text-green-500"
          : dai >= 0 ? "text-yellow-600 dark:text-yellow-400"
          : "text-red-600 dark:text-red-400";
        return (
          <span
            className={`font-mono text-xs ${daiColor}`}
            title={`DAI = log2(WT_IC50 / mut_IC50). WT: ${ep.wt_binding_affinity_nm?.toFixed(0) ?? "?"} nM, Mut: ${formatAffinity(ep.binding_affinity_nm)} nM`}
          >
            {dai > 0 ? "+" : ""}{dai.toFixed(2)}
          </span>
        );
      }
      case "confidence_tier": {
        const blast = blastResults[ep.id];
        return (
          <span className="flex items-center gap-1.5">
            <span
              className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                tierColor[ep.confidence_tier] || ""
              }`}
            >
              {ep.confidence_tier}
            </span>
            {blast && (
              <span
                className={`px-1.5 py-0.5 rounded text-[9px] font-semibold ${
                  blast.is_self_similar
                    ? "bg-orange-100 text-orange-700"
                    : "bg-green-100 text-green-700"
                }`}
                title={`BLAST: ${blast.max_identity_pct.toFixed(0)}% max identity to human proteome`}
              >
                {blast.is_self_similar ? `Self ${blast.max_identity_pct.toFixed(0)}%` : "Novel"}
              </span>
            )}
          </span>
        );
      }
      case "explain":
        return (
          <button
            onClick={(e) => {
              e.stopPropagation();
              router.push(`/analysis/${analysisId}/explain/${ep.id}`);
            }}
            className="px-2 py-0.5 text-xs border border-primary/30 text-primary rounded hover:bg-primary/10 transition"
            title="View score explanation"
          >
            Explain
          </button>
        );
      default:
        return "-";
    }
  };

  // -- Render --

  return (
    <div className="space-y-4">
      {/* Citation reminder */}
      <PrivacyBanner variant="results" />

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">
            Epitope Results
          </h1>
          <p className="text-sm text-muted-foreground">
            {analysisMeta?.project_name
              ? `${analysisMeta.project_name} \u00b7 Analysis ${analysisMeta.project_analysis_number ?? analysisId}`
              : `Analysis ${analysisId}`}
            {analysisMeta?.cancer_type && ` \u00b7 ${analysisMeta.cancer_type}`}
            {data && ` \u00b7 ${data.total} epitope${data.total !== 1 ? "s" : ""}`}
            {deduplicate && " (best per mutation)"}
            {selected.size > 0 && ` \u00b7 ${selected.size} selected`}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={async () => {
              if (!session?.accessToken) return;
              try {
                const res = await fetch(`/api/py/api/report/${analysisId}/pdf`, {
                  headers: { Authorization: `Bearer ${session.accessToken}` },
                });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = `ocvd_report_analysis_${analysisId}.pdf`;
                document.body.appendChild(a);
                a.click();
                setTimeout(() => { URL.revokeObjectURL(url); document.body.removeChild(a); }, 100);
              } catch (e) {
                setError(e instanceof Error ? e.message : "PDF export failed");
              }
            }}
            className="px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded-md hover:opacity-90 transition"
          >
            PDF Report
          </button>
          <button
            onClick={() => router.push(`/analysis/${analysisId}`)}
            className="px-3 py-1.5 text-sm border border-border rounded-md hover:bg-muted transition"
          >
            Back to Analysis
          </button>
        </div>
      </div>

      {/* Filter bar */}
      <div className="border border-border rounded-lg p-4 bg-white dark:bg-slate-950">
        <div className="flex gap-3 items-end flex-wrap">
          <div className="min-w-[120px]">
            <label className="block text-xs text-muted-foreground mb-1">Gene</label>
            <select
              value={filterGene}
              onChange={(e) => setFilterGene(e.target.value)}
              className="w-full px-2 py-1.5 border border-border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            >
              <option value="">All</option>
              {filterOptions.genes.map((g) => (
                <option key={g} value={g}>{g}</option>
              ))}
            </select>
          </div>

          <div className="min-w-[120px]">
            <label className="block text-xs text-muted-foreground mb-1">Type</label>
            <select
              value={filterType}
              onChange={(e) => setFilterType(e.target.value)}
              className="w-full px-2 py-1.5 border border-border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            >
              <option value="">All</option>
              {filterOptions.variant_types.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>

          <div className="min-w-[140px]">
            <label className="block text-xs text-muted-foreground mb-1">HLA Allele</label>
            <select
              value={filterHla}
              onChange={(e) => setFilterHla(e.target.value)}
              className="w-full px-2 py-1.5 border border-border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            >
              <option value="">All</option>
              {filterOptions.hla_alleles.map((h) => (
                <option key={h} value={h}>{h}</option>
              ))}
            </select>
          </div>

          <div className="min-w-[100px]">
            <label className="block text-xs text-muted-foreground mb-1">Tier</label>
            <select
              value={filterTier}
              onChange={(e) => setFilterTier(e.target.value)}
              className="w-full px-2 py-1.5 border border-border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            >
              <option value="">All</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </div>

          <div className="min-w-[100px]">
            <label className="block text-xs text-muted-foreground mb-1">Min Score</label>
            <input
              type="number"
              step="0.1"
              min="0"
              max="1"
              value={filterMinScore}
              onChange={(e) => setFilterMinScore(e.target.value)}
              placeholder="0.0"
              className="w-full px-2 py-1.5 border border-border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            />
          </div>

          {/* Deduplicate toggle */}
          <label className="flex items-center gap-2 cursor-pointer select-none whitespace-nowrap">
            <div className="relative">
              <input
                type="checkbox"
                checked={deduplicate}
                onChange={(e) => setDeduplicate(e.target.checked)}
                className="sr-only peer"
              />
              <div className="w-9 h-5 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-primary rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full rtl:peer-checked:after:-translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-primary" />
            </div>
            <span className="text-xs text-muted-foreground">Best per mutation</span>
          </label>

          {/* Spacer */}
          <div className="flex-1" />

          {/* Actions */}
          <div className="flex gap-2">
            <div className="relative">
              <button
                onClick={() => setShowColumnPicker(!showColumnPicker)}
                className="px-3 py-1.5 text-sm border border-border rounded-md hover:bg-muted transition"
              >
                Columns
              </button>
              {showColumnPicker && (
                <div className="absolute right-0 top-full mt-1 w-48 bg-white dark:bg-slate-950 border border-border rounded-md shadow-lg z-20 p-2">
                  {ALL_COLUMNS.filter((c) => c.key !== "select").map((col) => (
                    <label
                      key={col.key}
                      className="flex items-center gap-2 px-2 py-1 text-sm hover:bg-muted rounded cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={visibleColumns.has(col.key)}
                        onChange={() => toggleColumn(col.key)}
                      />
                      {col.label}
                    </label>
                  ))}
                </div>
              )}
            </div>

            <button
              onClick={() => handleExport("csv")}
              disabled={exporting}
              className="px-3 py-1.5 text-sm border border-border rounded-md hover:bg-muted disabled:opacity-40 transition"
            >
              {exporting ? "..." : "CSV"}
            </button>
            <button
              onClick={() => handleExport("tsv")}
              disabled={exporting}
              className="px-3 py-1.5 text-sm border border-border rounded-md hover:bg-muted disabled:opacity-40 transition"
            >
              {exporting ? "..." : "TSV"}
            </button>
            {/* Annotate variants with ClinVar/gnomAD/COSMIC */}
            {data && data.epitopes.length > 0 && !annotateData && (
              <button
                disabled={annotateRunning}
                onClick={async () => {
                  if (!session?.accessToken) return;
                  setAnnotateRunning(true);
                  try {
                    const res = await fetch(`/api/py/api/annotate/variants/${analysisId}`, {
                      method: "POST",
                      headers: { Authorization: `Bearer ${session.accessToken}` },
                    });
                    if (!res.ok) throw new Error(`HTTP ${res.status}`);
                    const json = await res.json();
                    // Build variant_id -> annotation map
                    const map: Record<number, Record<string, unknown>> = {};
                    for (const v of json.variants || []) {
                      map[v.variant_id] = v;
                    }
                    setAnnotateData(map);
                    if (json.warnings?.length) {
                      setError(json.warnings.join(" | "));
                    }
                  } catch (e) {
                    setError(e instanceof Error ? e.message : "Annotation failed");
                  } finally {
                    setAnnotateRunning(false);
                  }
                }}
                className="px-3 py-1.5 text-sm bg-teal-600 text-white rounded-md hover:bg-teal-700 disabled:opacity-50 transition"
                title="Enrich variants with ClinVar, gnomAD, and COSMIC annotations"
              >
                {annotateRunning ? "Annotating..." : "Annotate"}
              </button>
            )}
            {/* Compute DAI for analyses that don't have it yet */}
            {data && data.epitopes.length > 0 && hasDaiData === false && !daiComputed && (
              <button
                disabled={daiRunning}
                onClick={async () => {
                  if (!session?.accessToken) return;
                  setDaiRunning(true);
                  try {
                    const res = await fetch(`/api/py/api/dai/compute/${analysisId}`, {
                      method: "POST",
                      headers: { Authorization: `Bearer ${session.accessToken}` },
                    });
                    if (!res.ok) throw new Error(`HTTP ${res.status}`);
                    setDaiComputed(true);
                    fetchData(); // reload to show DAI values
                  } catch (e) {
                    setError(e instanceof Error ? e.message : "DAI computation failed");
                  } finally {
                    setDaiRunning(false);
                  }
                }}
                className="px-3 py-1.5 text-sm bg-violet-600 text-white rounded-md hover:bg-violet-700 disabled:opacity-50 transition"
                title="Compute Differential Agretopicity Index (wildtype vs mutant MHC binding comparison)"
              >
                {daiRunning ? "Computing DAI..." : "Compute DAI"}
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-md text-sm text-red-700 dark:text-red-200">
          {error}
        </div>
      )}

      {/* Table */}
      <div className="border border-border rounded-lg bg-white dark:bg-slate-950 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/30">
              {ALL_COLUMNS.filter((c) => visibleColumns.has(c.key)).map((col) => (
                <th
                  key={col.key}
                  onClick={() => col.sortable && handleSort(col.key)}
                  className={`text-left py-2.5 px-3 font-medium text-foreground whitespace-nowrap ${
                    col.sortable ? "cursor-pointer hover:bg-muted/50 select-none" : ""
                  }`}
                >
                  {col.key === "select" ? (
                    <input
                      type="checkbox"
                      checked={
                        !!data &&
                        data.epitopes.length > 0 &&
                        data.epitopes.every((e) => selected.has(e.id))
                      }
                      onChange={toggleSelectAll}
                      className="rounded"
                    />
                  ) : (
                    <span className="flex items-center gap-1">
                      {col.label}
                      {col.sortable && sortBy === col.key && (
                        <span className="text-xs">
                          {sortOrder === "asc" ? "\u25b2" : "\u25bc"}
                        </span>
                      )}
                    </span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td
                  colSpan={visibleColumns.size}
                  className="text-center py-12 text-muted-foreground"
                >
                  <div className="flex items-center justify-center gap-2">
                    <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                    Loading...
                  </div>
                </td>
              </tr>
            )}

            {!loading && data && data.epitopes.length === 0 && (
              <tr>
                <td
                  colSpan={visibleColumns.size}
                  className="text-center py-12 text-muted-foreground"
                >
                  No epitopes found
                  {(filterGene || filterType || filterHla || filterTier || filterMinScore)
                    ? ". Try adjusting filters."
                    : ". Run an analysis to see predictions."}
                </td>
              </tr>
            )}

            {!loading &&
              data?.epitopes.map((ep) => (
                <tr
                  key={ep.id}
                  onClick={() =>
                    router.push(
                      `/analysis/${analysisId}/explain/${ep.id}`
                    )
                  }
                  className={`border-b border-border/50 hover:bg-muted/30 cursor-pointer transition ${
                    selected.has(ep.id) ? "bg-primary/5" : ""
                  }`}
                >
                  {ALL_COLUMNS.filter((c) => visibleColumns.has(c.key)).map(
                    (col) => (
                      <td
                        key={col.key}
                        className="py-2 px-3 whitespace-nowrap"
                        onClick={
                          col.key === "select"
                            ? (e) => e.stopPropagation()
                            : undefined
                        }
                      >
                        {renderCell(ep, col.key)}
                      </td>
                    )
                  )}
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {data && data.total > 0 && (
        <div className="flex items-center justify-between text-sm">
          <p className="text-muted-foreground">
            {totalPages <= 1
              ? `${data.total} result${data.total !== 1 ? "s" : ""}`
              : `Showing ${page * pageSize + 1}\u2013${Math.min((page + 1) * pageSize, data.total)} of ${data.total}`}
          </p>
          <div className="flex gap-1">
            <button
              disabled={page === 0}
              onClick={() => setPage(0)}
              className="px-2 py-1 border border-border rounded hover:bg-muted disabled:opacity-40 transition"
            >
              First
            </button>
            <button
              disabled={page === 0}
              onClick={() => setPage((p) => p - 1)}
              className="px-2 py-1 border border-border rounded hover:bg-muted disabled:opacity-40 transition"
            >
              Prev
            </button>
            <span className="px-3 py-1 text-muted-foreground">
              {page + 1} / {totalPages}
            </span>
            <button
              disabled={page >= totalPages - 1}
              onClick={() => setPage((p) => p + 1)}
              className="px-2 py-1 border border-border rounded hover:bg-muted disabled:opacity-40 transition"
            >
              Next
            </button>
            <button
              disabled={page >= totalPages - 1}
              onClick={() => setPage(totalPages - 1)}
              className="px-2 py-1 border border-border rounded hover:bg-muted disabled:opacity-40 transition"
            >
              Last
            </button>
          </div>
        </div>
      )}

      {/* Selected shortlist summary */}
      {selected.size > 0 && (
        <div className="sticky bottom-0 border border-border rounded-lg p-3 bg-white dark:bg-slate-950 shadow-lg flex items-center justify-between">
          <p className="text-sm font-medium">
            {selected.size} epitope{selected.size !== 1 ? "s" : ""} selected for
            vaccine construct
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => setSelected(new Set())}
              className="px-3 py-1.5 text-sm border border-border rounded-md hover:bg-muted transition"
            >
              Clear
            </button>
            <button
              onClick={() => {
                // Export only selected rows -- re-use CSV with ID filter
                // For now, copy to clipboard as a quick action
                const selectedEps = data?.epitopes.filter((e) =>
                  selected.has(e.id)
                );
                if (selectedEps) {
                  const text = selectedEps
                    .map(
                      (e) =>
                        `${e.rank}\t${e.peptide_seq}\t${e.hla_allele}\t${e.gene}\t${e.immunogenicity_score.toFixed(3)}`
                    )
                    .join("\n");
                  navigator.clipboard.writeText(
                    "Rank\tPeptide\tHLA\tGene\tScore\n" + text
                  );
                }
              }}
              className="px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded-md hover:opacity-90 transition"
            >
              Copy Selected
            </button>
            <button
              onClick={() => {
                const ids = Array.from(selected).join(",");
                router.push(`/analysis/${analysisId}/construct?ids=${ids}`);
              }}
              className="px-3 py-1.5 text-sm bg-emerald-600 text-white rounded-md hover:bg-emerald-700 transition font-medium"
            >
              Build Construct
            </button>
            <button
              disabled={blastRunning}
              onClick={async () => {
                if (!session?.accessToken) return;
                setBlastRunning(true);
                try {
                  const res = await fetch("/api/py/api/blast/check", {
                    method: "POST",
                    headers: {
                      "Content-Type": "application/json",
                      Authorization: `Bearer ${session.accessToken}`,
                    },
                    body: JSON.stringify({
                      analysis_id: parseInt(analysisId, 10),
                      epitope_ids: Array.from(selected),
                    }),
                  });
                  if (!res.ok) throw new Error(`HTTP ${res.status}`);
                  const json = await res.json();
                  const map: Record<number, { is_self_similar: boolean; max_identity_pct: number; status: string }> = {};
                  for (const r of json.results) {
                    map[r.epitope_id] = { is_self_similar: r.is_self_similar, max_identity_pct: r.max_identity_pct, status: r.status };
                  }
                  setBlastResults((prev) => ({ ...prev, ...map }));
                } catch (e) {
                  setError(e instanceof Error ? e.message : "BLAST check failed");
                } finally {
                  setBlastRunning(false);
                }
              }}
              className="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-md hover:bg-indigo-700 transition font-medium disabled:opacity-50"
              title="Check selected peptides against human proteome (NCBI BLAST)"
            >
              {blastRunning ? "BLASTing..." : "BLAST Check"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
