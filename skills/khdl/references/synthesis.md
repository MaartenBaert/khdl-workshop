# Synthesis-friendly design patterns

KHDL loops and recursive functions can describe hardware with very different
costs despite similar source size. Reason about the elaborated data
dependencies, and inspect the generated hardware when cost or timing matters.

- [Static traversal versus dynamic indexing](#static-traversal-versus-dynamic-indexing)
- [Restricted window selection](#restricted-window-selection)
- [Two-pass round robin](#two-pass-round-robin)
- [Recursive balanced round robin](#recursive-balanced-round-robin)
- [Integration](#integration)

## Static traversal versus dynamic indexing

A loop unrolled at elaboration does not make its indices constant. For example,
`i = (last + 1 + offset) % count` still depends on hardware `last`, even when
`offset` is an elaboration constant. Reading `request[i]` inside every iteration
can replicate selection muxes, rotation logic, and non-power-of-two modulo.
Runtime writes similarly need decoding, and potentially arbitration between
unrolled writes. Do not assume synthesis will recover the intended arbiter or
memory organization from this form.

Prefer static traversal with hardware eligibility masks when selecting among
endpoints or lanes: each iteration accesses a fixed member, and a comparison
such as `i > last` controls its participation. Heterogeneous tuple iteration
naturally preserves each member's concrete type. This removes dynamic member
selection, but a running `found`, `ready`, or accumulator can still form a long
combinational chain. Balance reductions and selection when that chain matters.

Dynamic indexing is appropriate for actual runtime random access, particularly
when it maps to the intended RAM or register file. Consider port count, storage
inference, payload width, fanout, and the number of simultaneous accesses.
Static traversal is an alternative to evaluate, not a universal ban on indexing.
*(Revision-dependent: arrays lower to packed Verilog vectors, from which
synthesis tools do not infer RAM, so array storage currently becomes flip-flops
and multiplexers.)* Still write storage in its natural form; it can map to RAM
once the compiler supports memories.

## Restricted window selection

Dynamic indexing can be the right circuit when every entry is selectable. When
only a small subset of positions is legal, expose that subset explicitly rather
than expecting synthesis to recover it from arithmetic on a packed-vector index.
For a shared shift across many lanes, compare enumeration with a barrel network:
independent enumeration can build a priority chain, while a staged window shares
selection work between overlapping output lanes.

This helper selects a window from `choices` equally spaced starting positions.
`width`, `choices`, and `stride` are compile-time values; `selection` and `values`
may be hardware. Each recursive step halves the power-of-two choice count,
selects one of two fixed slices, and narrows the window. All source connections
are static. Array element types and zero-length windows are preserved.

```kh
pub fn select_window(width: uint, choices: positive, stride: positive,
    selection: uint(0..choices), values: any) -> any {
    assert(2**clog2(choices) == choices, "Window choices must be a power of two");
    assert(values.len >= width + (choices - 1) * stride, "Window input is too short");
    if (choices == 1) { return values[0..width]; }
    else {
        // Select the high offset bit first, then progressively narrower
        // windows. This is a logarithmic barrel network with fixed wiring.
        const half = choices / 2;
        const inner_width = width + (half - 1) * stride;
        var window = values[0..inner_width];
        if (selection >= half) {
            window = values[half * stride..half * stride + inner_width];
        }
        return select_window(width, half, stride, selection % half, window);
    }
}
```

The network has logarithmically many window-selection stages. Mapped cell count
and gate depth still depend on the selector logic and synthesis flow; measure
both. A direct array index may beat this network for unrestricted selection.

*(Revision-dependent synthesis quality: the current backend lowers array reads
to packed indexed part-selects. With non-power-of-two element widths, synthesis
can retain a general bit-routing network instead of recovering word selection,
even when all entries are legal. Check the emitted Verilog and compare a fixed
word mux before attributing that cost to the required algorithm.)*

## Two-pass round robin

Visit the endpoints twice in their static order:

1. First pass: consider only indices greater than the previous winner.
2. Second pass: consider every index, starting at zero.
3. Accept only the first eligible candidate across both passes.

This implements wraparound without adding a runtime offset or modulo to an
index. Snapshot the previous winner for the first pass's mask. The second pass
can be unmasked because the first accepted grant closes selection.

[round_robin.kh](../assets/round_robin.kh) implements
`round_robin_linear(count, request, last) -> Selection(count)`; its `found`
accumulator provides the first-grant exclusion. For reference-bearing
endpoints, a generic tuple walker with a grant callback works the same way:

```kh
fn arbitrate(endpoints: any, last: any, grant: any) {
    val previous = deref(last);
    for (pass in 0..2) {
        var index = 0;
        for (endpoint in endpoints) {
            if (pass == 1 || index > previous) {
                if (grant(endpoint)) { deref(last) = index; }
            }
            index += 1;
        }
    }
}
```

Here `last` is an explicit reference to state. The callback must return true
only when it accepts a request, perform no transfer when it returns false, and
make later calls unable to accept another request in the same cycle. A shared
one-slot output provides this property: its first push immediately makes
`can_push()` false. A nested callback can capture that output and specialize
for differently typed tuple members, with no runtime tuple lookup or erased
endpoint representation. The linear priority chain suits small banks; check it
before scaling to many endpoints.

## Recursive balanced round robin

For larger banks, separate request collection, winner selection, and dispatch.
Collect eligibility into a boolean array using static accesses. Two useful
implementations are a pair of ordinary priority trees, or a single tree whose
candidates carry an extra priority flag.

### Two full-width trees

Keep both candidate arrays at the full `count` width. One contains all requests;
the other clears the request bits at constant indices `<= last`. Find the first
request in each full array, then prefer the masked winner if present. Both calls
cover `[0, count)`: do not shorten an array, change recursion bounds, or slice a
candidate set using hardware `last`. The mask changes eligibility, not topology.

In [round_robin.kh](../assets/round_robin.kh), `first_request` recursively splits
a **compile-time** half-open interval `[lo, hi)`:

```text
if (hi - lo == 1) {
    return Selection(count).new(found=request[lo], index=lo);
} else {
    val middle = (lo + hi) / 2;
    val left = first_request(count, request, lo, middle);
    val right = first_request(count, request, middle, hi);
    var result = right;
    if (left.found) { result = left; }
    return result;
}
```

Both children are computed independently before the parent chooses a winner.
Each call receives a strictly smaller static interval, and the recursion ends
at one leaf. Odd lengths need no padding, and every array access is to a
statically known leaf. The division computes an elaboration constant; it does
not instantiate a runtime divider. Recursive bounds that depended on hardware
values would not describe a finite static tree.

`round_robin_tree` calls this helper for the two full-width request arrays,
then selects between their results. Each priority tree has logarithmic merge
depth and a linear number of nodes.

### One tree with ranked candidates

`round_robin_single_tree` instead uses an internal `TaggedSelection` that adds
`after` to `found` and `index`. At each leaf:

- `found = request[lo]`;
- `after = request[lo] && lo > last`, so `after` implies `found`;
- `index = lo`.

Each internal node receives independently computed left and right results. An
`after` candidate outranks a wraparound candidate. Within either class, the left
candidate has the lower index and wins ties:

```text
var index = right.index;
if (left.after || (left.found && !right.after)) { index = left.index; }
return TaggedSelection(count).new(found=left.found || right.found,
    after=left.after || right.after, index=index);
```

The OR reductions preserve whether any valid or after-last request exists in
each subtree, while the index names the best such request. The wrapper returns
the same public `Selection` as the other implementations. This uses one tree
and avoids duplicate index-selection trees, at the cost of a richer merge.
Synthesis may share logic between the two-tree implementation's trees too, so
fewer source nodes do not guarantee fewer cells or a shorter mapped path.
Compare the mapped circuits for the sizes that matter.

All three functions work in compile-time calls and as combinational hardware.

## Integration

- Encode actual request eligibility. Selection must not consume requests or
  reserve resources speculatively; dispatch the winner once resources permit.
- Update the previous-winner state only on a successful grant. A grant can mean
  enqueueing into a holding output register; it need not mean an external
  handshake. Preserve the held payload through backpressure.
- Initialize `last = count - 1` to start at endpoint zero. No increment or
  wrap counter is required: the state is assigned a selected constant index.
  Use independent state for independently arbitrated resources.
- The selectors require positive `count`. Handle empty endpoint collections at
  the caller. A singleton selector needs only a constant index.
- `Selection.index` is meaningful only when `found` is true.
- Dispatch heterogeneous endpoints by a second static traversal gated with
  `winner.found && winner.index == i`. Balance the payload path separately:
  side-effecting callbacks or repeated readiness updates can reintroduce a
  serial chain even after balanced winner selection.
- Test no requests, wraparound, skipped indices, simultaneous requests,
  singleton and odd sizes, output stalls, reset, and independently stalled
  arbiters. Check sustained contention for starvation, not just isolated
  winner examples.
- Fair arbitration does not resolve circular dependencies involving ordered
  data channels or bounded response storage. Keep those progress requirements
  explicit alongside handshake safety.
