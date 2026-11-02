#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
project_dir="$PWD"
git submodule update --init --recursive
python3 -m venv .venv
.venv/bin/python -m pip install maturin numpy pytest
.venv/bin/python -m pip install 'cocotb>=2,<3' 'cocotbext-axi>=0.1.28,<0.2'
export PATH="$project_dir/.venv/bin:$PATH"
# Keep the optimized compiler build explicit for elaboration-heavy RTL.
.venv/bin/python -m pip install --no-build-isolation \
    --config-settings 'maturin.build-args=--profile=release --locked' \
    -e khdl/rust/khdl_python
