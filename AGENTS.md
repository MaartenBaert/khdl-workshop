# Working in this repository

The main goal is to stress-test KHDL, especially generic programming, through
realistic hardware designs. Correct hardware behavior is necessary, but prefer
idiomatic abstractions that explore what KHDL can express beyond VHDL or
Verilog. Preserve useful architecture and explanatory comments when adapting
existing designs. Record reproducible language/compiler findings in
`notes/khdl-feedback.md` and other findings in `notes/`; keep reusable guidance
in the project-agnostic skill. When code, documentation, instructions, or the
skill start relying on an open feedback entry, an unmerged compiler PR, or
revision-dependent behavior, add the place to the feedback log's dependency
index, and work through the index when the compiler pin changes.

If `AGENTS.local.md` exists, read it too: it holds the local user's personal
instructions, is not committed, and takes precedence over this file.

Every agent working on KHDL must read [skills/khdl/SKILL.md](skills/khdl/SKILL.md)
and its relevant references. Use **KHDL**; `.kh` is its current extension.
Use UpperCamelCase for modules, interfaces, views, structs, and user-defined
types; lower_snake_case for functions, variables, namespaces, and filenames.

## Repository map

- `projects/<name>/rtl/khdl.toml`: independent manifests and hardware namespaces.
- `projects/<name>/tests/`: project-specific tests and reference models.
- `projects/<name>/tools/`: project-specific compilation/export tools; invoke
  Python tools as modules from the repository root.
- `projects/<name>/docs/`: project manuals included by `docs/index.adoc`.
- `projects/<name>/AGENTS.md`: architecture, interface, and verification contracts.
- `test_support/simulation.py`: reusable Verilator builds and ready/valid drivers.
- `test_support/pytest_plugin.py` and root `conftest.py`: shared simulator
  environment and pytest-managed temporary build directories.
- `docs/`: shared AsciiDoc/WaveDrom builder, dependencies, and site index.
- `khdl/`, `skills/khdl/`: shared compiler and language guidance.
- `notes/`: local, uncommitted findings for maintainers and agents, not user
  documentation: `khdl-feedback.md` for compiler/language issues, `<project>/`
  for project experiments and investigations. It may be absent in a fresh
  clone; create it when needed, and read `notes/README.md` before adding to it.
  Committed files must not link to notes; cite upstream issues as `khdl#<number>`.
- `plans/`: local, uncommitted implementation plans for multi-step work. Write
  plans here, not in the repository root or a tool's own location. It may be
  absent in a fresh clone; create it when needed. Committed files must not
  link to it.
- `.claude/skills/khdl`: symlink that exposes the skill to Claude Code.

Read the relevant project's instructions before changing its implementation.
Project guidance: [Reed–Solomon](projects/reed_solomon/AGENTS.md),
[AXI API experiments](projects/axi/AGENTS.md), and
[pipelined FIR](projects/fir/AGENTS.md).
Keep project-specific algorithms, reference vectors, and parameters local;
extract helpers only when their behavior is independent of the design.
Preserve separate RTL manifests so unrelated projects do not share namespaces.

## Compiler and environment

