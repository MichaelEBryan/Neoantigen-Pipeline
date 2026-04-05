# OCVD Progress - Session 2026-04-04

## What was done

### 1. DAI (Differential Agretopicity Index)
- **Backend**: `scorer.py` extended with `derive_wt_peptide()` and `compute_dai()` functions. For each missense neoepitope, derives the wildtype peptide (swaps alt_aa back to ref_aa at mutation position), runs MHCflurry on the WT peptide, computes `DAI = log2(WT_IC50 / mutant_IC50)`. Positive DAI = mutant binds MHC better = good vaccine candidate.
- **Pipeline integration**: `orchestrator.py` now calls `compute_dai()` after rank_and_select, before DB storage. Non-fatal if it fails.
- **DB schema**: Added `dai_score` (Float, nullable) and `wt_binding_affinity_nm` (Float, nullable) to Epitope model. Migration `004_add_dai_columns_to_epitopes.py`.
- **On-demand endpoint**: `POST /api/dai/compute/{analysis_id}` -- computes DAI for existing analyses retroactively. Registered in `main.py`.
- **API responses**: `epitopes.py` EpitopeResponse and EpitopeDetailResponse include `dai_score` and `wt_binding_affinity_nm`. CSV/TSV export includes both columns.
- **Frontend**: DAI column in results table with color coding (green for positive, red for negative). Tooltip shows WT and mutant IC50. "Compute DAI" button appears for analyses without DAI data.
- **Scorer change**: `explanation_json` now includes `mutation_position_in_peptide`, `ref_aa`, `alt_aa` for future WT derivation.

### 2. RNA Expression-Aware Heatmap
- **Frontend only** (client-side parsing, no backend needed).
- `construct/page.tsx`: Added RSEM expression matrix upload (file input, accepts .txt/.tsv/.csv).
- `parseRSEMExpression()`: Parses tab-delimited file with Hugo_Symbol as first column, detects sample columns automatically, averages TPM across all samples.
- Expression heatmap track rendered between immunogenicity and cleavage tracks. Color scale: gray (not expressed, <1 TPM) -> light blue -> blue -> deep purple (>100 TPM).
- Expression data shown in position detail panel and legend.
- File upload has green border when loaded, X button to remove.

### 3. Multi-Patient Comparison View
- **Backend**: `POST /api/compare/analyses` endpoint. Takes list of analysis_ids, min_score, max_ic50. Returns gene x analysis heatmap, shared peptides (same sequence + HLA in >1 analysis), shared mutations (same gene + protein_change in >1 analysis).
- **Frontend**: New page `/compare` with analysis picker (shows completed analyses), heatmap table (rows=genes, columns=analyses, cells=best score + epitope count), shared mutations table, shared peptides table. Genes present in multiple analyses are highlighted.
- Nav link added to sidebar ("Compare" with BarChart3 icon).

