# Language practices

This is a working guide, not a complete language specification. Check the
compiler's tests for constructs outside these examples. Items marked
*(revision-dependent)* describe current limitations; recheck them on a new
compiler revision.

- [Modules, files, and imports](#modules-files-and-imports)
- [Generics and parameter structs](#generics-and-parameter-structs)
- [Compile-time function pipelines](#compile-time-function-pipelines)
- [Elaboration-time configuration and tables](#elaboration-time-configuration-and-tables)
- [Explicit elaboration checks](#explicit-elaboration-checks)
- [Interfaces for transactions](#interfaces-for-transactions)
- [Types and arrays](#types-and-arrays)
- [Compile-time bounds and justified assumptions](#compile-time-bounds-and-justified-assumptions)
- [Booleans and integer bit operations](#booleans-and-integer-bit-operations)
- [State, domains, and reset timing](#state-domains-and-reset-timing)
- [Ceiling division and packages](#ceiling-division-and-packages)

Reference-bearing API objects are covered separately in
[endpoint_apis.md](endpoint_apis.md).

## Modules, files, and imports

The manifest maps source namespaces to directories:

```toml
[source]
_ = "."
```

A file `counter.kh` in that directory gives items paths such as
`counter.Counter`. File-level items are private by default; use `pub` for
items imported from another source file. Struct fields, static members, and
methods, and interface fields and views, have no visibility modifier: they are
accessible through their exported type or interface. Keep file-local helpers
private. Imports name items:

```text
import common.[Word, transform];
```

A synchronous counter module:

```kh
pub module Counter(width: positive = 8) ports(
    clk: in clock,
    sync(clk) {
        rst: in bool,
        enable: in bool,
        count: out uint(width),
    }
) {
    clocked(clk, sync rst) {
        reg wire count = 0;
        if (enable) {
            count = (count + 1) & (2**width - 1);
        }
    }
}
```

Instantiation uses `instance`, with generic arguments and named ports:

```text
instance Counter(width=8) ports(clk, rst, enable=advance, count=value);
```

`ports(clk)` abbreviates `ports(clk=clk)`: it connects the port named `clk`
to the local item named `clk`, rather than matching ports positionally.
Use `name=expression` when the names differ. A domain block applies to all
ports inside it, including interface ports; keep the clock outside its own
`sync(clk) { ... }` block. `async { ... }` is the corresponding grouping for
asynchronous and combinational ports.

The comma after the final port inside an `async` or `sync` block is optional.
The block itself takes no following comma: write
`ports(async { output: out bool })`, not `ports(async { output: out bool },)`.

## Generics and parameter structs

Generic functions and modules can take values, types, and other callable
items. Ordinary functions use `fn name(args) -> ReturnType { ... }`.
Computations needed during elaboration use `const` or compile-time arguments.
The same function can be evaluated at compile time and synthesized when
called with hardware values, as long as its operations are supported in
hardware.

Group related elaboration-time parameters in a struct with constrained
fields, for example `struct Unroll { lanes: positive, steps: positive }`.
Construct a value with `Unroll.new(lanes=4, steps=2)` and declare the generic
as `unroll: Unroll`. Its fields can be used directly in port types and
submodule arguments. A default generic may construct the struct inline. The
value adds no ports or state when it is used only during elaboration.

*(Revision-dependent: call arguments, including named arguments, must follow
parameter declaration order. Supply intervening default values explicitly when
passing later arguments. Struct `.new(...)` constructors require named fields
in declaration order.)*

Struct instance methods declare `self` without a type annotation, for example
`fn cost(self, width: positive) -> uint { return self.a * width + self.b; }`.
Call them as `value.cost(4)`. This also works in elaboration-time models.

## Compile-time function pipelines

A function factory can return a nested function as `Function`. The returned
function can capture compile-time parameters, and arrays of `Function` can hold
callables with different hardware input/output types. A statically traversed
array can therefore transform an untyped mutable temporary through several
hardware types:

```kh
fn make_transform(index: uint) -> Function {
    fn apply(value: any) -> any {
        if (index == 0) { return (value, value + 1); }
        else { return value.0 + value.1; }
    }
    return apply;
}

pub module Transform ports(input: in async int(8), output: out async int(9)) {
    comb {
        val transforms = [make_transform(i) for i in 0..2];
        var intermediate = input;
        for (transform in transforms) { intermediate = transform(intermediate); }
        output = intermediate;
    }
}
```

The traversal and type changes happen during elaboration, with no runtime
function dispatch. An explicit type annotation on the mutable temporary would
fix its assignment constraint, so leave that temporary untyped. Arrays of
`type` values can separately describe boundary types for a generic pipeline;
select their elements at compile-time indices when declaring interfaces.

A callable that declares registers inside a clocked caller owns state for that
expanded call. Use one canonical call site per algorithm step and pass enable
and functional-clear conditions into it. Separate data and clear call sites
would allocate separate state. Clock-stage grouping can then change independently
of the algorithm functions, while the functions control when history advances.

## Elaboration-time configuration and tables

Functions can return types, including a local struct whose static members
capture the function arguments. A type with no fields serves as a
configuration namespace that combines derived constants, types, functions,
and tables without runtime storage:

```kh
pub fn configuration(width: positive, length: positive) -> type {
    assert(length % 2 == 0, "length must be even");
    struct Config {
        const width = width;
        const length = length;
        type Symbol = uint(width);
        fn make_table() -> [length]Symbol {
            return [i % 2**width for i in 0..length];
        }
        const table = make_table();
    }
    return Config;
}

pub type Default = configuration(8, 16);

pub module Lookup(C: type = Default) ports(
    async {
        index: in uint(0..C.length),
        value: out C.Symbol,
    }
) {
    comb { value = C.table[index]; }
}
```

Use members directly in port types, bounds, and expressions, including calls
such as `C.multiply(a, b)`, instead of a prologue of local aliases.
Namespaced members need no repeated project prefix. Avoid exporting bare
aliases to members of a default configuration, which can bypass the selected
configuration. Pass the same configuration type into generic interfaces and
submodules. Interface identity includes the configuration argument, so
interfaces from different configurations cannot be mixed accidentally even
when their field sizes match.

Define struct member helpers before member constants that call them. A struct
body is a sequential scope, unlike a file: an unqualified call to a
later-declared member reports `undeclared identifier`.

Compile-time functions support mutable arrays and loops, so tables can be
built with ordinary algorithms instead of emitted source literals. Validate
configurations during elaboration, including algebraic properties that
integer ranges cannot express. Use `const { assert(...); }` in a module for
its specialization restrictions; these checks add no circuit state.

Member constant initializers and const blocks run when the type is
constructed, even if a member is never read: an unused member
`const invalid = 1 / 0;` fails at the factory call. Function bodies and
uncalled generic declarations remain deferred. Validate inputs before
expensive table construction, and cache the returned type when specializing
many modules with the same configuration.

## Explicit elaboration checks

Generic modules are callable. A file-level const block can call them to check
representative specializations without instantiating them in a hierarchy:

```kh
const {
    Counter(width=8);
    Counter(width=16);
}
```

The LSP's all-items compilation treats these blocks as elaboration roots and
checks the resulting module bodies and their instantiated hierarchy. These
checks cover only the chosen arguments. The Python API compiles lazily and
does not run file-level const blocks; call the factories explicitly there.

Const blocks can also appear in generic parameter lists, which keeps
constraints next to the parameters:

```kh
module Example(
    a: int, b: int,
    const { assert(a > b, "Expected a > b"); }
) ports() {}
```

Calling `Example(3, 4)` fails during parameter elaboration. *(Revision-dependent:
the LSP may not publish diagnostics whose primary location is in the standard
library, including failed `assert` calls. Keep a shell or Python check for
these failures.)*

## Interfaces for transactions

Group related signals in an interface and give it producer and consumer
views. Payload, framing, and `valid` travel forward; `ready` travels backward:

```kh
pub struct Packet(width: positive) {
    symbols: [width]uint(8),
    last: bool,
}

pub interface Transaction(T: type) {
    data: T,
    valid: bool,
    ready: bool,
    view Source { data: out, valid: out, ready: in }
    view Sink { data: in, valid: in, ready: out }
}

pub module Relay(T: type) ports(
    clk: in clock,
    sync(clk) {
        input: interface Transaction(T).Sink,
        output: interface Transaction(T).Source,
    }
) {
    comb {
        output.data = input.data;
        output.valid = input.valid;
        input.ready = output.ready;
    }
}
```

Use dotted fields in the module, e.g. `input.valid`. Register an output field
with `reg wire output.data = undef;` inside the clocked process. A view only
declares directions: it does not implement buffering or protocol behavior,
and a module must still hold payload and valid through a stall.

Connect a complete transaction between two instances:

```text
wire link: interface Transaction(Packet(4));
instance Relay(Packet(4)) ports(clk, input, output=link);
instance Relay(Packet(4)) ports(clk, input=link, output);
```

An interface wire uses the interface itself, without a view. Its field types
come from the declaration and its domain is inferred from the connected
modules. Port views must use the same interface specialization. Interfaces
can have generic parameters and conditional fields and views.

The backend flattens interface fields with underscores: `input.data` becomes
`input_data` in Verilog. Choose interface and field names that preserve an
existing external API when porting a design.

Prefer one payload-typed interface for a shared ready/valid protocol. `T` can
hold stage-specific arrays, constrained integers, and framing or status, and
routers forward `output.data = input.data` as a whole value. This keeps the
protocol explicit and avoids field-name lists. Register a whole struct output
with a constructor reset value, and build results with the same constructor:

```text
reg wire output.data = Packet(4).new(symbols=[0] * 4, last=false);
output.data = Packet(4).new(symbols=current_symbols, last=current_last);
```

Supply every constructor field. Struct generic arguments preserve type
identity, even when specializations have identical layouts.

Generic modules can accept `I: Interface` and use `I.Source`/`I.Sink` in
ports, but there is no interface-field reflection. *(Revision-dependent:
interfaces cannot contain other interfaces. For a composite protocol, use
typed payload signals in one flat interface; see endpoint_apis.md for binding
reusable channel code to such signals.)*

Instance `ports(...)` loops preserve their iteration scope, including computed
connection names. Computed identifiers use `ident(expression)`, for example
`ident("lane_{i}")` in an elaborated loop; the string is evaluated at compile
time; the compiler's `tests/module/test_dynamic_id.py` shows generic ports and
interface fields. When a wire created inside a module-body `for` or `if` must be accessed
from a later outer scope, declare it `pub wire ident("link_{i}")...`. This is
local elaboration visibility, distinct from file-level `pub`. A bank can
declare public links and instantiate workers in one loop, then connect them in
another. Computed ports add no storage; keep forwarding in `comb` when routing
must add no pipeline cycle.

## Types and arrays

- `uint(8)` is an 8-bit unsigned integer, range 0 through 255.
  `uint(0..8)` instead means the values 0 through 7, **not** 8 bits.
- Ranges use `..` for exclusive ends and `..=` for inclusive ends. Prefer
  `0..N` over `0..=N - 1` in loops, slices, and type bounds. Keep inclusive
  syntax when the endpoint itself is meaningful, e.g. `uint(0..=limit)` for a
  count that can equal `limit`.
- Bare `uint` means integers starting at zero. `positive` means `int(1..)`,
  `negative` means `int(..0)`, and `nonzero` means `int(..0, 1..)`. Use
  `positive` to reject zero before elaborating a generic body. Hardware
  storage needs a finite integer range.
- `[N]T` is an array and `[N][M]T` a nested array. Its length is `array.len`,
  not `len(array)`. Array types expose `.len` and `.inner` (element type).
- `a[lo..hi]` excludes `hi`; `a[lo..=hi]` includes it. Slice assignments
  evaluate the right-hand value first, so overlapping slices work.
- `[0] * N` repeats an array. Parenthesize computed counts: `[0] * (2 * N)`.
- Spread uses `[*array]`, and comprehensions use
  `[expression for i in 0..N]`.
- `for (i in 0..N) { ... }` unrolls during elaboration. Empty ranges work,
  which matters for width-one specializations. For descending order, use
  `for (r in 0..N) { val i = N - 1 - r; ... }`.
- Dynamic indexing and arithmetic must fit the known bounds; see the next
  section.

## Compile-time bounds and justified assumptions

Try a direct assignment before adding bounds assertions. Reset constants and
same-range copies need none, and compile-time loop indices carry precise
ranges, so index arithmetic on them can be assigned directly to constrained
temporaries.

Control flow narrows values. This counter needs no bounds assertion:

```kh
pub module Progress(length: positive) ports(
    clk: in clock,
    sync(clk) {
        rst: in bool,
        advance: in bool,
        count: out uint(0..length),
    }
) {
    clocked(clk, sync rst) {
        reg wire count = 0;
        if (advance) {
            if (count == length - 1) {
                count = 0;
            } else {
                count = count + 1;
            }
        }
    }
}
```

Use `const { assert(condition, "reason"); }` for generic restrictions. For an
unrolled algorithm, assert divisibility before constructing iteration bounds.
Align container types with the values they hold: lane indices for a width `W`
fit `uint(0..W)`.

*(Revision-dependent: with hardware values, narrowing does not continue after
an early `return`. Write `if (i == n - 1) { return 0; } else { return i + 1; }`
rather than returning early and falling through to `return i + 1;`.)*

Range analysis tracks intervals, not relations between values. Under
`2 * degree <= iter`, `iter + 1 - degree` is nonnegative, but the compiler
still infers a range that includes negatives. Add a **local** `assert_assume`
for only the missing constraint and explain why it holds. It emits a runtime
assertion and narrows the type; it is not an unchecked cast. Avoid a generic
`bounded` helper that assumes both bounds without documenting the invariant.

Carry bounds through generic interfaces, including pass-through stages:
declare lane positions as `[N]uint(0..W)` rather than a fixed byte-sized array
when the algorithm defines the width. Producer and consumer interface
specializations must match exactly, even when one range would fit into the
other. A width-one lane has the singleton range `0..1` and a zero-bit
representation, which works.

Do not widen a temporary, or introduce modulo or clamping, merely to bypass
an unproven contract. Use wrapping only when the algorithm wraps. A full-width
`uint(W)` does not wrap implicitly: assigning `x + 1` back to that type is
rejected when overflow is possible. A guarded terminal case permits a bounded
counter without modulo. For deliberate power-of-two wrapping, mask:
`(x + 1) & (2**W - 1)` has an inferred range that fits `uint(W)`. Modulo by
`2**W` is also accepted, but the Verilog backend may keep `%` and leave
strength reduction to downstream tools. Masking a result does not waive bounds
on intermediate assignments or array accesses; give temporaries adequate
ranges and guard actual indexing.

Inside a process, `else { result = undef; }` is accepted for a temporary whose
value is discarded on that path. Keep such values unobserved; controls and
padding still need defined values.

## Booleans and integer bit operations

Comparisons return `bool`; use them directly for boolean ports and registers.

`==` and `!=` support integers, booleans, arrays, tuples, structs, and enums.
Aggregate comparison recurses over elements and fields. Compile-time values
also support comparison of types, functions, interfaces, and packages.
Logical `&&`, `||`, and `^^` operate on scalar booleans. *(Revision-dependent:
`&&` and `||` evaluate both operands, even during constant evaluation, so they
cannot guard an invalid division, index, or asserting call on the right. Use a
separate `if`.)*

Bitwise `&`, `|`, and `^` support integer pairs, boolean pairs, equal-length
boolean arrays, and a boolean scalar with a boolean array, which broadcasts.
`!` is logical NOT for booleans, elementwise NOT for boolean arrays, and
two's-complement complement for integers: `!x = -1 - x`. Thus
`!uint_value` is negative; mask explicitly when a fixed width is intended.

```kh
pub fn xor_byte(a: uint(8), b: uint(8)) -> uint(8) {
    return a ^ b;
}
pub fn enabled_bits(bits: [8]bool, enable: bool) -> [8]bool {
    return bits & enable;
}
```

Integer bitwise results carry inferred bounds, so constant masks narrow
ranges without unsafe conversions. Shifts use `<<` and `>>`; a left shift
widens the value rather than truncating it. Signed operands use infinitely
sign-extended two's-complement semantics.

`T.to_bits` is least-significant-bit first, and `T.from_bits` reverses it,
with the target type setting the width. Use `T.size_bits` for bit-array loop
bounds. Safe `from_bits` requires every bit pattern to be valid for the
target, which constrained ranges such as `uint(0..10)` do not satisfy. Prefer
direct integer assignments checked against known bounds over
`from_bits_unsafe`.

For arithmetic networks, combine native word and array operations with
elaborated loops. For example, `bits & enable` masks a partial product
without a per-bit loop, and integer `^` accumulates it after shifting. Helper
functions inline into hardware; avoid per-bit conversion wrappers. Shorter
source does not necessarily produce smaller hardware or faster builds.

## State, domains, and reset timing

- `wire` connects hardware values and supports inference: `wire data;` infers
  type and domain from its driver, `wire data: uint(8);` fixes only the type,
  and `wire data: sync(clk);` only the domain. Write
  `wire data: sync(clk) uint(8);` for both. Inference needs the driver to be
  elaborated first; a read before that point needs explicit annotations.
  `reg wire data = undef;` also needs a known type, which `undef` cannot supply.
- `comb { ... }` drives combinational outputs. `reg` is persistent state,
  `var` a mutable local, `val` an immutable local, and `const` a compile-time
  value.
- Mark a port or wire driven by a clocked process with
  `reg wire output = reset_value;` inside that process. Plain assignments to
  an unmarked output from `clocked` are rejected.
- Assignments immediately affect later reads in the same process, including
  reads of registers. To implement a delay, capture `val previous = r` before
  updating `r`.
- For synchronous reset use `clocked(clk, sync rst)`, with `rst` typed
  `sync(clk) bool`. Register initializers are reset values, not power-on
  initialization. Reset is sampled at the clock edge and skips the whole
  process body, including final output assignments, so registered outputs need
  their own reset values even when they normally mirror internal registers.
- Use `undef` for reset values that do not matter. *(Revision-dependent: the
  compiler omits reset assignments for these registers, so they hold their
  value during reset.)* Express intent, and do not depend on a hold versus an
  X. A `clocked(clk)` block without reset cannot drive a register with a
  defined initializer.
- For asynchronous reset, declare `rst: in async bool`, data ports
  `sync(clk, async rst)`, and use `clocked(clk, async rst)` with defined reset
  values. This is a different domain and reset behavior; do not substitute it
  for synchronous reset when porting.
- Reading `async` data inside plain `clocked(clk)` is rejected. Declare the
  intended synchronous domain rather than suppressing the error with
  `unsafe_value_with_domain`, and use a real synchronizer where the hardware
  crosses domains.
- Immediate assignment applies within a process. Communication between
  instances still follows hardware scheduling and the declared clock domains.

## Ceiling division and packages

`a +/ b` is signed ceiling division; `a / b` is floor division. The divisor
must exclude zero. `+/` has multiplicative precedence and associates to the
left: write `(a + b) +/ c` for a sum in the numerator and `a +/ (b + c)` for a
sum in the divisor.

Imports bind package values, so `import lib;` permits `lib.Type` and
`lib.fn()`, and `import lib.[Type, function];` binds selected public items.
Packages can be passed to compile-time functions. Package members include
public items and child packages, but not another file's imports. The
prelude's `Package` type describes package values.
