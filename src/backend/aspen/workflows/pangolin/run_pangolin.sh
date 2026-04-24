#!/bin/bash

# TODO: fix pipefail flags to be informative
set -Eeuxo pipefail
shopt -s inherit_errexit  # no silent breaking

# Dump UShER binary diagnostics before any Snakemake invocation.
# Snakemake cleans up its tmpdir on exit, so the log file is gone by the time
# an EXIT trap fires. Running ldd and a direct binary test here captures any
# shared-library loading errors in CloudWatch before cleanup can occur.
echo "=== ldd usher-sampled ===" >&2
ldd /usr/local/bin/usher-sampled >&2 || echo "ldd usher-sampled FAILED (exit $?)" >&2
echo "=== ldd faToVcf ===" >&2
ldd /usr/local/bin/faToVcf >&2 || echo "ldd faToVcf FAILED (exit $?)" >&2
echo "=== usher-sampled --help ===" >&2
timeout 5 usher-sampled --help >&2 || echo "usher-sampled --help FAILED (exit $?)" >&2
echo "=== end diagnostics ===" >&2

# check pangolin is present:
pangolin -pv

sequences_output="sequences.fasta"

cd /usr/src/app/aspen/workflows/pangolin
# call export script to export renamed sequences
/usr/local/bin/python3 export.py \
  --sample-ids-file "$SAMPLE_IDS_FILE" \
  --sequences "$sequences_output"

# call pangolin on the exported sequences
lineage_report="lineage_report.csv"
pangolin --threads 16 $sequences_output --outfile "$lineage_report"

last_updated=$(date +'%m-%d-%Y')
# save the pangolin results back to the db:
/usr/local/bin/python3 save.py \
  --pangolin-csv "$lineage_report" \
  --pangolin-last-updated "$last_updated"
