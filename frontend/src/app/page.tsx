import Link from "next/link";
import { getServerSession } from "next-auth";
import { redirect } from "next/navigation";
import { authOptions } from "@/lib/auth";

export default async function LandingPage() {
  const session = await getServerSession(authOptions);
  if (session?.user) {
    redirect("/dashboard");
  }

  return (
    <div className="min-h-screen bg-white">
      {/* Sticky header */}
      <header className="border-b border-border/40 bg-white/90 backdrop-blur-sm sticky top-0 z-20">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2v6m0 8v6m-6-10H2m20 0h-4M7.8 7.8 4.6 4.6m14.8 14.8-3.2-3.2M7.8 16.2l-3.2 3.2M19.4 4.6l-3.2 3.2"/>
              </svg>
            </div>
            <span className="font-bold text-foreground tracking-tight">OCVD</span>
          </div>
          <nav className="hidden md:flex items-center gap-6 text-sm text-muted-foreground">
            <a href="#features" className="hover:text-foreground transition">Features</a>
            <a href="#pipeline" className="hover:text-foreground transition">Pipeline</a>
            <a href="#about" className="hover:text-foreground transition">About</a>
          </nav>
          <div className="flex items-center gap-3">
            <Link
              href="/login"
              className="px-4 py-2 text-sm font-medium text-foreground hover:text-primary transition"
            >
              Sign In
            </Link>
            <Link
              href="/register"
              className="px-4 py-2 text-sm font-medium bg-primary text-white rounded-lg hover:bg-primary/90 transition"
            >
              Get Started
            </Link>
          </div>
        </div>
      </header>

      {/* Hero */}
      <section className="bg-primary text-white relative overflow-hidden">
        <div className="max-w-6xl mx-auto px-6 py-20 md:py-28 grid md:grid-cols-2 gap-12 items-center relative z-10">
          {/* Left: copy */}
          <div>
            <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-white/10 text-white/80 text-xs font-medium mb-6 border border-white/15">
              Oxford Centre for Immuno-Oncology
            </div>
            <h1 className="text-4xl sm:text-5xl font-bold text-white leading-tight">
              Personalised neoantigen prediction for cancer vaccines
            </h1>
            <p className="mt-5 text-lg text-white/70 leading-relaxed max-w-lg">
              From somatic mutations to ranked vaccine targets in minutes. Upload VCF, MAF, or CSV. Get MHC binding predictions, cleavage scores, and immunogenicity rankings.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-4">
              <Link
                href="/register"
                className="px-6 py-3 bg-white text-primary rounded-lg hover:bg-white/90 font-semibold transition shadow-sm"
              >
                Start Free Analysis
              </Link>
              <Link
                href="/login"
                className="px-6 py-3 border border-white/30 rounded-lg hover:bg-white/10 font-medium transition text-white"
              >
                Sign In
              </Link>
            </div>
            <div className="mt-8 flex items-center gap-6 text-xs text-white/50">
              <div className="flex items-center gap-1.5">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/></svg>
                GCP Secured
              </div>
              <div className="flex items-center gap-1.5">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/></svg>
                GDPR Compliant
              </div>
              <div className="flex items-center gap-1.5">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M22 10v6M2 10l10-5 10 5-10 5z"/><path d="M6 12v5c3 3 8 3 12 0v-5"/></svg>
                University of Oxford
              </div>
            </div>
          </div>

          {/* Right: abstract molecular SVG */}
          <div className="hidden md:flex items-center justify-center">
            <svg width="380" height="380" viewBox="0 0 400 400" className="opacity-50">
              {/* Double helix abstraction */}
              <defs>
                <linearGradient id="helixGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stopColor="white" stopOpacity="0.3"/>
                  <stop offset="100%" stopColor="white" stopOpacity="0.05"/>
                </linearGradient>
              </defs>
              {/* Strand paths */}
              <path d="M120,40 C180,80 220,120 180,160 C140,200 180,240 220,280 C260,320 220,360 180,380"
                    fill="none" stroke="url(#helixGrad)" strokeWidth="2"/>
              <path d="M280,40 C220,80 180,120 220,160 C260,200 220,240 180,280 C140,320 180,360 220,380"
                    fill="none" stroke="url(#helixGrad)" strokeWidth="2"/>
              {/* Rungs */}
              {[80, 120, 160, 200, 240, 280, 320].map((y, i) => (
                <line key={i}
                  x1={150 + Math.sin(y * 0.03) * 30}
                  y1={y}
                  x2={250 - Math.sin(y * 0.03) * 30}
                  y2={y}
                  stroke="white" strokeOpacity={0.15} strokeWidth="1"
                />
              ))}
              {/* Nodes */}
              {[
                [150, 80], [250, 80],
                [170, 120], [230, 120],
                [180, 160], [220, 160],
                [170, 200], [230, 200],
                [180, 240], [220, 240],
                [160, 280], [240, 280],
                [170, 320], [230, 320],
              ].map(([x, y], i) => (
                <circle key={i} cx={x} cy={y} r={3} fill="white" opacity={0.3 + (i % 3) * 0.1}/>
              ))}
              {/* Floating particles */}
              {[
                [90, 100, 2], [310, 150, 1.5], [70, 250, 1.5], [330, 300, 2],
                [100, 350, 1], [300, 60, 1], [60, 180, 1.5], [340, 220, 1],
              ].map(([x, y, r], i) => (
                <circle key={`p${i}`} cx={x} cy={y} r={r} fill="white" opacity={0.15}/>
              ))}
            </svg>
          </div>
        </div>
      </section>

      {/* How it works */}
      <section id="pipeline" className="py-20 bg-slate-50/50">
        <div className="max-w-6xl mx-auto px-6">
          <h2 className="text-2xl font-bold text-foreground text-center mb-12">How it works</h2>
          <div className="grid sm:grid-cols-4 gap-6">
            <StepCard step={1} title="Upload Mutations"
              description="VCF (VEP, SnpEff, DRAGEN), TCGA MAF, or pre-processed CSV with protein changes."
            />
            <StepCard step={2} title="Parse & Filter"
              description="Extract coding somatic variants. Filter by consequence, VAF, read depth."
            />
            <StepCard step={3} title="Predict Binding"
              description="MHCflurry predicts binding affinity, presentation score, and cleavage probability per HLA allele."
            />
            <StepCard step={4} title="Rank Targets"
              description="Multi-factor scoring combining binding, expression, VAF, and sequence properties."
            />
          </div>
        </div>
      </section>

      {/* Features */}
      <section id="features" className="py-20">
        <div className="max-w-6xl mx-auto px-6">
          <h2 className="text-2xl font-bold text-foreground text-center mb-4">Built for real clinical data</h2>
          <p className="text-muted-foreground text-center mb-12 max-w-2xl mx-auto">
            Handles output from all major annotation pipelines. Robust parsing, informative error messages, and transparent scoring.
          </p>
          <div className="grid sm:grid-cols-3 gap-6">
            <FeatureCard
              title="Multi-format Input"
              description="VCF with VEP, SnpEff, Funcotator, DRAGEN, or Nirvana annotations. TCGA MAF format. Pre-processed CSV. Optional RNA expression matrices."
              icon={<><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/></>}
            />
            <FeatureCard
              title="Clinical-grade Prediction"
              description="MHCflurry 2.0 binding, presentation, and processing scores. Validated against published benchmarks. Supports 150+ HLA-A/B/C alleles."
              icon={<><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/></>}
            />
            <FeatureCard
              title="Transparent Scoring"
              description="Each epitope score is decomposable: binding weight, expression weight, VAF contribution, and sequence features. No black boxes."
              icon={<><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></>}
            />
          </div>
        </div>
      </section>

      {/* Stats banner */}
      <section className="bg-primary text-white py-12">
        <div className="max-w-6xl mx-auto px-6 grid grid-cols-2 md:grid-cols-4 gap-8 text-center">
          <StatBlock number="29" label="Consequence terms supported" />
          <StatBlock number="6" label="Annotation tools handled" />
          <StatBlock number="150+" label="HLA alleles supported" />
          <StatBlock number="5" label="Scoring factors combined" />
        </div>
      </section>

      {/* CTA */}
      <section id="about" className="py-20 text-center">
        <div className="max-w-2xl mx-auto px-6">
          <h2 className="text-2xl font-bold text-foreground mb-4">Ready to design your vaccine?</h2>
          <p className="text-muted-foreground mb-8">
            Create an account and run your first analysis in under 2 minutes.
          </p>
          <Link
            href="/register"
            className="inline-block px-8 py-3 bg-primary text-white rounded-lg hover:bg-primary/90 font-semibold transition shadow-sm"
          >
            Get Started
          </Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-border/40 bg-white">
        <div className="max-w-6xl mx-auto px-6 py-8 flex flex-col sm:flex-row items-center justify-between gap-4 text-xs text-muted-foreground">
          <div className="flex items-center gap-3">
            <div className="w-6 h-6 rounded bg-primary flex items-center justify-center">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2v6m0 8v6m-6-10H2m20 0h-4M7.8 7.8 4.6 4.6m14.8 14.8-3.2-3.2M7.8 16.2l-3.2 3.2M19.4 4.6l-3.2 3.2"/>
              </svg>
            </div>
            <span>University of Oxford, Centre for Immuno-Oncology</span>
          </div>
          <div className="flex items-center gap-6">
            <Link href="/terms" className="hover:text-foreground transition">Terms</Link>
            <Link href="/terms" className="hover:text-foreground transition">Privacy</Link>
            <span>michael.bryan@new.ox.ac.uk</span>
          </div>
        </div>
      </footer>
    </div>
  );
}


