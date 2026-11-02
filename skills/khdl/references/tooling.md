# Tooling and verification

Check the checkout's API and build configuration before applying any
revision-dependent workaround below.

- [Environment and compiler builds](#environment-and-compiler-builds)
- [Python API and diagnostic handling](#python-api-and-diagnostic-handling)
- [Command-line compiler](#command-line-compiler)
- [Verilator builds and performance](#verilator-builds-and-performance)
- [Simulator ports and clocking](#simulator-ports-and-clocking)
- [Ready/valid testbench rules](#readyvalid-testbench-rules)
- [Verilog export](#verilog-export)
- [VS Code LSP](#vs-code-lsp)

## Environment and compiler builds

Read `rust/Cargo.toml`, `rust/khdl_python/pyproject.toml`, and the project's
setup instructions for the required Rust, PyO3, and Python versions. Do not
infer that every permitted Python version has been tested.

For an editable, optimized Python extension, with paths adapted to the checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install maturin numpy pytest
pip install --no-build-isolation --config-settings 'maturin.build-args=--profile=release --locked' -e path/to/khdl/rust/khdl_python
```

Maturin's executable must be on `PATH` when build isolation is disabled;
running a virtual environment's Python by absolute path does not activate its
other executables. Select the release profile explicitly for predictable
compiler performance, including for editable installs. If the workspace
defines a profiling profile with debug information, use it only for native
stack inspection. `cargo fetch --locked` downloads Rust dependencies; it does
not build the extension.

To check which native library is loaded, use
`importlib.import_module("khdl.khdl").__file__`; `khdl.__file__` is only the
Python package initializer. Compare it with the expected `target/<profile>/`
artifact; library size alone is not evidence. Rebuild and reinstall after Rust
or embedded standard-library changes. The Python extension, CLI, and LSP server
are separate build targets.

## Python API and diagnostic handling

```python
from pathlib import Path
import khdl

try:
    source = khdl.Source.new_from_manifest_path('path/to/khdl.toml')
    compiler = source.compile()
    factory = compiler.resolve('counter.Counter')
    module = factory(width=8)
    verilog = module.as_verilog()
    Path('build').mkdir(exist_ok=True)
    Path('build/counter.v').write_text(verilog.source)
    print('Top:', verilog.module_name)
except khdl.DiagnosticException as exc:
    print(exc.combined_string)
    raise
```

A module without generic parameters resolves directly to a `khdl.Module`, and
calling it with `()` fails. A generic module resolves to a callable factory,
even when every argument has a default. Use `isinstance(item, khdl.Module)` in
helpers that support both. `resolve` can access private items, so it does not
check whether a KHDL import of that item would be allowed; test visibility
with an actual cross-file import.

`Source.compile()` is lazy: resolving a generic factory does not check its
body, and file-level const blocks do not run. Call concrete specializations
for frontend checks before building a simulator. Use small parameters while
iterating, then the intended sizes and edge cases. Test expected failures too:
an invalid configuration should produce a diagnostic, not a later Verilator
error.

Compile-time functions are callable through `resolve`. A type-returning
factory gives a `khdl.Value`, which can be passed back into KHDL as a type
argument. Keep values in the compilation context that produced them, including
constrained parameter structs built with `compiler.resolve('ns.Unroll').new(...)`.
Attribute access exposes static members as well as struct fields: for a
returned configuration `cfg`, use `cfg.length`, `cfg.Symbol.size_bits`,
`cfg.table`, or `cfg.multiply(a, b)` directly. Constants and arrays convert to
Python values; types and functions remain opaque callable objects. Pass native
Python integers and lists, not NumPy scalars. `rust/khdl_python/khdl.pyi` and
`Value.__getattr__` in `src/lib.rs` describe the API; the stub may not list all
supported attribute access.

Struct values expose `StructValue.fields` as a dictionary. Enum values expose
`EnumValue.tag` (the variant name) and `.payload` (`None` or the converted
payload). These are Python inspection APIs, not RTL reflection.

Repeated factory calls rerun their algorithms. Cache configuration values by
their scalar parameters within one compilation context, and discard the cache
when the context or source changes. Separately cache compiled simulators by
all configuration parameters, and create independent instances from them.

*(Revision-dependent: after a failed elaboration, later resolutions can raise
`DiagnosticPreviouslyReportedException`, even for valid items.)* Use a fresh
`Source` and compilation for each deliberately failing case, and keep those
cases out of shared caches.

For small reproducers, use `Source()` and
`add_file_content(['probe'], 'probe.kh', text)`. `khdl.format_file(text)`
formats source, but it can expand large array literals to one entry per line;
inspect the result before applying it broadly.

For a stalled binding call, Python's faulthandler only shows the Python call
site. Capture a native stack with GDB before blaming the GIL, garbage
collection, or compiler work. Repeat a small reproducer in fresh contexts,
because randomized hashing can make lock bugs intermittent; forcing hash
collisions reproduces them more reliably.

## Command-line compiler

The CLI package is `khdl_bin`, with `build` and `fmt` subcommands. Default
`build` evaluates all file-level items, including const elaboration checks;
`--top-only` restricts compilation to the selected tops. `--top` requires a
public module without generic parameters, so use a concrete wrapper or Python
for generic tops. Check `rust/khdl_bin/src/args.rs` for current options.

## Verilator builds and performance

Simulation needs Verilator, Make, and a C++ compiler on `PATH`. Use
`module.as_verilated(build_dir)`; the builder creates missing directories, and
`compiled.instance()` creates independent instances without another build.
Prefer fresh build directories, such as pytest temporary directories, plus a
cache of compiled modules.

Large suites can exhaust a memory-backed temporary filesystem. Use pytest's
`--basetemp` on a disk-backed build path, and set `TMPDIR` to an existing
disk-backed directory for compiler scratch files. Check free space during long
runs; failed builds after storage exhaustion are not hardware failures.

Measure the slow phase before tuning: elaboration, Verilog export, Verilator
generation, C++ compilation and linking, or simulation. They have separate
settings. Compare the same specialization and compiler profile, and account
for concurrent work and caches. Nested hardware helpers can produce large
intermediate Verilog.

- `optimization=None` or `0..3` sets Verilator's own `-O` level.
  `compiler="gcc"` or `"clang"` selects code-generation workarounds, C++ and
  precompiled-header flags, and the final linker together. Omit it to keep
  Verilator and Make defaults. Changing only `CXX`, or only Verilator's
  `--compiler`, leaves the other steps configured for a different compiler.
- `jobs=N` sets native simulator build parallelism; omit it for the builder's
  default. This is independent of optimization settings.
- Make's `OPT_FAST` controls optimization of the generated C++ separately.
  Lower optimization shortens builds for short benches; long simulations may
  justify slower builds. Re-check functional behavior after changing settings.
- For expensive C++ builds, enable Verilator's compiler cache with
  `OBJCACHE=ccache` and a writable `CCACHE_DIR`. Confirm cache hits from logs
  or statistics instead of assuming them, especially across different build
  paths, and respect explicit environment overrides.
- When comparing precompiled headers, keep the generated C++ and parallelism
  fixed, compare empty and warm compiler caches, and verify that disabled PCH
  files are actually not generated.

For a purely structural RTL cleanup, comparing normalized exported Verilog can
show that the hardware is unchanged without rebuilding simulators.

## Simulator ports and clocking

Python simulator ports preserve interface hierarchy: `inst.ports.input.ready`,
not `inst.ports.input_ready`. Values are read and assigned directly, e.g.
`inst.ports.clk = False`. Interface arrays keep indexing, e.g.
`inst.ports.links[0].valid`. Metadata lives in the matching `inst.info_ports`
hierarchy (`inst.info_ports.input.data.type`, `.direction`, `.name`), and
`inst.info_ports_flat` is the flattened map. Verilog and cocotb pins remain
flattened with underscores.

A struct-valued signal is packed into one port. Construct payloads with
`inst.info_ports.input.data.type.new(field=..., ...)`, supplying every field,
and read members through `inst.ports.input.data.field`. Captured struct values
compare structurally, which suits stalled-payload and expected-result checks.

`inst.step(1)` advances time and settles changes; it is not a clock cycle.
Drive the clock explicitly. Evaluate once with the clock low before the first
rising edge: *(revision-dependent)* the first `clk = True` after creating an
instance is otherwise not seen as an edge, and registers keep their zero
initialization instead of their reset values. For a synchronous reset:

```python
p = inst.ports
p.clk = False
p.enable = False
p.rst = True
inst.step(1)      # settle before the first edge
p.clk = True
inst.step(1)      # reset edge
p.clk = False
inst.step(1)
p.rst = False
inst.step(1)
```

Holding reset across two edges also avoids the missed first edge. Match the
module's reset timing; this is not an asynchronous reset recipe. Initialize
every input payload before using it.

To save a VCD, use `compiled.instance(trace_path=...)`; each step dumps the
current time. *(Revision-dependent: the file is buffered and stays incomplete
until `inst.save_trace()` is called or the instance is destroyed.
`save_trace()` closes the trace rather than taking a snapshot, so later steps
are not recorded.)* Call it once, after the last step you want to inspect.

## Ready/valid testbench rules

Simulator metadata uses `port.direction == 'input'` or `'output'`; use these
strings when filtering ports for initialization or observation.

Use or adapt a shared driver with these transfer semantics:

1. At clock low, present inputs and output readiness; settle combinational logic.
2. Snapshot both handshakes and the output payload before the rising edge.
3. Raise the clock, step, lower the clock, step.
4. Remove an accepted input only after the edge. Hold valid, framing, and
   payload unchanged throughout a stall.

Inferring a previous transfer from the newly registered `ready` or `valid`
after the edge can lose or duplicate data. Check stalled-output stability,
frame boundaries, metadata, and cycle bounds as well as payload. Use
deterministic random seeds and the design's lane and order conventions.

Independent Verilated instances advance through separate `step` calls. For
separate producer and consumer designs, stream several frames through one,
transform its captured outputs, then stream those through the other. Within
each design, keep driving inputs and collecting outputs across frame
boundaries so that frames overlap, and assert that they actually do. Connected
designs still need their shared timing and backpressure modeled.

Make input-launch and output-ready random decisions independently. A random
launch decision applies only when no beat is pending; it must never withdraw
an asserted valid beat. Choose stall probabilities that exercise the protocol.

## Verilog export

`as_verilog()` returns `.source` and `.module_name`. Use the emitted top name
rather than inferring it from a filename. Each source includes its whole
hierarchy, so separate exports can contain colliding generated names; compile
them separately or check before concatenating.

*(Revision-dependent: a failing hardware assertion emits printing and an
unguarded `$finish` in the surrounding process, including assertions from
`assert_assume`.)* Simulation then ends with `khdl.SimulationFinishedException`,
and the diagnostic text can remain in C stdio buffers: when testing messages
with pytest `capfd`, call `ctypes.CDLL(None).fflush(None)` before reading the
captured output. Synthesis tools such as Yosys can reject `$finish` outside an
initial block. This is a backend integration limitation, not an invalid
design: keep the checks in source and report the incompatibility. Removing the
simulation tasks from a temporary copy does not validate the original export
or preserve its properties.

## VS Code LSP

File-level `const { GenericModule(...); }` blocks select representative
specializations for editor checks. The LSP compiles all items, so it treats
these blocks as roots and elaborates the called module hierarchies. Default
generic arguments alone do not trigger a call, and uncalled variants remain
unchecked. Const blocks in generic parameter lists can assert specialization
restrictions too.

*(Revision-dependent: the LSP drops diagnostics whose primary location is in
an embedded standard-library file, including failed `assert` calls.)* Keep
explicit Python or CLI checks even when the editor shows no problems, and do
not remove assertions to work around missing diagnostics.
`khdl_lsp_server/src/server/compile.rs` maps URIs, and
`khdl_language/src/front/compile.rs` queues roots.

An installed editor extension does not expose its diagnostics to an agent. Use
a shell elaboration check or an explicit diagnostic bridge. For a direct stdio
LSP probe, advertise dynamic watched-file registration, complete the
initialize/initialized handshake, and acknowledge `client/registerCapability`
requests. The server can cancel compilation when new messages arrive and
publishes only files with diagnostics or previously reported errors, so
silence alone does not prove a complete, successful pass. Wait for a
non-cancelled pass to complete before interpreting it.

Build and install against the same checkout:

```bash
cd path/to/khdl/rust
cargo build --release --locked -p khdl_lsp_server
cd ../lsp_client
npm ci
npx vsce package --out /tmp/khdl.vsix
code --install-extension /tmp/khdl.vsix
```

Set `khdl.server.path` to the absolute server binary path, reload VS Code, and
open a `.kh` file with a discoverable `khdl.toml`. The client's `package.json`
and extension source are more reliable than its README. Rebuild the LSP
separately after relevant Rust changes.
