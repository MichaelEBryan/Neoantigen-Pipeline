"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { useSession } from "next-auth/react";

// -- Types matching backend EpitopeDetailResponse --

interface ExplanationJson {
  presentation_contribution: number;
  binding_rank_contribution: number;
  expression_contribution: number;
  vaf_contribution: number;
  mutation_type_contribution: number;
  processing_contribution: number;
  iedb_contribution: number;
  raw_binding_affinity_nm: number;
  raw_presentation_score: number;
  raw_processing_score: number;
  raw_expression_tpm: number | null;
  raw_vaf: number | null;
  mutation_type: string;
}

interface SiblingEpitope {
  id: number;
  peptide_seq: string;
  hla_allele: string;
  rank: number;
  immunogenicity_score: number;
  binding_affinity_nm: number;
}

interface EpitopeDetail {
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
  confidence_tier: string;
  explanation_json: ExplanationJson | null;
  vaf: number | null;
  scorer_weights: Record<string, number>;
  sibling_epitopes: SiblingEpitope[];
}

// -- Scorer component metadata for display --

const COMPONENT_META: Record<
  string,
  { label: string; description: string; color: string; rawKey?: string; rawUnit?: string }
> = {
  presentation: {
    label: "MHC Presentation",
    description: "MHCflurry presentation score. Combines binding affinity with antigen processing likelihood.",
    color: "#3b82f6", // blue
    rawKey: "raw_presentation_score",
    rawUnit: "",
  },
  binding_rank: {
    label: "Binding Affinity",
    description: "IC50 binding affinity (nM). Lower IC50 = stronger binding = higher score. Log-scaled.",
    color: "#8b5cf6", // violet
    rawKey: "raw_binding_affinity_nm",
    rawUnit: "nM",
  },
  expression: {
    label: "Gene Expression",
    description: "Tumor gene expression (TPM). Higher expression = more target peptide on cell surface.",
    color: "#10b981", // emerald
    rawKey: "raw_expression_tpm",
    rawUnit: "TPM",
  },
  vaf: {
    label: "Variant Allele Freq",
    description: "Fraction of reads supporting the variant. Higher VAF = more clonal = better target.",
    color: "#f59e0b", // amber
    rawKey: "raw_vaf",
    rawUnit: "",
  },
  mutation_type: {
    label: "Mutation Type",
    description: "Frameshift > nonsense > inframe indel > missense. Novel sequences are more immunogenic.",
    color: "#ef4444", // red
  },
  processing: {
    label: "Processing Score",
    description: "Proteasomal cleavage and TAP transport prediction from MHCflurry.",
    color: "#06b6d4", // cyan
    rawKey: "raw_processing_score",
    rawUnit: "",
  },
  iedb: {
    label: "IEDB Immunogenicity",
    description: "Heuristic based on peptide amino acid composition. Aromatic and charged residues at anchor positions.",
    color: "#ec4899", // pink
  },
};

const COMPONENT_ORDER = [
  "presentation",
  "binding_rank",
  "expression",
  "vaf",
  "mutation_type",
  "processing",
  "iedb",
];

// -- Amino acid properties for contact map --

const AA_PROPERTIES: Record<string, { hydrophobic: boolean; charge: number; anchor: boolean; color: string }> = {
  A: { hydrophobic: true, charge: 0, anchor: false, color: "#94a3b8" },
  R: { hydrophobic: false, charge: 1, anchor: false, color: "#3b82f6" },
  N: { hydrophobic: false, charge: 0, anchor: false, color: "#10b981" },
  D: { hydrophobic: false, charge: -1, anchor: false, color: "#ef4444" },
  C: { hydrophobic: true, charge: 0, anchor: false, color: "#eab308" },
  E: { hydrophobic: false, charge: -1, anchor: false, color: "#ef4444" },
  Q: { hydrophobic: false, charge: 0, anchor: false, color: "#10b981" },
  G: { hydrophobic: true, charge: 0, anchor: false, color: "#94a3b8" },
  H: { hydrophobic: false, charge: 0, anchor: false, color: "#8b5cf6" },
  I: { hydrophobic: true, charge: 0, anchor: true, color: "#f97316" },
  L: { hydrophobic: true, charge: 0, anchor: true, color: "#f97316" },
  K: { hydrophobic: false, charge: 1, anchor: false, color: "#3b82f6" },
  M: { hydrophobic: true, charge: 0, anchor: false, color: "#eab308" },
  F: { hydrophobic: true, charge: 0, anchor: true, color: "#f97316" },
  P: { hydrophobic: false, charge: 0, anchor: false, color: "#94a3b8" },
  S: { hydrophobic: false, charge: 0, anchor: false, color: "#10b981" },
  T: { hydrophobic: false, charge: 0, anchor: false, color: "#10b981" },
  W: { hydrophobic: true, charge: 0, anchor: true, color: "#f97316" },
  Y: { hydrophobic: true, charge: 0, anchor: true, color: "#f97316" },
  V: { hydrophobic: true, charge: 0, anchor: true, color: "#f97316" },
};

