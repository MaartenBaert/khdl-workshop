# Reed–Solomon in KHDL

A parametric Reed–Solomon encoder and decoder in
[KHDL](https://github.com/khdl-lang/khdl), ported structurally from an earlier
VHDL implementation. It keeps
`RsEncoder` and the `RsDecoder` pipeline (`RsSyndromes` → `RsBerlekamp` →
`RsChien` → `RsForney` → `RsCorrection`), synchronous active-high reset,
registers, butterfly balancers, unrolling parameters, and ready/valid flow
control. The original VHDL's explanatory comments are preserved next to the
matching logic.

The default code is **RS(240,224) over GF(2⁸), primitive polynomial 0x11d,
alpha=2, first consecutive root 1**. Any code with `0 < k < n < 2**m` and even
`n-k` works: `rs_config(n, k, m, prim, alpha, fcr)` computes every table during
elaboration, with no code generation step. Streams carry any positive number of
symbols per beat, and the decoder's arithmetic stages can be unrolled or
replicated independently.

- The [module manual](docs/index.adoc) explains the code, the stream contract,
  configuration, every module, timing models, and verification. Build it as
  described below.
- [`AGENTS.md`](AGENTS.md) holds the design contracts for contributors and
  agents; the [KHDL skill](../../skills/khdl/SKILL.md) holds general language
  guidance.

## Setup

See the [repository README](../../README.md) for prerequisites, compiler
installation, and editor integration. From the repository root:

```bash
./setup.sh
.venv/bin/pytest projects/reed_solomon/tests
.venv/bin/python docs/build.py      # manual: build/docs/reed_solomon.html
```

## Exporting Verilog

```bash
# Default code, 16 symbols per beat, buffered input:
.venv/bin/python -m projects.reed_solomon.tools.compile --unroll 16 --buffered
# A shortened code over GF(16), with three symbols per beat:
.venv/bin/python -m projects.reed_solomon.tools.compile --n 12 --k 6 --m 4 --prim 0x13 --alpha 3 --fcr 0 --unroll 3
```

Exports go to `build/reed_solomon/verilog/`; `--out` selects another
directory. `--unroll` sets the stream width of both modules.
`--unroll-berlekamp`, `--unroll-omegas`, `--unroll-forney`,
`--berlekamp-instances`, and `--forney-instances` set the decoder's
arithmetic unrolling and worker counts. The decoder's message FIFO defaults to
the pipeline's full frame capacity; `--message-depth` sets its depth in beats,
`--message-interval` sizes it to sustain one frame per that many cycles, `--message-memory` selects `lut`, `block:N`, or `external:N`, and
`--message-no-bypass` removes its bypass logic. `--check` elaborates the same
hierarchies without writing files, which gives diagnostics before a simulator
build. Each output file contains the complete module hierarchy and the command
prints its top module name. Compile the two exports separately, because
generated module names can collide between them.

## Using the modules in KHDL

Construct the configuration once and pass it to every interface and instance:

```text
type RS = rs_config(n=12, k=6, m=4, prim=0x13, alpha=3, fcr=0);
// In module ports: input: interface RsStream(3, decoded=false, RS=RS).Sink
instance RsEncoder(unroll=3, buffered=false, RS=RS) ports(clk, rst, input, output);
```

`rs_config` is in `reedsolo`, `RsStream` in `reedsolo_interfaces`, and each
module in its own `reedsolo_<stage>` namespace. The manual's quick start shows
a complete encoder and decoder. From Python, use
`config = c.resolve('reedsolo.rs_config')(**parameters)`, then
`factory(RS=config, ...)`. The test helpers also accept the six scalar
parameters directly, e.g. `instance('encoder', n=12, k=6, m=4, prim=19,
alpha=3, fcr=0, unroll=3)`.

## Relation to the earlier VHDL implementation

- Each VHDL entity/architecture pair has a matching `rtl/reedsolo_<stage>.kh`,
  with the same stage ports and arithmetic parameters. Modules and interfaces
  use UpperCamelCase names with an `Rs` prefix; source namespaces keep their
  `reedsolo_` filenames.
- `buffered=false` corresponds to the VHDL's `RS_FLOW_CONTROL_MINIMAL`, and
  `buffered=true` to `RS_FLOW_CONTROL_BUFFERED`. As in the VHDL's later
  revisions, `RsDecoder` applies it only to its input stage.
- VHDL process variables that persist become registers inside
  `clocked(clk, sync rst)`, and temporary variables stay local. KHDL
  assignments, like VHDL variable assignments, take effect immediately within
  the process.
- The VHDL's fixed tables are replaced by tables computed from `RS` during
  elaboration. Field reduction depends only on `prim`, so generators other
  than `alpha=2` need no change to the reduction network.
- The VHDL fixes the code in package constants and limits widths to at most
  `n`. The KHDL port takes the code as a parameter and accepts any positive
  width. Its decoder has one shared stream width where the VHDL has separate
  syndrome, Chien, and correction widths. Worker replication with round-robin
  routing and the timing models are additions.
- Lane and position ports use exact ranges such as `uint(0..unroll_chien)`
  instead of the VHDL's broad position types, so most bounds are checked
  statically. Two local, justified `assert_assume` sites cover invariants the
  range checker cannot prove: `RsBerlekamp`'s degree relation and `RsChien`'s
  maximum root count.

## Tests

The benches follow
[advent_of_fpga](https://github.com/KarelPeeters/advent_of_fpga): they compile
the manifest through the KHDL Python bindings, build Verilator simulators, and
drive them from pytest. `tests/reed_solomon.py` is an unchanged copy of the
original reference model, and vectors are generated deterministically during
the tests, so the original checkout is not needed. The manual's verification
chapter lists what each bench covers.

Simulator builds go to pytest temporary directories, which keep the last three
sessions, and each compiled specialization is reused within a session. When
`ccache` is on `PATH`, the harness sets `OBJCACHE=ccache` with the cache in
`build/ccache`, respecting existing `OBJCACHE`/`CCACHE_DIR` settings;
`OBJCACHE='' pytest` disables it. Generated C++ is compiled with
`OPT_FAST=-O0` to avoid long optimization of large functions; override it with,
for example, `MAKEFLAGS='OPT_FAST=-Os' pytest`. The harness uses Clang by
default; `KHDL_SIM_COMPILER=gcc` selects GCC for comparison.
