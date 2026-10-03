#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
mkdir -p artifacts/performance
for variant in ${BENCH_VARIANTS:-python native pipeline optimized packed blocks knot-python knot-packed}; do
    flags=()
    server=bind
    steps=${BENCH_STEPS:-20000}
    case "$variant" in
      native) flags=(--native) ;;
      pipeline) flags=(--native --pipeline) ;;
      optimized) flags=(--native --pipeline --optimize) ;;
      packed) flags=(--native --pipeline --optimize --packed) ;;
      blocks) flags=(--native --pipeline --optimize --packed --blocks) ;;
      knot-python) server=knot; steps=100 ;;
      knot-packed) server=knot; steps=100; flags=(--native --pipeline --optimize --packed) ;;
    esac
    docker run --rm --network none --shm-size 4g --user "$(id -u):$(id -g)" \
      --mount "type=bind,src=$PWD,dst=/work" --entrypoint python dns32:local \
      -m dns32.benchmark --server "$server" "${flags[@]}" \
      --steps "$steps" --repeat 3 --output "artifacts/performance/$variant" \
      > "artifacts/performance/$variant.log" 2>&1
    python3 -c 'import json,sys; r=json.load(open(sys.argv[1]));print(sys.argv[1],r["median_seconds"],flush=True)' \
      "artifacts/performance/$variant/benchmark.json"
done
