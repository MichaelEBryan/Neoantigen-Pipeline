"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useParams, useSearchParams, useRouter } from "next/navigation";
import { useSession } from "next-auth/react";

// Load IGV.js from CDN as a script tag. The npm import("igv") fails in
// Next.js webpack because IGV.js is a UMD module that doesn't play well
// with dynamic imports. Using the CDN approach gives us window.igv reliably.
function loadIgvScript(): Promise<any> {
  return new Promise((resolve, reject) => {
    if (typeof window !== "undefined" && (window as any).igv) {
      resolve((window as any).igv);
      return;
    }
    const script = document.createElement("script");
    script.src = "https://cdn.jsdelivr.net/npm/igv@3.1.1/dist/igv.min.js";
    script.onload = () => resolve((window as any).igv);
    script.onerror = () => reject(new Error("Failed to load IGV.js from CDN"));
    document.head.appendChild(script);
  });
}

// -- Types matching backend BrowserTracksResponse --

interface VariantFeature {
  chr: string;
  start: number;
  end: number;
  name: string;
  ref: string;
  alt: string;
  gene: string | null;
  variant_type: string;
  vaf: number | null;
  variant_id: number;
}

interface EpitopeFeature {
  chr: string;
  start: number;
  end: number;
  name: string;
  score: number;
  hla_allele: string;
  rank: number;
  tier: string;
  binding_affinity_nm: number;
  gene: string | null;
  protein_change: string | null;
  epitope_id: number;
}

interface TracksData {
  reference_genome: string;
  variants: VariantFeature[];
  epitopes: EpitopeFeature[];
}

// Tier -> color for epitope features
const TIER_COLORS: Record<string, string> = {
  high: "#22c55e",   // green-500
  medium: "#eab308", // yellow-500
  low: "#ef4444",    // red-500
};

// Map our genome names to IGV.js built-in genome IDs
const GENOME_MAP: Record<string, string> = {
  GRCh38: "hg38",
  GRCh37: "hg19",
  hg38: "hg38",
  hg19: "hg19",
};

