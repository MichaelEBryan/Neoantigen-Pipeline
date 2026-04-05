"use client";

/**
 * Admin panel -- platform-wide view of users, projects, uploaded files,
 * and aggregate stats. Only visible to users with is_admin=true.
 *
 * Tabs: Overview | Users | Projects | Files
 *
 * Default admin: michael.bryan@new.ox.ac.uk (seeded in migration 002).
 */

import { useEffect, useState, useCallback } from "react";
import { useSession } from "next-auth/react";
import { useRouter } from "next/navigation";
import {
  Users,
  FolderOpen,
  FileUp,
  Activity,
  Shield,
  ShieldOff,
  Search,
  Loader2,
  BarChart3,
  HardDrive,
  Trash2,
  X,
  Calendar,
  Download,
} from "lucide-react";

// -- Types --

interface PlatformStats {
  total_users: number;
  total_projects: number;
  total_analyses: number;
  analyses_by_status: Record<string, number>;
  total_variants: number;
  total_epitopes: number;
  total_upload_bytes: number;
  total_upload_files: number;
}

interface AdminUser {
  id: number;
  email: string;
  name: string;
  institution: string | null;
  is_admin: boolean;
  created_at: string;
  last_login_at: string | null;
  terms_accepted_at: string | null;
  project_count: number;
  analysis_count: number;
  epitope_count: number;
}

interface AdminUserProject {
  id: number;
  name: string;
  cancer_type: string;
  analysis_count: number;
  created_at: string;
}

interface AdminUserDetail {
  id: number;
  email: string;
  name: string;
  institution: string | null;
  is_admin: boolean;
  created_at: string;
  last_login_at: string | null;
  terms_accepted_at: string | null;
  project_count: number;
  analysis_count: number;
  epitope_count: number;
  variant_count: number;
  total_upload_bytes: number;
  projects: AdminUserProject[];
}

interface AdminProject {
  id: number;
  name: string;
  cancer_type: string;
  stage: string | null;
  reference_genome: string;
  created_at: string;
  owner_email: string;
  owner_name: string;
  analysis_count: number;
  status_breakdown: Record<string, number>;
}

interface AdminFile {
  id: number;
  analysis_id: number;
  project_name: string;
  owner_email: string;
  file_type: string;
  file_path: string;
  file_size: number | null;
  checksum: string | null;
  analysis_status: string;
  created_at: string;
}

type Tab = "overview" | "users" | "projects" | "files";

// -- Helpers --

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const STATUS_COLOR: Record<string, string> = {
  queued: "bg-gray-400",
  running: "bg-blue-500 animate-pulse",
  complete: "bg-emerald-500",
  failed: "bg-red-500",
  cancelled: "bg-amber-500",
};

export default function AdminPage() {
  const { data: session, status: authStatus } = useSession();
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("overview");

  // Check admin status from backend (not just cached JWT)
  const [isVerifiedAdmin, setIsVerifiedAdmin] = useState<boolean | null>(null);
  useEffect(() => {
    if (!session?.accessToken) return;
    fetch("/api/py/api/auth/me", {
      headers: { Authorization: `Bearer ${session.accessToken}` },
    })
      .then((r) => r.json())
      .then((u) => {
        setIsVerifiedAdmin(u.is_admin === true);
        if (!u.is_admin) router.replace("/dashboard");
      })
      .catch(() => setIsVerifiedAdmin(false));
  }, [session?.accessToken, router]);

  if (authStatus === "loading" || isVerifiedAdmin === null) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const token = session?.accessToken as string;

  const tabs: { key: Tab; label: string; icon: React.ReactNode }[] = [
    { key: "overview", label: "Overview", icon: <BarChart3 className="w-4 h-4" /> },
    { key: "users", label: "Users", icon: <Users className="w-4 h-4" /> },
    { key: "projects", label: "Projects", icon: <FolderOpen className="w-4 h-4" /> },
    { key: "files", label: "Files", icon: <FileUp className="w-4 h-4" /> },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Admin Panel</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Platform-wide overview and user management
        </p>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-border">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition ${
              tab === t.key
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === "overview" && <OverviewTab token={token} />}
      {tab === "users" && <UsersTab token={token} />}
      {tab === "projects" && <ProjectsTab token={token} />}
      {tab === "files" && <FilesTab token={token} />}
    </div>
  );
}

