# AXI API experiments

Read the shared [repository instructions](../../AGENTS.md) and
[KHDL skill](../../skills/khdl/SKILL.md). This project contains reusable
FIFO-style channel APIs and a five-channel AXI4 transaction library.

The purpose is to exercise interface references, reference-bearing structs,
API-created registers, and reusable transaction-level programming. Keep public
application code independent of buffering latency and traversal order.

- `rtl/khdl.toml` maps subdirectories to namespaces: `channel.*` for the generic
  layer, `axi.*` for the AXI4 API, and `examples.*` for demonstration modules.
- `rtl/channel/endpoints.kh`: Channel, Transmitter, Receiver and their binding
  functions. `rtl/channel/buffers.kh`: Internal, Fifo, and `storage`. Public code
  uses begin/end, availability, push/pop/peek operations.
- `rtl/channel/ref_self.kh`: isolated mutable-receiver relay prototype with
  ordinary per-cycle lifecycle state; queue-array APIs remain unchanged.
- `rtl/channel/examples.kh`: private forwarding, composition, method probes,
  and explicit elaboration roots for Python and the LSP.
- `rtl/axi/types.kh`, `interfaces.kh`: configuration, payloads, flat bus and role bindings.
- `rtl/axi/manager.kh`: fixed-ID logical endpoints with independent contexts/responses.
- `rtl/axi/pooled_manager.kh`: explicit shared-pool Manager for arbitrary-ID forwarding.
- `rtl/axi/subordinate.kh`: incoming requests, contexts, IDs, burst lifecycle.
- `rtl/axi/burst.kh`, `context.kh`: shared validation/address/lane and context helpers.
- `rtl/examples/{pipeline,ram,error,router,copy}.kh`: applications built on those APIs.
- `rtl/examples/checks.kh`: concrete elaboration/LSP profiles.
- Keep the layering: `channel` imports nothing from `axi`, and `axi` imports
  nothing from `examples`.
- `tools/compile.py`: hierarchy checks and example exports.
- `tools/axi_wrapper.py`: flat-pin test interoperability adapter, not API codegen.
- `tools/formal.py`, `formal/`: bounded transport/transaction safety and explicit assertion adapter.
- `tests/rtl/`: private KHDL test facades, injected only by tests.
- `tests/`: independent scoreboards, FIFO contracts, rate checks, reset,
  backpressure, lifecycle assertions, and negative elaboration checks.
- `docs/index.adoc`: manual root, included in the shared offline build. Its
  chapters run from high-level to low-level: AXI4 primer, programming model,
  Manager, Subordinate, pooled Manager, reference (configuration, burst rules,
  feature matrix, reset), modules, channel layer, and verification. Keep that
  order: explain a concept before relying on it, and keep the user-facing APIs
  understandable without prior AXI4 knowledge.

Use Transmitter/Receiver for channel roles and Manager/Subordinate for
AXI transaction roles (Arm IHI 0022L section A1.3). Follow these contracts:

- Bind endpoints inside one clocked process. Call begin/end unconditionally
  once per endpoint per cycle, surrounding conditional transaction-level work.
- Transmitter.begin retires a transferred output. push fills its registered
  output slot and immediately makes can_push false.
- Receiver.begin captures a handshake into its skid slot. can_pop/peek/pop
  observe that captured value immediately; pop empties it and sets ready true.
- Reset sets valid false and ready true. Input accepted during reset is dropped;
  invalid payloads and skid/internal data reset to undef.
- Internal is the default lightweight stage: one payload slot and valid flag,
  with immediate cross-side visibility allowing push/pop chains. Its begin/end
  only enforce lifecycle. Chaining can grow combinational paths.
- Fifo guarantees immediate observations on each side independently.
  Generic application code MUST NOT rely on push enabling pop or pop enabling push in
  the same cycle. The current implementation snapshots each side's availability
  and the head payload at begin, supports one push/pop per cycle, and commits
  occupancy at end. Depth is at least two to sustain one beat per cycle.
- Snapshotting the head before writes avoids false write-through data paths
  through forwarding loops. Keep both availability and data boundaries intact.
- Higher-level forwarding tests compare transactions across different queue
  counts, depths, storage implementations, and traversal orders. Exact timing checks belong to tests of
  a specific implementation, not to application correctness arguments.
