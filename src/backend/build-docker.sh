#!/bin/bash

# Set the date for tagging
DATE=$(date +%Y%m%d)

# Array of Dockerfiles to build
DOCKERFILES=(
    "Dockerfile.backend"
)

    # "Dockerfile.gisaid"
    # "Dockerfile.lineage_qc"
    # "Dockerfile.nextstrain"
    # "Dockerfile.pangolin"

# Function to build Docker image
build_docker() {
    local dockerfile=$1
    local tag_name=$(echo $dockerfile | tr '[:upper:]' '[:lower:]' | sed 's/dockerfile\.//g')
    echo "Tag name is: ${tag_name}"
    if [ "$tag_name" = "dockerfile" ]; then
        tag_name="backend"
    fi

    echo "Building $dockerfile..."
    docker build --no-cache -t genepi-$tag_name:$DATE -f $dockerfile . 2>&1 | tee docker_$dockerfile.log
    
    if [ $? -eq 0 ]; then
        echo "Successfully built $dockerfile"
    else
        echo "Failed to build $dockerfile"
    fi
    echo "----------------------------------------"
}

# Main execution
echo "Starting Docker builds on $DATE"
echo "----------------------------------------"

for dockerfile in "${DOCKERFILES[@]}"
do
    build_docker $dockerfile
done

echo "All builds completed."