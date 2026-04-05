"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useSession } from "next-auth/react";

// -- Types matching backend ConstructBuildResponse --

interface EpitopeInConstruct {
  id: number;
  peptide_seq: string;
  peptide_length: number;
  gene: string | null;
  protein_change: string | null;
  variant_type: string | null;
  hla_allele: string;
  binding_affinity_nm: number;
  immunogenicity_score: number;
  confidence_tier: string;
  start_pos: number;
  end_pos: number;
  context_25mer: string | null;
  wt_25mer: string | null;
  mutation_position_in_context: number | null;
}

interface LinkerPosition {
  start: number;
  end: number;
  sequence: string;
}

interface Region {
  name: string;
  start: number;
  end: number;
  color: string;
}

interface ConstructData {
  construct_sequence: string;
  total_length: number;
  epitopes: EpitopeInConstruct[];
  linker_positions: LinkerPosition[];
  regions: Region[];
  ordering_used: string;
  warnings?: string[];
}

interface CleavageData {
  cleavage_scores: number[];
  junction_cleavage: {
    junction_index: number;
    position: number;
    score: number;
    is_correct_cleavage: boolean;
  }[];
}

// -- LV2i-inspired color scale --
// Deep green (high) -> amber (medium) -> crimson (low)
// Matching the LV2i palette: #ae262d, #f59e0b, #16a34a

function immunogenicityColor(score: number): string {
  if (score >= 0.7) return "#16a34a"; // deep green -- strong
  if (score >= 0.55) return "#22c55e"; // medium green
  if (score >= 0.4) return "#f59e0b"; // amber -- medium
  if (score >= 0.25) return "#f97316"; // orange
  return "#ae262d"; // crimson -- low
}

function cleavageColor(score: number): string {
  if (score < 0.2) return "#f0f0f0";
  if (score < 0.5) return "#93c5fd";
  if (score < 0.7) return "#3b82f6";
  return "#1e3a5f";
}

// Expression heatmap: log-scale TPM coloring
// No expression (TPM < 1) = gray, low (1-10) = light blue, medium (10-100) = blue, high (>100) = deep purple
function expressionColor(tpm: number | null): string {
  if (tpm === null || tpm < 1) return "#e5e7eb"; // gray -- not expressed
  if (tpm < 5) return "#bfdbfe"; // light blue
  if (tpm < 10) return "#93c5fd";
  if (tpm < 50) return "#60a5fa"; // medium blue
  if (tpm < 100) return "#3b82f6";
  return "#6d28d9"; // deep purple -- highly expressed
}

// Parse RSEM expression matrix (tab-delimited)
// Format: Hugo_Symbol\tEntrez_Gene_Id\tSample1\tSample2...
// Returns map of gene -> average TPM across all samples
function parseRSEMExpression(text: string): Map<string, number> {
  const lines = text.split("\n").filter((l) => l.trim());
  if (lines.length < 2) return new Map();

  const header = lines[0].split("\t");
  // Find sample columns (skip Hugo_Symbol and Entrez_Gene_Id or similar metadata cols)
  // Sample columns start at index 2 typically, but we detect by checking if header[i]
  // looks like a gene/ID field or a sample name
  let dataStartCol = 1;
  for (let i = 0; i < header.length; i++) {
    const h = header[i].toLowerCase().trim();
    if (h === "hugo_symbol" || h === "entrez_gene_id" || h === "gene_id" || h === "gene_name") {
      dataStartCol = i + 1;
    }
  }

  const geneTPM = new Map<string, number>();

  for (let row = 1; row < lines.length; row++) {
    const cols = lines[row].split("\t");
    const gene = cols[0]?.trim();
    if (!gene || gene === "" || gene === "NA") continue;

    // Average TPM across all sample columns
    let sum = 0;
    let count = 0;
    for (let c = dataStartCol; c < cols.length; c++) {
      const val = parseFloat(cols[c]);
      if (!isNaN(val) && val >= 0) {
        sum += val;
        count++;
      }
    }
    if (count > 0) {
      geneTPM.set(gene, sum / count);
    }
  }

  return geneTPM;
}

// Estimated time: ~0.5s per epitope for DB + ordering, ~3s for 25mer per gene
function estimateBuildTime(n: number, mode: string): string {
  const base = Math.max(1, Math.ceil(n * 0.3));
  const extra = mode === "25mer" ? Math.ceil(n * 2) : 0;
  const total = base + extra;
  return total <= 2 ? "a few seconds" : `~${total}s`;
}

// -- Main Page --

