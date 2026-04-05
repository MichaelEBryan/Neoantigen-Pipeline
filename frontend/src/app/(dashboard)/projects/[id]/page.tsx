"use client";

/**
 * Project detail page -- shows project metadata and a table of all
 * analyses within the project. Each row links to the analysis detail page.
 *
 * Actions: clone analysis, delete project (with confirmation).
 *
 * Fetches:
 *   GET /api/py/api/projects/{id}
 *   GET /api/py/api/analyses?project_id={id}
 */

import { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { useSession } from "next-auth/react";
import {
  ArrowLeft,
  Plus,
  Copy,
  Trash2,
  Loader2,
  AlertCircle,
  ChevronRight,
} from "lucide-react";

interface Project {
  id: number;
  name: string;
  cancer_type: string;
  stage: string | null;
  reference_genome: string;
  created_at: string;
  analysis_count: number;
}

interface AnalysisRow {
  id: number;
  status: string;
  input_type: string;
  created_at: string;
  completed_at: string | null;
  variant_count: number | null;
  epitope_count: number | null;
}

const STATUS_BADGE: Record<string, string> = {
  queued: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400",
  running: "bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  complete: "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  failed: "bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  cancelled: "bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_BADGE[status] || STATUS_BADGE.queued}`}
    >
      {status === "running" && (
        <span className="w-1.5 h-1.5 rounded-full bg-primary mr-1.5 animate-pulse" />
      )}
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  );
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function ProjectDetailPage() {
  const params = useParams();
  const router = useRouter();
  const { data: session } = useSession();
  const projectId = Number(params.id);
  const token = (session as any)?.accessToken as string | undefined;

  const [project, setProject] = useState<Project | null>(null);
  const [analyses, setAnalyses] = useState<AnalysisRow[]>([]);
  const [totalAnalyses, setTotalAnalyses] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [cloning, setCloningId] = useState<number | null>(null);
  const pageSize = 20;

  const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};

  const fetchData = useCallback(async () => {
    if (!token || !projectId) return;
    setLoading(true);
    setError(null);
    try {
      const [projRes, analysesRes] = await Promise.all([
        fetch(`/api/py/api/projects/${projectId}`, { headers }),
        fetch(
          `/api/py/api/analyses/?project_id=${projectId}&skip=${page * pageSize}&limit=${pageSize}`,
          { headers }
        ),
      ]);

      if (!projRes.ok) throw new Error(projRes.status === 404 ? "Project not found" : `HTTP ${projRes.status}`);
      if (!analysesRes.ok) throw new Error(`Failed to load analyses: HTTP ${analysesRes.status}`);

      const projData = await projRes.json();
      const analysesData = await analysesRes.json();

      setProject(projData);
      setAnalyses(analysesData.analyses);
      setTotalAnalyses(analysesData.total);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [token, projectId, page]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleDelete = async () => {
    if (!token || !projectId) return;
    setDeleting(true);
    try {
      const res = await fetch(`/api/py/api/projects/${projectId}`, {
        method: "DELETE",
        headers,
      });
      if (res.ok || res.status === 204) {
        router.push("/projects");
      } else {
        const data = await res.json().catch(() => null);
        setError(data?.detail || "Failed to delete project");
      }
    } catch {
      setError("Network error deleting project");
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  };

  const handleClone = async (analysisId: number) => {
    if (!token) return;
    setCloningId(analysisId);
    try {
      const res = await fetch(`/api/py/api/analyses/${analysisId}/clone`, {
        method: "POST",
        headers,
      });
      if (res.ok) {
        const data = await res.json();
        // Navigate to the new cloned analysis
        router.push(`/analysis/${data.analysis_id}`);
      } else {
        const data = await res.json().catch(() => null);
        setError(data?.detail || "Failed to clone analysis");
      }
    } catch {
      setError("Network error cloning analysis");
    } finally {
      setCloningId(null);
    }
  };

  const totalPages = Math.ceil(totalAnalyses / pageSize);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error && !project) {
    return (
      <div className="space-y-4">
        <Link href="/projects" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition">
          <ArrowLeft className="w-4 h-4" /> Back to projects
        </Link>
        <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 rounded-md px-4 py-3">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      </div>
    );
  }

  if (!project) return null;

  return (
    <div className="space-y-6">
      {/* Breadcrumb */}
      <Link
        href="/projects"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition"
      >
        <ArrowLeft className="w-4 h-4" /> Back to projects
      </Link>

      {/* Project header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">{project.name}</h1>
          <div className="flex items-center gap-3 text-sm text-muted-foreground mt-1">
            <span>{project.cancer_type}</span>
            {project.stage && <span>Stage {project.stage}</span>}
            <span>{project.reference_genome}</span>
            <span>Created {formatDate(project.created_at)}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/analysis/new"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm bg-primary text-white rounded-lg hover:bg-secondary transition"
          >
            <Plus className="w-3.5 h-3.5" /> New Analysis
          </Link>
          {!confirmDelete ? (
            <button
              onClick={() => setConfirmDelete(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm border border-red-200 text-red-600 rounded-md hover:bg-red-50 transition"
            >
              <Trash2 className="w-3.5 h-3.5" /> Delete
            </button>
          ) : (
            <div className="flex items-center gap-1">
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="px-3 py-1.5 text-sm bg-red-500 text-white rounded-md hover:bg-red-600 transition disabled:opacity-50"
              >
                {deleting ? "Deleting..." : "Confirm Delete"}
              </button>
              <button
                onClick={() => setConfirmDelete(false)}
                className="px-3 py-1.5 text-sm border border-border text-muted-foreground rounded-md hover:bg-muted transition"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 dark:bg-red-900/20 rounded-md px-4 py-3">
          <AlertCircle className="w-4 h-4 flex-shrink-0" /> {error}
        </div>
      )}

      {/* Analyses table */}
      {analyses.length === 0 ? (
        <div className="text-center py-12 border border-border rounded-lg bg-white dark:bg-slate-950">
          <p className="text-muted-foreground">No analyses in this project yet.</p>
          <Link
            href="/analysis/new"
            className="inline-block mt-3 px-4 py-2 bg-primary text-white rounded-lg hover:bg-secondary transition text-sm"
          >
            Create Analysis
          </Link>
        </div>
      ) : (
        <div className="border border-border rounded-lg bg-white dark:bg-slate-950 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/30">
                <th className="text-left py-3 px-4 font-medium text-muted-foreground">ID</th>
                <th className="text-left py-3 px-4 font-medium text-muted-foreground">Status</th>
                <th className="text-left py-3 px-4 font-medium text-muted-foreground">Input</th>
                <th className="text-left py-3 px-4 font-medium text-muted-foreground">Created</th>
                <th className="text-right py-3 px-4 font-medium text-muted-foreground">Variants</th>
                <th className="text-right py-3 px-4 font-medium text-muted-foreground">Epitopes</th>
                <th className="text-right py-3 px-4 font-medium text-muted-foreground">Actions</th>
              </tr>
            </thead>
            <tbody>
              {analyses.map((a) => (
                <tr
                  key={a.id}
                  className="border-b border-border last:border-0 hover:bg-muted/20 transition"
                >
                  <td className="py-3 px-4">
                    <Link
                      href={`/analysis/${a.id}`}
                      className="font-medium text-primary hover:underline"
                    >
                      #{a.id}
                    </Link>
                  </td>
                  <td className="py-3 px-4">
                    <StatusBadge status={a.status} />
                  </td>
                  <td className="py-3 px-4 uppercase text-xs text-muted-foreground font-medium">
                    {a.input_type}
                  </td>
                  <td className="py-3 px-4 text-muted-foreground">{formatDate(a.created_at)}</td>
                  <td className="py-3 px-4 text-right tabular-nums">{a.variant_count ?? "--"}</td>
                  <td className="py-3 px-4 text-right tabular-nums">{a.epitope_count ?? "--"}</td>
                  <td className="py-3 px-4">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        onClick={(e) => {
                          e.preventDefault();
                          handleClone(a.id);
                        }}
                        disabled={cloning === a.id}
                        title="Clone analysis"
                        className="p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted rounded transition disabled:opacity-50"
                      >
                        {cloning === a.id ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <Copy className="w-3.5 h-3.5" />
                        )}
                      </button>
                      <Link
                        href={`/analysis/${a.id}`}
                        className="p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted rounded transition"
                        title="View analysis"
                      >
                        <ChevronRight className="w-3.5 h-3.5" />
                      </Link>
                    </div>
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
            Showing {page * pageSize + 1}-{Math.min((page + 1) * pageSize, totalAnalyses)} of{" "}
            {totalAnalyses}
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
