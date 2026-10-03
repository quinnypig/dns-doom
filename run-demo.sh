#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
docker build -q -t dns1:local .
docker build -q -f Dockerfile.rv32 -t dns32:local .
docker run --rm --network none --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$PWD,dst=/work" --entrypoint bash dns32:local ./build-native.sh
prepare=false
for file in artifacts/rv32/fibonacci.elf artifacts/tick-checkpoint/{expected-state.json,program.elf,checkpoint.json,memory.bin.gz,reference-view.png.indices}; do
  if [ ! -f "$file" ]; then prepare=true; fi
done
if "$prepare"; then
  docker run --rm --network none --user "$(id -u):$(id -g)" \
    --mount "type=bind,src=$PWD,dst=/work" --entrypoint bash dns32:local ./build-rv32.sh
  docker run --rm --network none --user "$(id -u):$(id -g)" \
    --mount "type=bind,src=$PWD,dst=/work" --entrypoint python dns32:local -m dns32.reference \
    artifacts/rv32/doom-small.elf --checkpoint D_DoomLoop --checkpoint-hit 2 --output artifacts/tick-checkpoint
fi
exec python3 -m dns32.demo "$@"