export default function BrowserPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();
  const { data: session } = useSession();
  const analysisId = params.id as string;

  // Initial locus from query params (set when clicking a row in results table)
  const initChr = searchParams.get("chr");
  const initPos = searchParams.get("pos");
  const initialLocus =
    initChr && initPos ? `${initChr}:${parseInt(initPos, 10)}` : undefined;

  const containerRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const browserRef = useRef<any>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tracksData, setTracksData] = useState<TracksData | null>(null);

  // Selected epitope for detail panel
  const [selectedEpitope, setSelectedEpitope] = useState<EpitopeFeature | null>(null);

  // -- Fetch track data --

  useEffect(() => {
    if (!session?.accessToken) return;

    fetch(`/api/py/api/analyses/${analysisId}/browser/tracks`, {
      headers: { Authorization: `Bearer ${session.accessToken}` },
    })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: TracksData) => setTracksData(data))
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load tracks")
      )
      .finally(() => setLoading(false));
  }, [session?.accessToken, analysisId]);

  // -- Initialize IGV.js once we have track data --

  const initBrowser = useCallback(async () => {
    if (!containerRef.current || !tracksData) return;
    // Avoid double-init
    if (browserRef.current) return;

    // Load IGV.js from CDN (npm dynamic import doesn't work in Next.js webpack)
    const igv = await loadIgvScript();
    if (!igv || !igv.createBrowser) {
      setError("IGV.js loaded but createBrowser not found. Check CDN version.");
      return;
    }

    const genomeId = GENOME_MAP[tracksData.reference_genome] || "hg38";

    // Pick a sensible initial locus
    // Note: feature coords are 0-based (BED), but IGV.js locus strings are 1-based.
    // So we add 1 when building locus strings from feature coordinates.
    let locus = initialLocus;
    if (!locus && tracksData.variants.length > 0) {
      // Default to the first variant (convert 0-based to 1-based for locus)
      const v = tracksData.variants[0];
      const start1 = v.start + 1; // 0-based -> 1-based
      const end1 = v.end;        // 0-based half-open end == 1-based inclusive end
      locus = `${v.chr}:${Math.max(1, start1 - 50)}-${end1 + 50}`;
    }

    // Build variant annotation features for IGV
    const variantFeatures = tracksData.variants.map((v) => ({
      chr: v.chr,
      start: v.start,
      end: v.end,
      name: v.name,
      // Extra fields rendered in popup
      ref: v.ref,
      alt: v.alt,
      vaf: v.vaf != null ? v.vaf.toFixed(2) : "N/A",
      type: v.variant_type,
    }));

    // Build epitope annotation features, colored by tier
    // Also build a lookup map so we can find the full epitope data on click
    // (IGV.js may strip custom properties from features)
    const epitopeLookup = new Map<string, EpitopeFeature>();
    const epitopeFeatures = tracksData.epitopes.map((ep) => {
      const featureName = `#${ep.rank} ${ep.name}`;
      epitopeLookup.set(featureName, ep);
      return {
        chr: ep.chr,
        start: ep.start,
        end: ep.end,
        name: featureName,
        score: ep.score,
        hla: ep.hla_allele,
        tier: ep.tier,
        ic50: ep.binding_affinity_nm.toFixed(1),
        color: TIER_COLORS[ep.tier] || "#9ca3af",
      };
    });

    const options = {
      genome: genomeId,
      locus: locus || "chr7:140,700,000-140,800,000", // BRAF region fallback
      showNavigation: true,
      showRuler: true,
      showCenterGuide: true,
      tracks: [
        // Variant track -- shown as red markers
        {
          type: "annotation" as const,
          format: "bed" as const,
          name: "Somatic Variants",
          features: variantFeatures,
          displayMode: "EXPANDED",
          color: "#dc2626",
          height: 60,
          order: 1,
        },
        // Epitope track -- colored bars by confidence tier
        {
          type: "annotation" as const,
          format: "bed" as const,
          name: "Predicted Epitopes",
          features: epitopeFeatures,
          displayMode: "EXPANDED",
          colorBy: "color",
          height: 150,
          autoHeight: true,
          order: 2,
        },
      ],
    };

    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const browser = await igv.createBrowser(containerRef.current, options as any);
      browserRef.current = browser;

      // Handle epitope clicks -- show detail panel
      // IGV.js trackclick receives an array of {name, value} pairs.
      // We look up the feature name in our epitope map.
      browser.on("trackclick", (_track: unknown, popoverData: unknown) => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const items = popoverData as any[];
        if (!Array.isArray(items)) return undefined;

        // Find the "Name" field in the popup data
        const nameEntry = items.find(
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (item: any) => item.name === "Name" || item.name === "name"
        );
        if (nameEntry) {
          const ep = epitopeLookup.get(nameEntry.value);
          if (ep) {
            setSelectedEpitope(ep);
            return false; // suppress default popup
          }
        }
        return undefined; // allow default popup for variant track
      });
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to initialize genome browser"
      );
    }
  }, [tracksData, initialLocus]);

  useEffect(() => {
    initBrowser();

    // Cleanup on unmount
    return () => {
      if (browserRef.current && typeof window !== "undefined" && (window as any).igv) {
        try {
          (window as any).igv.removeBrowser(browserRef.current);
        } catch { /* ignore cleanup errors */ }
        browserRef.current = null;
      }
    };
  }, [initBrowser]);

  // -- Navigate to locus programmatically --

  const navigateToLocus = useCallback(
    (chr: string, start0: number) => {
      if (!browserRef.current) return;
      // Convert 0-based feature coord to 1-based for IGV.js search
      const pos1 = start0 + 1;
      const locus = `${chr}:${Math.max(1, pos1 - 100)}-${pos1 + 100}`;
      browserRef.current.search(locus);
    },
    []
  );

  // -- Render --

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Genome Browser</h1>
          <p className="text-sm text-muted-foreground">
            Analysis {analysisId}
            {tracksData &&
              ` \u00b7 ${tracksData.variants.length} variant${tracksData.variants.length !== 1 ? "s" : ""}, ${tracksData.epitopes.length} epitope${tracksData.epitopes.length !== 1 ? "s" : ""}`}
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
            onClick={() => router.push(`/analysis/${analysisId}`)}
            className="px-3 py-1.5 text-sm border border-border rounded-md hover:bg-muted transition"
          >
            Back to Analysis
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-md text-sm text-red-700 dark:text-red-200">
          {error}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-20">
          <div className="flex items-center gap-2 text-muted-foreground">
            <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
            Loading track data...
          </div>
        </div>
      )}

      {/* Variant jump buttons -- quick navigation to each mutation */}
      {tracksData && tracksData.variants.length > 0 && (
        <div className="border border-border rounded-lg p-3 bg-white dark:bg-slate-950">
          <p className="text-xs text-muted-foreground mb-2">Jump to variant:</p>
          <div className="flex gap-2 flex-wrap">
            {tracksData.variants.map((v) => (
              <button
                key={v.variant_id}
                onClick={() => navigateToLocus(v.chr, v.start)}
                className="px-2.5 py-1 text-xs border border-border rounded-md hover:bg-muted transition font-mono"
              >
                {v.name}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* IGV.js container */}
      <div
        ref={containerRef}
        className="border border-border rounded-lg overflow-hidden bg-white dark:bg-slate-950"
        style={{ minHeight: loading ? 0 : 400 }}
      />

      {/* Track legend */}
      {!loading && tracksData && (
        <div className="border border-border rounded-lg p-3 bg-white dark:bg-slate-950">
          <p className="text-xs text-muted-foreground mb-2">Legend:</p>
          <div className="flex gap-4 text-xs">
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "#dc2626" }} />
              Somatic Variant
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: TIER_COLORS.high }} />
              High Confidence
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: TIER_COLORS.medium }} />
              Medium Confidence
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: TIER_COLORS.low }} />
              Low Confidence
            </span>
          </div>
        </div>
      )}

      {/* Epitope detail panel (shown when clicking an epitope in the browser) */}
      {selectedEpitope && (
        <div className="border border-border rounded-lg p-4 bg-white dark:bg-slate-950 shadow-lg">
          <div className="flex items-start justify-between mb-3">
            <h3 className="text-sm font-semibold text-foreground">
              Epitope Detail
            </h3>
            <button
              onClick={() => setSelectedEpitope(null)}
              className="text-muted-foreground hover:text-foreground text-sm"
            >
              Close
            </button>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
            <div>
              <p className="text-xs text-muted-foreground">Rank</p>
              <p className="font-medium">#{selectedEpitope.rank}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Peptide</p>
              <p className="font-mono text-xs">{selectedEpitope.name}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">HLA Allele</p>
              <p className="font-mono text-xs">{selectedEpitope.hla_allele}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Gene</p>
              <p className="font-medium">{selectedEpitope.gene || "-"}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Mutation</p>
              <p className="font-mono text-xs">{selectedEpitope.protein_change || "-"}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Score</p>
              <p className="font-medium">{selectedEpitope.score.toFixed(3)}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">IC50 (nM)</p>
              <p className="font-medium">{selectedEpitope.binding_affinity_nm.toFixed(1)}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Confidence</p>
              <span
                className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${
                  selectedEpitope.tier === "high"
                    ? "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300"
                    : selectedEpitope.tier === "medium"
                    ? "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300"
                    : "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300"
                }`}
              >
                {selectedEpitope.tier}
              </span>
            </div>
          </div>
          <div className="mt-3 flex gap-2">
            <button
              onClick={() =>
                router.push(
                  `/analysis/${analysisId}/results?gene=${selectedEpitope.gene || ""}`
                )
              }
              className="px-3 py-1.5 text-xs border border-border rounded-md hover:bg-muted transition"
            >
              View in Results Table
            </button>
            <button
              onClick={() =>
                router.push(
                  `/analysis/${analysisId}/explain/${selectedEpitope.epitope_id}`
                )
              }
              className="px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition"
            >
              View Explanation
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
