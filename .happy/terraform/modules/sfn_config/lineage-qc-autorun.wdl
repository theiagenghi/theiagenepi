version 1.1

workflow lineage_qc_autorun {
    input {
        String docker_image_id = "genepi-lineage-qc"
        String aws_region = "us-west-2"
        String genepi_config_secret_name
        String remote_dev_prefix = ""
    }

    call lineage_qc_autorun_workflow {
        input:
        docker_image_id = docker_image_id,
        aws_region = aws_region,
        genepi_config_secret_name = genepi_config_secret_name,
        remote_dev_prefix = remote_dev_prefix,
    }
}

task lineage_qc_autorun_workflow {
    input {
        String docker_image_id
        String aws_region
        String genepi_config_secret_name
        String remote_dev_prefix
    }

    command <<<
    set -Euxo pipefail
    # All the `1>&2` below is so miniwdl will log our messages since stdout
    # is effectively ignored in preference of only logging stderr
    echo "Starting task for processing lineage QC" 1>&2

    # Setup env vars for configs that expect them to be there.
    export AWS_REGION="~{aws_region}"
    export GENEPI_CONFIG_SECRET_NAME="~{genepi_config_secret_name}"
    if [ "~{remote_dev_prefix}" != "" ]; then
        export REMOTE_DEV_PREFIX="~{remote_dev_prefix}"
    fi

    # Ensure we start in an empty directory for entire process.
    WORKING_DIR=nextclade_autorun
    mkdir "${WORKING_DIR}"
    cd "${WORKING_DIR}"

    # TODO Implement an autorun process, this is just temporary setup for the
    # rest of the boilerplate until we get around to coding this.
    echo "TODO implement autorun of nextclade" 1>&2
    >>>

    runtime {
        docker: docker_image_id
    }
}
