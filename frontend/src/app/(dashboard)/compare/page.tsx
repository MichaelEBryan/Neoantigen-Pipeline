"use client";

import { useState, useEffect } from "react";
import { useSession } from "next-auth/react";

// -- Types --

interface AnalysisOption {
  id: number;
  project_name: string;
  cancer_type: string;
  status: string;
}

interface AnalysisSummary {
  analysis_id: number;
  project_name: string;
  cancer_type: string;
  total_epitopes: number;
  total_genes: number;
}

interface HeatmapCell {
  gene: string;
  analysis_id: number;
  epitope_count: number;
  best_score: number;
  best_peptide: string;
  best_hla: string;
  best_ic50: number;
}

interface SharedPeptide {
  peptide_seq: string;
  gene: string | null;
  protein_change: string | null;
  hla_allele: string;
  analysis_ids: number[];
  scores: number[];
  affinities: number[];
}

interface SharedMutation {
  gene: string;
  protein_change: string;
  analysis_ids: number[];
  count: number;
}

interface CompareData {
  analyses: AnalysisSummary[];
  genes: string[];
  heatmap: HeatmapCell[];
  shared_peptides: SharedPeptide[];
  shared_mutations: SharedMutation[];
}

// -- Helpers --

function scoreColor(score: number): string {
  if (score >= 0.7) return "#16a34a";
  if (score >= 0.4) return "#f59e0b";
  return "#ae262d";
}

function scoreBg(score: number): string {
  if (score >= 0.7) return "rgba(22,163,74,.15)";
  if (score >= 0.55) return "rgba(22,163,74,.08)";
  if (score >= 0.4) return "rgba(245,158,11,.12)";
  if (score >= 0.25) return "rgba(245,158,11,.06)";
  return "rgba(174,38,45,.08)";
}

