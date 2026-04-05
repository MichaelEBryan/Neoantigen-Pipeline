"use client";

/**
 * Privacy notice banner -- shown on the upload page and results pages.
 * Dismissible per-session (stored in state, reappears on refresh).
 * Links to the full terms page.
 *
 * Two variants:
 *   "upload" -- reminds about data handling before submission
 *   "results" -- reminds about citation requirement when viewing results
 */

import { useState } from "react";
import Link from "next/link";
import { ShieldCheck, X } from "lucide-react";

interface PrivacyBannerProps {
  variant: "upload" | "results";
}

const COPY = {
  upload: {
    text: "Uploaded files are processed on secure servers. Raw sequencing data (FASTQ/BAM) is deleted after analysis. Only variant calls, epitopes, and metadata are retained.",
    link: "View full data handling policy",
  },
  results: {
    text: "If you use these results in a publication, please cite Oxford Cancer Vaccine Design. Copy the citation from the button below or from the Terms page.",
    link: "View citation and terms",
  },
};

export default function PrivacyBanner({ variant }: PrivacyBannerProps) {
  const [dismissed, setDismissed] = useState(false);

  if (dismissed) return null;

  const { text, link } = COPY[variant];

  return (
    <div className="flex items-start gap-3 bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800/50 rounded-lg px-4 py-3 text-sm">
      <ShieldCheck className="w-4 h-4 text-blue-500 mt-0.5 flex-shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-blue-800 dark:text-blue-300">{text}</p>
        <Link
          href="/terms"
          className="text-xs text-blue-600 dark:text-blue-400 hover:underline mt-1 inline-block"
        >
          {link}
        </Link>
      </div>
      <button
        onClick={() => setDismissed(true)}
        className="text-blue-400 hover:text-blue-600 dark:hover:text-blue-300 transition flex-shrink-0"
        aria-label="Dismiss"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}
