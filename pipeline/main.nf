#!/usr/bin/env nextflow
/*
 * CVDash variant calling pipeline.
 *
 * Takes FASTQ or BAM input and produces an annotated VCF ready for
 * the immunogenicity scoring pipeline.
 *
 * Steps:
 *   FASTQ path:  fastp -> BWA-MEM2 -> samtools sort/index -> Mutect2 -> VEP
 *   BAM path:    Mutect2 -> VEP
 *
 * Designed to run on:
 *   - Google Cloud Batch (profile: docker)
 *   - Isambard HPC (profile: singularity)
 *   - Local dev (profile: docker, reduced resources)
 *
 * Usage:
 *   nextflow run main.nf -profile docker \
 *     --input_dir /mnt/inputs \
 *     --output_dir /mnt/output \
 *     --reference GRCh38 \
 *     --input_type fastq \
 *     --hla_alleles "HLA-A*02:01,HLA-B*44:02"
 */

nextflow.enable.dsl = 2

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

params.input_dir    = null       // directory containing FASTQ or BAM files
params.output_dir   = null       // where VCF + QC outputs go
params.work_dir     = null       // Nextflow work directory (scratch)
params.input_type   = "fastq"   // "fastq" or "bam"
params.reference    = "GRCh38"  // GRCh37 or GRCh38
params.hla_alleles  = ""        // comma-separated, for downstream use
params.paired       = false     // tumor-normal paired mode
params.container_dir = null     // Isambard: path to .sif files

// Reference genome paths (profile-dependent)
params.ref_fasta    = null       // set by profile
params.ref_bwa      = null       // BWA-MEM2 index prefix
params.known_sites  = null       // known variants for BQSR
params.vep_cache    = null       // VEP cache directory
params.vep_species  = "homo_sapiens"
params.vep_assembly = params.reference == "GRCh38" ? "GRCh38" : "GRCh37"

// Resource defaults (overridden per profile)
params.fastp_cpus   = 4
params.bwa_cpus     = 12
params.mutect_cpus  = 4
params.vep_cpus     = 2

// ---------------------------------------------------------------------------
// Profiles
// ---------------------------------------------------------------------------

profiles {
    docker {
        docker.enabled = true
        params.ref_fasta   = "/references/${params.reference}/genome.fa"
        params.ref_bwa     = "/references/${params.reference}/bwa-mem2/genome"
        params.known_sites = "/references/${params.reference}/known_sites.vcf.gz"
        params.vep_cache   = "/references/vep_cache"
    }
    singularity {
        singularity.enabled = true
        singularity.autoMounts = true
        params.ref_fasta   = "/projects/cvdash/references/${params.reference}/genome.fa"
        params.ref_bwa     = "/projects/cvdash/references/${params.reference}/bwa-mem2/genome"
        params.known_sites = "/projects/cvdash/references/${params.reference}/known_sites.vcf.gz"
        params.vep_cache   = "/projects/cvdash/references/vep_cache"
    }
    test {
        // Minimal resources for CI
        docker.enabled = true
        params.fastp_cpus  = 1
        params.bwa_cpus    = 2
        params.mutect_cpus = 1
        params.vep_cpus    = 1
    }
}

// ---------------------------------------------------------------------------
// Processes
// ---------------------------------------------------------------------------

/*
 * FASTP: adapter trimming and QC.
 * Produces trimmed FASTQs + JSON QC report.
 */
process FASTP {
    tag "${sample_id}"
    cpus params.fastp_cpus
    memory '8 GB'
    container 'quay.io/biocontainers/fastp:0.23.4--hadf994f_0'

    input:
    tuple val(sample_id), path(reads)

    output:
    tuple val(sample_id), path("trimmed_*.fastq.gz"), emit: trimmed
    path("${sample_id}_fastp.json"), emit: qc_report

    script:
    if (reads instanceof List && reads.size() == 2)
        """
        fastp \\
            -i ${reads[0]} \\
            -I ${reads[1]} \\
            -o trimmed_R1.fastq.gz \\
            -O trimmed_R2.fastq.gz \\
            --json ${sample_id}_fastp.json \\
            --thread ${task.cpus} \\
            --detect_adapter_for_pe \\
            --qualified_quality_phred 20 \\
            --length_required 36
        """
    else
        """
        fastp \\
            -i ${reads[0]} \\
            -o trimmed_R1.fastq.gz \\
            --json ${sample_id}_fastp.json \\
            --thread ${task.cpus} \\
            --qualified_quality_phred 20 \\
            --length_required 36
        """
}