export default function ComparePage() {
  const { data: session } = useSession();

  // Available analyses to pick from
  const [availableAnalyses, setAvailableAnalyses] = useState<AnalysisOption[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  // Comparison data
  const [data, setData] = useState<CompareData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [minScore, setMinScore] = useState(0);
  const [maxIc50, setMaxIc50] = useState(500);

  // Fetch available analyses
  useEffect(() => {
    if (!session?.accessToken) return;
    fetch("/api/py/api/analyses/", {
      headers: { Authorization: `Bearer ${session.accessToken}` },
    })
      .then((r) => (r.ok ? r.json() : { analyses: [] }))
      .then((data) => {
        // The analyses endpoint returns { analyses: [...], total: N }
        const all = data?.analyses || data || [];
        const complete = all.filter((a: Record<string, unknown>) => a.status === "complete");
        setAvailableAnalyses(
          complete.map((a: Record<string, unknown>) => ({
            id: a.id as number,
            project_name: (a.project_name as string) || `Analysis ${a.id}`,
            cancer_type: (a.cancer_type as string) || "unknown",
            status: a.status as string,
          }))
        );
      })
      .catch(() => {});
  }, [session?.accessToken]);

  const toggleAnalysis = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const runComparison = async () => {
    if (!session?.accessToken || selectedIds.size < 2) return;
    setLoading(true);
    setError(null);
    setData(null);

    try {
      const res = await fetch("/api/py/api/compare/analyses", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session.accessToken}`,
        },
        body: JSON.stringify({
          analysis_ids: Array.from(selectedIds),
          min_score: minScore,
          max_ic50: maxIc50,
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }

      const json: CompareData = await res.json();
      setData(json);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Comparison failed");
    } finally {
      setLoading(false);
    }
  };

  // Build lookup for heatmap: (gene, analysis_id) -> cell
  const cellMap = new Map<string, HeatmapCell>();
  if (data) {
    for (const cell of data.heatmap) {
      cellMap.set(`${cell.gene}:${cell.analysis_id}`, cell);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Multi-Patient Comparison</h1>
        <p className="text-sm text-muted-foreground">
          Compare neoantigen predictions across analyses to find shared targets
        </p>
      </div>

      {/* Analysis selection */}
      <div className="border border-border rounded-lg p-4 bg-white dark:bg-slate-950">
        <p className="text-sm font-medium mb-2">Select analyses to compare (min 2)</p>
        <div className="flex flex-wrap gap-2 mb-3">
          {availableAnalyses.length === 0 && (
            <p className="text-sm text-muted-foreground">No completed analyses available</p>
          )}
          {availableAnalyses.map((a) => (
            <button
              key={a.id}
              onClick={() => toggleAnalysis(a.id)}
              className={`px-3 py-1.5 text-xs border rounded-md transition ${
                selectedIds.has(a.id)
                  ? "bg-primary/10 border-primary text-primary font-medium"
                  : "border-border hover:bg-muted"
              }`}
            >
              {a.project_name} ({a.cancer_type})
            </button>
          ))}
        </div>

        <div className="flex gap-3 items-end flex-wrap">
          <div className="min-w-[100px]">
            <label className="block text-xs text-muted-foreground mb-1">Min Score</label>
            <input
              type="number"
              step="0.1"
              min="0"
              max="1"
              value={minScore}
              onChange={(e) => setMinScore(parseFloat(e.target.value) || 0)}
              className="w-full px-2 py-1.5 border border-border rounded-md text-sm"
            />
          </div>
          <div className="min-w-[100px]">
            <label className="block text-xs text-muted-foreground mb-1">Max IC50 (nM)</label>
            <input
              type="number"
              step="50"
              min="0"
              value={maxIc50}
              onChange={(e) => setMaxIc50(parseFloat(e.target.value) || 500)}
              className="w-full px-2 py-1.5 border border-border rounded-md text-sm"
            />
          </div>
          <button
            onClick={runComparison}
            disabled={selectedIds.size < 2 || loading}
            className="px-4 py-1.5 text-sm bg-primary text-primary-foreground rounded-md hover:opacity-90 disabled:opacity-40 transition font-medium"
          >
            {loading ? "Comparing..." : "Compare"}
          </button>
        </div>
      </div>

      {error && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-md text-sm text-red-700">{error}</div>
      )}

      {/* Results */}
      {data && (
        <>
          {/* Summary cards */}
          <div className="flex gap-3 flex-wrap">
            {data.analyses.map((a) => (
              <div
                key={a.analysis_id}
                className="rounded-lg p-3 min-w-[140px] border border-border bg-white dark:bg-slate-950"
              >
                <div className="text-sm font-semibold">{a.project_name}</div>
                <div className="text-xs text-muted-foreground">{a.cancer_type}</div>
                <div className="text-xs mt-1">
                  <span className="font-medium">{a.total_epitopes}</span> epitopes,{" "}
                  <span className="font-medium">{a.total_genes}</span> genes
                </div>
              </div>
            ))}
            <div className="rounded-lg p-3 min-w-[140px] border border-border bg-white dark:bg-slate-950">
              <div className="text-sm font-semibold">Shared</div>
              <div className="text-xs mt-1">
                <span className="font-medium text-green-700">{data.shared_peptides.length}</span> peptides,{" "}
                <span className="font-medium text-blue-700">{data.shared_mutations.length}</span> mutations
              </div>
            </div>
          </div>

          {/* Gene x Analysis heatmap */}
          <div className="border border-border rounded-lg bg-white dark:bg-slate-950 overflow-x-auto">
            <div className="px-4 py-3 border-b border-border">
              <h3 className="text-sm font-semibold">Gene Neoantigen Heatmap</h3>
              <p className="text-xs text-muted-foreground">
                Rows = genes, columns = analyses. Cell color = best immunogenicity score for that gene in that analysis.
              </p>
            </div>
            <div className="p-4 overflow-x-auto">
              <table className="text-xs">
                <thead>
                  <tr>
                    <th className="text-left px-2 py-1 font-medium text-muted-foreground sticky left-0 bg-white dark:bg-slate-950">Gene</th>
                    {data.analyses.map((a) => (
                      <th key={a.analysis_id} className="px-3 py-1 font-medium text-muted-foreground text-center whitespace-nowrap">
                        {a.project_name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.genes.map((gene) => {
                    // Only show genes present in at least one analysis
                    const hasAny = data.analyses.some((a) => cellMap.has(`${gene}:${a.analysis_id}`));
                    if (!hasAny) return null;

                    // Highlight if shared across multiple analyses
                    const presentIn = data.analyses.filter((a) => cellMap.has(`${gene}:${a.analysis_id}`)).length;

                    return (
                      <tr key={gene} className={presentIn > 1 ? "bg-blue-50/50 dark:bg-blue-950/20" : ""}>
                        <td className="px-2 py-1.5 font-medium sticky left-0 bg-inherit whitespace-nowrap">
                          {gene}
                          {presentIn > 1 && (
                            <span className="ml-1.5 px-1 py-0.5 rounded text-[9px] bg-blue-100 text-blue-700 font-semibold">
                              {presentIn}/{data.analyses.length}
                            </span>
                          )}
                        </td>
                        {data.analyses.map((a) => {
                          const cell = cellMap.get(`${gene}:${a.analysis_id}`);
                          if (!cell) {
                            return <td key={a.analysis_id} className="px-3 py-1.5 text-center text-muted-foreground">-</td>;
                          }
                          return (
                            <td
                              key={a.analysis_id}
                              className="px-3 py-1.5 text-center"
                              style={{ backgroundColor: scoreBg(cell.best_score) }}
                              title={`${cell.epitope_count} epitopes, best: ${cell.best_peptide} (${cell.best_hla}, IC50=${cell.best_ic50.toFixed(0)}nM)`}
                            >
                              <span className="font-mono font-semibold" style={{ color: scoreColor(cell.best_score) }}>
                                {cell.best_score.toFixed(3)}
                              </span>
                              <span className="text-muted-foreground ml-1">({cell.epitope_count})</span>
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Shared mutations */}
          {data.shared_mutations.length > 0 && (
            <div className="border border-border rounded-lg bg-white dark:bg-slate-950">
              <div className="px-4 py-3 border-b border-border">
                <h3 className="text-sm font-semibold">
                  Shared Mutations
                  <span className="ml-2 text-xs font-normal text-muted-foreground">
                    ({data.shared_mutations.length} mutations found in multiple analyses)
                  </span>
                </h3>
              </div>
              <div className="p-4">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="text-left px-2 py-1.5 font-medium text-muted-foreground">Gene</th>
                      <th className="text-left px-2 py-1.5 font-medium text-muted-foreground">Mutation</th>
                      <th className="text-left px-2 py-1.5 font-medium text-muted-foreground">Found in</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.shared_mutations as SharedMutation[]).map((m, i) => (
                      <tr key={i} className="border-b border-border/50">
                        <td className="px-2 py-1.5 font-medium">{m.gene}</td>
                        <td className="px-2 py-1.5 font-mono">{m.protein_change}</td>
                        <td className="px-2 py-1.5">
                          <span className="px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 font-semibold text-[10px]">
                            {m.count}/{data.analyses.length} analyses
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Shared peptides */}
          {data.shared_peptides.length > 0 && (
            <div className="border border-border rounded-lg bg-white dark:bg-slate-950">
              <div className="px-4 py-3 border-b border-border">
                <h3 className="text-sm font-semibold">
                  Shared Peptides
                  <span className="ml-2 text-xs font-normal text-muted-foreground">
                    ({data.shared_peptides.length} identical epitopes across analyses -- potential public neoantigens)
                  </span>
                </h3>
              </div>
              <div className="p-4">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="text-left px-2 py-1.5 font-medium text-muted-foreground">Peptide</th>
                      <th className="text-left px-2 py-1.5 font-medium text-muted-foreground">Gene</th>
                      <th className="text-left px-2 py-1.5 font-medium text-muted-foreground">HLA</th>
                      <th className="text-left px-2 py-1.5 font-medium text-muted-foreground">Shared</th>
                      <th className="text-left px-2 py-1.5 font-medium text-muted-foreground">Scores</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.shared_peptides.map((sp, i) => (
                      <tr key={i} className="border-b border-border/50">
                        <td className="px-2 py-1.5 font-mono tracking-wide">{sp.peptide_seq}</td>
                        <td className="px-2 py-1.5 font-medium">{sp.gene || "-"}</td>
                        <td className="px-2 py-1.5 font-mono">{sp.hla_allele}</td>
                        <td className="px-2 py-1.5">
                          <span className="px-1.5 py-0.5 rounded bg-green-100 text-green-700 font-semibold text-[10px]">
                            {sp.analysis_ids.length} analyses
                          </span>
                        </td>
                        <td className="px-2 py-1.5 font-mono">
                          {sp.scores.map((s, si) => (
                            <span key={si}>
                              {si > 0 && ", "}
                              <span style={{ color: scoreColor(s) }}>{s.toFixed(3)}</span>
                            </span>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {data.shared_peptides.length === 0 && data.shared_mutations.length === 0 && (
            <div className="p-6 text-center text-muted-foreground border border-border rounded-lg bg-white dark:bg-slate-950">
              No shared neoantigens found across these analyses. This is expected for most patients --
              neoantigens are typically private (patient-specific) mutations.
            </div>
          )}
        </>
      )}
    </div>
  );
}
