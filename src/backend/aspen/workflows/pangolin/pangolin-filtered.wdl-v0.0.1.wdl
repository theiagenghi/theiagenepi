version 1.1

# Variant of pangolin.wdl-v0.0.1.wdl that allows restricting find_samples.py
# by both collection_date age (days) AND submitting_group_id. Either filter
# may be omitted; omitting both yields behavior identical to the canonical
# unfiltered WDL. Intended for manual SFN invocation only; the scheduled
# EventBridge target continues to point at the unfiltered WDL.

workflow pangolin {
    input {
        String docker_image_id = "pangolin:latest"
        String aws_region = "us-west-2"
        String genepi_config_secret_name
        String remote_dev_prefix = ""
        Int? max_collection_age_days
        Array[Int] submitting_group_ids = []
    }

    call pangolin_workflow {
        input:
        docker_image_id = docker_image_id,
        aws_region = aws_region,
        genepi_config_secret_name = genepi_config_secret_name,
        remote_dev_prefix = remote_dev_prefix,
        max_collection_age_days = max_collection_age_days,
        submitting_group_ids = submitting_group_ids,
    }

    call pango_lineages_loading {
        input:
        docker_image_id = docker_image_id,
        genepi_config_secret_name = genepi_config_secret_name,
        remote_dev_prefix = remote_dev_prefix,
    }
}

task pangolin_workflow {
    input {
        String docker_image_id
        String aws_region
        String genepi_config_secret_name
        String remote_dev_prefix
        Int? max_collection_age_days
        Array[Int] submitting_group_ids
    }

    command <<<
    export GENEPI_CONFIG_SECRET_NAME="~{genepi_config_secret_name}"
    if [ "~{remote_dev_prefix}" != "" ]; then
        export REMOTE_DEV_PREFIX="~{remote_dev_prefix}"
    fi

    set -Euxo pipefail
    export SAMPLE_IDS_FILE="${HOME}/sample_ids.txt"

    cd /usr/src/app/aspen/workflows/pangolin
    ./update_pangolin.sh

    AGE_FLAG=""
    if [ -n "~{max_collection_age_days}" ]; then
        AGE_FLAG="--max-collection-age-days ~{max_collection_age_days}"
    fi

    GROUP_FLAGS=""
    for gid in ~{sep(' ', submitting_group_ids)}; do
        GROUP_FLAGS="${GROUP_FLAGS} --submitting-group-id ${gid}"
    done

    /usr/local/bin/python3 find_samples.py \
        --output-file "${SAMPLE_IDS_FILE}" \
        ${AGE_FLAG} \
        ${GROUP_FLAGS}
    ./run_pangolin.sh
    >>>

    runtime {
        docker: docker_image_id
    }
}

task pango_lineages_loading {
    input {
        String docker_image_id
        String genepi_config_secret_name
        String remote_dev_prefix
    }

    command <<<
    echo "Starting task for loading Pango lineages" 1>&2

    export GENEPI_CONFIG_SECRET_NAME="~{genepi_config_secret_name}"
    if [ "~{remote_dev_prefix}" != "" ]; then
        export REMOTE_DEV_PREFIX="~{remote_dev_prefix}"
    fi
    genepi_config="$(aws secretsmanager get-secret-value --secret-id ~{genepi_config_secret_name} --query SecretString --output text)"
    export ASPEN_S3_DB_BUCKET="$(jq -r .S3_db_bucket <<< "$genepi_config")"

    cd /usr/src/app/aspen/workflows/import_pango_lineages
    ./entrypoint.sh 1>&2
    >>>

    runtime {
        docker: docker_image_id
    }
}
