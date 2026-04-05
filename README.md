# Oxford Cancer Vaccine Design (OCVD)

Personalised neoantigen prediction platform for cancer vaccine design. Takes somatic mutation data (VCF, MAF, or CSV) and HLA typing as input, runs MHC binding prediction, scores candidate neoantigens, and returns a ranked list of vaccine targets.

Built at the University of Oxford, Centre for Immuno-Oncology.

## Architecture

```
frontend/          Next.js 14 (App Router, NextAuth, Tailwind)
backend/           FastAPI + Celery + PostgreSQL + Redis
  app/
    pipeline/      Core bioinformatics pipeline
      vcf_parser   VCF parsing (VEP, SnpEff, Funcotator, DRAGEN, pyensembl)
      maf_parser   TCGA MAF format parsing
      expression_parser   RNA expression matrix handling
      peptide_gen  Neoantigen peptide generation from variants
      mhc_predict  MHC-I/II binding prediction (NetMHCpan)
      scorer       Multi-factor neoantigen scoring and ranking
      orchestrator Task orchestration and pipeline control
    routers/       API endpoints (auth, uploads, analyses, admin, etc.)
    models.py      SQLAlchemy ORM models
    celery_app.py  Async task worker
infra/             GCP deployment (Docker Compose, nginx, Terraform)
```

## Pipeline

1. **Parse mutations** from VCF (with VEP/SnpEff/DRAGEN annotations), TCGA MAF, or pre-processed CSV.
2. **Filter** to coding somatic variants (missense, nonsense, frameshift, inframe indels). Configurable VAF threshold.
3. **Generate candidate peptides** (8-11mer for MHC-I, 15mer for MHC-II) spanning each mutation.
4. **Predict MHC binding** using NetMHCpan-4.1 / NetMHCIIpan-4.0 against patient HLA alleles.
5. **Score and rank** candidates using binding affinity, expression level, variant allele frequency, and sequence properties.
6. **Return results** as ranked epitope tables with per-allele binding data.

## Supported input formats

- **VCF** (.vcf, .vcf.gz) with annotation from VEP, SnpEff, Funcotator, DRAGEN/pyensembl, or Nirvana
- **MAF** (.maf, .txt) in standard TCGA Mutation Annotation Format
- **CSV** (.csv, .tsv) with columns: gene, protein_change, variant_type
- **Expression matrix** (.csv, .tsv, .txt) with gene-level TPM/FPKM values (optional)

## Running locally

### Prerequisites

- Docker and Docker Compose
- NetMHCpan-4.1 and/or NetMHCIIpan-4.0 binaries (academic license)

### Setup

```bash
cp .env.example .env
# Edit .env with your database credentials and secret key

docker compose up --build
```

Frontend runs on `localhost:3000`, backend API on `localhost:8000`.

### Database migrations

```bash
docker compose exec backend alembic upgrade head
```

## Deployment

Production deployment uses Docker Compose on a GCP VM with nginx reverse proxy and Let's Encrypt TLS. See `infra/` for configuration.

```bash
# On the VM
cd /opt/cvdash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
```

## Development notes

- Backend uses SQLAlchemy async sessions with PostgreSQL
- Celery handles long-running pipeline tasks with Redis as broker
- Frontend uses NextAuth with JWT strategy for authentication
- Admin panel at /admin for user management and system stats
- WebSocket endpoint at /ws/{analysis_id} for real-time progress updates

## License

Proprietary. University of Oxford.