// Anchor positions for MHC Class I (0-indexed).
// For 9-mers: positions 1 (P2) and 8 (P-omega) are primary anchors.
// For other lengths, last position is always anchor; position 1 is usually anchor.
function getAnchorPositions(length: number): Set<number> {
  const anchors = new Set<number>();
  anchors.add(1); // P2 -- almost universal for MHC-I
  anchors.add(length - 1); // P-omega (C-terminal)
  if (length >= 10) anchors.add(2); // P3 sometimes contributes
  return anchors;
}

// -- Tier styling --

const tierStyles: Record<string, { bg: string; text: string; border: string }> = {
  high: { bg: "bg-green-50 dark:bg-green-900/20", text: "text-green-700 dark:text-green-300", border: "border-green-200 dark:border-green-800" },
  medium: { bg: "bg-yellow-50 dark:bg-yellow-900/20", text: "text-yellow-700 dark:text-yellow-300", border: "border-yellow-200 dark:border-yellow-800" },
  low: { bg: "bg-red-50 dark:bg-red-900/20", text: "text-red-700 dark:text-red-300", border: "border-red-200 dark:border-red-800" },
};

// ============================================================
// Main Page Component
// ============================================================

export default function ExplainPage() {
  const params = useParams();
  const router = useRouter();
  const { data: session } = useSession();

  const analysisId = params.id as string;
  const epitopeId = params.epitopeId as string;

  const [data, setData] = useState<EpitopeDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!session?.accessToken) return;

    fetch(`/api/py/api/epitopes/${epitopeId}`, {
      headers: { Authorization: `Bearer ${session.accessToken}` },
    })
      .then(async (res) => {
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
          throw new Error(err.detail || `HTTP ${res.status}`);
        }
        return res.json();
      })
      .then((d) => {
        setData(d);
        setLoading(false);
      })
      .catch((e) => {
        setError(e.message);
        setLoading(false);
      });
  }, [session?.accessToken, epitopeId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="w-5 h-5 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        <span className="ml-2 text-muted-foreground">Loading epitope data...</span>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-md text-red-700 dark:text-red-200 text-sm">
        {error || "Epitope not found"}
      </div>
    );
  }

  const explanation = data.explanation_json;
  const tier = tierStyles[data.confidence_tier] || tierStyles.low;

  return (
    <div className="space-y-6 max-w-5xl">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">
            Epitope Explainability
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Why this peptide scored {data.immunogenicity_score.toFixed(4)} (rank #{data.rank})
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => router.push(`/analysis/${analysisId}/results`)}
            className="px-3 py-1.5 text-sm border border-border rounded-md hover:bg-muted transition"
          >
            Results Table
          </button>
          <button
            onClick={() =>
              router.push(
                `/analysis/${analysisId}/browser?chr=${data.chrom}&pos=${data.pos}`
              )
            }
            className="px-3 py-1.5 text-sm border border-border rounded-md hover:bg-muted transition"
          >
            Genome Browser
          </button>
        </div>
      </div>

      {/* Peptide Card */}
      <PeptideCard data={data} tier={tier} />

      {/* Two-column layout: Waterfall + Contact Map */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {explanation && (
          <WaterfallChart
            explanation={explanation}
            weights={data.scorer_weights}
            totalScore={data.immunogenicity_score}
          />
        )}
        <ContactMap
          peptideSeq={data.peptide_seq}
          hlaAllele={data.hla_allele}
          bindingAffinity={data.binding_affinity_nm}
        />
      </div>

      {/* Component Detail Cards */}
      {explanation && (
        <ComponentDetails explanation={explanation} weights={data.scorer_weights} />
      )}

      {/* Sibling epitopes from same variant */}
      {data.sibling_epitopes.length > 0 && (
        <SiblingTable
          siblings={data.sibling_epitopes}
          currentId={data.id}
          analysisId={analysisId}
        />
      )}
    </div>
  );
}