function StepCard({
  step,
  title,
  description,
}: {
  step: number;
  title: string;
  description: string;
}) {
  return (
    <div className="relative p-5 rounded-xl border border-border bg-white">
      <div className="flex items-center gap-3 mb-3">
        <span className="w-7 h-7 rounded-full bg-primary text-white text-xs font-bold flex items-center justify-center">
          {step}
        </span>
        <h3 className="font-semibold text-foreground text-sm">{title}</h3>
      </div>
      <p className="text-sm text-muted-foreground leading-relaxed">{description}</p>
    </div>
  );
}


function FeatureCard({
  icon,
  title,
  description,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div className="p-6 rounded-xl border border-border bg-white shadow-sm hover:shadow-md transition-shadow">
      <div className="w-10 h-10 rounded-lg bg-primary/8 text-primary flex items-center justify-center mb-4">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          {icon}
        </svg>
      </div>
      <h3 className="font-semibold text-foreground mb-2">{title}</h3>
      <p className="text-sm text-muted-foreground leading-relaxed">{description}</p>
    </div>
  );
}


function StatBlock({ number, label }: { number: string; label: string }) {
  return (
    <div>
      <div className="text-3xl font-bold text-white">{number}</div>
      <div className="mt-1 text-sm text-white/60">{label}</div>
    </div>
  );
}
