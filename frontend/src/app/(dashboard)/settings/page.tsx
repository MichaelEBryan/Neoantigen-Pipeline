"use client";

/**
 * Unified settings page: Profile, Analysis Defaults, Scoring Weights, Display.
 * Loads all preferences from GET /api/settings, saves per-section via PUT.
 */

import { useState, useEffect } from "react";
import { useSession } from "next-auth/react";
import {
  User,
  FlaskConical,
  SlidersHorizontal,
  Monitor,
  Loader2,
  Check,
  RotateCcw,
} from "lucide-react";

// -- Types --

interface Preferences {
  name: string;
  email: string;
  institution: string | null;
  created_at: string;
  default_cancer_type: string | null;
  default_stage: string | null;
  default_genome: string | null;
  default_hla_alleles: string[] | null;
  weight_presentation: number | null;
  weight_binding_rank: number | null;
  weight_expression: number | null;
  weight_vaf: number | null;
  weight_mutation_type: number | null;
  weight_processing: number | null;
  weight_iedb: number | null;
  theme: string | null;
  results_page_size: number | null;
  default_visible_columns: string[] | null;
}

// System defaults from scorer.py
const DEFAULT_WEIGHTS = {
  weight_presentation: 0.30,
  weight_binding_rank: 0.25,
  weight_expression: 0.15,
  weight_vaf: 0.10,
  weight_mutation_type: 0.10,
  weight_processing: 0.05,
  weight_iedb: 0.05,
};

const WEIGHT_LABELS: Record<string, { label: string; description: string }> = {
  weight_presentation: { label: "Presentation", description: "MHCflurry antigen presentation score" },
  weight_binding_rank: { label: "Binding Rank", description: "IC50 binding affinity (lower = better)" },
  weight_expression: { label: "Expression", description: "Gene expression level (TPM)" },
  weight_vaf: { label: "VAF", description: "Variant allele frequency (clonality)" },
  weight_mutation_type: { label: "Mutation Type", description: "Frameshift > nonsense > missense" },
  weight_processing: { label: "Processing", description: "Proteasomal cleavage + TAP transport" },
  weight_iedb: { label: "IEDB", description: "Immunogenicity heuristic" },
};

const CANCER_TYPES = [
  "Melanoma", "Non-Small Cell Lung Cancer (NSCLC)", "Small Cell Lung Cancer (SCLC)",
  "Colorectal Cancer", "Triple-Negative Breast Cancer", "Ovarian Cancer",
  "Pancreatic Adenocarcinoma", "Renal Cell Carcinoma", "Hepatocellular Carcinoma",
  "Gastric Cancer", "Head and Neck Squamous Cell Carcinoma", "Bladder Urothelial Carcinoma",
  "Merkel Cell Carcinoma", "Glioblastoma", "Other",
];

const COMMON_HLA = [
  "HLA-A*01:01", "HLA-A*02:01", "HLA-A*03:01", "HLA-A*11:01", "HLA-A*24:02",
  "HLA-B*07:02", "HLA-B*08:01", "HLA-B*15:01", "HLA-B*35:01", "HLA-B*44:02",
  "HLA-C*03:04", "HLA-C*04:01", "HLA-C*05:01", "HLA-C*06:02", "HLA-C*07:01",
];

type Tab = "profile" | "defaults" | "weights" | "display";

// -- Component --

