# Pipelined radix-2 FFA FIR

A generic signed fixed-point FIR filter with arbitrary positive tap counts and
samples per beat, configurable radix-2 FFA depth, systolic leaves, and elastic
ready/valid pipelines. Phase-aware overlap handles odd parallel widths while
preserving one input beat per output beat and causal convolution values.

```bash
# From the repository root:
.venv/bin/python -m projects.fir.tools.compile --check
.venv/bin/python -m projects.fir.tools.compile --taps 9 --parallel 7 --ffa-depth 2 --taps-per-stage 2
.venv/bin/pytest projects/fir/tests --basetemp=build/fir/pytest
.venv/bin/python docs/build.py
```

The export defaults to `build/fir/verilog/fir.v`. For large runs, create a
`build/fir/tmp` directory and set `TMPDIR="$PWD/build/fir/tmp"` as well. The
manual is built as `build/docs/fir.html`; its source is [docs/index.adoc](docs/index.adoc).

`fir.Fir` exposes the requested width/fraction, tap, parallelism, and depth
parameters. It adds `taps_per_stage` (default 4) and `pipeline_steps` (default
one algorithm step per elastic stage). See the manual for their exact meaning.

Input and output are interfaces: flattened Verilog ports include
`input_samples`, `input_valid`, `input_ready`, `output_samples`, `output_valid`,
`output_ready`, and `output_clipped`. Lane zero is earliest. The coefficient
ports are `coeff_value`, `coeff_push`, and `coeff_clear`. Clock/reset are `clk`
and active-high synchronous `rst`.

Push `taps` coefficients, h[0] first, after reset or clear. No input transfers
are admitted before the complete set is ready. Clear-then-push on one edge
starts a replacement set. Clear consumes and discards unfinished filtering work
while retaining completed products and any stalled external output. A marker
orders decoder-history clearing behind retained old results. Replacement does
not require resetting or violating a stream handshake.

There is no intermediate rounding. The final quantizer rounds ties toward
positive infinity, then saturates to the signed output range. `clipped` is true
if any lane needed saturation. Reset/clear starts convolution with zero history;
invalid cycles and stalls do not insert zeros. No tail beats are emitted.

This project owns its RTL and copied/adapted FIFO-style stream APIs; it imports
no KHDL from the other projects. Python simulator infrastructure is shared.