- Public misuse checks use plain runtime assert, with guarded state updates;
  assertions do not replace flow control. The backend currently emits unguarded
  simulation $finish tasks, which block direct Yosys synthesis (khdl#36).

API structs use the local compiler's transparent `ref` fields; binding
functions still accept explicit references. Reads and writes use ordinary
field/index syntax. Copying objects preserves aliases, and `ref(object.field)`
forwards the target reference. Do not replace this with general auto-dereference.
References stay inside their declaring module; module instances use normal
interface ports. RefInterface cannot encode a view at this revision. Binding
functions declare role-specific output registers and reject wrong-direction
bindings through those declarations. Runtime lifecycle checks catch common
misuse but cannot replace the caller's unconditional begin/end discipline.

From the repository root:

```bash
.venv/bin/python -m projects.axi.tools.compile --check
.venv/bin/pytest projects/axi/tests
.venv/bin/python docs/build.py
```

Use shared test_support without modifying its driver for project-local API
experiments. Keep new language/compiler findings in the local `notes/khdl-feedback.md`,
synthesis/formal experiment results in `notes/axi/`,
and reusable reference/register/assertion patterns in the skill.

The ref-self prototype uses mutable whole endpoint variables and passes explicit
references to forwarding helpers. It creates no active-state registers. It
cannot detect a missing end in the next cycle, and constant lifecycle misuse
may fail elaboration. Indexed receivers remain unsupported by the limited
compiler feature, so do not migrate the production queue-array API yet.
Mixed hardware/non-hardware structs are intended to work; the current
assignment limitation (khdl#38) will be fixed later. Until then,
use register references for hardware-valued per-cycle scratch and mark them
`TODO(khdl#38)` for conversion to ordinary fields. FIFO head/availability
and lifecycle flags are scratch; payload/protocol storage, indices, occupancy,
and other actual cross-cycle state remain register-backed. Production migration
also awaits indexed mutable receivers. Do not expand the compiler PRs to fix it.

## AXI transaction contracts

Follow `docs/reference.adoc` for the feature matrix and `docs/modules.adoc` for module limits. Use IHI 0022H
Part A for AXI4 functionality; Issue L establishes current terminology.

- Combined Manager/Subordinate begin/end owns all five child channels. Bind once
  per process and do not duplicate ownership through aliased copies.
- Create named `read_endpoint(C, id=..., capacity=..., response_depth=...)` and
  `write_endpoint(...)` objects first. Pass tuples, including `(endpoint,)` for
  one member, to `manager(C, ref(bus), reads=..., writes=...)`. Its fields alias original
  register references. Omitted
  directions are disabled. Manager alone owns begin/end of attached endpoints.
- Each endpoint owns a unique ID per direction, independent capacity and response
  storage. Same-endpoint results are ordered; other endpoints complete and reclaim
  storage independently. Read/write may share IDs; incorrect roles fail elaboration.
- Local Internal request/data slots decouple endpoint creation from physical bus
  wiring. `api.end()` forwards new work at the same edge when possible. Shared
  AR/AW slots have independent round-robin state, reset to serve endpoint zero
  first. Use two static tuple passes (first masked after the previous winner),
  without modulo or runtime indexing. Advance only on enqueue into the shared
  output register; preserve its payload through external stalls.
- Queue W ownership when AW is arbitrated into the physical output register.
  Release it when final W is forwarded there, independently of B. Local write
  data submission does not imply winning shared W arbitration. W never depends
  on AWREADY and never interleaves bursts.
- Response slots are local per endpoint; finite buffers still permit shared-bus
  head-of-line blocking when a consumer stops. Do not promise unlimited isolation.
- Applications must eventually supply each submitted write burst independently
  of later writes completing, and consume results without circular waits through
  shared response backpressure. Fair arbitration does not prove these liveness
  obligations. See the manual's application progress contract.
- The explicit `pooled_manager` and Subordinate retain token-based contexts and
  allocation-ordered reclamation. Tokens are separate from IDs; expire them at
  documented completion/reset. No generation check protects stale use after reuse.
- Request reservation, external transfer, and local completion are distinct. All
  combined bindings own their child lifecycle. Do not manually begin/end endpoints.
- Subordinate B requires AW and all W consumed. Separate W scheduling from B
  delivery. Error responses drain the full burst. Assertions diagnose malformed
  peers/local misuse, not supported recovery behavior.
- AXI modules use active-high asynchronous rst; external test pins use ARESETn
  inversion. Release synchronously, reset connected endpoints together, cancel
  contexts, clear VALID. Generic channel examples keep synchronous reset.
- AxiRam is register-array storage with undef unwritten data, read-before-write
  collision semantics, and compliant nonmonitoring exclusive behavior (OKAY).
  Reads and writes share one address-based range rule; an out-of-range beat
  returns DECERR without reading or writing memory.
- Managers diagnose a B response whose final W beat is still local or in the
  W register, using existing cursor/order state only. Keep peer checks free of
  extra registers unless the user approves the hardware cost.
- AxiRouter's per-destination/per-direction current-ID admission window avoids
  full-burst reorder storage; global response order can block later work.
- AxiCopy accepts aligned, non-overlapping in-range commands; errors can leave
  partial copies, but issued obligations always drain.
- Test independent peers as well as connected endpoints. Use custom schedules
  for different-ID reordering and repeated IDs; ordinary RAM peers are insufficient.
- Formal coverage is bounded five-channel transport and small two-beat error-target
  transaction safety, not general transaction-wide or liveness proof. Preserve assertions in the formal adapter and require its
  corrupted-payload negative control to fail. Do not claim direct synthesis or
  technology timing from these checks.

Run `.venv/bin/pytest projects/axi/tests` after API changes and the full suite
for shared channel/tooling changes. Setup installs cocotb/cocotbext-axi; formal
checks require Yosys. Record compiler findings without expanding pending compiler
PRs. Small flat signal-reference facades work around khdl#40 without changing transaction semantics.