// ============================================================
// OVERVIEW TAB
// ============================================================

function OverviewTab({ token }: { token: string }) {
  const [stats, setStats] = useState<PlatformStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/py/api/admin/stats", {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) setStats(await res.json());
      } catch {
        // silent
      } finally {
        setLoading(false);
      }
    })();
  }, [token]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-10">
        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!stats) {
    return <p className="text-sm text-red-600">Failed to load stats.</p>;
  }

  const cards = [
    { label: "Users", value: stats.total_users, icon: <Users className="w-5 h-5" /> },
    { label: "Projects", value: stats.total_projects, icon: <FolderOpen className="w-5 h-5" /> },
    { label: "Analyses", value: stats.total_analyses, icon: <Activity className="w-5 h-5" /> },
    { label: "Variants", value: stats.total_variants, icon: <BarChart3 className="w-5 h-5" /> },
    { label: "Epitopes", value: stats.total_epitopes, icon: <HardDrive className="w-5 h-5" /> },
    { label: "Uploaded Files", value: stats.total_upload_files, icon: <FileUp className="w-5 h-5" /> },
  ];

  return (
    <div className="space-y-6">
      {/* Stat cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
        {cards.map((c) => (
          <div key={c.label} className="border border-border rounded-lg p-4 bg-white">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-muted-foreground">{c.icon}</span>
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                {c.label}
              </span>
            </div>
            <p className="text-2xl font-bold tabular-nums text-foreground">
              {c.value.toLocaleString()}
            </p>
          </div>
        ))}
      </div>

      {/* Storage */}
      <div className="border border-border rounded-lg p-5 bg-white">
        <h3 className="text-sm font-semibold text-foreground mb-3">Storage</h3>
        <p className="text-lg font-bold text-foreground">
          {formatBytes(stats.total_upload_bytes)}
        </p>
        <p className="text-xs text-muted-foreground mt-1">
          Total uploaded data across {stats.total_upload_files} files
        </p>
      </div>

      {/* Analysis status breakdown */}
      <div className="border border-border rounded-lg p-5 bg-white">
        <h3 className="text-sm font-semibold text-foreground mb-3">Analyses by Status</h3>
        <div className="flex flex-wrap gap-4">
          {Object.entries(stats.analyses_by_status).map(([status, count]) => (
            <div key={status} className="flex items-center gap-2">
              <span
                className={`w-2.5 h-2.5 rounded-full ${STATUS_COLOR[status] || "bg-gray-400"}`}
              />
              <span className="text-sm capitalize text-foreground">{status}</span>
              <span className="text-sm font-bold tabular-nums text-foreground">{count}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ============================================================
// USERS TAB
// ============================================================

function UsersTab({ token }: { token: string }) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [toggling, setToggling] = useState<number | null>(null);
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  const [userDetail, setUserDetail] = useState<AdminUserDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const fetchUsers = useCallback(
    async (q?: string) => {
      setLoading(true);
      try {
        const params = new URLSearchParams({ skip: "0", limit: "100" });
        if (q) params.set("search", q);
        const res = await fetch(`/api/py/api/admin/users?${params}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const data = await res.json();
          setUsers(data.users);
          setTotal(data.total);
        }
      } catch {
        // silent
      } finally {
        setLoading(false);
      }
    },
    [token]
  );

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  const handleSearch = () => fetchUsers(search);

  const [adminToggleMessage, setAdminToggleMessage] = useState<string | null>(null);

  const toggleAdmin = async (userId: number, newValue: boolean) => {
    setToggling(userId);
    setAdminToggleMessage(null);
    try {
      const res = await fetch(`/api/py/api/admin/users/${userId}/admin`, {
        method: "PATCH",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ is_admin: newValue }),
      });
      if (res.ok) {
        const data = await res.json();
        setUsers((prev) =>
          prev.map((u) => (u.id === userId ? { ...u, is_admin: newValue } : u))
        );
        setAdminToggleMessage(
          newValue
            ? `${data.email} is now an admin. They need to log out and back in to see the admin panel.`
            : `${data.email} admin access revoked. Change takes effect on their next login.`
        );
      }
    } catch {
      // silent
    } finally {
      setToggling(null);
    }
  };

  const [deleting, setDeleting] = useState<number | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);

  const fetchUserDetail = async (userId: number) => {
    setSelectedUserId(userId);
    setDetailLoading(true);
    try {
      const res = await fetch(`/api/py/api/admin/users/${userId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setUserDetail(data);
      }
    } catch {
      // silent
    } finally {
      setDetailLoading(false);
    }
  };

  const closeDetailPanel = () => {
    setSelectedUserId(null);
    setUserDetail(null);
  };

  const deleteUser = async (userId: number) => {
    setDeleting(userId);
    try {
      const res = await fetch(`/api/py/api/admin/users/${userId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok || res.status === 204) {
        setUsers((prev) => prev.filter((u) => u.id !== userId));
        setTotal((prev) => prev - 1);
        closeDetailPanel();
      }
    } catch {
      // silent
    } finally {
      setDeleting(null);
      setConfirmDeleteId(null);
    }
  };

  return (
    <div className="space-y-4">
      {/* Search */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            placeholder="Search by name or email..."
            className="w-full pl-9 pr-3 py-2 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary bg-white transition"
          />
        </div>
        <button
          onClick={handleSearch}
          className="px-4 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary/90 transition"
        >
          Search
        </button>
      </div>

      {adminToggleMessage && (
        <div className="p-3 bg-blue-50 border border-blue-200 rounded-lg text-sm text-blue-700 flex items-center justify-between">
          <span>{adminToggleMessage}</span>
          <button onClick={() => setAdminToggleMessage(null)} className="ml-2 text-blue-400 hover:text-blue-600">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      <p className="text-xs text-muted-foreground">{total} users total</p>

      {/* Table */}
      <div className="border border-border rounded-lg bg-white overflow-x-auto">
        {loading ? (
          <div className="flex items-center justify-center py-10">
            <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/20">
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">User</th>
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">Institution</th>
                <th className="text-right py-2.5 px-4 font-medium text-muted-foreground">Projects</th>
                <th className="text-right py-2.5 px-4 font-medium text-muted-foreground">Analyses</th>
                <th className="text-right py-2.5 px-4 font-medium text-muted-foreground">Epitopes</th>
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">Joined</th>
                <th className="text-center py-2.5 px-4 font-medium text-muted-foreground">Admin</th>
                <th className="text-center py-2.5 px-4 font-medium text-muted-foreground">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr
                  key={u.id}
                  onClick={() => fetchUserDetail(u.id)}
                  className="border-b border-border last:border-0 hover:bg-muted/10 transition cursor-pointer"
                >
                  <td className="py-2.5 px-4">
                    <p className="font-medium text-foreground">{u.name}</p>
                    <p className="text-xs text-muted-foreground">{u.email}</p>
                  </td>
                  <td className="py-2.5 px-4 text-muted-foreground">
                    {u.institution || "--"}
                  </td>
                  <td className="py-2.5 px-4 text-right tabular-nums">{u.project_count}</td>
                  <td className="py-2.5 px-4 text-right tabular-nums">{u.analysis_count}</td>
                  <td className="py-2.5 px-4 text-right tabular-nums">{u.epitope_count}</td>
                  <td className="py-2.5 px-4 text-muted-foreground">
                    {formatDate(u.created_at)}
                  </td>
                  <td className="py-2.5 px-4 text-center">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleAdmin(u.id, !u.is_admin);
                      }}
                      disabled={toggling === u.id}
                      className={`inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition ${
                        u.is_admin
                          ? "bg-primary/10 text-primary hover:bg-primary/20"
                          : "bg-muted text-muted-foreground hover:bg-muted/80"
                      }`}
                      title={u.is_admin ? "Revoke admin" : "Grant admin"}
                    >
                      {toggling === u.id ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      ) : u.is_admin ? (
                        <Shield className="w-3 h-3" />
                      ) : (
                        <ShieldOff className="w-3 h-3" />
                      )}
                      {u.is_admin ? "Admin" : "User"}
                    </button>
                  </td>
                  <td className="py-2.5 px-4 text-center">
                    {confirmDeleteId === u.id ? (
                      <div className="flex items-center justify-center gap-1" onClick={(e) => e.stopPropagation()}>
                        <button
                          onClick={() => deleteUser(u.id)}
                          disabled={deleting === u.id}
                          className="px-2 py-1 text-xs bg-red-500 text-white rounded hover:bg-red-600 transition disabled:opacity-50"
                        >
                          {deleting === u.id ? "..." : "Confirm"}
                        </button>
                        <button
                          onClick={() => setConfirmDeleteId(null)}
                          className="px-2 py-1 text-xs border border-border text-muted-foreground rounded hover:bg-muted transition"
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setConfirmDeleteId(u.id);
                        }}
                        className="p-1.5 text-muted-foreground hover:text-red-500 hover:bg-red-50 rounded transition"
                        title="Delete user"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Detail panel slide-over */}
      {selectedUserId !== null && (
        <div className="fixed inset-0 z-50">
          {/* Backdrop */}
          <div
            onClick={closeDetailPanel}
            className="absolute inset-0 bg-black/20"
          />

          {/* Panel */}
          <div className="absolute right-0 top-0 bottom-0 w-full sm:w-96 bg-white border-l border-border shadow-lg flex flex-col">
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-4 border-b border-border">
              <h2 className="text-lg font-semibold text-foreground">User Details</h2>
              <button
                onClick={closeDetailPanel}
                className="p-1 text-muted-foreground hover:text-foreground transition"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto">
              {detailLoading ? (
                <div className="flex items-center justify-center py-10">
                  <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
                </div>
              ) : userDetail ? (
                <div className="p-5 space-y-6">
                  {/* User Info */}
                  <div className="space-y-3">
                    <div>
                      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">
                        Name
                      </p>
                      <p className="font-medium text-foreground">{userDetail.name}</p>
                    </div>
                    <div>
                      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">
                        Email
                      </p>
                      <p className="text-sm text-foreground">{userDetail.email}</p>
                    </div>
                    {userDetail.institution && (
                      <div>
                        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">
                          Institution
                        </p>
                        <p className="text-sm text-foreground">{userDetail.institution}</p>
                      </div>
                    )}
                  </div>

                  {/* Status badges */}
                  <div className="flex items-center gap-2">
                    {userDetail.is_admin && (
                      <span className="inline-flex items-center gap-1 px-2 py-1 bg-primary/10 text-primary rounded text-xs font-medium">
                        <Shield className="w-3 h-3" />
                        Admin
                      </span>
                    )}
                  </div>

                  {/* Dates */}
                  <div className="space-y-3 pt-4 border-t border-border">
                    <div>
                      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1 flex items-center gap-1">
                        <Calendar className="w-3 h-3" />
                        Joined
                      </p>
                      <p className="text-sm text-foreground">
                        {formatDate(userDetail.created_at)}
                      </p>
                    </div>
                    {userDetail.last_login_at ? (
                      <div>
                        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1 flex items-center gap-1">
                          <Calendar className="w-3 h-3" />
                          Last Login
                        </p>
                        <p className="text-sm text-foreground">
                          {formatDateTime(userDetail.last_login_at)}
                        </p>
                      </div>
                    ) : (
                      <div>
                        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">
                          Last Login
                        </p>
                        <p className="text-sm text-muted-foreground">Never</p>
                      </div>
                    )}
                    {userDetail.terms_accepted_at && (
                      <div>
                        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">
                          Terms Accepted
                        </p>
                        <p className="text-sm text-foreground">
                          {formatDate(userDetail.terms_accepted_at)}
                        </p>
                      </div>
                    )}
                  </div>

                  {/* Stats */}
                  <div className="space-y-2 pt-4 border-t border-border">
                    <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                      Statistics
                    </p>
                    <div className="grid grid-cols-2 gap-2">
                      <div className="p-2 bg-muted/30 rounded">
                        <p className="text-xs text-muted-foreground">Projects</p>
                        <p className="text-lg font-bold text-foreground">
                          {userDetail.project_count}
                        </p>
                      </div>
                      <div className="p-2 bg-muted/30 rounded">
                        <p className="text-xs text-muted-foreground">Analyses</p>
                        <p className="text-lg font-bold text-foreground">
                          {userDetail.analysis_count}
                        </p>
                      </div>
                      <div className="p-2 bg-muted/30 rounded">
                        <p className="text-xs text-muted-foreground">Variants</p>
                        <p className="text-lg font-bold text-foreground">
                          {userDetail.variant_count}
                        </p>
                      </div>
                      <div className="p-2 bg-muted/30 rounded">
                        <p className="text-xs text-muted-foreground">Epitopes</p>
                        <p className="text-lg font-bold text-foreground">
                          {userDetail.epitope_count}
                        </p>
                      </div>
                    </div>
                    <div className="p-2 bg-muted/30 rounded">
                      <p className="text-xs text-muted-foreground">Storage</p>
                      <p className="text-lg font-bold text-foreground">
                        {formatBytes(userDetail.total_upload_bytes)}
                      </p>
                    </div>
                  </div>

                  {/* Projects list */}
                  {userDetail.projects.length > 0 && (
                    <div className="space-y-2 pt-4 border-t border-border">
                      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                        Projects ({userDetail.projects.length})
                      </p>
                      <div className="space-y-2">
                        {userDetail.projects.map((proj) => (
                          <div
                            key={proj.id}
                            className="p-2.5 bg-muted/30 rounded border border-border"
                          >
                            <p className="font-medium text-sm text-foreground">
                              {proj.name}
                            </p>
                            <p className="text-xs text-muted-foreground">
                              {proj.cancer_type}
                            </p>
                            <p className="text-xs text-muted-foreground mt-1">
                              {proj.analysis_count} analysis
                              {proj.analysis_count !== 1 ? "es" : ""}
                            </p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : null}
            </div>

            {/* Footer - Delete button */}
            {userDetail && (
              <div className="p-4 border-t border-border bg-muted/10">
                {confirmDeleteId === selectedUserId ? (
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => deleteUser(selectedUserId)}
                      disabled={deleting === selectedUserId}
                      className="flex-1 px-3 py-2 text-sm bg-red-500 text-white rounded hover:bg-red-600 transition disabled:opacity-50 font-medium"
                    >
                      {deleting === selectedUserId ? "Deleting..." : "Confirm Delete"}
                    </button>
                    <button
                      onClick={() => setConfirmDeleteId(null)}
                      className="px-3 py-2 text-sm border border-border text-muted-foreground rounded hover:bg-muted transition"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setConfirmDeleteId(selectedUserId)}
                    className="w-full px-3 py-2 text-sm bg-red-50 text-red-600 rounded hover:bg-red-100 transition font-medium flex items-center justify-center gap-2"
                  >
                    <Trash2 className="w-4 h-4" />
                    Delete User
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================
// PROJECTS TAB
// ============================================================

function ProjectsTab({ token }: { token: string }) {
  const [projects, setProjects] = useState<AdminProject[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [deleting, setDeleting] = useState<number | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);

  const fetchProjects = useCallback(
    async (q?: string) => {
      setLoading(true);
      try {
        const params = new URLSearchParams({ skip: "0", limit: "100" });
        if (q) params.set("search", q);
        const res = await fetch(`/api/py/api/admin/projects?${params}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const data = await res.json();
          setProjects(data.projects);
          setTotal(data.total);
        }
      } catch {
        // silent
      } finally {
        setLoading(false);
      }
    },
    [token]
  );

  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  const handleSearch = () => fetchProjects(search);

  const deleteProject = async (projectId: number) => {
    setDeleting(projectId);
    try {
      const res = await fetch(`/api/py/api/admin/projects/${projectId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok || res.status === 204) {
        setProjects((prev) => prev.filter((p) => p.id !== projectId));
        setTotal((prev) => prev - 1);
      }
    } catch {
      // silent
    } finally {
      setDeleting(null);
      setConfirmDeleteId(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            placeholder="Search by project name or cancer type..."
            className="w-full pl-9 pr-3 py-2 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary bg-white transition"
          />
        </div>
        <button
          onClick={handleSearch}
          className="px-4 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary/90 transition"
        >
          Search
        </button>
      </div>

      <p className="text-xs text-muted-foreground">{total} projects total</p>

      <div className="border border-border rounded-lg bg-white overflow-x-auto">
        {loading ? (
          <div className="flex items-center justify-center py-10">
            <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/20">
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">Project</th>
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">Owner</th>
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">Cancer Type</th>
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">Genome</th>
                <th className="text-right py-2.5 px-4 font-medium text-muted-foreground">Analyses</th>
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">Status</th>
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">Created</th>
                <th className="text-center py-2.5 px-4 font-medium text-muted-foreground">Actions</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((p) => (
                <tr
                  key={p.id}
                  className="border-b border-border last:border-0 hover:bg-muted/10 transition"
                >
                  <td className="py-2.5 px-4">
                    <p className="font-medium text-foreground">{p.name}</p>
                    {p.stage && (
                      <span className="text-xs text-muted-foreground">Stage {p.stage}</span>
                    )}
                  </td>
                  <td className="py-2.5 px-4">
                    <p className="text-foreground">{p.owner_name}</p>
                    <p className="text-xs text-muted-foreground">{p.owner_email}</p>
                  </td>
                  <td className="py-2.5 px-4 text-muted-foreground">{p.cancer_type}</td>
                  <td className="py-2.5 px-4 text-xs text-muted-foreground font-mono">
                    {p.reference_genome}
                  </td>
                  <td className="py-2.5 px-4 text-right tabular-nums">{p.analysis_count}</td>
                  <td className="py-2.5 px-4">
                    <div className="flex flex-wrap gap-1.5">
                      {Object.entries(p.status_breakdown).map(([s, n]) => (
                        <span
                          key={s}
                          className="inline-flex items-center gap-1 text-xs"
                        >
                          <span className={`w-1.5 h-1.5 rounded-full ${STATUS_COLOR[s] || "bg-gray-400"}`} />
                          {n}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="py-2.5 px-4 text-muted-foreground">
                    {formatDate(p.created_at)}
                  </td>
                  <td className="py-2.5 px-4 text-center">
                    {confirmDeleteId === p.id ? (
                      <div className="flex items-center gap-1 justify-center">
                        <button
                          onClick={() => deleteProject(p.id)}
                          disabled={deleting === p.id}
                          className="px-2 py-1 text-xs bg-red-500 text-white rounded hover:bg-red-600 transition disabled:opacity-50 font-medium"
                        >
                          {deleting === p.id ? "..." : "Confirm"}
                        </button>
                        <button
                          onClick={() => setConfirmDeleteId(null)}
                          className="px-2 py-1 text-xs border border-border text-muted-foreground rounded hover:bg-muted transition"
                        >
                          <X className="w-3 h-3" />
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => setConfirmDeleteId(p.id)}
                        className="px-2 py-1 text-xs bg-red-50 text-red-600 rounded hover:bg-red-100 transition font-medium inline-flex items-center gap-1"
                      >
                        <Trash2 className="w-3 h-3" />
                        Delete
                      </button>
                    )}
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

// ============================================================
// FILES TAB
// ============================================================

function FilesTab({ token }: { token: string }) {
  const [files, setFiles] = useState<AdminFile[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [fileTypeFilter, setFileTypeFilter] = useState("");
  const [downloading, setDownloading] = useState<number | null>(null);

  const fetchFiles = useCallback(
    async (ft?: string) => {
      setLoading(true);
      try {
        const params = new URLSearchParams({ skip: "0", limit: "100" });
        if (ft) params.set("file_type", ft);
        const res = await fetch(`/api/py/api/admin/files?${params}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const data = await res.json();
          setFiles(data.files);
          setTotal(data.total);
        }
      } catch {
        // silent
      } finally {
        setLoading(false);
      }
    },
    [token]
  );

  useEffect(() => {
    fetchFiles();
  }, [fetchFiles]);

  const handleFilter = (ft: string) => {
    setFileTypeFilter(ft);
    fetchFiles(ft || undefined);
  };

  const downloadFile = async (fileId: number, fileName: string) => {
    setDownloading(fileId);
    try {
      const res = await fetch(`/api/py/api/admin/files/${fileId}/download`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = fileName;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
      }
    } catch {
      // silent
    } finally {
      setDownloading(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <select
          value={fileTypeFilter}
          onChange={(e) => handleFilter(e.target.value)}
          className="px-3 py-2 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary bg-white transition"
        >
          <option value="">All file types</option>
          <option value="vcf">VCF</option>
          <option value="bam">BAM</option>
          <option value="fastq">FASTQ</option>
          <option value="expression_matrix">Expression Matrix</option>
        </select>
        <p className="text-xs text-muted-foreground">{total} files total</p>
      </div>

      <div className="border border-border rounded-lg bg-white overflow-x-auto">
        {loading ? (
          <div className="flex items-center justify-center py-10">
            <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/20">
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">File</th>
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">Owner</th>
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">Project</th>
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">Type</th>
                <th className="text-right py-2.5 px-4 font-medium text-muted-foreground">Size</th>
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">Analysis</th>
                <th className="text-left py-2.5 px-4 font-medium text-muted-foreground">Uploaded</th>
                <th className="text-center py-2.5 px-4 font-medium text-muted-foreground">Actions</th>
              </tr>
            </thead>
            <tbody>
              {files.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-10 text-center text-muted-foreground">
                    No files found.
                  </td>
                </tr>
              ) : (
                files.map((f) => (
                  <tr
                    key={f.id}
                    className="border-b border-border last:border-0 hover:bg-muted/10 transition"
                  >
                    <td className="py-2.5 px-4">
                      <p className="font-mono text-xs truncate max-w-[200px] text-foreground">
                        {f.file_path}
                      </p>
                      {f.checksum && (
                        <p className="text-[10px] text-muted-foreground font-mono truncate max-w-[200px]">
                          SHA-256: {f.checksum.substring(0, 16)}...
                        </p>
                      )}
                    </td>
                    <td className="py-2.5 px-4 text-xs text-muted-foreground">
                      {f.owner_email}
                    </td>
                    <td className="py-2.5 px-4 text-foreground">{f.project_name}</td>
                    <td className="py-2.5 px-4 uppercase text-xs font-medium text-muted-foreground">
                      {f.file_type}
                    </td>
                    <td className="py-2.5 px-4 text-right tabular-nums text-muted-foreground">
                      {f.file_size ? formatBytes(f.file_size) : "--"}
                    </td>
                    <td className="py-2.5 px-4">
                      <span className="inline-flex items-center gap-1.5 text-xs">
                        <span
                          className={`w-2 h-2 rounded-full ${STATUS_COLOR[f.analysis_status] || "bg-gray-400"}`}
                        />
                        <span className="capitalize">{f.analysis_status}</span>
                      </span>
                    </td>
                    <td className="py-2.5 px-4 text-muted-foreground">
                      {formatDateTime(f.created_at)}
                    </td>
                    <td className="py-2.5 px-4 text-center">
                      <button
                        onClick={() => downloadFile(f.id, f.file_path)}
                        disabled={downloading === f.id}
                        className="px-2 py-1 text-xs bg-blue-50 text-blue-600 rounded hover:bg-blue-100 transition font-medium inline-flex items-center gap-1 disabled:opacity-50"
                      >
                        {downloading === f.id ? (
                          <Loader2 className="w-3 h-3 animate-spin" />
                        ) : (
                          <Download className="w-3 h-3" />
                        )}
                        {downloading === f.id ? "..." : "Download"}
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
