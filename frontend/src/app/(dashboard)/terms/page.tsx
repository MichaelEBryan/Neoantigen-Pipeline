import CitationBox from "@/components/citation-box";

/**
 * Terms of use, privacy policy, data retention, and citation info.
 * Static page -- no client-side data fetching needed.
 */

export default function TermsPage() {
  return (
    <div className="max-w-3xl space-y-10">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Terms of Use & Privacy</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Last updated: 1 April 2026
        </p>
      </div>

      {/* 1. Terms of Use */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-foreground">1. Terms of Use</h2>
        <div className="text-sm text-muted-foreground space-y-2 leading-relaxed">
          <p>
            Oxford Cancer Vaccine Design ("the Platform") is a research tool provided by the
            University of Oxford, Centre for Immuno-Oncology. It is intended for use by qualified
            researchers with appropriate institutional ethics approval.
          </p>
          <p>
            By using the Platform you agree to these terms. If you do not agree, do not use the
            Platform. We reserve the right to update these terms; continued use after changes
            constitutes acceptance.
          </p>
          <p>
            The Platform generates computational predictions only. Results must not be used directly
            for clinical decisions without independent validation. The University of Oxford accepts
            no liability for clinical outcomes derived from Platform output.
          </p>
        </div>
      </section>

      {/* 2. Data Handling */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-foreground">2. Data Handling</h2>
        <div className="text-sm text-muted-foreground space-y-2 leading-relaxed">
          <p>
            <span className="font-medium text-foreground">What we store:</span> Somatic variant
            calls (VCF-derived), predicted epitopes, HLA allele assignments, analysis metadata
            (cancer type, stage, timestamps), and your account details (name, email, institution).
          </p>
          <p>
            <span className="font-medium text-foreground">What we do not store:</span> Raw
            sequencing data (FASTQ, BAM). These files are processed in memory or on Isambard-AI
            and deleted immediately after the pipeline completes. We retain only the derived
            variant calls.
          </p>
          <p>
            <span className="font-medium text-foreground">Encryption:</span> Data at rest is
            encrypted with AES-256. All traffic uses TLS 1.3. Database backups are encrypted and
            stored in a separate security zone.
          </p>
          <p>
            <span className="font-medium text-foreground">No patient identifiers:</span> The
            Platform does not collect or store patient names, NHS numbers, dates of birth, or
            any other directly identifying information. You must ensure uploaded data has been
            appropriately anonymised.
          </p>
        </div>
      </section>

      {/* 3. Data Retention */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-foreground">3. Data Retention</h2>
        <div className="text-sm text-muted-foreground space-y-2 leading-relaxed">
          <p>
            Analysis results are retained for <span className="font-medium text-foreground">12 months</span> from
            the date of creation. After this period, results are automatically purged unless you
            explicitly request an extension.
          </p>
          <p>
            Uploaded FASTQ/BAM files are deleted within 24 hours of pipeline completion. VCF files
            derived from these are retained with the analysis results.
          </p>
          <p>
            Account data is retained for as long as your account is active. Inactive accounts
            (no login for 24 months) may be flagged for removal with 30 days notice.
          </p>
        </div>
      </section>

      {/* 4. Your Rights (GDPR) */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-foreground">4. Your Rights</h2>
        <div className="text-sm text-muted-foreground space-y-2 leading-relaxed">
          <p>
            Under UK GDPR, you have the right to access, correct, and delete your personal data.
            The Platform provides self-service tools for this:
          </p>
          <p>
            <span className="font-medium text-foreground">Download my data</span> exports all your
            projects, analyses, variants, epitopes, and account information as a JSON archive.
          </p>
          <p>
            <span className="font-medium text-foreground">Delete my account</span> permanently
            removes your account and all associated data. This action is irreversible.
          </p>
          <p>
            These controls are available in{" "}
            <a href="/settings/data" className="text-primary hover:underline">
              Settings &rarr; Data & Privacy
            </a>.
          </p>
        </div>
      </section>

      {/* 5. Your Responsibilities */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-foreground">5. Your Responsibilities</h2>
        <div className="text-sm text-muted-foreground space-y-2 leading-relaxed">
          <p>
            You confirm that you have obtained appropriate ethics committee approval and (where
            applicable) informed patient consent for any data you upload. You accept responsibility
            for ensuring data has been adequately anonymised before upload.
          </p>
          <p>
            You must not attempt to re-identify individuals from variant data or epitope predictions.
          </p>
        </div>
      </section>

      {/* 6. Citation */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-foreground">6. Citation</h2>
        <p className="text-sm text-muted-foreground leading-relaxed">
          If you use results from the Platform in a publication, presentation, or grant application,
          please cite us using one of the formats below.
        </p>
        <CitationBox />
      </section>

      {/* 7. Contact */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-foreground">7. Contact</h2>
        <div className="text-sm text-muted-foreground space-y-2 leading-relaxed">
          <p>
            For questions about data handling, privacy, or these terms, contact the
            Centre for Immuno-Oncology data team at{" "}
            <a
              href="mailto:vaccine-support@oncology.ox.ac.uk"
              className="text-primary hover:underline"
            >
              vaccine-support@oncology.ox.ac.uk
            </a>.
          </p>
          <p>
            The data controller is the University of Oxford. The University's Data Protection
            Officer can be contacted at{" "}
            <a
              href="mailto:dpo@admin.ox.ac.uk"
              className="text-primary hover:underline"
            >
              dpo@admin.ox.ac.uk
            </a>.
          </p>
        </div>
      </section>
    </div>
  );
}
