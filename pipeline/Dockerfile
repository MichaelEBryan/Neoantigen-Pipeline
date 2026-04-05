# CVDash variant calling pipeline container.
#
# Contains Nextflow + all bioinformatics tools needed:
#   - BWA-MEM2 (alignment)
#   - samtools (BAM manipulation)
#   - GATK 4.5 (Mutect2 variant calling)
#   - Ensembl VEP (annotation)
#   - fastp (QC + trimming)
#   - bcftools, tabix, bgzip (VCF utilities)
#   - Nextflow (pipeline runner)
#
# For GCP Batch: this image runs directly.
# For Isambard: convert to Singularity .sif (see Singularity.def).
#
# Build:
#   docker build -t cvdash-pipeline:latest .
#   docker tag cvdash-pipeline:latest europe-west2-docker.pkg.dev/{PROJECT}/cvdash/pipeline:latest

FROM ubuntu:22.04 AS base

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/opt/tools:/opt/nextflow:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl unzip openjdk-17-jre-headless \
    python3 python3-pip \
    libbz2-dev liblzma-dev libcurl4-openssl-dev zlib1g-dev \
    libncurses5-dev libdeflate-dev \
    build-essential autoconf \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/tools

# --- samtools 1.19 ---
RUN wget -q https://github.com/samtools/samtools/releases/download/1.19/samtools-1.19.tar.bz2 \
    && tar xjf samtools-1.19.tar.bz2 \
    && cd samtools-1.19 && ./configure --prefix=/opt/tools && make -j$(nproc) && make install \
    && cd .. && rm -rf samtools-1.19*

# --- htslib (bgzip, tabix) ---
RUN wget -q https://github.com/samtools/htslib/releases/download/1.19/htslib-1.19.tar.bz2 \
    && tar xjf htslib-1.19.tar.bz2 \
    && cd htslib-1.19 && ./configure --prefix=/opt/tools && make -j$(nproc) && make install \
    && cd .. && rm -rf htslib-1.19*

# --- bcftools 1.19 ---
RUN wget -q https://github.com/samtools/bcftools/releases/download/1.19/bcftools-1.19.tar.bz2 \
    && tar xjf bcftools-1.19.tar.bz2 \
    && cd bcftools-1.19 && ./configure --prefix=/opt/tools && make -j$(nproc) && make install \
    && cd .. && rm -rf bcftools-1.19*

# --- BWA-MEM2 2.2.1 ---
RUN wget -q https://github.com/bwa-mem2/bwa-mem2/releases/download/v2.2.1/bwa-mem2-2.2.1_x64-linux.tar.bz2 \
    && tar xjf bwa-mem2-2.2.1_x64-linux.tar.bz2 \
    && mv bwa-mem2-2.2.1_x64-linux/bwa-mem2* /opt/tools/bin/ \
    && rm -rf bwa-mem2-*

# --- fastp 0.23.4 ---
RUN wget -q http://opengene.org/fastp/fastp.0.23.4 -O /opt/tools/bin/fastp \
    && chmod +x /opt/tools/bin/fastp

# --- GATK 4.5.0.0 ---
RUN wget -q https://github.com/broadinstitute/gatk/releases/download/4.5.0.0/gatk-4.5.0.0.zip \
    && unzip -q gatk-4.5.0.0.zip \
    && mv gatk-4.5.0.0 /opt/gatk \
    && ln -s /opt/gatk/gatk /opt/tools/bin/gatk \
    && rm gatk-4.5.0.0.zip

# --- Ensembl VEP 112 ---
RUN pip3 install --no-cache-dir ensembl-vep==112.0 || true
# VEP is complex; for production, use the official ensembl-vep container
# and pull the cache separately. This layer is a placeholder.

# --- Nextflow ---
RUN mkdir -p /opt/nextflow && cd /opt/nextflow \
    && wget -q https://github.com/nextflow-io/nextflow/releases/download/v24.04.1/nextflow \
    && chmod +x nextflow

# Copy pipeline definition
COPY main.nf /pipeline/main.nf
COPY nextflow.config /pipeline/nextflow.config

WORKDIR /pipeline

ENTRYPOINT ["/bin/bash", "-c"]
