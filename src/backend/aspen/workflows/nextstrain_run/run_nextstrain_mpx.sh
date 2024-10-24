#!/bin/bash

# WDL inputs available through environmental vars:
# AWS_REGION
# GENEPI_CONFIG_SECRET_NAME
# REMOTE_DEV_PREFIX (if set)
# WORKFLOW_ID
# S3_FILESTEM

set -Eeuxo pipefail
shopt -s inherit_errexit

df 1>&2
cat /proc/meminfo 1>&2

start_time=$(date +%s)

aws configure set region $AWS_REGION

if [ -n "${BOTO_ENDPOINT_URL-}" ]; then
  export aws="aws --endpoint-url ${BOTO_ENDPOINT_URL}"
else
  export aws="aws"
fi

# fetch aspen config
set +x  # don't echo secrets
echo "* set \$genepi_config (not printing value because contains secrets)"
genepi_config="$($aws secretsmanager get-secret-value --secret-id $GENEPI_CONFIG_SECRET_NAME --query SecretString --output text)"
echo "* set \$aspen_s3_db_bucket"
aspen_s3_db_bucket="$(jq -r .S3_db_bucket <<< "$genepi_config")"
set -x

# Download the latest mpox exclusions and clades list. This happens at RUN time, not BUILD time so that
# we are always building trees with the latest upstream filters.
wget https://raw.githubusercontent.com/nextstrain/mpox/master/phylogenetic/defaults/exclude_accessions.txt -O /mpox/phylogenetic/defaults/exclude_accessions.txt
wget https://raw.githubusercontent.com/nextstrain/mpox/master/phylogenetic/defaults/clades.tsv -O /mpox/phylogenetic/defaults/clades.tsv

mkdir -p /mpox/phylogenetic/data
key_prefix="phylo_run/${S3_FILESTEM}/${WORKFLOW_ID}"
s3_prefix="s3://${aspen_s3_db_bucket}/${key_prefix}"

# We use a file to pass from `export.py` to `save.py` before writing them to DB
RESOLVED_TEMPLATE_ARGS_SAVEFILE=/tmp/resolved_template_args.json

# We have mixed ownership of this checkout on purpose
git config --global --ad safe.directory /mpox
mpox_git_rev=$(cd /mpox && git rev-parse HEAD)

# dump the sequences, metadata, and builds.yaml for a run out to disk.
# TODO -- we need to emit *aligned* mpox sequences!!!
aligned_upstream_location=$(
    python3 /usr/src/app/aspen/workflows/nextstrain_run/export.py        \
           --phylo-run-id "${WORKFLOW_ID}"                               \
           --sequences /mpox/phylogenetic/data/sequences_czge.fasta      \
           --metadata /mpox/phylogenetic/data/metadata_czge.tsv          \
           --selected /mpox/phylogenetic/data/include.txt                \
           --sequence-type aligned                                       \
           --resolved-template-args "${RESOLVED_TEMPLATE_ARGS_SAVEFILE}" \
           --builds-file /mpox/phylogenetic/build_czge.yaml              \
           --reset-status
)

aligned_upstream_s3_bucket=$(echo "${aligned_upstream_location}" | jq -r .bucket)
aligned_upstream_sequences_s3_key=$(echo "${aligned_upstream_location}" | jq -r .sequences_key)
aligned_upstream_metadata_s3_key=$(echo "${aligned_upstream_location}" | jq -r .metadata_key)

# fetch the upstream dataset
if [ ! -e /mpox/phylogenetic/data/upstream_sequences.fasta ]; then
    $aws s3 cp --no-progress "s3://${aligned_upstream_s3_bucket}/${aligned_upstream_sequences_s3_key}" /mpox/phylogenetic/data/upstream_sequences.fasta.xz
    unxz /mpox/phylogenetic/data/*.xz
fi
if [ ! -e /mpox/phylogenetic/data/upstream_metadata.tsv ]; then
    $aws s3 cp --no-progress "s3://${aligned_upstream_s3_bucket}/${aligned_upstream_metadata_s3_key}" /mpox/phylogenetic/data/upstream_metadata.tsv.xz
    unxz /mpox/phylogenetic/data/*.xz
fi

# If we've written out any samples, add them to the upstream metadata/fasta files
if [ -e /mpox/phylogenetic/data/sequences_czge.fasta ]; then
    python3 /usr/src/app/aspen/workflows/nextstrain_run/merge_mpx.py --required-metadata /mpox/phylogenetic/data/metadata_czge.tsv --required-sequences /mpox/phylogenetic/data/sequences_czge.fasta --upstream-metadata /mpox/phylogenetic/data/upstream_metadata.tsv --upstream-sequences /mpox/phylogenetic/data/upstream_sequences.fasta --destination-metadata /mpox/phylogenetic/data/metadata.tsv --destination-sequences /mpox/phylogenetic/data/sequences.fasta --required-match-column strain --upstream-match-column accession
else
    cp /mpox/phylogenetic/data/upstream_metadata.tsv /mpox/phylogenetic/data/metadata.tsv
    cp /mpox/phylogenetic/data/upstream_sequences.fasta /mpox/phylogenetic/data/sequences.fasta
fi;

# Persist the build config we generated.
$aws s3 cp /mpox/phylogenetic/build_czge.yaml "${s3_prefix}/build_czge.yaml"
$aws s3 cp /mpox/phylogenetic/data/include.txt "${s3_prefix}/include.txt"

# run snakemake, if run fails export the logs from snakemake to s3
(cd /mpox/phylogenetic && snakemake --printshellcmds --configfile build_czge.yaml --resources=mem_mb=312320) || { $aws s3 cp /mpox/phylogenetic/.snakemake/log/ "${s3_prefix}/logs/snakemake/" --recursive ; $aws s3 cp /mpox/phylogenetic/results/aspen/logs/ "${s3_prefix}/logs/mpox/" --recursive ; }

# upload the tree to S3. The variable key is created to use later
key="${key_prefix}/mpx_czge.json"
$aws s3 cp /mpox/phylogenetic/auspice/monkeypox_mpxv.json "s3://${aspen_s3_db_bucket}/${key}"

# update aspen
aspen_workflow_rev=WHATEVER
aspen_creation_rev=WHATEVER

end_time=$(date +%s)

# create the objects
python3 /usr/src/app/aspen/workflows/nextstrain_run/save.py                 \
    --aspen-workflow-rev "${aspen_workflow_rev}"                            \
    --aspen-creation-rev "${aspen_creation_rev}"                            \
    --ncov-rev "${mpox_git_rev}"                                            \
    --aspen-docker-image-version ""                                         \
    --end-time "${end_time}"                                                \
    --phylo-run-id "${WORKFLOW_ID}"                                         \
    --bucket "${aspen_s3_db_bucket}"                                        \
    --key "${key}"                                                          \
    --resolved-template-args "${RESOLVED_TEMPLATE_ARGS_SAVEFILE}"           \
    --tree-path /mpox/phylogenetic/auspice/monkeypox_mpxv.json              \
