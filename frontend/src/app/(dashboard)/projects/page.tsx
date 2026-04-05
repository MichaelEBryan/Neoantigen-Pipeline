"use client";

/**
 * Projects page -- lists all user projects with analysis counts,
 * status breakdown, and links to project detail.
 *
 * Fetches from GET /api/py/api/projects (paginated).
 * "New Project" button navigates to /analysis/new (project is created
 * implicitly during the analysis creation flow).
 */

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useSession } from "next-auth/react";
import { FolderOpen, Plus, Loader2, AlertCircle } from "lucide-react";

interface StatusCounts {
  queued: number;
  running: number;
  complete: number;
  failed: number;
  cancelled: number;
}

interface Project {
  id: number;
  name: string;
  cancer_type: string;
  stage: string | null;
  reference_genome: string;
  created_at: string;
  analysis_count: number;
  status_counts: StatusCounts | null;
}

const STATUS_COLORS: Record<string, string> = {
  complete: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  running: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  queued: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400",
  failed: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  cancelled: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
};

function StatusPills({ counts }: { counts: StatusCounts }) {
  // Only show statuses that have non-zero counts
  const entries = Object.entries(counts).filter(([, v]) => v > 0);
  if (entries.length === 0) return <span className="text-xs text-muted-foreground">No analyses</span>;

  return (
    <div className="flex flex-wrap gap-1">
      {entries.map(([status, count]) => (
        <span
          key={status}
          className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${STATUS_COLORS[status] || STATUS_COLORS.queued}`}
        >
          {count} {status}
        </span>
      ))}
    </div>
  );
}

export default function ProjectsPage() {
  const { data: session } = useSession();
  const [projects, setProjects] = useState<Project[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const pageSize = 20;

  const token = (session as any)?.accessToken as string | undefined;

  const fetchProjects = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/py/api/projects/?skip=${page * pageSize}&limit=${pageSize}`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setProjects(data.projects);
      setTotal(data.total);
    } catch (e: any) {
      setError(e.message || "Failed to load projects");
    } finally {
      setLoading(false);
    }
  }, [token, page]);

  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Projects</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {total} project{total !== 1 ? "s" : ""}
          </p>
        </div>
        <Link
          href="/analysis/new"
          className="inline-flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-lg hover:bg-secondary transition text-sm font-medium"
        >
          <Plus className="w-4 h-4" />
          New Analysis
        </Link>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded-md px-4 py-3">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          {error}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && projects.length === 0 && (
        <div className="text-center py-16 border border-border rounded-lg bg-white dark:bg-slate-950">
          <FolderOpen className="w-10 h-10 mx-auto text-muted-foreground/50 mb-3" />
          <p className="text-muted-foreground">No projects yet.</p>
          <p className="text-sm text-muted-foreground mt-1">
            Create your first analysis to get started.
          </p>
          <Link
            href="/analysis/new"
            className="inline-block mt-4 px-4 py-2 bg-primary text-white rounded-lg hover:bg-secondary transition text-sm font-medium"
          >
            New Analysis
          </Link>
        </div>
      )}

      {/* Project cards */}
      {!loading && projects.length > 0 && (
        <div className="space-y-3">
          {projects.map((p) => (
            <Link
              key={p.id}
              href={`/projects/${p.id}`}
              className="block border border-border rounded-lg p-5 bg-white dark:bg-slate-950 hover:border-blue-300 dark:hover:border-blue-700 transition group"
            >
              <div className="flex items-start justify-between">
                <div className="space-y-1.5">
                  <h3 className="font-semibold text-foreground group-hover:text-primary transition">
                    {p.name}
                  </h3>
                  <div className="flex items-center gap-3 text-xs text-muted-foreground">
                    <span>{p.cancer_type}</span>
                    {p.stage && <span>Stage {p.stage}</span>}
                    <span>{p.reference_genome}</span>
                    <span>
                      {new Date(p.created_at).toLocaleDateString("en-GB", {
                        day: "numeric",
                        month: "short",
                        year: "numeric",
                      })}
                    </span>
                  </div>
                </div>
                <div className="text-right space-y-1">
                  <p className="text-sm font-medium tabular-nums text-foreground">
                    {p.analysis_count} analysis{p.analysis_count !== 1 ? "es" : ""}
                  </p>
                  {p.status_counts && <StatusPills counts={p.status_counts} />}
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between pt-2">
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
