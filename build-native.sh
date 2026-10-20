#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
# Use the same interpreter for headers, ABI suffix and verification. Managed
# Python environments need not put python3-config on PATH.
PYTHON_BIN="${PYTHON_BIN:-python3}"
INCLUDE_DIR="$("${PYTHON_BIN}" -c 'import sysconfig; print(sysconfig.get_path("include"))')"
EXTENSION_SUFFIX="$("${PYTHON_BIN}" -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')"
g++ -O3 -std=c++17 -Wall -Wextra -Wno-missing-field-initializers -shared -fPIC \
    -I"${INCLUDE_DIR}" dns32/fastwire.cpp \
    -o "dns32/_fastwire${EXTENSION_SUFFIX}"
