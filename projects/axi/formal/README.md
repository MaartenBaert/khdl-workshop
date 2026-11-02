# Bounded transport safety

From the repository root:

```sh
.venv/bin/python -m projects.axi.tools.formal --depth 12
.venv/bin/python -m projects.axi.tools.formal --target error --depth 8 --out build/axi/formal-error
.venv/bin/pytest projects/axi/tests/test_formal.py
```

Yosys SAT checks all five channels of a small `AxiPipeline` simultaneously.
Independent two-entry scoreboards assert capacity, absence of phantom outputs,
payload ordering, and stable stalled outputs. The peer is constrained to hold
stalled inputs. Reset is asserted for the first cycle and then released;
clock edges are explicit. Mid-transaction asynchronous reset is covered in
simulation rather than by this bounded harness. No fairness is assumed.

The pytest check includes a negative control that inverts the forwarded AW
payload: the scoreboard must reject it and produce a counterexample.

The error-target harness checks a small AxiError with two context slots, one-bit
IDs, and two-beat read/write INCR bursts. Independent scoreboards assert response
counts, IDs, LAST, DECERR, B dependencies, stalled stability and reset. Address
attributes are fixed to legal byte-wide accesses; requests, IDs, channel skew and
response stalls are otherwise arbitrary. Reservations bound the scoreboard to
four outstanding requests per side and four early W bursts. W may precede AW.
A separate SAT reachability check requires early W, overlapping requests, final
R and B transfers in one trace; its VCD prevents a vacuous progress claim.

These are bounded safety checks, not an unbounded proof or full AXI4 transaction
verification. Arbitrary lengths, Manager response interleaving, routing and memory
semantics have independent simulation tests; broader proofs remain future work.
Progress is demonstrated by a reachable trace, not guaranteed for every schedule. ZipCPU's public full-AXI properties are also
incomplete, so importing them would not establish complete protocol coverage.

KHDL currently lowers assertion failures to `$finish`, unsupported by Yosys's
synthesis frontend. The tool preserves `original.v` and writes an explicit
formal-only copy, replacing each guarded `$finish` with `assert(1'b0)` and
omitting only diagnostic `$write` text. No checks are silently removed, and
normal RTL exports are unchanged. The adapter rejects unexpected lowering.
Generated scripts, RTL, logs and counterexamples belong under ignored
`build/axi/formal/` or pytest temporary directories.
