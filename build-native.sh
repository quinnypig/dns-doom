#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
g++ -O3 -std=c++17 -Wall -Wextra -Wno-missing-field-initializers -shared -fPIC \
    $(python3-config --includes) dns32/fastwire.cpp \
    -o "dns32/_fastwire$(python3-config --extension-suffix)"
