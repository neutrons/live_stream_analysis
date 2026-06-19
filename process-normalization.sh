#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="${IMAGE_NAME:-live-stream-analysis:local}"
OUTPUT_CSV="${OUTPUT_CSV:-normalization.csv}"
REBUILD_IMAGE="${REBUILD_IMAGE:-1}"

if [[ "${REBUILD_IMAGE}" == "1" ]] || ! docker image inspect "${IMAGE_NAME}" >/dev/null 2>&1; then
    docker build -t "${IMAGE_NAME}" -f "${SCRIPT_DIR}/Dockerfile" "${SCRIPT_DIR}"
fi

docker run --rm \
    -v "${SCRIPT_DIR}:/work" \
    -w /work \
    "${IMAGE_NAME}" \
    live_stream_analysis preparer \
    --mode normalization \
    --nexus-file /work/nexus_files/normalization/NOM_242969.nxs.h5 \
    --reduction-output-csv "/work/${OUTPUT_CSV}" \
    --q-min 0.0 \
    --q-max 60.0

