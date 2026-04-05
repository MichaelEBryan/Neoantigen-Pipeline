"use client";

/**
 * History page -- flat chronological list of all analyses across all
 * projects. Filterable by status. Links to analysis detail and project.
 *
 * Fetches from GET /api/py/api/analyses?status=...&skip=...&limit=...
 */

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useSession } from "next-auth/react";
import { Loader2, AlertCircle, Clock, Filter } from "lucide-react";

interface AnalysisRow {
  id: number;
  project_id: number;
  status: string;
  input_type: string;
  created_at: string;
  completed_at: string | null;
  project_name: string | null;
  cancer_type: string | null;
  variant_count: number | null;
  epitope_count: number | null;
}

const STATUS_OPTIONS = ["all", "queued", "running", "complete", "failed", "cancelled"];

const STATUS_DOT: Record<string, string> = {
  queued: "bg-gray-400",
  running: "bg-blue-500 animate-pulse",
  complete: "bg-emerald-500",
  failed: "bg-red-500",
  cancelled: "bg-amber-500",
};

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function HistoryPage() {
  const { data: session } = useSession();
  const [analyses, setAnalyses] = useState<AnalysisRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("all");
  const [page, setPage] = useState(0);
  const pageSize = 25;

  const token = (session as any)?.accessToken as string | undefined;

  const fetchAnalyses = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        skip: String(page * pageSize),
        limit: String(pageSize),
      });
      if (statusFilter !== "all") params.set("status", statusFilter);

      const res = await fetch(`/api/py/api/analyses/?${params}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setAnalyses(data.analyses);
      setTotal(data.total);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [token, page, statusFilter]);

  useEffect(() => {
    fetchAnalyses();
  }, [fetchAnalyses]);

  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className="space-y-6">
      {/* Header + filter */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-bold text-foreground">History</h1>
          <p className="text-sm text-muted-foreground mt-1">
            All analyses across your projects
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-muted-foreground" />
          <select
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              setPage(0); // reset to first page on filter change
            }}
            className="text-sm border border-border rounded-md px-3 py-1.5 bg-white dark:bg-slate-950 text-foreground"
          >
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s === "all" ? "All statuses" : s.charAt(0).toUpperCase() + s.slice(1)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 dark:bg-red-900/20 rounded-md px-4 py-3">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* Empty */}
      {!loading && !error && analyses.length === 0 && (
        <div className="text-center py-16 border border-border rounded-lg bg-white dark:bg-slate-950">
          <Clock className="w-10 h-10 mx-auto text-muted-foreground/50 mb-3" />
          <p className="text-muted-foreground">
            {statusFilter !== "all"
              ? `No ${statusFilter} analyses found.`
              : "No analyses yet."}
          </p>
        </div>
      )}

      {/* Table */}
      {!loading && analyses.length > 0 && (
        <div className="border border-border rounded-lg bg-white dark:bg-slate-950 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/20">
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">ID</th>
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">Project</th>
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">Status</th>
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">Input</th>
                <th className="text-right py-2.5 px-4 font-medium text-muted-foreground">Variants</th>
                <th className="text-right py-2.5 px-4 font-medium text-muted-foreground">Epitopes</th>
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">Created</th>
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">Completed</th>
              </tr>
            </thead>
            <tbody>
              {analyses.map((a) => (
                <tr
                  key={a.id}
                  className="border-b border-border last:border-0 hover:bg-muted/10 transition"
                >
                  <td className="py-2.5 px-4">
                    <Link
                      href={`/analysis/${a.id}`}
                      className="font-medium text-primary hover:underline"
                    >
                      #{a.id}
                    </Link>
                  </td>
                  <td className="py-2.5 px-4">
                    <Link
                      href={`/projects/${a.project_id}`}
                      className="text-foreground hover:text-blue-600 dark:hover:text-blue-400 transition"
                    >
                      {a.project_name || `#${a.project_id}`}
                    </Link>
                    {a.cancer_type && (
                      <span className="block text-xs text-muted-foreground/60 mt-0.5">
                        {a.cancer_type}
                      </span>
                    )}
                  </td>
                  <td className="py-2.5 px-4">
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        className={`w-2 h-2 rounded-full ${STATUS_DOT[a.status] || STATUS_DOT.queued}`}
                      />
                      <span className="capitalize">{a.status}</span>
                    </span>
                  </td>
                  <td className="py-2.5 px-4 uppercase text-xs text-muted-foreground font-medium">
                    {a.input_type}
                  </td>
                  <td className="py-2.5 px-4 text-right tabular-nums">
                    {a.variant_count ?? "--"}
                  </td>
                  <td className="py-2.5 px-4 text-right tabular-nums">
                    {a.epitope_count ?? "--"}
                  </td>
                  <td className="py-2.5 px-4 text-muted-foreground">
                    {formatDate(a.created_at)}
                  </td>
                  <td className="py-2.5 px-4 text-muted-foreground">
                    {a.completed_at ? formatDate(a.completed_at) : "--"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between pt-1">
          <p className="text-xs text-muted-foreground">
            Showing {page * pageSize + 1}-{Math.min((page + 1) * pageSize, total)} of {total}
          </p>
          <div className="flex gap-1">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="px-3 py-1 text-xs border border-border rounded hover:bg-muted transition disabled:opacity-40"
            >
              Previous
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="px-3 py-1 text-xs border border-border rounded hover:bg-muted transition disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