/*
 * BWA-MEM2: alignment to reference genome.
 * Produces a coordinate-sorted, indexed BAM.
 */
process BWA_MEM2 {
    tag "${sample_id}"
    cpus params.bwa_cpus
    memory '32 GB'
    container 'quay.io/biocontainers/bwa-mem2:2.2.1--hd03093a_5'

    input:
    tuple val(sample_id), path(reads)

    output:
    tuple val(sample_id), path("${sample_id}.sorted.bam"), path("${sample_id}.sorted.bam.bai"), emit: bam

    script:
    """
    bwa-mem2 mem \\
        -t ${task.cpus} \\
        -R "@RG\\tID:${sample_id}\\tSM:${sample_id}\\tPL:ILLUMINA\\tLB:lib1" \\
        ${params.ref_bwa} \\
        ${reads} \\
    | samtools sort -@ 4 -o ${sample_id}.sorted.bam -

    samtools index ${sample_id}.sorted.bam
    """
}

/*
 * MUTECT2: somatic variant calling.
 * Runs in tumor-only or tumor-normal mode depending on params.paired.
 */
process MUTECT2 {
    tag "${sample_id}"
    cpus params.mutect_cpus
    memory '16 GB'
    container 'broadinstitute/gatk:4.5.0.0'

    input:
    tuple val(sample_id), path(bam), path(bai)

    output:
    tuple val(sample_id), path("${sample_id}.mutect2.vcf.gz"), path("${sample_id}.mutect2.vcf.gz.tbi"), emit: vcf
    path("${sample_id}.mutect2.stats"), emit: stats

    script:
    """
    gatk Mutect2 \\
        -R ${params.ref_fasta} \\
        -I ${bam} \\
        -O ${sample_id}.mutect2.vcf.gz \\
        --native-pair-hmm-threads ${task.cpus} \\
        --f1r2-tar-gz ${sample_id}.f1r2.tar.gz

    # Filter (tumor-only mode uses default panel of normals)
    gatk FilterMutectCalls \\
        -R ${params.ref_fasta} \\
        -V ${sample_id}.mutect2.vcf.gz \\
        -O ${sample_id}.filtered.vcf.gz \\
        --stats ${sample_id}.mutect2.stats

    # Replace output with filtered version
    mv ${sample_id}.filtered.vcf.gz ${sample_id}.mutect2.vcf.gz
    mv ${sample_id}.filtered.vcf.gz.tbi ${sample_id}.mutect2.vcf.gz.tbi
    """
}

/*
 * VEP: variant effect prediction / annotation.
 * Adds gene, protein change, consequence annotations to each variant.
 */
process VEP_ANNOTATE {
    tag "${sample_id}"
    cpus params.vep_cpus
    memory '8 GB'
    container 'ensemblorg/ensembl-vep:release_112.0'

    input:
    tuple val(sample_id), path(vcf), path(tbi)

    output:
    tuple val(sample_id), path("${sample_id}.annotated.vcf.gz"), emit: annotated_vcf
    path("${sample_id}_vep_summary.html"), emit: vep_summary

    script:
    """
    vep \\
        --input_file ${vcf} \\
        --output_file ${sample_id}.annotated.vcf \\
        --format vcf \\
        --vcf \\
        --offline \\
        --cache \\
        --dir_cache ${params.vep_cache} \\
        --species ${params.vep_species} \\
        --assembly ${params.vep_assembly} \\
        --fork ${task.cpus} \\
        --everything \\
        --pick \\
        --stats_file ${sample_id}_vep_summary.html \\
        --no_stats

    bgzip ${sample_id}.annotated.vcf
    tabix -p vcf ${sample_id}.annotated.vcf.gz
    """
}

/*
 * COLLECT_QC: gather metrics into a JSON report for the web UI.
 */
