#!/bin/bash

# Only refresh pangolin-data (lineage definitions). The pangolin tool itself is
# pinned in Dockerfile.pangolin to a version compatible with the pinned snakemake;
# upgrading pangolin from master here pulls a newer release that imports
# snakemake.api (snakemake>=8) and breaks `pangolin -pv` at runtime.
pip3 install --upgrade git+https://github.com/cov-lineages/pangolin-data.git

pangolin -pv