// ============================================================
// Peptide Card
// ============================================================

function PeptideCard({
  data,
  tier,
}: {
  data: EpitopeDetail;
  tier: { bg: string; text: string; border: string };
}) {
  return (
    <div className={`border ${tier.border} ${tier.bg} rounded-lg p-5`}>
      <div className="flex items-start gap-6">
        {/* Peptide sequence with large monospace */}
        <div className="flex-1">
          <p className="text-xs text-muted-foreground mb-1">Peptide Sequence</p>
          <div className="flex gap-0.5">
            {data.peptide_seq.split("").map((aa, i) => {
              const props = AA_PROPERTIES[aa] || { color: "#94a3b8" };
              const anchors = getAnchorPositions(data.peptide_length);
              const isAnchor = anchors.has(i);
              return (
                <div
                  key={i}
                  className="flex flex-col items-center"
                  title={`Position ${i + 1} (P${i + 1})${isAnchor ? " - MHC anchor" : ""}`}
                >
                  <span
                    className={`font-mono text-xl font-bold px-1.5 py-1 rounded ${
                      isAnchor ? "ring-2 ring-offset-1 ring-primary" : ""
                    }`}
                    style={{ color: props.color }}
                  >
                    {aa}
                  </span>
                  <span className="text-[10px] text-muted-foreground mt-0.5">
                    P{i + 1}
                  </span>
                </div>
              );
            })}
          </div>
          <div className="flex gap-3 mt-2 text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <span className="inline-block w-2 h-2 rounded-full ring-2 ring-primary" />
              MHC anchor
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-2 h-2 rounded-full" style={{ background: "#f97316" }} />
              Hydrophobic
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-2 h-2 rounded-full" style={{ background: "#3b82f6" }} />
              Positive charge
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-2 h-2 rounded-full" style={{ background: "#ef4444" }} />
              Negative charge
            </span>
          </div>
        </div>

        {/* Key stats */}
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm flex-shrink-0">
          <div>
            <p className="text-xs text-muted-foreground">Gene</p>
            <p className="font-semibold">{data.gene || "-"}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Mutation</p>
            <p className="font-mono text-xs font-medium">{data.protein_change || "-"}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">HLA Allele</p>
            <p className="font-mono text-xs font-medium">{data.hla_allele}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">IC50</p>
            <p className="font-semibold">{data.binding_affinity_nm.toFixed(1)} nM</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Score</p>
            <p className="font-bold text-lg">{data.immunogenicity_score.toFixed(4)}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Confidence</p>
            <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${tier.text}`}>
              {data.confidence_tier}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ============================================================
// SHAP-style Waterfall Chart
// ============================================================

function WaterfallChart({
  explanation,
  weights,
  totalScore,
}: {
  explanation: ExplanationJson;
  weights: Record<string, number>;
  totalScore: number;
}) {
  // Map explanation contributions to component order
  const contributions = COMPONENT_ORDER.map((key) => {
    const contribKey = `${key}_contribution` as keyof ExplanationJson;
    const value = (explanation[contribKey] as number) || 0;
    const weight = weights[key] || 0;
    // Normalize: component score = contribution / weight (if weight > 0)
    const componentScore = weight > 0 ? value / weight : 0;
    return {
      key,
      meta: COMPONENT_META[key],
      contribution: value,
      weight,
      componentScore,
    };
  });

  // Sort by absolute contribution descending (most impactful first)
  const sorted = [...contributions].sort(
    (a, b) => Math.abs(b.contribution) - Math.abs(a.contribution)
  );

  // Max contribution for scaling bars
  const maxContrib = Math.max(...sorted.map((c) => c.contribution), 0.01);

  return (
    <div className="border border-border rounded-lg p-5 bg-white dark:bg-slate-950">
      <h3 className="text-sm font-semibold mb-1">Score Decomposition</h3>
      <p className="text-xs text-muted-foreground mb-4">
        Each bar shows how much that component contributed to the final score of{" "}
        <span className="font-semibold">{totalScore.toFixed(4)}</span>.
        Wider bar = larger impact.
      </p>

      <div className="space-y-2">
        {sorted.map((c) => {
          const barWidth = (c.contribution / maxContrib) * 100;
          return (
            <div key={c.key} className="group">
              <div className="flex items-center gap-3">
                {/* Label */}
                <div className="w-32 text-right text-xs font-medium truncate flex-shrink-0">
                  {c.meta.label}
                </div>

                {/* Bar */}
                <div className="flex-1 h-7 bg-muted/30 rounded relative overflow-hidden">
                  <div
                    className="h-full rounded transition-all duration-500 flex items-center"
                    style={{
                      width: `${Math.max(barWidth, 2)}%`,
                      backgroundColor: c.meta.color,
                      opacity: 0.85,
                    }}
                  >
                    {barWidth > 15 && (
                      <span className="text-[10px] font-mono text-white ml-2">
                        {c.contribution.toFixed(4)}
                      </span>
                    )}
                  </div>
                  {barWidth <= 15 && (
                    <span className="absolute left-[calc(2%+4px)] top-1/2 -translate-y-1/2 text-[10px] font-mono text-muted-foreground">
                      {c.contribution.toFixed(4)}
                    </span>
                  )}
                </div>

                {/* Weight badge */}
                <div className="w-12 text-right text-[10px] text-muted-foreground flex-shrink-0">
                  w={c.weight.toFixed(2)}
                </div>
              </div>

              {/* Tooltip/detail on hover */}
              <div className="hidden group-hover:block ml-36 mt-1 text-[11px] text-muted-foreground pl-1 border-l-2 border-muted">
                {c.meta.description}
                {c.componentScore > 0 && (
                  <span className="ml-2 font-mono">
                    (component = {c.componentScore.toFixed(3)} x {c.weight.toFixed(2)} = {c.contribution.toFixed(4)})
                  </span>
                )}
              </div>
            </div>
          );
        })}

        {/* Total */}
        <div className="flex items-center gap-3 pt-2 border-t border-border mt-2">
          <div className="w-32 text-right text-xs font-bold">Total Score</div>
          <div className="flex-1">
            <span className="font-mono font-bold text-sm">
              {totalScore.toFixed(4)}
            </span>
          </div>
          <div className="w-12" />
        </div>
      </div>
    </div>
  );
}

// ============================================================
// Peptide-MHC Contact Map
// ============================================================

function ContactMap({
  peptideSeq,
  hlaAllele,
  bindingAffinity,
}: {
  peptideSeq: string;
  hlaAllele: string;
  bindingAffinity: number;
}) {
  const length = peptideSeq.length;
  const anchors = getAnchorPositions(length);

  // Heuristic binding contribution per position.
  // Anchor positions contribute most. Flanking positions contribute moderately.
  // Central positions contribute less (they face the TCR, not the MHC groove).
  // This is a simplified model -- real contact maps need crystal structure data.
  const positionScores = peptideSeq.split("").map((aa, i) => {
    const props = AA_PROPERTIES[aa] || { hydrophobic: false, charge: 0, anchor: false };
    let score = 0.3; // baseline

    if (anchors.has(i)) {
      // Anchor positions: hydrophobic residues bind strongly in the B/F pockets
      score = props.hydrophobic ? 0.95 : 0.55;
    } else if (i === 0) {
      // P1: somewhat involved in A pocket
      score = props.hydrophobic ? 0.6 : 0.4;
    } else if (i >= 3 && i <= length - 3) {
      // Central positions: TCR-facing, less MHC contact
      // But aromatic residues can still interact
      score = props.hydrophobic ? 0.35 : 0.25;
    } else {
      // Flanking but non-anchor
      score = props.hydrophobic ? 0.5 : 0.35;
    }

    return {
      position: i,
      aa,
      score,
      isAnchor: anchors.has(i),
      label: anchors.has(i) ? "Anchor" : i >= 3 && i <= length - 3 ? "TCR" : "Flank",
    };
  });

  // Color scale: low (blue) -> high (red)
  function scoreToColor(s: number): string {
    // Simple gradient: blue -> yellow -> red
    if (s < 0.5) {
      const t = s / 0.5;
      const r = Math.round(59 + t * (245 - 59));
      const g = Math.round(130 + t * (158 - 130));
      const b = Math.round(246 + t * (11 - 246));
      return `rgb(${r},${g},${b})`;
    } else {
      const t = (s - 0.5) / 0.5;
      const r = Math.round(245 + t * (239 - 245));
      const g = Math.round(158 - t * 90);
      const b = Math.round(11 - t * 11);
      return `rgb(${r},${g},${b})`;
    }
  }

  return (
    <div className="border border-border rounded-lg p-5 bg-white dark:bg-slate-950">
      <h3 className="text-sm font-semibold mb-1">Peptide-MHC Binding Map</h3>
      <p className="text-xs text-muted-foreground mb-4">
        Estimated per-residue contribution to MHC groove binding.
        Anchor positions (P2, P{length}) bind in the B and F pockets.
        Central residues face the T-cell receptor.
      </p>

      {/* Heat map grid */}
      <div className="flex gap-1 mb-3 justify-center">
        {positionScores.map((ps) => (
          <div key={ps.position} className="flex flex-col items-center">
            {/* Binding strength bar */}
            <div
              className="w-8 rounded-t transition-all"
              style={{
                height: `${Math.max(ps.score * 60, 6)}px`,
                backgroundColor: scoreToColor(ps.score),
              }}
              title={`P${ps.position + 1}: ${ps.aa} - binding score ${ps.score.toFixed(2)}`}
            />
            {/* Amino acid */}
            <div
              className={`w-8 h-8 flex items-center justify-center font-mono text-sm font-bold rounded-b border ${
                ps.isAnchor
                  ? "border-primary bg-primary/10"
                  : "border-border bg-white dark:bg-slate-900"
              }`}
            >
              {ps.aa}
            </div>
            {/* Position label */}
            <span className="text-[9px] text-muted-foreground mt-0.5">
              P{ps.position + 1}
            </span>
            {/* Role label */}
            <span
              className={`text-[8px] mt-0.5 px-1 rounded ${
                ps.label === "Anchor"
                  ? "bg-primary/10 text-primary font-medium"
                  : ps.label === "TCR"
                  ? "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300"
                  : "text-muted-foreground"
              }`}
            >
              {ps.label}
            </span>
          </div>
        ))}
      </div>

      {/* MHC groove diagram */}
      <div className="mt-4 border-t border-border pt-3">
        <div className="flex items-center justify-between text-[10px] text-muted-foreground">
          <span>N-terminus</span>
          <span className="font-medium">{hlaAllele}</span>
          <span>C-terminus</span>
        </div>
        <div className="flex items-center gap-1 mt-1">
          <div className="h-2 flex-1 rounded-l-full bg-gradient-to-r from-blue-200 to-blue-100 dark:from-blue-900 dark:to-blue-800" />
          <div className="text-[9px] text-muted-foreground px-1">MHC groove</div>
          <div className="h-2 flex-1 rounded-r-full bg-gradient-to-r from-blue-100 to-blue-200 dark:from-blue-800 dark:to-blue-900" />
        </div>
        <p className="text-[10px] text-muted-foreground mt-2 text-center">
          IC50 = {bindingAffinity.toFixed(1)} nM
          {bindingAffinity <= 50 && " (strong binder)"}
          {bindingAffinity > 50 && bindingAffinity <= 500 && " (moderate binder)"}
          {bindingAffinity > 500 && " (weak binder)"}
        </p>
      </div>

      {/* Legend */}
      <div className="mt-3 flex items-center justify-center gap-4 text-[10px] text-muted-foreground">
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded" style={{ backgroundColor: scoreToColor(0.2) }} />
          Low binding
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded" style={{ backgroundColor: scoreToColor(0.5) }} />
          Moderate
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded" style={{ backgroundColor: scoreToColor(0.9) }} />
          Strong binding
        </span>
      </div>
    </div>
  );
}

// ============================================================
// Component Detail Cards
// ============================================================

function ComponentDetails({
  explanation,
  weights,
}: {
  explanation: ExplanationJson;
  weights: Record<string, number>;
}) {
  return (
    <div>
      <h3 className="text-sm font-semibold mb-3">Scoring Components</h3>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
        {COMPONENT_ORDER.map((key) => {
          const meta = COMPONENT_META[key];
          const contribKey = `${key}_contribution` as keyof ExplanationJson;
          const contribution = (explanation[contribKey] as number) || 0;
          const weight = weights[key] || 0;
          const componentScore = weight > 0 ? contribution / weight : 0;

          // Get raw value if available
          let rawValue: string | null = null;
          if (meta.rawKey) {
            const raw = explanation[meta.rawKey as keyof ExplanationJson];
            if (raw !== null && raw !== undefined) {
              rawValue = typeof raw === "number" ? raw.toFixed(2) : String(raw);
              if (meta.rawUnit) rawValue += ` ${meta.rawUnit}`;
            }
          }

          // Special: mutation type shows the actual type
          if (key === "mutation_type") {
            rawValue = explanation.mutation_type || "unknown";
          }

          return (
            <div
              key={key}
              className="border border-border rounded-lg p-3 bg-white dark:bg-slate-950"
            >
              <div className="flex items-center gap-2 mb-2">
                <div
                  className="w-2.5 h-2.5 rounded-full"
                  style={{ backgroundColor: meta.color }}
                />
                <span className="text-xs font-medium">{meta.label}</span>
              </div>

              {/* Score gauge */}
              <div className="flex items-baseline gap-2 mb-1">
                <span className="text-2xl font-bold font-mono">
                  {componentScore.toFixed(2)}
                </span>
                <span className="text-xs text-muted-foreground">/ 1.00</span>
              </div>

              {/* Progress bar */}
              <div className="w-full h-1.5 bg-muted/50 rounded-full overflow-hidden mb-2">
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${componentScore * 100}%`,
                    backgroundColor: meta.color,
                  }}
                />
              </div>

              <div className="flex justify-between text-[10px] text-muted-foreground">
                <span>
                  Contribution: <span className="font-mono">{contribution.toFixed(4)}</span>
                </span>
                <span>
                  Weight: <span className="font-mono">{weight.toFixed(2)}</span>
                </span>
              </div>

              {rawValue && (
                <p className="text-[10px] text-muted-foreground mt-1">
                  Raw: <span className="font-mono">{rawValue}</span>
                </p>
              )}

              <p className="text-[10px] text-muted-foreground mt-1 leading-tight">
                {meta.description}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ============================================================