process COLLECT_QC {
    tag "${sample_id}"
    memory '1 GB'
    container 'python:3.12-slim'

    input:
    tuple val(sample_id), path(vcf)
    path(fastp_report)
    path(mutect_stats)

    output:
    path("${sample_id}_qc_metrics.json"), emit: metrics

    script:
    """
    python3 -c "
import json, gzip, sys

metrics = {'sample_id': '${sample_id}'}

# Count variants in VCF
vcf_file = '${vcf}'
opener = gzip.open if vcf_file.endswith('.gz') else open
n_pass, n_total = 0, 0
with opener(vcf_file, 'rt') as f:
    for line in f:
        if line.startswith('#'):
            continue
        n_total += 1
        fields = line.strip().split('\t')
        if len(fields) > 6 and 'PASS' in fields[6]:
            n_pass += 1

metrics['total_variants'] = n_total
metrics['pass_variants'] = n_pass

# Parse fastp QC if present
try:
    with open('${fastp_report}') as f:
        fp = json.load(f)
    metrics['total_reads'] = fp.get('summary', {}).get('after_filtering', {}).get('total_reads', 0)
    metrics['q30_rate'] = fp.get('summary', {}).get('after_filtering', {}).get('q30_rate', 0)
except Exception:
    pass

json.dump(metrics, open('${sample_id}_qc_metrics.json', 'w'), indent=2)
    "
    """
}

/*
 * FINALIZE: copy final outputs to output_dir with standard naming.
 */
process FINALIZE {
    tag "${sample_id}"
    publishDir "${params.output_dir}", mode: 'copy'

    input:
    tuple val(sample_id), path(annotated_vcf)
    path(qc_metrics)

    output:
    path("*.vcf.gz")
    path("*.json")

    script:
    """
    cp ${annotated_vcf} ${sample_id}.final.vcf.gz
    cp ${qc_metrics} ${sample_id}_qc_metrics.json
    """
}

// ---------------------------------------------------------------------------
// Workflow
// ---------------------------------------------------------------------------

workflow {
    // Determine input channel based on input_type
    if (params.input_type == "fastq") {
        // Expect paired-end FASTQs: *_R1.fastq.gz, *_R2.fastq.gz
        // or single-end: *.fastq.gz
        Channel
            .fromFilePairs("${params.input_dir}/*_{R1,R2}.fastq.gz", flat: true)
            .ifEmpty {
                // Fall back to single-end
                Channel.fromPath("${params.input_dir}/*.fastq.gz")
                    .map { f -> [f.baseName.replaceAll(/\.fastq$/, ''), [f]] }
            }
            .set { raw_reads }

        // Trim
        FASTP(raw_reads)

        // Align
        BWA_MEM2(FASTP.out.trimmed)

        // Call variants
        MUTECT2(BWA_MEM2.out.bam)

        // Annotate
        VEP_ANNOTATE(MUTECT2.out.vcf)

        // QC
        COLLECT_QC(
            VEP_ANNOTATE.out.annotated_vcf,
            FASTP.out.qc_report.first(),
            MUTECT2.out.stats.first()
        )

        // Output
        FINALIZE(VEP_ANNOTATE.out.annotated_vcf, COLLECT_QC.out.metrics)

    } else {
        // BAM input: skip alignment, go straight to Mutect2
        Channel
            .fromPath("${params.input_dir}/*.bam")
            .map { bam ->
                def bai = file("${bam}.bai")
                if (!bai.exists()) bai = file("${bam.baseName}.bai")
                [bam.baseName, bam, bai]
            }
            .set { input_bams }

        MUTECT2(input_bams)
        VEP_ANNOTATE(MUTECT2.out.vcf)

        // No fastp report for BAM input -- create empty placeholder
        Channel.of(file("NO_FASTP_REPORT")).set { empty_fastp }

        COLLECT_QC(
            VEP_ANNOTATE.out.annotated_vcf,
            empty_fastp.first(),
            MUTECT2.out.stats.first()
        )

        FINALIZE(VEP_ANNOTATE.out.annotated_vcf, COLLECT_QC.out.metrics)
    }
}
