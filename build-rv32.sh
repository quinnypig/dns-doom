#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
mkdir -p artifacts/rv32
compiler=${RISCV_CC:-riscv64-unknown-elf-gcc}
"$compiler" -march=rv32i -mabi=ilp32 -c programs/rv32/start.S -o artifacts/rv32/start.o
for program in fibonacci doom doom-small; do
    source=$program
    extra=()
    if [ "$program" = doom-small ]; then
        source=doom
        extra=(-DDNS_SMALL_VIEW)
    fi
    "$compiler" -march=rv32i -mabi=ilp32 -O2 -ffreestanding -fno-builtin \
        -fno-pic -msmall-data-limit=0 -ffunction-sections -fdata-sections \
        -nostdlib -Wl,--gc-sections -Wl,-T,programs/rv32/link.ld \
        "${extra[@]}" artifacts/rv32/start.o "programs/rv32/$source.c" -lgcc \
        -o "artifacts/rv32/$program.elf"
done
