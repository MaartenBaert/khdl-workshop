---
name: khdl
description: Implement, port, compile, simulate, and debug hardware with KHDL, including its Python bindings and VS Code language server. Use for KHDL source files and tooling; consult the checked-out compiler because language examples can be outdated.
---

# KHDL

Use the name **KHDL** ([upstream](https://github.com/khdl-lang/khdl)). Source
files use the `.kh` extension; the project manifest is `khdl.toml`.
Use UpperCamelCase for modules, interfaces, views, structs, and user-defined
types, and lower_snake_case for functions, variables, namespaces, and
filenames.

KHDL is changing quickly. Make decisions against the compiler revision the
project uses, not against remembered HDL syntax or old examples. Notes marked
*(revision-dependent)* in this skill describe limitations or workarounds that
may already be fixed; check the checkout before relying on them, and do not
apply a workaround that is no longer needed.

## Reference material

| Topic | Read |
| --- | --- |
| Syntax, generics, configuration types, interfaces, types, bounds, state and reset | [references/language.md](references/language.md) |
| Reference-bearing API objects, binding functions, tuples of endpoints, lifecycles | [references/endpoint_apis.md](references/endpoint_apis.md) |
| Loops versus indexing, arbitration, balanced selection trees | [references/synthesis.md](references/synthesis.md) and [assets/round_robin.kh](assets/round_robin.kh) |
| Python bindings, elaboration checks, Verilator, testbenches, Verilog export, LSP | [references/tooling.md](references/tooling.md) |

## Establish the project's compiler and conventions

Read the project's `AGENTS.md`, README, and manifest. Find the KHDL checkout
and record its revision and local modifications before updating dependencies.
Use the project's setup and validation commands. Do not upgrade the compiler
just to match an example.

The most reliable references are in the compiler checkout:

- `rust/khdl_std/src/std/` for built-in types and functions.
- `python/src/khdl_sandbox/tests/` for exercised syntax, domains, generics,
  implications, registers, and Python conversions.
- `rust/khdl_python/khdl.pyi` for the Python API.
- `rust/khdl_language/src/syntax/grammar.lalrpop` and `src/front/` when a
  construct's behavior is unclear.
- `lsp_client/package.json` and `client/src/extension.ts` for editor settings.

[advent_of_fpga](https://github.com/KarelPeeters/advent_of_fpga) demonstrates
project organization and Python benches. Validate borrowed syntax and
handshake drivers against the checked-out revision and the target design.

## Standard library

The prelude makes the standard library available in every file without
imports. Check the checkout's `rust/khdl_std/src/std/` for the current
contents; at the time of writing it contains:

| File | Available functionality |
| --- | --- |
| `std.types` | `any`, `bool`, `str`, `int`, `uint` (starts at zero), `positive` (starts at one), `negative` (less than zero), `nonzero` (excludes zero); `Tuple`, `Range`, `Function`, `Module`, `Interface`, `InterfaceView`, `Package`; references `Ref(T)` and `RefInterface(I)`; enums `Option(T)` (`None`/`Some`), `Result(T, E)` (`Ok`/`Err`), and `Either(L, R)` (`Left`/`Right`). |
| `std.math` | Two-argument `min`/`max`, `abs`, `gcd`, `clog(base, n)`, `clog2(n)`. `gcd` takes positive inputs, and `clog` requires a base of at least two. Ceiling division is the native `a +/ b` operator. |
| `std.util` | `print(value="", end="\n")`, `assert(cond, msg="")`, `assert_assume(cond, msg="")`, `bool_to_int(b)` (range `0..=1`), and `unsafe_bool_to_clock(value)`. Clock conversion is explicitly unsafe; ordinary module ports already declare clocks. |

The compiler also provides `T.size_bits`, `T.to_bits(value)`, and
`T.from_bits(bits)` for hardware types, plus `array.len` and the array-type
members `.len` and `.inner`. Safe `from_bits` requires every bit pattern to be
valid for the target type; `from_bits_unsafe` bypasses that check. Arrays
support slices, repetition, spread, and comprehensions. Integer and
boolean-array bitwise operators `&`, `|`, `^`, and `!` are native, and boolean
scalars broadcast across boolean arrays. There are no standard array
`map`/`fold`/reverse helpers; use comprehensions and loops. Prefer prelude
helpers over project copies, and use comparison results directly as `bool`.

## Design guidance

**Interfaces.** Group related transaction signals in an interface with
producer and consumer views. For a common protocol, prefer a payload-typed
interface such as `Transaction(T)` with `data: T`, `valid`, and reverse
`ready`, and forward whole payload structs. Group ports that share a domain in
`sync(clk) { ... }`, use same-name connection shorthand such as
`ports(clk, rst, input, output=link)`, and let wires infer types and domains
from their drivers.

**Configuration types.** When a design has many related parameters, write a
factory that returns a configuration type containing constants, types,
functions, and computed tables. Thread it through modules and interfaces, so
independent configurations can coexist. Access members directly (`C.Symbol`,
`C.length`, `C.multiply(a, b)`), rather than through local aliases or a
file-level default alias that obscures the source of values. Group small sets
of related elaboration choices in a constrained struct value. Validate
parameter restrictions with elaboration-time assertions, and check generated
tables against an independent reference.

**API objects.** Structs holding references can bundle a protocol endpoint's
signals and registers behind methods. A binding function called inside a
clocked process creates the registers and returns the object; methods expand
in place in the caller. This lets transaction-level code work with different
buffering implementations. See [references/endpoint_apis.md](references/endpoint_apis.md).

**Scopes and values.** Declare temporaries in the smallest useful block or
loop scope. Prefer `val` for values that do not change, and `var` for
accumulators or partially built arrays. Do not promote temporaries to
registers. This also avoids unnecessary state merges across unrelated branches.

**Bounds.** Prefer compile-time bounds from constrained integer types and
existing control flow. Try a direct assignment before adding assumptions. If
an invariant remains unprovable, use a local `assert_assume` with an explicit
justification and only the missing constraint. Do not widen state or protocol
types, or add wrapping or clamping, just to make a range error disappear.
Carry parameter-dependent bounds through generic interfaces and pass-through
stages, rather than widening a payload and asserting its range at the consumer.

**Porting.** When porting another HDL, preserve its module boundaries, register
state, reset timing, array ordering, latency, ready/valid behavior, and useful
comments. KHDL assignments are immediate within a process, like VHDL process
variables rather than signals: save previous values explicitly when
translating delayed signal assignments. Prefer `clocked(clk, sync rst)` with
register initializers for synchronous reset. Reset skips the process body, so
registered output and ready wires need their own reset values. Use `undef`
where the design does not care about a value across reset, and keep tests
independent of whether that becomes a hold or an X.

**Hardware cost.** Elaboration-time loops can still replicate runtime muxing
or build long serial combinational chains. Read
[references/synthesis.md](references/synthesis.md) when loops, dynamic
indexing, arbitration, or reductions could dominate cost.

## Validate changes

1. After each coherent RTL edit, run a frontend elaboration of concrete
   specializations and print `DiagnosticException.combined_string` on failure.
   Resolving a generic module does not elaborate its body.
2. Give the LSP and other all-items compilations explicit roots: file-level
   `const` blocks that call generic modules and functions with representative
   arguments. Uncalled generic bodies remain unchecked. The Python API compiles
   lazily and does not run these blocks; call the factories explicitly.
3. Use a fresh compilation context after an expected failure *(revision-dependent:
   a failed context can keep reporting the earlier error)*.
4. Simulate against an independent reference model, with stalls, framing,
   reset, and error cases. A successful compile-time call does not show that
   the generated hardware is correct.

Use an optimized compiler build for ordinary work, and check which Cargo
profile the Python package was built with. Measure elaboration, Verilog
export, Verilator generation, C++ builds, and simulation separately before
changing optimization settings. Reuse configuration values within one
compilation context and compiled simulators across instances.

## Report findings

Separate language limitations, confirmed compiler or tooling bugs, and
project-specific protocol assumptions. For a bug, capture a small reproducer,
the compiler revision, the impact, and any workaround. Do not treat an awkward
construct as unsupported without checking the compiler's tests. Keep fixes
scoped to the requested work, and keep local compiler patches reproducible.

## Maintain this skill

This skill is project-agnostic. Add verified, reusable language and tooling
guidance here, with idiomatic examples that compile on the current revision.
Keep project parameters, scripts, compiler revisions and patches, test
coverage, measurements, and design-specific conventions in the project's
`AGENTS.md`, README, or documentation, and record bugs in the project's
feedback log if it has one. Label speculation, and distinguish environment
restrictions from compiler bugs.