export default function SettingsPage() {
  const { data: session } = useSession();
  const token = session?.accessToken;

  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null); // section name that was just saved
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("profile");

  // Editable state
  const [name, setName] = useState("");
  const [institution, setInstitution] = useState("");
  const [cancerType, setCancerType] = useState("");
  const [stage, setStage] = useState("");
  const [genome, setGenome] = useState("");
  const [hlaAlleles, setHlaAlleles] = useState<string[]>([]);
  const [weights, setWeights] = useState<Record<string, number>>({ ...DEFAULT_WEIGHTS });
  const [theme, setTheme] = useState("light");
  const [pageSize, setPageSize] = useState(50);

  // Load preferences
  useEffect(() => {
    if (!token) return;
    fetch("/api/py/api/settings/", {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: Preferences | null) => {
        if (data) {
          setPrefs(data);
          setName(data.name || "");
          setInstitution(data.institution || "");
          setCancerType(data.default_cancer_type || "");
          setStage(data.default_stage || "");
          setGenome(data.default_genome || "GRCh38");
          setHlaAlleles(data.default_hla_alleles || []);
          setWeights({
            weight_presentation: data.weight_presentation ?? DEFAULT_WEIGHTS.weight_presentation,
            weight_binding_rank: data.weight_binding_rank ?? DEFAULT_WEIGHTS.weight_binding_rank,
            weight_expression: data.weight_expression ?? DEFAULT_WEIGHTS.weight_expression,
            weight_vaf: data.weight_vaf ?? DEFAULT_WEIGHTS.weight_vaf,
            weight_mutation_type: data.weight_mutation_type ?? DEFAULT_WEIGHTS.weight_mutation_type,
            weight_processing: data.weight_processing ?? DEFAULT_WEIGHTS.weight_processing,
            weight_iedb: data.weight_iedb ?? DEFAULT_WEIGHTS.weight_iedb,
          });
          setTheme(data.theme || "light");
          setPageSize(data.results_page_size || 50);
        }
      })
      .catch(() => setError("Failed to load settings"))
      .finally(() => setLoading(false));
  }, [token]);

  // Save helper
  const save = async (endpoint: string, body: Record<string, unknown>, section: string) => {
    if (!token) return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const res = await fetch(`/api/py/api/settings/${endpoint}`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail || `Save failed`);
      }
      const data: Preferences = await res.json();
      setPrefs(data);
      setSaved(section);
      setTimeout(() => setSaved(null), 2000);

      // Apply theme immediately
      if (section === "display") {
        const resolved = data.theme === "system"
          ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
          : (data.theme || "light");
        document.documentElement.className = resolved;
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const weightSum = Object.values(weights).reduce((a, b) => a + b, 0);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const tabs: { key: Tab; label: string; icon: React.ReactNode }[] = [
    { key: "profile", label: "Profile", icon: <User className="w-4 h-4" /> },
    { key: "defaults", label: "Analysis Defaults", icon: <FlaskConical className="w-4 h-4" /> },
    { key: "weights", label: "Scoring Weights", icon: <SlidersHorizontal className="w-4 h-4" /> },
    { key: "display", label: "Display", icon: <Monitor className="w-4 h-4" /> },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Manage your profile, analysis defaults, scoring model, and display preferences.
        </p>
      </div>

      {error && (
        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-md text-sm text-red-700 dark:text-red-200">
          {error}
        </div>
      )}

      {/* Tab navigation */}
      <div className="flex gap-1 border-b border-border">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition ${
              tab === t.key
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground hover:border-border"
            }`}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      {/* Profile tab */}
      {tab === "profile" && (
        <section className="border border-border rounded-lg p-6 bg-white dark:bg-slate-950 space-y-5 max-w-lg">
          <div>
            <label className="block text-sm font-medium mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 border border-border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Email</label>
            <input
              type="text"
              value={prefs?.email || ""}
              disabled
              className="w-full px-3 py-2 border border-border rounded-md text-sm bg-muted text-muted-foreground"
            />
            <p className="text-xs text-muted-foreground mt-1">Email cannot be changed.</p>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Institution</label>
            <input
              type="text"
              value={institution}
              onChange={(e) => setInstitution(e.target.value)}
              placeholder="e.g. University of Oxford"
              className="w-full px-3 py-2 border border-border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            />
          </div>
          <div className="text-xs text-muted-foreground">
            Member since {prefs?.created_at ? new Date(prefs.created_at).toLocaleDateString() : "..."}
          </div>
          <button
            onClick={() => save("profile", { name, institution }, "profile")}
            disabled={saving}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:opacity-90 disabled:opacity-50 transition"
          >
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : saved === "profile" ? <Check className="w-4 h-4" /> : null}
            {saved === "profile" ? "Saved" : "Save Profile"}
          </button>
        </section>
      )}

      {/* Analysis Defaults tab */}
      {tab === "defaults" && (
        <section className="border border-border rounded-lg p-6 bg-white dark:bg-slate-950 space-y-5 max-w-lg">
          <p className="text-sm text-muted-foreground">
            These values pre-fill the New Analysis form. You can always override per analysis.
          </p>
          <div>
            <label className="block text-sm font-medium mb-1">Default Cancer Type</label>
            <select
              value={cancerType}
              onChange={(e) => setCancerType(e.target.value)}
              className="w-full px-3 py-2 border border-border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            >
              <option value="">None (select each time)</option>
              {CANCER_TYPES.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Default Stage</label>
            <select
              value={stage}
              onChange={(e) => setStage(e.target.value)}
              className="w-full px-3 py-2 border border-border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            >
              <option value="">None</option>
              <option value="I">I</option>
              <option value="II">II</option>
              <option value="III">III</option>
              <option value="IV">IV</option>
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Default Reference Genome</label>
            <div className="flex gap-3">
              {["GRCh38", "GRCh37"].map((g) => (
                <label key={g} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="genome"
                    value={g}
                    checked={genome === g}
                    onChange={() => setGenome(g)}
                    className="text-primary focus:ring-primary"
                  />
                  <span className="text-sm">{g}</span>
                </label>
              ))}
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Default HLA Alleles</label>
            <div className="flex flex-wrap gap-1.5 mb-2">
              {COMMON_HLA.map((hla) => (
                <button
                  key={hla}
                  onClick={() => {
                    setHlaAlleles((prev) =>
                      prev.includes(hla) ? prev.filter((h) => h !== hla) : [...prev, hla]
                    );
                  }}
                  className={`px-2 py-1 text-xs border rounded-md transition ${
                    hlaAlleles.includes(hla)
                      ? "bg-primary/10 border-primary text-primary font-medium"
                      : "border-border hover:bg-muted text-muted-foreground"
                  }`}
                >
                  {hla.replace("HLA-", "")}
                </button>
              ))}
            </div>
            {hlaAlleles.length > 0 && (
              <p className="text-xs text-muted-foreground">
                {hlaAlleles.length} allele{hlaAlleles.length !== 1 ? "s" : ""} selected
              </p>
            )}
          </div>
          <button
            onClick={() =>
              save("analysis-defaults", {
                default_cancer_type: cancerType || null,
                default_stage: stage || null,
                default_genome: genome || null,
                default_hla_alleles: hlaAlleles.length > 0 ? hlaAlleles : null,
              }, "defaults")
            }
            disabled={saving}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:opacity-90 disabled:opacity-50 transition"
          >
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : saved === "defaults" ? <Check className="w-4 h-4" /> : null}
            {saved === "defaults" ? "Saved" : "Save Defaults"}
          </button>
        </section>
      )}

      {/* Scoring Weights tab */}
      {tab === "weights" && (
        <section className="border border-border rounded-lg p-6 bg-white dark:bg-slate-950 space-y-5 max-w-lg">
          <p className="text-sm text-muted-foreground">
            Customize the 7-component immunogenicity scoring weights. These apply to new
            analyses. Weights should sum to 1.0 for proper normalization.
          </p>
          <div className="space-y-4">
            {Object.entries(WEIGHT_LABELS).map(([key, { label, description }]) => (
              <div key={key}>
                <div className="flex items-center justify-between mb-1">
                  <label className="text-sm font-medium">{label}</label>
                  <span className="text-xs font-mono text-muted-foreground">
                    {(weights[key] ?? 0).toFixed(2)}
                  </span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="0.5"
                  step="0.01"
                  value={weights[key] ?? 0}
                  onChange={(e) =>
                    setWeights((prev) => ({ ...prev, [key]: parseFloat(e.target.value) }))
                  }
                  className="w-full h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full appearance-none cursor-pointer accent-primary"
                />
                <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
              </div>
            ))}
          </div>
          <div className={`text-sm font-medium ${
            Math.abs(weightSum - 1.0) < 0.02 ? "text-green-600" : "text-amber-600"
          }`}>
            Sum: {weightSum.toFixed(2)} {Math.abs(weightSum - 1.0) >= 0.02 && "(should be ~1.0)"}
          </div>
          <div className="flex gap-2">
            <button
              onClick={() =>
                save("scoring-weights", weights, "weights")
              }
              disabled={saving}
              className="inline-flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:opacity-90 disabled:opacity-50 transition"
            >
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : saved === "weights" ? <Check className="w-4 h-4" /> : null}
              {saved === "weights" ? "Saved" : "Save Weights"}
            </button>
            <button
              onClick={() => setWeights({ ...DEFAULT_WEIGHTS })}
              className="inline-flex items-center gap-2 px-4 py-2 text-sm border border-border rounded-md hover:bg-muted transition text-muted-foreground"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Reset to Defaults
            </button>
          </div>
        </section>
      )}

      {/* Display tab */}
      {tab === "display" && (
        <section className="border border-border rounded-lg p-6 bg-white dark:bg-slate-950 space-y-5 max-w-lg">
          <div>
            <label className="block text-sm font-medium mb-2">Theme</label>
            <div className="flex gap-2">
              {[
                { value: "light", label: "Light", icon: "sun" },
                { value: "dark", label: "Dark", icon: "moon" },
                { value: "system", label: "System", icon: "monitor" },
              ].map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => setTheme(opt.value)}
                  className={`flex-1 px-4 py-3 border rounded-lg text-sm font-medium transition ${
                    theme === opt.value
                      ? "bg-primary/10 border-primary text-primary"
                      : "border-border hover:bg-muted text-muted-foreground"
                  }`}
                >
                  <div className="text-center">
                    <div className="text-lg mb-1">
                      {opt.icon === "sun" ? "\u2600\uFE0F" : opt.icon === "moon" ? "\uD83C\uDF19" : "\uD83D\uDCBB"}
                    </div>
                    {opt.label}
                  </div>
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Results Page Size</label>
            <select
              value={pageSize}
              onChange={(e) => setPageSize(parseInt(e.target.value))}
              className="w-full px-3 py-2 border border-border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            >
              <option value={25}>25 rows</option>
              <option value={50}>50 rows (default)</option>
              <option value={100}>100 rows</option>
            </select>
          </div>
          <button
            onClick={() =>
              save("display", { theme, results_page_size: pageSize }, "display")
            }
            disabled={saving}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:opacity-90 disabled:opacity-50 transition"
          >
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : saved === "display" ? <Check className="w-4 h-4" /> : null}
            {saved === "display" ? "Saved" : "Save Display Settings"}
          </button>
        </section>
      )}
    </div>
  );
}
