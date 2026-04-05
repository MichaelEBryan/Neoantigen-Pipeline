"use client";

/**
 * Citation box -- shows the recommended citation in plain text and BibTeX.
 * Copy button for each format. Used on the terms page and optionally
 * on results pages.
 */

import { useState } from "react";
import { Copy, Check } from "lucide-react";

const PLAIN_CITATION = `Oxford Cancer Vaccine Design: A platform for personalised neoantigen prediction and cancer vaccine design. University of Oxford, Centre for Immuno-Oncology. https://vaccine.ox.ac.uk (2026).`;

const BIBTEX_CITATION = `@misc{oxfordcvd2026,
  title  = {Oxford Cancer Vaccine Design},
  author = {University of Oxford, Centre for Immuno-Oncology},
  year   = {2026},
  url    = {https://vaccine.ox.ac.uk},
  note   = {Personalised neoantigen prediction platform}
}`;

type Format = "plain" | "bibtex";

export default function CitationBox() {
  const [copied, setCopied] = useState<Format | null>(null);

  const handleCopy = async (format: Format) => {
    const text = format === "plain" ? PLAIN_CITATION : BIBTEX_CITATION;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(format);
      setTimeout(() => setCopied(null), 2000);
    } catch {
      // Clipboard API might not be available
    }
  };

  return (
    <div className="space-y-4">
      {/* Plain text */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
            Plain text
          </span>
          <button
            onClick={() => handleCopy("plain")}
            className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
          >
            {copied === "plain" ? (
              <>
                <Check className="w-3 h-3" /> Copied
              </>
            ) : (
              <>
                <Copy className="w-3 h-3" /> Copy
              </>
            )}
          </button>
        </div>
        <div className="bg-muted/30 border border-border rounded-md px-3 py-2.5 text-sm text-foreground font-mono leading-relaxed">
          {PLAIN_CITATION}
        </div>
      </div>

      {/* BibTeX */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
            BibTeX
          </span>
          <button
            onClick={() => handleCopy("bibtex")}
            className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
          >
            {copied === "bibtex" ? (
              <>
                <Check className="w-3 h-3" /> Copied
              </>
            ) : (
              <>
                <Copy className="w-3 h-3" /> Copy
              </>
            )}
          </button>
        </div>
        <pre className="bg-muted/30 border border-border rounded-md px-3 py-2.5 text-sm text-foreground font-mono leading-relaxed overflow-x-auto whitespace-pre">
          {BIBTEX_CITATION}
        </pre>
      </div>
    </div>
  );
}