export default function ConstructPage() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { data: session } = useSession();
  const analysisId = String(params.id ?? "");

  const idsParam = searchParams.get("ids");
  const epitopeIds = idsParam
    ? idsParam.split(",").map(Number).filter(Boolean)
    : [];

  // State
  const [construct, setConstruct] = useState<ConstructData | null>(null);
  const [cleavage, setCleavage] = useState<CleavageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [cleavageLoading, setCleavageLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [buildStartTime, setBuildStartTime] = useState<number | null>(null);
  const [cleavageStartTime, setCleavageStartTime] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);

  // Controls
  const [ordering, setOrdering] = useState("immunogenicity");
  const [sequenceMode, setSequenceMode] = useState("epitope");
  const [linker, setLinker] = useState("AAY");
  const [showCleavage, setShowCleavage] = useState(false);
  const [selectedPosition, setSelectedPosition] = useState<number | null>(null);

  // Manual reordering -- use a ref alongside state to avoid stale closure
  // in buildConstruct (which is called from handleDragEnd)
  const [dragOrder, setDragOrder] = useState<number[]>(epitopeIds);
  const dragOrderRef = useRef<number[]>(epitopeIds);
  const [dragIndex, setDragIndex] = useState<number | null>(null);

  // RNA expression data
  const [expressionData, setExpressionData] = useState<Map<string, number> | null>(null);
  const [expressionFileName, setExpressionFileName] = useState<string | null>(null);
  const [showExpression, setShowExpression] = useState(false);
  const expressionInputRef = useRef<HTMLInputElement>(null);

  // Keep ref in sync with state
  useEffect(() => { dragOrderRef.current = dragOrder; }, [dragOrder]);

  const heatmapRef = useRef<HTMLDivElement>(null);

  // Elapsed timer for loading states
  useEffect(() => {
    const active = buildStartTime || cleavageStartTime;
    if (!active) { setElapsed(0); return; }
    const interval = setInterval(() => {
      setElapsed(Math.floor((Date.now() - active) / 1000));
    }, 500);
    return () => clearInterval(interval);
  }, [buildStartTime, cleavageStartTime]);

  // -- Build construct --

  const buildConstruct = useCallback(async () => {
    if (!session?.accessToken || epitopeIds.length === 0) return;

    setLoading(true);
    setError(null);
    setCleavage(null);
    setShowCleavage(false);
    setBuildStartTime(Date.now());

    // Use ref for dragOrder to avoid stale closure when called from handleDragEnd
    const orderedIds = ordering === "manual" ? dragOrderRef.current : epitopeIds;

    try {
      const res = await fetch("/api/py/api/construct/build", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session.accessToken}`,
        },
        body: JSON.stringify({
          analysis_id: parseInt(analysisId),
          epitope_ids: orderedIds,
          ordering,
          sequence_mode: sequenceMode,
          linker,
        }),
      });

      if (!res.ok) {
        const err = await res
          .json()
          .catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail || `Build failed: HTTP ${res.status}`);
      }

      const data: ConstructData = await res.json();
      setConstruct(data);
      setDragOrder(data.epitopes.map((e) => e.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to build construct");
    } finally {
      setLoading(false);
      setBuildStartTime(null);
    }
  }, [session?.accessToken, analysisId, epitopeIds, ordering, sequenceMode, linker]);

  useEffect(() => {
    buildConstruct();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.accessToken, ordering, sequenceMode, linker]);

  // -- Cleavage prediction --

  const runCleavage = async () => {
    if (!session?.accessToken || !construct) return;
    setCleavageLoading(true);
    setCleavageStartTime(Date.now());
    try {
      const res = await fetch("/api/py/api/construct/cleavage", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session.accessToken}`,
        },
        body: JSON.stringify({
          sequence: construct.construct_sequence,
          epitope_boundaries: construct.epitopes.map((e) => [e.start_pos, e.end_pos]),
          linker_positions: construct.linker_positions.map((l) => [l.start, l.end]),
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail);
      }

      const data: CleavageData = await res.json();
      setCleavage(data);
      setShowCleavage(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cleavage prediction failed");
    } finally {
      setCleavageLoading(false);
      setCleavageStartTime(null);
    }
  };

  // -- Drag handlers --

  const handleDragStart = (index: number) => setDragIndex(index);
  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault();
    if (dragIndex === null || dragIndex === index) return;
    const newOrder = [...dragOrder];
    const [moved] = newOrder.splice(dragIndex, 1);
    newOrder.splice(index, 0, moved);
    setDragOrder(newOrder);
    setDragIndex(index);
  };
  const handleDragEnd = () => {
    setDragIndex(null);
    if (ordering === "manual") buildConstruct();
  };

  // -- Export FASTA --

  const exportFasta = () => {
    if (!construct) return;
    const header = `>vaccine_construct_analysis_${analysisId} ${construct.epitopes.length}_epitopes ${construct.total_length}aa`;
    const wrapped = construct.construct_sequence.match(/.{1,80}/g)?.join("\n") || construct.construct_sequence;
    const fasta = `${header}\n${wrapped}\n`;
    const blob = new Blob([fasta], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `construct_analysis_${analysisId}.fasta`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(url); document.body.removeChild(a); }, 100);
  };

  // -- Position info lookup --

  const getPositionInfo = (pos: number) => {
    if (!construct) return null;
    for (const lp of construct.linker_positions) {
      if (pos >= lp.start && pos < lp.end) return { type: "linker" as const, linker: lp };
    }
    for (const ep of construct.epitopes) {
      if (pos >= ep.start_pos && pos < ep.end_pos) return { type: "epitope" as const, epitope: ep };
    }
    return null;
  };

  // -- Find mutation position within construct --
  const getMutationPositions = (): Set<number> => {
    if (!construct) return new Set();
    const positions = new Set<number>();
    for (const ep of construct.epitopes) {
      if (ep.protein_change) {
        // For epitope mode: the mutation is somewhere in the short peptide.
        // We don't have per-residue mutation index in the epitope, so we
        // highlight based on variant_type: SNV = single residue change
        if (ep.variant_type === "missense" && ep.protein_change) {
          // Parse the mutated AA position from p.V600E style
          const m = ep.protein_change.match(/p\.\w+?(\d+)/);
          if (m) {
            // For the heatmap, mark the midpoint of the epitope as the mutation
            // (exact position within the 8-11mer isn't stored)
            const mid = Math.floor((ep.start_pos + ep.end_pos) / 2);
            positions.add(mid);
          }
        } else if (ep.variant_type === "frameshift") {
          positions.add(ep.start_pos);
        }
      }
    }
    return positions;
  };

  // -- Render --

  if (epitopeIds.length === 0) {
    return (
      <div className="p-8 text-center text-muted-foreground">
        <p>No epitopes selected. Go back to the results table and select epitopes first.</p>
        <button
          onClick={() => router.push(`/analysis/${analysisId}/results`)}
          className="mt-4 px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary/90 transition"
        >
          Back to Results
        </button>
      </div>
    );
  }

  const mutPositions = getMutationPositions();
  const PX_PER_AA = 4.5; // LV2i uses 4.5px per position

  return (
    <div className="space-y-4 max-w-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Vaccine Construct Builder</h1>
          <p className="text-sm text-muted-foreground">
            {construct
              ? `${construct.epitopes.length} epitopes \u00b7 ${construct.total_length} aa \u00b7 ${new Set(construct.epitopes.map((e) => e.gene).filter(Boolean)).size} genes`
              : `${epitopeIds.length} epitopes selected`}
          </p>
        </div>
        <button
          onClick={() => router.push(`/analysis/${analysisId}/results`)}
          className="px-3 py-1.5 text-sm border border-border rounded-md hover:bg-muted transition"
        >
          Back to Results
        </button>
      </div>

      {/* Controls */}
      <div className="border border-border rounded-lg p-4" style={{ background: "#fff", borderColor: "#eef0f2" }}>
        <div className="flex gap-4 items-end flex-wrap">
          <div className="min-w-[180px]">
            <label className="block text-xs text-muted-foreground mb-1">Ordering</label>
            <select
              value={ordering}
              onChange={(e) => setOrdering(e.target.value)}
              className="w-full px-2 py-1.5 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-[#004875]"
              style={{ borderColor: "#dce0e5" }}
            >
              <option value="immunogenicity">By immunogenicity (descending)</option>
              <option value="alternating">Alternating ends</option>
              <option value="gene_cluster">Gene clustering</option>
              <option value="manual">Manual (drag &amp; drop)</option>
            </select>
          </div>

          <div className="min-w-[160px]">
            <label className="block text-xs text-muted-foreground mb-1">Sequence</label>
            <select
              value={sequenceMode}
              onChange={(e) => setSequenceMode(e.target.value)}
              className="w-full px-2 py-1.5 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-[#004875]"
              style={{ borderColor: "#dce0e5" }}
            >
              <option value="epitope">Minimal epitope (8-11mer)</option>
              <option value="25mer">25mer context</option>
            </select>
          </div>

          <div className="min-w-[90px]">
            <label className="block text-xs text-muted-foreground mb-1">Linker</label>
            <input
              type="text"
              value={linker}
              onChange={(e) => setLinker(e.target.value.toUpperCase())}
              placeholder="AAY"
              className="w-full px-2 py-1.5 border rounded-md text-sm font-mono focus:outline-none focus:ring-2 focus:ring-[#004875] tracking-wider"
              style={{ borderColor: "#dce0e5" }}
              maxLength={20}
            />
          </div>

          {/* Expression data upload */}
          <div className="min-w-[160px]">
            <label className="block text-xs text-muted-foreground mb-1">Expression (RSEM)</label>
            <div className="flex gap-1">
              <input
                ref={expressionInputRef}
                type="file"
                accept=".txt,.tsv,.csv"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  const reader = new FileReader();
                  reader.onload = () => {
                    const text = reader.result as string;
                    const parsed = parseRSEMExpression(text);
                    if (parsed.size > 0) {
                      setExpressionData(parsed);
                      setExpressionFileName(file.name);
                      setShowExpression(true);
                    } else {
                      setError("Could not parse expression data. Expected RSEM format with Hugo_Symbol column.");
                    }
                  };
                  reader.readAsText(file);
                }}
              />
              <button
                onClick={() => expressionInputRef.current?.click()}
                className="w-full px-2 py-1.5 border rounded-md text-sm text-left transition truncate"
                style={{
                  borderColor: expressionData ? "#16a34a" : "#dce0e5",
                  color: expressionData ? "#16a34a" : "#667",
                  background: expressionData ? "rgba(22,163,74,.05)" : "#fff",
                }}
              >
                {expressionFileName || "Upload..."}
              </button>
              {expressionData && (
                <button
                  onClick={() => { setExpressionData(null); setExpressionFileName(null); setShowExpression(false); }}
                  className="px-1.5 py-1 text-xs rounded"
                  style={{ color: "#ae262d", border: "1px solid rgba(174,38,45,.2)" }}
                  title="Remove expression data"
                >
                  ×
                </button>
              )}
            </div>
          </div>

          <div className="flex-1" />

          <div className="flex gap-2">
            <button
              onClick={runCleavage}
              disabled={!construct || cleavageLoading}
              className="px-3 py-1.5 text-sm font-semibold border rounded-md transition disabled:opacity-40"
              style={{
                borderColor: "#004875",
                color: cleavageLoading ? "#667" : "#004875",
                background: cleavageLoading ? "#f7f8fa" : "#fff",
              }}
            >
              {cleavageLoading
                ? `Predicting... ${elapsed > 0 ? `(${elapsed}s)` : ""}`
                : showCleavage
                ? "Re-run Cleavage"
                : "Predict Cleavage"}
            </button>
            <button
              onClick={exportFasta}
              disabled={!construct}
              className="px-3 py-1.5 text-sm font-semibold rounded-md transition disabled:opacity-40"
              style={{ background: "#004875", color: "#fff", border: "1px solid #004875" }}
            >
              Export FASTA
            </button>
          </div>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-md text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Warnings (e.g. 25mer fallback) */}
      {construct?.warnings && construct.warnings.length > 0 && (
        <div className="p-3 bg-amber-50 border border-amber-200 rounded-md text-sm text-amber-800">
          {construct.warnings.map((w, i) => (
            <p key={i}>{w}</p>
          ))}
        </div>
      )}

      {/* Loading with progress */}
      {loading && (
        <div className="border rounded-lg p-6 text-center" style={{ background: "#fff", borderColor: "#eef0f2" }}>
          <div className="flex items-center justify-center gap-2 text-muted-foreground mb-2">
            <div className="w-4 h-4 border-2 border-[#004875] border-t-transparent rounded-full animate-spin" />
            <span className="text-sm font-medium">Building construct...</span>
          </div>
          <p className="text-xs text-muted-foreground">
            {elapsed > 0 && `${elapsed}s elapsed \u00b7 `}
            Estimated {estimateBuildTime(epitopeIds.length, sequenceMode)}
            {sequenceMode === "25mer" && " (25mer context requires protein lookups)"}
          </p>
          <div className="mt-3 mx-auto w-48 h-1.5 bg-gray-100 rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{
                background: "linear-gradient(90deg, #004875, #16a34a)",
                width: `${Math.min(95, elapsed * 8)}%`,
              }}
            />
          </div>
        </div>
      )}

      {/* Construct visualisation */}
      {!loading && construct && (
        <>
          {/* Stats bar */}
          <div className="flex gap-3 flex-wrap">
            {[
              { value: construct.epitopes.length, label: "Epitopes" },
              { value: construct.total_length, label: "Total AA" },
              { value: construct.linker_positions.length, label: "Junctions" },
              { value: new Set(construct.epitopes.map((e) => e.gene).filter(Boolean)).size, label: "Genes" },
            ].map((s) => (
              <div
                key={s.label}
                className="rounded-lg p-3 text-center min-w-[90px]"
                style={{ background: "#fff", border: "1px solid #eef0f2", boxShadow: "0 1px 3px rgba(0,0,0,.06)" }}
              >
                <div className="text-xl font-bold" style={{ color: "#1a2a3a" }}>{s.value}</div>
                <div className="text-[11px]" style={{ color: "#667" }}>{s.label}</div>
              </div>
            ))}
            {cleavage && (
              <div
                className="rounded-lg p-3 text-center min-w-[110px]"
                style={{ background: "#fff", border: "1px solid #eef0f2", boxShadow: "0 1px 3px rgba(0,0,0,.06)" }}
              >
                <div className="text-xl font-bold" style={{ color: "#16a34a" }}>
                  {cleavage.junction_cleavage.filter((j) => j.is_correct_cleavage).length}/
                  {cleavage.junction_cleavage.length}
                </div>
                <div className="text-[11px]" style={{ color: "#667" }}>Correct Cleavage</div>
              </div>
            )}
          </div>

          {/* Heatmap visualisation */}
          <div
            className="rounded-lg p-4 overflow-x-auto"
            ref={heatmapRef}
            style={{ background: "#fff", border: "1px solid #eef0f2", boxShadow: "0 1px 3px rgba(0,0,0,.06)" }}
          >
            {/* Gene region bar -- epitopes only, no linkers */}
            <p className="text-[11px] mb-1.5" style={{ color: "#667" }}>Gene regions</p>
            <div className="relative" style={{ width: construct.total_length * PX_PER_AA, height: 20 }}>
              {construct.epitopes.map((ep, i) => (
                <div
                  key={`reg-${i}`}
                  className="absolute overflow-hidden flex items-center justify-center"
                  style={{
                    left: ep.start_pos * PX_PER_AA,
                    width: (ep.end_pos - ep.start_pos) * PX_PER_AA,
                    height: 18,
                    backgroundColor: construct.regions[i]?.color || "#94a3b8",
                    borderRadius: 2,
                    fontSize: 7,
                    color: "rgba(255,255,255,.9)",
                    fontWeight: 600,
                    whiteSpace: "nowrap",
                    textOverflow: "ellipsis",
                  }}
                  title={`${ep.gene || "?"}: pos ${ep.start_pos + 1}-${ep.end_pos}`}
                >
                  {(ep.end_pos - ep.start_pos) * PX_PER_AA > 30 ? (ep.gene || "?") : ""}
                </div>
              ))}
            </div>

            {/* Immunogenicity heatmap */}
            <p className="text-[11px] mt-3 mb-1" style={{ color: "#667" }}>Immunogenicity</p>
            <div className="flex" style={{ width: construct.total_length * PX_PER_AA }}>
              {construct.construct_sequence.split("").map((aa, i) => {
                const info = getPositionInfo(i);
                const isLinker = info?.type === "linker";
                const isMut = mutPositions.has(i);
                let bg = "#f0f0f0";
                if (info?.type === "epitope") {
                  bg = immunogenicityColor(info.epitope.immunogenicity_score);
                }
                const isSelected = selectedPosition === i;

                return (
                  <div
                    key={i}
                    className="cursor-crosshair flex-shrink-0 relative"
                    style={{
                      width: PX_PER_AA,
                      height: 26,
                      backgroundColor: isLinker ? "transparent" : bg,
                      opacity: isLinker ? 0.3 : 1,
                      borderRadius: 1,
                      transform: isSelected ? "scaleY(1.35)" : "none",
                      transition: "transform 0.08s",
                      zIndex: isSelected ? 2 : 0,
                      // LV2i mutation marker: diagonal stripe
                      backgroundImage: isMut && !isLinker
                        ? "linear-gradient(135deg, rgba(255,255,255,.6) 35%, transparent 35%, transparent 65%, rgba(255,255,255,.6) 65%)"
                        : undefined,
                      backgroundSize: isMut ? "100% 100%" : undefined,
                    }}
                    onClick={() => setSelectedPosition(i)}
                    title={`Pos ${i + 1}: ${aa}${
                      info?.type === "epitope"
                        ? ` (${info.epitope.gene || "?"} \u00b7 ${info.epitope.immunogenicity_score.toFixed(3)})`
                        : info?.type === "linker"
                        ? ` (${info.linker.sequence})`
                        : ""
                    }${isMut ? " \u2022 MUTATION" : ""}`}
                  >
                    {/* Mutation tick mark */}
                    {isMut && !isLinker && (
                      <div
                        className="absolute left-0 right-0 top-0"
                        style={{
                          height: 3,
                          background: "#fff",
                          borderBottom: "1.5px solid #1a2a3a",
                        }}
                      />
                    )}
                  </div>
                );
              })}
            </div>

            {/* Expression heatmap */}
            {showExpression && expressionData && construct && (
              <>
                <p className="text-[11px] mt-3 mb-1" style={{ color: "#667" }}>Gene expression (TPM)</p>
                <div className="flex" style={{ width: construct.total_length * PX_PER_AA }}>
                  {construct.construct_sequence.split("").map((_, i) => {
                    const info = getPositionInfo(i);
                    const isLinker = info?.type === "linker";
                    let tpm: number | null = null;
                    if (info?.type === "epitope" && info.epitope.gene) {
                      tpm = expressionData.get(info.epitope.gene) ?? null;
                    }
                    return (
                      <div
                        key={i}
                        className="cursor-crosshair flex-shrink-0"
                        style={{
                          width: PX_PER_AA,
                          height: 26,
                          backgroundColor: isLinker ? "transparent" : expressionColor(tpm),
                          opacity: isLinker ? 0.3 : 1,
                          borderRadius: 1,
                        }}
                        onClick={() => setSelectedPosition(i)}
                        title={`Pos ${i + 1}: ${info?.type === "epitope" ? `${info.epitope.gene || "?"} ${tpm !== null ? tpm.toFixed(1) + " TPM" : "no data"}` : "linker"}`}
                      />
                    );
                  })}
                </div>
              </>
            )}

            {/* Cleavage overlay */}
            {showCleavage && cleavage && (
              <>
                <p className="text-[11px] mt-3 mb-1" style={{ color: "#667" }}>Proteasomal cleavage</p>
                <div className="flex" style={{ width: construct.total_length * PX_PER_AA }}>
                  {cleavage.cleavage_scores.map((score, i) => (
                    <div
                      key={i}
                      className="cursor-crosshair flex-shrink-0"
                      style={{
                        width: PX_PER_AA,
                        height: 26,
                        backgroundColor: cleavageColor(score),
                        borderRadius: 1,
                      }}
                      onClick={() => setSelectedPosition(i)}
                      title={`Pos ${i + 1}: cleavage ${score.toFixed(3)}`}
                    />
                  ))}
                </div>
              </>
            )}

            {/* Sequence track -- show AA every 5th position */}
            <div className="flex mt-1" style={{ width: construct.total_length * PX_PER_AA }}>
              {construct.construct_sequence.split("").map((aa, i) => (
                <div
                  key={i}
                  className="text-center flex-shrink-0"
                  style={{ width: PX_PER_AA, fontSize: 7, color: "#667", fontFamily: "Courier New, monospace" }}
                >
                  {i % 5 === 0 ? aa : ""}
                </div>
              ))}
            </div>

            {/* Position numbering */}
            <div className="flex" style={{ width: construct.total_length * PX_PER_AA }}>
              {construct.construct_sequence.split("").map((_, i) => (
                <div
                  key={i}
                  className="text-center flex-shrink-0"
                  style={{ width: PX_PER_AA, fontSize: 6, color: "#bbb" }}
                >
                  {i % 10 === 0 ? i + 1 : ""}
                </div>
              ))}
            </div>
          </div>

          {/* Legend */}
          <div className="flex gap-5 flex-wrap text-[11px] px-1" style={{ color: "#667" }}>
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "#16a34a" }} />
              High ({"\u2265"}0.7)
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "#f59e0b" }} />
              Medium (0.4-0.7)
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "#ae262d" }} />
              Low (&lt;0.4)
            </span>
            <span className="flex items-center gap-1.5">
              <span
                className="w-3 h-3 rounded-sm"
                style={{
                  backgroundColor: "#16a34a",
                  backgroundImage: "linear-gradient(135deg, rgba(255,255,255,.6) 35%, transparent 35%, transparent 65%, rgba(255,255,255,.6) 65%)",
                  backgroundSize: "100% 100%",
                }}
              />
              Mutation site
            </span>
            {showExpression && expressionData && (
              <>
                <span style={{ color: "#ccc" }}>|</span>
                <span className="flex items-center gap-1.5">
                  <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "#e5e7eb" }} />
                  Not expressed
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "#60a5fa" }} />
                  Moderate TPM
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "#6d28d9" }} />
                  High TPM
                </span>
              </>
            )}
            {showCleavage && (
              <>
                <span style={{ color: "#ccc" }}>|</span>
                <span className="flex items-center gap-1.5">
                  <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "#93c5fd" }} />
                  Low cleavage
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "#1e3a5f" }} />
                  High cleavage
                </span>
              </>
            )}
          </div>

          {/* Position detail panel */}
          {selectedPosition !== null && construct && (
            <PositionDetail
              position={selectedPosition}
              construct={construct}
              cleavage={cleavage}
              mutPositions={mutPositions}
              expressionData={expressionData}
              onClose={() => setSelectedPosition(null)}
            />
          )}

          {/* Epitope list */}
          <div className="rounded-lg overflow-hidden" style={{ background: "#fff", border: "1px solid #eef0f2", boxShadow: "0 1px 3px rgba(0,0,0,.06)" }}>
            <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: "1px solid #eef0f2" }}>
              <h3 className="text-sm font-semibold" style={{ color: "#1a2a3a" }}>
                Epitopes in construct
                {ordering === "manual" && (
                  <span className="text-xs ml-2 font-normal" style={{ color: "#667" }}>
                    Drag to reorder
                  </span>
                )}
              </h3>
            </div>
            <div>
              {construct.epitopes.map((ep, i) => (
                <div
                  key={ep.id}
                  draggable={ordering === "manual"}
                  onDragStart={() => handleDragStart(i)}
                  onDragOver={(e) => handleDragOver(e, i)}
                  onDragEnd={handleDragEnd}
                  className={`px-4 py-3 flex items-center gap-4 text-sm transition ${
                    ordering === "manual" ? "cursor-grab active:cursor-grabbing" : ""
                  } ${dragIndex === i ? "bg-blue-50/50" : ""}`}
                  style={{ borderBottom: i < construct.epitopes.length - 1 ? "1px solid #f3f4f6" : undefined }}
                >
                  {ordering === "manual" && (
                    <div style={{ color: "#bbb" }}>
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <circle cx="9" cy="6" r="1" /><circle cx="15" cy="6" r="1" />
                        <circle cx="9" cy="12" r="1" /><circle cx="15" cy="12" r="1" />
                        <circle cx="9" cy="18" r="1" /><circle cx="15" cy="18" r="1" />
                      </svg>
                    </div>
                  )}

                  <div className="w-6 text-center text-xs font-medium" style={{ color: "#667" }}>
                    {i + 1}
                  </div>

                  <div
                    className="w-1.5 h-8 rounded-sm flex-shrink-0"
                    style={{ backgroundColor: construct.regions[i]?.color || "#94a3b8" }}
                  />

                  <div className="min-w-[80px]">
                    <div className="font-semibold text-xs" style={{ color: "#1a2a3a" }}>{ep.gene || "-"}</div>
                    <div className="text-[10px] font-mono" style={{ color: "#667" }}>
                      {ep.protein_change || "-"}
                    </div>
                  </div>

                  {/* Peptide with mutation highlighted */}
                  <div className="font-mono text-xs tracking-wide flex-1">
                    {ep.peptide_seq.split("").map((aa, ai) => {
                      // Highlight mutation residue: for missense, approximate from protein_change
                      const isMutResidue = ep.variant_type === "missense" && ep.protein_change
                        ? ai === Math.floor(ep.peptide_length / 2)  // approximate mid-point
                        : ep.variant_type === "frameshift" && ai === 0;
                      return (
                        <span
                          key={ai}
                          style={isMutResidue ? {
                            background: "#ae262d",
                            color: "#fff",
                            fontWeight: 700,
                            borderRadius: 2,
                            padding: "0 1px",
                          } : { color: "#1a2a3a" }}
                        >
                          {aa}
                        </span>
                      );
                    })}
                  </div>

                  <div className="text-xs font-mono" style={{ color: "#667" }}>
                    {ep.hla_allele}
                  </div>

                  <div className="text-xs">
                    <span style={{ color: ep.binding_affinity_nm <= 50 ? "#16a34a" : ep.binding_affinity_nm <= 500 ? "#f59e0b" : "#ae262d" }}>
                      {ep.binding_affinity_nm.toFixed(0)} nM
                    </span>
                  </div>

                  <div className="text-xs font-semibold w-12 text-right" style={{ color: "#1a2a3a" }}>
                    {ep.immunogenicity_score.toFixed(3)}
                  </div>

                  <span
                    className="px-2 py-0.5 rounded-full text-[10px] font-medium"
                    style={{
                      background: ep.confidence_tier === "high" ? "rgba(22,163,74,.1)" : ep.confidence_tier === "medium" ? "rgba(245,158,11,.1)" : "rgba(174,38,45,.1)",
                      color: ep.confidence_tier === "high" ? "#16a34a" : ep.confidence_tier === "medium" ? "#d97706" : "#ae262d",
                    }}
                  >
                    {ep.confidence_tier}
                  </span>

                  <button
                    onClick={() => router.push(`/analysis/${analysisId}/explain/${ep.id}`)}
                    className="px-2 py-0.5 text-[10px] rounded transition"
                    style={{ border: "1px solid rgba(0,72,117,.3)", color: "#004875" }}
                  >
                    Explain
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* Junction cleavage analysis */}
          {cleavage && cleavage.junction_cleavage.length > 0 && (
            <div className="rounded-lg overflow-hidden" style={{ background: "#fff", border: "1px solid #eef0f2", boxShadow: "0 1px 3px rgba(0,0,0,.06)" }}>
              <div className="px-4 py-3" style={{ borderBottom: "1px solid #eef0f2" }}>
                <h3 className="text-sm font-semibold" style={{ color: "#1a2a3a" }}>Junction Cleavage Analysis</h3>
                <p className="text-[11px] mt-0.5" style={{ color: "#667" }}>
                  Each junction has two cleavage sites: C-terminal (end of preceding epitope) and N-terminal (start of next epitope).
                  Scores above 0.5 indicate the proteasome will cleave there, liberating the epitope.
                </p>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr style={{ borderBottom: "1px solid #eef0f2", background: "#f7f8fa" }}>
                      <th className="px-4 py-2 text-left font-medium" style={{ color: "#667" }}>#</th>
                      <th className="px-4 py-2 text-left font-medium" style={{ color: "#667" }}>Junction</th>
                      <th className="px-4 py-2 text-left font-medium" style={{ color: "#667" }}>Linker</th>
                      <th className="px-4 py-2 text-center font-medium" style={{ color: "#667" }}>C-term score</th>
                      <th className="px-4 py-2 text-center font-medium" style={{ color: "#667" }}>N-term score</th>
                      <th className="px-4 py-2 text-center font-medium" style={{ color: "#667" }}>Verdict</th>
                    </tr>
                  </thead>
                  <tbody>
                    {construct.linker_positions.map((lp, i) => {
                      const junctions = cleavage.junction_cleavage.filter((j) => j.junction_index === i);
                      const before = construct.epitopes[i];
                      const after = construct.epitopes[i + 1];
                      // junctions[0] = C-terminal, junctions[1] = N-terminal
                      const cTerm = junctions[0];
                      const nTerm = junctions[1];
                      const anyCleavage = junctions.some((j) => j.is_correct_cleavage);
                      const bestScore = Math.max(...junctions.map((j) => j.score), 0);

                      return (
                        <tr key={i} style={{ borderBottom: "1px solid #eef0f2" }}>
                          <td className="px-4 py-2.5 font-mono" style={{ color: "#667" }}>{i + 1}</td>
                          <td className="px-4 py-2.5">
                            <span className="font-medium" style={{ color: "#1a2a3a" }}>
                              {before?.gene || "?"}
                            </span>
                            <span style={{ color: "#667" }}> / </span>
                            <span className="font-medium" style={{ color: "#1a2a3a" }}>
                              {after?.gene || "?"}
                            </span>
                          </td>
                          <td className="px-4 py-2.5">
                            <span className="font-mono px-1.5 py-0.5 rounded" style={{ background: "#f7f8fa", color: "#667" }}>
                              {lp.sequence}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-center">
                            {cTerm ? (
                              <span
                                className="inline-block font-mono font-semibold px-2 py-0.5 rounded"
                                style={{
                                  backgroundColor: cTerm.score >= 0.5 ? "#dcfce7" : cTerm.score >= 0.3 ? "#fef3c7" : "#fee2e2",
                                  color: cTerm.score >= 0.5 ? "#166534" : cTerm.score >= 0.3 ? "#92400e" : "#991b1b",
                                }}
                              >
                                {cTerm.score.toFixed(3)}
                              </span>
                            ) : <span style={{ color: "#ccc" }}>-</span>}
                          </td>
                          <td className="px-4 py-2.5 text-center">
                            {nTerm ? (
                              <span
                                className="inline-block font-mono font-semibold px-2 py-0.5 rounded"
                                style={{
                                  backgroundColor: nTerm.score >= 0.5 ? "#dcfce7" : nTerm.score >= 0.3 ? "#fef3c7" : "#fee2e2",
                                  color: nTerm.score >= 0.5 ? "#166534" : nTerm.score >= 0.3 ? "#92400e" : "#991b1b",
                                }}
                              >
                                {nTerm.score.toFixed(3)}
                              </span>
                            ) : <span style={{ color: "#ccc" }}>-</span>}
                          </td>
                          <td className="px-4 py-2.5 text-center">
                            {anyCleavage ? (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold"
                                style={{ backgroundColor: "#dcfce7", color: "#166534" }}>
                                <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: "#16a34a" }} />
                                Good
                              </span>
                            ) : bestScore >= 0.3 ? (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold"
                                style={{ backgroundColor: "#fef3c7", color: "#92400e" }}>
                                <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: "#f59e0b" }} />
                                Marginal
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold"
                                style={{ backgroundColor: "#fee2e2", color: "#991b1b" }}>
                                <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: "#ae262d" }} />
                                Poor
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {/* Summary */}
              <div className="px-4 py-3 flex items-center gap-4" style={{ borderTop: "1px solid #eef0f2", background: "#f7f8fa" }}>
                {(() => {
                  const good = construct.linker_positions.filter((_, i) => {
                    const j = cleavage.junction_cleavage.filter((jc) => jc.junction_index === i);
                    return j.some((jc) => jc.is_correct_cleavage);
                  }).length;
                  const total = construct.linker_positions.length;
                  return (
                    <span className="text-xs" style={{ color: "#1a2a3a" }}>
                      <span className="font-semibold">{good}/{total}</span> junctions have predicted cleavage (score {"\u2265"} 0.5)
                    </span>
                  );
                })()}
              </div>
            </div>
          )}

          {/* Construct sequence */}
          <div className="rounded-lg p-4" style={{ background: "#fff", border: "1px solid #eef0f2", boxShadow: "0 1px 3px rgba(0,0,0,.06)" }}>
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-semibold" style={{ color: "#1a2a3a" }}>Construct Sequence</h3>
              <button
                onClick={() => navigator.clipboard.writeText(construct.construct_sequence)}
                className="px-2 py-1 text-xs rounded transition"
                style={{ border: "1px solid #dce0e5", color: "#667" }}
              >
                Copy
              </button>
            </div>
            <div
              className="font-mono text-xs leading-5 break-all rounded p-3 max-h-40 overflow-y-auto"
              style={{ background: "#f7f8fa", color: "#667" }}
            >
              {construct.construct_sequence}
            </div>
          </div>
        </>
      )}
    </div>
  );
}


