"use client";

/**
 * Dashboard home page -- shows aggregate stats (projects, active analyses,
 * epitopes) and a recent analyses table. Data comes from a single
 * GET /api/py/api/analyses/stats/dashboard call to avoid N+1.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSession } from "next-auth/react";
import {
  FolderOpen,
  FlaskConical,
  Dna,
  Activity,
  Loader2,
  Plus,
} from "lucide-react";

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

interface DashboardStats {
  total_projects: number;
  total_analyses: number;
  active_analyses: number;
  total_epitopes: number;
  recent_analyses: AnalysisRow[];
}

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
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function DashboardPage() {
  const { data: session } = useSession();
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);

  const token = (session as any)?.accessToken as string | undefined;

  useEffect(() => {
    if (!token) return;

    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/py/api/analyses/stats/dashboard", {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) setStats(data);
      } catch {
        // Non-critical -- dashboard just shows zeros
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [token]);

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Dashboard</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Overview of your vaccine design work
          </p>
        </div>
        <Link
          href="/analysis/new"
          className="inline-flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary/90 transition text-sm font-medium shadow-sm"
        >
          <Plus className="w-4 h-4" />
          New Analysis
        </Link>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          icon={<FolderOpen className="w-5 h-5" />}
          label="Projects"
          value={stats?.total_projects ?? 0}
          loading={loading}
          href="/projects"
        />
        <StatCard
          icon={<FlaskConical className="w-5 h-5" />}
          label="Total Analyses"
          value={stats?.total_analyses ?? 0}
          loading={loading}
        />
        <StatCard
          icon={<Activity className="w-5 h-5" />}
          label="Active Now"
          value={stats?.active_analyses ?? 0}
          loading={loading}
          accent={stats?.active_analyses ? true : false}
        />
        <StatCard
          icon={<Dna className="w-5 h-5" />}
          label="Epitopes Found"
          value={stats?.total_epitopes ?? 0}
          loading={loading}
        />
      </div>

      {/* Recent analyses */}
      <div className="border border-border rounded-lg bg-white">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h2 className="font-semibold text-foreground">Recent Analyses</h2>
          {stats && stats.total_analyses > 5 && (
            <Link
              href="/projects"
              className="text-xs text-primary hover:underline"
            >
              View all
            </Link>
          )}
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-10">
            <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
          </div>
        ) : !stats || stats.recent_analyses.length === 0 ? (
          <div className="text-center py-10">
            <p className="text-sm text-muted-foreground">No analyses yet.</p>
            <Link
              href="/analysis/new"
              className="inline-block mt-3 text-sm text-primary hover:underline"
            >
              Create your first analysis
            </Link>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/20">
                <th className="text-left py-2.5 px-5 font-medium text-muted-foreground">Analysis</th>
                <th className="text-left py-2.5 px-5 font-medium text-muted-foreground">Project</th>
                <th className="text-left py-2.5 px-5 font-medium text-muted-foreground">Status</th>
                <th className="text-left py-2.5 px-5 font-medium text-muted-foreground">Type</th>
                <th className="text-right py-2.5 px-5 font-medium text-muted-foreground">Epitopes</th>
                <th className="text-left py-2.5 px-5 font-medium text-muted-foreground">Created</th>
              </tr>
            </thead>
            <tbody>
              {stats.recent_analyses.map((a) => (
                <tr
                  key={a.id}
                  className="border-b border-border last:border-0 hover:bg-muted/10 transition"
                >
                  <td className="py-2.5 px-5">
                    <Link
                      href={`/analysis/${a.id}`}
                      className="font-medium text-primary hover:underline"
                    >
                      #{a.id}
                    </Link>
                  </td>
                  <td className="py-2.5 px-5">
                    <Link
                      href={`/projects/${a.project_id}`}
                      className="text-muted-foreground hover:text-foreground transition"
                    >
                      {a.project_name || `Project #${a.project_id}`}
                    </Link>
                    {a.cancer_type && (
                      <span className="ml-2 text-xs text-muted-foreground/60">{a.cancer_type}</span>
                    )}
                  </td>
                  <td className="py-2.5 px-5">
                    <span className="inline-flex items-center gap-1.5">
                      <span className={`w-2 h-2 rounded-full ${STATUS_DOT[a.status] || STATUS_DOT.queued}`} />
                      <span className="capitalize">{a.status}</span>
                    </span>
                  </td>
                  <td className="py-2.5 px-5 uppercase text-xs text-muted-foreground font-medium">
                    {a.input_type}
                  </td>
                  <td className="py-2.5 px-5 text-right tabular-nums">
                    {a.epitope_count ?? "--"}
                  </td>
                  <td className="py-2.5 px-5 text-muted-foreground">
                    {formatDate(a.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
  loading,
  href,
  accent,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  loading: boolean;
  href?: string;
  accent?: boolean;
}) {
  const content = (
    <div
      className={`border border-border rounded-lg p-5 bg-white ${
        href ? "hover:border-primary/40 transition cursor-pointer" : ""
      }`}
    >
      <div className="flex items-center gap-3 mb-3">
        <span className="text-muted-foreground">{icon}</span>
        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          {label}
        </span>
      </div>
      {loading ? (
        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
      ) : (
        <p
          className={`text-3xl font-bold tabular-nums ${
            accent ? "text-primary" : "text-foreground"
          }`}
        >
          {value.toLocaleString()}
        </p>
      )}
    </div>
  );

  return href ? <Link href={href}>{content}</Link> : content;
}
