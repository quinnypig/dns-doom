#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
mkdir -p artifacts/rv32 artifacts/performance
docker build -q -t dns1:local .
docker build -q -f Dockerfile.rv32 -t dns32:local .
container=(docker run --rm --network none --shm-size 4g
    --user "$(id -u):$(id -g)"
    --mount "type=bind,src=$PWD,dst=/work")
"${container[@]}" --entrypoint bash dns32:local ./build-rv32.sh
"${container[@]}" --entrypoint bash dns32:local ./build-native.sh
mode=${1:-fast}
if [ "$#" -gt 0 ]; then shift; fi
case "$mode" in
    fibonacci)
        "${container[@]}" dns32:local artifacts/rv32/fibonacci.elf \
            --steps 1000 --output artifacts/dns32-fibonacci "$@" ;;
    cold)
        "${container[@]}" dns32:local artifacts/rv32/doom.elf \
            --steps 100000000 --seconds 180 --output artifacts/dns32-doom "$@" ;;
    tick|fast)
        "${container[@]}" --entrypoint python dns32:local -m dns32.reference \
            artifacts/rv32/doom-small.elf --checkpoint D_DoomLoop --checkpoint-hit 2 \
            --output artifacts/tick-checkpoint
        flags=()
        if [ "$mode" = fast ]; then flags=(--client native --pipeline --optimize --packed --blocks --sequencer native --transport udp --commit-every 8); fi
        "${container[@]}" dns32:local artifacts/rv32/doom-small.elf \
            --checkpoint artifacts/tick-checkpoint --steps 3000000 --seconds 1800 \
            --output artifacts/dns32-tick "${flags[@]}" "$@" ;;
    test)
        "${container[@]}" --entrypoint python dns32:local -m unittest discover -s tests
        "${container[@]}" --entrypoint python dns32:local -m dns32.verify_live
        "${container[@]}" --entrypoint python dns32:local -m dns32.verify_live --native --optimized --packed --server bind --output artifacts/performance/bind-live.json
        "${container[@]}" --entrypoint python dns32:local -m dns32.verify_live --native --optimized --packed --server bind --sequencer native --transport udp --output artifacts/performance/bind-live-native.json
        "${container[@]}" --entrypoint python dns32:local -m dns32.verify_live --native --optimized --packed --server knot --output artifacts/performance/knot-live.json ;;
    *) echo "Usage: $0 {fibonacci|cold|tick|fast|test} [runtime arguments]" >&2; exit 2 ;;
esac