`khdl/` is pinned at `cdca7cc7c62a8b3d30ce4ee2a879199048c93d74`, based on
upstream `e0264ac3` (ordered argument matching, assignment-invariant fixes,
and native Verilator warning/build controls).
`struct-reference-fields` at `9df78364` adds transparent `ref field: T` /
`ref field: Interface` ([PR #27](https://github.com/khdl-lang/khdl/pull/27));
the AXI API depends on it. `reference-method-receivers` adds limited `ref self`
methods ([PR #28](https://github.com/khdl-lang/khdl/pull/28), depending on #27);
the isolated AXI relay prototype depends on that feature. PR #27 also includes
deferred reference resolution and interface-array reference fields; PR #28 is
rebased on that branch. Arguments, including named
arguments and struct constructors, must follow parameter/field declaration order.
Default parameters cannot be skipped before later explicit arguments. Python
uses `inst.ports.input.ready` and direct values (`inst.ports.clk = False`); use
`inst.info_ports` for type/direction metadata.
Rebuild bindings and the LSP from the checkout after Rust changes.
`./setup.sh` builds the checkout without applying patch files. Compiler
selection, instance-port loop scopes, and the elaboration-cache deadlock fix
are upstream. The previous local patches were removed after this update.

Keep patches reproducible and preserve local submodule changes when updating.
Do not change the revision just to match an old example. Submit requested
compiler changes as pull requests from a feature branch, pushed to a fork
unless the user has write access upstream.
Never push to KHDL main; submit changes through pull requests only. Each PR
must have its own feature branch unless explicitly instructed otherwise.
Keep compiler fixes separate from project RTL and unrelated patches.

When the user requests it, agents may open issues and pull requests in
[khdl-lang/khdl](https://github.com/khdl-lang/khdl) with the `gh` tool. First
search the open and closed issues and pull requests for an existing report or
a similar change, and link or comment there instead of duplicating it. Apply
the repository's existing labels (`gh label list`), such as bug, enhancement,
or documentation, as fits the report. Link each new issue from its
`notes/khdl-feedback.md` entry, and state the compiler revision and branch a
reproducer was checked on.

The `.venv` uses Python 3.14. Rust/embedded-library changes require rebuilding
bindings; preserve setup's explicit `--profile=release --locked` build. Rebuild
the LSP separately when affected; see README rebuild commands. Preserve the
user's VS Code settings and other local work.

## Commits

Commits made by a coding agent, in this repository and on KHDL pull-request
branches, keep the user as author and end the message with a `Co-Authored-By:`
trailer naming the agent's model, for example:

```text
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

Other agents add the same trailer with their own full model name and no-reply
address. An agent that does not know its full model name asks the user before
committing.

## Verification

Run commands from the repository root. Before simulation, use a project's
concrete elaboration checks; resolving a generic factory alone does not check
its body. The pinned Python compiler is lazy and does not execute file-level
const blocks automatically; the LSP also drops standard-library assertion
diagnostics, so a clean Problems panel alone proves little.

Use `.venv/bin/pytest projects/<name>/tests` for a project and
`.venv/bin/pytest` for all projects. Changes to shared simulator/driver code
require the full suite. Keep tests in Python packages and use relative imports
for project-local helpers; root pytest configuration uses importlib mode to
avoid filename collisions. Keep reference models unchanged unless the requested
work requires otherwise.

Use `test_support.simulation.build_simulator` for fresh pytest-owned build
directories; cache compiled specializations within a session. Keep ccache at
`build/ccache` and respect environment overrides. Generated exports belong in
`build/<project>/`; documentation goes in `build/docs/`. Keep environments,
Verilog, caches, and logs under ignored paths. Simulation does not establish
synthesis or timing results. Avoid accumulating verification histories:
record a result worth keeping as a current note in `notes/<project>/`, and
replace it when it is superseded.

## Documentation and knowledge maintenance

Build the offline landing page and project manuals with `.venv/bin/python docs/build.py`;
install `docs/requirements.txt` separately if needed. AsciiDoc diagnostics fail
the build. Output is `build/docs/index.html` plus `build/docs/<project>.html`.
Keep project-prefixed anchors. Check links, embedded diagrams, and examples; documentation-only
changes do not need simulator rebuilds.

Keep common workflow here, design contracts in project instructions/manuals,
reusable language idioms in the skill, reproducible bugs/proposals in
`notes/khdl-feedback.md`, and measurements, experiment results, and
investigations in `notes/<project>/`. User documentation states contracts and
limits; it can summarize a note's conclusion, but not its raw numbers or
history. Distinguish observed bugs from design choices and speculation.
The skill is project-agnostic: no project names, parameters, compiler hashes,
test coverage, or measurements. Mark compiler limitations there as
revision-dependent, and keep its KHDL examples compiling on the pinned revision.
