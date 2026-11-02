# Reed–Solomon project

Shared repository instructions and the [KHDL skill](../../skills/khdl/SKILL.md)
apply here. Paths below are relative to this project unless a command says
otherwise; run shell commands from the repository root. Language feedback is
kept in the local, uncommitted `notes/khdl-feedback.md`, and documentation build
commands use the root `docs/` tooling.

## Project map

- `rtl/khdl.toml`: manifest; files in `rtl/` are top-level namespaces.
- `rtl/reedsolo.kh`: `rs_config`, computed field tables, GF arithmetic, `RsDefault`.
- `rtl/reedsolo_interfaces.kh`: `RsStream`, `RsTransaction(T)`, stage payload structs.
- `rtl/reedsolo_{encoder,syndromes,berlekamp,chien,forney,correction,decoder}.kh`:
  processing stages; `reedsolo_decoder` also defines `RsDecoderParallelism`.
- `rtl/reedsolo_round_robin.kh`: combinational routing with selection counters.
- `rtl/reedsolo_packet_fifo.kh`: generic commit/cancel packet FIFO (`RsPacketFifo`),
  its memory API flavors (`RsFifoMemory`), the `RsRamPort` interface, and the
  behavioral `RsRam` model; ported from
  [axi_fifos](https://github.com/MaartenBaert/axi_fifos). Not yet used by
  `RsDecoder`. Bypass variants must stay cycle-equivalent to a LUT packet FIFO.
- `rtl/reedsolo_gf_mul.kh`: private Python arithmetic verification wrapper.
- `rtl/reedsolo_checks.kh`: representative const elaboration roots for the LSP.
- `rtl/reedsolo_performance.kh`: elaboration-time timing/cost models and optional
  encoder-width/decoder-parallelism/message-buffer selection; `docs/performance.adoc` owns units,
  formulas, cost assumptions, and examples.
- `tests/rtl/`: verification-only KHDL tops and payload types, injected by tests.
- `tests/util.py`, `tests/conftest.py`, `tests/test_*.py`: cached compilers/simulators,
  ready/valid drivers, reference vectors, stage and pipeline regressions.
- `tools/compile.py`: concrete hierarchy checks and Verilog export.
- `docs/index.adoc`: manual root with overview and quick start. Chapters run
  from user-facing to internal: `primer.adoc`, `streams.adoc` (handshake,
  ordering, padding), `configuration.adoc` (parameters and constraints),
  `modules/encoder.adoc`, `modules/decoder.adoc`, `performance.adoc`,
  `stages.adoc` (block payloads, butterfly positions, and the stage chapters
  in `modules/`), and `verification.adoc`.

The design was ported from an earlier, unpublished VHDL implementation;
builds and tests must not depend on it. Keep `tests/reed_solomon.py`,
the copied reference, unchanged unless the requested work requires otherwise.

## RTL conventions

The baseline is RS(240,224), 8-bit symbols, `prim=0x11d`, `alpha=2`, **fcr=1**.
The Python reference defaults to a different `fcr`; configure it explicitly.
`rs_config(n, k, m, prim, alpha, fcr)` returns a type holding parameters,
symbol types, tables, and arithmetic. Pass it as `RS` throughout the hierarchy
and payload types; use `RS.n`, `RS.Sym`, `RS.gf_mul(...)` directly. Avoid local
aliases for every member or aliases to default members. `RsDefault` supplies
generic defaults; no table-codegen build step is needed. Field reduction depends
on `prim` independently of `alpha`; exponent construction must validate the
complete nonzero field cycle.

Follow the skill's idioms: local `val`/`var`, native bitwise operators, prelude
math, exclusive count ranges, `view Source`/`Sink`, grouped `sync(clk)` ports,
and same-name connections. Preserve persistent `r_` state and useful `v_`
temporary names from the port. Assignments are immediate within a process;
final port assignments observe updated state. Keep construction helpers and
verification-only items private; Python `resolve` can access private items.

Reset is active-high and synchronous. Use `clocked(clk, sync rst)` with register
initializers, including explicit reset values for registered output/ready wires.
Use `undef` for values irrelevant across reset; correction's invalid data/status
are deliberately unspecified. Tests must accept either hold or X lowering.

Keep bounds meaningful through payloads and local storage. Prefer existing
conditions and constrained types; use a justified local `assert_assume` only
for a missing compiler proof. Avoid blanket bounds helpers, artificial index
remapping, widening, or clamping to silence diagnostics. Assert generic
constraints before constructing tables/bounds. For partial lanes, reduce field
exponents at elaboration, compute valid arithmetic and mask discarded results.
Guard calculations whose indices/ranges would be invalid; discarded temporaries
may receive `undef`, but required control and padding remain defined. Avoid
runtime non-power-of-two modulo; guarded counters and explicit power-of-two
masking preserve cheap hardware. Wider integer types do not wrap implicitly.

## Interface and architecture contracts

Consult the manual for full timing and layouts; preserve these core contracts:

- Polynomial arrays are low-coefficient first. Streams send highest groups
  first; serial order within a beat is high lane to low lane. Partial final
  beats contain valid symbols in high lanes and zeros in low lanes. Input
  padding is ignored/forced to zero by the arithmetic.
- `RsEncoder` accepts `(k +/ U)` message beats and emits `(n +/ U)`
  codeword beats, including mixed message/parity beats. Any positive width is
  supported, even beyond parity count, block length, or field period. First
  output can transfer one cycle after first input in both buffering modes.
  Preserve its internal leading-zero alignment tail, newly computed mixed-beat
  parity, and clearing of residual state between frames.
- `RsDecoder` accepts `(n +/ U)` beats and emits `(k +/ U)` message
  beats. `RsSyndromes` evaluates `x**P * C(x)` for `P=(n +/ U)*U-n`.
  `RsChien` masks its first `P` search positions before counting/balancing.
  `RsCorrection` visits groups in reverse, offset by
  `(n +/ U)-(k +/ U)`, preserving inverse-butterfly lane positions.
  Clear final message padding even for failed frames and shared parity lanes.
  Unused error slots carry sentinel cycle `(n +/ U)`, not cycle zero.
- `RsStream` is the symbol-beat interface. Block stages use `RsTransaction(T)`:
  forward `data: T`/`valid`, reverse `ready`, with payload held through stalls.
  Block payloads carry decoding information only; reserve "codeword" for all
  `n` symbols. Forward whole payloads and construct results as structs.
  Structs pack into Verilog `input_data`/`output_data`; Python writes
  `inst.info_ports.input.data.type.new(**payload)` to `inst.ports.input.data`
  and reads `inst.ports.output.data.syndromes`.
- Message symbols bypass the block stages: `RsSyndromes` forwards the first
  `(k +/ U)` codeword beats unchanged on `message` with registered
  `message_commit`/`message_cancel`, `RsDecoder` buffers them in an
  `RsPacketFifo`, and `RsCorrection` takes one message beat per output beat.
  Keep commit asserted until the frame's last message beat is accepted; cancel
  malformed frames without forwarding their final beat; never let one frame's
  beats mix with another's commit or cancel. The default
  `full_message_buffer` holds `(3 + N_B + N_F)` frames so it never limits
  throughput; smaller buffers must hold at least one frame. Stage benches drive
  several streams with `tests/streams.py`.
- Chien/Forney/correction position payloads preserve the same search width and
  `RS` identity. Lane arrays, including butterfly temporaries, use
  `uint(0..unroll_chien)` (or correction's `uint(0..unroll)`). Standalone
  syndromes/Chien instances must also agree on padding coordinates.
- `RsDecoder` takes `parallelism: RsDecoderParallelism`, with positive fields
  `stream_unroll`, `berlekamp_unroll`, `omegas_unroll`, `forney_unroll`, `berlekamp_instances`,
  `forney_instances`, all defaulting
  to 1 when the argument is omitted. `stream_unroll` is shared by external streams,
  syndromes, Chien, and correction; standalone stages retain scalar unrolls.
  BM unroll divides `2*t`; omega unroll divides `t`; Forney permits a partial
  final arithmetic cycle with `1 <= unroll <= 2*t`. Stream widths need no
  divisibility. See the manual's configuration chapter for all restrictions.
- Each BM/Forney bank uses direct connections for one worker. Multiple workers
  use strict round-robin dispatch/collection without payload buffers or added cycles. Reset routers
  and workers together; preserve identical worker order and one result per
  accepted block. Later results wait for the selected worker. Worker counts
  are independent; replication preserves each stage's single-frame latency.
- `buffered` selects external input flow control: encoder uses it directly;
  decoder forwards it only to syndromes. It adds one input holding beat and
  cuts external ready's combinational dependence on downstream ready. Preserve
  both modes; it does not configure buffers at every stage.

## Verification

After a coherent RTL edit, check concrete elaboration before simulation:

```bash
.venv/bin/python -m projects.reed_solomon.tools.compile --check --n 12 --k 6 --m 4 --prim 0x13 --alpha 3 --fcr 0 --unroll 3 --unroll-berlekamp 3 --unroll-omegas 3 --unroll-forney 4
.venv/bin/python -m projects.reed_solomon.tools.compile --check
```

Repeat with `--buffered` when both modes are affected; add
`--berlekamp-instances 2 --forney-instances 3` for parallel-stage changes.
These check selected encoder/decoder hierarchies without generating files or simulators. Keep LSP
roots current, but use shell checks too: Python compilation is lazy and does
not execute file-level const blocks, and this LSP drops standard-library
assertion diagnostics. A clean Problems panel alone proves little.

Use `.venv/bin/pytest projects/reed_solomon/tests/test_<stage>.py` for stage changes and the full
`.venv/bin/pytest` suite for shared arithmetic/flow-control/pipeline changes.
Exercise affected parameter profiles, both buffering modes, padding, stalls,
reset, and frame overlap. Keep BM simulation unrolls at most four; larger
factors remain legal RTL but have impractical timing paths. Documentation-only
changes need link/example/manual checks, not hardware simulation rebuilds.
Record validation specific to the changes; simulation does not establish
synthesis/timing results. Existing regression evidence is under
`build/verification/` when available; avoid accumulating run histories here.

The shared `test_support` harness uses fresh pytest `tmp_path_factory` build directories, caches simulators within a session, and
keeps ccache in `build/ccache`. Respect environment overrides. Verilator uses
`-O0`; C++ uses `OPT_FAST=-O0` unless `MAKEFLAGS` overrides it. Clang and PCH
are the defaults; set `KHDL_SIM_COMPILER=clang` or `gcc` before pytest.
`OBJCACHE=` disables ccache; verify actual cache hits when measuring reuse.
Keep generated Verilog, caches, and environments under ignored paths.

The unchanged reference's `encode()`/`decode()` self-checks assume unroll=4;
parametric benches use `rs_parity()` directly. Its `add_random_errors()` samples
only within `k`; use test-local injection across `n` for parity/message coverage.
The Python helpers accept scalar configuration/parallelism keywords, construct
values in their compiler context, and cache with scalar keys. The stress bench
advances encoder and decoder independently; preserve continuous multi-frame
traffic, independent input/output stalls, and assertions of actual overlap.

## Documentation and knowledge maintenance

Build the manual with `.venv/bin/python docs/build.py`; install
`docs/requirements.txt` separately if needed. Output is offline HTML with
embedded SVGs at `build/docs/reed_solomon.html`; AsciiDoc diagnostics fail the build.
Keep the manual's order: explain a concept before relying on it, keep the
encoder/decoder chapters understandable without Reed–Solomon background, and
keep internal stage details in the stage reference. Test history and
measurements belong in `notes/reed_solomon/`, not in the manual or README.

Use backticks for inline code/formulas (plus-delimited monospace still applies
formatting). Refer to hardware by full module names such as `RsSyndromes`, and
reserve names such as syndromes for the mathematical values. `[wavedrom]`
literal blocks hold strict JSON, rendered offline by the Python WaveDrom
filter in `docs/`. WaveDrom columns show values sampled at rising edges: edge
i updates appear in column i+1. Use `.` to hold levels, including `x`
intervals, rather than repeating `0`/`1`, which draws glitches; keep explicit
bus states for successive beats even when their values are equal. Keep cycle
formulas, diagrams, examples, and RTL consistent. Check diagrams and internal links.

Keep this `AGENTS.md` focused on current Reed–Solomon instructions. Reusable language
idioms belong in the skill; detailed interface/algorithm explanations belong
in the manual; reproducible bugs and proposals belong in `notes/khdl-feedback.md`.
Local notes and verification logs retain past results without duplicating them
here. Distinguish observed compiler bugs from design choices and speculation.