// Sibling Epitopes Table
// ============================================================

function SiblingTable({
  siblings,
  currentId,
  analysisId,
}: {
  siblings: SiblingEpitope[];
  currentId: number;
  analysisId: string;
}) {
  const router = useRouter();

  return (
    <div>
      <h3 className="text-sm font-semibold mb-2">
        Other Epitopes from Same Variant
      </h3>
      <p className="text-xs text-muted-foreground mb-3">
        Different peptide lengths and HLA alleles from the same somatic mutation.
      </p>
      <div className="border border-border rounded-lg overflow-hidden bg-white dark:bg-slate-950">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/30">
              <th className="text-left py-2 px-3 text-xs font-medium">Rank</th>
              <th className="text-left py-2 px-3 text-xs font-medium">Peptide</th>
              <th className="text-left py-2 px-3 text-xs font-medium">HLA</th>
              <th className="text-left py-2 px-3 text-xs font-medium">IC50 (nM)</th>
              <th className="text-left py-2 px-3 text-xs font-medium">Score</th>
            </tr>
          </thead>
          <tbody>
            {siblings.map((s) => (
              <tr
                key={s.id}
                onClick={() =>
                  router.push(`/analysis/${analysisId}/explain/${s.id}`)
                }
                className={`border-b border-border/50 hover:bg-muted/30 cursor-pointer transition ${s.id === currentId ? "bg-primary/5 font-semibold" : ""}`}
              >
                <td className="py-1.5 px-3">#{s.rank}</td>
                <td className="py-1.5 px-3 font-mono text-xs">{s.peptide_seq}</td>
                <td className="py-1.5 px-3 font-mono text-xs">{s.hla_allele}</td>
                <td className="py-1.5 px-3">{s.binding_affinity_nm.toFixed(1)}</td>
                <td className="py-1.5 px-3 font-semibold">
                  {s.immunogenicity_score.toFixed(4)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