### 4. PDF Report Export
- **Backend**: `GET /api/report/{analysis_id}/pdf` generates clinical-style PDF using reportlab.
- Content: OCVD header, analysis metadata (project, cancer type, genome, dates), HLA typing, score distribution table (High/Medium/Low counts), ranked epitope table (top 50 with rank, peptide, gene, mutation, HLA, IC50, score, DAI, tier), disclaimer.
- Styled with Oxford navy (#004875) theme, alternating row colors, monospace for peptide/HLA columns, color-coded tier column.
- `reportlab==4.4.10` added to requirements.txt.
- **Frontend**: "PDF Report" button added to results page header, fetches blob and triggers download.

### 5. Variant Annotation Enrichment
- **Backend**: `POST /api/annotate/variants/{analysis_id}` queries:
  - **ClinVar** (via NCBI E-utilities): pathogenicity significance, ClinVar ID. Verifies ref/alt alleles in entry title to reduce false positives.
  - **gnomAD** (v4 GraphQL API): population allele frequency. Flags variants with AF > 0.01 as likely germline.
  - **COSMIC**: Full Cancer Gene Census (748 genes, Tier 1 + Tier 2) loaded from `backend/app/data/cosmic_cgc.csv`. Each gene has tier, role_in_cancer (oncogene/TSG/fusion), somatic/germline flags.
- ClinVar + gnomAD queries run concurrently per variant (asyncio.gather), batched in groups of 5. NCBI semaphore (capacity 2) enforces rate limits.
- Results cached in `variant.annotation_json` to avoid repeated lookups.
- **Frontend**: "Annotate" button on results page. Gene column shows role-aware badges: "Oncogene" / "TSG" / "Onco/TSG" / "Driver" (purple for Tier 1, lighter for Tier 2), "Germline?" (red, gnomAD AF > 1%), "CV" (blue, ClinVar match). Tooltip shows full COSMIC tier and role. Warning banner shows germline/driver counts.

## Files modified

### Backend
- `backend/app/models.py` - Added dai_score, wt_binding_affinity_nm to Epitope
- `backend/app/pipeline/scorer.py` - Added derive_wt_peptide(), compute_dai(), mutation tracking in explanation. Imports fixed (re, _to_single_letter at module level).
- `backend/app/pipeline/orchestrator.py` - DAI computation step, DAI fields in DB write
- `backend/app/routers/epitopes.py` - DAI in API responses and CSV export
- `backend/app/routers/dai.py` - NEW: on-demand DAI endpoint
- `backend/app/routers/compare.py` - NEW: multi-patient comparison (with proper SharedMutation Pydantic model)
- `backend/app/routers/report.py` - NEW: PDF report generation (variant count query uses select_from)
- `backend/app/routers/annotate.py` - NEW: variant annotation enrichment (concurrent queries, ClinVar allele verification, full CGC from CSV)
- `backend/app/main.py` - Registered dai, compare, report, annotate routers
- `backend/app/data/cosmic_cgc.csv` - NEW: full COSMIC Cancer Gene Census (748 genes)
- `backend/requirements.txt` - Added reportlab
- `backend/alembic/versions/004_add_dai_columns_to_epitopes.py` - NEW: migration

### Frontend
- `frontend/src/app/(dashboard)/analysis/[id]/results/page.tsx` - DAI column, Compute DAI button (uses hasDaiData state), Annotate button, PDF Report button, role-aware annotation badges
- `frontend/src/app/(dashboard)/analysis/[id]/construct/page.tsx` - Expression upload, expression heatmap track, expression in legend and detail panel
- `frontend/src/app/(dashboard)/compare/page.tsx` - NEW: multi-patient comparison page
- `frontend/src/app/(dashboard)/layout.tsx` - Compare nav link

## Critical Review Fixes (Session 2)

### Backend fixes

1. **scorer.py -- inline imports moved to module level**
   - `import re` and `from .peptide_gen import _to_single_letter` were inside the scoring loop (called per-prediction). Moved to top-level imports. Performance bug on large variant sets.

2. **dai.py -- removed unused `update` import**

3. **report.py -- removed unused `PageBreak` import, fixed variant count query**
   - Added `.select_from(Variant)` to avoid ambiguous FROM clause.

4. **annotate.py -- ClinVar allele verification + concurrent queries + full CGC**
   - ClinVar now verifies ref/alt alleles in entry title (previously position-only).
   - ClinVar + gnomAD run concurrently via asyncio.gather, batched in groups of 5.
   - Hardcoded 60-gene driver set replaced with full 748-gene CGC loaded from CSV.
   - VariantAnnotation model now exposes cosmic_tier and cosmic_role.

5. **compare.py -- proper Pydantic model for shared mutations**
   - `shared_mutations: list[dict]` replaced with `list[SharedMutation]`.

### Frontend fixes

6. **results/page.tsx -- "Compute DAI" button logic**
   - Now uses `hasDaiData` state set once on first data load, not per-page check.

7. **results/page.tsx -- annotation badges show role, not just "Driver"**
   - Shows "Oncogene", "TSG", "Onco/TSG" with Tier 1 vs Tier 2 distinction.

### Verification
- All Python files pass `ast.parse()` (no syntax errors)
- Frontend compiles cleanly with `npx tsc --noEmit` (no type errors)
- COSMIC CSV loads correctly (748 genes, path resolves via `Path(__file__).parent.parent / "data"`)

## Deploy command
```bash
cd ~/Downloads/CVDash
git add -A && git commit -m "feat: 5 features + critical review fixes + full COSMIC CGC"
git push origin main
gcloud compute ssh cvdash-v01 --zone=europe-west2-c -- \
  "cd /opt/cvdash && git stash && git pull && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build backend frontend"
```

## Post-deploy checklist
1. SSH into VM, run the Alembic migration: `docker compose exec backend alembic upgrade head`
2. Verify the COSMIC CSV is present inside the container: `docker compose exec backend ls /app/data/cosmic_cgc.csv`
3. Test annotation endpoint: hit "Annotate" on a completed analysis, check badges render
4. Test DAI: hit "Compute DAI" on an analysis, verify DAI column populates
5. Test PDF: click "PDF Report", verify download with correct content
6. Test Compare: select 2+ analyses, run comparison

## Session 3 - Settings, Preferences, Bug Fixes

### 6. User Preferences & Settings Page
- **Backend**: `UserPreferences` model in models.py with columns for analysis defaults (cancer_type, stage, genome, HLA alleles), scoring weights (7 floats), and display preferences (theme, page_size, visible_columns). Migration `005_add_user_preferences.py`.
- **Settings API**: `backend/app/routers/settings.py` with 5 endpoints:
  - `GET /api/settings/` - load full preferences
  - `PUT /api/settings/profile` - update name/institution
  - `PUT /api/settings/analysis-defaults` - cancer type, stage, genome, HLA
  - `PUT /api/settings/scoring-weights` - 7-component weights
  - `PUT /api/settings/display` - theme, page size
- Validators: stage (I-IV), genome (GRCh38/37), theme (light/dark/system).
- **Frontend**: `settings/page.tsx` - 4-tab settings page (Profile, Analysis Defaults, Scoring Weights, Display). Weight sliders with sum indicator. Theme selector with immediate application.
- **New analysis form**: Loads defaults from settings API on mount, pre-fills form fields.
- **Theme provider**: `theme-provider.tsx` reads theme from settings API, applies to html className. Handles system preference.

### 7. Scoring Weights Wired into Pipeline
- `scorer.py` `score_epitopes()` now accepts optional `custom_weights` dict. Merges with defaults, auto-normalizes if sum != 1.0.
- `orchestrator.py` `run_pipeline()` accepts `custom_weights` param.
- `celery_app.py` loads `UserPreferences` for the analysis owner, extracts weight_* columns, passes to run_pipeline. Both VCF and remote analysis tasks updated.

### 8. Frontend Bug Fixes
- **Stale closure in construct/page.tsx**: `dragOrder` was captured in `buildConstruct` closure but mutated by `handleDragOver`. Fixed by using `dragOrderRef` (useRef) read inside the callback, synced via useEffect.
- **Unsafe `params.id as string`**: Changed to `String(params.id ?? "")` in both results and construct pages.
- **Missing error catch in results fetch**: `res.json()` on error response could throw; added `.catch()` fallback.
- **TypeScript session type**: Created `src/types/next-auth.d.ts` to properly augment `Session` with `accessToken`. Removed `as Record<string, unknown>` casts in settings, theme-provider, and new analysis pages.

### Files added/modified this session
- `backend/app/models.py` - UserPreferences model, User.preferences relationship
- `backend/app/routers/settings.py` - NEW: settings API
- `backend/app/main.py` - Registered settings router
- `backend/app/pipeline/scorer.py` - custom_weights param, local `w` dict, auto-normalization
- `backend/app/pipeline/orchestrator.py` - custom_weights param passthrough
- `backend/app/celery_app.py` - Load UserPreferences, pass custom_weights to run_pipeline
- `backend/alembic/versions/005_add_user_preferences.py` - NEW: migration
- `frontend/src/types/next-auth.d.ts` - NEW: Session type augmentation
- `frontend/src/app/(dashboard)/settings/page.tsx` - NEW: settings page
- `frontend/src/app/(dashboard)/analysis/new/page.tsx` - Load defaults on mount
- `frontend/src/app/(dashboard)/analysis/[id]/results/page.tsx` - Safe params.id, error handling fix
- `frontend/src/app/(dashboard)/analysis/[id]/construct/page.tsx` - Safe params.id, dragOrder ref fix
- `frontend/src/components/theme-provider.tsx` - NEW: theme from settings API
- `frontend/src/components/providers.tsx` - ThemeProvider added
- `frontend/src/app/layout.tsx` - Removed hardcoded class="light"
- `frontend/src/app/(dashboard)/layout.tsx` - Settings nav reorganized

### Verification
- All Python files pass `py_compile` (zero errors)
- `npx tsc --noEmit` passes with zero errors
- Scoring weights: normalized if sum deviates from 1.0

## Assumptions
- MHCflurry is available in the Docker container (confirmed from prior session)
- reportlab will be installed via requirements.txt during Docker build
- gnomAD GraphQL API is publicly accessible (no auth needed)
- NCBI E-utilities don't need API key at current request rates (~2/sec)
- COSMIC CGC CSV is from COSMIC v99 (the uploaded file). If a newer version is needed, replace `backend/app/data/cosmic_cgc.csv` and rebuild.
- Expression RSEM format uses Hugo_Symbol as first column, tab-delimited
- DAI for frameshifts is set to None (entire downstream is novel, no meaningful WT comparison)
- The `_to_single_letter` function in peptide_gen.py handles 3-letter to 1-letter AA conversion (existing code)