// -- Position Detail Component --

function PositionDetail({
  position,
  construct,
  cleavage,
  mutPositions,
  expressionData,
  onClose,
}: {
  position: number;
  construct: ConstructData;
  cleavage: CleavageData | null;
  mutPositions: Set<number>;
  expressionData: Map<string, number> | null;
  onClose: () => void;
}) {
  const aa = construct.construct_sequence[position];
  const isMut = mutPositions.has(position);

  let epitope: EpitopeInConstruct | null = null;
  let isLinker = false;

  for (const ep of construct.epitopes) {
    if (position >= ep.start_pos && position < ep.end_pos) {
      epitope = ep;
      break;
    }
  }
  for (const lp of construct.linker_positions) {
    if (position >= lp.start && position < lp.end) {
      isLinker = true;
      break;
    }
  }

  return (
    <div className="rounded-lg p-4" style={{ background: "#fff", border: "1px solid #eef0f2", boxShadow: "0 1px 3px rgba(0,0,0,.06)" }}>
      <div className="flex items-start justify-between mb-3">
        <h3 className="text-sm font-semibold" style={{ color: "#1a2a3a" }}>
          Position {position + 1}
          <span className="font-mono ml-1.5 px-1.5 py-0.5 rounded text-xs" style={{
            background: isMut ? "#ae262d" : "#f7f8fa",
            color: isMut ? "#fff" : "#1a2a3a",
            fontWeight: isMut ? 700 : 500,
          }}>
            {aa}
          </span>
          {isLinker && <span className="text-xs ml-2 font-normal" style={{ color: "#667" }}>Linker</span>}
          {isMut && <span className="text-xs ml-2 font-normal" style={{ color: "#ae262d" }}>Mutation site</span>}
        </h3>
        <button onClick={onClose} className="text-sm" style={{ color: "#667" }}>Close</button>
      </div>

      {epitope && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
            <div>
              <p className="text-[11px]" style={{ color: "#667" }}>Gene</p>
              <p className="font-semibold" style={{ color: "#1a2a3a" }}>{epitope.gene || "-"}</p>
            </div>
            <div>
              <p className="text-[11px]" style={{ color: "#667" }}>Peptide</p>
              <p className="font-mono text-xs">
                {epitope.peptide_seq.split("").map((r, ri) => {
                  const isMutRes = epitope!.variant_type === "missense"
                    ? ri === Math.floor(epitope!.peptide_length / 2)
                    : epitope!.variant_type === "frameshift" && ri === 0;
                  return (
                    <span key={ri} style={isMutRes ? { background: "#ae262d", color: "#fff", fontWeight: 700, borderRadius: 2, padding: "0 1px" } : {}}>
                      {r}
                    </span>
                  );
                })}
              </p>
            </div>
            <div>
              <p className="text-[11px]" style={{ color: "#667" }}>Mutation</p>
              <p className="font-mono text-xs" style={{ color: "#1a2a3a" }}>{epitope.protein_change || "-"}</p>
            </div>
            <div>
              <p className="text-[11px]" style={{ color: "#667" }}>HLA</p>
              <p className="font-mono text-xs" style={{ color: "#1a2a3a" }}>{epitope.hla_allele}</p>
            </div>
            <div>
              <p className="text-[11px]" style={{ color: "#667" }}>IC50</p>
              <p className="font-semibold" style={{ color: epitope.binding_affinity_nm <= 50 ? "#16a34a" : epitope.binding_affinity_nm <= 500 ? "#f59e0b" : "#ae262d" }}>
                {epitope.binding_affinity_nm.toFixed(1)} nM
              </p>
            </div>
            <div>
              <p className="text-[11px]" style={{ color: "#667" }}>Score</p>
              <p className="font-bold" style={{ color: "#1a2a3a" }}>{epitope.immunogenicity_score.toFixed(4)}</p>
            </div>
            <div>
              <p className="text-[11px]" style={{ color: "#667" }}>Tier</p>
              <span
                className="px-2 py-0.5 rounded-full text-xs font-medium"
                style={{
                  background: epitope.confidence_tier === "high" ? "rgba(22,163,74,.1)" : epitope.confidence_tier === "medium" ? "rgba(245,158,11,.1)" : "rgba(174,38,45,.1)",
                  color: epitope.confidence_tier === "high" ? "#16a34a" : epitope.confidence_tier === "medium" ? "#d97706" : "#ae262d",
                }}
              >
                {epitope.confidence_tier}
              </span>
            </div>
            {expressionData && epitope?.gene && (
              <div>
                <p className="text-[11px]" style={{ color: "#667" }}>Expression</p>
                {(() => {
                  const tpm = expressionData.get(epitope!.gene!);
                  return tpm !== undefined ? (
                    <p className="font-mono text-xs" style={{
                      color: tpm < 1 ? "#ae262d" : tpm < 10 ? "#f59e0b" : "#16a34a"
                    }}>
                      {tpm.toFixed(1)} TPM
                    </p>
                  ) : (
                    <p className="text-xs" style={{ color: "#ccc" }}>No data</p>
                  );
                })()}
              </div>
            )}
            {cleavage && position < cleavage.cleavage_scores.length && (
              <div>
                <p className="text-[11px]" style={{ color: "#667" }}>Cleavage</p>
                <p className="font-mono text-xs" style={{ color: "#1a2a3a" }}>{cleavage.cleavage_scores[position].toFixed(3)}</p>
              </div>
            )}
          </div>

          {epitope.context_25mer && (
            <div className="mt-3 pt-3" style={{ borderTop: "1px solid #eef0f2" }}>
              <p className="text-[11px] mb-1" style={{ color: "#667" }}>25mer context (mutant)</p>
              <div className="flex gap-0.5 font-mono text-xs">
                {epitope.context_25mer.split("").map((residue, ri) => {
                  const isMutSite = ri === epitope!.mutation_position_in_context;
                  return (
                    <span
                      key={ri}
                      className="px-0.5 rounded"
                      style={isMutSite ? { background: "#ae262d", color: "#fff", fontWeight: 700 } : { color: "#1a2a3a" }}
                    >
                      {residue}
                    </span>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
