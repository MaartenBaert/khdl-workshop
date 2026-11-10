# AXI4 transaction APIs in KHDL

A reusable five-channel AXI4 library that stress-tests KHDL interfaces,
references, generics, and API-created state. Application code submits and
services transactions through FIFO-style operations; the APIs own handshake
storage, context capacity, ID association, burst progression, and LAST.

- [Configuration and payloads](rtl/axi/types.kh), [interface bindings](rtl/axi/interfaces.kh).
- [Manager](rtl/axi/manager.kh) and [Subordinate](rtl/axi/subordinate.kh) endpoints.
- [Generic channel endpoints](rtl/channel/endpoints.kh) (Transmitter, Receiver) and
  [storage](rtl/channel/buffers.kh) (Internal, Fifo).
- Demonstrations: [AxiPipeline](rtl/examples/pipeline.kh), [AxiRam](rtl/examples/ram.kh),
  [AxiError](rtl/examples/error.kh), [AxiRouter](rtl/examples/router.kh), [AxiCopy](rtl/examples/copy.kh).

Inside the owning clocked process:

```text
val descriptors = read_endpoint(C, id=0);
val payloads = read_endpoint(C, id=1, capacity=3);
val writes = write_endpoint(C);
val api = manager(C, ref(bus), reads=(descriptors, payloads), writes=(writes,));
api.begin();
if (want_read && descriptors.can_push()) {
    descriptors.push(read_request(C, address, beats=4));
}
if (payloads.can_pop() && application_can_accept) {
    val beat = payloads.pop();
    // Consume beat.data/resp/last/user.
}
api.end();
```

Call begin/end unconditionally each cycle. Endpoints have compile-time IDs and
independent context/response queues, with no exposed transaction tokens. Results
are ordered within an endpoint; different endpoints can complete and reuse storage
independently. The Manager shares the named objects through their internal references. Local
requests are forwarded at end with independent round-robin arbitration for AR and
AW, rotating in tuple order; Internal slots
allow forwarding at the same edge as submission. A small queue ensures W follows
actual AW arbitration order. Omitted directions are disabled.

Applications must eventually supply submitted write bursts without depending on
later writes completing, and drain responses without creating circular waits
through shared-channel backpressure. See the [progress contract](docs/manager.adoc#axi4-manager-progress).

[The pooled alternative](rtl/axi/pooled_manager.kh) remains available for forwarding
externally chosen IDs. It and the Subordinate API still expose context tokens,
with documented lifetime and ordered slot-reclamation restrictions. AxiRouter
uses that alternative; AxiCopy uses fixed-ID endpoints.

The [integrated manual](docs/index.adoc) starts with an AXI4 primer and the
shared programming model, then documents the [Manager](docs/manager.adoc) and
[Subordinate](docs/subordinate.adoc) APIs, the [feature matrix and reset
contract](docs/reference.adoc), [module parameters](docs/modules.adoc), the
[channel layer](docs/channels.adoc), and [verification limits](docs/verification.adoc).
AXI4 behavior follows Arm IHI 0022H, Part A; current terminology is
Manager/Subordinate for transaction roles and Transmitter/Receiver for channels.

From the repository root:

```bash
./setup.sh
.venv/bin/python -m projects.axi.tools.compile --check
.venv/bin/pytest projects/axi/tests
.venv/bin/python -m projects.axi.tools.compile
.venv/bin/python -m projects.axi.tools.formal --target manager
.venv/bin/python docs/build.py
```

Verilator is required for simulation, and Yosys for formal checks; ABC makes
them faster and enables unbounded proofs. Setup installs
cocotb and cocotbext-axi alongside shared Python dependencies. Tests combine
independent peers, arithmetic references, transaction scoreboards, negative API
checks, and adversarial response schedules. Private test facades are in
`tests/rtl/`; they are not part of the application API.

Exports go to `build/axi/verilog/`; the offline manual is `build/docs/axi.html`.
The [formal checks](formal/README.md) cover `AxiPipeline` transport, with an
unbounded proof, and `AxiError` and the fixed-ID Manager against an AXI4
protocol monitor written from the specification, with bounded checks. Each
target has a negative control, and the monitored targets a reachability trace.
Unbounded proofs of the monitored targets, liveness, and the remaining modules
are future work. Runtime
assertion lowering currently blocks direct Yosys synthesis; the formal tool
preserves the original RTL and uses an explicit assertion adapter.

AXI demonstration modules use active-high asynchronous `rst`; the flat test
adapter exposes active-low `aresetn`. Release reset synchronously and reset
connected endpoints together. Generic channel examples retain synchronous reset.
AxiRam uses register-array storage, undefined unwritten bytes, and no exclusive
monitor. AxiRouter uses a documented per-destination ID admission restriction.
AxiCopy requires aligned, non-overlapping regions. These examples do not cover
CDC, width conversion, ACE/AXI5, or a general crossbar.
