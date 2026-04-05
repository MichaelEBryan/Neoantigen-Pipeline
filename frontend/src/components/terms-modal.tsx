"use client";

import { useState } from "react";
import { useSession } from "next-auth/react";

export default function TermsModal() {
  const { data: session, update: updateSession } = useSession();
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Only show if user exists and hasn't accepted terms
  if (!session?.user || session.user.terms_accepted_at) {
    return null;
  }

  const handleAccept = async () => {
    setIsLoading(true);
    setError(null);

    try {
      const res = await fetch("/api/py/api/auth/accept-terms", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session.accessToken}`,
        },
      });

      if (!res.ok) {
        const errorData = await res.json();
        setError(errorData.detail || "Failed to accept terms");
        return;
      }

      // Update session to reflect terms acceptance
      await updateSession();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "An error occurred"
      );
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-slate-950 rounded-lg border border-border p-8 max-w-2xl w-full max-h-96 overflow-y-auto space-y-4">
        <div className="space-y-2">
          <h2 className="text-2xl font-bold">Data Privacy & Ethics Confirmation</h2>
          <p className="text-muted-foreground">
            Please review and accept our data handling practices before continuing.
          </p>
        </div>

        <div className="space-y-4 text-sm">
          <div className="space-y-2">
            <h3 className="font-semibold">What We Store</h3>
            <ul className="space-y-1 text-muted-foreground list-disc list-inside">
              <li>
                Somatic variant calls, predicted epitopes, HLA types, and analysis
                metadata
              </li>
              <li>
                We DO NOT store raw sequencing data (FASTQ/BAM files) after analysis
              </li>
            </ul>
          </div>

          <div className="space-y-2">
            <h3 className="font-semibold">Privacy & Security</h3>
            <ul className="space-y-1 text-muted-foreground list-disc list-inside">
              <li>No patient-identifying information is collected or stored</li>
              <li>All data is encrypted at rest (AES-256) and in transit (TLS 1.3)</li>
            </ul>
          </div>

          <div className="space-y-2">
            <h3 className="font-semibold">Your Responsibility</h3>
            <p className="text-muted-foreground">
              You confirm you have appropriate ethics approval and patient consent
              for the data you analyze.
            </p>
          </div>

          <div className="space-y-2">
            <h3 className="font-semibold">Citation Requirement</h3>
            <p className="text-muted-foreground">
              You agree to cite Oxford Cancer Vaccine Design in publications using results from this platform.
            </p>
          </div>
        </div>

        {error && (
          <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-md text-sm text-red-700 dark:text-red-200">
            {error}
          </div>
        )}

        <button
          onClick={handleAccept}
          disabled={isLoading}
          className="w-full px-4 py-2 bg-primary text-primary-foreground rounded-md hover:opacity-90 font-medium transition disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isLoading ? "Accepting..." : "I Accept"}
        </button>
      </div>
    </div>
  );
}
