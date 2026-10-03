#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
mkdir -p artifacts
docker build -q -t dns1:local .
if [ "$#" -eq 0 ]; then
    set -- demo
fi
docker run --rm --network none \
    --user "$(id -u):$(id -g)" \
    --mount "type=bind,src=$PWD/artifacts,dst=/output" \
    -e DNSCPU_OUTPUT=/output/countdown \
    dns1:local "$@"
