"use client";

/**
 * Data & Privacy settings page.
 * GDPR self-service: download my data, delete my account.
 * Links back to the full terms page.
 */

import { useState } from "react";
import { useSession, signOut } from "next-auth/react";
import Link from "next/link";
import {
  Download,
  Trash2,
  Loader2,
  AlertTriangle,
  ShieldCheck,
  ArrowLeft,
} from "lucide-react";

export default function DataSettingsPage() {
  const { data: session } = useSession();
  const token = (session as any)?.accessToken as string | undefined;

  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const [deleteStep, setDeleteStep] = useState<"idle" | "confirm" | "deleting">("idle");
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // -- Export handler: fetch JSON, create blob, trigger download --
  const handleExport = async () => {
    if (!token) return;
    setExporting(true);
    setExportError(null);
    try {
      const res = await fetch("/api/py/api/auth/export-my-data", {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail || `HTTP ${res.status}`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `oxford-cvd-export.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setExportError(e.message || "Export failed");
    } finally {
      setExporting(false);
    }
  };

  // -- Delete handler --
  const handleDelete = async () => {
    if (!token) return;
    setDeleteStep("deleting");
    setDeleteError(null);
    try {
      const res = await fetch("/api/py/api/auth/delete-my-account", {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail || `HTTP ${res.status}`);
      }
      // Account is gone. Sign out and redirect to login.
      await signOut({ redirect: true, callbackUrl: "/login" });
    } catch (e: any) {
      setDeleteError(e.message || "Delete failed");
      setDeleteStep("confirm");
    }
  };

  return (
    <div className="max-w-2xl space-y-8">
      {/* Header */}
      <div>
        <Link
          href="/terms"
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition mb-4"
        >
          <ArrowLeft className="w-3 h-3" /> View full terms & privacy policy
        </Link>
        <h1 className="text-2xl font-bold text-foreground">Data & Privacy</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Manage your data under UK GDPR. These actions apply to your entire account.
        </p>
      </div>

      {/* Retention summary */}
      <div className="flex items-start gap-3 bg-muted/30 border border-border rounded-lg px-4 py-3">
        <ShieldCheck className="w-5 h-5 text-muted-foreground mt-0.5 flex-shrink-0" />
        <div className="text-sm text-muted-foreground space-y-1">
          <p>
            Analysis results are retained for <span className="font-medium text-foreground">12 months</span>.
            Raw sequencing files are deleted within 24 hours of pipeline completion.
          </p>
          <p>No patient-identifying information is stored.</p>
        </div>
      </div>

      {/* Export */}
      <section className="border border-border rounded-lg p-5 bg-white dark:bg-slate-950 space-y-3">
        <div className="flex items-center gap-2">
          <Download className="w-5 h-5 text-foreground" />
          <h2 className="font-semibold text-foreground">Download my data</h2>
        </div>
        <p className="text-sm text-muted-foreground">
          Export all your projects, analyses, variants, epitopes, and account information
          as a single JSON file. This does not delete anything.
        </p>
        {exportError && (
          <p className="text-sm text-red-600 dark:text-red-400">{exportError}</p>
        )}
        <button
          onClick={handleExport}
          disabled={exporting}
          className="inline-flex items-center gap-2 px-4 py-2 text-sm bg-blue-500 text-white rounded-md hover:bg-blue-600 transition disabled:opacity-50"
        >
          {exporting ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" /> Exporting...
            </>
          ) : (
            <>
              <Download className="w-4 h-4" /> Download JSON
            </>
          )}
        </button>
      </section>

      {/* Delete */}
      <section className="border border-red-200 dark:border-red-800/50 rounded-lg p-5 bg-white dark:bg-slate-950 space-y-3">
        <div className="flex items-center gap-2">
          <Trash2 className="w-5 h-5 text-red-500" />
          <h2 className="font-semibold text-red-600 dark:text-red-400">Delete my account</h2>
        </div>
        <p className="text-sm text-muted-foreground">
          Permanently delete your account and all associated data: projects, analyses,
          variants, epitopes, and job logs. This cannot be undone.
        </p>

        {deleteStep === "idle" && (
          <button
            onClick={() => setDeleteStep("confirm")}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 rounded-md hover:bg-red-50 dark:hover:bg-red-900/20 transition"
          >
            <Trash2 className="w-4 h-4" /> Delete my account
          </button>
        )}

        {deleteStep === "confirm" && (
          <div className="space-y-3">
            <div className="flex items-start gap-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/50 rounded-md px-3 py-2.5">
              <AlertTriangle className="w-4 h-4 text-red-500 mt-0.5 flex-shrink-0" />
              <p className="text-sm text-red-700 dark:text-red-300">
                This will permanently delete your account, all projects, and all analysis data.
                There is no undo. Are you sure?
              </p>
            </div>
            {deleteError && (
              <p className="text-sm text-red-600 dark:text-red-400">{deleteError}</p>
            )}
            <div className="flex gap-2">
              <button
                onClick={handleDelete}
                className="px-4 py-2 text-sm bg-red-500 text-white rounded-md hover:bg-red-600 transition"
              >
                Yes, permanently delete everything
              </button>
              <button
                onClick={() => {
                  setDeleteStep("idle");
                  setDeleteError(null);
                }}
                className="px-4 py-2 text-sm border border-border text-muted-foreground rounded-md hover:bg-muted transition"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {deleteStep === "deleting" && (
          <div className="flex items-center gap-2 text-sm text-red-600">
            <Loader2 className="w-4 h-4 animate-spin" /> Deleting account...
          </div>
        )}
      </section>
    </div>
  );
}
