# KHDL workshop

Realistic hardware designs used to stress-test [KHDL](https://github.com/khdl-lang/khdl),
especially its generic programming facilities. Each project owns its RTL,
tests, reference models, tools, and module documentation. The compiler,
Python environment, simulator infrastructure, and language feedback are shared.

The current projects are:

- [Reed–Solomon](projects/reed_solomon/README.md): a parametric encoder and decoder,
  configurable unrolling and worker replication, and ready/valid interfaces.
- [AXI4 APIs](projects/axi/README.md): FIFO-style channel and transaction APIs,
  a pipeline, RAM/error targets, router, and copy engine built using references.
- [Pipelined FFA FIR](projects/fir/README.md): signed fixed-point filtering,
  phase-aware parallelism, streaming coefficients, and configurable pipelines.

```text
khdl/                       Shared compiler submodule
skills/khdl/                Reusable language/tooling guidance
test_support/               Shared simulator builds and ready/valid drivers
projects/
  reed_solomon/
    rtl/                    KHDL manifest and hardware modules
    tests/                  Pytest benches and unchanged Python reference
    tools/                  Elaboration checks and Verilog export
    docs/                   Module manual, interface contracts, timing diagrams
  axi/                      AXI4 APIs, demonstrations, tests, and manual
  fir/                      Pipelined FFA FIR, tests, tools, and manual
docs/                       Shared documentation builder and landing page
build/                      Ignored exports, documentation, and compiler cache
```

Instructions common to every project live in [AGENTS.md](AGENTS.md);
design-specific instructions live in each project's own `AGENTS.md`.
Reusable language and tooling practices live in the
[KHDL skill](skills/khdl/SKILL.md). Compiler limitations that affect these
designs are reported as [KHDL issues](https://github.com/khdl-lang/khdl/issues)
and cited as `khdl#<number>`.

## Setup and tests

Install Rust (at least 1.95), Python 3.12–3.14, Verilator, Clang, and Make.
From the repository root:

```bash
./setup.sh
.venv/bin/pytest                              # All projects
.venv/bin/pytest projects/reed_solomon/tests   # One project
.venv/bin/python -m projects.reed_solomon.tools.compile --check
.venv/bin/python -m projects.reed_solomon.tools.compile --unroll 16 --buffered
```

The last command exports the encoder and decoder to
`build/reed_solomon/verilog/`; `--out` selects another directory. See the
[Reed–Solomon README](projects/reed_solomon/README.md) for configuration options.
Python tools run as modules from the repository root, so they use the same
package imports as the tests. Tests use relative imports within each project;
new projects can reuse filenames such as `test_encoder.py` and `util.py`.

`setup.sh` initializes the shared submodule and installs KHDL, NumPy, pytest,
maturin, cocotb, and cocotbext-axi into `.venv`. AXI bounded formal checks also
require Yosys. The submodule is pinned at
`cdca7cc7c62a8b3d30ce4ee2a879199048c93d74`, including transparent reference
fields ([PR #27](https://github.com/khdl-lang/khdl/pull/27)) and limited mutable
method receivers ([PR #28](https://github.com/khdl-lang/khdl/pull/28)). PR #28 is rebased onto PR #27 at `9df78364`, based on upstream `e0264ac3`;
there are no additional local compiler patches. Calls and struct constructors
now require arguments in declaration order, including explicit defaults before
later arguments. Python simulator ports retain interface
hierarchy (`inst.ports.input.ready`) and expose values directly, without a
`.value` wrapper. Use `inst.info_ports.input.data.type` for payload constructors.
The binding build explicitly selects Cargo's release profile. After Rust or
embedded standard-library changes, rebuild the bindings:

```bash
.venv/bin/python -m pip install --no-build-isolation \
  --config-settings 'maturin.build-args=--profile=release --locked' \
  -e khdl/rust/khdl_python
```

The release LSP is a separate build target. Upstream's `profiling` Cargo
profile provides optimized builds with debugging symbols when needed.

Simulators use fresh pytest temporary directories and are cached within a
session. Shared compiler caching uses `build/ccache` when `ccache` is available;
existing `CCACHE_DIR` and `OBJCACHE` settings take precedence. The shared test
plugin defaults generated C++ to `OPT_FAST=-O0`, respecting an explicit
`MAKEFLAGS` choice. `test_support.simulation.build_simulator` selects Clang
and Verilator `optimization=0`; `KHDL_SIM_COMPILER=gcc` selects GCC.

Large regression runs can exhaust a memory-backed `/tmp`. Put pytest's fresh
build directories on the main disk with
`.venv/bin/pytest --basetemp=build/pytest`. To move compiler scratch files too,
create `build/tmp` and set `TMPDIR="$PWD/build/tmp"` for the command.

## Documentation

The [documentation index](docs/index.adoc) links to separate offline project
manuals with embedded WaveDrom SVGs:

```bash
.venv/bin/python -m pip install -r docs/requirements.txt
.venv/bin/python docs/build.py
# Open build/docs/index.html
```

A new project adds `projects/<name>/docs/index.adoc` and a link in the shared
index. The builder automatically generates `build/docs/<name>.html`; the
landing page is `build/docs/index.html`. Keep project-prefixed chapter anchors
for clarity. Documentation builds do not elaborate RTL or build simulators.

## VS Code

Build the LSP server, then package and install the checked-out client:

```bash
cd khdl/rust
cargo build --release --locked -p khdl_lsp_server
cd ../lsp_client
npm ci
npx vsce package --out /tmp/khdl.vsix
code --install-extension /tmp/khdl.vsix
```

Set `khdl.server.path` to the absolute path of
`khdl/rust/target/release/khdl_lsp_server` in VS Code settings, reload the
window, and open a `.kh` file. The Reed–Solomon manifest is
`projects/reed_solomon/rtl/khdl.toml`. The upstream
client README currently describes a generic LSP example; its actual
`package.json` and `client/src/extension.ts` define this setting.

## Licence

This repository is released under the [MIT License](LICENCE.txt). The `khdl`
submodule is a separate project with its own licence.
