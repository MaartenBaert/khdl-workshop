# Reference-bearing endpoint APIs

KHDL structs can hold references to signals, registers, and interfaces. A
struct of references, with methods that read and assign through them, packages
a protocol endpoint as an ordinary value. The resulting API objects let
transaction-level algorithms work unchanged with different storage and
buffering implementations. This file shows the idioms and their limits.

- [Transparent reference fields](#transparent-reference-fields)
- [Binding functions create state](#binding-functions-create-state)
- [A complete ready/valid endpoint API](#a-complete-readyvalid-endpoint-api)
- [Composite protocols and flat interfaces](#composite-protocols-and-flat-interfaces)
- [Collections of differently typed endpoints](#collections-of-differently-typed-endpoints)
- [Choosing an implementation with an enum generic](#choosing-an-implementation-with-an-enum-generic)
- [Mutable receivers and per-cycle state](#mutable-receivers-and-per-cycle-state)
- [Checks and immediate semantics](#checks-and-immediate-semantics)

## Transparent reference fields

*(Revision-dependent: transparent `ref` fields were added by
[khdl#27](https://github.com/khdl-lang/khdl/pull/27). Check that the compiler
supports them; without them, use ordinary fields such as
`channel: RefInterface(Channel(T))` and explicit `deref(self.channel).data`.)*

```kh
pub interface Channel(T: type) {
    data: T, valid: bool, ready: bool,
    view Transmitter { data: out, valid: out, ready: in }
    view Receiver { data: in, valid: in, ready: out }
}

pub struct Driver(T: type) {
    ref channel: Channel(T),
    fn drive(self, data: T, valid: bool) {
        self.channel.data = data;
        self.channel.valid = valid;
    }
}
```

`ref field: T` stores a `Ref(T)`; an interface annotation stores a
`RefInterface(I)`. Constructors receive explicit references:
`Driver(T).new(channel=ref(output))`. Reading or assigning the field reads or
assigns its target. The target's own mutability, direction, domain, and
lifetime checks still apply, including through nested method calls: writing an
input through a reference is rejected, and so is binding a differently
specialized interface. `ref(object.field)` forwards the reference itself.

Interface-array fields use the same syntax, for example
`ref channels: [2]Channel(T)`. Indexing and slicing preserve interface shape
and direction; indices and slice starts must be compile-time values.

Copying a struct copies its references, so the copy aliases the same targets.
Assigning a field updates the target; replacing a whole mutable struct
replaces its references. A reference field dereferences exactly one binding,
and there is no general auto-dereference: ordinary `field: Ref(T)` fields keep
explicit `deref(...)` semantics. Reference-bearing structs are non-hardware
values. They add no runtime pointers or storage.

*(Revision-dependent: `RefInterface` takes an `Interface`, not an
`InterfaceView`, so a role-specific wrapper can bind the opposite view, and the
direction error appears only when a method accesses a forbidden signal. Have
the binding function declare the role's output registers, which checks the
direction at binding time.)*

References and structs containing them cannot cross module boundaries: they
cannot be module generic arguments. Connect instances through ordinary
interface ports. References to ordinary local variables keep their scope
lifetime checks.

## Binding functions create state

A binding function called inside a clocked process can declare `reg` state
and return a struct that references it. Unlike ordinary local variables,
registers survive the function's return:

```kh
pub struct Slot(T: type) {
    ref value: T,
    ref occupied: bool,
}

pub fn slot(T: type) -> Slot(T) {
    reg r_value: T = undef;
    reg r_occupied: bool = false;
    return Slot(T).new(value=ref(r_value), occupied=ref(r_occupied));
}
```

Each call allocates independent state in the calling process; copies of the
result share it. A method or binding function can also write
`reg wire self.channel.valid = false;`, which registers the actual target
signal in the calling process. Defined reset values then require a reset on
that process. Registering the same output twice is rejected. Array
comprehensions can collect independently constructed objects.

Keep the explicit `ref(...)` at call sites. Use const blocks for configuration
checks inside binding functions: in a clocked call context, even a constant
`assert` condition can otherwise become a runtime assertion.

## A complete ready/valid endpoint API

The following example is a minimal FIFO-style channel API. Each object owns
one payload slot and the handshake signal it drives. Application code calls
`begin()` and `end()` unconditionally each cycle and performs conditional work
in between, using availability predicates:

```kh
pub interface Channel(T: type) {
    data: T, valid: bool, ready: bool,
    view Transmitter { data: out, valid: out, ready: in }
    view Receiver { data: in, valid: in, ready: out }
}

// The output register is the slot: occupied while valid is high.
pub struct Transmitter(T: type) {
    ref channel: Channel(T),
    fn begin(self) {
        if (self.channel.valid && self.channel.ready) { self.channel.valid = false; }
    }
    fn end(self) {}
    fn can_push(self) -> bool { return !self.channel.valid; }
    fn push(self, data: T) {
        val available = self.can_push();
        assert(available, "Transmitter.push(): can_push required");
        if (available) {
            self.channel.data = data;
            self.channel.valid = true;
        }
    }
}

pub fn transmitter(T: type, channel: RefInterface(Channel(T))) -> Transmitter(T) {
    val tx = Transmitter(T).new(channel=channel);
    reg wire tx.channel.data = undef;
    reg wire tx.channel.valid = false;
    return tx;
}

// A skid slot, occupied while ready is low, keeps ready registered.
pub struct Receiver(T: type) {
    ref channel: Channel(T),
    ref slot: T,
    fn begin(self) {
        if (self.channel.valid && self.channel.ready) {
            self.slot = self.channel.data;
            self.channel.ready = false;
        }
    }
    fn end(self) {}
    fn can_pop(self) -> bool { return !self.channel.ready; }
    fn pop(self) -> T {
        val available = self.can_pop();
        assert(available, "Receiver.pop(): can_pop required");
        var data: T = undef;
        if (available) {
            data = self.slot;
            self.channel.ready = true;
        }
        return data;
    }
}

pub fn receiver(T: type, channel: RefInterface(Channel(T))) -> Receiver(T) {
    reg r_slot: T = undef;
    val rx = Receiver(T).new(channel=channel, slot=ref(r_slot));
    reg wire rx.channel.ready = true;
    return rx;
}

// Works with any objects that provide these operations.
fn forward(input: any, output: any) {
    if (input.can_pop() && output.can_push()) { output.push(input.pop()); }
}

pub module Relay(T: type) ports(
    clk: in clock,
    sync(clk) {
        rst: in bool,
        input: interface Channel(T).Receiver,
        output: interface Channel(T).Transmitter,
    }
) {
    clocked(clk, sync rst) {
        val rx = receiver(T, ref(input));
        val tx = transmitter(T, ref(output));
        rx.begin(); tx.begin();
        forward(rx, tx);
        tx.end(); rx.end();
    }
}
```

The relay is correct because each pop requires data and each push requires
space; its latency never enters the argument. `forward` accepts `any`, so the
same function works with other storage objects that provide the same
operations, such as a one-slot internal stage or a FIFO. Applications must not
assume that a push makes data poppable in the same cycle, or that a pop frees
space for a push in the same cycle, unless the storage type promises it.

`begin()` and `end()` give each object a place to service its state every
cycle, independently of the application's conditions. Composite objects can
own the lifecycle of their children. Here `end()` is empty, but a buffer with
separate push and pop sides can snapshot its availability in `begin()` and
commit occupancy in `end()`.

## Composite protocols and flat interfaces

*(Revision-dependent: interfaces cannot contain other interfaces.)* For a
composite protocol, put typed payload signals in one flat interface and bind
reusable channel code through a struct of signal references:

```kh
pub struct ChannelSignals(T: type) {
    ref data: T,
    ref valid: bool,
    ref ready: bool,
}
```

A binding function builds one per sub-channel, for example
`ChannelSignals(Request).new(data=ref(deref(bus).request), valid=ref(deref(bus).request_valid), ready=ref(deref(bus).request_ready))`.
The references retain directions and domains, and `reg wire signals.data = undef;`
registers the original target. The channel API then holds a
`ChannelSignals(T)` field instead of a `ref channel: Channel(T)`. Keep any
conventional flat pin naming at the external boundary.

Access constructors and types directly through configuration types, e.g.
`C.Payload.new(...)`.

## Collections of differently typed endpoints

A constructor can create an endpoint first, and a later composing constructor
can store it. Use `type(x)` for the type of a bound identifier, for example a
function returning `Pair(type(first), type(second))`. `type(...)` accepts bare
identifiers; bind an expression to a local value first. Pass the endpoint
*type* as a generic argument and the object itself as an ordinary field, not
as a generic value argument.

Tuples hold differently specialized objects. `for (item in tuple_value)`
elaborates each iteration with that member's concrete type. Tuple values and
types expose `.len`, and `Tuple(A, B).elements` is the tuple `(A, B)` of
member types. Traverse `.elements` for validation that must not read hardware
state, for example to check that member IDs are unique:

```kh
fn validate_ids(Endpoints: type) {
    var index = 0;
    for (F in Endpoints.elements) {
        var other = 0;
        for (G in Endpoints.elements) {
            if (other < index) { assert(F.id != G.id, "IDs must be unique"); }
            other += 1;
        }
        index += 1;
    }
}
```

Empty tuples naturally disable optional collections; a one-member tuple needs
a trailing comma, `(item,)`. Prefer tuples for differently specialized
endpoints and arrays for homogeneous collections. Tuple member indexing uses
literal numeric indices such as `pair.0`.

A traversal helper can take a callable and invoke it on each member. A
callback parameter typed `any` specializes for each member type, and a nested
function can capture the caller's API objects. This separates a traversal
policy, such as arbitration, from endpoint operations, without runtime object
selection. [synthesis.md](synthesis.md) shows a round-robin arbiter built this
way. Explicit `ref(...)` arguments carry mutable traversal state.

Prefer file-level generic structs for recursive type construction.
*(Revision-dependent: a struct local to a type-returning function cannot name
itself in a static method's return annotation.)* Repeated calls to a factory
that declares a local nominal type can produce distinct types, so construct
through an already-selected type rather than recomputing it.

## Choosing an implementation with an enum generic

An enum generic can select between implementations that share one API, so one
module covers several flavors. A `match` in the port list adds ports that only
some flavors need, and a compile-time `match` binds the matching API object.
Only the matching arm is elaborated, so each arm may produce a different type:

```kh
pub enum Storage { Registers, Delayed(positive) }

// Write and read once per cycle; read() returns data requested `latency`
// cycles earlier. A zero-length delay line disappears entirely.
struct Memory(T: type, depth: positive, read_latency: uint) {
    const latency = read_latency;
    ref cells: [depth]T,
    ref pipeline: [read_latency]T,
    fn write(self, enable: bool, address: uint(0..depth), data: T) {
        if (enable) { self.cells[address] = data; }
    }
    fn read(self, address: uint(0..depth)) -> T {
        var data = self.cells[address];
        if (read_latency > 0) {
            val response = self.pipeline[read_latency - 1];
            self.pipeline = [data, *self.pipeline[0..read_latency - 1]];
            data = response;
        }
        return data;
    }
}

fn memory(T: type, depth: positive, latency: uint) -> Memory(T, depth, latency) {
    reg r_cells: [depth]T = undef;
    reg r_pipeline: [latency]T = undef;
    return Memory(T, depth, latency).new(cells=ref(r_cells), pipeline=ref(r_pipeline));
}

pub module Store(storage: Storage = Storage.Registers) ports(
    clk: in clock,
    sync(clk) {
        rst: in bool,
        write: in bool, address: in uint(0..8), data: in uint(8), value: out uint(8),
        match (storage) {
            .Delayed(_) => { delayed: out bool }
            _ => {}
        }
    }
) {
    clocked(clk, sync rst) {
        reg wire value = 0;
        var api;
        match (storage) {
            .Registers => { api = memory(uint(8), 8, 0); }
            .Delayed(val latency) => {
                api = memory(uint(8), 8, latency);
                reg wire delayed = true;
            }
        }
        api.write(write, address, data);
        value = api.read(address);
    }
}
```

From Python, construct the variants as attributes of the resolved enum, for
example `kinds.Registers` or `kinds.Delayed(3)`. *(Revision-dependent: there
are no `match` or `if` expressions, so the selected object is an untyped `var`
assigned in a `match` statement. Code outside the process that needs a
member, such as the latency, can derive the type with a separate type-returning
function.)*

## Mutable receivers and per-cycle state

*(Revision-dependent: [khdl#28](https://github.com/khdl-lang/khdl/pull/28)
adds `fn method(ref self)` for mutable whole-variable receivers. Check the
compiler for support and its current restrictions, such as rejecting
temporaries, immutable variables, and indexed or nested targets.)*

A `ref self` method updates the caller's object, so ordinary fields can hold
per-cycle state, such as lifecycle flags or availability snapshots, without
registers. Ordinary `self` methods receive a copy: their ordinary-field writes
are lost, while reference fields still alias the same targets. Helpers that
need to mutate an API object accept a reference and call methods on
`deref(arg)`.

*(Revision-dependent: hardware partial assignment to a struct that also
contains references fails, because the compiler tries to represent the whole
struct in hardware. Pure hardware structs, and compile-time ordinary fields
alongside references, work.)* Until that is fixed, references to registers can
hold hardware-valued per-cycle scratch. Mark such registers as workarounds,
and keep them distinct from state that must persist across cycles. References
to factory-local `var`s expire at return.

## Checks and immediate semantics

Use ordinary `assert` for API preconditions, and reserve `assert_assume` for
facts that should also narrow compiler bounds. Guard the mutation as well as
asserting, so that a failed check does not also overwrite occupied storage.

Assignments inside methods take effect immediately, as in the caller. Capture
old state or handshake predicates before modifying an endpoint: a buffer
computes acceptance from its old valid and ready values before replacing the
payload. When an algorithm separates operations within one process, snapshot
payloads as well as availability before later writes; reading mutable arrays
after a write can otherwise create write-through mux paths, even when the
indices are logically disjoint.

Test independent API objects in the same process, composition across
instances, reset, and stalls. Elaborating a generic API does not establish its
hardware behavior.
