import Link from "next/link";

export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen flex">
      {/* Left branding panel */}
      <div className="hidden lg:flex lg:w-[45%] bg-gradient-to-br from-primary to-primary/80 text-white p-12 flex-col justify-between">
        <div>
          <Link href="/" className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-white/15 flex items-center justify-center">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2v6m0 8v6m-6-10H2m20 0h-4M7.8 7.8 4.6 4.6m14.8 14.8-3.2-3.2M7.8 16.2l-3.2 3.2M19.4 4.6l-3.2 3.2"/>
              </svg>
            </div>
            <span className="font-bold text-lg">Oxford Cancer Vaccine Design</span>
          </Link>
        </div>

        <div className="space-y-6">
          <h2 className="text-3xl font-bold leading-tight text-white">
            Personalised neoantigen prediction for cancer vaccines
          </h2>
          <p className="text-white/80 leading-relaxed">
            Upload somatic variants, get ranked epitope candidates with
            MHC-I binding affinity, cleavage, and immunogenicity scores.
          </p>
          <div className="flex flex-col gap-3 text-sm text-white/70">
            <div className="flex items-center gap-2">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="m9 12 2 2 4-4"/><circle cx="12" cy="12" r="10"/>
              </svg>
              VCF and CSV input supported
            </div>
            <div className="flex items-center gap-2">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="m9 12 2 2 4-4"/><circle cx="12" cy="12" r="10"/>
              </svg>
              MHCflurry + proteasomal cleavage scoring
            </div>
            <div className="flex items-center gap-2">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="m9 12 2 2 4-4"/><circle cx="12" cy="12" r="10"/>
              </svg>
              Secure GCP processing, data deleted after analysis
            </div>
          </div>
        </div>

        <div className="text-xs text-white/50">
          University of Oxford
        </div>
      </div>

      {/* Right form panel */}
      <div className="flex-1 flex items-center justify-center p-6 bg-background">
        <div className="w-full max-w-md">{children}</div>
      </div>
    </div>
  );
}
