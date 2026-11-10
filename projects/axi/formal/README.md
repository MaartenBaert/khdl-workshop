# Formal checks

From the repository root:

```sh
.venv/bin/python -m projects.axi.tools.formal --target pipeline
.venv/bin/python -m projects.axi.tools.formal --target pipeline --unbounded
.venv/bin/python -m projects.axi.tools.formal --target error
.venv/bin/python -m projects.axi.tools.formal --target manager
.venv/bin/python -m projects.axi.tools.formal --target error --inject-fault  # must fail
.venv/bin/pytest projects/axi/tests/test_formal.py
```

`--depth` sets the number of checked cycles after reset. The defaults are 10
for `pipeline`, 8 for `error`, and 5 for `manager`. Runtime grows steeply with
depth; the `manager` default takes a few minutes. The pytest suite proves the
`manager` target one cycle shallower. `--unbounded` proves the properties for
any number of cycles instead. It succeeds quickly for `pipeline`; for `error`
and `manager` it has not finished within 20 minutes.

## Engines

`--engine abc` is the default when ABC (`yosys-abc` or `abc`) and
`yosys-witness` are installed. It exports the model as an AIGER circuit and
runs ABC's `bmc3` for bounded checks, or `pdr` for `--unbounded`. It is several
times faster than `--engine sat`, Yosys's built-in SAT solver, which needs only
Yosys and remains the fallback. Both start from the same Yosys model and treat
undefined values and uninitialized registers as arbitrary. Yosys's
SMT-based `yosys-smtbmc` with Yices was not faster than `sat` on the
`manager` target.

Output goes to `build/axi/formal/<target>/` unless `--out` is given:
`original.v` (the unmodified export), `design.formal.v` (the adapted copy),
`harness.sv`, Yosys scripts and logs, and `check.vcd` for a counterexample or
`reachable.vcd` for the reachability trace. With `abc`, a failure lists each
violated assertion with its source line, such as
`axi4_protocol.sv:430: AXI4_SUBORDINATE_RULE(rlast_ok)` or a KHDL assertion in
a named module; the trace is ABC's counterexample replayed in Yosys's
simulator. With `sat`, a counterexample table in `check.log` lists every named
rule; the failing one is 0 in the last step, or a `khdl_assertion_failed` flag
is 1.

## Method

`tools/formal.py` exports a module from KHDL, wraps it in a generated
`formal_top`, and checks it from reset for `--depth` cycles. Reset is
assumed in the first cycle and is unconstrained afterwards, so resets during
transactions are explored. `async2sync` models the asynchronous reset at
cycle granularity, which matches the reset contract: assertion at any time,
release synchronous to the clock.

KHDL lowers runtime assertion failures to diagnostic `$write` text and
`$finish`, which Yosys cannot use (khdl-lang/khdl#36). The adapter replaces each
`$finish` with a sticky per-module `khdl_assertion_failed` flag, asserted to
stay low, and removes only the diagnostic text. Every KHDL assertion is
therefore a proof obligation. The flag is visible one cycle after the failing
edge, so the proof runs one extra step. Unexpected lowering is rejected, and
normal RTL exports are unchanged.

## Protocol monitor

`axi4_protocol.sv` is written from the AXI specification (Arm IHI 0022H,
Part A), not from this implementation. Its header lists each rule with its
section. Rules on the DUT's side of an interface are assertions; rules on the
environment's side are assumptions. For a Subordinate DUT, the environment is
an arbitrary legal Manager; for a Manager DUT, an arbitrary legal Subordinate.
Assumptions therefore allow everything the specification allows, including
W before AW, different-ID read interleaving, reordered responses, narrow,
unaligned, FIXED, WRAP, and exclusive bursts, and any legal attributes.

The monitor tracks a bounded number of outstanding reads, writes, and W beats
sent before any AW is presented. Exceeding a bound is a failure for the DUT or
excluded for the environment; the bounds never silently drop checks. They are
harness limits, not AXI rules.

## Targets

| Target | Device under test | Checks |
| --- | --- | --- |
| `pipeline` | `AxiPipeline`, 8-bit data | Design contract: per channel, a two-entry FIFO of arbitrary payloads, in order, with stable stalled outputs and VALID low in and after reset. Inputs are not required to be legal AXI. |
| `error` | `AxiError`, two contexts | Protocol monitor as Subordinate; design contract: every R and B response is DECERR. |
| `manager` | `formal/manager.kh`: fixed-ID Manager with read and write endpoints for IDs 0 and 1, capacity two each | Protocol monitor as Manager. The facade's application inputs are arbitrary except for the API usage contract: push when allowed, only valid requests, strobes within the beat's lanes. |

`error` and `manager` use 12-bit addresses, 16-bit data, and one-bit IDs.

Each target has a negative control (`--inject-fault`) that must fail:
`pipeline` inverts the forwarded AW payload, `error` flips RLAST as seen by the
monitor, and `manager` marks AR bursts with the reserved BURST encoding. A
reachability run must find a trace where the assumptions permit useful
progress. It runs at the proof depth, or at the shallowest depth where the
trace exists if that is deeper (5 cycles for `error` and `manager`), because
finding a trace is much cheaper than a proof: for `error`, early W, overlapping requests, a completed multi-beat
read, and a write response; for `manager`, R beats of both IDs interleaved, a
completed multi-beat read, and a write response. The pytest suite also checks
that a failing KHDL assertion fails the proof, and that a passing one does not.

## Limits

Apart from the unbounded `pipeline` proof, these are bounded checks from
reset. A depth of `n` covers every behavior within `n` cycles after the
initial reset; long bursts are covered only up to that depth. The checks are safety properties: they do
not show that transactions complete (liveness), and they do not check data
integrity: that the fixed-ID Manager delivers each response to the right
endpoint, or `AxiRam`, the pooled Manager, `AxiRouter`, and `AxiCopy`, which
have simulation coverage instead. Exclusive-monitor semantics, attribute
meanings, USER contents, and X propagation are not checked.

ZipCPU's wb2axip publishes its full-AXI4 properties only as a reduced subset:
its header says it is not intended to be functional, parts are elided, it
instantiates a module that the repository lacks, and the complete set is sold
separately. The subset also assumes that W never precedes AW, which the
specification and these designs allow. The monitor here covers the
subset's protocol-rule categories, rewritten from the specification, except
bounded response delays and exclusive-monitor behavior.
