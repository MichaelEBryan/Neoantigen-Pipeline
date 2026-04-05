"use client";

/**
 * Analysis detail page.
 *
 * Shows:
 *  - Real-time pipeline status (PipelineStatus component with WS)
 *  - Result counts (variants, epitopes)
 *  - Links to genome browser and results table (shown when complete)
 *  - Analysis metadata (input type, HLA, timestamps)
 */

import { useParams, useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import PipelineStatus from "@/components/pipeline-status";

interface AnalysisData {
  id: number;
  project_id: number;
  status: string;
  input_type: string;
  hla_provided: boolean;
  isambard_job_id: string | null;
  created_at: string;
  completed_at: string | null;
  project_name?: string;
  cancer_type?: string;
  project_analysis_number?: number;
  project_analysis_total?: number;
}

interface StatusData {
  analysis_id: number;
  status: string;
  progress_pct: number;
  variant_count: number;
  epitope_count: number;
  updated_at: string;
}

export default function AnalysisPage() {
  const params = useParams();
  const router = useRouter();
  const { data: session } = useSession();
  const analysisId = Number(params.id);

  const [analysis, setAnalysis] = useState<AnalysisData | null>(null);
  const [statusData, setStatusData] = useState<StatusData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch analysis details + status on mount
  useEffect(() => {
    if (!session?.accessToken || !analysisId) return;

    const headers = { Authorization: `Bearer ${session.accessToken}` };

    Promise.all([
      fetch(`/api/py/api/analyses/${analysisId}`, { headers }).then((r) =>
        r.ok ? r.json() : Promise.reject("Failed to load analysis")
      ),
      fetch(`/api/py/api/analyses/${analysisId}/status`, { headers }).then((r) =>
        r.ok ? r.json() : Promise.reject("Failed to load status")
      ),
    ])
      .then(([a, s]) => {
        setAnalysis(a);
        setStatusData(s);
      })
      .catch((e) => setError(typeof e === "string" ? e : "Failed to load data"))
      .finally(() => setLoading(false));
  }, [session?.accessToken, analysisId]);

  // Called when pipeline finishes (from WS or polling)
  const handlePipelineComplete = useCallback(
    (finalStatus: string) => {
      // Re-fetch status to get final variant/epitope counts
      if (!session?.accessToken) return;
      fetch(`/api/py/api/analyses/${analysisId}/status`, {
        headers: { Authorization: `Bearer ${session.accessToken}` },
      })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (data) setStatusData(data);
        })
        .catch(() => {});

      // Update local analysis status
      setAnalysis((prev) => (prev ? { ...prev, status: finalStatus } : prev));
    },
    [session?.accessToken, analysisId]
  );

  const isComplete = analysis?.status === "complete" || statusData?.status === "complete";
  const variantCount = statusData?.variant_count ?? 0;
  const epitopeCount = statusData?.epitope_count ?? 0;

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        <span className="ml-3 text-muted-foreground">Loading analysis...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-20">
        <p className="text-red-600 dark:text-red-400">{error}</p>
        <button
          onClick={() => router.push("/dashboard")}
          className="mt-4 text-sm text-blue-500 hover:underline"
        >
          Back to dashboard
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-5xl">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">
            {analysis?.project_name
              ? `${analysis.project_name} -- Analysis ${analysis.project_analysis_number ?? analysisId}`
              : `Analysis ${analysisId}`}
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            {analysis?.cancer_type && `${analysis.cancer_type} \u00b7 `}
            {analysis?.input_type?.toUpperCase()} analysis
            {analysis?.project_analysis_total && analysis.project_analysis_total > 1
              ? ` (${analysis.project_analysis_number} of ${analysis.project_analysis_total})`
              : ""}
            {analysis?.created_at &&
              ` \u00b7 ${new Date(analysis.created_at).toLocaleDateString()}`}
          </p>
        </div>
        <div className="flex gap-2">
          {isComplete && (
            <>
              <Link
                href={`/analysis/${analysisId}/browser`}
                className="px-4 py-2 border border-border rounded-md hover:bg-gray-50 dark:hover:bg-gray-800 transition text-sm font-medium"
              >
                Genome Browser
              </Link>
              <Link
                href={`/analysis/${analysisId}/results`}
                className="px-4 py-2 bg-primary text-white rounded-lg hover:bg-secondary transition text-sm font-medium"
              >
                Results Table
              </Link>
            </>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Pipeline status panel (left/top) */}
        <div className="lg:col-span-2 border border-border rounded-lg p-6 bg-white dark:bg-slate-950">
          <h2 className="text-base font-semibold text-foreground mb-4">
            Pipeline Status
          </h2>
          {session?.accessToken ? (
            <PipelineStatus
              analysisId={analysisId}
              accessToken={session.accessToken}
              onComplete={handlePipelineComplete}
            />
          ) : (
            <p className="text-muted-foreground text-sm">Authenticating...</p>
          )}
        </div>

        {/* Results summary (right/bottom) */}
        <div className="lg:col-span-1 space-y-6">
          {/* Counts card */}
          <div className="border border-border rounded-lg p-6 bg-white dark:bg-slate-950">
            <h2 className="text-base font-semibold text-foreground mb-4">
              Results
            </h2>
            <div className="space-y-4">
              <div>
                <p className="text-xs text-muted-foreground uppercase tracking-wide">
                  Variants
                </p>
                <p className="text-3xl font-bold text-foreground mt-1 tabular-nums">
                  {variantCount}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground uppercase tracking-wide">
                  Epitopes
                </p>
                <p className="text-3xl font-bold text-foreground mt-1 tabular-nums">
                  {epitopeCount}
                </p>
              </div>
              {isComplete && epitopeCount > 0 && (
                <Link
                  href={`/analysis/${analysisId}/results`}
                  className="block text-center w-full py-2 text-sm font-medium text-blue-500 border border-blue-200 dark:border-blue-800 rounded-md hover:bg-blue-50 dark:hover:bg-blue-900/20 transition mt-2"
                >
                  View all epitopes
                </Link>
              )}
            </div>
          </div>

          {/* Metadata card */}
          <div className="border border-border rounded-lg p-6 bg-white dark:bg-slate-950">
            <h2 className="text-base font-semibold text-foreground mb-4">
              Details
            </h2>
            <dl className="space-y-3 text-sm">
              <div>
                <dt className="text-muted-foreground">Input type</dt>
                <dd className="font-medium text-foreground">
                  {analysis?.input_type?.toUpperCase()}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">HLA alleles</dt>
                <dd className="font-medium text-foreground">
                  {analysis?.hla_provided ? "Provided" : "Auto-detect"}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Created</dt>
                <dd className="font-medium text-foreground">
                  {analysis?.created_at
                    ? new Date(analysis.created_at).toLocaleString()
                    : "--"}
                </dd>
              </div>
              {analysis?.completed_at && (
                <div>
                  <dt className="text-muted-foreground">Completed</dt>
                  <dd className="font-medium text-foreground">
                    {new Date(analysis.completed_at).toLocaleString()}
                  </dd>
                </div>
              )}
            </dl>
          </div>
        </div>
      </div>
    </div>
  );
}
